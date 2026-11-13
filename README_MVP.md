# TowerAI MVP — offline collaboration appliance

A small, self-contained web app that runs its **own local network service** for
a group of people with **no internet**: shared chat rooms, accounts, persistent
history, and a local AI assistant with tools (via LM Studio / Ollama). Live
captions, meetings, voice, vision, IoT microphones and a network map load as
**pluggable features** (see `features/`). Planned work: [ROADMAP.md](ROADMAP.md).

## What it is (and isn't)

- ✅ Offline-first: talks only to a **local** LLM (localhost-first, no LAN scanning).
- ✅ Multi-user: real accounts (hashed passwords), shared rooms, SQLite persistence.
- ✅ LAN appliance: serves connected devices; reachable at `towerai.local` via mDNS.
- ✅ Safe by default: every route behind login; **no shell or host-control endpoints**.
- 🚫 Not a connectivity/backhaul/mesh project — that needs hardware and is out of scope.

## Quick start

```bash
cd towerai-mvp
python -m venv .venv && .venv\Scripts\activate     # Windows
#   Linux/macOS: python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start a local model in LM Studio (port 1234) or Ollama, then:
python serve.py          # threaded server with WebSockets  ← recommended
#   or
python app.py            # Flask dev server
```

Open `http://localhost:5000` (or `http://towerai.local:5000` from other devices
on the same Wi-Fi), create an account, and start chatting. Start a message with
`@tower` — or tick the **AI** box — to bring the assistant into the room.

## Providing the local network (zero budget)

This app does **not** create the Wi-Fi itself (that coupling is what made the old
version fragile). Supply the LAN with whatever is free:

1. **Reuse any old Wi-Fi router** as a plain access point (no internet plugged in) — most reliable.
2. **Windows Mobile Hotspot** — the modern replacement for `netsh hostednetwork`.

Run TowerAI on a machine joined to that network. Devices connect and reach it by
IP or `towerai.local`.

## Architecture

| File | Role |
|------|------|
| `app.py` | App factory + optional-feature loader |
| `serve.py` | Server entrypoint (threaded, WebSockets, optional TLS) |
| `config.py` | Env-driven settings; localhost-first LLM endpoint |
| `db.py` | SQLite: users, rooms, messages |
| `status.py` | Status page, model check (`python status.py`) |
| `agent.py` | Agent harness: tool-calling loop for the local LLM (+ CLI) |
| `admin.py` | Assistant permissions, training data (admins); scheduled tasks |
| `scheduler.py` | Recurring assistant tasks (daily / weekly / every N min) |
| `notifications.py` | @mentions, assigned action items, unread counts |
| `files.py` | Shared files per room (searchable, readable by the assistant) |
| `learning.py` | 👍/👎 feedback → in-context examples + fine-tuning export |
| `voices.py` | Remembered voices (speaker embeddings) |
| `vocab.py` | Learned vocabulary passed to Whisper |
| `tts.py` | Offline text-to-speech (Piper) |
| `evals/` | Agent evaluation set + runner against the real model |
| `training/` | Fine-tuning guide for LM Studio models |
| `llm.py` | Safe local LLM client (salvaged from old `send_chat_with_fallback`) |
| `auth.py` | Register / login / logout / `login_required` |
| `chat.py` | Shared rooms + AI participant (REST: history + fallback) |
| `realtime.py` | WebSocket instant messaging (primary transport) |
| `discovery.py` | mDNS `*.local` advertising (optional) |
| `stt.py` | Shared STT engine — Whisper (faster-whisper, CPU or GPU) |
| `captions.py` | Shared per-room caption registry (+ meeting-record hook) |
| `pubsub.py` | Broadcast fan-out — in-process, or Redis across workers |
| `devices.py` | IoT device identity + API-key auth |
| `tls_cert.py` | Self-signed cert generator (`serve.py --gen-cert`) |
| `netmap.py` | Network mapping core (discovery + link telemetry) |
| `features/vision.py` | Share images in chat; local vision model answers |
| `features/voice.py` | Push-to-talk with the assistant (Whisper + browser TTS) |
| `features/meetings.py` | Stored, searchable meetings: recording uploads, summaries, action items |
| `recordings.py` | Uploaded recording → speaker-labeled transcript (Whisper + diarization) |
| `diarize.py` | Offline speaker diarization — "who said what" (sherpa-onnx) |
| `summarizer.py` | Meeting summary, decisions and action items via the local LLM |
| `features/netmap.py` | Network Map UI + LLM-assisted analysis |

