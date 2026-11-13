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
  // "[image:<file>]\n<text>" -> inline image (features/vision.py) + text
  // Markers inside message text:
  //   "[image:<file>]" first line -> inline image (features/vision.py)
  //   "[confirm:<id>] <action>"    -> Approve / Reject buttons (agent.py)
  //   "[file:<id>:<name>]"         -> link to a shared file (files.py)
  function body(content) {
    let html = "";
    const m = /^\[image:([\w.-]+)\]\n?/.exec(content);
    if (m) {
      const src = `/vision/images/${encodeURIComponent(m[1])}`;
      html = `<a href="${src}" target="_blank"><img class="chat-img" src="${src}" alt="image"></a>`;
      content = content.slice(m[0].length);
    }
    return html + esc(content)
      .replace(/^\[confirm:(\d+)\] (.*)$/gm, (_, id, desc) =>
        `<span class="confirm" data-id="${id}">✋ <code>${desc}</code> ` +
        `<button class="btn primary" data-act="approve">Approve</button> ` +
        `<button class="btn" data-act="reject">Reject</button></span>`)
      .replace(/\[file:(\d+):([^\]]+)\]/g, (_, id, name) =>
        `<a class="file-chip" href="/files/${id}/download">📎 ${name}</a>`);
  }

  box.addEventListener("click", (e) => {
    const say = e.target.closest(".say");
    if (say) window.towerSpeak(say.closest(".msg").dataset.raw);
    const rb = e.target.closest(".rate button");
    if (rb) {
      const msg = rb.closest(".msg");
      const rating = Number(rb.dataset.r);
      const correction = rating < 0 ? prompt("What should the answer have been? (optional — it teaches the assistant)") : null;
      if (rating < 0 && correction === null) return;  // cancelled
      fetch(`/api/messages/${msg.dataset.id}/feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rating, correction }),
      }).then((r) => { if (r.ok) msg.querySelector(".rate").textContent = rating > 0 ? "👍 thanks" : "👎 noted"; });
    }
  });

  box.addEventListener("click", async (e) => {
    const b = e.target.closest(".confirm button");
    if (!b) return;
    const wrap = b.closest(".confirm");
    wrap.querySelectorAll("button").forEach((x) => (x.disabled = true));
    const res = await fetch(`/api/agent/pending/${wrap.dataset.id}`, {
      method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ decision: b.dataset.act }),
    });
    const data = await res.json().catch(() => ({}));
    wrap.insertAdjacentText("beforeend", res.ok ? ` — ${data.status}` : ` — ${data.error || "failed"}`);
  });
  // Tell the server what we've seen (unread badges); at most every 2 s.
  let readTimer = null;
  function markRead() {
    if (readTimer || document.hidden) return;
    readTimer = setTimeout(() => {
      readTimer = null;
      fetch(`/api/rooms/${encodeURIComponent(room)}/read`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ last_id: lastId }),
      }).catch(() => {});
    }, 2000);
  }
  document.addEventListener("visibilitychange", markRead);

  function timeStr(ts) {
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ""; }
  }

  // Live "Tower is thinking… / 🔧 tool…" line while the assistant works.
  const agentStatus = document.getElementById("agent-status");
  let agentTimer = null;
  function showAgentStatus(text, streaming) {
    if (!agentStatus) return;
    agentStatus.textContent = text;
    agentStatus.classList.toggle("streaming", !!streaming);
    agentStatus.hidden = false;
    clearTimeout(agentTimer);
    agentTimer = setTimeout(() => { agentStatus.hidden = true; }, 120000);
  }

  function renderOne(m) {
    if (m.role === "assistant" && agentStatus) agentStatus.hidden = true;
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
      `<div class="body">${body(m.content)}</div>` +
      (m.role === "assistant" && window.towerSpeak ? `<button class="say" title="Read aloud">🔊</button>` : "") +
      (m.role === "assistant" ? `<span class="rate"><button data-r="1" title="Good answer">👍</button>` +
        `<button data-r="-1" title="Bad answer — optionally say what it should have been">👎</button></span>` : "");
    el.dataset.raw = m.content;
    el.dataset.id = m.id;
    box.appendChild(el);
    if (nearBottom) box.scrollTop = box.scrollHeight;
    markRead();
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
      try {
        const m = JSON.parse(ev.data);
        if (m.type === "agent_status") showAgentStatus(m.text);
        else if (m.type === "agent_stream") showAgentStatus(m.text, true);
        else renderOne(m);
      } catch (e) {}
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
