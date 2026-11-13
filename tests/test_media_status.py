import io

import db
import status
from conftest import FakeWhisper, wav_bytes, wait_for

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0f01f0005000201"
                    "a5f0a2b60000000049454e44ae426082")


def test_vision_question_gets_an_answer(client, llm, room):
    llm.handler = lambda b: {"content": "A red square." if isinstance(b["messages"][-1]["content"], list)
                             else "text"}
    r = client.post("/vision/rooms/general/image", content_type="multipart/form-data",
                    data={"image": (io.BytesIO(PNG), "x.png"), "question": "what is this?"})
    assert r.status_code == 200
    assert wait_for(lambda: db.recent_messages(room["id"])[-1]["content"] == "A red square.")
    sent = [c for c in llm.calls if isinstance(c["messages"][-1]["content"], list)][0]
    assert sent["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert client.get(f"/vision/images/{r.json['image']}").data == PNG
    bad = client.post("/vision/rooms/general/image", content_type="multipart/form-data",
                      data={"image": (io.BytesIO(b"<svg/>"), "x.svg")})
    assert bad.status_code == 400


def test_voice_question_is_transcribed_and_answered(client, llm, room):
    FakeWhisper.text = "what time is the meeting"
    llm.handler = lambda b: {"content": "At ten."}
    try:
        r = client.post("/voice/rooms/general/ask", content_type="multipart/form-data",
                        data={"audio": (io.BytesIO(wav_bytes(2)), "clip")})
    finally:
        FakeWhisper.text = None
    assert r.json["text"] == "what time is the meeting" and r.json["reply"].startswith("At ten.")
    assert db.recent_messages(room["id"])[-2]["content"] == "🎤 what time is the meeting"


def _check(client):
    client.post("/status/model-check")
    assert wait_for(lambda: not status._check["running"])
    return client.get("/api/status").json


def test_model_check_capable_and_weak_models(client, llm):
    def capable(b):
        if "tools" in b:
            return {"content": None, "tool_calls": [{"id": "1", "type": "function",
                                                     "function": {"name": "get_time", "arguments": "{}"}}]}
        return {"content": "Red." if isinstance(b["messages"][-1]["content"], list) else "OK"}
    llm.handler = capable
    mc = _check(client)["model_check"]["result"]
    assert mc["chat"]["ok"] and mc["tools"]["mode"] == "native" and mc["vision"]["ok"]

    llm.handler = lambda b: ((400, {"error": "model does not support tools"}) if "tools" in b else
                             (400, {"error": "no vision"}) if isinstance(b["messages"][-1]["content"], list)
                             else {"content": "OK"})
    j = _check(client)
    mc = j["model_check"]["result"]
    assert mc["tools"]["mode"] == "rejected" and not mc["vision"]["ok"]
    assert any("AGENT_PROTOCOL=text" in t["text"] for t in j["advice"])


def test_status_page_requires_login_and_renders(app, client):
    assert app.test_client().get("/status").status_code == 302
    page = client.get("/status").get_data(as_text=True)
    assert "System status" in page and "Assistant activity" in page
