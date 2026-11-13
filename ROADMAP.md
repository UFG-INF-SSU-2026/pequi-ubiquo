# TowerAI roadmap

> **Done since this was written:** remembered voices (#6), hands-free voice (#7),
> offline TTS (#8), shared files (#9), live speaker labels (#10), notifications
> (#11), learned vocabulary (#12), confirm before acting (#13), scheduled tasks
> (#14), streaming (#15), per-tool permissions (#16), evaluation set (#17), roles
> (part of #3), and learning from feedback with a fine-tuning export.
> Also done: backups (#2), run as a service (#4), CI (#5).
> Still open: validation with real models (#1).

What the appliance still lacks, ordered by value for a group working offline.
Each item notes what already exists to build on. Effort: S ≈ a day, M ≈ a few
days, L ≈ a week or more.

## Now — make what exists dependable

| # | Item | Why | Builds on | Effort |
|---|------|-----|-----------|--------|
| 1 | **Run it with real models** — Whisper `small`, a tool-calling LLM (Qwen 2.5 7B), a vision model; tune `WHISPER_SILENCE_*`, `AGENT_*`, prompts | Everything so far is tested against fakes | `status.py` model check | S |
| 2 | **Backups and export** — one-click download of the database (and restore), scheduled copies to a USB drive | A single SQLite file is a single point of failure | `db.py`, status page | S |
| 3 | **Roles: admin vs member** — only admins see Status, Network Map, IoT keys, and can delete rooms/meetings | Today every account can do everything | `auth.py` | S |
| 4 | **Run as a service** — systemd unit / Windows service (NSSM), start on boot, restart on crash, log rotation | The old project's "self-healing" goal, done with standard tools | `serve.py` | S |
| 5 | **CI** — run `pytest` and `pyflakes` on every push (GitHub Actions) | The suite exists; nothing runs it automatically | `tests/` | S |

## Next — features people will ask for

| # | Item | Why | Builds on | Effort |
|---|------|-----|-----------|--------|
| 6 | **Remember voices** — save a speaker's voiceprint when they're renamed; label known people automatically in later uploads | The first real *learning* feature; no new models needed | `diarize.py` embeddings, speaker rename | M |
| 7 | **Hands-free voice** — continuous listening with a wake phrase ("oi torre"), "torre parar", replay last answer | The old version had it; push-to-talk is clumsy in a meeting | `features/voice.py`, `stt.WhisperRecognizer` | M |
| 8 | **Offline speech output** — Piper TTS on the server so every device gets the same good Portuguese voice | Browser voices vary a lot by device | `features/voice.py` | S |
| 9 | **Shared files per room** — upload/download documents; the agent can read text/PDF files and answer questions about them (local search) | Collaboration without internet needs file exchange | vision upload code, agent tools | M |
| 10 | **Live meeting speaker labels** — diarize a shared room microphone during live captions (keep the audio while recording) | Uploads are labeled; live meetings with one mic aren't | `diarize.py`, meetings recorder | M |
| 11 | **Notifications** — @mentions, unread counts per room, browser notifications; action-item reminders on due dates | People miss things in shared rooms | chat, action items | M |
| 12 | **Learn vocabulary** — names/terms corrected in transcripts become Whisper hints (`initial_prompt`) | Fewer misspelt names over time | `stt.py`, meetings | S |

## Agent harness

| # | Item | Why | Effort |
|---|------|-----|--------|
| 13 | **Confirm before acting** — write tools (post, stop recording, mark done) ask the user to approve in chat when requested by voice or by someone other than the room's owner | Keeps the agent's actions accountable | M |
| 14 | **Scheduled tasks** — "every Friday at 17:00 summarize this week's meetings into #general" | Recurring work without anyone asking | M |
| 15 | **Streaming replies** — show the answer as it's generated | Small local models are slow; streaming feels much faster | M |
| 16 | **Per-tool permissions** — admins choose which tools members' requests can use | Fits roles (#3) | S |
| 17 | **Evaluation set** — a fixed list of questions with expected tool calls, run against a real model to compare models/prompts | Turns "seems to work" into a number | S |

## Later

- **Network anomaly alerts** — learn normal devices/latency from scan history and flag new or unstable devices.
- **Local OCR / object detection tools** for the agent (Tesseract, a small YOLO) so image questions work with text-only models.
- **Multi-node** — several TowerAI boxes sharing rooms over the LAN (Redis pub/sub already fans out messages).
- **Mobile-friendly UI / installable PWA** with offline caching of the app shell.
- **Optional guided hotspot setup** script — with a user-chosen password, never required by the app.

## Deliberately not planned

Remote control of the host (screen, mouse, keyboard, shell), game automation and
an in-app ML training platform. They were the main sources of the old project's
security problems and heavy dependencies and don't fit a shared appliance.
