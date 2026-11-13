import io
import wave

import pytest

import db
import tts
from features import voice


@pytest.mark.parametrize("text,expected", [
    ("Torre, qual é a pauta?", (True, "qual é a pauta?")),
    ("oi torre qual é a pauta", (True, "qual é a pauta")),
    ("Hey Tower, what's next?", (True, "what's next?")),
    ("Torre.", (True, "")),
    ("a torre é alta", (False, "a torre é alta")),
    ("Torrent of data", (False, "Torrent of data")),
])
def test_wake_phrase(text, expected):
    assert voice.split_wake(text) == expected


def test_hands_free_flow(client, llm, room):
    sent = []
    hf = voice.HandsFree(room, db.get_user(client.user_id), sent.append)
    llm.handler = lambda b: {"content": "The agenda is the budget."}

    hf.handle("we were talking about lunch")             # no wake word: ignored
    assert [m["type"] for m in sent] == ["heard"]
    hf.handle("Torre.")                                   # bare wake phrase: listen for the next sentence
    assert sent[-1]["type"] == "wake"
    hf.handle("what is on the agenda?")
    assert sent[-1] == {"type": "reply", "text": "The agenda is the budget.", "tts": tts.available()}
    msgs = [m["content"] for m in db.recent_messages(room["id"])]
    assert "🎤 what is on the agenda?" in msgs and "The agenda is the budget." in msgs[-1]
    hf.handle("Torre, repetir")
    assert sent[-1]["text"] == "The agenda is the budget."
    hf.handle("Torre, parar")
    assert sent[-1] == {"type": "stop"}


def test_speakable_strips_markers():
    assert tts.speakable("Done ✅\n🔧 calculate\n[confirm:3] post_message(…)\nSee [file:2:plan.pdf]") \
        == "Done \nSee plan.pdf"


@pytest.mark.skipif(not tts.available(), reason="no Piper voice in models/piper/")
def test_offline_tts(client):
    r = client.post("/voice/tts", json={"text": "The meeting starts at nine."})
    assert r.status_code == 200 and r.mimetype == "audio/wav"
    w = wave.open(io.BytesIO(r.data))
    assert w.getnframes() / w.getframerate() > 1.0
    assert client.post("/voice/tts", json={"text": "🔧"}).status_code == 400
