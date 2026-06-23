(function () {
  const config = window.__folioFormData;
  const form = document.getElementById("reviewForm");
  if (!config || !form) return;

  const saveDraftBtn = document.getElementById("saveDraftBtn");
  const approveBtn = document.getElementById("approveBtn");
  const rejectBtn = document.getElementById("rejectBtn");
  const rejectWrap = document.getElementById("rejectReasonWrap");
  const rejectReasonInput = document.getElementById("rejectReasonInput");
  const confirmRejectBtn = document.getElementById("confirmRejectBtn");
  const cancelRejectBtn = document.getElementById("cancelRejectBtn");
  const savedIndicator = document.getElementById("savedIndicator");
  const resendEmailBtn = document.getElementById("resendEmailBtn");
  const ocrToggleBtn = document.getElementById("ocrToggleBtn");
  const ocrRawBlock = document.getElementById("ocrRawBlock");
  const summaryTotalDisplay = document.getElementById("summaryTotalDisplay");
  const completedCounter = document.getElementById("completedCounter");
  const attentionCounter = document.getElementById("attentionCounter");
  const attentionLine = document.getElementById("attentionCounterLine");

  const REQUIRED_FIELDS = [
    "dateOfHospitality",
    "locationOfHospitality",
    "host",
    "occasion",
    "invoiceAmount",
    "totalAmount",
    "merchant",
  ];

  function getFieldValue(name) {
    const el = form.elements[name];
    if (!el) return "";
    return (el.value || "").toString().trim();
  }

  function parseFloatSafe(value) {
    const numeric = Number.parseFloat(value);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  function getPayload() {
    return {
      action: "profile",
      type: getFieldValue("type"),
      expenseCategory: getFieldValue("expenseCategory"),
      host: getFieldValue("host"),
      hostedPersons: getFieldValue("hostedPersons"),
      occasion: getFieldValue("occasion"),
      dateOfHospitality: getFieldValue("dateOfHospitality"),
      locationOfHospitality: getFieldValue("locationOfHospitality"),
      invoiceAmount: getFieldValue("invoiceAmount") || null,
      tip: getFieldValue("tip") || null,
      totalAmount: getFieldValue("totalAmount") || null,
      merchant: getFieldValue("merchant"),
      receiptNumber: getFieldValue("receiptNumber"),
      date: getFieldValue("date") || null,
      place: getFieldValue("place"),
      address: getFieldValue("address"),
    };
  }

  function highlightRules() {
    const missing = new Set(config.missingFields || []);
    const confidence = config.aiConfidence || {};
    const lowConfidenceFields = new Set(
      Object.keys(confidence).filter(function (key) {
        const value = Number(confidence[key] || 0);
        return value > 0 && value < 0.5;
      })
    );

    form.querySelectorAll("[data-field]").forEach(function (container) {
      const field = container.getAttribute("data-field");
      const labelLine = container.querySelector(".field-label-line");
      container.classList.toggle("field-missing", missing.has(field));
      container.classList.toggle("field-low-confidence", lowConfidenceFields.has(field));

      if (labelLine && missing.has(field) && !labelLine.querySelector(".required-badge")) {
        const badge = document.createElement("span");
        badge.className = "required-badge";
        badge.textContent = "Required";
        labelLine.appendChild(badge);
      }
      if (labelLine && lowConfidenceFields.has(field) && !labelLine.querySelector(".low-confidence-hint")) {
        const hint = document.createElement("span");
        hint.className = "low-confidence-hint";
        hint.title = "Low confidence — please verify";
        hint.textContent = "ⓘ";
        labelLine.appendChild(hint);
      }
    });
  }

  function recalcTotal() {
    const invoice = parseFloatSafe(getFieldValue("invoiceAmount"));
    const tip = parseFloatSafe(getFieldValue("tip"));
    const total = (invoice + tip).toFixed(2);
    form.elements.totalAmount.value = total;
    summaryTotalDisplay.textContent = total;
  }

  function validateAndToggleApprove() {
    const payload = getPayload();
    let completed = 0;
    let missingCount = 0;
    REQUIRED_FIELDS.forEach(function (field) {
      const value = payload[field];
      const hasValue = !(value === null || value === undefined || String(value).trim() === "");
      if (hasValue) {
        completed += 1;
      } else {
        missingCount += 1;
      }
    });
    approveBtn.disabled = false;
    completedCounter.textContent = String(completed);
    attentionCounter.textContent = String(missingCount);
    attentionLine.hidden = missingCount === 0;
  }

  async function saveDraft(showToast) {
    const response = await fetch("/forms/" + config.formId + "/save-draft", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(getPayload()),
    });
    await response.json();
    if (!response.ok) return;
    highlightRules();
    validateAndToggleApprove();
    if (showToast) {
      savedIndicator.classList.add("visible");
      if (window.showToast) {
        window.showToast("Draft saved", "info", 2000);
      }
      window.setTimeout(function () {
        savedIndicator.classList.remove("visible");
      }, 3000);
    }
  }

  ocrToggleBtn?.addEventListener("click", function () {
    ocrRawBlock.hidden = !ocrRawBlock.hidden;
    ocrToggleBtn.textContent = ocrRawBlock.hidden ? "View extracted text ▾" : "View extracted text ▴";
  });

  form.addEventListener("input", function (event) {
    if (event.target && (event.target.name === "invoiceAmount" || event.target.name === "tip")) {
      recalcTotal();
    }
    validateAndToggleApprove();
  });

  saveDraftBtn?.addEventListener("click", function () {
    saveDraft(true);
  });

  approveBtn?.addEventListener("click", async function () {
    const response = await fetch("/forms/" + config.formId + "/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(getPayload()),
    });
    const data = await response.json();
    if (response.ok && data.redirectUrl) {
      window.location.href = data.redirectUrl;
    }
  });

  rejectBtn?.addEventListener("click", function () {
    rejectWrap.classList.toggle("show");
  });

  confirmRejectBtn?.addEventListener("click", async function () {
    const reason = (rejectReasonInput.value || "").trim();
    if (!reason) return;
    const response = await fetch("/forms/" + config.formId + "/reject", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ reason: reason }),
    });
    const data = await response.json();
    if (response.ok) {
      window.location.href = data.redirectUrl || "/receipts/new";
    }
  });

  cancelRejectBtn?.addEventListener("click", function () {
    rejectWrap.classList.remove("show");
    rejectReasonInput.value = "";
  });

  resendEmailBtn?.addEventListener("click", async function () {
    if (!config.documentId) return;
    const response = await fetch("/documents/" + config.documentId + "/resend-email", {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    if (response.ok) {
      window.location.reload();
    }
  });

  if (!config.isReadOnly) {
    setInterval(function () {
      saveDraft(false);
    }, 30000);
  }

  recalcTotal();
  highlightRules();
  if (!config.isReadOnly) {
    validateAndToggleApprove();
  }
})();
