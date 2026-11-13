"""Remembered voices: speaker embeddings ("voiceprints") that let TowerAI label
known people by name — in uploaded recordings and in live captions.

How it learns: when someone renames "Speaker 2" to "Bruno" on a meeting page,
that speaker's embedding from the meeting is averaged into Bruno's voiceprint.
Later recordings compare each speaker (all of their speech pooled, at least
VOICE_MIN_SECONDS) against every voiceprint and suggest the name as "Bruno?"
when the best match clears VOICE_MATCH_THRESHOLD *and* beats the runner-up by
VOICE_MATCH_MARGIN. Renaming "Bruno?" to "Bruno" confirms it and teaches the
voiceprint more; a wrong guess is just renamed to the right person.

Accuracy caveat: on short clips (2–4 s) these embeddings separate speakers
poorly — in testing, different speakers sometimes scored higher than the same
speaker. Pooling long stretches of speech and requiring a margin is what makes
suggestions usable; tune the thresholds on your own recordings with
`python voices.py compare a.wav b.wav`.

Uses the WeSpeaker embedding model already downloaded for diarization
(diarize.py), through sherpa-onnx — no new dependencies.
"""
import threading
import time

import numpy as np

import config
import db

SAMPLE_RATE = 16000
MIN_SECONDS = 1.0     # embed() refuses anything shorter
LIVE_MIN_SECONDS = 2.5  # live utterances shorter than this keep the previous label
MAX_SAMPLES = 20      # cap on how much history weighs in the running mean

_extractor = None
_lock = threading.Lock()


def available():
    import diarize
    return config.VOICES_ENABLED and diarize.available()


