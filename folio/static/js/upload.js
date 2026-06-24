(function () {
  const uploadI18n = window.__uploadI18n || {};
  const processingI18n = uploadI18n.processing || {};
  const uploadText = uploadI18n.upload || {};
  function initProcessingPage() {
    const card = document.getElementById("processingCard");
    if (!card) return;

    const receiptId = card.getAttribute("data-receipt-id");
    const statusMessage = document.getElementById("processingStatusMessage");
    const confidenceWrap = document.getElementById("confidenceWrap");
    const confidenceValue = document.getElementById("confidenceValue");
    const confidenceLabel = document.getElementById("confidenceLabel");
    const ringFill = document.getElementById("confidenceRingFill");
    const errorCard = document.getElementById("processingError");
    const errorMessage = document.getElementById("processingErrorMessage");
    const retryBtn = document.getElementById("retryProcessingBtn");
    let pollTimer = null;
    let pendingRedirect = false;

    function setStepVisual(index, state) {
      const stepEl = document.querySelector('[data-step-index="' + index + '"]');
      if (!stepEl) return;
      stepEl.classList.remove(
        "processing-step-pending",
        "processing-step-active",
        "processing-step-complete",
        "processing-step-error"
      );
      stepEl.classList.add("processing-step-" + state);
      const icon = stepEl.querySelector(".processing-icon");
      if (!icon) return;
      icon.classList.remove("processing-spinner");
      if (state === "pending") {
        icon.textContent = String(index);
      } else if (state === "active") {
        icon.textContent = "";
        icon.classList.add("processing-spinner");
      } else if (state === "complete") {
        icon.textContent = "✓";
      } else if (state === "error") {
        icon.textContent = "✕";
      }
    }

    function renderStepProgress(activeStep, hasError) {
      for (let i = 1; i <= 6; i += 1) {
        if (hasError && i === activeStep) {
          setStepVisual(i, "error");
        } else if (i < activeStep) {
          setStepVisual(i, "complete");
        } else if (i === activeStep && !hasError) {
          setStepVisual(i, "active");
        } else {
          setStepVisual(i, "pending");
        }
      }
    }

    function updateConfidence(confidence) {
      const value = Math.max(0, Math.min(100, Number(confidence) || 0));
      confidenceWrap.hidden = false;
      confidenceValue.textContent = value.toFixed(1);

      const circumference = 2 * Math.PI * 52;
      const offset = circumference - (value / 100) * circumference;
      ringFill.style.strokeDasharray = String(circumference);
      ringFill.style.strokeDashoffset = String(offset);

      if (value >= 70) {
        ringFill.style.stroke = "var(--success)";
        confidenceLabel.textContent = processingI18n.highConfidence || "High confidence";
      } else if (value >= 40) {
        ringFill.style.stroke = "var(--warning)";
        confidenceLabel.textContent = processingI18n.mediumConfidence || "Medium confidence";
      } else {
        ringFill.style.stroke = "var(--error)";
        confidenceLabel.textContent = processingI18n.lowConfidence || "Low confidence — please review carefully";
      }
    }

    async function pollStatus() {
      const response = await fetch("/receipts/" + receiptId + "/status", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();

      statusMessage.textContent = payload.message || processingI18n.updateInProgress || "Processing update in progress.";
      const step = Number(payload.step || 1);

      if (payload.status === "error") {
        renderStepProgress(step || 1, true);
        errorCard.hidden = false;
        errorMessage.textContent = payload.error || payload.message || processingI18n.processingFailed || "Processing failed.";
        if (pollTimer) window.clearInterval(pollTimer);
        return;
      }

      errorCard.hidden = true;
      renderStepProgress(step, false);
      if (typeof payload.confidence !== "undefined") {
        updateConfidence(payload.confidence);
      }

      if (payload.redirectUrl && !pendingRedirect) {
        pendingRedirect = true;
        window.setTimeout(function () {
          window.location.href = payload.redirectUrl;
        }, 1500);
      }
    }

    async function startPipeline() {
      await fetch("/receipts/" + receiptId + "/process", { headers: { Accept: "application/json" } });
      await pollStatus();
      pollTimer = window.setInterval(pollStatus, 2000);
    }

    retryBtn?.addEventListener("click", function () {
      window.location.href = "/receipts/new";
    });

    startPipeline();
  }

  initProcessingPage();

  const dropzone = document.getElementById("receiptDropzone");
  const fileInput = document.getElementById("receiptFileInput");
  const filePreview = document.getElementById("filePreview");
  const filePreviewImage = document.getElementById("filePreviewImage");
  const fileName = document.getElementById("fileName");
  const fileSize = document.getElementById("fileSize");
  const uploadNowBtn = document.getElementById("uploadNowBtn");
  const progressWrap = document.getElementById("uploadProgress");
  const progressFill = document.getElementById("uploadProgressFill");
  const uploadedPreview = document.getElementById("uploadedPreview");
  const uploadedPreviewImage = document.getElementById("uploadedPreviewImage");
  const uploadedPreviewTitle = document.getElementById("uploadedPreviewTitle");
  const uploadedPreviewMeta = document.getElementById("uploadedPreviewMeta");
  const tabs = document.querySelectorAll("[data-tab]");
  const panes = document.querySelectorAll("[data-pane]");
  const cameraPreview = document.getElementById("cameraPreview");
  const cameraCanvas = document.getElementById("cameraCanvas");
  const cameraCaptureBtn = document.getElementById("cameraCaptureBtn");
  const cameraDeniedState = document.getElementById("cameraDeniedState");

  if (!dropzone || !fileInput || !uploadNowBtn) return;

  let selectedFile = null;
  let stream = null;
  let activeTab = "file";
  let uploadInFlight = false;
  let uploadedPreviewObjectUrl = "";

  if (uploadedPreview) {
    uploadedPreview.hidden = true;
  }
  if (uploadedPreviewImage) {
    uploadedPreviewImage.removeAttribute("src");
  }
  if (uploadedPreviewTitle) {
    uploadedPreviewTitle.textContent = "";
  }
  if (uploadedPreviewMeta) {
    uploadedPreviewMeta.textContent = "";
  }

  function showToast(message, type, duration) {
    if (window.showToast) {
      window.showToast(message, type || "info", duration || 4000);
      return;
    }
  }

  function bytesToMb(bytes) {
    return (bytes / (1024 * 1024)).toFixed(2) + " " + (uploadText.mb || "MB");
  }

  function setPreview(file, source) {
    selectedFile = file;
    if (fileName) fileName.textContent = file.name;
    if (fileSize) fileSize.textContent = bytesToMb(file.size);
    if (filePreview) filePreview.hidden = false;
    if (filePreviewImage) {
      const reader = new FileReader();
      reader.onload = function (event) {
        filePreviewImage.src = event.target.result;
      };
      reader.readAsDataURL(file);
    }
    if (source === "file") {
      showUploadedPreview(file);
    }
  }

  function setProgress(percent) {
    progressWrap.hidden = false;
    progressFill.style.width = Math.max(0, Math.min(percent, 100)) + "%";
  }

  function showUploadedPreview(file) {
    if (!uploadedPreview || !uploadedPreviewImage || !uploadedPreviewMeta || !file) return;
    if (uploadedPreviewTitle) {
      uploadedPreviewTitle.textContent = uploadText.uploadComplete || "Upload complete!";
    }
    uploadedPreviewMeta.textContent = (file.name || "receipt") + " • " + bytesToMb(file.size || 0);
    if (uploadedPreviewObjectUrl) {
      URL.revokeObjectURL(uploadedPreviewObjectUrl);
      uploadedPreviewObjectUrl = "";
    }
    uploadedPreviewObjectUrl = URL.createObjectURL(file);
    uploadedPreviewImage.src = uploadedPreviewObjectUrl;
    uploadedPreview.hidden = false;
  }

  function uploadSelectedFile(file) {
    if (uploadInFlight) return;
    if (!file) {
      showToast(uploadText.selectImageWarning || "Please select or capture a receipt image.", "warning");
      return;
    }

    uploadInFlight = true;
    uploadNowBtn.disabled = true;

    const formData = new FormData();
    formData.append("receipt", file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/receipts/upload", true);
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.addEventListener("progress", function (event) {
      if (!event.lengthComputable) return;
      setProgress((event.loaded / event.total) * 100);
    });

    xhr.addEventListener("load", function () {
      setProgress(100);
      let response = {};
      try {
        response = JSON.parse(xhr.responseText || "{}");
      } catch (error) {
        response = {};
      }

      if (xhr.status >= 200 && xhr.status < 300 && response.receiptId) {
        showToast(uploadText.uploadComplete || "Upload complete!", "success", 2200);
        showUploadedPreview(file);
        window.setTimeout(function () {
          window.location.assign("/receipts/" + response.receiptId + "/processing");
        }, 1800);
        return;
      }

      const message = response.message || uploadText.uploadFailed || "Receipt upload failed. Please try again.";
      showToast(message, "error");
      progressWrap.hidden = true;
      progressFill.style.width = "0%";
      uploadInFlight = false;
      uploadNowBtn.disabled = false;
    });

    xhr.addEventListener("error", function () {
      showToast(uploadText.networkUploadError || "Network error while uploading receipt.", "error");
      progressWrap.hidden = true;
      progressFill.style.width = "0%";
      uploadInFlight = false;
      uploadNowBtn.disabled = false;
    });

    xhr.send(formData);
  }

  function resolveSelectedFile() {
    if (selectedFile) return selectedFile;
    if (fileInput.files && fileInput.files[0]) return fileInput.files[0];
    return null;
  }

  function stopCamera() {
    if (!stream) return;
    stream.getTracks().forEach(function (track) {
      track.stop();
    });
    stream = null;
  }

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      cameraDeniedState.hidden = false;
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      cameraPreview.srcObject = stream;
      cameraDeniedState.hidden = true;
    } catch (error) {
      cameraDeniedState.hidden = false;
    }
  }

  function switchTab(tabName) {
    activeTab = tabName;
    tabs.forEach(function (tab) {
      tab.classList.toggle("upload-tab-active", tab.getAttribute("data-tab") === tabName);
    });
    panes.forEach(function (pane) {
      pane.hidden = pane.getAttribute("data-pane") !== tabName;
    });
    if (tabName === "camera") {
      startCamera();
    } else {
      stopCamera();
    }
  }

  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      switchTab(tab.getAttribute("data-tab"));
    });
  });

  fileInput.addEventListener("click", function () {
    // Reset value so re-selecting the same file still triggers "change".
    fileInput.value = "";
  });

  fileInput.addEventListener("change", function () {
    if (fileInput.files && fileInput.files[0]) {
      setPreview(fileInput.files[0], "file");
    }
  });

  dropzone.addEventListener("dragover", function (event) {
    event.preventDefault();
    dropzone.classList.add("is-dragover");
  });

  dropzone.addEventListener("dragleave", function () {
    dropzone.classList.remove("is-dragover");
  });

  dropzone.addEventListener("drop", function (event) {
    event.preventDefault();
    dropzone.classList.remove("is-dragover");
    const file = event.dataTransfer && event.dataTransfer.files ? event.dataTransfer.files[0] : null;
    if (file) setPreview(file, "file");
  });

  cameraCaptureBtn?.addEventListener("click", function () {
    if (!cameraPreview || !cameraCanvas) return;
    const width = cameraPreview.videoWidth || 1280;
    const height = cameraPreview.videoHeight || 720;
    cameraCanvas.width = width;
    cameraCanvas.height = height;
    const ctx = cameraCanvas.getContext("2d");
    ctx.drawImage(cameraPreview, 0, 0, width, height);
    cameraCanvas.toBlob(
      function (blob) {
        if (!blob) return;
        const file = new File([blob], "camera-capture.jpg", { type: "image/jpeg" });
        setPreview(file, "camera");
        switchTab("file");
      },
      "image/jpeg",
      0.92
    );
  });

  uploadNowBtn.addEventListener("click", function () {
    uploadSelectedFile(resolveSelectedFile());
  });

  window.addEventListener("beforeunload", function () {
    stopCamera();
    if (uploadedPreviewObjectUrl) {
      URL.revokeObjectURL(uploadedPreviewObjectUrl);
      uploadedPreviewObjectUrl = "";
    }
  });
})();
