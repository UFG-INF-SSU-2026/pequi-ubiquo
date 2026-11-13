"""Scheduled assistant tasks: "every Friday at 17:00, summarize this week's
meetings into #general".

Each schedule stores a prompt, a room and when to run (daily at HH:MM, weekly on
a weekday at HH:MM, or every N minutes). A background thread checks every
SCHEDULER_TICK_SEC; a due task runs the agent as the person who created it and
posts the answer in the room ("⏰ …"). Actions that need approval wait for it in
the room as usual. Claiming a run is an atomic UPDATE, so several worker
processes never run the same task twice.
"""
import threading
import time
from datetime import datetime, timedelta

import config
import db

KINDS = ("daily", "weekly", "interval")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
TICK_SEC = 20
_started = False


def next_run(kind, at_time=None, weekday=None, every_min=None, now=None):
    now = now or time.time()
    if kind == "interval":
        return now + max(5, int(every_min)) * 60
    hh, mm = (int(x) for x in at_time.split(":"))
    d = datetime.fromtimestamp(now).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "daily":
        if d.timestamp() <= now:
            d += timedelta(days=1)
    else:
        d += timedelta(days=(int(weekday) - d.weekday()) % 7)
        if d.timestamp() <= now:
            d += timedelta(days=7)
    return d.timestamp()


def describe(s):
    if s["kind"] == "interval":
        return f"every {s['every_min']} min"
    if s["kind"] == "daily":
        return f"daily at {s['at_time']}"
    return f"every {WEEKDAYS[s['weekday']]} at {s['at_time']}"


def create(room, prompt, kind, at_time=None, weekday=None, every_min=None, user_id=None):
    """Validate and store a schedule. Returns its id; raises ValueError."""
    prompt = (prompt or "").strip()[:2000]
    if not prompt:
        raise ValueError("the task needs a prompt")
    if not db.get_room(room):
        raise ValueError(f"no such room: {room}")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if kind == "interval":
        every_min = int(every_min or 0)
        if every_min < 5:
            raise ValueError("interval must be at least 5 minutes")
        at_time = weekday = None
    else:
        try:
            hh, mm = (int(x) for x in str(at_time).split(":"))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            raise ValueError("time must be HH:MM")
        at_time = f"{hh:02d}:{mm:02d}"
        if kind == "weekly":
            weekday = int(weekday)
            if not 0 <= weekday < 7:
                raise ValueError("weekday must be 0 (Monday) to 6 (Sunday)")
        else:
            weekday = None
        every_min = None
    with db.get_db() as c:
        return c.execute(
            "INSERT INTO schedules (room, prompt, kind, at_time, weekday, every_min, created_by, next_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (room, prompt, kind, at_time, weekday, every_min, user_id,
             next_run(kind, at_time, weekday, every_min))).lastrowid


def list_all():
    with db.get_db() as c:
        return c.execute("SELECT s.*, u.username FROM schedules s LEFT JOIN users u "
                         "ON u.id = s.created_by ORDER BY s.next_run").fetchall()


def get(sid):
    with db.get_db() as c:
        return c.execute("SELECT * FROM schedules WHERE id = ?", (sid,)).fetchone()


def delete(sid):
    with db.get_db() as c:
        return c.execute("DELETE FROM schedules WHERE id = ?", (sid,)).rowcount


def run_task(s):
    """Run one schedule now; posts the answer in its room. Returns the reply."""
    import chat
    import notifications
    room = db.get_room(s["room"])
    user = db.get_user(s["created_by"]) if s["created_by"] else None
    if not room:
        return "room no longer exists"
    reply = chat.assistant_reply(room, user, prompt=f"(Scheduled task, {describe(s)}) {s['prompt']}")
    chat.deliver(room, None, "assistant", config.ASSISTANT_NAME, f"⏰ {s['prompt'][:80]}\n\n{reply}")
    with db.get_db() as c:
        c.execute("UPDATE schedules SET last_run = ?, last_result = ? WHERE id = ?",
                  (time.time(), reply[:500], s["id"]))
    if user:
        notifications.notify(user["id"], "schedule", f"Scheduled task ran in #{room['name']}: "
                             f"{s['prompt'][:60]}", f"/rooms/{room['name']}")
    return reply


def run_due(now=None):
    """Run every enabled schedule whose time has come. Returns how many ran."""
    now = now or time.time()
    with db.get_db() as c:
        due = c.execute("SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ?", (now,)).fetchall()
    ran = 0
    for s in due:
        nxt = next_run(s["kind"], s["at_time"], s["weekday"], s["every_min"], now)
        with db.get_db() as c:  # claim it (another worker may have)
            claimed = c.execute("UPDATE schedules SET next_run = ? WHERE id = ? AND next_run = ?",
                                (nxt, s["id"], s["next_run"])).rowcount
        if claimed:
            try:
                run_task(s)
            except Exception as e:
                with db.get_db() as c:
                    c.execute("UPDATE schedules SET last_run = ?, last_result = ? WHERE id = ?",
                              (now, f"error: {e}"[:500], s["id"]))
            ran += 1
    return ran


def start():
    global _started
    if _started or not config.SCHEDULER_ENABLED:
        return
    _started = True

    def loop():
        while True:
            time.sleep(TICK_SEC)
            try:
                run_due()
            except Exception:
                pass
            try:
                import backup
                backup.maybe_auto()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="scheduler").start()
