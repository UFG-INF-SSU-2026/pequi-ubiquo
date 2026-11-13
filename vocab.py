"""Learned vocabulary: names and terms Whisper should spell right.

Sources: terms added by hand (Voices & vocabulary page), words people introduce
when they correct a transcript line, plus everyone's username, room names and
remembered voice names. They're passed to Whisper as an `initial_prompt`
("Glossary: …"), which biases recognition towards these spellings — the
standard way to teach Whisper vocabulary without retraining it.
"""
import re
import time

import config
import db

_cache = {"at": 0.0, "prompt": ""}
WORD_RE = re.compile(r"[\w][\w'’.-]*[\w]|\w", re.UNICODE)


def add(term, source="manual"):
    term = term.strip()[:64]
    if not term:
        return False
    with db.get_db() as c:
        c.execute("INSERT INTO vocabulary (term, source, created_at) VALUES (?, ?, ?) "
                  "ON CONFLICT(term) DO UPDATE SET uses = uses + 1", (term, source, time.time()))
    _cache["at"] = 0
    return True


def remove(term):
    with db.get_db() as c:
        c.execute("DELETE FROM vocabulary WHERE term = ?", (term,))
    _cache["at"] = 0


def terms():
    with db.get_db() as c:
        return c.execute("SELECT * FROM vocabulary ORDER BY uses DESC, term").fetchall()


def learn_from_correction(old_text, new_text, limit=5):
    """Words that appear in the corrected text but not the original — usually the
    names and jargon Whisper got wrong. Returns the terms learned."""
    old = {w.lower() for w in WORD_RE.findall(old_text or "")}
    learned = []
    for w in WORD_RE.findall(new_text or ""):
        if len(w) >= 3 and not w.isdigit() and w.lower() not in old and w not in learned:
            learned.append(w)
    for w in learned[:limit]:
        add(w, "correction")
    return learned[:limit]


def prompt():
    """The Whisper initial_prompt, cached for 30 s ('' when there's nothing)."""
    if not config.VOCAB_ENABLED:
        return ""
    if time.time() - _cache["at"] < 30:
        return _cache["prompt"]
    words = [t["term"] for t in terms()]
    with db.get_db() as c:
        words += [r[0] for r in c.execute("SELECT username FROM users")]
        words += [r[0] for r in c.execute("SELECT name FROM rooms")]
        words += [r[0] for r in c.execute("SELECT name FROM voiceprints")]
    seen, out, size = set(), [], 0
    for w in words:
        if w.lower() in seen or w.lower().startswith("speaker "):
            continue
        seen.add(w.lower())
        if size + len(w) > config.VOCAB_MAX_CHARS:
            break
        out.append(w)
        size += len(w) + 2
    _cache.update(at=time.time(), prompt=("Glossary: " + ", ".join(out) + ".") if out else "")
    return _cache["prompt"]
