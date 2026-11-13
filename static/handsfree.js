// 🎧 Hands-free voice (features/voice.py): stream the mic continuously; the server
// transcribes and answers utterances that start with the wake phrase ("Torre, …").
// The mic is muted while a reply is being spoken, so Tower doesn't hear itself.
(function () {
  const btn = document.getElementById("hf-btn");
  if (!btn) return;
  const room = document.querySelector(".chat-main").dataset.room;
  const status = document.getElementById("media-status");
  let ws = null, stream = null, ctx = null, node = null, speaking = false, wake = "Torre";

  function show(msg) { status.hidden = !msg; status.textContent = msg || ""; }

  async function start() {
    if (!navigator.mediaDevices || !window.AudioWorkletNode) {
      show("Hands-free needs HTTPS (or localhost) and a modern browser.");
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      try { ctx = new AudioContext({ sampleRate: 16000 }); } catch (e) { ctx = new AudioContext(); }
      await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
      node = new AudioWorkletNode(ctx, "pcm-worklet");
      ctx.createMediaStreamSource(stream).connect(node);
      node.connect(ctx.destination); // outputs silence
    } catch (e) {
      show("Microphone unavailable: " + e.message);
      stop();
      return;
    }
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host +
                       "/ws/voice/" + encodeURIComponent(room));
    node.port.onmessage = (e) => {
      if (!speaking && ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
    };
    ws.onmessage = async (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "ready") { wake = m.wake; show(`🎧 Listening — say “${cap(wake)}, …”`); }
      else if (m.type === "wake") show("🎧 Yes? …");
      else if (m.type === "thinking") show(`🎧 “${m.text}” — thinking…`);
      else if (m.type === "stop") stop();
      else if (m.type === "error") show("🎧 " + m.text);
      else if (m.type === "reply") {
        speaking = true;
        show("🔊 Speaking… (say nothing)");
        await window.towerSpeak(m.text);
        speaking = false;
        if (ws) show(`🎧 Listening — say “${cap(wake)}, …”`);
      }
    };
    ws.onclose = () => { if (btn.classList.contains("rec")) stop(); };
    btn.classList.add("rec");
    btn.title = "Stop hands-free";
  }

  function stop() {
    btn.classList.remove("rec");
    btn.title = "Hands-free: talk to the assistant by saying its name";
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx) ctx.close().catch(() => {});
    stream = ctx = node = null;
    window.towerStopSpeaking();
    show("");
  }

  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  btn.addEventListener("click", () => (btn.classList.contains("rec") ? stop() : start()));
})();
