"""Cross-worker broadcast fan-out for WebSocket delivery.

Single process (default): `publish` calls the local delivery handler directly —
identical to the old in-memory behavior.

Multi-worker (set REDIS_URL, run under gunicorn -k gevent -w N): `publish` sends
to Redis pub/sub, and every worker's subscriber delivers to ITS OWN local WS
clients. This is what lets one shared LAN scale past a single process.

Side-effect hooks (e.g. persisting a meeting transcript) must NOT go through
here — they run once at the origin (see captions.broadcast), or they'd fire once
per worker.
"""
import json
import threading

import config

_handlers = {}        # channel -> callable(data: dict)  (local delivery)
_redis = None
_started = False
_lock = threading.Lock()


def register_channel(channel, handler):
    _handlers[channel] = handler


def start():
    """Connect Redis (if configured) and start the subscriber. Idempotent."""
    global _redis, _started
    with _lock:
        if _started:
            return _redis is not None
        _started = True
        if not config.REDIS_URL:
            return False
        try:
            import redis
            r = redis.Redis.from_url(config.REDIS_URL)
            r.ping()
            _redis = r
        except Exception as e:
            print(f"[pubsub] Redis unavailable ({e}); using in-process broadcast.")
            _redis = None
            return False

    def _run():
        ps = _redis.pubsub(ignore_subscribe_messages=True)
        ps.psubscribe("towerai:*")
        for msg in ps.listen():
            try:
                channel = msg["channel"].decode().split(":", 1)[1]
                data = json.loads(msg["data"])
                h = _handlers.get(channel)
                if h:
                    h(data)
            except Exception:
                continue

    threading.Thread(target=_run, daemon=True).start()
    print("[pubsub] Redis fan-out enabled.")
    return True


def publish(channel, data):
    """Deliver `data` to local clients (and other workers if Redis is enabled)."""
    if _redis is not None:
        try:
            _redis.publish(f"towerai:{channel}", json.dumps(data))
            return  # subscriber (this worker + others) performs local delivery
        except Exception:
            pass
    h = _handlers.get(channel)
    if h:
        h(data)
