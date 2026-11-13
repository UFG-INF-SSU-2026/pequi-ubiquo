"""Uploaded meeting recordings -> speaker-labeled transcript segments.

Pipeline: decode the file (any common audio/video format) -> optional speaker
diarization (diarize.py) -> Whisper transcription (stt.py) with word timestamps
-> each word is attributed to the speaker turn it overlaps most -> consecutive
words by the same speaker are merged into transcript segments.

Speakers come out as "Speaker 1", "Speaker 2"… in order of first appearance;
they can be renamed on the meeting page.
"""
import config
import diarize
import stt

MAX_GAP_SEC = 2.0        # a pause this long starts a new segment
MAX_SEGMENT_SEC = 40.0   # keep segments readable


def _speaker_at(turns, start, end):
    """Index into turns of the speaker overlapping [start, end] most (or nearest)."""
    best, best_ov, nearest, nearest_d = None, 0.0, None, float("inf")
    for t0, t1, spk in turns:
        if t0 > end + 5:
            break
        ov = min(end, t1) - max(start, t0)
        if ov > best_ov:
            best, best_ov = spk, ov
        d = max(t0 - end, start - t1, 0)
        if d < nearest_d:
            nearest, nearest_d = spk, d
    return best if best is not None else nearest


def _label_map(turns):
    order = {}
    for _, _, spk in sorted(turns):
        order.setdefault(spk, f"Speaker {len(order) + 1}")
    return order


def build_segments(whisper_segments, turns, default_speaker="Speaker", labels=None):
    """-> [(speaker, text, start_sec)]. Pure function (unit-testable).
    labels: {speaker_index: name} (e.g. remembered voices); default "Speaker N"."""
    labels = labels or (_label_map(turns) if turns else {})
    out, cur = [], None  # cur = [speaker, [texts], start, end]

    def flush():
        if cur and "".join(cur[1]).strip():
            out.append((cur[0], "".join(cur[1]).strip(), round(cur[2], 2)))

    for seg in whisper_segments:
        words = seg.words if (turns and getattr(seg, "words", None)) else None
        pieces = ([(w.start, w.end, w.word) for w in words] if words
                  else [(seg.start, seg.end, " " + seg.text.strip())])
        for start, end, text in pieces:
            spk = labels.get(_speaker_at(turns, start, end), default_speaker) if turns else default_speaker
            if (cur is None or spk != cur[0] or start - cur[3] > MAX_GAP_SEC
                    or end - cur[2] > MAX_SEGMENT_SEC):
                flush()
                cur = [spk, [], start, end]
            cur[1].append(text)
            cur[3] = end
    flush()
    return out


def process(path, num_speakers=None, use_diarization=True, on_status=lambda s, d=None: None):
    """Transcribe (and diarize) a recording.
    Returns (segments, duration_sec, labeled, voices) — voices: {label: embedding}."""
    on_status("transcribing", "decoding audio")
    audio = stt.decode_file(path)
    duration = len(audio) / stt.SAMPLE_RATE
    if duration < 0.5:
        raise ValueError("recording is empty or too short")

    turns = None
    if use_diarization and diarize.available():
        on_status("diarizing", "0%")
        turns = diarize.diarize(
            audio, num_speakers, progress=lambda f: on_status("diarizing", f"{int(f * 100)}%"))

    labels, voice_embs = None, {}
    if turns:
        import voices
        if voices.available():
            on_status("diarizing", "matching voices")
            named = voices.label_speakers(audio, turns)
            labels = {spk: lbl for spk, (lbl, _) in named.items()}
            voice_embs = {lbl: e for lbl, e in named.values() if e is not None}

    on_status("transcribing", "0%")
    stt.ensure_ready()

    def tracked():
        for seg in stt.transcribe_segments(audio, word_timestamps=bool(turns)):
            on_status("transcribing", f"{min(99, int(seg.end / duration * 100))}%")
            yield seg

    segments = build_segments(tracked(), turns or [], default_speaker=config.UPLOAD_DEFAULT_SPEAKER,
                              labels=labels)
    return segments, duration, bool(turns), voice_embs
