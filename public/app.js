(() => {
  "use strict";

  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024; // keep in sync with api/compress.py

  const LEVEL_HINTS = {
    low: "lightest compression — best quality",
    medium: "balanced — good size, good clarity",
    high: "smallest file — more aggressive",
  };

  const dropzone = document.getElementById("dropzone");
  const chooseBtn = document.getElementById("chooseBtn");
  const fileInput = document.getElementById("fileInput");

  const fileInfo = document.getElementById("fileInfo");
  const fileNameEl = document.getElementById("fileName");
  const fileSizeEl = document.getElementById("fileSize");

  const levelSelect = document.getElementById("levelSelect");
  const levelBtns = Array.from(document.querySelectorAll(".level-btn"));
  const levelHint = document.getElementById("levelHint");

  const compressBtn = document.getElementById("compressBtn");
  const errorMsg = document.getElementById("errorMsg");

  const panelUpload = document.getElementById("panel-upload");
  const panelLoading = document.getElementById("panel-loading");
  const panelResult = document.getElementById("panel-result");
  const loadingText = document.getElementById("loadingText");

  const resOriginal = document.getElementById("resOriginal");
  const resCompressed = document.getElementById("resCompressed");
  const resPercent = document.getElementById("resPercent");
  const downloadBtn = document.getElementById("downloadBtn");
  const resetBtn = document.getElementById("resetBtn");

  let selectedFile = null;
  let selectedLevel = "medium";
  let pendingBlobUrl = null;
  let pendingFilename = null;

  // ---------- helpers ----------

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    const mb = kb / 1024;
    return `${mb.toFixed(2)} MB`;
  }

  function showError(msg) {
    errorMsg.textContent = msg;
    errorMsg.classList.remove("hidden");
  }

  function clearError() {
    errorMsg.textContent = "";
    errorMsg.classList.add("hidden");
  }

  function resetToUpload() {
    selectedFile = null;
    fileInput.value = "";
    fileInfo.classList.add("hidden");
    levelSelect.classList.add("hidden");
    compressBtn.classList.add("hidden");
    clearError();

    if (pendingBlobUrl) {
      URL.revokeObjectURL(pendingBlobUrl);
      pendingBlobUrl = null;
    }
    pendingFilename = null;

    panelResult.classList.add("hidden");
    panelLoading.classList.add("hidden");
    panelUpload.classList.remove("hidden");
  }

  function handleFile(file) {
    clearError();

    if (!file) return;

    const looksLikePdf =
      file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

    if (!looksLikePdf) {
      showError("that doesn't look like a pdf. try another file.");
      return;
    }

    if (file.size > MAX_UPLOAD_BYTES) {
      showError(`file is too large. max upload is ${formatBytes(MAX_UPLOAD_BYTES)}.`);
      return;
    }

    selectedFile = file;
    fileNameEl.textContent = file.name;
    fileSizeEl.textContent = formatBytes(file.size);

    fileInfo.classList.remove("hidden");
    levelSelect.classList.remove("hidden");
    compressBtn.classList.remove("hidden");
  }

  // ---------- upload interactions ----------

  chooseBtn.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("click", (e) => {
    if (e.target === chooseBtn) return;
    fileInput.click();
  });
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) {
      handleFile(fileInput.files[0]);
    }
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("drag-over");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("drag-over");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files[0]) {
      handleFile(dt.files[0]);
    }
  });

  // ---------- level selection ----------

  levelBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      levelBtns.forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      selectedLevel = btn.dataset.level;
      levelHint.textContent = LEVEL_HINTS[selectedLevel] || "";
    });
  });

  // ---------- compress ----------

  compressBtn.addEventListener("click", async () => {
    if (!selectedFile) return;
    clearError();

    panelUpload.classList.add("hidden");
    panelLoading.classList.remove("hidden");
    loadingText.textContent = "uploading & processing pages\u2026";

    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("level", selectedLevel);

    try {
      const response = await fetch("/api/compress", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        let message = "compression failed. try a different file.";
        try {
          const errBody = await response.json();
          if (errBody && errBody.error) message = errBody.error;
        } catch (_) {
          /* response wasn't JSON, keep default message */
        }
        panelLoading.classList.add("hidden");
        panelUpload.classList.remove("hidden");
        showError(message);
        return;
      }

      const originalSize = parseInt(response.headers.get("X-Original-Size") || "0", 10);
      const compressedSize = parseInt(response.headers.get("X-Compressed-Size") || "0", 10);
      const percentSaved = response.headers.get("X-Percent-Saved") || "0";
      const filename = response.headers.get("X-Filename") || "compressed.pdf";

      const blob = await response.blob();

      if (pendingBlobUrl) URL.revokeObjectURL(pendingBlobUrl);
      pendingBlobUrl = URL.createObjectURL(blob);
      pendingFilename = filename;

      resOriginal.textContent = formatBytes(originalSize);
      resCompressed.textContent = formatBytes(compressedSize);
      resPercent.textContent = `${percentSaved}%`;

      panelLoading.classList.add("hidden");
      panelResult.classList.remove("hidden");

      triggerDownload(pendingBlobUrl, pendingFilename);
    } catch (err) {
      panelLoading.classList.add("hidden");
      panelUpload.classList.remove("hidden");
      showError("network error — could not reach the compressor.");
    }
  });

  function triggerDownload(blobUrl, filename) {
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  downloadBtn.addEventListener("click", () => {
    if (pendingBlobUrl && pendingFilename) {
      triggerDownload(pendingBlobUrl, pendingFilename);
    }
  });

  resetBtn.addEventListener("click", resetToUpload);
})();
