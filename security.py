"""Request-level protections for a LAN appliance where browsers stay logged in.

- CSRF: every state-changing request (POST/PUT/PATCH/DELETE) from a browser must
  carry the session's token, as a `csrf_token` form field or an `X-CSRF-Token`
  header. base.html adds both automatically (hidden field on forms, header on
  fetch), so templates and scripts need no changes. Device endpoints that
  authenticate with an API key (/api/stt/…) are exempt: no cookie, no CSRF.
- WebSocket origin: browsers don't apply same-origin rules to WebSockets, so a
  page on another site could open /ws/… with the user's cookie. Upgrades whose
  Origin isn't this host are refused.
- Login throttling: after LOGIN_MAX_FAILURES failed logins for a username (or 4×
  that from one client address, since a shared PC or NAT can front many people)
  within LOGIN_WINDOW_SEC, further attempts are refused until the window passes
  (in-memory, per process).
- Safe redirects: `next=` after login only goes to a local path.
"""
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import abort, jsonify, request, session

import config

UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
EXEMPT_PREFIXES = ("/api/stt/",)  # API-key authenticated device endpoints


def csrf_token():
    tok = session.get("csrf_token")
    if not tok:
        tok = session["csrf_token"] = secrets.token_urlsafe(32)
    return tok


def _check_csrf():
    if request.method not in UNSAFE_METHODS or request.path.startswith(EXEMPT_PREFIXES):
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf_token") or ""
    if expected and hmac.compare_digest(sent, expected):
        return None
    msg = "Security token missing or expired — reload the page and try again."
    if request.accept_mimetypes.best == "application/json" or request.is_json \
            or request.headers.get("X-CSRF-Token") is not None:
        return jsonify(error=msg), 400
    abort(400, msg)


def _check_ws_origin():
    if request.headers.get("Upgrade", "").lower() != "websocket" \
            or request.path.startswith(EXEMPT_PREFIXES):
        return None
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        abort(403)
    return None


# --- Login throttling --------------------------------------------------------
_failures = defaultdict(deque)  # key -> timestamps of recent failures
_lock = threading.Lock()


def _keys(username):
    return (f"user:{username.lower()}", f"addr:{request.remote_addr}")


def _prune(q, now):
    while q and now - q[0] > config.LOGIN_WINDOW_SEC:
        q.popleft()


def login_blocked(username):
    """Seconds until another attempt is allowed (0 = allowed now)."""
    now = time.time()
    wait = 0
    with _lock:
        for k in _keys(username):
            q = _failures.get(k)
            if q is None:
                continue
            _prune(q, now)
            limit = config.LOGIN_MAX_FAILURES * (1 if k.startswith("user:") else 4)
            if len(q) >= limit:
                wait = max(wait, int(config.LOGIN_WINDOW_SEC - (now - q[0])) + 1)
    return wait


def record_login_failure(username):
    now = time.time()
    with _lock:
        for k in _keys(username):
            _failures[k].append(now)


def clear_login_failures(username):
    with _lock:
        _failures.pop(_keys(username)[0], None)


def safe_next(target, default):
    """Only allow local paths (blocks //evil.com and https://evil.com)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def init_app(app):
    @app.before_request
    def _protect():
        return _check_ws_origin() or _check_csrf()

    app.jinja_env.globals["csrf_token"] = csrf_token