## Agent (the assistant uses tools)

When asked in chat (`@tower …`, the AI toggle, or 🎤), the assistant runs as an
agent (`agent.py`): it can search meeting transcripts, read a meeting's summary,
list and tick off action items across meetings, read or post in rooms,
start/stop recordings, summarize a meeting, remember notes per room (shown to it
in later conversations), list IoT devices, check the latest network scan, get
the time, and calculate. It loops (model → tools → results → model) up to
`AGENT_MAX_STEPS`; the room shows what it's doing live ("🔧 search_transcripts…")
and replies list the tools used.

- Guard rails: a repeated identical tool call gets the cached result back, and
  a model going in circles is stopped and asked for its answer; old tool
  results are trimmed to stay under `AGENT_MAX_CONTEXT_CHARS`.
- Audit log: every run (who, room, request, tools, errors, time) is stored and
  shown under **Assistant activity** on the status page.

- Works with tool-calling models over the OpenAI-compatible API (LM Studio,
  Ollama: Qwen 2.5/3, Llama 3.1+, Mistral…). Models without tool support fall
  back automatically to a JSON-in-text protocol (`AGENT_PROTOCOL=text` forces it).
- Safety: tools only touch TowerAI's own data — no shell, files or host
  control. `AGENT_ALLOW_WRITES=0` makes it read-only; `AGENT_ENABLED=0` restores
  the plain chat assistant.
- API: `POST /api/agent {"prompt": "...", "room": "general"}` → reply + steps.
- CLI: `python agent.py "what did we decide about the budget?"`
  (`--list-tools`, `--protocol text`).
- New tools: add a function with the `@tool(...)` decorator in `agent.py`.

### Approvals, permissions, schedules, streaming, evaluation

- **Confirm before acting** — tools marked ✋ (post a message, start/stop a
  recording, tick off an item, forget a note, create/delete a schedule) don't
  run when asked in chat: the reply shows **Approve / Reject** buttons for the
  requester (or an admin). `AGENT_CONFIRM_ACTIONS=0` turns this off.
- **Per-tool permissions** — the first account is admin; on **🔐 Assistant
  permissions** admins set each tool to *everyone*, *admins* or *off*, and pick
  other admins.
- **Streaming** — the answer appears in the room as the model writes it
  (`AGENT_STREAM`).
- **Scheduled tasks** — **⏰ Scheduled tasks** (or ask the assistant): a prompt
  that runs daily, weekly or every N minutes and posts its answer in a room.
- **Evaluation** — `python evals/run.py [--model ID] [--protocol text]` runs the
  questions in `evals/cases.json` against the real model on a seeded temporary
  database and reports pass rate, tool-choice accuracy and latency.

### Learning from feedback (LM Studio)

👍/👎 under each assistant reply; 👎 lets you type the answer it should have
given. The most similar well-rated or corrected answers are shown to the model
as examples before it answers (`AGENT_FEW_SHOT`), so it improves immediately
with any model. **🧠 Training data** (admins) exports them as a LoRA
fine-tuning dataset; `training/README.md` covers training, converting to GGUF
and loading the result in LM Studio.

## Voices, vocabulary and hands-free

- **🎧 Hands-free** in a room: speak naturally; sentences starting with
  "Torre, …" / "Hey Tower, …" (`VOICE_WAKE_WORDS`) go to the assistant and the
  answer is spoken. "Torre, parar" stops; "Torre, repetir" replays.
- **Offline voice** — replies are spoken by Piper (`models/piper/`, Brazilian
  Portuguese "faber" via `demo_setup.py --download-model`); 🔊 on any reply.
- **Remembered voices** — rename "Speaker 2" → "Bruno" on a meeting and later
  uploads suggest "Bruno?" when the voice clearly matches; confirm by renaming.
  Live captions' **Shared mic** option labels speakers by voice. Short clips are
  unreliable for voice matching, so suggestions need ≥ 6 s of speech and a
  clear margin; calibrate with `python voices.py compare a.wav b.wav`.
- **Vocabulary** — correct a transcript line (✎) and its new words are passed
  to Whisper from then on; manage terms on **🎙 Voices & vocabulary**.

## Shared files and notifications

- **📎 Files** per room: upload/download; text, PDF and Word files are
  searchable and readable by the assistant. Files are always served as downloads.
- **🔔 Notifications** for @mentions, action items assigned to you by name and
  scheduled-task runs; unread badges per room; optional desktop notifications.

## Network Map (device discovery + LLM analysis)

