"""Live speech-to-text captions (accessibility) — fully offline, via Whisper.

Browser-facing half of transcription: serves a high-contrast "Live Captions"
page per room, and a WebSocket where a browser streams 16 kHz PCM16 mono audio.
The heavy lifting lives in the shared modules:
  - stt.py       — Whisper model (loaded once) + streaming recognizer
  - captions.py  — the per-room caption broadcast registry

Device/IoT audio ingestion is in features/iot.py and publishes to the SAME
captions registry, so an IoT mic in a room appears live on this page too.

Performance: the WS receive loop only enqueues audio; a worker thread runs the
recognizer; a bounded queue drops the oldest frames when behind, so latency
stays bounded. Loads only if faster-whisper + a model are available.
"""
import json
import queue
import threading

from flask import Blueprint, render_template, session, abort
from flask_sock import Sock

import captions
import db
import stt
from auth import login_required, current_user

bp = Blueprint("transcription", __name__, url_prefix="/transcription")

QUEUE_MAX = 32  # ~3.2 s of 0.1 s frames before we drop oldest


@bp.route("/<name>")
@login_required
def page(name):
    room = db.get_room(name)
    if not room:
        abort(404)
    return render_template("transcription.html", room=room, user=current_user())


def _speaker_for(ev, speaker, opts):
    """Shared-mic mode: label each final utterance by voice (voices.LiveTracker)."""
    if not (opts.get("speakers") and ev.get("pcm")):
        return speaker
    import voices
    if not voices.available():
        return speaker
    if "tracker" not in opts:
        opts["tracker"] = voices.LiveTracker()
    try:
        return opts["tracker"].label(stt._pcm16_to_float32(ev["pcm"])) or speaker
    except Exception:
        return speaker


def _save_live_voices(room_name, opts):
    """If this room is recording a meeting, keep the stream's voice clusters so
    renaming "Speaker 2" on the meeting page teaches TowerAI that voice."""
    tracker = opts.get("tracker")
    m = db.active_meeting_for_room(room_name)
    if tracker and m:
        import voices
        for label, centroid, _ in tracker.clusters:
            voices.save_meeting_voice(m["id"], label, centroid)


def _recognizer_worker(room_name, speaker, audio_q, stop, opts=None):
    opts = opts if opts is not None else {}
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
                if ev["type"] == "final" and not ev["text"]:
                    continue
                who = _speaker_for(ev, speaker, opts) if ev["type"] == "final" else speaker
                captions.broadcast(room_name, {"type": ev["type"], "speaker": who, "text": ev["text"]})
        except Exception:
            continue
    try:
        for ev in rec.finish():
            if ev["text"]:
                captions.broadcast(room_name, {"type": "final", "speaker": _speaker_for(ev, speaker, opts),
                                               "text": ev["text"]})
    except Exception:
        pass
    _save_live_voices(room_name, opts)


def ws_transcribe(ws, name):
    if not session.get("user_id"):
        return
    room = db.get_room(name)
    if not room:
        return
    user = db.get_user(session["user_id"])
    speaker = user["username"] if user else "Someone"

    listener = captions.Listener(ws)
    captions.join(name, listener)

    audio_q = queue.Queue(maxsize=QUEUE_MAX)
    stop = threading.Event()
    opts = {}  # {"speakers": bool} — set by the page ("shared microphone")
    worker = None
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str):
                try:
                    opts["speakers"] = bool(json.loads(msg).get("speakers"))
                except (ValueError, AttributeError):
                    pass
                continue
            if worker is None:
                worker = threading.Thread(
                    target=_recognizer_worker, args=(name, speaker, audio_q, stop, opts), daemon=True
                )
                worker.start()
            chunk = bytes(msg)
            try:
                audio_q.put_nowait(chunk)
            except queue.Full:
                try:
                    audio_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    audio_q.put_nowait(chunk)
                except queue.Full:
                    pass
    finally:
        captions.leave(name, listener)
        stop.set()
        if worker is not None:
            try:
                audio_q.put_nowait(None)
            except queue.Full:
                pass
            worker.join(timeout=2)


def register(app):
    """Loaded by the core only if the active STT engine + model are available."""
    stt.ensure_ready()  # raises if engine/model missing -> feature is skipped
    app.logger.info(f"[transcription] STT engine: {stt.engine_desc()}")
    sock = Sock(app)
    sock.route("/ws/transcribe/<name>")(ws_transcribe)
    app.register_blueprint(bp)
