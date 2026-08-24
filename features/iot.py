"""IoT audio-transcription microservice.

Lets network devices (ESP32 mics, Raspberry Pis, phones, other services) send
audio and get transcripts, authenticating with an API key instead of a browser
login. Two ingestion modes:

  - Batch:    POST /api/stt/transcribe   (raw PCM16 mono, or a WAV)  -> {text}
  - Streaming: WS  /api/stt/stream        (PCM16 mono frames)         -> caption events

Both reuse the shared Vosk engine (stt.py). If a `room` is given (query param, or
the device's default room), transcripts are ALSO published to that room's Live
Captions page (captions.py) — so an IoT mic shows up live for human viewers.

Human provisioning UI (login required): GET/POST /iot to mint & revoke keys.

This blueprint is loaded into the main app as an optional feature, and is also
served standalone by stt_service.py as an independent microservice.
"""
import json
import queue
import threading

from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify, g
)
from flask_sock import Sock

import captions
import db
import devices
import stt
from auth import login_required

bp = Blueprint("iot", __name__)

QUEUE_MAX = 32


# --- Provisioning UI (humans) ---------------------------------------------
@bp.route("/iot")
@login_required
def manage():
    return render_template("iot.html", devices=db.list_devices(), new_key=None, new_name=None)


@bp.route("/iot/devices", methods=["POST"])
@login_required
def create():
    name = (request.form.get("name") or "").strip()[:64]
    room = (request.form.get("room") or "").strip()[:32] or None
    if not name:
        return redirect(url_for("iot.manage"))
    _, key = devices.create_device(name, room)
    # Show the plaintext key exactly once.
    return render_template("iot.html", devices=db.list_devices(), new_key=key, new_name=name)


@bp.route("/iot/devices/<int:device_id>/delete", methods=["POST"])
@login_required
def revoke(device_id):
    db.delete_device(device_id)
    return redirect(url_for("iot.manage"))


# --- Batch ingestion (devices) --------------------------------------------
@bp.route("/api/stt/transcribe", methods=["POST"])
@devices.require_device
def transcribe():
    data = request.get_data()
    if not data:
        return jsonify(error="empty body"), 400
    try:
        if data[:4] == b"RIFF":          # a WAV container
            text = stt.transcribe_wav(data)
        else:                            # raw PCM16 mono
            rate = request.args.get("rate", default=stt.SAMPLE_RATE, type=int)
            text = stt.transcribe_pcm16(data, rate)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"transcription failed: {e}"), 500

    room = request.args.get("room") or g.device["room"]
    if room and text:
        captions.broadcast(room, {"type": "final", "speaker": g.device["name"], "text": text})
    return jsonify(text=text, device=g.device["name"], room=room)


# --- Streaming ingestion (devices) ----------------------------------------
def ws_stream(ws):
    device = devices.verify(devices.extract_key())
    if not device:
        return  # closes socket
    room = request.args.get("room") or device["room"]
    speaker = device["name"]
    dev_sender = captions.Listener(ws)   # locked sender back to the device

    audio_q = queue.Queue(maxsize=QUEUE_MAX)
    stop = threading.Event()

    def emit(kind, text):
        evt = {"type": kind, "speaker": speaker, "text": text}
        try:
            dev_sender.send(json.dumps(evt))
        except Exception:
            pass
        if room:
            captions.broadcast(room, evt)

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
                    if ev["type"] == "final" and not ev["text"]:
                        continue
                    emit(ev["type"], ev["text"])
            except Exception:
                continue
        try:
            for ev in rec.finish():
                if ev["text"]:
                    emit("final", ev["text"])
        except Exception:
            pass

    wt = None
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            if isinstance(msg, str):
                continue
            if wt is None:
                wt = threading.Thread(target=worker, daemon=True)
                wt.start()
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
        stop.set()
        if wt is not None:
            try:
                audio_q.put_nowait(None)
            except queue.Full:
                pass
            wt.join(timeout=2)


def register(app):
    """Loaded by the core only if the active STT engine + model are available."""
    stt.ensure_ready()  # raises if engine/model missing -> feature is skipped
    sock = Sock(app)
    sock.route("/api/stt/stream")(ws_stream)
    app.register_blueprint(bp)
