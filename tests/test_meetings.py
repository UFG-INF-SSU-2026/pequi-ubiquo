import io
import json

import db
import recordings
import summarizer
from conftest import FakeWhisper, wav_bytes, wait_for


def test_build_segments_splits_on_speaker_change():
    class W:
        def __init__(s, a, b, t): s.start, s.end, s.word = a, b, t

    class S:
        def __init__(s, words): s.words, s.start, s.end, s.text = words, words[0].start, words[-1].end, ""
    segs = [S([W(0, .4, " hello"), W(.5, .9, " there"), W(1.0, 1.4, " hi"), W(1.5, 1.9, " back")])]
    turns = [(0.0, 0.95, 7), (0.95, 2.0, 3)]
    assert recordings.build_segments(segs, turns) == [
        ("Speaker 1", "hello there", 0.0), ("Speaker 2", "hi back", 1.0)]


def test_parse_notes_handles_fences_strings_and_garbage():
    n = summarizer.parse_notes('```json\n{"summary": "S", "decisions": ["D"], '
                               '"action_items": [{"task": "T", "owner": "Ana"}, "Loose task"]}\n```')
    assert n["summary"] == "S" and n["decisions"] == ["D"]
    assert [i["task"] for i in n["action_items"]] == ["T", "Loose task"]
    assert summarizer.parse_notes("not json at all") == {
        "summary": "not json at all", "decisions": [], "action_items": []}


def test_upload_transcribe_summarize_rename_toggle_export(client, llm):
    FakeWhisper.text = None
    llm.handler = lambda b: {"content": '{"summary": "We met.", "decisions": [], "action_items": '
                                       '[{"task": "Prepare slides", "owner": "Speaker", "due": "Thu"}]}'
                             if "ONLY a JSON" in b["messages"][-1]["content"] else "- notes"}
    r = client.post("/meetings/upload", content_type="multipart/form-data",
                    data={"recording": (io.BytesIO(wav_bytes(8)), "standup.wav"), "summarize": "on"})
    assert r.status_code == 302
    mid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert wait_for(lambda: db.get_meeting(mid)["notes"] is not None), db.get_meeting(mid)["status_detail"]
    m = db.get_meeting(mid)
    assert m["title"] == "standup" and m["status"] is None and round(m["ended_at"] - m["started_at"]) == 8
    segs = db.meeting_segments(mid)
    assert segs and segs[0]["speaker"] == "Speaker" and segs[0]["text"].startswith("w0.0")

    client.post(f"/meetings/{mid}/speakers", data={"old": "Speaker", "new": "Ana"})
    client.post(f"/meetings/{mid}/actions/0/toggle")
    item = json.loads(db.get_meeting(mid)["notes"])["action_items"][0]
    assert item == {"task": "Prepare slides", "owner": "Ana", "due": "Thu", "done": True}
    text = client.get(f"/meetings/{mid}/export").get_data(as_text=True)
    assert "## Action items" in text and "- [x] Prepare slides (Ana, Thu)" in text and "Ana: w0.0" in text


def test_long_transcripts_are_summarized_in_parts(llm):
    llm.handler = lambda b: {"content": '{"summary": "All good.", "decisions": [], "action_items": []}'
                             if "ONLY a JSON" in b["messages"][-1]["content"] else "- part notes"}
    segs = [{"speaker": "ana", "text": "x" * 150, "start_sec": i * 10.0} for i in range(8)]
    notes = summarizer.summarize(segs)
    prompts = [c["messages"][-1]["content"] for c in llm.calls]
    assert notes["summary"] == "All good."
    assert sum(p.startswith("This is part") for p in prompts) >= 3
    assert "Notes from part 1" in prompts[-1]


def test_upload_rejects_non_audio(client):
    r = client.post("/meetings/upload", content_type="multipart/form-data", follow_redirects=True,
                    data={"recording": (io.BytesIO(b"print(1)"), "x.py")})
    assert b"Unsupported file type" in r.data


def test_old_databases_are_upgraded(tmp_path, monkeypatch):
    import sqlite3
    import config
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE meetings (id INTEGER PRIMARY KEY, room TEXT NOT NULL, title TEXT,"
                      " created_by INTEGER, started_at REAL NOT NULL, ended_at REAL);"
                      "CREATE TABLE transcript_segments (id INTEGER PRIMARY KEY, meeting_id INTEGER,"
                      " speaker TEXT, text TEXT NOT NULL, ts REAL NOT NULL);"
                      "INSERT INTO meetings (room, title, started_at) VALUES ('general', 'old', 1);")
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    m = db.get_meeting(1)
    assert m["title"] == "old" and m["source"] == "live" and m["notes"] is None
