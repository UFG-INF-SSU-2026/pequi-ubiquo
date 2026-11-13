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
# Failed logins allowed per username / per client address within the window.
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_WINDOW_SEC = int(os.environ.get("LOGIN_WINDOW_SEC", "300"))

# --- Speech-to-text (Whisper via faster-whisper) --------------------------
# Offline once the model is on disk (`python demo_setup.py --download-model`
# stores it in models/faster-whisper-<size>). Device auto-detects CUDA, else CPU.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")     # tiny|base|small|medium|large-v3|<path>
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto")    # auto|cuda|cpu
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "")      # ""=auto (float16 gpu / int8 cpu)
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "pt")  # ""/"auto" = detect
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "1"))
# Streaming segmentation: an utterance ends after this much trailing silence,
# or when it reaches the max length. RMS threshold is on a 0..1 float scale.
WHISPER_SILENCE_SEC = float(os.environ.get("WHISPER_SILENCE_SEC", "0.7"))
WHISPER_SILENCE_RMS = float(os.environ.get("WHISPER_SILENCE_RMS", "0.01"))
WHISPER_MAX_SEGMENT_SEC = float(os.environ.get("WHISPER_MAX_SEGMENT_SEC", "10"))
# Live partial captions: re-transcribe the in-progress utterance every N seconds.
# "auto" = on (1 s) with a GPU, off on CPU; "0" disables.
WHISPER_PARTIAL_SEC = os.environ.get("WHISPER_PARTIAL_SEC", "auto").lower()

# --- Meetings: uploads, speaker labels, summaries -------------------------
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
RECORDINGS_DIR = os.environ.get("RECORDINGS_DIR", str(BASE_DIR / "recordings"))
# Uploaded audio is deleted once transcribed unless this is set.
KEEP_RECORDINGS = os.environ.get("KEEP_RECORDINGS", "0").lower() in ("1", "true", "yes")
UPLOAD_DEFAULT_SPEAKER = os.environ.get("UPLOAD_DEFAULT_SPEAKER", "Speaker")
# Speaker diarization (sherpa-onnx). Models default to models/diarization/.
DIARIZATION_ENABLED = os.environ.get("DIARIZATION_ENABLED", "1").lower() in ("1", "true", "yes")
DIARIZATION_THRESHOLD = float(os.environ.get("DIARIZATION_THRESHOLD", "0.5"))  # lower => more speakers
DIARIZATION_SEGMENTATION_MODEL = os.environ.get("DIARIZATION_SEGMENTATION_MODEL", "")
DIARIZATION_EMBEDDING_MODEL = os.environ.get("DIARIZATION_EMBEDDING_MODEL", "")
# Remembered voices: cosine similarity needed to call a speaker by a saved name.
VOICES_ENABLED = os.environ.get("VOICES_ENABLED", "1").lower() in ("1", "true", "yes")
# Defaults suit the WeSpeaker model: same-voice scores sit around 0.8–0.9 and
# different voices can come close, hence the margin over the runner-up.
VOICE_MATCH_THRESHOLD = float(os.environ.get("VOICE_MATCH_THRESHOLD", "0.80"))
VOICE_MATCH_MARGIN = float(os.environ.get("VOICE_MATCH_MARGIN", "0.05"))
VOICE_MIN_SECONDS = float(os.environ.get("VOICE_MIN_SECONDS", "6"))  # pooled speech per speaker
# Learned vocabulary passed to Whisper as a glossary prompt.
VOCAB_ENABLED = os.environ.get("VOCAB_ENABLED", "1").lower() in ("1", "true", "yes")
VOCAB_MAX_CHARS = int(os.environ.get("VOCAB_MAX_CHARS", "600"))
# Offline text-to-speech (Piper) and hands-free voice.
TTS_ENABLED = os.environ.get("TTS_ENABLED", "1").lower() in ("1", "true", "yes")
TTS_VOICE = os.environ.get("TTS_VOICE", "")          # path or name in models/piper/
TTS_MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1500"))
VOICE_WAKE_WORDS = [w.strip().lower() for w in os.environ.get(
    "VOICE_WAKE_WORDS", "oi torre,ok torre,ei torre,torre,hey tower,ok tower,tower").split(",") if w.strip()]
