import json
import time

import agent
import config
import db
import notifications
import scheduler
from werkzeug.security import generate_password_hash


def tool_call(name, args, id="c1"):
    return {"content": None, "tool_calls": [
        {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def make_client(app, admin=False):
    import conftest
    conftest._n[0] += 1
    uid = db.create_user(f"u{conftest._n[0]}x", generate_password_hash("pw"))
    with db.get_db() as c:
        c.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(admin), uid))
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"], s["csrf_token"] = uid, "tok"
    c.environ_base["HTTP_X_CSRF_TOKEN"] = "tok"
    c.user_id = uid
    return c


def test_streaming_text_and_tool_calls(llm, room):
    llm.handler = lambda b: (tool_call("calculate", {"expression": "6*7"})
                             if b["messages"][-1]["role"] == "user" else {"content": "The answer is 42."})
    seen = []
    out = agent.run([{"role": "user", "content": "6*7?"}], agent.Context(room=room), on_token=seen.append)
    assert all(c.get("stream") for c in llm.calls)
    assert out["steps"][0]["result"] == {"result": 42}  # tool call assembled from fragments
    assert out["reply"] == "The answer is 42." and seen[-1] == "The answer is 42." and len(seen) > 1


def test_streaming_hides_text_protocol_json(llm, room):
    llm.handler = lambda b: ((400, {"error": "no tools support"}) if "tools" in b else
                             {"content": "Done."} if str(b["messages"][-1]["content"]).startswith("Result")
                             else {"content": '{"tool": "get_time", "arguments": {}}'})
    seen = []
    out = agent.run([{"role": "user", "content": "time?"}], agent.Context(room=room), on_token=seen.append)
    assert out["reply"] == "Done." and not any("{" in s for s in seen)


def test_actions_wait_for_approval(app, client, llm, room):
    while db.active_meeting_for_room("general"):  # left over from other tests
        db.stop_meeting(db.active_meeting_for_room("general")["id"])
    llm.handler = lambda b: ({"content": "I asked to start recording."} if b["messages"][-1]["role"] == "tool"
                             else tool_call("start_recording", {"title": "Retro"}))
    client.post("/api/rooms/general/messages", json={"content": "@tower record this as Retro"})
    reply = db.recent_messages(room["id"])[-1]["content"]
    pid = int(reply.split("[confirm:")[1].split("]")[0])
    assert db.active_meeting_for_room("general") is None  # not started yet
    other = make_client(app)
    assert other.post(f"/api/agent/pending/{pid}", json={"decision": "approve"}).status_code == 403
    r = client.post(f"/api/agent/pending/{pid}", json={"decision": "approve"})
    assert r.status_code == 200 and r.json["result"]["recording"]
    m = db.active_meeting_for_room("general")
    assert m and m["title"] == "Retro"
    db.stop_meeting(m["id"])
    assert db.recent_messages(room["id"])[-1]["content"].startswith("✅")
    assert client.post(f"/api/agent/pending/{pid}", json={"decision": "approve"}).status_code == 409


def test_rejecting_an_action(client, llm, room):
    ctx = agent.Context(user=db.get_user(client.user_id), room=room, confirm=True)
    res = agent._loop.__globals__["request_approval"](ctx, "post_message", {"room": "general", "text": "spam"})
    r = client.post(f"/api/agent/pending/{res['pending_id']}", json={"decision": "reject"})
    assert r.json["status"] == "rejected" and "rejected" in db.recent_messages(room["id"])[-1]["content"]


def test_tool_permissions(app, llm):
    admin, member = make_client(app, admin=True), make_client(app)
    assert member.get("/admin/tools").status_code == 403
    form = {f"perm_{n}": "everyone" for n in agent.TOOLS}
    form.update(perm_network_status="admins", perm_calculate="off", admin=str(admin.user_id))
    assert admin.post("/admin/tools", data=form).status_code == 302
    as_member = agent.available_tools(db.get_user(member.user_id))
    as_admin = agent.available_tools(db.get_user(admin.user_id))
    assert "network_status" not in as_member and "network_status" in as_admin
    assert "calculate" not in as_member and "calculate" not in as_admin
    db.set_setting("tool_permissions", {})


def test_schedule_math_and_runs(client, llm, room):
    friday_noon = time.mktime((2026, 11, 6, 12, 0, 0, 0, 0, -1))  # a Friday
    nxt = scheduler.next_run("weekly", "17:00", 4, now=friday_noon)
    assert time.localtime(nxt)[:5] == (2026, 11, 6, 17, 0)
    assert time.localtime(scheduler.next_run("daily", "09:00", now=friday_noon))[2:4] == (7, 9)
    llm.handler = lambda b: {"content": "Weekly summary: all good."}
    r = client.post("/schedules", data={"room": "general", "prompt": "Summarize the week",
                                        "kind": "interval", "every_min": "5"})
    assert r.status_code == 302
    sid = scheduler.list_all()[-1]["id"]
    with db.get_db() as c:
        c.execute("UPDATE schedules SET next_run = ? WHERE id = ?", (time.time() - 1, sid))
    assert scheduler.run_due() == 1 and scheduler.run_due() == 0  # claimed once
    assert db.recent_messages(room["id"])[-1]["content"].startswith("⏰ Summarize the week")
    assert scheduler.get(sid)["next_run"] > time.time() + 200
    assert any(n["kind"] == "schedule" for n in notifications.recent(client.user_id))
    scheduler.delete(sid)


def test_mentions_and_unread(app, client, room):
    other = make_client(app)
    name = db.get_user(other.user_id)["username"]
    client.post("/api/rooms/general/messages", json={"content": f"hey @{name} look"})
    j = other.get("/api/notifications").json
    assert j["unread"] >= 1 and j["items"][0]["kind"] == "mention"
    assert j["rooms"].get("general", 0) >= 1
    with db.get_db() as c:
        last = c.execute("SELECT MAX(id) FROM messages WHERE room_id = ?", (room["id"],)).fetchone()[0]
    other.post("/api/rooms/general/read", json={"last_id": last})
    assert other.get("/api/notifications").json["rooms"].get("general", 0) == 0
    other.get("/notifications")
    assert other.get("/api/notifications").json["unread"] == 0


def test_action_items_notify_their_owner(app, llm):
    owner = make_client(app)
    name = db.get_user(owner.user_id)["username"]
    m = db.get_meeting(db.start_meeting("general", "Plan", None))
    notifications.on_action_items(m, [{"task": "Do X", "owner": name.upper()}, {"task": "Y", "owner": "nobody"}])
    items = notifications.recent(owner.user_id)
    assert items[0]["kind"] == "action_item" and "Do X" in items[0]["text"]
    db.stop_meeting(m["id"])


def test_eval_runner(llm, monkeypatch, tmp_path):
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "evals"))
    import run as evalrun
    monkeypatch.setenv("TOWERAI_DB", os.environ["TOWERAI_DB"])
    monkeypatch.setattr(config, "DB_PATH", config.DB_PATH)
    monkeypatch.setattr(config, "SCHEDULER_ENABLED", False)

    def handler(b):
        last = b["messages"][-1]
        if last["role"] == "tool":
            return {"content": f"It is {json.loads(last['content'])['result']}"}
        return tool_call("calculate", {"expression": "1234*56"})
    llm.handler = handler
    s = evalrun.main(["--cases", "arithmetic,no-tool-needed", "--out", str(tmp_path)])
    by_id = {r["id"]: r for r in s["results"]}
    assert by_id["arithmetic"]["ok"] and by_id["arithmetic"]["reply"] == "It is 69104"
    assert not by_id["no-tool-needed"]["ok"]  # used a tool when none was expected
    assert list(tmp_path.glob("*.json"))
