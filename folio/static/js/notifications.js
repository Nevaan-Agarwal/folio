(function () {
  const toastTypes = {
    success: { border: "var(--success)", icon: "✓" },
    error: { border: "var(--error)", icon: "✕" },
    warning: { border: "var(--warning)", icon: "⚠" },
    info: { border: "var(--accent-primary)", icon: "i" },
  };

  function ensureToastContainer() {
    let container = document.getElementById("folioToastContainer");
    if (container) return container;
    container = document.createElement("div");
    container.id = "folioToastContainer";
    container.style.position = "fixed";
    container.style.right = "16px";
    container.style.bottom = "16px";
    container.style.zIndex = "9999";
    container.style.display = "grid";
    container.style.gap = "10px";
    document.body.appendChild(container);
    return container;
  }

  window.showToast = function showToast(message, type, duration) {
    const toastType = toastTypes[type] ? type : "info";
    const timeout = Number(duration) > 0 ? Number(duration) : 4000;
    const palette = toastTypes[toastType];
    const container = ensureToastContainer();
    const toast = document.createElement("article");
    toast.style.background = "var(--bg-surface)";
    toast.style.borderLeft = "4px solid " + palette.border;
    toast.style.borderRadius = "var(--radius-md)";
    toast.style.padding = "14px 18px";
    toast.style.boxShadow = "var(--shadow-card)";
    toast.style.minWidth = "300px";
    toast.style.maxWidth = "360px";
    toast.style.color = "var(--text-primary)";
    toast.style.transform = "translateX(120%)";
    toast.style.opacity = "0";
    toast.style.transition = "transform .22s ease, opacity .22s ease";
    toast.innerHTML =
      '<div style="display:flex;gap:10px;align-items:flex-start;">' +
      '<span style="font-weight:700;color:' +
      palette.border +
      ';">' +
      palette.icon +
      "</span>" +
      "<span>" +
      String(message || "").replace(/</g, "&lt;") +
      "</span></div>";
    container.appendChild(toast);
    window.requestAnimationFrame(function () {
      toast.style.transform = "translateX(0)";
      toast.style.opacity = "1";
    });
    window.setTimeout(function () {
      toast.style.transform = "translateX(120%)";
      toast.style.opacity = "0";
      window.setTimeout(function () {
        toast.remove();
      }, 220);
    }, timeout);
  };

  const bell = document.getElementById("notificationBellBtn");
  const badge = document.getElementById("notificationBadge");
  const dropdown = document.getElementById("notificationDropdown");
  const list = document.getElementById("notificationList");
  const markAll = document.getElementById("notificationMarkAllRead");
  if (!bell || !badge || !dropdown || !list || !markAll) return;

  function iconClass(icon) {
    return ["receipt", "pdf", "error", "email", "form", "review", "info"].includes(icon)
      ? icon
      : "info";
  }

  function updateBadge(count) {
    const value = Number(count) || 0;
    badge.textContent = String(value);
    badge.hidden = value <= 0;
  }

  async function fetchUnreadCount() {
    try {
      const response = await fetch("/api/notifications/count", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const payload = await response.json();
      updateBadge(payload.unreadCount || 0);
    } catch (_error) {}
  }

  async function markRead(notificationId) {
    await fetch("/api/notifications/" + notificationId + "/read", {
      method: "POST",
      headers: { Accept: "application/json" },
    });
  }

  function timeLabel(timestamp) {
    if (!timestamp) return "";
    try {
      const dt = new Date(timestamp);
      if (Number.isNaN(dt.getTime())) return String(timestamp);
      return dt.toLocaleString();
    } catch (_error) {
      return String(timestamp);
    }
  }

  function renderNotifications(items) {
    if (!Array.isArray(items) || items.length === 0) {
      list.innerHTML = '<p class="notif-empty">No notifications yet.</p>';
      return;
    }
    list.innerHTML = items
      .map(function (item) {
        return (
          '<button class="notif-item" type="button" data-id="' +
          item.id +
          '" data-link="' +
          (item.link || "/auth/dashboard") +
          '">' +
          '<span class="notif-icon ' +
          iconClass(item.icon) +
          '">' +
          (item.icon === "error" ? "!" : item.icon === "pdf" ? "PDF" : "•") +
          "</span>" +
          "<span>" +
          '<p class="notif-message">' +
          String(item.message || "").replace(/</g, "&lt;") +
          "</p>" +
          '<p class="notif-time">' +
          timeLabel(item.timestamp) +
          "</p>" +
          "</span>" +
          '<span class="notif-unread-dot" ' +
          (item.isRead ? "hidden" : "") +
          "></span>" +
          "</button>"
        );
      })
      .join("");

    list.querySelectorAll(".notif-item").forEach(function (button) {
      button.addEventListener("click", async function () {
        const id = button.getAttribute("data-id");
        const link = button.getAttribute("data-link") || "/auth/dashboard";
        try {
          await markRead(id);
        } finally {
          window.location.href = link;
        }
      });
    });
  }

  async function fetchNotifications() {
    try {
      const response = await fetch("/api/notifications", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const items = await response.json();
      renderNotifications(items);
      const unread = (items || []).filter(function (item) {
        return !item.isRead;
      }).length;
      updateBadge(unread);
    } catch (_error) {}
  }

  bell.addEventListener("click", async function () {
    const isHidden = dropdown.hidden;
    dropdown.hidden = !isHidden;
    if (isHidden) {
      await fetchNotifications();
    }
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest("#notificationCenter")) {
      dropdown.hidden = true;
    }
  });

  markAll.addEventListener("click", async function () {
    try {
      await fetch("/api/notifications/read-all", {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      await fetchNotifications();
    } catch (_error) {}
  });

  fetchUnreadCount();
  window.setInterval(fetchUnreadCount, 60000);
})();
