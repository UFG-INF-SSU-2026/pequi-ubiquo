// Shared-room chat client.
// Primary transport: native WebSocket (instant). Fallback: REST polling.
(function () {
  const main = document.querySelector(".chat-main");
  const room = main.dataset.room;
  const me = main.dataset.me;
  const box = document.getElementById("messages");
  const form = document.getElementById("composer");
  const input = document.getElementById("msg");
  const askai = document.getElementById("askai");

  const api = `/api/rooms/${encodeURIComponent(room)}/messages`;
  const wsUrl =
    (location.protocol === "https:" ? "wss://" : "ws://") +
    location.host + `/ws/rooms/${encodeURIComponent(room)}`;

  const seen = new Set();
  let lastId = 0;
  let ws = null;
  let wsOk = false;
  let pollTimer = null;
  let retry = 0;

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }
  function timeStr(ts) {
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ""; }
  }

  function renderOne(m) {
    if (seen.has(m.id)) return;
    seen.add(m.id);
    lastId = Math.max(lastId, m.id);
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const el = document.createElement("div");
    let cls = "msg";
    if (m.role === "assistant") cls += " assistant";
    else if (m.username === me) cls += " mine";
    el.className = cls;
    el.innerHTML =
      `<span class="name">${esc(m.username)}</span>` +
      `<span class="time">${timeStr(m.created_at)}</span>` +
      `<div class="body">${esc(m.content)}</div>`;
    box.appendChild(el);
    if (nearBottom) box.scrollTop = box.scrollHeight;
  }

  async function loadHistory() {
    try {
      const res = await fetch(`${api}?since=0`, { headers: { Accept: "application/json" } });
      if (res.ok) (await res.json()).forEach(renderOne);
    } catch (e) { /* will retry via transport */ }
  }

  // --- WebSocket transport ---
  function connectWS() {
    try { ws = new WebSocket(wsUrl); }
    catch (e) { startPolling(); return; }

    ws.onopen = () => { wsOk = true; retry = 0; stopPolling(); };
    ws.onmessage = (ev) => {
      try { renderOne(JSON.parse(ev.data)); } catch (e) {}
    };
    ws.onclose = () => {
      wsOk = false;
      // Exponential backoff reconnect; poll in the meantime so nothing is missed.
      startPolling();
      retry = Math.min(retry + 1, 6);
      setTimeout(connectWS, 500 * Math.pow(2, retry));
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  // --- Polling fallback ---
  async function poll() {
    try {
      const res = await fetch(`${api}?since=${lastId}`, { headers: { Accept: "application/json" } });
      if (res.ok) (await res.json()).forEach(renderOne);
    } catch (e) {}
  }
  function startPolling() {
    if (pollTimer) return;
    poll();
    pollTimer = setInterval(poll, 2000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // --- Sending ---
  async function sendMessage(content, ask) {
    if (wsOk && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ content, ask_ai: ask }));
    } else {
      // REST fallback; next poll/WS message reflects it back.
      try {
        await fetch(api, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content, ask_ai: ask }),
        });
        poll();
      } catch (e) {}
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const content = input.value.trim();
    if (!content) return;
    input.value = "";
    sendMessage(content, askai.checked);
  });

  loadHistory().then(connectWS);
})();
