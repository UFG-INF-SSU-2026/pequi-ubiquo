"""Agent harness: lets the local LLM use tools over TowerAI's own data.

The model gets a set of tools (search meeting transcripts, read a room, start a
recording, check the network…) and runs a loop: call the model -> run the tools
it asks for -> feed the results back -> repeat until it answers, or the step
limit is hit.

Two protocols, picked automatically:
  - native: OpenAI-style `tools` / `tool_calls` (LM Studio, Ollama, llama.cpp
    with tool-capable models such as Qwen 2.5/3, Llama 3.1+, Mistral).
  - text:   for models without tool calling, the tools are described in the
    system prompt and the model replies with a JSON object to call one.

Safety: tools only touch TowerAI's own data. There is deliberately NO shell,
file, or host-control tool (see features/README.md, "Do NOT port"). Tools that
change something (post a message, start/stop a recording, summarize) are marked
`writes` and can be switched off with AGENT_ALLOW_WRITES=0.

Used by the chat assistant (chat.assistant_reply), POST /api/agent, and the CLI:
    python agent.py "what did we decide about the budget?"
"""
import ast
import json
import operator
import re
import time
from datetime import datetime

import config
import db
import llm

TOOLS = {}  # name -> {"fn", "description", "parameters", "writes"}


def tool(description, writes=False, confirm=False, **params):
    """Register fn as a tool. params: name=(json_type, description[, required]).
    writes: changes something (off when AGENT_ALLOW_WRITES=0).
    confirm: when a person asked, the action waits for their approval in chat."""
    def deco(fn):
        props, required = {}, []
        for pname, spec in params.items():
            props[pname] = {"type": spec[0], "description": spec[1]}
            if len(spec) < 3 or spec[2]:
                required.append(pname)
        TOOLS[fn.__name__] = {
            "fn": fn, "description": description, "writes": writes, "confirm": confirm,
            "parameters": {"type": "object", "properties": props, "required": required},
        }
        return fn
    return deco


class Context:
    """Who is asking and where; tools default to this room.
    confirm: actions marked `confirm` wait for approval instead of running
    (chat requests); the CLI and approved actions run them directly."""
    def __init__(self, user=None, room=None, confirm=False):
        self.user = user    # db row or None (CLI / trusted local caller)
        self.room = room    # db row or None
        self.confirm = confirm
        self.pending = []   # [(pending_id, description)] created during this run


def _room(ctx, name):
    r = db.get_room(name) if name else ctx.room
    if not r:
        raise ValueError(f"no such room: {name!r}" if name else "no room given")
    return r


