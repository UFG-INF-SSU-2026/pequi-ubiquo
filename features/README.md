# Optional features (vision · voice · transcription)

These are **planned, first-class features** of TowerAI — they're kept out of the
core only so the base appliance stays small and dependency-light. The app loads
them automatically when present.

## The contract

Create `features/<name>.py` (where `<name>` is one of the names in
`config.OPTIONAL_FEATURES` — currently `vision`, `voice`, `transcription`) that
exposes a single function:

```python
def register(app):
    app.register_blueprint(my_blueprint)
```

That's it. On startup `app.register_features()` imports the module and calls
`register(app)`. If the module is missing, or its heavy dependencies aren't
installed, the core app logs it and keeps running — no crash.

## Recommended shape

```python
# features/vision.py
from flask import Blueprint, request, jsonify
from auth import login_required          # reuse the core auth guard
import llm                                # reuse the local LLM client
import db                                 # reuse persistence if needed

bp = Blueprint("vision", __name__, url_prefix="/vision")

@bp.post("/analyze")
@login_required                          # NEVER expose a feature unauthenticated
def analyze():
    ...
    return jsonify(ok=True)

def register(app):
    app.register_blueprint(bp)
```

## Enabling `transcription` (Live Captions, offline via Vosk)

Live speech-to-text captions for accessibility. Audio never leaves the machine.

1. Install the engine:
   ```
   pip install vosk
   ```
2. Download a model into `models/` — any `vosk-model-*` folder is auto-detected,
   and a Portuguese (`pt`) model is preferred. From https://alphacephei.com/vosk/models:
   - Brazilian Portuguese, best accuracy: `vosk-model-pt-fb-v0.1.1-20220516_2113` (~1.6 GB)
   - Brazilian Portuguese, fast/small:   `vosk-model-small-pt-0.3` (~31 MB)
   Or pin one explicitly with `VOSK_MODEL_PATH=/path/to/model`.
3. Start the app. A **🟢 Live Captions** link appears in each room's sidebar.
   The page shows a live transcript; click **Start captioning** to stream your
   mic. Everyone on that room's captions page sees the text in real time.

**Accuracy & keeping up:** Vosk runs on **CPU + RAM (not VRAM)**, so a large pt
model (~1.6 GB) is fine on a laptop and is far more accurate than the small one —
use it if the CPU keeps up. Capture is 16 kHz mono via an AudioWorklet; the
server recognizes on a worker thread with a bounded queue that **drops the oldest
audio when it falls behind**, so latency stays bounded instead of lagging.

**Microphone + HTTPS:** browsers only allow mic capture over HTTPS or on
localhost. A remote *speaker* therefore needs TLS — run with `USE_TLS=1`
(`pip install cryptography`) and accept the one-time self-signed warning.
*Listeners* (reading captions) need no mic, so plain HTTP works for them.

If `vosk` or the model is missing, the feature simply doesn't load (the core
app logs it and the captions link stays hidden) — nothing breaks.

## Porting notes from the old codebase

The original `E:\TowerAI\TowerAI` project already has working logic you can lift
into these plugins (clean it up and put it behind `@login_required`):

- **vision** → `process_chat_image`, `format_image_analysis_for_chat`,
  `lmstudio_vision.py`, `advanced_vision.py`, `vision_tools.py`,
  and `model_utils.check_vision_capability`.
- **voice** → `voice_tools.py` and the old `/voice/*` routes (note: the old
  `/voice/process` had a broken `send_chat_with_fallback(text, session_id)`
  call — use `llm.chat_completion(messages)` here instead).
- **transcription** → `meeting_transcription.py` and the old `/meeting/*` routes.

## Do NOT port

The old `/screen/*` endpoints (host mouse/keyboard/screenshot control) and the
`run_shell_command` agent tool. Those are remote host-control over the LAN and
have no place in a shared collaboration appliance.