An admin tool (**🌐 Network Map**, login required) that scans the appliance's own
private `/24` and shows a live reachability map of the segment: discovered hosts
with MAC/vendor (OUI), per-host latency, open ports, and mDNS names, plus the
host's own Wi-Fi signal/channel. **🤖 Analyze with AI** feeds the telemetry to
the local LLM for advisory recommendations (weak links, channel/interference,
unexpected devices, open-port flags, coverage/AP-placement) — the model only
advises, it never runs anything. Scans persist (last 50), and each scan is
**diffed against the previous one** — the UI shows devices that appeared, dropped,
or changed (RTT, open ports, MAC/name), and the history panel lets you compare any
past scan to the latest. This is what makes it useful as you add infrastructure:
plug in an ESP32 or Pi, rescan, and it shows up in the "+ new" list.

Honest scope: this is a reachability map of one L2 segment (ARP + ping sweep +
mDNS) and RTT-based link quality — not physical topology (needs SNMP/LLDP) or a
geographic coverage heatmap (needs GPS/RF).
| `features/transcription.py` | Browser Live Captions (uses `stt` + `captions`) |
| `features/iot.py` | IoT STT microservice (device-auth WS + HTTP ingestion) |
| `stt_service.py` | Standalone entrypoint to run the STT microservice alone |
| `features/` | Drop-in vision / voice / transcription / iot plugins |

## IoT audio transcription (device microservice)

Network devices (ESP32 mics, Raspberry Pis, phones, other services) can send
audio and get transcripts using an **API key** instead of a browser login. The
same shared Whisper engine powers it, and transcripts optionally publish into a
room's Live Captions so IoT audio shows up live for people.

Provision a key (human, logged in): open **📟 IoT Devices**, or headless:

```bash
python stt_service.py --create-device hall-mic-1 --room general
```

Devices then use either mode:

```bash
# Batch: POST a WAV (or raw PCM16 mono) -> {"text": ...}
curl -X POST "http://TOWER:5000/api/stt/transcribe?room=general" \
     -H "Authorization: Bearer tk_..." --data-binary @clip.wav

# Streaming: WebSocket, send 16 kHz PCM16 mono frames, receive caption events
#   ws://TOWER:5000/api/stt/stream?key=tk_...&room=general
```

Run it as an **independent microservice** on its own port (shares the same DB
and model): `python stt_service.py` (default `:5100`).

## Adding features

See `features/README.md`. In short: create `features/<name>.py` exposing
`register(app)` and add the name to `config.OPTIONAL_FEATURES`; it loads
automatically on next start, and a failure only disables that feature.

## Running it for real

- **As a service** (start on boot, restart on crash, rotated logs):
  Linux `deploy/towerai.service` (systemd; instructions inside),
  Windows `deploy/install-windows-service.ps1` (NSSM).
- **Backups** — admins: **💾 Backups** → download a .zip (database + shared
  files + images). Set `BACKUP_DIR` (e.g. a USB drive) for automatic backups
  every `BACKUP_EVERY_HOURS`, keeping `BACKUP_KEEP`. Restore with the server
  stopped: `python backup.py restore towerai-backup-….zip`.
- **CI** — `.github/workflows/ci.yml` runs lint and the test suite on every push.

## Security

- Every page needs login except `/health`, which shows only up/down to anonymous callers; there are no shell or host-control endpoints.
- CSRF protection on all forms and API calls (the page adds the token itself);
  WebSockets from other sites are refused; device endpoints use API keys.
- Logins are throttled (`LOGIN_MAX_FAILURES` per user in `LOGIN_WINDOW_SEC`,
  4× that per address) and `next=` redirects stay on this site.

## Tests

```bash
pip install pytest
pytest -q
```

Runs offline in a few seconds: a fake LLM server and a stubbed Whisper model
stand in for the real ones (`tests/conftest.py`).

## Health and status

- **🩺 Status page** (`/status`, login required; also `GET /api/status` as JSON):
  AI server and model, Whisper and speaker models, loaded/failed features,
  background jobs and their errors, storage, network addresses, HTTPS, Redis —
  plus plain-language advice for anything that needs fixing.
- **Model check** (button on the page, or `python status.py`): sends three tiny
  requests to the local LLM — does it answer (and how fast), does it support
  native tool calling (agent), and can it read an image (📷). `python status.py`
  exits with code 1 when no LLM server answers.
- `GET /health` → up/down for monitors; logged in, also the LLM endpoint, models and room count.