def _ts(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else None


# --- Tools -----------------------------------------------------------------
@tool("Current local date and time.")
def get_time(ctx):
    return {"now": datetime.now().strftime("%Y-%m-%d %H:%M (%A)")}


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


@tool("Evaluate an arithmetic expression exactly, e.g. '(1200*3)/4'.",
      expression=("string", "the expression"))
def calculate(ctx, expression):
    return {"result": _eval(ast.parse(expression, mode="eval").body)}


@tool("List the chat rooms.")
def list_rooms(ctx):
    return {"rooms": [r["name"] for r in db.list_rooms()]}


@tool("Read the most recent messages of a chat room.",
      room=("string", "room name; defaults to the current room", False),
      limit=("integer", "how many messages (max 50)", False))
def read_messages(ctx, room=None, limit=20):
    r = _room(ctx, room)
    rows = db.recent_messages(r["id"], limit=max(1, min(int(limit), 50)))
    return {"room": r["name"], "messages": [
        {"from": m["username"], "at": _ts(m["created_at"]), "text": m["content"][:500]} for m in rows]}


@tool("Post a message, as the assistant, into a chat room.", writes=True, confirm=True,
      room=("string", "room name"), text=("string", "message text"))
def post_message(ctx, room, text):
    import chat
    r = _room(ctx, room)
    chat.deliver(r, None, "assistant", config.ASSISTANT_NAME, text.strip()[:4000])
    return {"posted": True, "room": r["name"]}


@tool("Full-text search across all stored meeting transcripts.",
      query=("string", "words to search for"),
      limit=("integer", "max results (max 30)", False))
def search_transcripts(ctx, query, limit=15):
    rows = db.search_segments(query, limit=max(1, min(int(limit), 30)))
    return {"results": [{"meeting_id": r["meeting_id"], "meeting": r["title"], "room": r["room"],
                         "speaker": r["speaker"], "text": r["text"][:300], "at": _ts(r["ts"])}
                        for r in rows]}


@tool("List recent meetings (newest first).",
      limit=("integer", "how many (max 30)", False))
def list_meetings(ctx, limit=10):
    return {"meetings": [
        {"id": m["id"], "title": m["title"], "room": m["room"], "started": _ts(m["started_at"]),
         "recording": not m["ended_at"], "segments": m["segment_count"],
         "has_summary": bool(m["notes"])}
        for m in db.list_meetings(limit=max(1, min(int(limit), 30)))]}


@tool("Get a meeting's summary, decisions, action items and (optionally) transcript.",
      meeting_id=("integer", "meeting id"),
      include_transcript=("boolean", "also return the transcript text", False))
def get_meeting(ctx, meeting_id, include_transcript=False):
    m = db.get_meeting(int(meeting_id))
    if not m:
        raise ValueError(f"no meeting {meeting_id}")
    out = {"id": m["id"], "title": m["title"], "room": m["room"], "started": _ts(m["started_at"]),
           "recording": not m["ended_at"],
           "notes": json.loads(m["notes"]) if m["notes"] else None,
           "speakers": [s["speaker"] for s in db.meeting_speakers(m["id"])]}
    if include_transcript:
        import summarizer
        text = "\n".join(summarizer.transcript_lines(db.meeting_segments(m["id"])))
        limit = config.AGENT_MAX_RESULT_CHARS - 600
        out["transcript"] = text if len(text) <= limit else text[:limit] + "\n…(truncated)"
    return out


@tool("Start recording the live captions of a room as a new meeting.", writes=True, confirm=True,
      room=("string", "room name; defaults to the current room", False),
      title=("string", "meeting title", False))
def start_recording(ctx, room=None, title=None):
    r = _room(ctx, room)
    m = db.active_meeting_for_room(r["name"])
    if m:
        return {"already_recording": True, "meeting_id": m["id"]}
    title = (title or f"{r['name']} — {datetime.now():%Y-%m-%d %H:%M}")[:120]
    mid = db.start_meeting(r["name"], title, ctx.user["id"] if ctx.user else None)
    return {"recording": True, "meeting_id": mid}


@tool("Stop the meeting recording running in a room.", writes=True, confirm=True,
      room=("string", "room name; defaults to the current room", False))
def stop_recording(ctx, room=None):
    r = _room(ctx, room)
    m = db.active_meeting_for_room(r["name"])
    if not m:
        return {"was_recording": False}
    db.stop_meeting(m["id"])
    return {"stopped": True, "meeting_id": m["id"]}


@tool("Write (or rewrite) a meeting's summary, decisions and action items. Slow.", writes=True,
      meeting_id=("integer", "meeting id"))
def summarize_meeting(ctx, meeting_id):
    import summarizer
    mid = int(meeting_id)
    if not db.get_meeting(mid):
        raise ValueError(f"no meeting {meeting_id}")
    notes = summarizer.summarize(db.meeting_segments(mid))
    db.save_meeting_notes(mid, json.dumps(notes, ensure_ascii=False))
    import notifications
    notifications.on_action_items(db.get_meeting(mid), notes["action_items"])
    return notes


@tool("List the registered IoT devices (microphones, sensors).")
def list_devices(ctx):
    return {"devices": [{"name": d["name"], "room": d["room"], "last_seen": _ts(d["last_seen"])}
                        for d in db.list_devices()]}


@tool("Latest network scan: devices on the LAN with vendor, latency and open ports.")
def network_status(ctx):
    row = db.last_netscan()
    if not row:
        return {"scan": None, "hint": "no scan yet — run one from the Network Map page"}
    data = json.loads(row["data"])
    devs = data.get("devices", [])
    return {"scanned": _ts(data.get("ts")), "host": data.get("host"), "wifi": data.get("wifi"),
            "device_count": data.get("device_count", len(devs)),
            "devices": [{k: d.get(k) for k in ("ip", "name", "vendor", "rtt_ms", "open_ports")}
                        for d in devs[:60]]}


@tool("List action items from meeting notes (newest meetings first).",
      open_only=("boolean", "only items not done yet (default true)", False),
      owner=("string", "only items owned by this person", False))
def list_action_items(ctx, open_only=True, owner=None):
    items = []
    for m in db.list_meetings(limit=200):
        if not m["notes"]:
            continue
        try:
            notes = json.loads(m["notes"])
        except ValueError:
            continue
        for i, it in enumerate(notes.get("action_items", [])):
            if open_only and it.get("done"):
                continue
            if owner and owner.lower() not in (it.get("owner") or "").lower():
                continue
            items.append({"meeting_id": m["id"], "meeting": m["title"], "item_index": i,
                          "task": it.get("task"), "owner": it.get("owner"), "due": it.get("due"),
                          "done": bool(it.get("done"))})
    return {"action_items": items[:60], "total": len(items)}


@tool("Mark a meeting action item as done (or not done).", writes=True, confirm=True,
      meeting_id=("integer", "meeting id"), item_index=("integer", "item_index from list_action_items"),
      done=("boolean", "true = done (default), false = reopen", False))
def complete_action_item(ctx, meeting_id, item_index, done=True):
    m = db.get_meeting(int(meeting_id))
    notes = json.loads(m["notes"]) if m and m["notes"] else None
    items = (notes or {}).get("action_items", [])
    if not 0 <= int(item_index) < len(items):
        raise ValueError("no such action item — call list_action_items first")
    items[int(item_index)]["done"] = bool(done)
    db.save_meeting_notes(m["id"], json.dumps(notes, ensure_ascii=False))
    return {"task": items[int(item_index)]["task"], "done": bool(done)}


@tool("Save a short note to this room's memory, to recall in later conversations "
      "(decisions, preferences, facts people ask you to remember).", writes=True,
      text=("string", "the note, one or two sentences"))
def remember(ctx, text):
    if not ctx.room:
        raise ValueError("memory needs a room")
    text = text.strip()[:500]
    if not text:
        raise ValueError("empty note")
    author = ctx.user["username"] if ctx.user else config.ASSISTANT_NAME
    return {"saved": True, "memory_id": db.add_memory(ctx.room["name"], text, author)}


@tool("Search this room's saved notes (memory).",
      query=("string", "words to look for; empty = most recent notes", False))
def recall(ctx, query=None):
    if not ctx.room:
        raise ValueError("memory needs a room")
    return {"notes": [{"id": n["id"], "text": n["text"], "by": n["author"], "at": _ts(n["created_at"])}
                      for n in db.list_memory(ctx.room["name"], query or None)]}


@tool("Delete a saved note from this room's memory.", writes=True, confirm=True,
      memory_id=("integer", "id from recall"))
def forget(ctx, memory_id):
    if not ctx.room:
        raise ValueError("memory needs a room")
    return {"deleted": bool(db.delete_memory(ctx.room["name"], int(memory_id)))}


@tool("List the files shared in a room.",
      room=("string", "room name; defaults to the current room", False))
def list_files(ctx, room=None):
    import files
    r = _room(ctx, room)
    return {"room": r["name"], "files": [
        {"file_id": f["id"], "filename": f["filename"], "size_kb": round(f["size"] / 1024, 1),
         "by": f["username"], "at": _ts(f["created_at"]), "readable": bool(f["text_len"])}
        for f in files.list_room(r["name"])]}


@tool("Search the text of shared files (all rooms, or one).",
      query=("string", "words to look for"), room=("string", "limit to this room", False))
def search_files(ctx, query, room=None):
    import files
    return {"results": files.search(query, _room(ctx, room)["name"] if room else None)}


@tool("Read the text of a shared file (in parts for long files).",
      file_id=("integer", "id from list_files / search_files"),
      start=("integer", "character offset to start from (default 0)", False))
def read_file(ctx, file_id, start=0):
    import files
    f = files.get(int(file_id))
    if not f:
        raise ValueError(f"no file {file_id}")
    if not f["text"]:
        return {"filename": f["filename"], "error": "no readable text in this file type"}
    size = config.AGENT_MAX_RESULT_CHARS - 400
    start = max(0, int(start))
    part = f["text"][start:start + size]
    return {"filename": f["filename"], "start": start, "text": part, "total_chars": len(f["text"]),
            "more": start + size < len(f["text"])}


@tool("List the scheduled (recurring) assistant tasks.")
def list_schedules(ctx):
    import scheduler
    return {"schedules": [{"id": s["id"], "room": s["room"], "prompt": s["prompt"],
                           "when": scheduler.describe(s), "next_run": _ts(s["next_run"])}
                          for s in scheduler.list_all()]}


@tool("Schedule a recurring task for the assistant (it runs the prompt and posts the answer).",
      writes=True, confirm=True,
      prompt=("string", "what to do each time"),
      kind=("string", "daily | weekly | interval"),
      at_time=("string", "HH:MM for daily/weekly", False),
      weekday=("integer", "0=Monday … 6=Sunday, for weekly", False),
      every_min=("integer", "minutes, for interval (min 5)", False),
      room=("string", "room to post in; defaults to the current room", False))
def create_schedule(ctx, prompt, kind, at_time=None, weekday=None, every_min=None, room=None):
    import scheduler
    r = _room(ctx, room)
    sid = scheduler.create(r["name"], prompt, kind, at_time, weekday, every_min,
                           ctx.user["id"] if ctx.user else None)
    s = scheduler.get(sid)
    return {"scheduled": True, "id": sid, "when": scheduler.describe(s), "next_run": _ts(s["next_run"])}


@tool("Delete a scheduled task.", writes=True, confirm=True, schedule_id=("integer", "id from list_schedules"))
def delete_schedule(ctx, schedule_id):
    import scheduler
    return {"deleted": bool(scheduler.delete(int(schedule_id)))}


# --- Loop ------------------------------------------------------------------
GUIDE = (
    "You can use tools to look things up in TowerAI (rooms, messages, meeting "
    "transcripts and their summaries, IoT devices, the network) and to act there. "
    "Use a tool whenever the answer depends on that data — never guess it. "
    "Call tools one step at a time, then answer the user plainly and briefly, in "
    "their language. If a tool returns an error, fix the arguments or explain. "
    "When someone asks you to remember something, use the remember tool."
)

TEXT_PROTOCOL = (
    "\n\nTo use a tool, reply with ONLY a JSON object, no other text:\n"
    '{{"tool": "<name>", "arguments": {{...}}}}\n'
    "You will get the result and can call another tool or answer. When you are "
    "ready to answer, reply with plain text (not JSON).\n\nTools:\n{tools}"
)


PERMISSIONS = ("everyone", "admins", "off")


def tool_permissions():
    """{tool: 'everyone'|'admins'|'off'} as set by admins (default: everyone)."""
    saved = db.get_setting("tool_permissions", {}) or {}
    return {n: saved.get(n, "everyone") for n in TOOLS}


def available_tools(user=None):
    """Tools this requester may use. user=None is a trusted local caller (CLI)."""
    perms = tool_permissions()
    is_admin = user is None or bool(user["is_admin"])
    return {n: t for n, t in TOOLS.items()
            if (config.AGENT_ALLOW_WRITES or not t["writes"])
            and perms[n] != "off" and (perms[n] == "everyone" or is_admin)}


def describe_call(name, args):
    shown = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)[:120]}" for k, v in (args or {}).items())
    return f"{name}({shown})"


