"""Shared speech-to-text engine, used by the browser captions feature and the
IoT device microservice. Backed by Whisper via faster-whisper (CTranslate2):
fully offline once the model is on disk, GPU-capable (CUDA auto-detected, else
CPU int8; override with WHISPER_DEVICE / WHISPER_COMPUTE).

Whisper transcribes whole utterances rather than word-by-word, so streaming is
emulated: audio is buffered, an utterance is finalized when the speaker pauses
(trailing silence) or the buffer hits WHISPER_MAX_SEGMENT_SEC, and — when
enabled — the in-progress buffer is re-transcribed every WHISPER_PARTIAL_SEC to
emit live "partial" captions.

Recognizer interface: feed(pcm16_mono_bytes) -> list[event],
finish() -> list[event], where event = {"type": "partial"|"final", "text": str}.
The model loads once as a process-wide singleton (it can be large).
"""
import io
import os
import threading
import wave
from pathlib import Path

import config

SAMPLE_RATE = 16000
_BYTES_PER_SEC = SAMPLE_RATE * 2  # PCM16 mono

# --- Model singleton -------------------------------------------------------
_whisper = None
_whisper_desc = None
_device = None
_lock = threading.Lock()


def local_model_dir(name=None):
    """Where `demo_setup.py --download-model` stores a model for offline use."""
    return Path(config.BASE_DIR) / "models" / f"faster-whisper-{name or config.WHISPER_MODEL}"


def resolve_model():
    """WHISPER_MODEL may be a size/HF id ("small", "large-v3") or a local path.
    A pre-downloaded copy in models/faster-whisper-<size> is preferred so the
    appliance never needs the network at runtime."""
    m = config.WHISPER_MODEL
    if os.path.isdir(m):
        return m
    local = local_model_dir(m)
    if local.is_dir():
        return str(local)
    return m  # faster-whisper downloads it to the HF cache on first use


def _device_compute():
    dev = config.WHISPER_DEVICE
    if dev == "auto":
        try:
            import ctranslate2
            dev = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            dev = "cpu"
    comp = config.WHISPER_COMPUTE or ("float16" if dev == "cuda" else "int8")
    return dev, comp


def get_model():
    global _whisper, _whisper_desc, _device
    if _whisper is None:
        with _lock:
            if _whisper is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as e:
                    raise RuntimeError("faster-whisper not installed — `pip install faster-whisper`") from e
                model = resolve_model()
                dev, comp = _device_compute()
                try:
                    _whisper = WhisperModel(model, device=dev, compute_type=comp)
                except Exception:
                    if dev == "cpu":
                        raise
                    # GPU libs missing / unsupported -> CPU fallback
                    dev, comp = "cpu", "int8"
                    _whisper = WhisperModel(model, device=dev, compute_type=comp)
                _device = dev
                _whisper_desc = f"whisper:{model} on {dev}/{comp}"
    return _whisper


def _language():
    lang = (config.WHISPER_LANGUAGE or "").strip().lower()
    return None if lang in ("", "auto") else lang


def _partial_interval():
    """Seconds of new audio between partial captions; 0 disables partials.
    "auto" enables them only on GPU, where re-transcribing is cheap."""
    v = config.WHISPER_PARTIAL_SEC
    if v == "auto":
        get_model()
        return 1.0 if _device == "cuda" else 0.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 0.0


# --- Audio helpers ---------------------------------------------------------
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


