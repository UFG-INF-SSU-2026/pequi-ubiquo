"""Meeting transcripts: record a room's live captions into a stored, searchable
transcript you can revisit.

While a meeting is "active" for a room, every FINAL caption segment in that room
(from a browser or an IoT device) is appended to the meeting via a hook on the
shared captions bus (captions.add_final_hook). Recording is decided from the DB
(active_meeting_for_room), so it works correctly even across multiple workers.

Recordings can also be uploaded (recordings.py): they're transcribed with
Whisper and, when the diarization models are installed, split by speaker
("Speaker 1", "Speaker 2"… — renameable). Any meeting can be summarized by the
local LLM into a summary, decisions and a checklist of action items
(summarizer.py). Uploads and summaries run as background jobs; the meeting's
`status` column tracks progress and the page refreshes itself until done.

This feature does not need the STT engine to load — you can browse and search
past transcripts even when transcription is offline.
"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, Response, abort, jsonify, flash
)
from werkzeug.utils import secure_filename

import captions
import config
import db
import diarize
import recordings
import summarizer
from auth import login_required, current_user

bp = Blueprint("meetings", __name__)

RUNNING = ("queued", "transcribing", "diarizing", "summarizing")
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
              ".webm", ".mp4", ".mkv", ".mov", ".wma", ".amr", ".3gp"}

# Heavy jobs (Whisper over a whole file) run one at a time so live captions keep
# up; LLM summaries are light on this machine and get their own lane.
_transcribe_lane = threading.Semaphore(1)
_summary_lane = threading.Semaphore(1)


# --- Background jobs -------------------------------------------------------
def _status_writer(meeting_id):
    last = {}

    def write(status, detail=None):
        if last.get("v") != (status, detail):  # skip redundant DB writes
            last["v"] = (status, detail)
            db.set_meeting_status(meeting_id, status, detail)
    return write


def _run_job(meeting_id, lane, fn):
    def target():
        with lane:
            try:
                fn()
            except Exception as e:
                db.set_meeting_status(meeting_id, "error", str(e)[:300])
    threading.Thread(target=target, daemon=True).start()


def _summarize_job(meeting_id):
    status = _status_writer(meeting_id)
    status("summarizing", "reading transcript")
    notes = summarizer.summarize(db.meeting_segments(meeting_id),
                                 on_progress=lambda d: status("summarizing", d))
    db.save_meeting_notes(meeting_id, json.dumps(notes, ensure_ascii=False))
    db.set_meeting_status(meeting_id, None)
    import notifications
    notifications.on_action_items(db.get_meeting(meeting_id), notes["action_items"])


def _upload_job(meeting_id, path, num_speakers, use_diarization, then_summarize):
    status = _status_writer(meeting_id)
    started = db.get_meeting(meeting_id)["started_at"]
    try:
        segs, duration, _, voice_embs = recordings.process(path, num_speakers, use_diarization,
                                                           on_status=status)
    finally:
        if not config.KEEP_RECORDINGS:
            try:
                os.remove(path)
            except OSError:
                pass
    db.add_segments(meeting_id, [(spk, text, started + off, off) for spk, text, off in segs])
    import voices
    for label, emb in voice_embs.items():  # lets a later rename teach TowerAI this voice
        voices.save_meeting_voice(meeting_id, label, emb)
    db.set_meeting_ended(meeting_id, started + duration)
    db.set_meeting_status(meeting_id, None)
    if then_summarize and segs:
        _run_job(meeting_id, _summary_lane, lambda: _summarize_job(meeting_id))


def _notes(m):
    try:
        return json.loads(m["notes"]) if m["notes"] else None
    except ValueError:
        return None


def _on_final(room, speaker, text):
    """Caption hook: persist a final segment if a meeting is recording this room."""
    m = db.active_meeting_for_room(room)
    if m:
        db.add_segment(m["id"], speaker, text)


@bp.route("/meetings")
@login_required
def index():
    q = (request.args.get("q") or "").strip()
    results = db.search_segments(q) if q else None
    return render_template("meetings.html", meetings=db.list_meetings(), q=q, results=results,
                           diarization=diarize.available(), max_upload_mb=config.MAX_UPLOAD_MB)


@bp.route("/meetings/upload", methods=["POST"])
@login_required
def upload():
    f = request.files.get("recording")
    if not f or not f.filename:
        flash("Choose a recording to upload.", "error")
        return redirect(url_for("meetings.index"))
    ext = Path(secure_filename(f.filename)).suffix.lower()
    if ext not in AUDIO_EXTS:
        flash(f"Unsupported file type '{ext or '?'}'. Use an audio or video file.", "error")
        return redirect(url_for("meetings.index"))

    room = (request.form.get("room") or "general").strip()[:32] or "general"
    title = (request.form.get("title") or "").strip()[:120] or Path(f.filename).stem[:120]
    num_speakers = request.form.get("num_speakers", type=int)
    num_speakers = num_speakers if num_speakers and 1 <= num_speakers <= 20 else None
    use_diarization = bool(request.form.get("diarize"))
    then_summarize = bool(request.form.get("summarize"))

    meeting_id = db.create_upload_meeting(room, title, current_user()["id"])
    rec_dir = Path(config.RECORDINGS_DIR)
    rec_dir.mkdir(parents=True, exist_ok=True)
    path = str(rec_dir / f"meeting-{meeting_id}{ext}")
    f.save(path)
    _run_job(meeting_id, _transcribe_lane,
             lambda: _upload_job(meeting_id, path, num_speakers, use_diarization, then_summarize))
    return redirect(url_for("meetings.view", meeting_id=meeting_id))


@bp.route("/meetings/start", methods=["POST"])
@login_required
def start():
    room = (request.form.get("room") or "general").strip()[:32]
    title = (request.form.get("title") or "").strip()[:120] or f"{room} — {datetime.now():%Y-%m-%d %H:%M}"
    if not db.active_meeting_for_room(room):
        db.start_meeting(room, title, current_user()["id"])
    # Return JSON for fetch() callers (captions page), redirect for form posts.
    if request.headers.get("Accept", "").startswith("application/json"):
        m = db.active_meeting_for_room(room)
        return jsonify(recording=True, meeting_id=m["id"] if m else None, room=room)
    return redirect(url_for("meetings.index"))


@bp.route("/meetings/<int:meeting_id>/stop", methods=["POST"])
@login_required
def stop(meeting_id):
    db.stop_meeting(meeting_id)
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify(recording=False, meeting_id=meeting_id)
    return redirect(url_for("meetings.view", meeting_id=meeting_id))


@bp.route("/meetings/status")
@login_required
def status():
    """Is a meeting recording this room? Used by the captions page toggle."""
    room = (request.args.get("room") or "general").strip()
    m = db.active_meeting_for_room(room)
    return jsonify(recording=bool(m), meeting_id=m["id"] if m else None)


@bp.route("/meetings/<int:meeting_id>")
@login_required
def view(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        abort(404)
    speakers = db.meeting_speakers(meeting_id)
    colors = {sp["speaker"]: i % 8 for i, sp in enumerate(speakers)}
    return render_template("meeting.html", meeting=m, segments=db.meeting_segments(meeting_id),
                           speakers=speakers, colors=colors, notes=_notes(m),
                           running=m["status"] in RUNNING, fmt=summarizer.fmt_offset)


@bp.route("/meetings/<int:meeting_id>/summarize", methods=["POST"])
@login_required
def summarize(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        abort(404)
    if m["status"] not in RUNNING:
        db.set_meeting_status(meeting_id, "summarizing", "queued")
        _run_job(meeting_id, _summary_lane, lambda: _summarize_job(meeting_id))
    return redirect(url_for("meetings.view", meeting_id=meeting_id))


@bp.route("/meetings/<int:meeting_id>/actions/<int:idx>/toggle", methods=["POST"])
@login_required
def toggle_action(meeting_id, idx):
    m = db.get_meeting(meeting_id)
    notes = _notes(m) if m else None
    if not notes or not 0 <= idx < len(notes["action_items"]):
        abort(404)
    item = notes["action_items"][idx]
    item["done"] = not item.get("done")
    db.save_meeting_notes(meeting_id, json.dumps(notes, ensure_ascii=False))
    return redirect(url_for("meetings.view", meeting_id=meeting_id) + "#actions")


@bp.route("/meetings/<int:meeting_id>/speakers", methods=["POST"])
@login_required
def rename_speaker(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        abort(404)
    old = (request.form.get("old") or "").strip()
    new = (request.form.get("new") or "").strip()[:64]
    if old and new and old != new:
        db.rename_speaker(meeting_id, old, new)
        import voices
        if voices.learn_from_rename(meeting_id, old, new):
            flash(f"🎙 Voice remembered — {new} will be recognised in future recordings.", "ok")
        notes = _notes(m)
        if notes:  # keep action-item owners in sync with the new name
            for it in notes["action_items"]:
                if it.get("owner") == old:
                    it["owner"] = new
            db.save_meeting_notes(meeting_id, json.dumps(notes, ensure_ascii=False))
    return redirect(url_for("meetings.view", meeting_id=meeting_id))


@bp.route("/meetings/<int:meeting_id>/export")
@login_required
def export(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        abort(404)
    lines = [f"# {m['title'] or 'Meeting'}  (room: {m['room']})", ""]
    notes = _notes(m)
    if notes:
        lines += ["## Summary", "", notes["summary"], ""]
        if notes["decisions"]:
            lines += ["## Decisions", ""] + [f"- {d}" for d in notes["decisions"]] + [""]
        if notes["action_items"]:
            lines += ["## Action items", ""]
            for it in notes["action_items"]:
                extra = ", ".join(x for x in (it.get("owner"), it.get("due")) if x)
                lines.append(f"- [{'x' if it.get('done') else ' '}] {it['task']}"
                             + (f" ({extra})" if extra else ""))
            lines.append("")
        lines += ["## Transcript", ""]
    for s in db.meeting_segments(meeting_id):
        if s["start_sec"] is not None:
            stamp = summarizer.fmt_offset(s["start_sec"])
        else:
            stamp = datetime.fromtimestamp(s["ts"]).strftime("%H:%M:%S")
        lines.append(f"[{stamp}] {s['speaker']}: {s['text']}")
    body = "\n".join(lines) + "\n"
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.txt"'})


@bp.route("/meetings/<int:meeting_id>/segments/<int:segment_id>", methods=["POST"])
@login_required
def edit_segment(meeting_id, segment_id):
    """Correct a transcript line; new words become Whisper vocabulary."""
    import vocab
    text = (request.form.get("text") or "").strip()[:2000]
    old = db.update_segment(meeting_id, segment_id, text) if text else None
    if old is not None:
        learned = vocab.learn_from_correction(old, text)
        if learned:
            flash("📖 Learned: " + ", ".join(learned), "ok")
    return redirect(url_for("meetings.view", meeting_id=meeting_id) + f"#seg-{segment_id}")


@bp.route("/voices", methods=["GET", "POST"])
@login_required
def voices_page():
    """Remembered voices and the learned vocabulary."""
    import vocab
    import voices
    if request.method == "POST":
        if request.form.get("add_term"):
            vocab.add(request.form["add_term"])
        elif request.form.get("remove_term"):
            vocab.remove(request.form["remove_term"])
        elif request.form.get("forget_voice"):
            if not current_user()["is_admin"]:
                abort(403)
            voices.forget(int(request.form["forget_voice"]))
        return redirect(url_for("meetings.voices_page"))
    return render_template("voices.html", voices=voices.all_voiceprints(), terms=vocab.terms(),
                           available=voices.available(), glossary=vocab.prompt())


def register(app):
    db.init_db()
    db.reset_stale_jobs()
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024
    captions.add_final_hook(_on_final)
    app.register_blueprint(bp)
