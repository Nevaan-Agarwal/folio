(function () {
  const initialData = window.__analyticsInitial || {};
  const startDateInput = document.getElementById("analyticsStartDate");
  const endDateInput = document.getElementById("analyticsEndDate");
  const refreshBtn = document.getElementById("analyticsRefreshBtn");
  const downloadBtn = document.getElementById("analyticsDownloadBtn");
  if (!refreshBtn) return;
  const i18n = window.__analyticsI18n || {};

  const palette = [
    "#F5A623",
    "#2DD4A0",
    "#FF5C5C",
    "#3A5490",
    "#8A97B0",
    "#A78BFA",
    "#22D3EE",
    "#F97316",
  ];

  let monthChart = null;
  let categoryChart = null;
  let employeesChart = null;

  function euro(value) {
    return (i18n.currencyCode || "EUR") + " " + Number(value || 0).toFixed(2);
  }

  function renderKpis(data) {
    const kpis = data.kpis || {};
    document.getElementById("kpiTotalSpending").textContent = euro(kpis.total_spending);
    document.getElementById("kpiSubmissions").textContent = String(kpis.total_submissions || 0);
    document.getElementById("kpiAvgExpense").textContent = euro(kpis.average_expense);
    const pending = Number(kpis.pending_review || 0);
    const pendingEl = document.getElementById("kpiPending");
    pendingEl.textContent = String(pending);
    pendingEl.classList.toggle("kpi-pending-alert", pending > 0);
    pendingEl.classList.toggle("kpi-pending-ok", pending === 0);
  }

  function chartDefaults() {
    return {
      plugins: {
        legend: { labels: { color: "#8A97B0" } },
      },
      scales: {
        x: {
          ticks: { color: "#8A97B0" },
          grid: { color: "#2A3F6B" },
        },
        y: {
          ticks: { color: "#8A97B0" },
          grid: { color: "#2A3F6B" },
        },
      },
    };
  }

  function renderCharts(data) {
    const monthLabels = Object.keys(data.spending_by_month || {});
    const monthValues = monthLabels.map(function (key) {
      return Number(data.spending_by_month[key] || 0);
    });
    if (monthChart) monthChart.destroy();
    monthChart = new Chart(document.getElementById("spendingByMonthChart"), {
      type: "line",
      data: {
        labels: monthLabels,
        datasets: [
          {
            label: i18n.spending || "Spending",
            data: monthValues,
            borderColor: "#F5A623",
            backgroundColor: "rgba(245, 166, 35, 0.1)",
            fill: true,
            tension: 0.25,
          },
        ],
      },
      options: chartDefaults(),
    });

    const categoryLabels = Object.keys(data.spending_by_category || {});
    const categoryValues = categoryLabels.map(function (key) {
      return Number(data.spending_by_category[key] || 0);
    });
    if (categoryChart) categoryChart.destroy();
    categoryChart = new Chart(document.getElementById("spendingByCategoryChart"), {
      type: "doughnut",
      data: {
        labels: categoryLabels,
        datasets: [{ data: categoryValues, backgroundColor: palette }],
      },
      options: {
        plugins: {
          legend: { position: "right", labels: { color: "#8A97B0" } },
        },
      },
    });

    const employeeEntries = Object.values(data.spending_by_employee || {}).sort(function (a, b) {
      return Number(b.total || 0) - Number(a.total || 0);
    }).slice(0, 10);
    const employeeLabels = employeeEntries.map(function (entry) {
      const name = entry.name || (i18n.unknown || "Unknown");
      const parts = name.split(" ");
      const initials = parts.map(function (p) { return p[0] || ""; }).join("").slice(0, 2).toUpperCase();
      return initials + " " + name;
    });
    const employeeValues = employeeEntries.map(function (entry) {
      return Number(entry.total || 0);
    });
    if (employeesChart) employeesChart.destroy();
    employeesChart = new Chart(document.getElementById("topEmployeesChart"), {
      type: "bar",
      data: {
        labels: employeeLabels,
        datasets: [{ data: employeeValues, backgroundColor: "#F5A623" }],
      },
      options: Object.assign(chartDefaults(), { indexAxis: "y" }),
    });
  }

  function renderTables(data) {
    const merchantsBody = document.getElementById("topMerchantsTableBody");
    const recentBody = document.getElementById("recentActivityTableBody");
    const merchants = data.top_merchants || [];
    merchantsBody.innerHTML = merchants.map(function (item, idx) {
      return "<tr><td>" + (idx + 1) + "</td><td>" + (item.merchant || "-") + "</td><td>" + euro(item.total) + "</td><td>" + (item.count || 0) + "</td><td>" + euro(item.avg_amount) + "</td></tr>";
    }).join("");
    const activity = data.recent_activity || [];
    recentBody.innerHTML = activity.map(function (item) {
      return "<tr><td>" + (item.employee || "-") + "</td><td>" + (item.merchant || "-") + "</td><td>" + euro(item.amount) + "</td><td>" + (item.category || "-") + "</td><td>" + (item.date || "-") + "</td><td>" + (item.status || "-") + "</td></tr>";
    }).join("");
  }

  function renderAll(data) {
    renderKpis(data);
    renderCharts(data);
    renderTables(data);
  }

  function updateDownloadLink() {
    const params = new URLSearchParams();
    if (startDateInput.value) params.set("start_date", startDateInput.value);
    if (endDateInput.value) params.set("end_date", endDateInput.value);
    downloadBtn.href = "/admin/analytics/export" + (params.toString() ? "?" + params.toString() : "");
  }

  async function refreshData() {
    const params = new URLSearchParams();
    if (startDateInput.value) params.set("start_date", startDateInput.value);
    if (endDateInput.value) params.set("end_date", endDateInput.value);
    const response = await fetch("/admin/analytics/data?" + params.toString(), { headers: { Accept: "application/json" } });
    if (!response.ok) return;
    const payload = await response.json();
    if (!payload.success) return;
    renderAll(payload.data || {});
    updateDownloadLink();
  }

  function applyQuickRange(range) {
    const now = new Date();
    function fmt(d) {
      return d.toISOString().slice(0, 10);
    }
    if (range === "this_month") {
      const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
      startDateInput.value = fmt(start);
      endDateInput.value = fmt(now);
    } else if (range === "last_3_months") {
      const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 2, 1));
      startDateInput.value = fmt(start);
      endDateInput.value = fmt(now);
    } else if (range === "this_year") {
      const start = new Date(Date.UTC(now.getUTCFullYear(), 0, 1));
      startDateInput.value = fmt(start);
      endDateInput.value = fmt(now);
    } else if (range === "all_time") {
      startDateInput.value = "";
      endDateInput.value = "";
    }
    refreshData();
  }

  document.querySelectorAll("[data-range]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyQuickRange(btn.getAttribute("data-range"));
    });
  });
  refreshBtn.addEventListener("click", refreshData);
  renderAll(initialData);
  updateDownloadLink();
})();
