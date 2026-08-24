"""Server entrypoint.

WebSockets: the chat/captions use flask-sock, and waitress can't upgrade
WebSocket connections — so this uses Werkzeug's threaded server, adequate for a
small offline LAN (tens of concurrent users).

TLS (for remote mic access — browsers block getUserMedia over plain HTTP off
localhost): generate a stable self-signed cert once with `python serve.py
--gen-cert`, or set USE_TLS=1 for an ephemeral one. Clients accept the one-time
warning; listeners (no mic) can use plain HTTP.

Scaling on Linux: run under gunicorn with gevent workers (WSGI + WebSocket), and
set REDIS_URL so broadcasts fan out across workers (see pubsub.py):
    gunicorn -k gevent -w 4 -b 0.0.0.0:5000 app:app
"""
import argparse
import os

import config
import discovery
from app import app


def _ssl_context():
    if os.path.exists(config.TLS_CERT) and os.path.exists(config.TLS_KEY):
        return (config.TLS_CERT, config.TLS_KEY)  # werkzeug accepts a (cert, key) tuple
    if config.USE_TLS:
        return "adhoc"  # ephemeral self-signed (regenerated each start)
    return None


def main():
    ap = argparse.ArgumentParser(description="TowerAI server")
    ap.add_argument("--gen-cert", action="store_true", help="write a stable self-signed cert and exit")
    args = ap.parse_args()

    if args.gen_cert:
        from tls_cert import generate_self_signed
        cert, key = generate_self_signed(config.TLS_CERT, config.TLS_KEY, config.MDNS_HOSTNAME)
        print(f"Wrote {cert} and {key}. Start with USE_TLS unset (they're auto-used).")
        return

    discovery.start_mdns(config.PORT)
    ctx = _ssl_context()
    scheme = "https" if ctx else "http"
    print(f"TowerAI serving on {scheme}://{config.HOST}:{config.PORT}  "
          f"(try {scheme}://{config.MDNS_HOSTNAME}.local:{config.PORT})")
    app.run(host=config.HOST, port=config.PORT, debug=False,
            threaded=True, ssl_context=ctx)


if __name__ == "__main__":
    main()
