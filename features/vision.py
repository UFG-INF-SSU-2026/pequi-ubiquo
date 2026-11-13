"""Vision: share an image in a chat room and ask the local AI about it.

POST /vision/rooms/<room>/image (multipart: image, question) stores the image,
posts it to the room as a message ("[image:<file>]" + question) and has the
assistant answer in a background thread, using an OpenAI-compatible vision
request (image sent inline as a data URL) to the same local LLM server. Load a
vision-capable model in LM Studio / Ollama (e.g. Qwen2.5-VL, LLaVA, Gemma 3), or
set VISION_MODEL to pick one. Images are served only to logged-in users.
"""
import base64
import threading
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, abort, send_from_directory

import chat
import config
import db
import llm
from auth import login_required, current_user

bp = Blueprint("vision", __name__, url_prefix="/vision")

# magic bytes -> (extension, mime type); no image library needed
_FORMATS = [
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
]
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _sniff(data):
    for magic, ext, _ in _FORMATS:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _images_dir():
    d = Path(config.IMAGES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _answer(room, image_path, question, asker):
    """Background: ask the vision model about the image, post its reply."""
    data_url = (f"data:{_MIME[image_path.suffix]};base64,"
                + base64.b64encode(image_path.read_bytes()).decode())
    msgs = chat._build_context(room["id"])
    msgs[-1] = {"role": "user", "content": [
        {"type": "text", "text": f"{asker}: {question or 'Describe this image.'}"},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}
    try:
        reply = llm.chat_completion(msgs, timeout=(3, config.VISION_TIMEOUT_SEC),
                                    model=config.VISION_MODEL or None)
    except Exception as e:
        reply = (f"[{config.ASSISTANT_NAME} couldn't read the image — is a vision-capable "
                 f"model loaded? {e}]")
    chat.deliver(room, None, "assistant", config.ASSISTANT_NAME, reply)


@bp.route("/rooms/<name>/image", methods=["POST"])
@login_required
def upload(name):
    room = db.get_room(name)
    if not room:
        abort(404)
    f = request.files.get("image")
    data = f.read(config.VISION_MAX_MB * 1024 * 1024 + 1) if f else b""
    if not data:
        return jsonify(error="no image"), 400
    if len(data) > config.VISION_MAX_MB * 1024 * 1024:
        return jsonify(error=f"image larger than {config.VISION_MAX_MB} MB"), 413
    ext = _sniff(data)
    if not ext:
        return jsonify(error="unsupported image (use PNG, JPEG, GIF or WebP)"), 400

    path = _images_dir() / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    question = (request.form.get("question") or "").strip()[:2000]
    user = current_user()
    chat.deliver(room, user["id"], "user", user["username"],
                 f"[image:{path.name}]" + (f"\n{question}" if question else ""))
    if request.form.get("ask_ai", "1") != "0":
        threading.Thread(target=_answer, args=(room, path, question, user["username"]),
                         daemon=True).start()
    return jsonify(ok=True, image=path.name)


@bp.route("/images/<filename>")
@login_required
def image(filename):
    return send_from_directory(_images_dir(), filename, max_age=86400)


def register(app):
    app.register_blueprint(bp)
