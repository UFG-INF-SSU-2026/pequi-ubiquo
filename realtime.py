"""WebSocket layer for instant messaging.

Primary transport for the chat rooms. The REST endpoints in chat.py remain as a
fallback for clients that can't open a WebSocket. Native browser WebSocket is
used on the client side (no bundled JS library), which keeps this fully offline.

Concurrency model: flask-sock runs each connection in its own thread under the
threaded WSGI server. A registry maps room name -> set of Connection objects;
broadcasts iterate that set, sending under a per-connection lock. The LLM call
(which can take seconds) is dispatched to a worker thread so it never blocks the
sender's receive loop.
"""
import json
import time
import threading
from collections import defaultdict

from flask import session
from flask_sock import Sock

import config
import db
import llm
import pubsub
from chat import _mentions_assistant, _build_context

sock = Sock()

_rooms = defaultdict(set)      # room name -> set[Connection]  (this process's clients)
_registry_lock = threading.Lock()


class Connection:
    def __init__(self, ws, user):
        self.ws = ws
        self.user = user
        self._send_lock = threading.Lock()

    def send(self, text):
        with self._send_lock:
            self.ws.send(text)


def _join(room_name, conn):
    with _registry_lock:
        _rooms[room_name].add(conn)


def _leave(room_name, conn):
    with _registry_lock:
        _rooms[room_name].discard(conn)


def _broadcast(room_name, message: dict):
    # Fans out across workers when Redis is configured (see pubsub.py).
    pubsub.publish("chat", {"room": room_name, "message": message})


def _deliver(data):
    room_name = data["room"]
    payload = json.dumps(data["message"])
    with _registry_lock:
        conns = list(_rooms.get(room_name, ()))
    dead = []
    for conn in conns:
        try:
            conn.send(payload)
        except Exception:
            dead.append(conn)
    if dead:
        with _registry_lock:
            for conn in dead:
                _rooms.get(room_name, set()).discard(conn)


pubsub.register_channel("chat", _deliver)


def _persist_and_broadcast(room_name, room_id, user_id, role, username, content):
    mid = db.add_message(room_id, user_id, role, username, content)
    _broadcast(room_name, {
        "id": mid, "role": role, "username": username,
        "content": content, "created_at": time.time(),
    })


def _handle_ai(room_name, room_id):
    """Runs in a worker thread: call the LLM, then broadcast its reply."""
    try:
        reply = llm.chat_completion(_build_context(room_id))
    except Exception as e:
        reply = f"[{config.ASSISTANT_NAME} is unavailable: {e}]"
    _persist_and_broadcast(
        room_name, room_id, None, "assistant", config.ASSISTANT_NAME, reply
    )


def register(app):
    sock.init_app(app)


@sock.route("/ws/rooms/<name>")
def ws_room(ws, name):
    # Authenticate from the signed session cookie carried on the upgrade request.
    uid = session.get("user_id")
    user = db.get_user(uid) if uid else None
    room = db.get_room(name)
    if not user or not room:
        return  # closes the socket

    conn = Connection(ws, user)
    _join(name, conn)
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            content = (data.get("content") or "").strip()
            if not content:
                continue

            _persist_and_broadcast(
                name, room["id"], user["id"], "user", user["username"], content
            )

            if data.get("ask_ai") or _mentions_assistant(content):
                threading.Thread(
                    target=_handle_ai, args=(name, room["id"]), daemon=True
                ).start()
    finally:
        _leave(name, conn)
