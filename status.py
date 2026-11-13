"""System status page + model check (replaces the old check_models.py).

GET  /status                 admin page: LLM server, speech models, features,
                             background jobs, storage, network — plus advice.
POST /status/model-check     run the model check in the background.
GET  /api/status             the same data as JSON.
CLI: python status.py        print the status and run the model check
                             (exit code 1 if the LLM server is unreachable).

The model check sends three tiny requests to the local LLM: a plain reply
(is it up, how fast), a tool call (can the agent use native tool calling) and a
small generated image (can 📷 vision work). Results are kept in memory.
"""
import importlib.util
import os
import platform
import shutil
import socket
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

from flask import Blueprint, current_app, jsonify, redirect, render_template, url_for

import config
import db
import llm
from auth import login_required

bp = Blueprint("status", __name__)

_STARTED = time.time()
_check = {"running": False, "result": None}
_check_lock = threading.Lock()


# --- Model check -------------------------------------------------------------
def _png(rgb=(220, 30, 30), size=32):
    """A solid-colour PNG, built by hand (no image library needed)."""
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _timed(fn):
    t0 = time.time()
    try:
        return fn(), None, round(time.time() - t0, 1)
    except Exception as e:
        return None, str(e)[:300], round(time.time() - t0, 1)


_TIME_TOOL = [{"type": "function", "function": {
    "name": "get_time", "description": "Get the current time.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}]


def run_model_check():
    """-> dict with server, chat, tools and vision results (each ok/err/secs)."""
    import base64
    ok, base, models = llm.health()
    out = {"at": time.time(), "server": {"ok": ok, "base": base, "models": models}}
    if not ok:
        return out
    timeout = (3, 90)

    msg, err, secs = _timed(lambda: llm.chat(
        [{"role": "user", "content": "Reply with exactly the word OK."}], temperature=0, timeout=timeout))
    reply = (msg or {}).get("content") or ""
    out["chat"] = {"ok": bool(msg) and bool(reply.strip()), "error": err, "secs": secs,
                   "model": (msg or {}).get("_model"), "reply": reply.strip()[:80]}
    if not msg:
        return out

    def tools():
        try:
            m = llm.chat([{"role": "user", "content": "What time is it? Use the get_time tool."}],
                         temperature=0, timeout=timeout, tools=_TIME_TOOL)
        except llm.ToolsUnsupported as e:
            return {"mode": "rejected", "detail": str(e)[:200]}
        if m.get("tool_calls"):
            return {"mode": "native"}
        return {"mode": "text", "detail": (m.get("content") or "")[:120]}
    res, err, secs = _timed(tools)
    out["tools"] = {"ok": bool(res) and res["mode"] == "native", "error": err, "secs": secs, **(res or {})}

    data_url = "data:image/png;base64," + base64.b64encode(_png()).decode()
    msg, err, secs = _timed(lambda: llm.chat([{"role": "user", "content": [
        {"type": "text", "text": "What is the main colour of this image? Answer with one word."},
        {"type": "image_url", "image_url": {"url": data_url}}]}],
        temperature=0, timeout=timeout, model=config.VISION_MODEL or None))
    reply = ((msg or {}).get("content") or "").strip()
    out["vision"] = {"ok": any(w in reply.lower() for w in ("red", "vermelh", "rojo", "rouge")),
                     "error": err, "secs": secs, "reply": reply[:80],
                     "model": (msg or {}).get("_model")}
    return out


def start_model_check():
    with _check_lock:
        if _check["running"]:
            return False
        _check["running"] = True

    def target():
        try:
            _check["result"] = run_model_check()
        except Exception as e:
            _check["result"] = {"at": time.time(), "error": str(e)[:300]}
        finally:
            _check["running"] = False
    threading.Thread(target=target, daemon=True).start()
    return True


# --- Component status ------------------------------------------------------
def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _speech():
    import stt
    installed = importlib.util.find_spec("faster_whisper") is not None
    model = stt.resolve_model()
    out = {"installed": installed, "model": config.WHISPER_MODEL,
           "local_copy": os.path.isdir(model), "loaded": stt._whisper is not None,
           "engine": stt.engine_desc() if stt._whisper is not None else None}
    try:
        import diarize
        out["diarization"] = diarize.available()
    except Exception:
        out["diarization"] = False
    return out


def _jobs():
    with db.get_db() as c:
        rows = c.execute("SELECT status, COUNT(*) AS n FROM meetings WHERE status IS NOT NULL "
                         "GROUP BY status").fetchall()
        recent_errors = c.execute(
            "SELECT id, title, status_detail FROM meetings WHERE status = 'error' "
            "ORDER BY id DESC LIMIT 5").fetchall()
    return {"counts": {r["status"]: r["n"] for r in rows},
            "errors": [dict(r) for r in recent_errors]}


def _storage():
    base = Path(config.BASE_DIR)
    disk = shutil.disk_usage(base)
    paths = {"database": config.DB_PATH, "models": str(base / "models"),
             "recordings": config.RECORDINGS_DIR, "images": config.IMAGES_DIR}
    sizes = {}
    for name, p in paths.items():
        if os.path.isfile(p):
            sizes[name] = os.path.getsize(p)
        elif os.path.isdir(p):
            sizes[name] = _dir_size(p)
        else:
            sizes[name] = 0
    return {"free": disk.free, "total": disk.total, "sizes": sizes}


def _network():
    try:
        import discovery
        ip = discovery._local_ip()
    except Exception:
        ip = None
    try:
        import pubsub
        redis = "connected" if pubsub._redis is not None else ("configured, not connected"
                                                              if config.REDIS_URL else "off")
    except Exception:
        redis = "off"
    scheme = "https" if config.USE_TLS else "http"
    return {"hostname": socket.gethostname(), "ip": ip,
            "urls": [f"{scheme}://{config.MDNS_HOSTNAME}.local:{config.PORT}"]
                    + ([f"{scheme}://{ip}:{config.PORT}"] if ip else []),
            "tls": config.USE_TLS, "redis": redis}


def _agent_runs(limit=15):
    import json
    runs = []
    for r in db.recent_agent_runs(limit):
        steps = json.loads(r["steps"] or "[]")
        runs.append({"at": r["ts"], "user": r["username"], "room": r["room"], "prompt": r["prompt"],
                     "tools": [s["tool"] + ("" if s.get("ok") else " ✗") for s in steps],
                     "secs": r["secs"], "error": r["error"], "protocol": r["protocol"]})
    return runs


def collect(app=None):
    app = app or current_app
    with db.get_db() as c:
        counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("users", "rooms", "messages", "meetings", "devices")}
    return {
        "system": {"python": sys.version.split()[0], "platform": platform.platform(terse=True),
                   "cpus": os.cpu_count(), "uptime_s": int(time.time() - _STARTED)},
        "llm": {"base_url": config.LLM_BASE_URL, "model": config.LLM_MODEL,
                "vision_model": config.VISION_MODEL or None},
        "agent": {"enabled": config.AGENT_ENABLED, "protocol": config.AGENT_PROTOCOL,
                  "writes": config.AGENT_ALLOW_WRITES, "max_steps": config.AGENT_MAX_STEPS},
        "speech": _speech(),
        "features": {"loaded": sorted(app.config.get("LOADED_FEATURES", ())),
                     "failed": app.config.get("FAILED_FEATURES", {})},
        "jobs": _jobs(),
        "storage": _storage(),
        "network": _network(),
        "counts": counts,
        "model_check": {"running": _check["running"], "result": _check["result"]},
        "agent_runs": _agent_runs(),
    }


def advice(s):
    """Plain-language problems and how to fix them."""
    tips = []
    feats = s["features"]
    mc = s["model_check"]["result"]
    if mc and not mc.get("server", {}).get("ok", True):
        tips.append(("error", f"No local AI server answers at {s['llm']['base_url']}. Start LM Studio "
                              "(Developer → Start Server) or Ollama, or fix LLM_BASE_URL."))
    if mc and mc.get("server", {}).get("ok"):
        if mc.get("chat") and not mc["chat"]["ok"]:
            tips.append(("error", "The AI server is up but the model didn't answer — load a model in "
                                  "LM Studio / Ollama."))
        tools = mc.get("tools")
        if tools and tools.get("mode") in ("rejected", "text") and s["agent"]["enabled"] \
                and s["agent"]["protocol"] == "native":
            tips.append(("warn", "This model doesn't do native tool calling. The agent falls back "
                                 "automatically; set AGENT_PROTOCOL=text to skip the failed attempt, "
                                 "or load a tool-capable model (Qwen 2.5/3, Llama 3.1+, Mistral)."))
        vis = mc.get("vision")
        if vis and not vis["ok"] and "vision" in feats["loaded"]:
            tips.append(("warn", "The model can't read images, so 📷 in chat won't work. Load a "
                                 "vision model (Qwen2.5-VL, Gemma 3, LLaVA) or set VISION_MODEL."))
    if not mc:
        tips.append(("info", "Run the model check to test the AI model (answers, tools, images)."))
    sp = s["speech"]
    if not sp["installed"]:
        tips.append(("error", "faster-whisper isn't installed: `pip install -r requirements.txt`."))
    elif not sp["local_copy"] and not sp["loaded"]:
        tips.append(("warn", f"No local copy of the Whisper '{sp['model']}' model — it will try to "
                             "download at startup. Run `python demo_setup.py --download-model` "
                             "while online."))
    if not sp["diarization"]:
        tips.append(("info", "Speaker labels for uploads are off — `python demo_setup.py "
                             "--download-model` fetches the models (~33 MB)."))
    for name, err in feats["failed"].items():
        tips.append(("warn", f"Feature '{name}' didn't load: {err}"))
    if s["storage"]["free"] < 2 * 1024 ** 3:
        tips.append(("warn", "Less than 2 GB of disk left — recordings and models may fail to save."))
    failed = [r for r in s["agent_runs"] if r["error"]]
    if len(failed) >= 3:
        tips.append(("warn", f"{len(failed)} of the last {len(s['agent_runs'])} assistant requests "
                             "failed — see Assistant activity below."))
    if s["jobs"]["errors"]:
        tips.append(("info", f"{len(s['jobs']['errors'])} recent meeting job(s) failed — see below."))
    if not s["network"]["tls"]:
        tips.append(("info", "HTTPS is off: microphones (captions, 🎤) only work on the host itself. "
                             "Set USE_TLS=1 for other devices."))
    return tips


# --- Routes ----------------------------------------------------------------
def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@bp.route("/status")
@login_required
def page():
    s = collect()
    return render_template("status.html", s=s, tips=advice(s), human=_human)


@bp.route("/status/model-check", methods=["POST"])
@login_required
def model_check():
    start_model_check()
    return redirect(url_for("status.page"))


@bp.route("/api/status")
@login_required
def api():
    s = collect()
    s["advice"] = [{"level": lvl, "text": t} for lvl, t in advice(s)]
    return jsonify(s)


def register(app):
    app.register_blueprint(bp)


# --- CLI -------------------------------------------------------------------
def main():
    import json
    print(f"Checking the local AI server at {config.LLM_BASE_URL} …")
    res = run_model_check()
    srv = res["server"]
    if not srv["ok"]:
        print("✗ No LLM server answered (tried LLM_BASE_URL and localhost:1234).")
        sys.exit(1)
    print(f"✓ Server {srv['base']} — models: {', '.join(srv['models']) or '(none listed)'}")
    for key, label in (("chat", "Answers"), ("tools", "Tool calling"), ("vision", "Reads images")):
        r = res.get(key)
        if not r:
            continue
        extra = r.get("error") or r.get("mode") or r.get("reply") or ""
        print(f"{'✓' if r['ok'] else '✗'} {label:<13} {r['secs']:>5}s  {extra}")
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
