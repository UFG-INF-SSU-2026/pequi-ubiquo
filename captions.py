"""Shared per-room caption broadcast registry.

Both the browser Live Captions feature and the IoT device microservice publish
caption events here. WebSocket delivery goes through pubsub.py (so it fans out
across workers when Redis is configured). Final segments also fire local hooks
exactly once at the origin (used by the meeting recorder) — those do NOT fan out.

A caption event is: {"type": "partial"|"final", "speaker": str, "text": str}.
"""
import json
import threading
from collections import defaultdict

import pubsub

_rooms = defaultdict(set)      # room name -> set[Listener]  (this process's clients)
_lock = threading.Lock()
_final_hooks = []              # list of fn(room, speaker, text)


class Listener:
    def __init__(self, ws):
        self.ws = ws
        self._send_lock = threading.Lock()

    def send(self, text):
        with self._send_lock:
            self.ws.send(text)


def join(room_name, listener):
    with _lock:
        _rooms[room_name].add(listener)


def leave(room_name, listener):
    with _lock:
        _rooms[room_name].discard(listener)


def add_final_hook(fn):
    """Register fn(room, speaker, text), called once per final segment (origin only)."""
    _final_hooks.append(fn)


def broadcast(room_name, event):
    # Side-effects (e.g. meeting recording) run once, here at the origin.
    if event.get("type") == "final" and event.get("text"):
        for h in _final_hooks:
            try:
                h(room_name, event.get("speaker", ""), event["text"])
            except Exception:
                pass
    # WebSocket delivery (fans out across workers when Redis is configured).
    pubsub.publish("captions", {"room": room_name, "event": event})


def _deliver(data):
    room_name = data["room"]
    payload = json.dumps(data["event"])
    with _lock:
        listeners = list(_rooms.get(room_name, ()))
    dead = []
    for l in listeners:
        try:
            l.send(payload)
        except Exception:
            dead.append(l)
    if dead:
        with _lock:
            for l in dead:
                _rooms.get(room_name, set()).discard(l)


pubsub.register_channel("captions", _deliver)
