"""Shared files per room: upload, download, delete — and readable by the
assistant (list_files / search_files / read_file tools).

Text is extracted at upload time (plain-text formats, PDF via pypdf, Word
.docx) and stored for search. Files are always served as downloads
(Content-Disposition: attachment), so an uploaded HTML/SVG can't run scripts in
the app's origin.
"""
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path

from flask import (Blueprint, abort, flash, redirect, render_template, request, send_from_directory,
                   url_for)
from werkzeug.utils import secure_filename

import config
import db
from auth import current_user, login_required

bp = Blueprint("files", __name__)
TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml", ".ini",
             ".py", ".js", ".html", ".css", ".sql", ".srt", ".vtt"}
MAX_TEXT = 300_000


def files_dir():
    d = Path(config.FILES_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def extract_text(path, ext):
    """Best-effort plain text for search and the assistant ('' if unknown)."""
    try:
        if ext in TEXT_EXTS:
            return path.read_bytes()[:MAX_TEXT * 2].decode("utf-8", errors="ignore")[:MAX_TEXT]
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            out = []
            for page in reader.pages:
                out.append(page.extract_text() or "")
                if sum(map(len, out)) > MAX_TEXT:
                    break
            return "\n".join(out)[:MAX_TEXT]
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)[:MAX_TEXT]
    except Exception:
        return ""
    return ""


def save_upload(room, storage, user):
    """Store an uploaded file for a room; returns its id. Raises ValueError."""
    name = secure_filename(storage.filename or "") or "file"
    data = storage.read(config.FILES_MAX_MB * 1024 * 1024 + 1)
    if not data:
        raise ValueError("empty file")
    if len(data) > config.FILES_MAX_MB * 1024 * 1024:
        raise ValueError(f"files are limited to {config.FILES_MAX_MB} MB")
    ext = Path(name).suffix.lower()
    stored = uuid.uuid4().hex + ext
    path = files_dir() / stored
    path.write_bytes(data)
    import time
    with db.get_db() as c:
        return c.execute(
            "INSERT INTO files (room, filename, stored_name, size, mime, text, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (room["name"], name, stored, len(data), mimetypes.guess_type(name)[0],
             extract_text(path, ext), user["id"] if user else None, time.time())).lastrowid


def get(file_id):
    with db.get_db() as c:
        return c.execute("SELECT f.*, u.username FROM files f LEFT JOIN users u ON u.id = f.uploaded_by "
                         "WHERE f.id = ?", (file_id,)).fetchone()


def list_room(room_name):
    with db.get_db() as c:
        return c.execute("SELECT f.id, f.filename, f.size, f.created_at, f.uploaded_by, u.username, "
                         "length(f.text) AS text_len FROM files f LEFT JOIN users u ON u.id = f.uploaded_by "
                         "WHERE f.room = ? ORDER BY f.id DESC", (room_name,)).fetchall()


def search(query, room_name=None, limit=10):
    sql = "SELECT id, room, filename, text FROM files WHERE text LIKE ?"
    args = [f"%{query}%"]
    if room_name:
        sql += " AND room = ?"
        args.append(room_name)
    with db.get_db() as c:
        rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    out = []
    for r in rows:
        i = r["text"].lower().find(query.lower())
        out.append({"file_id": r["id"], "room": r["room"], "filename": r["filename"],
                    "snippet": r["text"][max(0, i - 150):i + 250].replace("\n", " ")})
    return out


def delete(file_id):
    f = get(file_id)
    if f:
        (files_dir() / f["stored_name"]).unlink(missing_ok=True)
        with db.get_db() as c:
            c.execute("DELETE FROM files WHERE id = ?", (file_id,))
    return f


@bp.route("/rooms/<name>/files", methods=["GET", "POST"])
@login_required
def room_files(name):
    import chat
    room = db.get_room(name)
    if not room:
        abort(404)
    if request.method == "POST":
        user = current_user()
        for storage in request.files.getlist("file"):
            if not storage or not storage.filename:
                continue
            try:
                fid = save_upload(room, storage, user)
            except ValueError as e:
                flash(f"{storage.filename}: {e}", "error")
                continue
            f = get(fid)
            chat.deliver(room, user["id"], "user", user["username"], f"shared [file:{fid}:{f['filename']}]")
        return redirect(url_for("files.room_files", name=name))
    return render_template("files.html", room=room, files=list_room(name), max_mb=config.FILES_MAX_MB)


@bp.route("/files/<int:file_id>/download")
@login_required
def download(file_id):
    f = get(file_id)
    if not f:
        abort(404)
    return send_from_directory(files_dir(), f["stored_name"], as_attachment=True,
                               download_name=f["filename"], mimetype="application/octet-stream")


@bp.route("/files/<int:file_id>/delete", methods=["POST"])
@login_required
def remove(file_id):
    f = get(file_id)
    if not f:
        abort(404)
    user = current_user()
    if not (user["is_admin"] or f["uploaded_by"] == user["id"]):
        abort(403)
    delete(file_id)
    return redirect(url_for("files.room_files", name=f["room"]))


def register(app):
    app.register_blueprint(bp)
