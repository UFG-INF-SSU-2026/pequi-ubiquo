"""Agent evaluation: run fixed questions against the REAL local LLM and score
which tools it chose and what it answered — to compare models, prompts and
AGENT_* settings with numbers instead of impressions.

    python evals/run.py                      # all cases, configured model
    python evals/run.py --model qwen2.5-7b-instruct --protocol text --cases budget,arithmetic

Each case runs on a fresh, seeded temporary database (a budget meeting with
action items, a network scan, a saved note), so results don't touch real data.
A case passes when the agent used at least one expected tool (or none, if none
are expected) and the reply contains one of the expected strings. Reports go to
evals/results/<timestamp>.json.
"""
import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


def seed():
    import db
    from werkzeug.security import generate_password_hash
    uid = db.create_user("evaluser", generate_password_hash("x"))
    mid = db.start_meeting("general", "Q3 budget review", uid)
    for spk, text in [("ana", "Let's review the budget for the third quarter."),
                      ("bruno", "I propose 3600 for the new equipment."),
                      ("ana", "Agreed, the budget of 3600 is approved."),
                      ("bruno", "I will prepare the slides for Friday.")]:
        db.add_segment(mid, spk, text)
    db.stop_meeting(mid)
    db.save_meeting_notes(mid, json.dumps({
        "summary": "The Q3 budget of 3600 for new equipment was approved.",
        "decisions": ["Budget of 3600 approved"],
        "action_items": [{"task": "Prepare the slides", "owner": "bruno", "due": "Friday", "done": False},
                         {"task": "Order the equipment", "owner": "ana", "due": "", "done": True}]}))
    db.save_netscan(time.time(), json.dumps({"ts": time.time(), "device_count": 3, "devices": [
        {"ip": "192.168.0.1", "vendor": "TP-Link", "rtt_ms": 2},
        {"ip": "192.168.0.10", "vendor": "Espressif", "rtt_ms": 15, "name": "hall-mic"},
        {"ip": "192.168.0.20", "vendor": "Apple", "rtt_ms": 6}]}))
    db.add_memory("general", "Standups are at 9:15", "ana")
    return uid


def run_case(case, protocol=None):
    import agent
    import chat
    import db
    user = db.get_user_by_name("evaluser")
    room = db.get_room("general")
    msgs = [{"role": "system", "content": chat.SYSTEM_PROMPT},
            {"role": "user", "content": f"evaluser: {case['prompt']}"}]
    ctx = agent.Context(user=dict(user) | {"is_admin": 1}, room=room, confirm=True)
    t0 = time.time()
    try:
        out = agent.run(msgs, ctx, protocol=protocol)
        err = None
    except Exception as e:
        out, err = {"reply": "", "steps": [], "protocol": protocol}, str(e)
    secs = round(time.time() - t0, 1)
    used = [s["tool"] for s in out["steps"]]
    exp_t = case.get("expect_tools", [])
    tools_ok = (not used) if not exp_t else any(t in used for t in exp_t)
    exp_r = [x.lower() for x in case.get("expect_reply", [])]
    reply_ok = not exp_r or any(x in out["reply"].lower() for x in exp_r)
    return {"id": case["id"], "ok": bool(tools_ok and reply_ok and not err), "tools_ok": tools_ok,
            "reply_ok": reply_ok, "tools": used, "reply": out["reply"][:300], "secs": secs,
            "protocol": out["protocol"], "error": err}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", help="model id (default: LLM_MODEL)")
    ap.add_argument("--protocol", choices=("native", "text"))
    ap.add_argument("--cases", help="comma-separated case ids")
    ap.add_argument("--file", default=os.path.join(HERE, "cases.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args(argv)

    os.environ["TOWERAI_DB"] = os.path.join(tempfile.mkdtemp(prefix="towerai-eval-"), "eval.db")
    os.environ["SCHEDULER_ENABLED"] = "0"
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    import config
    import db
    config.DB_PATH = os.environ["TOWERAI_DB"]
    config.SCHEDULER_ENABLED = False
    if args.model:
        config.LLM_MODEL = args.model

    cases = json.load(open(args.file, encoding="utf-8"))
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    results = []
    for case in cases:
        db.init_db()
        with db.get_db() as c:  # fresh data per case
            for t in ("messages", "transcript_segments", "agent_pending", "schedules", "agent_memory",
                      "netscans", "meetings", "users"):  # children before parents (foreign keys)
                c.execute(f"DELETE FROM {t}")
        seed()
        r = run_case(case, args.protocol)
        results.append(r)
        print(f"{'✓' if r['ok'] else '✗'} {r['id']:<32} {r['secs']:>6}s  tools={','.join(r['tools']) or '-'}"
              + (f"  error={r['error']}" if r["error"] else ""))
    passed = sum(r["ok"] for r in results)
    summary = {"model": config.LLM_MODEL, "protocol": args.protocol or config.AGENT_PROTOCOL,
               "passed": passed, "total": len(results),
               "tool_accuracy": round(sum(r["tools_ok"] for r in results) / max(1, len(results)), 2),
               "avg_secs": round(sum(r["secs"] for r in results) / max(1, len(results)), 1),
               "results": results}
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n{passed}/{len(results)} passed · tool choice {summary['tool_accuracy']:.0%} · "
          f"avg {summary['avg_secs']}s · report: {path}")
    return summary


if __name__ == "__main__":
    s = main()
    sys.exit(0 if s["passed"] == s["total"] else 1)
