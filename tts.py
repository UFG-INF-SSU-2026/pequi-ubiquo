"""Offline text-to-speech with Piper (ONNX, CPU, fast): every device hears the
same natural voice instead of whatever its browser provides.

Voices are .onnx files (+ .onnx.json) in models/piper/; TTS_VOICE picks one by
path or name, else a Brazilian Portuguese voice is preferred, else any voice.
`python demo_setup.py --download-model` fetches pt_BR "faber" (medium). Without
piper-tts or a voice, available() is False and browsers fall back to their
built-in speech synthesis.
"""
import importlib.util
import io
import re
import threading
import wave
from pathlib import Path

import config

VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
             "pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx")
_voice = None
_voice_path = None
_lock = threading.Lock()


def voices_dir():
    return Path(config.BASE_DIR) / "models" / "piper"


def voice_path():
    if config.TTS_VOICE and Path(config.TTS_VOICE).is_file():
        return Path(config.TTS_VOICE)
    found = sorted(voices_dir().glob("*.onnx")) if voices_dir().is_dir() else []
    if config.TTS_VOICE:
        found = [p for p in found if config.TTS_VOICE in p.name] or found
    pt = [p for p in found if p.name.lower().startswith("pt")]
    return (pt or found or [None])[0]


def available():
    return (config.TTS_ENABLED and importlib.util.find_spec("piper") is not None
            and voice_path() is not None)


def _get():
    global _voice, _voice_path
    path = voice_path()
    if _voice is None or _voice_path != path:
        from piper import PiperVoice
        _voice, _voice_path = PiperVoice.load(str(path)), path
    return _voice


def speakable(text):
    """Drop what shouldn't be read aloud: approval markers, tool footers, files, emoji."""
    lines = [ln for ln in (text or "").splitlines()
             if not ln.startswith(("[confirm:", "🔧")) and not ln.strip().startswith("[image:")]
    text = re.sub(r"\[file:\d+:([^\]]+)\]", r"\1", "\n".join(lines))
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", "", text)
    return text.strip()[:config.TTS_MAX_CHARS]


def synthesize(text):
    """WAV bytes for `text` (raises if TTS isn't available)."""
    text = speakable(text)
    if not text:
        raise ValueError("nothing to say")
    buf = io.BytesIO()
    with _lock:
        voice = _get()
        with wave.open(buf, "wb") as wf:
            if hasattr(voice, "synthesize_wav"):   # piper-tts >= 1.3
                voice.synthesize_wav(text, wf)
            else:                                  # piper-tts 1.2
                voice.synthesize(text, wf)
    return buf.getvalue()


def download_voice():
    import urllib.request
    dest = voices_dir()
    dest.mkdir(parents=True, exist_ok=True)
    name = VOICE_URL.rsplit("/", 1)[1]
    for suffix in ("", ".json"):
        target = dest / (name + suffix)
        if not target.is_file():
            urllib.request.urlretrieve(VOICE_URL + suffix, target)
    return dest / name