def request_approval(ctx, name, args):
    pid = db.create_pending(ctx.room["name"] if ctx.room else "general",
                            ctx.user["id"] if ctx.user else None, name, json.dumps(args, default=str))
    ctx.pending.append((pid, describe_call(name, args)))
    return {"status": "waiting_for_approval", "pending_id": pid,
            "note": "Not done yet: the user must approve this in the chat. Tell them what "
                    "you asked to do; it runs when someone clicks Approve."}


def execute_pending(pid, decider, approve):
    """Approve (run) or reject a pending action. Returns (row, result) or raises."""
    row = db.get_pending(pid)
    if not row:
        raise LookupError("no such request")
    if row["status"] != "pending":
        raise ValueError(f"already {row['status']}")
    if not (decider["is_admin"] or decider["id"] == row["user_id"]):
        raise PermissionError("only the person who asked, or an admin, can decide")
    if not approve:
        db.decide_pending(pid, "rejected", None, decider["id"])
        return row, None
    requester = db.get_user(row["user_id"]) if row["user_id"] else None
    ctx = Context(user=requester, room=db.get_room(row["room"]))
    tools = available_tools(requester)
    result = run_tool(ctx, row["tool"], json.loads(row["arguments"]), tools)
    status = "failed" if isinstance(result, dict) and "error" in result else "approved"
    db.decide_pending(pid, status, json.dumps(result, ensure_ascii=False, default=str)[:2000], decider["id"])
    return row, result


