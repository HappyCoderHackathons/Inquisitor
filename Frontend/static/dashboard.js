"use strict";
(() => {
  // frontend/format.ts
  var dateTime = new Intl.DateTimeFormat(void 0, { dateStyle: "medium", timeStyle: "short" });
  function formatTime(iso, fallback = "Not recorded") {
    if (!iso) return fallback;
    const value = new Date(iso);
    return Number.isNaN(value.getTime()) ? fallback : dateTime.format(value);
  }
  function formatAgo(iso, fallback = "never") {
    if (!iso) return fallback;
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return fallback;
    const seconds = Math.max(0, (Date.now() - value.getTime()) / 1e3);
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
    return formatTime(iso);
  }
  function formatScore(value, fallback = "not available") {
    return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : fallback;
  }
  function formatDuration(seconds, fallback = "not recorded") {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return fallback;
    const total = Math.round(seconds);
    if (total < 60) return `${total} s`;
    const minutes = Math.floor(total / 60), rest = total % 60;
    if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    return `${hours} h ${minutes % 60} min`;
  }

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
  var modelSummaryStatus = $("#modelSummaryStatus");
  var dangerForms = document.querySelectorAll(".typed-confirm");
  var monitoringStatusViews = {
    normal: {
      label: "Normal",
      detail: "Recent activity is consistent with this device's enrollment.",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.5"/>'
    },
    not_enough_activity: {
      label: "Not enough activity",
      detail: "There is too little recent input to compare, so no judgment is made.",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
    },
    unusual: {
      label: "Unusual",
      detail: "Recent activity differs from this device's enrollment. Treat this as a prompt to check, not a finding about any person.",
      icon: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>'
    },
    needs_verification: {
      label: "Needs verification",
      detail: "Please confirm it is you. This does not identify anyone.",
      icon: '<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/><path d="M9.8 9.8a2.3 2.3 0 1 1 3.3 2.1c-.7.3-1.1.8-1.1 1.6M12 16.5h.01"/>'
    },
    paused: {
      label: "Paused",
      detail: "Monitoring is paused. No input is being measured.",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>'
    },
    offline: {
      label: "Offline",
      detail: "The agent has not checked in recently. The last score is not a live signal.",
      icon: '<circle cx="12" cy="12" r="9"/><path d="M8 8l8 8M16 8l-8 8"/>'
    },
    none: {
      label: "No signal",
      detail: "No live monitoring status is available in this prototype.",
      icon: '<circle cx="12" cy="12" r="9" stroke-dasharray="3 3"/><path d="M8.5 12h7"/>'
    }
  };
  function renderMonitoringStatus(status) {
    const card = $("#monitoringStatusCard");
    const view = monitoringStatusViews[status ?? "none"];
    if (!card) return;
    card.dataset.state = status ?? "none";
    const icon = $("#monitoringStatusIcon");
    const label = $("#monitoringStatusLabel");
    const detail = $("#monitoringStatusDetail");
    if (icon) icon.innerHTML = `<svg viewBox="0 0 24 24" focusable="false">${view.icon}</svg>`;
    if (label) label.textContent = view.label;
    if (detail) detail.textContent = view.detail;
  }
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
    if (!collectionStatus) return;
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
  function recorded(value) {
    return value && value.length > 0 ? value : "Not recorded";
  }
  function windowText(summary) {
    if (!summary.available) return "Not recorded";
    if (summary.training_windows !== null && summary.validation_windows !== null) {
      return `${summary.training_windows} training, ${summary.validation_windows} calibration`;
    }
    if (summary.window_count !== null) return String(summary.window_count);
    return "Not recorded";
  }
  function rateText(alerts, total, label) {
    if (alerts === null || total === null) return "Not available";
    return `${alerts} of ${total} ${label} windows`;
  }
  function showModelSummary(summary) {
    if (modelSummaryStatus) {
      modelSummaryStatus.classList.remove("error");
      modelSummaryStatus.textContent = summary.available ? "This is the active model for the signed-in account." : "No trained model is recorded for this account.";
    }
    const detectionDetail = $("#detectionEmptyDetail");
    if (detectionDetail) {
      detectionDetail.textContent = summary.available ? "A trained model is active. No live signal is connected yet." : "No trained model is active for this account yet. Enroll to train one.";
    }
    const enrollLink = $("#detectionEnrollLink");
    if (enrollLink) enrollLink.hidden = summary.available;
    const device = $("#modelDevice");
    if (device) {
      device.textContent = summary.available ? summary.device_name && summary.device_name.length > 0 ? summary.device_name : "Unnamed device" : "Not recorded";
    }
    const version = $("#modelVersion");
    if (version) {
      version.textContent = summary.available ? summary.trained_at ? `Trained ${formatTime(summary.trained_at)}` : recorded(summary.model_id) : "Not recorded";
      version.title = summary.available ? summary.model_id ?? "" : "";
    }
    const trained = $("#modelTrained");
    if (trained) trained.textContent = summary.available ? formatTime(summary.trained_at) : "Not recorded";
    const windows = $("#modelWindows");
    if (windows) windows.textContent = windowText(summary);
    const sessions = $("#modelSessions");
    if (sessions) {
      sessions.textContent = summary.available && summary.session_count !== null ? String(summary.session_count) : "Not recorded";
    }
    const algorithm = $("#modelAlgorithm");
    if (algorithm) {
      algorithm.textContent = summary.available ? recorded(summary.algorithm_label ?? summary.algorithm) : "Not recorded";
    }
    const threshold = $("#modelThreshold");
    if (threshold) threshold.textContent = summary.available ? recorded(summary.threshold_label) : "Not recorded";
    const holdout = $("#modelHoldout");
    if (holdout) holdout.textContent = rateText(summary.holdout_alerts, summary.holdout_windows, "holdout");
    const impostor = $("#modelImpostor");
    if (impostor) impostor.textContent = rateText(summary.impostor_alerts, summary.impostor_windows, "impostor");
  }
  async function refreshModelSummary() {
    let response;
    try {
      response = await fetch(withDevice("/api/model"));
    } catch {
      if (modelSummaryStatus) {
        modelSummaryStatus.classList.add("error");
        modelSummaryStatus.textContent = "Could not load the model summary. The server did not respond.";
      }
      return;
    }
    if (response.status === 404) return;
    if (!response.ok) {
      if (modelSummaryStatus) {
        modelSummaryStatus.classList.add("error");
        modelSummaryStatus.textContent = `Could not load the model summary (${response.status}).`;
      }
      return;
    }
    showModelSummary(await response.json());
  }
  function actionStatus(form) {
    if (form.dataset.status) return document.getElementById(form.dataset.status);
    return dataActionStatus;
  }
  function wireDangerForm(form) {
    const phrase = form.dataset.phrase ?? "";
    const method = form.dataset.method ?? "POST";
    const path = form.dataset.path ?? "";
    const success = form.dataset.success ?? "Done.";
    const status = actionStatus(form);
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
      if (status) {
        status.classList.remove("error");
        status.textContent = "Please wait.";
      }
      void fetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form.dataset.approve === "true" ? { approve: true } : { confirmation: phrase })
      }).then(async (response) => {
        if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
        closeDangerForms();
        if (status) {
          status.classList.remove("error");
          status.textContent = success;
        }
        try {
          await refreshCollectionSummary();
          if (form.dataset.status) await refreshModelSummary();
        } catch (error) {
          if (status) {
            status.classList.add("error");
            status.textContent = error instanceof Error ? error.message : "The action finished, but saved windows could not be reloaded.";
          }
        }
      }).catch((error) => {
        if (status) {
          status.classList.add("error");
          status.textContent = error instanceof TypeError ? "Could not reach the server. Nothing was changed." : error instanceof Error ? error.message : "Could not complete that action.";
        }
        if (submit) submit.disabled = !input || input.value !== phrase;
      });
    });
  }
  dangerForms.forEach(wireDangerForm);
  var monitoringPollMs = 5e3;
  var monitoringTimer = 0;
  var monitoringSource = null;
  var monitoringUnavailable = false;
  var selectedDeviceId = "";
  function withDevice(path) {
    if (!selectedDeviceId) return path;
    const join = path.includes("?") ? "&" : "?";
    return `${path}${join}device_id=${encodeURIComponent(selectedDeviceId)}`;
  }
  function renderAgentConnection(snapshot) {
    const heading = $("#agentConnectionStatus");
    const detail = $("#agentConnectionDetail");
    const indicator = $("#agentConnectionIndicator");
    const seen = snapshot.last_seen ? `Last check-in ${formatAgo(snapshot.last_seen)}.` : "No check-in has been recorded.";
    if (heading) {
      heading.textContent = snapshot.agent === "active" ? "Connected" : snapshot.agent === "paused" ? "Paused" : "Offline";
    }
    if (detail) {
      detail.textContent = snapshot.stale ? `${seen} No recent check-in. These scores may be out of date.` : snapshot.agent === "paused" ? `${seen} Collection is paused.` : seen;
    }
    indicator?.classList.toggle("pending", snapshot.stale || snapshot.agent !== "active");
  }
  function renderScoreChart(points) {
    const svg = $("#scoreChart");
    const summary = $("#scoreChartSummary");
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const scored = points.filter((point) => typeof point.score === "number");
    const latest = scored[scored.length - 1];
    if (summary) {
      summary.textContent = latest ? `Latest anomaly score is ${formatScore(latest.score)}. Threshold is ${formatScore(latest.threshold, "not recorded")}.` : "No scored windows yet.";
    }
    if (scored.length === 0) return;
    const threshold = [...scored].reverse().find((point) => typeof point.threshold === "number")?.threshold ?? null;
    const values = scored.map((point) => point.score);
    if (threshold !== null) values.push(threshold);
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const width = 640;
    const height = 200;
    const pad = 28;
    const xAt = (index) => pad + index * (width - pad * 2) / Math.max(scored.length - 1, 1);
    const yAt = (value) => height - pad - (value - min) / (max - min) * (height - pad * 2);
    const namespace = "http://www.w3.org/2000/svg";
    const add = (name, attrs) => {
      const element = document.createElementNS(namespace, name);
      for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
      svg.append(element);
    };
    if (threshold !== null) {
      const y = String(yAt(threshold));
      add("line", { x1: String(pad), x2: String(width - pad), y1: y, y2: y, class: "threshold-line" });
    }
    add("polyline", {
      points: scored.map((point, index) => `${xAt(index)},${yAt(point.score)}`).join(" "),
      class: "score-line"
    });
  }
  function renderAlerts(alerts) {
    const list = $("#alertList");
    const status = $("#alertListStatus");
    if (!list) return;
    list.replaceChildren();
    status?.classList.remove("error");
    if (status)
      status.textContent = alerts.length === 0 ? "No alerts recorded." : `${alerts.length} alert${alerts.length === 1 ? "" : "s"}.`;
    for (const alert of alerts) {
      const item = document.createElement("li");
      item.className = "alert-item";
      const title = document.createElement("strong");
      title.textContent = alert.ongoing ? "Open alert" : "Closed alert";
      const body = document.createElement("p");
      const started = alert.started_at ? `Started ${formatTime(alert.started_at)}` : "Start time not recorded";
      const duration = alert.ongoing ? "Ongoing" : `Lasted ${formatDuration(alert.duration_seconds)}`;
      const label = alert.acknowledgement === "expected" ? "Marked expected." : alert.acknowledgement === "unexpected" ? "Marked unexpected." : "";
      body.textContent = `${started}. ${duration}. ${alert.explanation} ${label}`.trim();
      item.append(title, body);
      if (alert.investigation.length > 0) {
        const heading = document.createElement("p");
        heading.textContent = "Things to check (suggested by Gemini):";
        const hints = document.createElement("ul");
        for (const hint of alert.investigation) {
          const entry = document.createElement("li");
          entry.textContent = hint;
          hints.append(entry);
        }
        item.append(heading, hints);
      }
      if (!alert.acknowledgement) {
        const actions = document.createElement("div");
        actions.className = "alert-actions";
        for (const choice of ["expected", "unexpected"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "button secondary-button";
          button.textContent = choice === "expected" ? "Expected" : "Unexpected";
          button.addEventListener("click", () => {
            for (const sibling of actions.querySelectorAll("button")) sibling.disabled = true;
            void acknowledgeAlert(alert.id, choice, actions);
          });
          actions.append(button);
        }
        item.append(actions);
      }
      list.append(item);
    }
  }
  async function acknowledgeAlert(id, label, actions) {
    const status = $("#alertListStatus");
    try {
      const response = await fetch(`/api/alerts/${encodeURIComponent(id)}/acknowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label })
      });
      if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
      await refreshMonitoring();
    } catch (error) {
      for (const button of actions.querySelectorAll("button")) button.disabled = false;
      if (status) {
        status.classList.add("error");
        status.textContent = error instanceof TypeError ? "Could not acknowledge that alert. The server did not respond." : error instanceof Error ? error.message : "Could not acknowledge that alert.";
      }
    }
  }
  async function refreshMonitoring() {
    const chartSummary = $("#scoreChartSummary");
    const alertStatus = $("#alertListStatus");
    let response;
    try {
      response = await fetch(withDevice("/api/monitoring/status"));
    } catch {
      const detail = $("#monitoringStatusDetail");
      if (detail) detail.textContent = "Could not load monitoring status. The server did not respond.";
      return;
    }
    if (response.status === 404) {
      monitoringUnavailable = true;
      monitoringSource?.close();
      monitoringSource = null;
      window.clearInterval(monitoringTimer);
      monitoringTimer = 0;
      renderMonitoringStatus(null);
      return;
    }
    if (!response.ok) {
      const detail = $("#monitoringStatusDetail");
      if (detail) detail.textContent = `Could not load monitoring status (${response.status}).`;
      return;
    }
    const snapshot = await response.json();
    renderMonitoringStatus(snapshot.state);
    renderAgentConnection(snapshot);
    const modelDetail = $("#modelConnectionDetail");
    const modelHeading = $("#modelConnectionStatus");
    if (modelHeading) modelHeading.textContent = snapshot.score === null ? "Not connected" : "Scoring";
    if (modelDetail && snapshot.score !== null) {
      modelDetail.textContent = "Recent windows are scored against this account's active model.";
    }
    try {
      const history = await fetch(withDevice("/api/monitoring/history?limit=60"));
      if (history.ok) renderScoreChart((await history.json()).points ?? []);
      else if (chartSummary) chartSummary.textContent = `Could not load the anomaly score (${history.status}).`;
    } catch {
      if (chartSummary) chartSummary.textContent = "Could not load the anomaly score. The server did not respond.";
    }
    try {
      const alerts = await fetch(withDevice("/api/alerts"));
      if (alerts.ok) renderAlerts((await alerts.json()).alerts ?? []);
      else if (alertStatus) alertStatus.textContent = `Could not load alerts (${alerts.status}).`;
    } catch {
      if (alertStatus) alertStatus.textContent = "Could not load alerts. The server did not respond.";
    }
  }
  function startMonitoringPoll() {
    if (monitoringTimer) return;
    monitoringTimer = window.setInterval(() => void refreshMonitoring(), monitoringPollMs);
  }
  function openMonitoringStream() {
    if (monitoringUnavailable) return;
    monitoringSource?.close();
    monitoringSource = null;
    if (monitoringTimer) {
      window.clearInterval(monitoringTimer);
      monitoringTimer = 0;
    }
    if (typeof EventSource === "undefined") {
      startMonitoringPoll();
      return;
    }
    const source = new EventSource(withDevice("/api/monitoring/stream"));
    monitoringSource = source;
    source.addEventListener("monitoring", () => {
      void refreshMonitoring();
    });
    source.onerror = () => {
      source.close();
      if (monitoringSource === source) monitoringSource = null;
      if (monitoringUnavailable) return;
      const detail = $("#monitoringStatusDetail");
      if (detail) detail.textContent = "Could not open live monitoring. Checking again every few seconds.";
      startMonitoringPoll();
    };
  }
  async function loadDeviceSwitcher() {
    const row = $("#deviceSwitcherRow");
    const select = document.querySelector("#deviceSwitcher");
    if (!select) return;
    let response;
    try {
      response = await fetch("/api/devices");
    } catch {
      return;
    }
    if (!response.ok) return;
    const devices = (await response.json()).devices ?? [];
    select.replaceChildren();
    for (const device of devices) {
      const option = document.createElement("option");
      option.value = device.id;
      const name = device.name && device.name.length > 0 ? device.name : "Unnamed device";
      option.textContent = device.revoked ? `${name} (revoked)` : name;
      select.append(option);
    }
    if (row) row.hidden = devices.length < 2;
    if (devices.length > 0 && !devices.some((device) => device.id === selectedDeviceId)) {
      selectedDeviceId = devices[0]?.id ?? "";
    }
    if (selectedDeviceId) select.value = selectedDeviceId;
  }
  async function loadAccount() {
    const heading = $("#accountConnectionStatus");
    const detail = $("#accountConnectionDetail");
    let response;
    try {
      response = await fetch("/api/me");
    } catch {
      if (detail) detail.textContent = "Could not load your account. The server did not respond.";
      return;
    }
    if (response.status === 404 || response.status === 401) return;
    if (!response.ok) {
      if (detail) detail.textContent = `Could not load your account (${response.status}).`;
      return;
    }
    const account = await response.json();
    if (heading) heading.textContent = "Signed in";
    if (detail) detail.textContent = account.email ? `Signed in as ${account.email}.` : "Signed in.";
    $("#accountConnectionIndicator")?.classList.remove("pending");
    const email = $("#accountEmail");
    if (email) email.textContent = account.email ?? "Not recorded";
    const id = $("#accountId");
    if (id) id.textContent = account.user_id;
  }
  updateControls();
  void loadAccount();
  void (async () => {
    await loadDeviceSwitcher();
    void refreshModelSummary();
    void refreshMonitoring();
    openMonitoringStream();
  })();
  $("#deviceSwitcher")?.addEventListener("change", (event) => {
    selectedDeviceId = event.currentTarget.value;
    void refreshModelSummary();
    void refreshMonitoring();
    openMonitoringStream();
  });
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
})();
