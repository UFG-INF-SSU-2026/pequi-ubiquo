"""Offline speaker diarization ("who spoke when") for uploaded recordings.

Uses sherpa-onnx (ONNX Runtime, CPU, no PyTorch): a pyannote segmentation model
finds speech turns and a WeSpeaker embedding model clusters them into speakers.
Both models are small (~33 MB together) and live in models/diarization/; fetch
them once with `python demo_setup.py --download-model`.

diarize(f32_16k, num_speakers=None) -> [(start_sec, end_sec, speaker_index)].
Knowing the number of speakers improves accuracy a lot; without it, speakers
are clustered by DIARIZATION_THRESHOLD (lower = more speakers).

Optional: if sherpa-onnx or the models are missing, available() is False and
uploads are transcribed without speaker labels.
"""
import importlib.util
import os
import tarfile
import threading
import urllib.request
from pathlib import Path

import config

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEGMENTATION_URL = f"{_RELEASES}/speaker-segmentation-models/{SEGMENTATION_DIR}.tar.bz2"
EMBEDDING_FILE = "wespeaker_en_voxceleb_resnet34_LM.onnx"
EMBEDDING_URL = f"{_RELEASES}/speaker-recongition-models/{EMBEDDING_FILE}"  # sic, upstream tag

_cache = {}  # num_speakers -> OfflineSpeakerDiarization
_lock = threading.Lock()


def models_dir():
    return Path(config.BASE_DIR) / "models" / "diarization"


def model_paths():
    seg = config.DIARIZATION_SEGMENTATION_MODEL or str(models_dir() / SEGMENTATION_DIR / "model.onnx")
    emb = config.DIARIZATION_EMBEDDING_MODEL or str(models_dir() / EMBEDDING_FILE)
    return seg, emb


def available():
    if not config.DIARIZATION_ENABLED:
        return False
    if importlib.util.find_spec("sherpa_onnx") is None:
        return False
    return all(os.path.isfile(p) for p in model_paths())


def _get(num_speakers):
    import sherpa_onnx as so
    key = num_speakers or -1
    sd = _cache.get(key)
    if sd is None:
        seg, emb = model_paths()
        threads = max(1, (os.cpu_count() or 2) // 2)
        cfg = so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg),
                num_threads=threads,
            ),
            embedding=so.SpeakerEmbeddingExtractorConfig(model=emb, num_threads=threads),
            clustering=so.FastClusteringConfig(
                num_clusters=key, threshold=config.DIARIZATION_THRESHOLD),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not cfg.validate():
            raise RuntimeError(f"invalid diarization models: {seg}, {emb}")
        sd = so.OfflineSpeakerDiarization(cfg)
        _cache[key] = sd
    return sd


def diarize(f32_16k, num_speakers=None, progress=None):
    """Speaker turns sorted by start time. progress(fraction) is called as it runs."""
    def cb(done, total):
        if progress and total:
            progress(done / total)
        return 0  # 0 = keep going

    with _lock:  # one diarization at a time; it already uses several threads
        sd = _get(num_speakers if num_speakers and num_speakers > 0 else None)
        result = sd.process(f32_16k, callback=cb).sort_by_start_time()
    return [(r.start, r.end, r.speaker) for r in result]


def download_models():
    """Fetch both models into models/diarization/ (used by demo_setup.py)."""
    dest = models_dir()
    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / SEGMENTATION_DIR / "model.onnx").is_file():
        archive = dest / f"{SEGMENTATION_DIR}.tar.bz2"
        urllib.request.urlretrieve(SEGMENTATION_URL, archive)
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(dest)
        archive.unlink()
    if not (dest / EMBEDDING_FILE).is_file():
        tmp = dest / (EMBEDDING_FILE + ".part")
        urllib.request.urlretrieve(EMBEDDING_URL, tmp)
        tmp.rename(dest / EMBEDDING_FILE)
    return dest
