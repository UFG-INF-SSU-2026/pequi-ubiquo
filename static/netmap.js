// Network map client: trigger scans, render the reachability topology + device
// table, and request LLM analysis. Self-contained SVG (no external libs).
(function () {
  const scanBtn = document.getElementById("scanBtn");
  const anBtn = document.getElementById("anBtn");
  const statusEl = document.getElementById("nmStatus");
  const summaryEl = document.getElementById("summary");
  const topoEl = document.getElementById("topo");
  const devsEl = document.getElementById("devs");
  const anOut = document.getElementById("anOut");
  const diffEl = document.getElementById("diff");
  const historyEl = document.getElementById("history");

  function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }
  function rttColor(r) { if (r == null) return "#8b95a3"; if (r < 30) return "#46c178"; if (r < 100) return "#e0b341"; return "#e0554b"; }
  function ts(t) { try { return new Date(t * 1000).toLocaleString(); } catch (e) { return "" + t; } }

  function renderDiff(diff, label) {
    if (!diff) { diffEl.hidden = true; return; }
    const c = diff.counts;
    if (!c.added && !c.removed && !c.changed) {
      diffEl.hidden = false;
      diffEl.innerHTML = `<div class="nm-diff-head">${esc(label || "Changes")}: <span class="muted">no changes</span></div>`;
      return;
    }
    let html = `<div class="nm-diff-head">${esc(label || "Changes")}: ` +
      `<span class="d-add">+${c.added} new</span> · <span class="d-rem">−${c.removed} gone</span> · <span class="d-chg">${c.changed} changed</span></div>`;
    if (diff.added.length) html += `<div class="d-sec"><b class="d-add">New devices</b>` +
      diff.added.map(d => `<div>${esc(d.ip)} ${esc(d.vendor || d.mac || "")} <span class="muted">rtt ${d.rtt_ms == null ? "—" : d.rtt_ms + "ms"}, ports ${d.ports.join(",") || "—"}</span></div>`).join("") + `</div>`;
    if (diff.removed.length) html += `<div class="d-sec"><b class="d-rem">Gone</b>` +
      diff.removed.map(d => `<div>${esc(d.ip)} ${esc(d.vendor || d.mac || "")}</div>`).join("") + `</div>`;
    if (diff.changed.length) html += `<div class="d-sec"><b class="d-chg">Changed</b>` +
      diff.changed.map(d => {
        const ch = d.changes; const bits = [];
        if (ch.rtt_ms) bits.push(`rtt ${ch.rtt_ms[0] ?? "—"}→${ch.rtt_ms[1] ?? "—"}ms`);
        if (ch.ports) bits.push(`ports ${ch.ports.opened.length ? "+" + ch.ports.opened.join(",") : ""}${ch.ports.closed.length ? " −" + ch.ports.closed.join(",") : ""}`);
        if (ch.mac) bits.push(`MAC changed`);
        if (ch.names) bits.push(`names changed`);
        return `<div>${esc(d.ip)} ${esc(d.vendor || "")} <span class="muted">${esc(bits.join("; "))}</span></div>`;
      }).join("") + `</div>`;
    diffEl.hidden = false;
    diffEl.innerHTML = html;
  }

  async function loadHistory() {
    try {
      const rows = await (await fetch("/netmap/history")).json();
      if (!rows.length) { historyEl.innerHTML = "<p class='muted'>No scans recorded.</p>"; return; }
      const latest = rows[0].id;
      historyEl.innerHTML = "<table class='iot-table'><thead><tr><th>When</th><th>Devices</th><th></th></tr></thead><tbody>" +
        rows.map(r => `<tr><td>${esc(ts(r.ts))}</td><td>${r.device_count == null ? "—" : r.device_count}</td>` +
          `<td>${r.id === latest ? "<span class='muted'>latest</span>" : `<button class="btn nm-cmp" data-a="${r.id}" data-b="${latest}">compare → latest</button>`}</td></tr>`).join("") +
        "</tbody></table>";
      historyEl.querySelectorAll(".nm-cmp").forEach(b => b.addEventListener("click", async () => {
        const res = await (await fetch(`/netmap/diff?a=${b.dataset.a}&b=${b.dataset.b}`)).json();
        renderDiff(res.diff, "This scan vs latest");
        diffEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }));
    } catch (e) { historyEl.innerHTML = "<p class='muted'>history unavailable</p>"; }
  }

  function render(scan) {
    if (!scan || !scan.devices) { summaryEl.innerHTML = "<p class='muted'>No scan yet — click <b>Scan now</b>.</p>"; return; }
    const h = scan.host, w = scan.wifi;
    summaryEl.innerHTML =
      `<div class="nm-cards">
        <div class="nm-card"><span>Host</span><b>${esc(h.hostname)}</b>${esc(h.ip)}</div>
        <div class="nm-card"><span>Subnet</span><b>${esc(h.subnet || "?")}</b>gw ${esc(scan.gateway || "?")}</div>
        <div class="nm-card"><span>Devices</span><b>${scan.device_count}</b>${scan.duration_s}s scan</div>
        <div class="nm-card"><span>Wi-Fi</span><b>${w ? esc(w.signal || "?") : "wired"}</b>${w ? esc((w.ssid || "") + " ch " + (w.channel || "?")) : "no WLAN"}</div>
      </div>`;
    drawTopo(scan);
    drawTable(scan);
  }

  function drawTopo(scan) {
    const W = 640, H = 420, cx = W / 2, cy = H / 2, R = 150;
    const gw = scan.devices.find(d => d.roles.includes("gateway"));
    const leaves = scan.devices.filter(d => d !== gw);
    const svg = [`<svg viewBox="0 0 ${W} ${H}" class="topo-svg" role="img" aria-label="network topology">`];
    const hubX = cx, hubY = cy;
    leaves.forEach((d, i) => {
      const a = (2 * Math.PI * i) / Math.max(1, leaves.length) - Math.PI / 2;
      const x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
      d._x = x; d._y = y;
      svg.push(`<line x1="${hubX}" y1="${hubY}" x2="${x}" y2="${y}" stroke="${rttColor(d.rtt_ms)}" stroke-width="2" opacity="0.7"/>`);
    });
    function node(d, x, y, hub) {
      const role = d.roles[0] || "";
      const fill = hub ? "#2f7a4a" : d.roles.includes("this-host") ? "#4f8cff" : d.roles.includes("llm") ? "#7c5cff" : "#1f242d";
      const label = (d.vendor || d.names[0] || "").slice(0, 14);
      return `<g><circle cx="${x}" cy="${y}" r="${hub ? 26 : 20}" fill="${fill}" stroke="#2a313c"/>` +
        `<text x="${x}" y="${y - (hub ? 34 : 28)}" text-anchor="middle" class="nm-ip">${esc(d.ip)}</text>` +
        (role ? `<text x="${x}" y="${y + 4}" text-anchor="middle" class="nm-badge">${esc(role[0].toUpperCase())}</text>` : "") +
        (label ? `<text x="${x}" y="${y + (hub ? 42 : 36)}" text-anchor="middle" class="nm-vendor">${esc(label)}</text>` : "") +
        (d.rtt_ms != null && !hub ? `<text x="${x}" y="${y + (hub ? 56 : 50)}" text-anchor="middle" class="nm-rtt" fill="${rttColor(d.rtt_ms)}">${d.rtt_ms}ms</text>` : "") +
        `</g>`;
    }
    if (gw) svg.push(node(gw, hubX, hubY, true));
    else svg.push(`<circle cx="${hubX}" cy="${hubY}" r="26" fill="#2f7a4a"/><text x="${hubX}" y="${hubY+4}" text-anchor="middle" class="nm-badge">GW</text>`);
    leaves.forEach(d => svg.push(node(d, d._x, d._y, false)));
    svg.push("</svg>");
    topoEl.innerHTML = svg.join("");
  }

  function drawTable(scan) {
    const rows = scan.devices.map(d =>
      `<tr>
        <td>${esc(d.ip)} ${d.roles.map(r => `<span class="tag">${esc(r)}</span>`).join(" ")}</td>
        <td class="muted">${esc(d.mac || "—")}</td>
        <td>${esc(d.vendor || "—")}</td>
        <td style="color:${rttColor(d.rtt_ms)}">${d.rtt_ms == null ? "—" : d.rtt_ms + " ms"}</td>
        <td>${d.ports.length ? d.ports.join(", ") : "—"}</td>
        <td>${esc((d.names || []).join(", "))}</td>
      </tr>`).join("");
    devsEl.innerHTML =
      `<table class="iot-table"><thead><tr><th>IP</th><th>MAC</th><th>Vendor</th><th>RTT</th><th>Open ports</th><th>Names</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function scan() {
    scanBtn.disabled = true; statusEl.textContent = "scanning…";
    try {
      const r = await fetch("/netmap/scan", { method: "POST", headers: { Accept: "application/json" } });
      const data = await r.json();
      render(data);
      renderDiff(data.diff, "Since previous scan");
      loadHistory();
      statusEl.textContent = "done";
    } catch (e) { statusEl.textContent = "scan failed"; }
    finally { scanBtn.disabled = false; }
  }

  async function analyze() {
    anBtn.disabled = true; anOut.hidden = false; anOut.textContent = "Analyzing telemetry with the local model…";
    try {
      const r = await fetch("/netmap/analyze", { method: "POST", headers: { Accept: "application/json" } });
      const data = await r.json();
      anOut.textContent = data.analysis || data.error || "no response";
    } catch (e) { anOut.textContent = "analysis failed"; }
    finally { anBtn.disabled = false; }
  }

  scanBtn.addEventListener("click", scan);
  anBtn.addEventListener("click", analyze);

  render(window.__SCAN__);
  loadHistory();
  if (window.__SCAN__) {
    // Show what changed between the two most recent stored scans on load.
    fetch("/netmap/diff").then(r => r.ok ? r.json() : null)
      .then(res => { if (res && res.a_present) renderDiff(res.diff, "Since previous scan"); })
      .catch(() => {});
  }
})();
