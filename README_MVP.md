# TowerAI MVP — offline collaboration appliance

A small, self-contained web app that runs its **own local network service** for
a group of people with **no internet**: shared chat rooms, accounts, persistent
history, and a local AI assistant (via LM Studio / Ollama). Vision, voice, and
transcription are designed in as **pluggable features** (see `features/`).

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
pip install -r requirements.txt

# Start a local model in LM Studio (port 1234) or Ollama, then:
python serve.py          # production WSGI (waitress)  ← recommended
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
| `serve.py` | Production entrypoint (waitress) |
| `config.py` | Env-driven settings; localhost-first LLM endpoint |
| `db.py` | SQLite: users, rooms, messages |
| `llm.py` | Safe local LLM client (salvaged from old `send_chat_with_fallback`) |
| `auth.py` | Register / login / logout / `login_required` |
| `chat.py` | Shared rooms + AI participant (REST: history + fallback) |
| `realtime.py` | WebSocket instant messaging (primary transport) |
| `discovery.py` | mDNS `*.local` advertising (optional) |
| `stt.py` | Shared STT engine — Vosk (streaming) or Whisper (GPU-capable) |
| `captions.py` | Shared per-room caption registry (+ meeting-record hook) |
| `pubsub.py` | Broadcast fan-out — in-process, or Redis across workers |
| `devices.py` | IoT device identity + API-key auth |
| `tls_cert.py` | Self-signed cert generator (`serve.py --gen-cert`) |
| `netmap.py` | Network mapping core (discovery + link telemetry) |
| `features/meetings.py` | Stored, searchable meeting transcripts |
| `features/netmap.py` | Network Map UI + LLM-assisted analysis |

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
same shared Vosk engine powers it, and transcripts optionally publish into a
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

## Adding vision / voice / transcription

See `features/README.md`. In short: create `features/vision.py` exposing
`register(app)`; it loads automatically on next start. Working logic from the old
project (`lmstudio_vision.py`, `voice_tools.py`, `meeting_transcription.py`) can
be lifted in behind `@login_required`.

## Health

`GET /health` → live LLM status, active endpoint, available models, room count.