def tool_specs(tools):
    return [{"type": "function", "function": {
        "name": n, "description": t["description"], "parameters": t["parameters"]}}
        for n, t in tools.items()]


def run_tool(ctx, name, args, tools):
    t = tools.get(name)
    if not t:
        return {"error": f"unknown tool {name!r}; available: {', '.join(tools)}"}
    if not isinstance(args, dict):
        return {"error": "arguments must be a JSON object"}
    try:
        return t["fn"](ctx, **args)
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    except Exception as e:
        return {"error": str(e)[:300]}


def _dump(result):
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= config.AGENT_MAX_RESULT_CHARS else s[:config.AGENT_MAX_RESULT_CHARS] + "…(truncated)"


def _parse_text_call(content, tools):
    """A tool call written as JSON in plain text (text protocol, or a native-mode
    model that ignored the tools API). Returns (name, args) or None."""
    text = re.sub(r"^```(?:json)?|```$", "", (content or "").strip(), flags=re.M).strip()
    if not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool") or obj.get("name")
    if name not in tools:
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            return None
    return name, args


def _parse_args(raw):
    """Native tool-call arguments: a JSON string (sometimes wrapped in prose)."""
    if isinstance(raw, dict):
        return raw
    raw = (raw or "").strip() or "{}"
    try:
        return json.loads(raw)
    except ValueError:
        a, b = raw.find("{"), raw.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(raw[a:b + 1])
            except ValueError:
                pass
    return None


