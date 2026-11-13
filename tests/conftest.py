"""Test fixtures: a temporary database, a fake OpenAI-compatible LLM server, and a
stubbed Whisper model — so the whole suite runs offline without any models."""
import io
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="towerai-tests-")


# --- Fake LLM server -------------------------------------------------------
class FakeLLM:
    """handler(body) -> message dict, or (status, body) for errors."""
    def __init__(self):
        self.calls = []
        self.handler = lambda body: {"content": "OK"}
        self.models = ["fake-model"]

    def reply(self, body):
        self.calls.append(body)
        out = self.handler(body)
        if isinstance(out, tuple):
            return out
        return 200, {"model": self.models[0], "choices": [{"message": out}]}


FAKE = FakeLLM()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"data": [{"id": m} for m in FAKE.models]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        code, obj = FAKE.reply(body)
        if code != 200 or not body.get("stream"):
            return self._send(code, obj)
        # Server-sent events, split into pieces like a real server streams them.
        msg = obj["choices"][0]["message"]
        chunks = []
        text = msg.get("content") or ""
        for i in range(0, len(text), 4):
            chunks.append({"content": text[i:i + 4]})
        for i, tc in enumerate(msg.get("tool_calls") or []):
            args = tc["function"]["arguments"]
            half = len(args) // 2
            chunks.append({"tool_calls": [{"index": i, "id": tc.get("id"), "type": "function",
                                           "function": {"name": tc["function"]["name"], "arguments": args[:half]}}]})
            chunks.append({"tool_calls": [{"index": i, "function": {"arguments": args[half:]}}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for ch in chunks:
            self.wfile.write(f"data: {json.dumps({'model': obj['model'], 'choices': [{'delta': ch}]})}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


_server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
threading.Thread(target=_server.serve_forever, daemon=True).start()

os.environ.update(
    TOWERAI_DB=os.path.join(TMP, "test.db"),
    LLM_BASE_URL=f"http://127.0.0.1:{_server.server_port}",
    RECORDINGS_DIR=os.path.join(TMP, "recordings"),
    IMAGES_DIR=os.path.join(TMP, "images"),
    SECRET_KEY="test-secret",
    DIARIZATION_ENABLED="0",
    SUMMARY_CHUNK_CHARS="400",
    SCHEDULER_ENABLED="0",  # tests call scheduler.run_due() directly
)


# --- Stub Whisper ----------------------------------------------------------
class _Word:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


class _Seg:
    def __init__(self, start, end, words):
        self.start, self.end, self.words = start, end, words
        self.text = "".join(w.word for w in words)


class FakeWhisper:
    """Emits one word every 0.5 s of audio ("w0.0 w0.5 …"), or fixed text."""
    text = None
    last_kwargs = {}

    def transcribe(self, audio, word_timestamps=False, **kw):
        FakeWhisper.last_kwargs = kw
        dur = len(audio) / 16000
        if self.text is not None:
            return iter([_Seg(0, dur, [_Word(0, dur, " " + self.text)])]), None
        segs, t = [], 0.0
        while t < dur - 0.5:
            ws = [_Word(t + i * .5, t + i * .5 + .4, f" w{t + i * .5:.1f}")
                  for i in range(6) if t + i * .5 + .4 < dur]
            segs.append(_Seg(ws[0].start, ws[-1].end, ws))
            t += 3
        return iter(segs), None


import stt  # noqa: E402

stt._whisper = FakeWhisper()
stt._whisper_desc = "fake whisper"

import app as appmod  # noqa: E402  (after env + stubs)
import db  # noqa: E402


def wav_bytes(seconds=2.0, rate=16000, freq=220):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate)))
                               for i in range(int(seconds * rate))))
    return buf.getvalue()


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture(scope="session")
def app():
    appmod.app.testing = True
    return appmod.app


@pytest.fixture(autouse=True)
def _reset_login_throttle():
    import security
    security._failures.clear()


@pytest.fixture
def llm():
    FAKE.calls.clear()
    FAKE.handler = lambda body: {"content": "OK"}
    FAKE.models = ["fake-model"]
    yield FAKE


_n = [0]


@pytest.fixture
def client(app, llm):
    """Logged-in client that sends the CSRF header on every request."""
    from werkzeug.security import generate_password_hash
    _n[0] += 1
    name = f"user{_n[0]}"
    uid = db.create_user(name, generate_password_hash("secret"))
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["csrf_token"] = "tok"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = "tok"
    c.username, c.user_id = name, uid
    return c


@pytest.fixture
def room():
    return db.get_room("general")
