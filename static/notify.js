// Notifications (notifications.py): bell count, unread badges per room, and
// desktop notifications for new items (if the user allowed them). Polls every 15 s.
(function () {
  const bell = document.getElementById("bell-count");
  let since = null;
  async function poll() {
    try {
      const r = await fetch("/api/notifications" + (since !== null ? `?since=${since}` : ""),
                            { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const d = await r.json();
      if (bell) { bell.textContent = d.unread; bell.hidden = !d.unread; }
      document.querySelectorAll("[data-room-badge]").forEach((el) => {
        const n = d.rooms[el.dataset.roomBadge] || 0;
        const here = document.querySelector(".chat-main")?.dataset.room === el.dataset.roomBadge;
        el.textContent = n; el.hidden = !n || here;
      });
      const newest = d.items.length ? d.items[0].id : since;
      if (since !== null && "Notification" in window && Notification.permission === "granted") {
        d.items.filter((n) => !n.read).forEach((n) => {
          const note = new Notification("TowerAI", { body: n.text, tag: "towerai-" + n.id });
          note.onclick = () => { window.focus(); if (n.link) location.href = n.link; };
        });
      }
      since = Math.max(since || 0, newest || 0);
    } catch (e) {}
  }
  poll();
  setInterval(poll, 15000);
})();
