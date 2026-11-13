# Optional features (vision · voice · transcription · iot · meetings · netmap)

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

## Enabling `transcription` (Live Captions, offline via Whisper)

Live speech-to-text captions for accessibility. Audio never leaves the machine.

1. Install the engine (already in `requirements.txt`):
   ```
   pip install faster-whisper
   ```
2. Get a model. Pick a size with `WHISPER_MODEL` (`tiny`, `base`, `small` —
   default, `medium`, `large-v3`) and pre-download it so the appliance runs
   offline:
   ```
   python demo_setup.py --download-model
   ```
   This stores it in `models/faster-whisper-<size>`, which is used automatically.
   `WHISPER_MODEL` can also point at any local CTranslate2 Whisper model folder.
   Without a local copy, faster-whisper downloads the model on first start.
   The language defaults to Portuguese (`WHISPER_LANGUAGE=pt`; `auto` detects).
3. Start the app. A **🟢 Live Captions** link appears in each room's sidebar.
   The page shows a live transcript; click **Start captioning** to stream your
   mic. Everyone on that room's captions page sees the text in real time.

**How streaming works:** Whisper transcribes whole utterances, so the server
buffers audio and emits a caption line when the speaker pauses
(`WHISPER_SILENCE_SEC`, default 0.7 s) or after `WHISPER_MAX_SEGMENT_SEC`
(default 10 s). With a GPU it also shows live *partial* captions while you speak
(`WHISPER_PARTIAL_SEC`, `auto` = GPU only; set e.g. `1.5` to force on CPU, `0`
to disable). In a noisy room raise `WHISPER_SILENCE_RMS` so pauses are detected.

**Accuracy & keeping up:** the device auto-detects CUDA (float16), else CPU
(int8). On CPU, `small` is a good balance; `base`/`tiny` are faster, and
`medium`/`large-v3` are far more accurate but realistically need a GPU. Capture
is 16 kHz mono via an AudioWorklet; the server recognizes on a worker thread with
a bounded queue that **drops the oldest audio when it falls behind**, so latency
stays bounded instead of lagging.

**Microphone + HTTPS:** browsers only allow mic capture over HTTPS or on
localhost. A remote *speaker* therefore needs TLS — run with `USE_TLS=1`
(`pip install cryptography`) and accept the one-time self-signed warning.
*Listeners* (reading captions) need no mic, so plain HTTP works for them.

If `faster-whisper` or the model is missing, the feature simply doesn't load (the core
app logs it and the captions link stays hidden) — nothing breaks.

## `vision` — images in chat

Click **📷** in a room's composer to share a PNG/JPEG/GIF/WebP (≤ `VISION_MAX_MB`,
10). Text in the box becomes the question; the assistant answers in the room via
an OpenAI-compatible vision request to the same local LLM server. Load a
vision-capable model (Qwen2.5-VL, Gemma 3, LLaVA…) or set `VISION_MODEL`.
Images are stored in `uploads/images/` and served only to logged-in users.

## `voice` — push-to-talk with the assistant

Hold **🎤** in a room and speak. The clip is transcribed offline with Whisper,
posted as your message, and the assistant's reply is posted and read aloud by
the browser's built-in speech synthesis (no TTS install). Loads only when
Whisper does. The mic needs HTTPS (`USE_TLS=1`) on devices other than the host.

## Meetings: recording uploads, speaker labels, summaries

All in `features/meetings.py` (no extra install beyond `requirements.txt`):

- **Upload a recording** (📝 Meetings → *Upload recording*): any audio or video
  file (decoded by PyAV, which ships with faster-whisper — no ffmpeg needed), up
  to `MAX_UPLOAD_MB` (500). It's transcribed in a background job, one upload at a
  time so Live Captions keep up; the meeting page shows progress and refreshes
  itself. The file is deleted afterwards unless `KEEP_RECORDINGS=1`.
- **Who said what**: `diarize.py` runs offline speaker diarization with
  sherpa-onnx (ONNX Runtime, CPU, no PyTorch). Models (~33 MB) go in
  `models/diarization/` via `python demo_setup.py --download-model`. Whisper's
  word timestamps are matched to the speaker turns, so segments split exactly
  where the speaker changes. Labels are *Speaker 1…N* and can be renamed on the
  meeting page (action-item owners follow the rename). Entering the number of
  speakers improves accuracy a lot; otherwise `DIARIZATION_THRESHOLD` (0.5,
  lower = more speakers) decides. Without the models, uploads still work, just
  unlabeled. Live meetings are already labeled by who is captioning (each
  browser user / IoT device).
- **Summary & action items**: *🤖 Summarize* sends the transcript to the local
  LLM (`llm.py`) and stores a summary, decisions and a checklist of action items
  with owner and due date. Transcripts longer than `SUMMARY_CHUNK_CHARS` are
  summarized in parts first, so long meetings fit a small model. Notes are
  written in the transcript's language and included in the `.txt` export.

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
