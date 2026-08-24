// Live captions client: receives caption events for everyone; streams mic
// audio (16 kHz PCM16 mono) when the user starts captioning. Fully offline —
// audio goes only to this app's server, never to a third party.
//
// Capture uses an AudioWorklet (off-main-thread) with a 16 kHz AudioContext, so
// the browser does high-quality resampling and the UI never starves the audio.
// Falls back to a ScriptProcessorNode on older browsers.
(function () {
  const page = document.querySelector(".captions-page");
  const room = page.dataset.room;
  const transcript = document.getElementById("transcript");
  const partialEl = document.getElementById("partial");
  const micBtn = document.getElementById("micBtn");
  const statusEl = document.getElementById("capStatus");

  const wsUrl =
    (location.protocol === "https:" ? "wss://" : "ws://") +
    location.host + "/ws/transcribe/" + encodeURIComponent(room);

  let ws = null;
  let capturing = false;
  let audioCtx = null, source = null, node = null, stream = null;

  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  // --- caption channel ---
  function connect() {
    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { statusEl.textContent = "connected"; };
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "partial") {
        partialEl.textContent = m.text ? `${m.speaker}: ${m.text}` : "";
      } else if (m.type === "final") {
        if (m.text) {
          const line = document.createElement("div");
          line.className = "cap-line";
          line.innerHTML = `<span class="sp">${esc(m.speaker)}</span> ${esc(m.text)}`;
          transcript.appendChild(line);
          transcript.scrollTop = transcript.scrollHeight;
        }
        partialEl.textContent = "";
      }
    };
    ws.onclose = () => { statusEl.textContent = "reconnecting…"; setTimeout(connect, 1000); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  connect();

  function sendChunk(buf) {
    if (capturing && ws && ws.readyState === WebSocket.OPEN) ws.send(buf);
  }

  async function startMic() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      statusEl.textContent = "Mic unavailable. Speakers need HTTPS or localhost (see README).";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: 16000, echoCancellation: true, noiseSuppression: true },
      });
    } catch (e) {
      statusEl.textContent = "Microphone blocked — speakers need HTTPS (self-signed OK) or localhost.";
      return;
    }
    // Prefer a 16 kHz context so the browser resamples cleanly.
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 }); }
    catch (e) { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); }
    source = audioCtx.createMediaStreamSource(stream);

    if (audioCtx.audioWorklet) {
      try {
        await audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");
        node = new AudioWorkletNode(audioCtx, "pcm-worklet");
        node.port.onmessage = (e) => sendChunk(e.data);
        source.connect(node);
        node.connect(audioCtx.destination); // worklet outputs silence — no feedback
        capturing = true;
        micBtn.textContent = "Stop captioning";
        micBtn.classList.add("rec");
        statusEl.textContent = "listening…";
        return;
      } catch (e) { /* fall through to ScriptProcessor */ }
    }
    startMicFallback();
  }

  // Legacy path for browsers without AudioWorklet.
  function startMicFallback() {
    const proc = audioCtx.createScriptProcessor(4096, 1, 1);
    const mute = audioCtx.createGain(); mute.gain.value = 0;
    const inRate = audioCtx.sampleRate, ratio = inRate / 16000;
    source.connect(proc); proc.connect(mute); mute.connect(audioCtx.destination);
    proc.onaudioprocess = (e) => {
      if (!capturing) return;
      const input = e.inputBuffer.getChannelData(0);
      const len = Math.floor(input.length / ratio);
      const out = new Int16Array(len);
      for (let i = 0; i < len; i++) {
        let s = 0, c = 0;
        const start = Math.floor(i * ratio), end = Math.floor((i + 1) * ratio);
        for (let j = start; j < end && j < input.length; j++) { s += input[j]; c++; }
        let v = c ? s / c : 0; v = Math.max(-1, Math.min(1, v));
        out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      sendChunk(out.buffer);
    };
    node = proc;
    capturing = true;
    micBtn.textContent = "Stop captioning";
    micBtn.classList.add("rec");
    statusEl.textContent = "listening…";
  }

  function stopMic() {
    capturing = false;
    try { if (node) node.disconnect(); } catch (e) {}
    try { if (source) source.disconnect(); } catch (e) {}
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (audioCtx) audioCtx.close();
    node = source = audioCtx = stream = null;
    micBtn.textContent = "Start captioning";
    micBtn.classList.remove("rec");
    statusEl.textContent = "stopped";
  }

  micBtn.addEventListener("click", () => (capturing ? stopMic() : startMic()));
})();