def _trim(msgs):
    """Keep the conversation under AGENT_MAX_CONTEXT_CHARS by blanking the oldest
    tool results (the latest two are kept intact)."""
    def size():
        return sum(len(json.dumps(m.get("content"), ensure_ascii=False)) for m in msgs)
    results = [m for m in msgs if m["role"] == "tool"
               or (m["role"] == "user" and str(m.get("content", "")).startswith("Result of "))]
    for m in results[:-2]:
        if size() <= config.AGENT_MAX_CONTEXT_CHARS:
            break
        m["content"] = ("Result of (earlier tool): removed to save space" if m["role"] == "user"
                        else "(earlier result removed to save space)")


def _prompt_text(messages):
    for m in reversed(messages):
        if m["role"] == "user":
            c = m["content"]
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return str(c)[:500]
    return ""


def run(messages, ctx=None, protocol=None, on_step=None, on_progress=None, on_token=None):
    """Run the agent loop on an OpenAI-style message list (system prompt first).
    Returns {"reply": str, "steps": [{"tool", "arguments", "result"}], "protocol"}.
    on_progress(text) is called before each tool runs (for live status in chat);
    on_step(step) after; on_token(text_so_far) streams the answer as it's written
    (AGENT_STREAM). Every run is written to the agent_runs audit log."""
    t0 = time.time()
    ctx = ctx or Context()
    out = {"reply": "", "steps": [], "protocol": protocol or config.AGENT_PROTOCOL}
    error = None
    try:
        _loop(messages, ctx, out, on_step, on_progress, on_token)
        return out
    except Exception as e:
        error = str(e)[:300]
        raise
    finally:
        try:
            db.log_agent_run(
                ctx.user["id"] if ctx.user else None, ctx.room["name"] if ctx.room else None,
                _prompt_text(messages), out["protocol"],
                json.dumps([{"tool": s["tool"], "arguments": s["arguments"],
                             "ok": not (isinstance(s["result"], dict) and "error" in s["result"])}
                            for s in out["steps"]], ensure_ascii=False, default=str),
                out["reply"][:500], error, round(time.time() - t0, 1))
        except Exception:
            pass  # the audit log must never break a reply


def _stream_filter(on_token):
    """Forward streamed text unless it looks like a JSON tool call (text protocol)."""
    state = {"text": "", "mode": None}

    def delta(d):
        state["text"] += d
        if state["mode"] is None:
            head = state["text"].lstrip()
            if not head:
                return
            state["mode"] = "hide" if head[0] in "{`" else "show"
        if state["mode"] == "show":
            on_token(state["text"])
    return delta


def _chat(msgs, timeout, tools, on_token):
    if on_token and config.AGENT_STREAM:
        return llm.chat_stream(msgs, temperature=0.2, timeout=timeout, tools=tools,
                               on_delta=_stream_filter(on_token))
    return llm.chat(msgs, temperature=0.2, timeout=timeout, tools=tools)


