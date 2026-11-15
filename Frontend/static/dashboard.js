"use strict";
(() => {
  // frontend/dashboard.ts
  var $ = (selector) => document.querySelector(selector);
  var collectionSurface = $("#collectorSurface");
  var collectionMode = $("#collectionMode");
  var collectionConsent = $("#collectionConsent");
  var mouseConsent = $("#mouseConsent");
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
  var dataActionStatus = $("#dataActionStatus");
  var dangerForms = document.querySelectorAll(".typed-confirm");
  var collectionWindowMs = 3e4;
  var mouseSegmentMs = 20;
  var mouseStrokeGapMs = 250;
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
  var trackMouse = false;
  var lastPointer = null;
  var lastMoveAt = null;
  var segmentStart = 0;
  var segmentDx = 0;
  var segmentDy = 0;
  var previousSpeed = null;
  var previousAngle = null;
  var lastClickAt = null;
  var clickCount = 0;
  var speeds = [];
  var accelerations = [];
  var turns = [];
  var clickIntervals = [];
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
    const storedDataLocked = collectionActive || pendingWindow !== null;
    document.querySelectorAll("[data-danger-open]").forEach((button) => {
      button.disabled = storedDataLocked;
    });
    if (storedDataLocked) closeDangerForms();
    if (collectionMode) collectionMode.disabled = collectionActive;
    if (collectionConsent) collectionConsent.disabled = collectionActive;
    if (mouseConsent) mouseConsent.disabled = collectionActive;
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
    lastPointer = null;
    lastMoveAt = null;
    previousSpeed = null;
    previousAngle = null;
    lastClickAt = null;
    clickCount = 0;
    speeds = [];
    accelerations = [];
    turns = [];
    clickIntervals = [];
    if (progress) progress.value = 0;
    if (progressLabel) progressLabel.textContent = "Waiting for keyboard input";
  }
  function average(values) {
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  }
  function standardDeviation(values) {
    if (values.length === 0) return 0;
    const average2 = values.reduce((sum, value) => sum + value, 0) / values.length;
    return Math.sqrt(values.reduce((sum, value) => sum + (value - average2) ** 2, 0) / values.length);
  }
  function recordMove(dx, dy, now) {
    if (lastMoveAt === null || now - lastMoveAt > mouseStrokeGapMs) {
      segmentStart = now;
      segmentDx = 0;
      segmentDy = 0;
      previousSpeed = null;
      previousAngle = null;
      lastMoveAt = now;
      return;
    }
    lastMoveAt = now;
    segmentDx += dx;
    segmentDy += dy;
    const elapsed = now - segmentStart;
    if (elapsed < mouseSegmentMs) return;
    const distance = Math.hypot(segmentDx, segmentDy);
    const speed = distance * 1e3 / elapsed;
    speeds.push(speed);
    if (previousSpeed !== null) accelerations.push(Math.abs(speed - previousSpeed) * 1e3 / elapsed);
    if (distance > 0) {
      const angle = Math.atan2(segmentDy, segmentDx);
      if (previousAngle !== null) {
        const wrapped = ((angle - previousAngle + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
        turns.push(Math.abs(wrapped - Math.PI));
      }
      previousAngle = angle;
    }
    previousSpeed = speed;
    segmentStart = now;
    segmentDx = 0;
    segmentDy = 0;
  }
  function mouseFeatures() {
    return {
      move_count: speeds.length,
      speed_mean_px_s: average(speeds),
      speed_std_px_s: standardDeviation(speeds),
      acceleration_mean_px_s2: average(accelerations),
      acceleration_std_px_s2: standardDeviation(accelerations),
      turn_angle_mean_rad: average(turns),
      click_count: clickCount,
      click_interval_mean_ms: average(clickIntervals),
      click_interval_std_ms: standardDeviation(clickIntervals)
    };
  }
  function makeWindow(now) {
    if (firstPressAt === null || keyCount < 30 || holds.length === 0 || intervals.length === 0) return null;
    const durationMs = Math.max(1, Math.ceil(now - firstPressAt));
    const pauses = intervals.filter((gap) => gap > 1e3);
    const pauseMs = pauses.reduce((sum, gap) => sum + gap, 0);
    const activeMs = Math.max(1, durationMs - pauseMs);
    const sortedIntervals = [...intervals].sort((a, b) => a - b);
    const value = {
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
        hold_mean_ms: holds.reduce((sum, value2) => sum + value2, 0) / holds.length,
        hold_std_ms: standardDeviation(holds),
        press_interval_mean_ms: intervals.reduce((sum, value2) => sum + value2, 0) / intervals.length,
        press_interval_std_ms: standardDeviation(intervals),
        press_interval_p90_ms: sortedIntervals[Math.ceil(0.9 * sortedIntervals.length) - 1],
        pause_count: pauses.length,
        pause_fraction: pauseMs / durationMs,
        burst_count: pauses.length + 1,
        press_overlap_fraction: overlaps / intervals.length
      }
    };
    if (trackMouse) {
      value.schema_version = 2;
      value.mouse_features = mouseFeatures();
    }
    return value;
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
    let response;
    try {
      response = await fetch("/api/feature-windows");
    } catch {
      throw new Error("Could not load saved windows. The server did not respond.");
    }
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
      setCollectionStatus(
        error instanceof TypeError ? "Could not save feature window. The server did not respond." : error instanceof Error ? error.message : "Could not save feature window.",
        true
      );
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
    trackMouse = mouseConsent?.checked === true;
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
    setCollectionStatus(
      pendingWindow ? "Collection stopped. Retry saving the pending aggregate window or leave it unsaved." : message
    );
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
  collectionSurface?.addEventListener("pointermove", (event) => {
    if (!trackMouse || !collectionActive || collectionPaused || pendingWindow || event.pointerType !== "mouse") return;
    if (lastPointer !== null) recordMove(event.clientX - lastPointer.x, event.clientY - lastPointer.y, event.timeStamp);
    lastPointer = { x: event.clientX, y: event.clientY };
  });
  collectionSurface?.addEventListener("pointerleave", () => {
    lastPointer = null;
  });
  collectionSurface?.addEventListener("pointerdown", (event) => {
    if (!trackMouse || !collectionActive || collectionPaused || pendingWindow || event.pointerType !== "mouse") return;
    if (lastClickAt !== null) clickIntervals.push(event.timeStamp - lastClickAt);
    lastClickAt = event.timeStamp;
    clickCount++;
  });
  window.addEventListener("blur", () => {
    heldKeys.clear();
    lastPointer = null;
  });
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
  function closeDangerForms() {
    dangerForms.forEach((form) => {
      form.hidden = true;
      const opener = document.querySelector(`[aria-controls="${form.id}"]`);
      if (opener) opener.setAttribute("aria-expanded", "false");
      const input = form.querySelector("input");
      if (input) input.value = "";
      const submit = form.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
    });
  }
  function responseError(payload, status) {
    if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
      return payload.error;
    }
    return `Request failed (${status}).`;
  }
  function wireDangerForm(form) {
    const phrase = form.dataset.phrase ?? "";
    const method = form.dataset.method ?? "POST";
    const path = form.dataset.path ?? "";
    const success = form.dataset.success ?? "Done.";
    const input = form.querySelector("input");
    const submit = form.querySelector('button[type="submit"]');
    const opener = document.querySelector(`[aria-controls="${form.id}"]`);
    input?.addEventListener("input", () => {
      if (submit && input) submit.disabled = input.value !== phrase;
    });
    opener?.addEventListener("click", () => {
      if (collectionActive || pendingWindow) {
        setCollectionStatus("Stop collection and save or discard any pending window before changing stored data.", true);
        return;
      }
      const opening = form.hidden;
      closeDangerForms();
      form.hidden = !opening;
      opener.setAttribute("aria-expanded", opening ? "true" : "false");
      if (opening) input?.focus();
    });
    form.querySelector(".typed-cancel")?.addEventListener("click", () => closeDangerForms());
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!input || input.value !== phrase || !submit) return;
      submit.disabled = true;
      if (dataActionStatus) {
        dataActionStatus.classList.remove("error");
        dataActionStatus.textContent = "Please wait.";
      }
      void fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: phrase })
      }).then(async (response) => {
        if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
        closeDangerForms();
        if (dataActionStatus) {
          dataActionStatus.classList.remove("error");
          dataActionStatus.textContent = success;
        }
        try {
          await refreshCollectionSummary();
        } catch (error) {
          if (dataActionStatus) {
            dataActionStatus.classList.add("error");
            dataActionStatus.textContent = error instanceof Error ? error.message : "The action finished, but saved windows could not be reloaded.";
          }
        }
      }).catch((error) => {
        if (dataActionStatus) {
          dataActionStatus.classList.add("error");
          dataActionStatus.textContent = error instanceof TypeError ? "Could not reach the server. Nothing was changed." : error instanceof Error ? error.message : "Could not complete that action.";
        }
        if (submit) submit.disabled = !input || input.value !== phrase;
      });
    });
  }
  dangerForms.forEach(wireDangerForm);
  updateControls();
  void (async () => {
    setCollectionStatus("Loading saved windows\u2026");
    try {
      await refreshCollectionSummary();
      if (!collectionActive) setCollectionStatus("Collection is off. Choose a mode and opt in to begin.");
    } catch (error) {
      setCollectionStatus(
        error instanceof Error ? error.message : "Could not load saved windows. The server did not respond.",
        true
      );
    }
  })();
  var detectionTitle = $("#detectionEmptyTitle");
  var detectionDetail = $("#detectionEmptyDetail");
  var enrollLink = $("#detectionEnrollLink");
  function setDetectionState(title, detail, enroll = false) {
    if (detectionTitle) detectionTitle.textContent = title;
    if (detectionDetail) detectionDetail.textContent = detail;
    if (enrollLink) enrollLink.hidden = !enroll;
  }
  async function refreshModelStatus() {
    try {
      const me = await fetch("/api/me");
      if (!me.ok) throw new Error("not signed in");
      const response = await fetch("/api/onboarding");
      if (response.status === 404) {
        setDetectionState("No anomaly signal", "No trained model is active for this account yet.", true);
        return;
      }
      if (!response.ok) throw new Error("model status unavailable");
      const status = await response.json();
      if (status.model_id) {
        setDetectionState("Model active", "A trained model is active. This page does not show signals yet.");
      } else {
        setDetectionState("No anomaly signal", "No trained model is active for this account yet.", true);
      }
    } catch {
      setDetectionState("Model status unavailable", "Sign in to check this account's model. No signal is shown.");
    }
  }
  void refreshModelStatus();
})();
