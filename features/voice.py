"""Voice: talk to the assistant in a chat room — push-to-talk or hands-free.

The browser records a short clip (MediaRecorder) and POSTs it to
/voice/rooms/<room>/ask. It is transcribed offline with Whisper (stt.py), posted
to the room as the speaker's message, and the assistant's answer is posted too
and returned, so the browser can read it aloud with its built-in speech
synthesis (no server-side TTS to install). Loads only if the STT engine does.

Hands-free (🎧): the browser streams the mic continuously over
/ws/voice/<room>; Whisper transcribes each utterance and only those starting
with a wake phrase ("Torre, …", "Hey Tower, …" — VOICE_WAKE_WORDS) go to the
assistant. Saying just the wake phrase makes it listen to the next sentence.
"Torre, parar" stops listening; "Torre, repetir" replays the last answer.

Replies are spoken with offline Piper TTS (tts.py, POST /voice/tts) when a
voice is installed, else with the browser's speech synthesis.

Microphone capture needs HTTPS (or localhost) — see USE_TLS.
"""
import os
import tempfile

import json
import queue
import re
import threading
import time
import unicodedata

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_sock import Sock

import captions
import chat
import config
import db
import stt
import tts
from auth import login_required, current_user

bp = Blueprint("voice", __name__, url_prefix="/voice")

MAX_CLIP_BYTES = 20 * 1024 * 1024  # ~ several minutes of compressed audio


def transcribe_clip(data):
    """Decode any browser clip (webm/ogg/mp4/wav) and transcribe it."""
    fd, path = tempfile.mkstemp(suffix=".audio")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        audio = stt.decode_file(path)
    finally:
        os.remove(path)
    return " ".join(s.text.strip() for s in stt.transcribe_segments(audio)).strip()


@bp.route("/rooms/<name>/ask", methods=["POST"])
@login_required
def ask(name):
    room = db.get_room(name)
    if not room:
        abort(404)
    f = request.files.get("audio")
    data = f.read(MAX_CLIP_BYTES + 1) if f else b""
    if not data:
        return jsonify(error="no audio"), 400
    if len(data) > MAX_CLIP_BYTES:
        return jsonify(error="clip too long"), 413
    try:
        text = transcribe_clip(data)
    except Exception as e:
        return jsonify(error=f"could not transcribe: {e}"), 400
    if not text:
        return jsonify(text="", reply="")

    user = current_user()
    chat.deliver(room, user["id"], "user", user["username"], f"🎤 {text}")
    reply = chat.assistant_reply(room, user)
    chat.deliver(room, None, "assistant", config.ASSISTANT_NAME, reply)
    return jsonify(text=text, reply=reply, lang=config.WHISPER_LANGUAGE)


@bp.route("/tts", methods=["POST"])
@login_required
def speak():
    """WAV audio of the given text, spoken by the offline Piper voice."""
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not tts.available():
        return jsonify(error="no offline voice installed"), 404
    try:
        return Response(tts.synthesize(text), mimetype="audio/wav")
    except ValueError as e:
        return jsonify(error=str(e)), 400


# --- Hands-free ------------------------------------------------------------
STOP = {"parar", "pare", "para", "stop", "silencio", "chega", "tchau", "parar de ouvir"}
REPEAT = {"repetir", "repita", "repete", "de novo", "repeat", "again", "say again"}
AWAIT_SEC = 8  # after a bare wake phrase, the next sentence is the question


def _norm(text):
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def split_wake(text):
    """(woke, rest): whether `text` starts with a wake phrase, and what follows it
    (original wording, without the wake phrase)."""
    norm = _norm(text)
    for w in sorted((_norm(x) for x in config.VOICE_WAKE_WORDS), key=len, reverse=True):
        if norm == w or norm.startswith(w + " "):
            words = len(w.split())
            rest = re.split(r"\s+", text.strip(), maxsplit=words)
            return True, (rest[words] if len(rest) > words else "").lstrip(" ,.!?:;-")
    return False, text


class HandsFree:
    """Per-connection state: decides what to do with each transcribed utterance."""
    def __init__(self, room, user, send):
        self.room, self.user, self.send = room, user, send
        self.awaiting_until = 0.0
        self.last_reply = ""

    def handle(self, text):
        self.send({"type": "heard", "text": text})
        woke, rest = split_wake(text)
        if not woke and time.time() < self.awaiting_until:
            woke, rest = True, text
        if not woke:
            return
        if not rest:
            self.awaiting_until = time.time() + AWAIT_SEC
            self.send({"type": "wake"})
            return
        self.awaiting_until = 0.0
        cmd = _norm(rest)
        if cmd in STOP:
            self.send({"type": "stop"})
            return
        if cmd in REPEAT:
            self.send({"type": "reply", "text": self.last_reply, "tts": tts.available()})
            return
        self.send({"type": "thinking", "text": rest})
        chat.deliver(self.room, self.user["id"], "user", self.user["username"], f"🎤 {rest}")
        reply = chat.assistant_reply(self.room, self.user)
        chat.deliver(self.room, None, "assistant", config.ASSISTANT_NAME, reply)
        self.last_reply = reply
        self.send({"type": "reply", "text": reply, "tts": tts.available()})


def ws_handsfree(ws, name):
    uid = session.get("user_id")
    user = db.get_user(uid) if uid else None
    room = db.get_room(name)
    if not user or not room:
        return
    out = captions.Listener(ws)  # thread-safe sender

    def send(obj):
        try:
            out.send(json.dumps(obj))
        except Exception:
            pass
    hf = HandsFree(room, user, send)
    audio_q, stop = queue.Queue(maxsize=64), threading.Event()

    def worker():
        rec = stt.create_recognizer()
        while not stop.is_set():
            try:
                chunk = audio_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if chunk is None:
                break
            try:
                for ev in rec.feed(chunk):
                    if ev["type"] == "final" and ev["text"]:
                        hf.handle(ev["text"])
            except Exception as e:
                send({"type": "error", "text": str(e)[:200]})
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    send({"type": "ready", "wake": config.VOICE_WAKE_WORDS[0]})
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str):
                continue
            try:
                audio_q.put_nowait(bytes(msg))
            except queue.Full:  # behind: drop the oldest audio
                try:
                    audio_q.get_nowait()
                    audio_q.put_nowait(bytes(msg))
                except (queue.Empty, queue.Full):
                    pass
    finally:
        stop.set()
        try:
            audio_q.put_nowait(None)
        except queue.Full:
            pass


def register(app):
    stt.ensure_ready()  # raises if Whisper is unavailable -> feature skipped
    Sock(app).route("/ws/voice/<name>")(ws_handsfree)
    app.register_blueprint(bp)