def _loop(messages, ctx, out, on_step, on_progress, on_token=None):
    tools = available_tools(ctx.user)
    steps = out["steps"]
    msgs = [dict(m) for m in messages]
    system = GUIDE
    if ctx.room:
        notes = db.list_memory(ctx.room["name"], limit=config.AGENT_MEMORY_IN_PROMPT)
        if notes:
            system += ("\n\nNotes saved in this room's memory (newest first):\n"
                       + "\n".join(f"- [{n['id']}] {n['text']}" for n in notes))
    try:  # in-context learning from 👍 / corrected answers (learning.py)
        import learning
        system += learning.prompt_block(_prompt_text(messages))
    except Exception:
        pass
    if msgs and msgs[0]["role"] == "system":
        msgs[0]["content"] += "\n\n" + system
    else:
        msgs.insert(0, {"role": "system", "content": system})
    timeout = (3, config.AGENT_TIMEOUT_SEC)
    cache, repeats = {}, 0

    def text_mode():
        listing = "\n".join(f"- {n}: {t['description']} params={json.dumps(t['parameters']['properties'])}"
                            for n, t in tools.items())
        msgs[0]["content"] += TEXT_PROTOCOL.format(tools=listing)

    def call(name, args):
        """Run a tool once per (name, args); repeats get the cached result back."""
        nonlocal repeats
        key = (name, json.dumps(args, sort_keys=True, default=str))
        if key in cache:
            repeats += 1
            result = {"note": "You already called this with the same arguments. Use this "
                              "result, try something else, or answer.", "result": cache[key]}
        else:
            if on_progress:
                on_progress(f"🔧 {name}…")
            t = tools.get(name)
            if t and t["confirm"] and ctx.confirm and config.AGENT_CONFIRM_ACTIONS \
                    and isinstance(args, dict):
                result = cache[key] = request_approval(ctx, name, args)
            else:
                result = cache[key] = run_tool(ctx, name, args, tools)
        steps.append({"tool": name, "arguments": args, "result": result})
        if on_step:
            on_step(steps[-1])
        return result

    if out["protocol"] == "text":
        text_mode()

    for n in range(config.AGENT_MAX_STEPS):
        if repeats >= 2:  # the model is going in circles
            break
        _trim(msgs)
        if out["protocol"] == "text":
            msg = _chat(msgs, timeout, None, on_token)
        else:
            try:
                msg = _chat(msgs, timeout, tool_specs(tools), on_token)
            except llm.ToolsUnsupported:
                out["protocol"] = "text"
                text_mode()
                continue

        calls = msg.get("tool_calls") or []
        if calls:  # native tool calls
            for i, c in enumerate(calls):
                c.setdefault("id", f"call_{n}_{i}")
                c.setdefault("type", "function")
            msgs.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                fn = c.get("function", {})
                result = call(fn.get("name"), _parse_args(fn.get("arguments")))
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": _dump(result)})
            continue

        parsed = _parse_text_call(msg.get("content"), tools)
        if parsed:  # text-protocol call
            name, args = parsed
            result = call(name, args)
            msgs.append({"role": "assistant", "content": msg.get("content")})
            msgs.append({"role": "user", "content": f"Result of {name}: {_dump(result)}"})
            continue

        out["reply"] = (msg.get("content") or "").strip()
        return

    # Out of steps (or looping): ask for a final answer with what it has.
    _trim(msgs)
    msgs.append({"role": "user", "content": "Stop using tools now and give your best final answer."})
    final = _chat(msgs, timeout, None, on_token)
    out["reply"] = (final.get("content") or "").strip()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Ask the TowerAI agent a question (uses the local LLM).")
    ap.add_argument("prompt")
    ap.add_argument("--room", default="general")
    ap.add_argument("--protocol", choices=("native", "text"))
    ap.add_argument("--list-tools", action="store_true")
    args = ap.parse_args()
    db.init_db()
    if args.list_tools:
        for n, t in available_tools().items():
            print(f"{n}{' (writes)' if t['writes'] else ''}: {t['description']}")
        return
    t0 = time.time()
    out = run([{"role": "user", "content": args.prompt}], Context(room=db.get_room(args.room)),
              protocol=args.protocol,
              on_step=lambda s: print(f"🔧 {s['tool']}({json.dumps(s['arguments'], ensure_ascii=False)})"))
    print(f"\n{out['reply']}\n\n[{len(out['steps'])} tool calls, {out['protocol']} protocol, {time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
