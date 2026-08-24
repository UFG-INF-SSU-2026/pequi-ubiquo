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


def init_db():
    with get_db() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM rooms WHERE name = ?", ("general",)).fetchone():
            c.execute(
                "INSERT INTO rooms (name, created_at) VALUES (?, ?)",
                ("general", time.time()),
            )


# --- Users -----------------------------------------------------------------
def create_user(username: str, password_hash: str):
    with get_db() as c:
        cur = c.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, time.time()),
        )
        return cur.lastrowid


def get_user_by_name(username: str):
    with get_db() as c:
        return c.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def get_user(user_id: int):
    with get_db() as c:
        return c.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
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
            "SELECT speaker, text, ts FROM transcript_segments WHERE meeting_id = ? ORDER BY id ASC",
            (meeting_id,),
        ).fetchall()


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
