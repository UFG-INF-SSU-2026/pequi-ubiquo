"""Learning from feedback — for local models in LM Studio / Ollama.

LM Studio runs models but can't train them, so TowerAI learns in two ways:

1. In-context learning (live, no training): people rate assistant replies 👍/👎
   and can write the answer it *should* have given. Before each request, the
   agent retrieves up to AGENT_FEW_SHOT past questions most similar to the new
   one (word-overlap similarity) whose answers were rated 👍 or corrected, and
   shows them to the model as examples. The assistant gets better at this
   group's questions immediately, with any model.

2. Fine-tuning data: /admin/training exports the rated conversations as a
   chat-format JSONL dataset ({"messages": [...]}) — the format LoRA trainers
   (mlx-lm, Unsloth, Axolotl, TRL) accept. Train an adapter on a GPU machine,
   merge, convert to GGUF and load the result in LM Studio — see training/README.md.
"""
import json
import re
import time

import config
import db

WORD_RE = re.compile(r"\w{3,}", re.UNICODE)
FOOTER_RE = re.compile(r"\n*(🔧 .*|\[confirm:\d+\] .*)$", re.M)


def clean_answer(text):
    return FOOTER_RE.sub("", text or "").strip()


def rate(message_id, user_id, rating, correction=None):
    """Store 👍 (1) / 👎 (-1) and an optional corrected answer for an assistant message."""
    with db.get_db() as c:
        msg = c.execute("SELECT role FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not msg or msg["role"] != "assistant":
            raise ValueError("only assistant replies can be rated")
        c.execute("INSERT INTO feedback (message_id, user_id, rating, correction, created_at) "
                  "VALUES (?, ?, ?, ?, ?) ON CONFLICT(message_id, user_id) DO UPDATE SET "
                  "rating = excluded.rating, correction = excluded.correction, created_at = excluded.created_at",
                  (message_id, user_id, 1 if rating > 0 else -1,
                   (correction or "").strip()[:4000] or None, time.time()))


def _question_for(c, msg):
    """The user message the assistant was answering (latest one before it)."""
    row = c.execute("SELECT username, content FROM messages WHERE room_id = ? AND id < ? AND role = 'user' "
                    "ORDER BY id DESC LIMIT 1", (msg["room_id"], msg["id"])).fetchone()
    return row["content"] if row else ""


def examples(only_good=True):
    """[(question, answer, rating, message_id)]: corrections win over the original answer."""
    out = []
    with db.get_db() as c:
        rows = c.execute(
            "SELECT m.id, m.room_id, m.content, SUM(f.rating) AS score, "
            "MAX(f.correction) AS correction FROM feedback f JOIN messages m ON m.id = f.message_id "
            "GROUP BY m.id ORDER BY m.id DESC LIMIT 2000").fetchall()
        for r in rows:
            answer = r["correction"] or (clean_answer(r["content"]) if r["score"] > 0 else None)
            if only_good and not answer:
                continue
            out.append((_question_for(c, r), answer or clean_answer(r["content"]), r["score"], r["id"]))
    return out


def _words(text):
    return {w.lower() for w in WORD_RE.findall(text or "")}


def similar_examples(question, k=None):
    """Up to k good past (question, answer) pairs most similar to `question`."""
    k = config.AGENT_FEW_SHOT if k is None else k
    if k <= 0:
        return []
    q = _words(question)
    if not q:
        return []
    scored = []
    for past_q, answer, _, _ in examples():
        p = _words(past_q)
        if p:
            sim = len(q & p) / len(q | p)
            if sim >= 0.2:
                scored.append((sim, past_q, answer))
    scored.sort(key=lambda x: -x[0])
    return [(pq, a) for _, pq, a in scored[:k]]


def prompt_block(question):
    ex = similar_examples(question)
    if not ex:
        return ""
    return ("\n\nAnswers people in this group rated as good (follow their style and facts "
            "when relevant):\n" + "\n".join(f"Q: {q[:300]}\nA: {a[:600]}" for q, a in ex))


def export_jsonl(min_score=1, context=6):
    """Fine-tuning dataset: one {"messages": [system, …context, user, assistant]} per
    good example (rated 👍 or corrected), most recent context included."""
    import chat
    lines = []
    with db.get_db() as c:
        rows = c.execute(
            "SELECT m.id, m.room_id, m.content, SUM(f.rating) AS score, MAX(f.correction) AS correction "
            "FROM feedback f JOIN messages m ON m.id = f.message_id GROUP BY m.id ORDER BY m.id").fetchall()
        for r in rows:
            answer = r["correction"] or (clean_answer(r["content"]) if r["score"] >= min_score else None)
            if not answer:
                continue
            prev = c.execute("SELECT role, username, content FROM messages WHERE room_id = ? AND id < ? "
                             "ORDER BY id DESC LIMIT ?", (r["room_id"], r["id"], context)).fetchall()[::-1]
            msgs = [{"role": "system", "content": chat.SYSTEM_PROMPT}]
            for p in prev:
                if p["role"] == "assistant":
                    msgs.append({"role": "assistant", "content": clean_answer(p["content"])})
                else:
                    msgs.append({"role": "user", "content": f"{p['username']}: {p['content']}"})
            if len(msgs) < 2 or msgs[-1]["role"] != "user":
                continue
            msgs.append({"role": "assistant", "content": answer})
            lines.append(json.dumps({"messages": msgs}, ensure_ascii=False))
    return "\n".join(lines) + ("\n" if lines else "")


def stats():
    with db.get_db() as c:
        row = c.execute("SELECT SUM(rating > 0) AS up, SUM(rating < 0) AS down, "
                        "SUM(correction IS NOT NULL) AS corrected FROM feedback").fetchone()
    return {"up": row["up"] or 0, "down": row["down"] or 0, "corrected": row["corrected"] or 0}
