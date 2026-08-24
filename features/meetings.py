"""Meeting transcripts: record a room's live captions into a stored, searchable
transcript you can revisit.

While a meeting is "active" for a room, every FINAL caption segment in that room
(from a browser or an IoT device) is appended to the meeting via a hook on the
shared captions bus (captions.add_final_hook). Recording is decided from the DB
(active_meeting_for_room), so it works correctly even across multiple workers.

This feature does not need the STT engine to load — you can browse and search
past transcripts even when transcription is offline.
"""
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, Response, abort, jsonify
)

import captions
import db
from auth import login_required, current_user

bp = Blueprint("meetings", __name__)


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
    return render_template("meetings.html", meetings=db.list_meetings(), q=q, results=results)


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
    return render_template("meeting.html", meeting=m, segments=db.meeting_segments(meeting_id))


@bp.route("/meetings/<int:meeting_id>/export")
@login_required
def export(meeting_id):
    m = db.get_meeting(meeting_id)
    if not m:
        abort(404)
    lines = [f"# {m['title'] or 'Meeting'}  (room: {m['room']})", ""]
    for s in db.meeting_segments(meeting_id):
        stamp = datetime.fromtimestamp(s["ts"]).strftime("%H:%M:%S")
        lines.append(f"[{stamp}] {s['speaker']}: {s['text']}")
    body = "\n".join(lines) + "\n"
    return Response(body, mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.txt"'})


def register(app):
    captions.add_final_hook(_on_final)
    app.register_blueprint(bp)