def _get_extractor():
    global _extractor
    if _extractor is None:
        import os
        import sherpa_onnx as so
        import diarize
        cfg = so.SpeakerEmbeddingExtractorConfig(model=diarize.model_paths()[1],
                                                 num_threads=max(1, (os.cpu_count() or 2) // 2))
        if not cfg.validate():
            raise RuntimeError("invalid speaker embedding model")
        _extractor = so.SpeakerEmbeddingExtractor(cfg)
    return _extractor


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def embed(f32_16k):
    """L2-normalised embedding of a mono 16 kHz clip, or None if it's too short."""
    if f32_16k is None or len(f32_16k) < MIN_SECONDS * SAMPLE_RATE:
        return None
    with _lock:
        ex = _get_extractor()
        stream = ex.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.ascontiguousarray(f32_16k, dtype=np.float32))
        stream.input_finished()
        if not ex.is_ready(stream):
            return None
        return _norm(ex.compute(stream))


def to_blob(v):
    return _norm(v).astype(np.float32).tobytes()


def from_blob(b):
    return np.frombuffer(b, dtype=np.float32)


def all_voiceprints():
    with db.get_db() as c:
        return c.execute("SELECT * FROM voiceprints ORDER BY name").fetchall()


def _scores(emb, exclude=()):
    return sorted(((float(np.dot(emb, from_blob(vp["embedding"]))), vp["name"])
                   for vp in all_voiceprints() if vp["name"] not in exclude), reverse=True)


def _confident(ranked):
    """Best (score, name) if it clears the threshold and beats the runner-up by the margin."""
    if not ranked or ranked[0][0] < config.VOICE_MATCH_THRESHOLD:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < config.VOICE_MATCH_MARGIN:
        return None
    return ranked[0]


def match(emb, exclude=()):
    """(name, score) of a confidently matching voiceprint, else (None, best score)."""
    ranked = _scores(emb, exclude)
    hit = _confident(ranked)
    return (hit[1], hit[0]) if hit else (None, ranked[0][0] if ranked else -1.0)


def learn(name, emb):
    """Average `emb` into `name`'s voiceprint (creating it if new)."""
    name = name.strip().rstrip("?").strip()[:64]
    with db.get_db() as c:
        row = c.execute("SELECT * FROM voiceprints WHERE name = ?", (name,)).fetchone()
        if row:
            n = min(row["samples"], MAX_SAMPLES)
            merged = _norm(from_blob(row["embedding"]) * n + emb)
            c.execute("UPDATE voiceprints SET embedding = ?, samples = samples + 1, updated_at = ? "
                      "WHERE id = ?", (to_blob(merged), time.time(), row["id"]))
        else:
            c.execute("INSERT INTO voiceprints (name, embedding, updated_at) VALUES (?, ?, ?)",
                      (name, to_blob(emb), time.time()))


def forget(voice_id):
    with db.get_db() as c:
        return c.execute("DELETE FROM voiceprints WHERE id = ?", (voice_id,)).rowcount


def save_meeting_voice(meeting_id, label, emb):
    with db.get_db() as c:
        c.execute("INSERT INTO meeting_voices (meeting_id, label, embedding) VALUES (?, ?, ?) "
                  "ON CONFLICT(meeting_id, label) DO UPDATE SET embedding = excluded.embedding",
                  (meeting_id, label, to_blob(emb)))


def learn_from_rename(meeting_id, old, new):
    """Called when a speaker is renamed on a meeting page. True if a voice was learned."""
    with db.get_db() as c:
        row = c.execute("SELECT embedding FROM meeting_voices WHERE meeting_id = ? AND label = ?",
                        (meeting_id, old)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM meeting_voices WHERE meeting_id = ? AND label = ?", (meeting_id, new))
        c.execute("UPDATE meeting_voices SET label = ? WHERE meeting_id = ? AND label = ?",
                  (new, meeting_id, old))
    if new.lower().startswith("speaker ") or new.endswith("?"):
        return False  # don't learn placeholders or unconfirmed guesses
    learn(new, from_blob(row["embedding"]))
    return True


def label_speakers(audio, turns):
    """For diarized audio: {speaker_index: (label, embedding|None)}. A confidently
    matching known voice is suggested as "Name?" (each name used once, best match
    first); everyone else is "Speaker N"."""
    by_spk = {}
    for start, end, spk in turns:
        by_spk.setdefault(spk, []).append(audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
    embs = {}
    for spk, parts in by_spk.items():
        pooled = np.concatenate(parts)[:60 * SAMPLE_RATE]
        embs[spk] = embed(pooled) if len(pooled) >= config.VOICE_MIN_SECONDS * SAMPLE_RATE else None
    order = []  # speakers in order of first appearance
    for _, _, spk in sorted(turns):
        if spk not in order:
            order.append(spk)
    candidates = []
    for spk, e in embs.items():
        hit = _confident(_scores(e)) if e is not None else None
        if hit:
            candidates.append((hit[0], spk, hit[1]))
    named, used = {}, set()
    for score, spk, name in sorted(candidates, reverse=True):
        if spk not in named and name not in used:
            named[spk] = name
            used.add(name)
    out, n = {}, 0
    for spk in order:
        if spk in named:
            out[spk] = (named[spk] + "?", embs[spk])
        else:
            n += 1
            out[spk] = (f"Speaker {n}", embs[spk])
    return out


class LiveTracker:
    """Speaker labels for one live captions stream from a shared microphone:
    known voices by name, others clustered into "Speaker N" on the fly."""
    def __init__(self):
        self.clusters = []  # [label, centroid, count]
        self.last = None

    def label(self, f32_16k):
        if f32_16k is None or len(f32_16k) < LIVE_MIN_SECONDS * SAMPLE_RATE:
            return self.last  # too short to tell: assume the same person kept talking
        emb = embed(f32_16k)
        if emb is None:
            return self.last
        name, _ = match(emb)
        if name:
            self.last = name + "?"
            return self.last
        best, best_score = None, -1.0
        for cl in self.clusters:
            score = float(np.dot(emb, cl[1]))
            if score > best_score:
                best, best_score = cl, score
        if best is not None and best_score >= config.VOICE_MATCH_THRESHOLD:
            best[2] += 1
            best[1] = _norm(best[1] * min(best[2], MAX_SAMPLES) + emb)
            self.last = best[0]
            return best[0]
        cl = [f"Speaker {len(self.clusters) + 1}", emb, 1]
        self.clusters.append(cl)
        self.last = cl[0]
        return cl[0]


def main():
    """python voices.py compare a.wav b.wav — similarity of two recordings, to
    calibrate VOICE_MATCH_THRESHOLD / VOICE_MATCH_MARGIN on your own voices."""
    import sys
    import stt
    if len(sys.argv) != 4 or sys.argv[1] != "compare":
        print(main.__doc__)
        return
    a, b = (embed(stt.decode_file(p)) for p in sys.argv[2:4])
    if a is None or b is None:
        print("A recording is too short (need at least 1 s of speech).")
        return
    s = float(np.dot(a, b))
    print(f"similarity {s:.3f} — threshold {config.VOICE_MATCH_THRESHOLD} "
          f"({'same speaker' if s >= config.VOICE_MATCH_THRESHOLD else 'different speakers'} by current settings)")


if __name__ == "__main__":
    main()
