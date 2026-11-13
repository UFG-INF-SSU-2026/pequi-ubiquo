// Chat media: 📷 image questions (features/vision.py) and 🎤 push-to-talk
// (features/voice.py). Messages arrive through the normal chat stream.
(function () {
  const room = document.querySelector(".chat-main").dataset.room;
  const input = document.getElementById("msg");
  const askai = document.getElementById("askai");
  const status = document.getElementById("media-status");
  const enc = encodeURIComponent(room);

  function show(msg) {
    status.hidden = !msg;
    status.textContent = msg || "";
  }
  async function post(url, form) {
    const res = await fetch(url, { method: "POST", body: form, headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  // --- 📷 image + question --------------------------------------------------
  const imgBtn = document.getElementById("img-btn");
  const imgFile = document.getElementById("img-file");
  if (imgBtn) {
    imgBtn.addEventListener("click", () => imgFile.click());
    imgFile.addEventListener("change", async () => {
      const file = imgFile.files[0];
      imgFile.value = "";
      if (!file) return;
      const form = new FormData();
      form.append("image", file);
      form.append("question", input.value.trim());
      form.append("ask_ai", askai.checked || input.value.trim() ? "1" : "0");
      input.value = "";
      show("Uploading image…");
      try {
        await post(`/vision/rooms/${enc}/image`, form);
        show("");
      } catch (e) {
        show("Image failed: " + e.message);
      }
    });
  }

  // --- 🎤 hold to talk -----------------------------------------------------
  const mic = document.getElementById("mic-btn");
  if (!mic) return;
  let rec = null, chunks = [], held = false;

  async function start(ev) {
    ev.preventDefault();
    held = true;
    if (rec) return;
    if (!navigator.mediaDevices) {
      show("Microphone needs HTTPS (or localhost). See USE_TLS in the README.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      rec = new MediaRecorder(stream);
    } catch (e) {
      show("Microphone unavailable: " + e.message);
      return;
    }
    chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = send;
    rec.start();
    if (!held) return stop();  // released while the mic permission was pending
    mic.classList.add("rec");
    show("Listening… release to send");
  }

  function stop() {
    held = false;
    if (!rec || rec.state !== "recording") return;
    rec.stop();
    rec.stream.getTracks().forEach((t) => t.stop());
    mic.classList.remove("rec");
  }

  async function send() {
    const blob = new Blob(chunks, { type: rec.mimeType });
    rec = null;
    if (blob.size < 2000) { show(""); return; }  // accidental tap
    const form = new FormData();
    form.append("audio", blob, "clip");
    show("Transcribing…");
    try {
      const r = await post(`/voice/rooms/${enc}/ask`, form);
      show(r.text ? "" : "Didn't catch that — try again.");
      window.towerSpeak(r.reply);
    } catch (e) {
      show("Voice failed: " + e.message);
    }
  }

  mic.addEventListener("pointerdown", start);
  mic.addEventListener("pointerup", stop);
  mic.addEventListener("pointerleave", stop);
})();