def _rms(pcm_bytes):
    import numpy as np
    if len(pcm_bytes) < 2:
        return 0.0
    a = _pcm16_to_float32(pcm_bytes[: len(pcm_bytes) // 2 * 2])
    return float(np.sqrt(np.mean(a * a)))


def _transcribe(f32_16k, vad=True):
    model = get_model()
    segments, _ = model.transcribe(
        f32_16k,
        language=_language(),
        beam_size=config.WHISPER_BEAM_SIZE,
        vad_filter=vad,
        condition_on_previous_text=False,
        initial_prompt=_glossary(),
    )
    return " ".join(s.text.strip() for s in segments).strip()


def _glossary():
    try:
        import vocab
        return vocab.prompt() or None
    except Exception:
        return None


# --- Engine lifecycle ------------------------------------------------------
def ensure_ready():
    """Load the model (raises if unavailable). Used to gate features."""
    get_model()


def engine_desc():
    return _whisper_desc or f"whisper:{config.WHISPER_MODEL}"


# --- Streaming recognizer --------------------------------------------------
class WhisperRecognizer:
    """Utterance-segmenting streaming wrapper around Whisper.

    Simple energy endpointing: frames with RMS below WHISPER_SILENCE_RMS count
    as silence. Once speech has been heard, WHISPER_SILENCE_SEC of trailing
    silence (or WHISPER_MAX_SEGMENT_SEC of audio) closes the utterance and emits
    a final. Leading silence is trimmed so idle mics cost nothing.
    """
    _PREROLL = int(0.3 * _BYTES_PER_SEC)  # keep a little audio before speech onset

    def __init__(self):
        self._buf = bytearray()
        self._speech = False
        self._silence = 0       # bytes of trailing silence
        self._since_partial = 0  # bytes since the last partial
        self._last_partial = ""
        self._max = int(config.WHISPER_MAX_SEGMENT_SEC * _BYTES_PER_SEC)
        self._end_silence = int(config.WHISPER_SILENCE_SEC * _BYTES_PER_SEC)
        self._partial_every = int(_partial_interval() * _BYTES_PER_SEC)

    def _reset(self):
        self._buf = bytearray()
        self._speech = False
        self._silence = 0
        self._since_partial = 0
        self._last_partial = ""

    def _final(self):
        pcm = bytes(self._buf)
        text = _transcribe(_pcm16_to_float32(pcm))
        had_partial = bool(self._last_partial)
        self._reset()
        if text:  # "pcm": the utterance audio, for live speaker labels (not broadcast)
            return [{"type": "final", "text": text, "pcm": pcm}]
        # clear a stale partial line if the utterance turned out empty
        return [{"type": "partial", "text": ""}] if had_partial else []

    def feed(self, pcm):
        if not pcm:
            return []
        self._buf.extend(pcm)
        if _rms(pcm) >= config.WHISPER_SILENCE_RMS:
            self._speech = True
            self._silence = 0
        else:
            self._silence += len(pcm)

        if not self._speech:
            if len(self._buf) > self._PREROLL:  # idle: drop old silence
                del self._buf[: len(self._buf) - self._PREROLL]
            return []

        if self._silence >= self._end_silence or len(self._buf) >= self._max:
            return self._final()

        if self._partial_every:
            self._since_partial += len(pcm)
            if self._since_partial >= self._partial_every:
                self._since_partial = 0
                text = _transcribe(_pcm16_to_float32(bytes(self._buf)), vad=False)
                if text and text != self._last_partial:
                    self._last_partial = text
                    return [{"type": "partial", "text": text}]
        return []

    def finish(self):
        if not self._speech or len(self._buf) < _BYTES_PER_SEC // 2:  # < 0.5 s: ignore
            self._reset()
            return []
        return [e for e in self._final() if e["text"]]


def create_recognizer():
    return WhisperRecognizer()


# --- Batch -----------------------------------------------------------------
def transcribe_pcm16(pcm_bytes, sample_rate=SAMPLE_RATE):
    f = _pcm16_to_float32(pcm_bytes[: len(pcm_bytes) // 2 * 2])
    if sample_rate != SAMPLE_RATE:
        f = _resample_f32(f, sample_rate)
    return _transcribe(f)


def transcribe_wav(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("WAV must be mono 16-bit PCM")
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return transcribe_pcm16(frames, rate)


# --- Recordings (uploaded files) -------------------------------------------
def decode_file(path):
    """Decode any audio/video file (wav, mp3, m4a, ogg, webm, mp4…) to 16 kHz mono
    float32. Uses PyAV, which ships with faster-whisper — no ffmpeg install needed.
    (Not faster_whisper.audio.decode_audio: it passes an av.open() argument that
    newer PyAV releases removed.)"""
    import av
    import numpy as np
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks = []
    with av.open(path, mode="r") as container:
        stream = container.streams.audio[0]
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe_segments(f32_16k, word_timestamps=False):
    """Yield faster-whisper segments (with .start/.end and optionally .words)."""
    segments, _ = get_model().transcribe(
        f32_16k,
        language=_language(),
        beam_size=config.WHISPER_BEAM_SIZE,
        vad_filter=True,
        word_timestamps=word_timestamps,
        initial_prompt=_glossary(),
    )
    yield from segments