# LLM summaries: transcripts longer than this are summarized in chunks first.
SUMMARY_CHUNK_CHARS = int(os.environ.get("SUMMARY_CHUNK_CHARS", "12000"))
SUMMARY_TIMEOUT_SEC = int(os.environ.get("SUMMARY_TIMEOUT_SEC", "300"))

# --- Shared files per room -------------------------------------------------
FILES_DIR = os.environ.get("FILES_DIR", str(BASE_DIR / "uploads" / "files"))
FILES_MAX_MB = int(os.environ.get("FILES_MAX_MB", "50"))

# --- Vision (images in chat) ---------------------------------------------
# Uses the same local LLM server; load a vision-capable model there, or name one.
VISION_MODEL = os.environ.get("VISION_MODEL", "")            # ""=use LLM_MODEL
VISION_MAX_MB = int(os.environ.get("VISION_MAX_MB", "10"))
VISION_TIMEOUT_SEC = int(os.environ.get("VISION_TIMEOUT_SEC", "180"))
IMAGES_DIR = os.environ.get("IMAGES_DIR", str(BASE_DIR / "uploads" / "images"))

# --- Agent (tool use by the local LLM) -------------------------------------
# The chat assistant runs as an agent with tools over TowerAI's data (agent.py).
AGENT_ENABLED = os.environ.get("AGENT_ENABLED", "1").lower() in ("1", "true", "yes")
AGENT_PROTOCOL = os.environ.get("AGENT_PROTOCOL", "native").lower()  # native | text
AGENT_ALLOW_WRITES = os.environ.get("AGENT_ALLOW_WRITES", "1").lower() in ("1", "true", "yes")
AGENT_MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "6"))
AGENT_MAX_RESULT_CHARS = int(os.environ.get("AGENT_MAX_RESULT_CHARS", "6000"))
AGENT_TIMEOUT_SEC = int(os.environ.get("AGENT_TIMEOUT_SEC", "180"))
AGENT_MAX_CONTEXT_CHARS = int(os.environ.get("AGENT_MAX_CONTEXT_CHARS", "24000"))
AGENT_MEMORY_IN_PROMPT = int(os.environ.get("AGENT_MEMORY_IN_PROMPT", "8"))  # room notes shown to the model
# Actions like posting, starting/stopping recordings wait for approval in chat.
AGENT_CONFIRM_ACTIONS = os.environ.get("AGENT_CONFIRM_ACTIONS", "1").lower() in ("1", "true", "yes")
AGENT_STREAM = os.environ.get("AGENT_STREAM", "1").lower() in ("1", "true", "yes")  # stream replies live
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1").lower() in ("1", "true", "yes")
AGENT_FEW_SHOT = int(os.environ.get("AGENT_FEW_SHOT", "2"))  # well-rated past answers shown as examples
AGENT_SHOW_TOOLS = os.environ.get("AGENT_SHOW_TOOLS", "1").lower() in ("1", "true", "yes")

# --- Backups (backup.py) ---------------------------------------------------
BACKUP_DIR = os.environ.get("BACKUP_DIR", "")            # e.g. a USB drive; "" = no automatic backups
BACKUP_EVERY_HOURS = float(os.environ.get("BACKUP_EVERY_HOURS", "24"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))

# --- Scaling (multi-worker) ------------------------------------------------
# Set REDIS_URL (e.g. redis://127.0.0.1:6379/0) to fan out WebSocket broadcasts
# across gunicorn workers. Unset => single-process in-memory broadcast.
REDIS_URL = os.environ.get("REDIS_URL", "")

# Optional features to attempt to load from the features/ package.
OPTIONAL_FEATURES = ("vision", "voice", "transcription", "iot", "meetings", "netmap")
