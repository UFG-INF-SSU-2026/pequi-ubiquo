"""Central configuration for TowerAI MVP.

Everything is environment-driven with safe local defaults. The LLM endpoint is
localhost-first by design (no LAN scanning, no magic IPs) — see llm.py.
"""
import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent

# --- Storage ---------------------------------------------------------------
DB_PATH = os.environ.get("TOWERAI_DB", str(BASE_DIR / "towerai.db"))

# --- Local LLM (LM Studio / Ollama, OpenAI-compatible) ---------------------
# Localhost-first. Point this at wherever your local model server runs.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:1234")
# "auto" => don't send a model id, let the server use whatever is loaded.
LLM_MODEL = os.environ.get("LLM_MODEL", "auto")
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Tower")

# --- Network ---------------------------------------------------------------
# 0.0.0.0 is intentional: this is a LAN appliance meant to serve connected
# devices. Every route is behind a login, and there are NO shell/host-control
# endpoints, so binding to the LAN is the purpose, not a leak.
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
# Advertised over mDNS as "<MDNS_HOSTNAME>.local" so devices can find it.
MDNS_HOSTNAME = os.environ.get("MDNS_HOSTNAME", "towerai")

# Browsers only allow microphone capture over HTTPS or on localhost. Speakers
# using Live Captions from another device therefore need TLS. Set USE_TLS=1 to
# serve with an ad-hoc self-signed cert (requires `pip install cryptography`);
# clients accept the one-time browser warning. Listeners don't need a mic, so
# plain HTTP is fine for them.
USE_TLS = os.environ.get("USE_TLS", "0").lower() in ("1", "true", "yes")
# Stable self-signed cert (generate with `python serve.py --gen-cert`). If both
# files exist they're used; otherwise USE_TLS=1 falls back to an ephemeral cert.
TLS_CERT = os.environ.get("TLS_CERT", str(BASE_DIR / "certs" / "cert.pem"))
TLS_KEY = os.environ.get("TLS_KEY", str(BASE_DIR / "certs" / "key.pem"))

# --- Auth / sessions -------------------------------------------------------
_SECRET_FILE = BASE_DIR / ".secret_key"


def _load_secret() -> str:
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    try:
        _SECRET_FILE.write_text(key, encoding="utf-8")
    except OSError:
        pass  # ephemeral key; sessions reset on restart
    return key


SECRET_KEY = _load_secret()
MIN_PASSWORD_LEN = int(os.environ.get("MIN_PASSWORD_LEN", "4"))

# --- Speech-to-text engine -------------------------------------------------
# "vosk" (CPU, true streaming) or "whisper" (faster-whisper, GPU-capable, higher
# accuracy, windowed). Whisper device auto-detects CUDA, else CPU.
STT_ENGINE = os.environ.get("STT_ENGINE", "vosk").lower()
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")     # tiny|base|small|medium|large-v3
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")    # auto|cuda|cpu
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "")      # ""=auto (float16 gpu / int8 cpu)
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "pt")
WHISPER_WINDOW_SEC = float(os.environ.get("WHISPER_WINDOW_SEC", "4"))

# --- Scaling (multi-worker) ------------------------------------------------
# Set REDIS_URL (e.g. redis://127.0.0.1:6379/0) to fan out WebSocket broadcasts
# across gunicorn workers. Unset => single-process in-memory broadcast.
REDIS_URL = os.environ.get("REDIS_URL", "")

# Optional features to attempt to load from the features/ package.
OPTIONAL_FEATURES = ("vision", "voice", "transcription", "iot", "meetings", "netmap")
