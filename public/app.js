(() => {
  "use strict";

  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024; // keep in sync with api/compress.py
  const FILENAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";

  function randomAlphaFilename() {
    const length = 10 + Math.floor(Math.random() * 7); // 10-16 chars
    let name = "";
    for (let i = 0; i < length; i++) {
      name += FILENAME_CHARS[Math.floor(Math.random() * FILENAME_CHARS.length)];
    }
    return `${name}.pdf`;
  }

  function strengthHint(strength) {
    if (strength <= 30) return "lightest compression — best quality";
    if (strength <= 65) return "balanced — good size, good clarity";
    return "smallest file — more aggressive";
  }

  // Client-side heuristic only — a real prediction would require actually
  // compressing the file. This gives instant feedback while dragging the
  // slider; the exact number always comes from the server after compressing.
  function estimateRatio(strength) {
    const t = strength / 100;
    return 0.65 * Math.pow(1 - t, 1.8) + 0.02;
  }

  const dropzone = document.getElementById("dropzone");
  const chooseBtn = document.getElementById("chooseBtn");
  const fileInput = document.getElementById("fileInput");

  const fileInfo = document.getElementById("fileInfo");
  const fileNameEl = document.getElementById("fileName");
  const fileSizeEl = document.getElementById("fileSize");

  const levelSelect = document.getElementById("levelSelect");
  const levelBtns = Array.from(document.querySelectorAll(".level-btn"));
  const strengthSlider = document.getElementById("strengthSlider");
  const strengthValueEl = document.getElementById("strengthValue");
  const strengthDescEl = document.getElementById("strengthDesc");
  const estimateValueEl = document.getElementById("estimateValue");

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
  let selectedStrength = 50;
  let pendingBlobUrl = null;

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

    panelResult.classList.add("hidden");
    panelLoading.classList.add("hidden");
    panelUpload.classList.remove("hidden");

    setStrength(50);
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

    setStrength(selectedStrength); // populate the KB estimate for this file
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

  // ---------- compression strength (slider + presets) ----------

  function setStrength(value, { syncPresets = true } = {}) {
    selectedStrength = Math.max(0, Math.min(100, value));
    strengthSlider.value = String(selectedStrength);
    strengthValueEl.textContent = String(selectedStrength);
    strengthDescEl.textContent = strengthHint(selectedStrength);

    if (selectedFile) {
      const estimatedBytes = Math.round(selectedFile.size * estimateRatio(selectedStrength));
      const savedPct = Math.round((1 - estimateRatio(selectedStrength)) * 100);
      estimateValueEl.textContent = `~${formatBytes(estimatedBytes)} (~${savedPct}% smaller)`;
    }

    if (syncPresets) {
      levelBtns.forEach((b) => {
        b.classList.toggle("is-active", Number(b.dataset.strength) === selectedStrength);
      });
    } else {
      levelBtns.forEach((b) => b.classList.remove("is-active"));
    }
  }

  levelBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setStrength(Number(btn.dataset.strength));
    });
  });

  strengthSlider.addEventListener("input", () => {
    setStrength(Number(strengthSlider.value), { syncPresets: true });
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
    formData.append("strength", String(selectedStrength));

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

      const blob = await response.blob();

      if (pendingBlobUrl) URL.revokeObjectURL(pendingBlobUrl);
      pendingBlobUrl = URL.createObjectURL(blob);

      resOriginal.textContent = formatBytes(originalSize);
      resCompressed.textContent = formatBytes(compressedSize);
      resPercent.textContent = `${percentSaved}%`;

      panelLoading.classList.add("hidden");
      panelResult.classList.remove("hidden");

      // Every download — including this first automatic one — gets its
      // own fresh alphabet-only filename, even for the same compressed file.
      triggerDownload(pendingBlobUrl);
    } catch (err) {
      panelLoading.classList.add("hidden");
      panelUpload.classList.remove("hidden");
      showError("network error — could not reach the compressor.");
    }
  });

  function triggerDownload(blobUrl) {
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = randomAlphaFilename();
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  downloadBtn.addEventListener("click", () => {
    if (pendingBlobUrl) {
      triggerDownload(pendingBlobUrl);
    }
  });

  resetBtn.addEventListener("click", resetToUpload);
})();
