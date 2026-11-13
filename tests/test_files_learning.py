import io
import json
import zipfile

import agent
import db
import learning
from test_harness import make_client


def docx_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
                                        f'</w:body></w:document>')
    return buf.getvalue()


def test_shared_files_and_agent_tools(app, client, room):
    r = client.post("/rooms/general/files", content_type="multipart/form-data", data={"file": [
        (io.BytesIO(b"Wi-Fi password for the event: tower-2024"), "event notes.txt"),
        (io.BytesIO(docx_bytes("The venue opens at 8am")), "venue.docx"),
        (io.BytesIO(b"<script>alert(1)</script>"), "x.html")]})
    assert r.status_code == 302
    listing = client.get("/rooms/general/files").get_data(as_text=True)
    assert "event_notes.txt" in listing and "venue.docx" in listing
    assert "shared [file:" in db.recent_messages(room["id"])[-1]["content"]

    ctx, tools = agent.Context(room=room), agent.available_tools()
    found = agent.run_tool(ctx, "search_files", {"query": "venue opens"}, tools)["results"]
    assert found and found[0]["filename"] == "venue.docx"
    files = {f["filename"]: f for f in agent.run_tool(ctx, "list_files", {}, tools)["files"]}
    text = agent.run_tool(ctx, "read_file", {"file_id": files["event_notes.txt"]["file_id"]}, tools)["text"]
    assert "tower-2024" in text

    dl = client.get(f"/files/{files['x.html']['file_id']}/download")
    assert dl.headers["Content-Disposition"].startswith("attachment") and dl.mimetype == "application/octet-stream"
    other = make_client(app)
    assert other.post(f"/files/{files['x.html']['file_id']}/delete").status_code == 403
    assert client.post(f"/files/{files['x.html']['file_id']}/delete").status_code == 302
    assert client.get(f"/files/{files['x.html']['file_id']}/download").status_code == 404


def test_feedback_teaches_the_assistant(app, client, llm, room):
    db.add_message(room["id"], client.user_id, "user", client.username, "@tower where is the projector kept?")
    mid = db.add_message(room["id"], None, "assistant", "Tower", "I don't know.\n\n🔧 search_files")
    uid_msg = db.add_message(room["id"], client.user_id, "user", client.username, "thanks")
    assert client.post(f"/api/messages/{uid_msg}/feedback", json={"rating": 1}).status_code == 400
    r = client.post(f"/api/messages/{mid}/feedback",
                    json={"rating": -1, "correction": "In the cabinet next to room 2."})
    assert r.status_code == 200
    assert learning.stats()["corrected"] >= 1

    # The correction is shown to the model for a similar question…
    agent.run([{"role": "user", "content": "where is the projector?"}], agent.Context(room=room))
    system = llm.calls[-1]["messages"][0]["content"]
    assert "In the cabinet next to room 2." in system and "where is the projector kept" in system
    # …but not for an unrelated one.
    agent.run([{"role": "user", "content": "what time is lunch today"}], agent.Context(room=room))
    assert "cabinet" not in llm.calls[-1]["messages"][0]["content"]

    # Fine-tuning export: chat format, corrected answer, no tool footers.
    rows = [json.loads(line) for line in learning.export_jsonl().splitlines()]
    row = [r for r in rows if r["messages"][-1]["content"] == "In the cabinet next to room 2."][0]
    assert row["messages"][0]["role"] == "system" and row["messages"][-2]["role"] == "user"
    assert all("🔧" not in m["content"] for m in row["messages"])


def test_training_pages_are_admin_only(app):
    admin, member = make_client(app, admin=True), make_client(app)
    assert member.get("/admin/training").status_code == 403
    assert admin.get("/admin/training").status_code == 200
    r = admin.get("/admin/training/export.jsonl")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]


def test_split_script(tmp_path):
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "training"))
    import split
    src = tmp_path / "d.jsonl"
    src.write_text("".join(json.dumps({"messages": [{"role": "user", "content": str(i)}]}) + "\n"
                           for i in range(20)))
    assert split.split(str(src), str(tmp_path / "data")) == (18, 2)
    assert len((tmp_path / "data" / "train.jsonl").read_text().splitlines()) == 18
