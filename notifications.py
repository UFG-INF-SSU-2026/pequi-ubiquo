"""Notifications: @mentions, action items assigned to you, scheduled-task runs;
plus unread message counts per room. The browser polls /api/notifications
(notify.js) and shows a bell count, sidebar badges and desktop notifications."""
import re

import config
import db

MENTION_RE = re.compile(r"@([A-Za-z0-9_-]{1,32})")


def notify(user_id, kind, text, link=None):
    import time
    with db.get_db() as c:
        c.execute("INSERT INTO notifications (user_id, kind, text, link, created_at) VALUES (?, ?, ?, ?, ?)",
                  (user_id, kind, text[:300], link, time.time()))


def on_message(room_name, author, content):
    """Notify people @mentioned in a new message (not the author, not the assistant)."""
    names = {m.lower() for m in MENTION_RE.findall(content or "")}
    names.discard((author or "").lower())
    names.discard(config.ASSISTANT_NAME.lower())
    names.discard("tower")
    for name in names:
        u = db.get_user_by_name(name)
        if u is None:
            with db.get_db() as c:
                u = c.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (name,)).fetchone()
        if u:
            notify(u["id"], "mention", f"{author} mentioned you in #{room_name}: {content[:120]}",
                   f"/rooms/{room_name}")


def on_action_items(meeting, items):
    """Tell people about action items assigned to them by name."""
    for it in items:
        owner = (it.get("owner") or "").strip()
        if not owner:
            continue
        with db.get_db() as c:
            u = c.execute("SELECT id FROM users WHERE username = ? COLLATE NOCASE", (owner,)).fetchone()
        if u:
            notify(u["id"], "action_item", f"Action item for you from “{meeting['title']}”: {it['task']}",
                   f"/meetings/{meeting['id']}#actions")


def unread_counts(user_id):
    with db.get_db() as c:
        rows = c.execute(
            "SELECT r.name, (SELECT COUNT(*) FROM messages m WHERE m.room_id = r.id "
            "AND m.id > COALESCE((SELECT last_read_id FROM room_reads WHERE user_id = ? "
            "AND room_id = r.id), 0) AND (m.user_id IS NULL OR m.user_id != ?)) AS n FROM rooms r",
            (user_id, user_id)).fetchall()
    return {r["name"]: r["n"] for r in rows if r["n"]}


def mark_read(user_id, room_id, last_id):
    with db.get_db() as c:
        c.execute("INSERT INTO room_reads (user_id, room_id, last_read_id) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id, room_id) DO UPDATE SET last_read_id = "
                  "MAX(last_read_id, excluded.last_read_id)", (user_id, room_id, int(last_id)))


def recent(user_id, since_id=0, limit=30):
    with db.get_db() as c:
        return c.execute("SELECT * FROM notifications WHERE user_id = ? AND id > ? "
                         "ORDER BY id DESC LIMIT ?", (user_id, since_id, limit)).fetchall()


def unread_count(user_id):
    with db.get_db() as c:
        return c.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
                         (user_id,)).fetchone()[0]


def mark_all_read(user_id):
    with db.get_db() as c:
        c.execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (user_id,))
