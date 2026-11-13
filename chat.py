"""Shared collaboration rooms with an LLM participant.

Difference from the old app.py chat: messages are persisted to SQLite and a
room is *shared* — every logged-in member sees the same conversation. The
assistant replies into the room (visible to all) when addressed with `@tower`
or when the client sets `ask_ai`.
"""
import json
import re

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


# Messages can carry an attached image as a first line "[image:<file>]"
# (features/vision.py); the text model only sees a placeholder for it.
IMAGE_RE = re.compile(r"^\[image:([\w.-]+)\]\n?")


def _build_context(room_id):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for r in db.recent_messages(room_id, limit=20):
        if r["role"] == "assistant":
            msgs.append({"role": "assistant", "content": r["content"]})
        else:
            text = IMAGE_RE.sub("(shared an image) ", r["content"])
            msgs.append({"role": "user", "content": f'{r["username"]}: {text}'})
    return msgs


def assistant_reply(room, user=None, prompt=None):
    """The assistant's answer to the room's latest messages (plus `prompt`, for
    scheduled tasks) — via the agent when enabled, else a plain completion.
    Shows live progress and the answer streaming in, and appends approval
    buttons ([confirm:<id>]) for actions that wait for a person's OK."""
    msgs = _build_context(room["id"])
    if prompt:
        msgs.append({"role": "user", "content": prompt})
    progress = lambda text: _live_status(room, text)  # noqa: E731
    progress(f"{config.ASSISTANT_NAME} is thinking…")
    try:
        if not config.AGENT_ENABLED:
            return llm.chat_completion(msgs)
        import agent
        ctx = agent.Context(user=user, room=room, confirm=True)
        out = agent.run(msgs, ctx, on_progress=progress, on_token=_streamer(room))
        reply = out["reply"] or "…"
        if config.AGENT_SHOW_TOOLS and out["steps"]:
            used = list(dict.fromkeys(s["tool"] for s in out["steps"]))
            reply += "\n\n🔧 " + ", ".join(used)
        for pid, desc in ctx.pending:
            reply += f"\n[confirm:{pid}] {desc}"
        return reply
    except Exception as e:  # surface failure into the room, don't 500
        return f"[{config.ASSISTANT_NAME} is unavailable: {e}]"


def _live_status(room, text, kind="agent_status"):
    """Transient line for WebSocket clients (not stored): what the assistant is
    doing, or its answer as it streams in."""
    try:
        import realtime
        realtime._broadcast(room["name"], {"type": kind, "text": text})
    except ImportError:
        pass


def _streamer(room, every=0.15):
    """on_token callback that pushes the growing answer at most every `every` s."""
    import time
    last = [0.0]

    def push(text):
        now = time.time()
        if now - last[0] >= every:
            last[0] = now
            _live_status(room, text, "agent_stream")
    return push


def deliver(room, user_id, role, username, content):
    """Store a message and push it to the room live (WebSocket when available;
    polling clients pick it up from the DB either way). Notifies @mentions."""
    try:
        import realtime
        realtime._persist_and_broadcast(room["name"], room["id"], user_id, role, username, content)
    except ImportError:
        db.add_message(room["id"], user_id, role, username, content)
        if role == "user":
            import notifications
            notifications.on_message(room["name"], username, content)


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

    deliver(r, user["id"], "user", user["username"], content)

    ask_ai = bool(data.get("ask_ai")) or _mentions_assistant(content)
    if ask_ai:
        reply = assistant_reply(r, user)
        deliver(r, None, "assistant", config.ASSISTANT_NAME, reply)

    return jsonify(ok=True)


@bp.route("/api/agent", methods=["POST"])
@login_required
def agent_api():
    """Run the agent on one prompt: {"prompt", "room"?} -> {"reply", "steps", "protocol"}."""
    import agent
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify(error="empty prompt"), 400
    room = db.get_room(data.get("room") or "general")
    try:
        out = agent.run([{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": prompt}],
                        agent.Context(user=current_user(), room=room))
    except Exception as e:
        return jsonify(error=str(e)), 502
    return jsonify(out)


@bp.route("/api/agent/pending/<int:pid>", methods=["POST"])
@login_required
def decide_pending(pid):
    """Approve or reject an action the assistant asked to take."""
    import agent
    approve = (request.get_json(silent=True) or {}).get("decision") == "approve"
    user = current_user()
    try:
        row, result = agent.execute_pending(pid, user, approve)
    except LookupError as e:
        return jsonify(error=str(e)), 404
    except PermissionError as e:
        return jsonify(error=str(e)), 403
    except ValueError as e:
        return jsonify(error=str(e)), 409
    room = db.get_room(row["room"])
    if not approve:
        text = f"❌ {user['username']} rejected: {agent.describe_call(row['tool'], json.loads(row['arguments']))}"
    elif isinstance(result, dict) and "error" in result:
        text = f"⚠️ {row['tool']} failed: {result['error']}"
    else:
        text = f"✅ {user['username']} approved {row['tool']} — done."
    if room:
        deliver(room, None, "assistant", config.ASSISTANT_NAME, text)
    return jsonify(ok=True, status="approved" if approve else "rejected", result=result)


@bp.route("/api/messages/<int:message_id>/feedback", methods=["POST"])
@login_required
def feedback(message_id):
    """👍 / 👎 (+ optional corrected answer) on an assistant reply (learning.py)."""
    import learning
    data = request.get_json(silent=True) or {}
    try:
        learning.rate(message_id, current_user()["id"], int(data.get("rating", 0)), data.get("correction"))
    except (ValueError, TypeError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


@bp.route("/api/rooms/<name>/read", methods=["POST"])
@login_required
def mark_read(name):
    import notifications
    r = db.get_room(name)
    if not r:
        abort(404)
    last_id = (request.get_json(silent=True) or {}).get("last_id", 0)
    notifications.mark_read(current_user()["id"], r["id"], last_id)
    return jsonify(ok=True)


@bp.route("/api/notifications")
@login_required
def notifications_api():
    import notifications
    uid = current_user()["id"]
    since = request.args.get("since", 0, type=int)
    return jsonify(
        unread=notifications.unread_count(uid),
        rooms=notifications.unread_counts(uid),
        items=[dict(n) for n in notifications.recent(uid, since)],
    )


@bp.route("/notifications")
@login_required
def notifications_page():
    import notifications
    uid = current_user()["id"]
    items = notifications.recent(uid, 0, 100)
    notifications.mark_all_read(uid)
    return render_template("notifications.html", items=items)


@bp.route("/health")
def health():
    """Up/down for monitors; endpoint, models and room count only when logged in."""
    ok, base, models = llm.health()
    if not current_user():
        return jsonify(ok=True, llm_ok=ok)
    return jsonify(
        ok=True,
        llm_ok=ok,
        llm_endpoint=base,
        models=models,
        rooms=len(db.list_rooms()),
    )
