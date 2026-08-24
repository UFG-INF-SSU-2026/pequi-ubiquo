"""Network map (Phase 3) — admin tool to discover devices on the local segment,
collect link telemetry, and get LLM-assisted, advisory recommendations.

Scanning is a legitimate self-administration function of the appliance's OWN
LAN, behind login. It scans only the host's derived private /24 (see netmap.py).
The LLM only *advises* — it never executes anything.
"""
import json

from flask import Blueprint, render_template, jsonify, request

import db
import llm
import netmap
from auth import login_required

bp = Blueprint("netmap", __name__)


@bp.route("/netmap")
@login_required
def page():
    row = db.last_netscan()
    scan = json.loads(row["data"]) if row else None
    return render_template("netmap.html", scan=scan)


@bp.route("/netmap/scan", methods=["POST"])
@login_required
def do_scan():
    prev = db.last_netscan()
    result = netmap.scan(do_mdns=True)
    db.save_netscan(result["ts"], json.dumps(result))
    # Include the diff vs the previous scan so the UI shows what changed.
    prev_data = json.loads(prev["data"]) if prev else None
    result["diff"] = netmap.diff_scans(prev_data, result)
    result["previous_ts"] = prev["ts"] if prev else None
    return jsonify(result)


@bp.route("/netmap/last")
@login_required
def last():
    row = db.last_netscan()
    return jsonify(json.loads(row["data"]) if row else {})


@bp.route("/netmap/history")
@login_required
def history():
    out = []
    for r in db.recent_netscans(50):
        try:
            dc = json.loads(r["data"]).get("device_count")
        except Exception:
            dc = None
        out.append({"id": r["id"], "ts": r["ts"], "device_count": dc})
    return jsonify(out)


@bp.route("/netmap/diff")
@login_required
def diff():
    """Diff two scans. Defaults to latest (b) vs previous (a)."""
    rows = db.recent_netscans(50)
    if not rows:
        return jsonify(error="no scans yet"), 400
    by_id = {r["id"]: json.loads(r["data"]) for r in rows}
    ids = [r["id"] for r in rows]  # newest first
    b_id = request.args.get("b", type=int)
    a_id = request.args.get("a", type=int)
    b = by_id.get(b_id) if b_id else by_id[ids[0]]
    a = by_id.get(a_id) if a_id else (by_id[ids[1]] if len(ids) > 1 else None)
    if b is None:
        return jsonify(error="scan not found"), 404
    return jsonify(diff=netmap.diff_scans(a, b), a_present=a is not None)


def _prompt(d):
    lines = []
    h = d["host"]
    lines.append(f"Host {h['hostname']} at {h['ip']} on {h['subnet']}; gateway {d.get('gateway')}.")
    w = d.get("wifi")
    if w:
        lines.append(f"Host Wi-Fi: SSID={w.get('ssid')} signal={w.get('signal')} "
                     f"channel={w.get('channel')} radio={w.get('radio')} "
                     f"rx={w.get('rx_rate')} tx={w.get('tx_rate')}.")
    else:
        lines.append("Host is wired (no Wi-Fi telemetry).")
    lines.append(f"{d['device_count']} devices discovered:")
    for dev in d["devices"]:
        lines.append(f"- {dev['ip']} mac={dev['mac'] or '?'} vendor={dev['vendor'] or '?'} "
                     f"rtt={dev['rtt_ms']}ms ports={dev['ports']} roles={dev['roles']} names={dev['names']}")
    lines.append(
        "Provide a brief, bulleted assessment: (1) weak/high-latency links, "
        "(2) Wi-Fi channel/interference notes if applicable, (3) unidentified or "
        "unexpected devices, (4) open-port security flags, (5) practical coverage / "
        "AP-placement or wiring suggestions for a small offline LAN. Do not suggest "
        "running commands; recommendations only."
    )
    return "\n".join(lines)


@bp.route("/netmap/analyze", methods=["POST"])
@login_required
def analyze():
    row = db.last_netscan()
    if not row:
        return jsonify(error="run a scan first"), 400
    data = json.loads(row["data"])
    try:
        text = llm.chat_completion([
            {"role": "system", "content":
                "You are a network engineering assistant for a small offline LAN "
                "appliance. Give concise, practical, non-destructive advice."},
            {"role": "user", "content": _prompt(data)},
        ])
    except Exception as e:
        text = f"[AI analysis unavailable: {e}]"
    return jsonify(analysis=text)


def register(app):
    app.register_blueprint(bp)
