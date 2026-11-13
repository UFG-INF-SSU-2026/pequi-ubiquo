"""SQLite persistence: users, rooms, messages.

A fresh connection is opened per call so the app is safe under the threaded
WSGI server (waitress). Replaces the old in-memory `session_histories` dict,
which couldn't persist or be shared between users.
"""
import sqlite3
import time
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL REFERENCES rooms(id),
    user_id    INTEGER REFERENCES users(id),   -- NULL => the assistant
    role       TEXT NOT NULL,                   -- 'user' | 'assistant'
    username   TEXT NOT NULL,                   -- display name (cached)
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);
CREATE TABLE IF NOT EXISTS devices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    api_key_hash TEXT UNIQUE NOT NULL,
    room         TEXT,                          -- default room to post captions into
    created_at   REAL NOT NULL,
    last_seen    REAL
);
CREATE TABLE IF NOT EXISTS meetings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room       TEXT NOT NULL,
    title      TEXT,
    created_by INTEGER REFERENCES users(id),
    started_at REAL NOT NULL,
    ended_at   REAL
);
CREATE TABLE IF NOT EXISTS transcript_segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    speaker    TEXT,
    text       TEXT NOT NULL,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_meeting ON transcript_segments(meeting_id, id);
CREATE TABLE IF NOT EXISTS agent_runs (      -- audit log of the assistant's tool use
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    user_id   INTEGER,
    room      TEXT,
    prompt    TEXT,
    protocol  TEXT,
    steps     TEXT,                            -- JSON [{tool, arguments, ok}]
    reply     TEXT,
    error     TEXT,
    secs      REAL
);
CREATE TABLE IF NOT EXISTS agent_memory (    -- notes the assistant saved per room
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room       TEXT NOT NULL,
    text       TEXT NOT NULL,
    author     TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_room ON agent_memory(room, id);
CREATE TABLE IF NOT EXISTS settings (        -- small JSON values (tool permissions…)
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_pending (   -- write actions waiting for approval
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room       TEXT NOT NULL,
    user_id    INTEGER,
    tool       TEXT NOT NULL,
    arguments  TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected | failed
    result     TEXT,
    created_at REAL NOT NULL,
    decided_by INTEGER,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS schedules (       -- recurring assistant tasks
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room       TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    kind       TEXT NOT NULL,                  -- daily | weekly | interval
    at_time    TEXT,                           -- HH:MM (daily/weekly)
    weekday    INTEGER,                        -- 0=Monday (weekly)
    every_min  INTEGER,                        -- (interval)
    created_by INTEGER,
    enabled    INTEGER NOT NULL DEFAULT 1,
    next_run   REAL NOT NULL,
    last_run   REAL,
    last_result TEXT
);
CREATE TABLE IF NOT EXISTS voiceprints (     -- remembered voices (speaker embeddings)
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    embedding  BLOB NOT NULL,                  -- float32, L2-normalised running mean
    samples    INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_voices (  -- per-meeting speaker embeddings (for learning names)
    meeting_id INTEGER NOT NULL,
    label      TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    PRIMARY KEY (meeting_id, label)
);
CREATE TABLE IF NOT EXISTS vocabulary (      -- terms Whisper should spell right
    term       TEXT PRIMARY KEY COLLATE NOCASE,
    source     TEXT,                           -- manual | correction
    uses       INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (           -- shared files per room
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mime        TEXT,
    text        TEXT,                          -- extracted text (for search / the agent)
    uploaded_by INTEGER,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,                  -- mention | action_item | schedule | confirm
    text       TEXT NOT NULL,
    link       TEXT,
    created_at REAL NOT NULL,
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, read, id);
CREATE TABLE IF NOT EXISTS room_reads (
    user_id     INTEGER NOT NULL,
    room_id     INTEGER NOT NULL,
    last_read_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, room_id)
);
CREATE TABLE IF NOT EXISTS feedback (        -- 👍/👎 + corrections on assistant replies
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    rating     INTEGER NOT NULL,               -- 1 | -1
    correction TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (message_id, user_id)
);
CREATE TABLE IF NOT EXISTS netscans (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   REAL NOT NULL,
    data TEXT NOT NULL
);
"""


@contextmanager
def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release; init_db() adds any that are missing so
# existing towerai.db files upgrade in place.
_MIGRATIONS = {
    "meetings": [
        ("source", "TEXT DEFAULT 'live'"),   # 'live' | 'upload'
        ("status", "TEXT"),                  # NULL | transcribing | diarizing | summarizing | error
        ("status_detail", "TEXT"),
        ("notes", "TEXT"),                   # JSON: {summary, decisions, action_items}
        ("summarized_at", "REAL"),
    ],
    "users": [
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
    ],
    "transcript_segments": [
        ("start_sec", "REAL"),               # offset into an uploaded recording
    ],
}


def _migrate(c):
    for table, cols in _MIGRATIONS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    with get_db() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        # There is always an admin: the oldest account, if none was chosen.
        if not c.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
            c.execute("UPDATE users SET is_admin = 1 WHERE id = (SELECT MIN(id) FROM users)")
        if not c.execute("SELECT 1 FROM rooms WHERE name = ?", ("general",)).fetchone():
            c.execute(
                "INSERT INTO rooms (name, created_at) VALUES (?, ?)",
                ("general", time.time()),
            )


# --- Users -----------------------------------------------------------------
def create_user(username: str, password_hash: str):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO users (username, password_hash, created_at, is_admin) "
            "VALUES (?, ?, ?, NOT EXISTS (SELECT 1 FROM users WHERE is_admin = 1))",
            (username, password_hash, time.time()),
        )
        return cur.lastrowid


def list_users():
    with get_db() as c:
        return c.execute("SELECT id, username, is_admin FROM users ORDER BY username").fetchall()


def get_setting(key, default=None):
    import json
    with get_db() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(key, value):
    import json
    with get_db() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def get_user_by_name(username: str):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def get_user(user_id: int):
    with get_db() as c:
        return c.execute(
            "SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)
        ).fetchone()


# --- Rooms -----------------------------------------------------------------
def list_rooms():
    with get_db() as c:
        return c.execute("SELECT * FROM rooms ORDER BY name").fetchall()


def get_room(name: str):
    with get_db() as c:
        return c.execute("SELECT * FROM rooms WHERE name = ?", (name,)).fetchone()


def create_room(name: str):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO rooms (name, created_at) VALUES (?, ?)", (name, time.time())
        )
        return cur.lastrowid


# --- Messages --------------------------------------------------------------
def add_message(room_id, user_id, role, username, content):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO messages (room_id, user_id, role, username, content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (room_id, user_id, role, username, content, time.time()),
        )
        return cur.lastrowid


def messages_after(room_id, since_id=0, limit=200):
    with get_db() as c:
        return c.execute(
            "SELECT id, role, username, content, created_at FROM messages "
            "WHERE room_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (room_id, since_id, limit),
        ).fetchall()


def recent_messages(room_id, limit=20):
    """Most recent `limit` messages, returned oldest-first (for LLM context)."""
    with get_db() as c:
        rows = c.execute(
            "SELECT role, username, content FROM messages "
            "WHERE room_id = ? ORDER BY id DESC LIMIT ?",
            (room_id, limit),
        ).fetchall()
    return list(reversed(rows))


# --- Devices (IoT API keys) -----------------------------------------------
def add_device(name, api_key_hash, room=None):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO devices (name, api_key_hash, room, created_at) VALUES (?, ?, ?, ?)",
            (name, api_key_hash, room, time.time()),
        )
        return cur.lastrowid


def get_device_by_hash(api_key_hash):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM devices WHERE api_key_hash = ?", (api_key_hash,)
        ).fetchone()


def list_devices():
    with get_db() as c:
        return c.execute("SELECT id, name, room, created_at, last_seen FROM devices ORDER BY name").fetchall()


def delete_device(device_id):
    with get_db() as c:
        c.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def touch_device(device_id, ts):
    with get_db() as c:
        c.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (ts, device_id))


# --- Meetings / stored transcripts ----------------------------------------
def start_meeting(room, title, created_by):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO meetings (room, title, created_by, started_at) VALUES (?, ?, ?, ?)",
            (room, title, created_by, time.time()),
        )
        return cur.lastrowid


def stop_meeting(meeting_id):
    with get_db() as c:
        c.execute("UPDATE meetings SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                  (time.time(), meeting_id))


def active_meeting_for_room(room):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM meetings WHERE room = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (room,),
        ).fetchone()


def add_segment(meeting_id, speaker, text):
    with get_db() as c:
        c.execute(
            "INSERT INTO transcript_segments (meeting_id, speaker, text, ts) VALUES (?, ?, ?, ?)",
            (meeting_id, speaker, text, time.time()),
        )


def list_meetings(limit=100):
    with get_db() as c:
        return c.execute(
            "SELECT m.*, (SELECT COUNT(*) FROM transcript_segments s WHERE s.meeting_id = m.id) "
            "AS segment_count FROM meetings m ORDER BY m.started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_meeting(meeting_id):
    with get_db() as c:
        return c.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()


def meeting_segments(meeting_id):
    with get_db() as c:
        return c.execute(
            "SELECT id, speaker, text, ts, start_sec FROM transcript_segments "
            "WHERE meeting_id = ? ORDER BY id ASC",
            (meeting_id,),
        ).fetchall()


def create_upload_meeting(room, title, created_by):
    """A meeting fed by an uploaded recording. ended_at is set immediately so the
    live-caption recorder (active_meeting_for_room) never appends to it."""
    now = time.time()
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO meetings (room, title, created_by, started_at, ended_at, source, status) "
            "VALUES (?, ?, ?, ?, ?, 'upload', 'queued')",
            (room, title, created_by, now, now),
        )
        return cur.lastrowid


def add_segments(meeting_id, rows):
    """Bulk insert [(speaker, text, ts, start_sec)]."""
    with get_db() as c:
        c.executemany(
            "INSERT INTO transcript_segments (meeting_id, speaker, text, ts, start_sec) "
            "VALUES (?, ?, ?, ?, ?)",
            [(meeting_id, *r) for r in rows],
        )


def set_meeting_status(meeting_id, status, detail=None):
    with get_db() as c:
        c.execute("UPDATE meetings SET status = ?, status_detail = ? WHERE id = ?",
                  (status, detail, meeting_id))


def reset_stale_jobs():
    """Jobs run in-process; any still 'running' at startup were cut off by a restart."""
    with get_db() as c:
        c.execute("UPDATE meetings SET status = 'error', status_detail = 'interrupted by a restart' "
                  "WHERE status IN ('queued', 'transcribing', 'diarizing', 'summarizing')")


def set_meeting_ended(meeting_id, ended_at):
    with get_db() as c:
        c.execute("UPDATE meetings SET ended_at = ? WHERE id = ?", (ended_at, meeting_id))


def save_meeting_notes(meeting_id, notes_json):
    with get_db() as c:
        c.execute("UPDATE meetings SET notes = ?, summarized_at = ? WHERE id = ?",
                  (notes_json, time.time(), meeting_id))


def meeting_speakers(meeting_id):
    with get_db() as c:
        return c.execute(
            "SELECT speaker, COUNT(*) AS n FROM transcript_segments WHERE meeting_id = ? "
            "GROUP BY speaker ORDER BY MIN(id)",
            (meeting_id,),
        ).fetchall()


def rename_speaker(meeting_id, old, new):
    with get_db() as c:
        c.execute("UPDATE transcript_segments SET speaker = ? WHERE meeting_id = ? AND speaker = ?",
                  (new, meeting_id, old))


def search_segments(query, limit=100):
    like = f"%{query}%"
    with get_db() as c:
        return c.execute(
            "SELECT s.meeting_id, s.speaker, s.text, s.ts, m.title, m.room "
            "FROM transcript_segments s JOIN meetings m ON m.id = s.meeting_id "
            "WHERE s.text LIKE ? ORDER BY s.id DESC LIMIT ?",
            (like, limit),
        ).fetchall()


# --- Network scans (Phase 3 mapping) --------------------------------------
def save_netscan(ts, data_json):
    with get_db() as c:
        cur = c.execute("INSERT INTO netscans (ts, data) VALUES (?, ?)", (ts, data_json))
        c.execute("DELETE FROM netscans WHERE id NOT IN "
                  "(SELECT id FROM netscans ORDER BY id DESC LIMIT 50)")  # keep last 50
        return cur.lastrowid


def last_netscan():
    with get_db() as c:
        return c.execute("SELECT * FROM netscans ORDER BY id DESC LIMIT 1").fetchone()


def list_netscans(limit=50):
    with get_db() as c:
        return c.execute("SELECT id, ts FROM netscans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def recent_netscans(limit=50):
    with get_db() as c:
        return c.execute("SELECT id, ts, data FROM netscans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_netscan(scan_id):
    with get_db() as c:
        return c.execute("SELECT * FROM netscans WHERE id = ?", (scan_id,)).fetchone()


# --- Agent audit log + room memory ----------------------------------------
def log_agent_run(user_id, room, prompt, protocol, steps_json, reply, error, secs):
    with get_db() as c:
        c.execute(
            "INSERT INTO agent_runs (ts, user_id, room, prompt, protocol, steps, reply, error, secs) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), user_id, room, prompt, protocol, steps_json, reply, error, secs),
        )
        c.execute("DELETE FROM agent_runs WHERE id <= (SELECT MAX(id) FROM agent_runs) - 1000")


def recent_agent_runs(limit=20):
    with get_db() as c:
        return c.execute(
            "SELECT r.*, u.username FROM agent_runs r LEFT JOIN users u ON u.id = r.user_id "
            "ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()


def add_memory(room, text, author):
    with get_db() as c:
        return c.execute(
            "INSERT INTO agent_memory (room, text, author, created_at) VALUES (?, ?, ?, ?)",
            (room, text, author, time.time())).lastrowid


def list_memory(room, query=None, limit=20):
    sql, args = "SELECT * FROM agent_memory WHERE room = ?", [room]
    if query:
        sql += " AND text LIKE ?"
        args.append(f"%{query}%")
    with get_db() as c:
        return c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()


def delete_memory(room, memory_id):
    with get_db() as c:
        return c.execute("DELETE FROM agent_memory WHERE room = ? AND id = ?",
                         (room, memory_id)).rowcount


# --- Pending agent actions (confirm before acting) -------------------------
def create_pending(room, user_id, tool, arguments_json):
    with get_db() as c:
        return c.execute(
            "INSERT INTO agent_pending (room, user_id, tool, arguments, created_at) VALUES (?, ?, ?, ?, ?)",
            (room, user_id, tool, arguments_json, time.time())).lastrowid


def get_pending(pid):
    with get_db() as c:
        return c.execute("SELECT * FROM agent_pending WHERE id = ?", (pid,)).fetchone()


def decide_pending(pid, status, result, decided_by):
    with get_db() as c:
        c.execute("UPDATE agent_pending SET status = ?, result = ?, decided_by = ?, decided_at = ? "
                  "WHERE id = ?", (status, result, decided_by, time.time(), pid))


def update_segment(meeting_id, segment_id, text):
    """Replace a transcript line; returns the old text (None if not found)."""
    with get_db() as c:
        row = c.execute("SELECT text FROM transcript_segments WHERE id = ? AND meeting_id = ?",
                        (segment_id, meeting_id)).fetchone()
        if not row:
            return None
        c.execute("UPDATE transcript_segments SET text = ? WHERE id = ?", (text, segment_id))
        return row["text"]
