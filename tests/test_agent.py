import json

import agent
import config
import db


def tool_call(name, args, id="c1"):
    return {"content": None, "tool_calls": [
        {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def last_user(body):
    return body["messages"][-1]


def test_native_tool_use_from_chat(client, llm, room):
    mid = db.start_meeting("general", "Q3 budget", client.user_id)
    db.add_segment(mid, "bruno", "the budget of 3600 is approved")
    db.stop_meeting(mid)

    def handler(body):
        if last_user(body)["role"] == "tool":
            hit = json.loads(last_user(body)["content"])["results"][0]["text"]
            return {"content": f"Approved: {hit}"}
        return tool_call("search_transcripts", {"query": "budget"})
    llm.handler = handler
    assert client.post("/api/rooms/general/messages", json={"content": "@tower budget?"}).status_code == 200
    reply = db.recent_messages(room["id"])[-1]["content"]
    assert reply.startswith("Approved: the budget of 3600") and "🔧 search_transcripts" in reply
    run = db.recent_agent_runs(1)[0]
    assert run["user_id"] == client.user_id and run["room"] == "general"
    assert json.loads(run["steps"])[0] == {"tool": "search_transcripts", "arguments": {"query": "budget"}, "ok": True}


def test_text_protocol_fallback(client, llm):
    def handler(body):
        if "tools" in body:
            return 400, {"error": "this model does not support tools"}
        if str(last_user(body)["content"]).startswith("Result of calculate"):
            return {"content": "It is 900"}
        return {"content": '```json\n{"tool": "calculate", "arguments": {"expression": "(1200*3)/4"}}\n```'}
    llm.handler = handler
    j = client.post("/api/agent", json={"prompt": "compute"}).json
    assert j["protocol"] == "text" and j["reply"] == "It is 900"
    assert j["steps"][0]["result"] == {"result": 900.0}


def test_repeated_calls_are_cached_and_stop_the_loop(llm):
    llm.handler = lambda b: ({"content": "final"} if "Stop using tools" in str(last_user(b)["content"])
                             else tool_call("get_time", {}))
    out = agent.run([{"role": "user", "content": "loop"}])
    assert out["reply"] == "final" and len(out["steps"]) == 3
    assert "already called" in out["steps"][1]["result"]["note"]


def test_step_limit(llm, monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_STEPS", 3)
    n = [0]

    def handler(b):
        if "Stop using tools" in str(last_user(b)["content"]):
            return {"content": "done"}
        n[0] += 1
        return tool_call("calculate", {"expression": str(n[0])})  # different args each time
    llm.handler = handler
    out = agent.run([{"role": "user", "content": "go"}])
    assert out["reply"] == "done" and len(out["steps"]) == 3


def test_read_only_mode_and_bad_input(room, monkeypatch):
    monkeypatch.setattr(config, "AGENT_ALLOW_WRITES", False)
    tools = agent.available_tools()
    assert "post_message" not in tools and "remember" not in tools and "recall" in tools
    ctx = agent.Context(room=room)
    assert "error" in agent.run_tool(ctx, "post_message", {"room": "general", "text": "x"}, tools)
    assert "bad arguments" in agent.run_tool(ctx, "get_time", {"x": 1}, tools)["error"]
    for bad in ("__import__('os')", "2**10000", "open('x')"):
        assert "error" in agent.run_tool(ctx, "calculate", {"expression": bad}, tools)


def test_room_memory_is_shown_to_the_model(client, llm, room):
    ctx = agent.Context(user=db.get_user(client.user_id), room=room)
    tools = agent.available_tools()
    saved = agent.run_tool(ctx, "remember", {"text": "Standups are at 9:15"}, tools)
    assert saved["saved"]
    assert agent.run_tool(ctx, "recall", {"query": "standup"}, tools)["notes"][0]["text"] == "Standups are at 9:15"
    agent.run([{"role": "user", "content": "when is standup?"}], ctx)
    assert "Standups are at 9:15" in llm.calls[-1]["messages"][0]["content"]
    assert agent.run_tool(ctx, "forget", {"memory_id": saved["memory_id"]}, tools) == {"deleted": True}


def test_action_item_tools(client):
    mid = db.start_meeting("general", "Planning", client.user_id)
    db.save_meeting_notes(mid, json.dumps({"summary": "", "decisions": [], "action_items": [
        {"task": "Book room", "owner": "Ana", "due": "", "done": False},
        {"task": "Send slides", "owner": "Bruno", "due": "Fri", "done": False}]}))
    tools = agent.available_tools()
    ctx = agent.Context()
    items = agent.run_tool(ctx, "list_action_items", {"owner": "bruno"}, tools)["action_items"]
    assert [i["task"] for i in items] == ["Send slides"]
    agent.run_tool(ctx, "complete_action_item", {"meeting_id": mid, "item_index": items[0]["item_index"]}, tools)
    still_open = agent.run_tool(ctx, "list_action_items", {}, tools)["action_items"]
    assert "Send slides" not in [i["task"] for i in still_open if i["meeting_id"] == mid]


def test_old_tool_results_are_trimmed(monkeypatch):
    monkeypatch.setattr(config, "AGENT_MAX_CONTEXT_CHARS", 500)
    msgs = [{"role": "system", "content": "s"}] + [
        {"role": "tool", "tool_call_id": str(i), "content": "x" * 300} for i in range(4)]
    agent._trim(msgs)
    assert [len(m["content"]) for m in msgs[1:]][-2:] == [300, 300]
    assert "removed" in msgs[1]["content"]
