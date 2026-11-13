import os

import numpy as np
import pytest

import config
import db
import stt
import vocab
import voices

DATA = os.path.join(os.path.dirname(__file__), "data", "four-speakers.wav")
# Real speaker-model tests: need `python demo_setup.py --download-model` and the
# sample (curl -L -o tests/data/four-speakers.wav https://github.com/k2-fsa/sherpa-onnx/
# releases/download/speaker-segmentation-models/0-four-speakers-zh.wav).
need_models = pytest.mark.skipif(
    not (os.path.exists(DATA) and all(os.path.isfile(p) for p in __import__("diarize").model_paths())),
    reason="speaker models / sample audio not downloaded")


@pytest.fixture
def clean_voices(monkeypatch):
    monkeypatch.setattr(config, "VOICES_ENABLED", True)
    with db.get_db() as c:
        c.execute("DELETE FROM voiceprints")
        c.execute("DELETE FROM meeting_voices")
    yield
    with db.get_db() as c:
        c.execute("DELETE FROM voiceprints")


def test_corrections_teach_whisper_new_words(client, room):
    mid = db.start_meeting("general", "Vocab", client.user_id)
    db.add_segment(mid, "ana", "we will meet with car la from tower ai")
    db.stop_meeting(mid)
    sid = db.meeting_segments(mid)[0]["id"]
    r = client.post(f"/meetings/{mid}/segments/{sid}", data={"text": "we will meet with Carla from TowerAI"})
    assert r.status_code == 302
    assert db.meeting_segments(mid)[0]["text"] == "we will meet with Carla from TowerAI"
    terms = {t["term"] for t in vocab.terms()}
    assert {"Carla", "TowerAI"} <= terms and "meet" not in terms
    vocab._cache["at"] = 0
    stt._transcribe(np.zeros(16000, dtype=np.float32))
    from conftest import FakeWhisper
    prompt = FakeWhisper.last_kwargs["initial_prompt"]
    assert prompt.startswith("Glossary:") and "Carla" in prompt and client.username in prompt
    client.post("/voices", data={"remove_term": "Carla"})
    assert "Carla" not in {t["term"] for t in vocab.terms()}


@need_models
def test_known_voice_is_suggested_and_confirmed(clean_voices):
    audio = stt.decode_file(DATA)
    clip = lambda s, e: audio[int(s * 16000):int(e * 16000)]  # noqa: E731
    voices.learn("Ana", voices.embed(clip(0.3, 6.9)))
    turns = [(0.3, 6.9, 5), (7.0, 10.7, 9), (11.5, 13.6, 9)]
    labels = voices.label_speakers(audio, turns)
    assert labels[5][0] == "Ana?"          # suggestion, not a fact
    assert labels[9][0] == "Speaker 1"     # too little speech to match (< VOICE_MIN_SECONDS)

    # An equally good second voiceprint removes the confidence margin.
    voices.learn("Twin", voices.embed(clip(0.3, 6.9)))
    assert voices.label_speakers(audio, turns)[5][0] == "Speaker 1"


@need_models
def test_renaming_teaches_a_voice(client, clean_voices):
    audio = stt.decode_file(DATA)
    emb = voices.embed(audio[int(7.0 * 16000):int(13.6 * 16000)])
    mid = db.start_meeting("general", "Voices", client.user_id)
    db.add_segment(mid, "Speaker 2", "hello")
    db.stop_meeting(mid)
    voices.save_meeting_voice(mid, "Speaker 2", emb)
    r = client.post(f"/meetings/{mid}/speakers", data={"old": "Speaker 2", "new": "Bruno"})
    assert r.status_code == 302
    vp = {v["name"]: v for v in voices.all_voiceprints()}
    assert "Bruno" in vp and vp["Bruno"]["samples"] == 1
    assert voices.match(emb)[0] == "Bruno"
    # unconfirmed guesses are never learned
    voices.save_meeting_voice(mid, "Speaker 3", emb)
    assert not voices.learn_from_rename(mid, "Speaker 3", "Carla?")


@need_models
def test_live_tracker_keeps_short_utterances_with_previous_speaker(clean_voices):
    audio = stt.decode_file(DATA)
    t = voices.LiveTracker()
    first = t.label(audio[int(0.3 * 16000):int(6.9 * 16000)])
    assert first == "Speaker 1"
    assert t.label(audio[:16000]) == "Speaker 1"  # 1 s: too short, same speaker assumed
