"""Shared collaboration rooms with an LLM participant.

Difference from the old app.py chat: messages are persisted to SQLite and a
room is *shared* — every logged-in member sees the same conversation. The
assistant replies into the room (visible to all) when addressed with `@tower`
or when the client sets `ask_ai`.
"""
from flask import (
    Blueprint, render_template, request, redirect, url_for, jsonify, abort
)

import config
import db
import llm
from auth import login_required, current_user

bp = Blueprint("chat", __name__)

SYSTEM_PROMPT = (
    f"You are {config.ASSISTANT_NAME}, a helpful AI assistant in a shared offline "
    "group chat on a local network. Several people may talk at once. Human "
    "messages are prefixed with the speaker's name (e.g. 'alice: ...'); your own "
    "messages are not prefixed. Be concise and helpful, answer only what is asked, "
    "and reply in the language the user is using."
)


def _mentions_assistant(text: str) -> bool:
    low = text.lower()
    return low.startswith("@tower") or low.startswith("@" + config.ASSISTANT_NAME.lower())


def _build_context(room_id):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for r in db.recent_messages(room_id, limit=20):
        if r["role"] == "assistant":
            msgs.append({"role": "assistant", "content": r["content"]})
        else:
            msgs.append({"role": "user", "content": f'{r["username"]}: {r["content"]}'})
    return msgs


@bp.route("/")
@login_required
def index():
    return redirect(url_for("chat.room", name="general"))


@bp.route("/rooms/<name>")
@login_required
def room(name):
    r = db.get_room(name)
    if not r:
        abort(404)
    return render_template(
        "chat.html",
        room=r,
        rooms=db.list_rooms(),
        user=current_user(),
        assistant_name=config.ASSISTANT_NAME,
    )


@bp.route("/rooms", methods=["POST"])
@login_required
def make_room():
    name = (request.form.get("name") or "").strip().lower().replace(" ", "-")
    name = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:32]
    if name and not db.get_room(name):
        db.create_room(name)
    return redirect(url_for("chat.room", name=name or "general"))


@bp.route("/api/rooms/<name>/messages")
@login_required
def get_messages(name):
    r = db.get_room(name)
    if not r:
        abort(404)
    since = request.args.get("since", default=0, type=int)
    rows = db.messages_after(r["id"], since_id=since)
    return jsonify([dict(row) for row in rows])


@bp.route("/api/rooms/<name>/messages", methods=["POST"])
@login_required
def post_message(name):
    r = db.get_room(name)
    if not r:
        abort(404)
    user = current_user()
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify(error="empty message"), 400

    db.add_message(r["id"], user["id"], "user", user["username"], content)

    ask_ai = bool(data.get("ask_ai")) or _mentions_assistant(content)
    if ask_ai:
        try:
            reply = llm.chat_completion(_build_context(r["id"]))
        except Exception as e:  # surface failure into the room, don't 500
            reply = f"[{config.ASSISTANT_NAME} is unavailable: {e}]"
        db.add_message(r["id"], None, "assistant", config.ASSISTANT_NAME, reply)

    return jsonify(ok=True)


@bp.route("/health")
def health():
    ok, base, models = llm.health()
    return jsonify(
        llm_ok=ok,
        llm_endpoint=base,
        models=models,
        rooms=len(db.list_rooms()),
    )
