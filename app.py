"""TowerAI MVP — offline collaboration appliance.

App factory + optional-feature loader. Vision, voice, and transcription are
NOT in the core; they load automatically if present in the features/ package.
See features/README.md for the (tiny) contract.
"""
import importlib

from flask import Flask

import config
import db
from auth import bp as auth_bp, current_user
from chat import bp as chat_bp


def register_features(app: Flask):
    """Auto-load optional features (vision/voice/transcription) if installed.

    Each feature module must expose `register(app)`. Missing or broken features
    never take the core down.
    """
    loaded = set()
    for name in config.OPTIONAL_FEATURES:
        try:
            mod = importlib.import_module(f"features.{name}")
        except ModuleNotFoundError:
            app.logger.info(f"[feature] '{name}' not installed (optional)")
            continue
        try:
            mod.register(app)
            loaded.add(name)
            app.logger.info(f"[feature] '{name}' loaded")
        except Exception as e:
            app.logger.warning(f"[feature] '{name}' failed to load: {e}")
    app.config["LOADED_FEATURES"] = loaded


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config["SESSION_PERMANENT"] = True

    db.init_db()

    # Cross-worker broadcast fan-out (Redis if REDIS_URL set, else in-process).
    import pubsub
    pubsub.start()

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)

    # Realtime WebSocket layer (instant messaging). Non-fatal if flask-sock
    # isn't installed — the chat falls back to REST polling automatically.
    try:
        import realtime
        realtime.register(app)
        app.logger.info("[realtime] WebSocket messaging enabled")
    except Exception as e:
        app.logger.warning(f"[realtime] WebSocket disabled, using polling fallback: {e}")

    register_features(app)

    @app.context_processor
    def inject_user():
        return {
            "current_user": current_user(),
            "assistant_name": config.ASSISTANT_NAME,
            "loaded_features": app.config.get("LOADED_FEATURES", set()),
        }

    return app


app = create_app()


if __name__ == "__main__":
    # threaded=True is required for the WebSocket layer (flask-sock) to serve
    # concurrent connections. See serve.py for the production note.
    import discovery
    discovery.start_mdns(config.PORT)
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
