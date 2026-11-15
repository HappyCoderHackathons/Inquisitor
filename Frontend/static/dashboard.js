(() => {
  // frontend/dashboard.ts
  var $ = (selector) => document.querySelector(selector);
  var collectionSurface = $("#collectorSurface");
  var collectionMode = $("#collectionMode");
  var collectionConsent = $("#collectionConsent");
  var startButton = $("#startCollection");
  var pauseButton = $("#pauseCollection");
  var stopButton = $("#stopCollection");
  var retryButton = $("#retryWindow");
  var progress = $("#windowProgress");
  var progressLabel = $("#windowProgressLabel");
  var collectionStatus = $("#collectionStatus");
  var windowCount = $("#featureWindowCount");
  var sessionCount = $("#featureSessionCount");
  var exportLink = $("#exportFeatureWindows");
  var deleteLocalDataButton = $("#deleteLocalData");
  var dataActionStatus = $("#dataActionStatus");
  var collectionWindowMs = 3e4;
  var deviceId = "";
  var sessionId = "";
  var mode = "enrollment";
  var sequence = 0;
  var collectionActive = false;
  var collectionPaused = false;
  var timerId = 0;
  var firstPressAt = null;
  var windowStartedAt = "";
  var lastPressAt = null;
  var lastPressCode = null;
  var keyCount = 0;
  var overlaps = 0;
  var intervals = [];
  var holds = [];
  var heldKeys = /* @__PURE__ */ new Map();
  var pendingWindow = null;
  var resumeAfterRetry = false;
  function setCollectionStatus(message, isError = false) {
    if (!collectionStatus) return;
    collectionStatus.textContent = message;
    collectionStatus.classList.toggle("error", isError);
  }
  function updateControls() {
    if (startButton) startButton.disabled = !collectionConsent?.checked || collectionActive || pendingWindow !== null;
    if (pauseButton) {
      pauseButton.disabled = !collectionActive || pendingWindow !== null;
      pauseButton.textContent = collectionPaused ? "Resume" : "Pause";
    }
    if (stopButton) stopButton.disabled = !collectionActive;
    if (retryButton) retryButton.hidden = pendingWindow === null;
    if (deleteLocalDataButton) deleteLocalDataButton.disabled = collectionActive || pendingWindow !== null;
    if (collectionMode) collectionMode.disabled = collectionActive;
    if (collectionConsent) collectionConsent.disabled = collectionActive;
  }
  function resetWindow() {
    firstPressAt = null;
    windowStartedAt = "";
    lastPressAt = null;
    lastPressCode = null;
    keyCount = 0;
    overlaps = 0;
    intervals = [];
    holds = [];
    heldKeys.clear();
    if (progress) progress.value = 0;
    if (progressLabel) progressLabel.textContent = "Waiting for keyboard input";
  }
  function standardDeviation(values) {
    const average = values.reduce((sum, value) => sum + value, 0) / values.length;
    return Math.sqrt(values.reduce((sum, value) => sum + (value - average) ** 2, 0) / values.length);
  }
  function makeWindow(now) {
    if (firstPressAt === null || keyCount < 30 || holds.length === 0 || intervals.length === 0) return null;
    const durationMs = Math.max(1, Math.ceil(now - firstPressAt));
    const pauses = intervals.filter((gap) => gap > 1e3);
    const pauseMs = pauses.reduce((sum, gap) => sum + gap, 0);
    const activeMs = Math.max(1, durationMs - pauseMs);
    const sortedIntervals = [...intervals].sort((a, b) => a - b);
    return {
      schema_version: 1,
      device_id: deviceId,
      session_id: sessionId,
      sequence,
      mode,
      started_at: windowStartedAt,
      duration_ms: durationMs,
      features: {
        key_count: keyCount,
        presses_per_second: keyCount * 1e3 / activeMs,
        hold_mean_ms: holds.reduce((sum, value) => sum + value, 0) / holds.length,
        hold_std_ms: standardDeviation(holds),
        press_interval_mean_ms: intervals.reduce((sum, value) => sum + value, 0) / intervals.length,
        press_interval_std_ms: standardDeviation(intervals),
        press_interval_p90_ms: sortedIntervals[Math.ceil(0.9 * sortedIntervals.length) - 1],
        pause_count: pauses.length,
        pause_fraction: pauseMs / durationMs,
        burst_count: pauses.length + 1,
        press_overlap_fraction: overlaps / intervals.length
      }
    };
  }
  function startTimer() {
    window.clearInterval(timerId);
    timerId = window.setInterval(() => {
      if (pendingWindow || firstPressAt === null) return;
      const elapsed = performance.now() - firstPressAt;
      if (progress) progress.value = Math.min(elapsed, collectionWindowMs);
      if (progressLabel) {
        progressLabel.textContent = `${Math.min(Math.floor(elapsed / 1e3), 30)} / 30 seconds \xB7 ${keyCount} presses`;
      }
      if (elapsed >= collectionWindowMs) {
        const windowValue = makeWindow(performance.now());
        if (windowValue) void saveWindow(windowValue);
        else if (progressLabel) progressLabel.textContent = `Need 30 presses and a completed hold \xB7 ${keyCount} presses`;
      }
    }, 250);
  }
  function localDeviceId() {
    const storageKey = "inquisitor.localDeviceId";
    const existing = window.localStorage.getItem(storageKey);
    if (existing) return existing;
    const created = window.crypto.randomUUID();
    window.localStorage.setItem(storageKey, created);
    return created;
  }
  async function refreshCollectionSummary() {
    const response = await fetch("/api/feature-windows");
    if (!response.ok) throw new Error(`Could not load feature-window summary (${response.status}).`);
    const data = await response.json();
    if (windowCount) windowCount.textContent = String(data.windows);
    if (sessionCount) sessionCount.textContent = String(data.sessions);
    if (exportLink) {
      exportLink.setAttribute("aria-disabled", data.windows === 0 ? "true" : "false");
      exportLink.tabIndex = data.windows === 0 ? -1 : 0;
    }
  }
  async function saveWindow(value) {
    if (pendingWindow) return;
    pendingWindow = value;
    window.clearInterval(timerId);
    if (progressLabel) progressLabel.textContent = "Saving aggregate feature window\u2026";
    updateControls();
    await retrySave();
  }
  async function retrySave() {
    if (!pendingWindow) return;
    try {
      const response = await fetch("/api/feature-windows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingWindow)
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error || `Could not save feature window (${response.status}).`);
      }
      pendingWindow = null;
      sequence++;
      await refreshCollectionSummary();
      resetWindow();
      setCollectionStatus("Aggregate feature window saved locally. No raw key data was sent.");
      if (collectionActive && resumeAfterRetry) {
        collectionPaused = false;
        collectionSurface?.focus();
        startTimer();
      }
      resumeAfterRetry = false;
    } catch (error) {
      resumeAfterRetry = collectionActive && !collectionPaused;
      collectionPaused = true;
      setCollectionStatus(error instanceof Error ? error.message : "Could not save feature window.", true);
    } finally {
      updateControls();
    }
  }
  function startCollection() {
    if (!collectionConsent?.checked || !collectionSurface || !collectionMode) return;
    try {
      deviceId = localDeviceId();
    } catch {
      setCollectionStatus("Local browser storage is unavailable; collection cannot start.", true);
      return;
    }
    sessionId = window.crypto.randomUUID();
    mode = collectionMode.value === "monitoring" ? "monitoring" : "enrollment";
    sequence = 0;
    collectionActive = true;
    collectionPaused = false;
    resetWindow();
    setCollectionStatus("Collection is on. Focus the capture area and type naturally; press Esc to stop.");
    updateControls();
    collectionSurface.focus();
  }
  function stopCollection(message = "Collection stopped.") {
    collectionActive = false;
    collectionPaused = false;
    window.clearInterval(timerId);
    resetWindow();
    setCollectionStatus(pendingWindow ? "Collection stopped. Retry saving the pending aggregate window or leave it unsaved." : message);
    updateControls();
  }
  function pauseCollection() {
    if (!collectionActive || pendingWindow) return;
    collectionPaused = !collectionPaused;
    window.clearInterval(timerId);
    resetWindow();
    if (collectionPaused) {
      setCollectionStatus("Paused. The incomplete window was discarded.");
    } else {
      setCollectionStatus("Collection resumed. Focus the capture area and type naturally.");
      collectionSurface?.focus();
      startTimer();
    }
    updateControls();
  }
  collectionSurface?.addEventListener("keydown", (event) => {
    if (!collectionActive || collectionPaused || pendingWindow) return;
    if (event.key === "Escape") {
      event.preventDefault();
      stopCollection("Collection stopped with Esc.");
      return;
    }
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    if (heldKeys.has(event.code)) return;
    const now = performance.now();
    if (firstPressAt === null) {
      firstPressAt = now;
      windowStartedAt = (/* @__PURE__ */ new Date()).toISOString();
      startTimer();
    }
    if (lastPressAt !== null) {
      intervals.push(now - lastPressAt);
      if (lastPressCode !== null && heldKeys.has(lastPressCode)) overlaps++;
    }
    heldKeys.set(event.code, now);
    lastPressAt = now;
    lastPressCode = event.code;
    keyCount++;
  });
  collectionSurface?.addEventListener("keyup", (event) => {
    const pressedAt = heldKeys.get(event.code);
    if (pressedAt === void 0) return;
    holds.push(Math.max(0, performance.now() - pressedAt));
    heldKeys.delete(event.code);
  });
  window.addEventListener("blur", () => heldKeys.clear());
  collectionConsent?.addEventListener("change", updateControls);
  startButton?.addEventListener("click", startCollection);
  pauseButton?.addEventListener("click", pauseCollection);
  stopButton?.addEventListener("click", () => stopCollection());
  retryButton?.addEventListener("click", () => void retrySave());
  exportLink?.addEventListener("click", (event) => {
    if (exportLink.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      setCollectionStatus("Collect at least one feature window before exporting.");
    }
  });
  deleteLocalDataButton?.addEventListener("click", async () => {
    if (collectionActive || pendingWindow) {
      setCollectionStatus("Stop collection and save or discard any pending window before deleting data.", true);
      return;
    }
    if (!window.confirm("Delete all locally stored arcade rounds and feature windows? This cannot be undone.")) return;
    if (dataActionStatus) dataActionStatus.textContent = "";
    try {
      const response = await fetch("/api/local-data", { method: "DELETE" });
      if (!response.ok) throw new Error(`Request failed (${response.status}).`);
      if (dataActionStatus) {
        dataActionStatus.classList.remove("error");
        dataActionStatus.textContent = "Local arcade rounds and feature windows deleted.";
      }
      await refreshCollectionSummary();
    } catch (error) {
      if (dataActionStatus) {
        dataActionStatus.classList.add("error");
        dataActionStatus.textContent = error instanceof Error ? error.message : "Could not delete local data.";
      }
    }
  });
  updateControls();
  void refreshCollectionSummary().catch((error) => {
    setCollectionStatus(error instanceof Error ? error.message : "Could not load saved windows.", true);
  });
})();
