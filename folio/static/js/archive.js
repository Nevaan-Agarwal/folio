(function () {
  function initArchive() {
  const root = document.getElementById("archiveRoot");
  if (!root) return;

  const grid = document.getElementById("archiveGrid");
  const empty = document.getElementById("archiveEmpty");
  const showing = document.getElementById("archiveShowing");
  const total = document.getElementById("archiveTotal");
  const loadMoreBtn = document.getElementById("archiveLoadMore");
  const filtersForm = document.getElementById("archiveFilters");
  const clearBtn = document.getElementById("clearArchiveFilters");

  const i18n = window.__archiveI18n || {};
  const state = {
    items: Array.isArray(window.__archiveInitial) ? window.__archiveInitial.slice() : [],
    nextCursor: root.getAttribute("data-next-cursor") || "",
  };

  function categoryClass(category) {
    const normalized = (category || "").toLowerCase();
    if (normalized === "restaurant") return "cat-restaurant";
    if (normalized === "travel") return "cat-travel";
    if (normalized === "hotel") return "cat-hotel";
    if (normalized === "business meal") return "cat-business-meal";
    return "cat-other";
  }

  function statusBadge(status) {
    const value = (status || "").toLowerCase();
    if (value === "completed" || value === "pdf_generated") return '<span class="badge-success">' + (i18n.completed || "Completed") + "</span>";
    if (value === "error") return '<span class="badge-error">' + (i18n.error || "Error") + "</span>";
    return '<span class="badge-warning">' + (i18n.processing || "Processing") + "</span>";
  }

  function cardHtml(item) {
    const totalAmount = Number(item.totalAmount || 0).toFixed(2);
    const occasion = item.occasion || "-";
    return (
      '<article class="archive-card" data-merchant="' +
      (item.merchant || "").toLowerCase() +
      '" data-date="' +
      (item.date || "") +
      '" data-category="' +
      (item.category || "") +
      '">' +
      '<div data-href="/archive/' +
      (item.documentId || item.id) +
      '">' +
      '<div class="archive-top">' +
      '<img class="archive-thumb" src="' +
      (item.thumbnailUrl || "") +
      '" alt="' + (i18n.receiptThumbnail || "Receipt thumbnail") + '" />' +
      "<div style=\"min-width:0;flex:1;\">" +
      '<p class="archive-merchant">' +
      (item.merchant || i18n.unknownMerchant || "Unknown Merchant") +
      "</p>" +
      '<p class="archive-date">' +
      (item.date || "-") +
      "</p>" +
      "</div></div>" +
      '<div class="archive-middle"><span class="cat-pill ' +
      categoryClass(item.category) +
      '">' +
      (item.category || i18n.other || "Other") +
      "</span></div>" +
      '<p class="archive-occasion">' +
      occasion +
      "</p>" +
      '<div class="archive-bottom"><span class="archive-total">' + (i18n.currencyCode || "EUR") + " " +
      totalAmount +
      "</span>" +
      statusBadge(item.status) +
      '<a class="archive-pdf-btn" href="' +
      (item.pdfUrl || "#") +
      '" target="_blank" rel="noopener">' + (i18n.pdfLabel || "PDF") + "</a></div>" +
      "</div>" +
      "</article>"
    );
  }

  function render() {
    grid.innerHTML = state.items.map(cardHtml).join("");
    grid.querySelectorAll("[data-href]").forEach(function (node) {
      node.addEventListener("click", function (event) {
        if (event.target && event.target.closest(".archive-pdf-btn")) return;
        const href = node.getAttribute("data-href");
        if (href) window.location.href = href;
      });
    });
    const visible = state.items.length;
    showing.textContent = String(visible);
    empty.hidden = visible > 0;
    loadMoreBtn.hidden = !state.nextCursor;
  }

  function buildQueryForLoadMore() {
    const params = new URLSearchParams(new FormData(filtersForm));
    params.set("cursor", state.nextCursor);
    return params.toString();
  }

  async function loadMore() {
    if (!state.nextCursor) return;
    const response = await fetch("/archive/data?" + buildQueryForLoadMore(), {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return;
    const data = await response.json();
    if (!data.success) return;
    state.items = state.items.concat(data.results || []);
    state.nextCursor = data.nextCursor || "";
    if (typeof data.total === "number") {
      total.textContent = String(data.total);
      document.getElementById("archiveSubmissionCount").textContent = String(data.total);
    }
    render();
  }

  clearBtn?.addEventListener("click", function () {
    filtersForm.reset();
  });

  loadMoreBtn?.addEventListener("click", loadMore);

  render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initArchive);
  } else {
    initArchive();
  }
})();

(function () {
  const root = document.getElementById("globalSearchRoot");
  const input = document.getElementById("globalSearchInput");
  const icon = document.getElementById("globalSearchIcon");
  const dropdown = document.getElementById("globalSearchDropdown");
  if (!root || !input || !icon || !dropdown) return;

  const RECENT_KEY = "folio_recent_searches";
  let latestQuery = "";

  function loadRecentSearches() {
    try {
      const parsed = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(parsed) ? parsed.filter(Boolean).slice(0, 5) : [];
    } catch (_error) {
      return [];
    }
  }

  function saveRecentSearch(term) {
    const normalized = String(term || "").trim();
    if (!normalized) return;
    const current = loadRecentSearches().filter(function (item) {
      return item.toLowerCase() !== normalized.toLowerCase();
    });
    current.unshift(normalized);
    localStorage.setItem(RECENT_KEY, JSON.stringify(current.slice(0, 5)));
  }

  function openDropdown(html) {
    dropdown.innerHTML = html;
    dropdown.hidden = false;
  }

  function closeDropdown() {
    dropdown.hidden = true;
  }

  function goSearch(query) {
    const normalized = String(query || "").trim();
    if (!normalized) return;
    saveRecentSearch(normalized);
    window.location.href = "/search?q=" + encodeURIComponent(normalized);
  }

  const globalI18n = (window.__folioI18n && window.__folioI18n.common) || {};
  function renderRecentSearches() {
    const recent = loadRecentSearches();
    if (!recent.length) {
      openDropdown('<div class="global-search-empty">' + (globalI18n.noRecentSearches || "No recent searches") + "</div>");
      return;
    }
    openDropdown(
      recent
        .map(function (term) {
          return (
            '<button class="global-search-item" type="button" data-search-term="' +
            term.replace(/"/g, "&quot;") +
            '"><strong>' +
            term.replace(/</g, "&lt;") +
            '</strong><div class="meta">' + (globalI18n.recentSearch || "Recent search") + "</div></button>"
          );
        })
        .join("")
    );
    dropdown.querySelectorAll("[data-search-term]").forEach(function (button) {
      button.addEventListener("click", function () {
        const term = button.getAttribute("data-search-term") || "";
        goSearch(term);
      });
    });
  }

  async function fetchAutocomplete(query) {
    const currentQuery = String(query || "").trim();
    if (currentQuery.length < 2) {
      if (!currentQuery) renderRecentSearches();
      else closeDropdown();
      return;
    }
    latestQuery = currentQuery;
    try {
      const response = await fetch(
        "/api/search?q=" + encodeURIComponent(currentQuery) + "&limit=5",
        { headers: { Accept: "application/json" } }
      );
      if (!response.ok) return;
      const payload = await response.json();
      if (latestQuery !== currentQuery) return;
      const results = payload.results || [];
      if (!results.length) {
        openDropdown('<div class="global-search-empty">' + (globalI18n.noQuickMatches || "No quick matches") + "</div>");
        return;
      }
      openDropdown(
        results
          .map(function (item) {
            return (
              '<button class="global-search-item" type="button" data-link="' +
              (item.link || "#") +
              '">' +
              '<div><strong>' +
              String(item.merchant || "-").replace(/</g, "&lt;") +
              '</strong></div>' +
              '<div class="meta">' +
              String(item.date || "-").replace(/</g, "&lt;") +
              " • " + (globalI18n.currencyCode || "EUR") + " " +
              Number(item.amount || 0).toFixed(2) +
              "</div>" +
              "</button>"
            );
          })
          .join("")
      );
      dropdown.querySelectorAll("[data-link]").forEach(function (button) {
        button.addEventListener("click", function () {
          const link = button.getAttribute("data-link");
          if (link) window.location.href = link;
        });
      });
    } catch (_error) {}
  }

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      goSearch(input.value);
    }
  });

  input.addEventListener("focus", function () {
    if (!input.value.trim()) {
      renderRecentSearches();
    }
  });

  input.addEventListener("input", function () {
    fetchAutocomplete(input.value);
  });

  icon.addEventListener("click", function () {
    goSearch(input.value);
  });

  document.addEventListener("click", function (event) {
    if (!event.target.closest("#globalSearchRoot")) {
      closeDropdown();
    }
  });
})();
