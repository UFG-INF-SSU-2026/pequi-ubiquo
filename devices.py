"""Device identity & API-key auth for IoT ingestion.

Network devices (ESP32 mics, Raspberry Pis, phones) can't do cookie login, so
they authenticate with a bearer API key. The plaintext key is shown to the
human ONCE at creation; only its SHA-256 hash is stored.
"""
import hashlib
import secrets
import time
from functools import wraps

from flask import request, jsonify, g

import db

KEY_PREFIX = "tk_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_device(name: str, room: str = None):
    """Create a device and return (device_id, plaintext_key). Key is shown once."""
    key = KEY_PREFIX + secrets.token_hex(24)
    device_id = db.add_device(name, _hash(key), room)
    return device_id, key


def extract_key():
    """Pull an API key from Authorization: Bearer, X-API-Key, or ?key= (last resort)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    xkey = request.headers.get("X-API-Key")
    if xkey:
        return xkey.strip()
    return request.args.get("key")  # for constrained devices / WebSocket handshakes


def verify(key: str):
    if not key or not key.startswith(KEY_PREFIX):
        return None
    row = db.get_device_by_hash(_hash(key))
    if row:
        db.touch_device(row["id"], time.time())
    return row


def require_device(view):
    """Decorator: reject unless a valid device API key is presented."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        device = verify(extract_key())
        if not device:
            return jsonify(error="invalid or missing device API key"), 401
        g.device = device
        return view(*args, **kwargs)
    return wrapped
