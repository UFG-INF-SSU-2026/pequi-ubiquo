"""Standalone IoT speech-to-text microservice.

Runs ONLY the device-facing STT ingestion API (+ provisioning UI), so it can be
deployed as its own service on its own port — or even a separate machine — while
sharing the same SQLite DB and Vosk model resolution as the main app.

Usage:
    python stt_service.py                       # run the service (default :5100)
    python stt_service.py --port 5100
    python stt_service.py --create-device NAME [--room ROOM]   # mint an API key (headless)

The same endpoints also auto-load into the main app (features/iot.py); this
entrypoint is for running transcription as an independent microservice.
"""
import argparse
import os

from flask import Flask

import config
import db
import devices
from auth import bp as auth_bp
from features import iot


def create_service():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = config.SECRET_KEY
    app.config["SESSION_PERMANENT"] = True
    db.init_db()
    app.register_blueprint(auth_bp)   # lets a human log in to provision devices
    iot.register(app)                 # device API (WS + HTTP) + provisioning UI
    return app


def main():
    parser = argparse.ArgumentParser(description="TowerAI IoT STT microservice")
    parser.add_argument("--create-device", metavar="NAME", help="mint an API key and exit")
    parser.add_argument("--room", help="default room for --create-device")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("STT_SERVICE_PORT", "5100")))
    args = parser.parse_args()

    db.init_db()

    if args.create_device:
        _, key = devices.create_device(args.create_device, args.room)
        print(f"Device '{args.create_device}' created. API key (shown once):")
        print(key)
        return

    app = create_service()
    print(f"TowerAI STT microservice on http://{config.HOST}:{args.port}")
    print(f"  batch : POST http://{config.HOST}:{args.port}/api/stt/transcribe")
    print(f"  stream: WS   ws://{config.HOST}:{args.port}/api/stt/stream?key=...")
    app.run(host=config.HOST, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
