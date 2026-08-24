"""Shared speech-to-text engine, used by the browser captions feature and the
IoT device microservice. Two interchangeable backends (STT_ENGINE):

  - "vosk"   : lightweight, CPU, true streaming (partial words as you speak).
  - "whisper": faster-whisper (CTranslate2), higher accuracy, GPU-capable.
               Windowed, not word-streaming — it emits a final per ~N seconds,
               so it trades a little latency for accuracy. Device is auto-detected
               (CUDA if present, else CPU int8); override with WHISPER_DEVICE.

Recognizer interface (both backends): feed(pcm16_mono_bytes) -> list[event],
finish() -> list[event], where event = {"type": "partial"|"final", "text": str}.
Models load once as process-wide singletons (they can be large).
"""
import io
import json
import os
import threading
import wave

import config

SAMPLE_RATE = 16000

# --- Vosk singleton --------------------------------------------------------
_vosk = None
_vosk_path = None
_lock = threading.Lock()


def resolve_model_path():
    env = os.environ.get("VOSK_MODEL_PATH")
    if env:
        return env
    from pathlib import Path
    models_dir = Path(config.BASE_DIR) / "models"
    if models_dir.is_dir():
        cands = sorted(p for p in models_dir.iterdir()
                       if p.is_dir() and "vosk-model" in p.name.lower())
        pt = [p for p in cands if "pt" in p.name.lower()]
        if pt:
            return str(pt[0])
        if cands:
            return str(cands[0])
    return str(models_dir / "vosk-model-pt-fb-v0.1.1-20220516_2113")


def get_model():
    global _vosk, _vosk_path
    if _vosk is None:
        with _lock:
            if _vosk is None:
                try:
                    from vosk import Model, SetLogLevel
                except ImportError as e:
                    raise RuntimeError("vosk not installed — `pip install vosk`") from e
                _vosk_path = resolve_model_path()
                if not os.path.isdir(_vosk_path):
                    raise RuntimeError(f"Vosk model not found at {_vosk_path}")
                SetLogLevel(-1)
                _vosk = Model(_vosk_path)
    return _vosk


def model_path():
    return _vosk_path


# --- Whisper singleton -----------------------------------------------------
_whisper = None
_whisper_desc = None


def _whisper_device_compute():
    dev = config.WHISPER_DEVICE
    if dev == "auto":
        try:
            import ctranslate2
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            dev = "cpu"
    comp = config.WHISPER_COMPUTE or ("float16" if dev == "cuda" else "int8")
    return dev, comp


def get_whisper():
    global _whisper, _whisper_desc
    if _whisper is None:
        with _lock:
            if _whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as e:
                    raise RuntimeError("faster-whisper not installed — `pip install faster-whisper`") from e
                dev, comp = _whisper_device_compute()
                try:
                    _whisper = WhisperModel(config.WHISPER_MODEL, device=dev, compute_type=comp)
                    _whisper_desc = f"whisper:{config.WHISPER_MODEL} on {dev}/{comp}"
                except Exception:
                    # GPU libs missing / unsupported -> CPU fallback
                    _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
                    _whisper_desc = f"whisper:{config.WHISPER_MODEL} on cpu/int8 (gpu fallback)"
    return _whisper


def _pcm16_to_float32(pcm_bytes):
    import numpy as np
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def _resample_f32(arr, in_rate, out_rate=SAMPLE_RATE):
    if in_rate == out_rate or arr.size == 0:
        return arr
    import numpy as np
    n_out = int(round(arr.size * out_rate / in_rate))
    x_old = np.linspace(0, 1, arr.size, endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    return np.interp(x_new, x_old, arr).astype("float32")


def _whisper_transcribe(f32_16k):
    model = get_whisper()
    segments, _ = model.transcribe(
        f32_16k,
        language=(config.WHISPER_LANGUAGE or None),
        beam_size=1,
        vad_filter=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


# --- Engine lifecycle ------------------------------------------------------
def ensure_ready():
    """Load the active engine's model (raises if unavailable). Used to gate features."""
    if config.STT_ENGINE == "whisper":
        get_whisper()
    else:
        get_model()


def engine_desc():
    if config.STT_ENGINE == "whisper":
        return _whisper_desc or f"whisper:{config.WHISPER_MODEL}"
    return f"vosk:{model_path()}"


# --- Streaming recognizers -------------------------------------------------
class _VoskRecognizer:
    def __init__(self):
        from vosk import KaldiRecognizer
        self._rec = KaldiRecognizer(get_model(), SAMPLE_RATE)

    def feed(self, pcm):
        if self._rec.AcceptWaveform(pcm):
            return [{"type": "final", "text": json.loads(self._rec.Result()).get("text", "").strip()}]
        return [{"type": "partial", "text": json.loads(self._rec.PartialResult()).get("partial", "").strip()}]

    def finish(self):
        t = json.loads(self._rec.FinalResult()).get("text", "").strip()
        return [{"type": "final", "text": t}] if t else []


class _WhisperRecognizer:
    """Buffers audio into ~WHISPER_WINDOW_SEC windows and transcribes each."""
    def __init__(self):
        self._buf = bytearray()
        self._window = int(config.WHISPER_WINDOW_SEC * SAMPLE_RATE * 2)  # bytes of PCM16

    def feed(self, pcm):
        self._buf.extend(pcm)
        events = []
        while len(self._buf) >= self._window:
            chunk = bytes(self._buf[:self._window])
            del self._buf[:self._window]
            text = _whisper_transcribe(_pcm16_to_float32(chunk))
            if text:
                events.append({"type": "final", "text": text})
        return events

    def finish(self):
        if len(self._buf) < SAMPLE_RATE:  # < 0.5 s tail: ignore
            self._buf = bytearray()
            return []
        text = _whisper_transcribe(_pcm16_to_float32(bytes(self._buf)))
        self._buf = bytearray()
        return [{"type": "final", "text": text}] if text else []


def create_recognizer():
    return _WhisperRecognizer() if config.STT_ENGINE == "whisper" else _VoskRecognizer()


# --- Batch -----------------------------------------------------------------
def transcribe_pcm16(pcm_bytes, sample_rate=SAMPLE_RATE):
    if config.STT_ENGINE == "whisper":
        f = _pcm16_to_float32(pcm_bytes)
        if sample_rate != SAMPLE_RATE:
            f = _resample_f32(f, sample_rate)
        return _whisper_transcribe(f)
    from vosk import KaldiRecognizer
    rec = KaldiRecognizer(get_model(), sample_rate)
    rec.AcceptWaveform(pcm_bytes)
    return json.loads(rec.FinalResult()).get("text", "").strip()


def transcribe_wav(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("WAV must be mono 16-bit PCM")
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return transcribe_pcm16(frames, rate)
