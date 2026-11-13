// Speaking replies: offline Piper voice on the server (tts.py) when installed,
// else the browser's speech synthesis. towerSpeak() resolves when speech ends.
(function () {
  const host = document.querySelector("[data-tts]");
  const serverTTS = host && host.dataset.tts === "1";
  const lang = host && host.dataset.lang;
  let current = null;

  function browserSpeak(text) {
    return new Promise((resolve) => {
      if (!("speechSynthesis" in window)) return resolve();
      const u = new SpeechSynthesisUtterance(text);
      if (lang && lang !== "auto") u.lang = lang === "pt" ? "pt-BR" : lang;
      u.onend = u.onerror = resolve;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    });
  }

  window.towerStopSpeaking = function () {
    if (current) { current.pause(); current = null; }
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  };

  window.towerSpeak = async function (text) {
    if (!text || text.startsWith("[")) return;
    const clean = text.split("\n").filter((l) => !l.startsWith("[confirm:") && !l.startsWith("🔧")).join("\n");
    window.towerStopSpeaking();
    if (serverTTS) {
      try {
        const r = await fetch("/voice/tts", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: clean }),
        });
        if (r.ok) {
          const url = URL.createObjectURL(await r.blob());
          const audio = (current = new Audio(url));
          await new Promise((resolve) => {
            audio.onended = audio.onerror = resolve;
            audio.play().catch(resolve);
          });
          URL.revokeObjectURL(url);
          return;
        }
      } catch (e) {}
    }
    return browserSpeak(clean);
  };
})();
