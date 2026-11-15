type CollectionMode = "enrollment" | "monitoring";
type MouseFeatures = {
  move_count: number;
  speed_mean_px_s: number;
  speed_std_px_s: number;
  acceleration_mean_px_s2: number;
  acceleration_std_px_s2: number;
  turn_angle_mean_rad: number;
  click_count: number;
  click_interval_mean_ms: number;
  click_interval_std_ms: number;
};
type FeatureWindow = {
  schema_version: 1 | 2;
  device_id: string;
  session_id: string;
  sequence: number;
  mode: CollectionMode;
  started_at: string;
  duration_ms: number;
  features: {
    key_count: number;
    presses_per_second: number;
    hold_mean_ms: number;
    hold_std_ms: number;
    press_interval_mean_ms: number;
    press_interval_std_ms: number;
    press_interval_p90_ms: number;
    pause_count: number;
    pause_fraction: number;
    burst_count: number;
    press_overlap_fraction: number;
  };
  mouse_features?: MouseFeatures;
};
type CollectionSummary = { windows: number; sessions: number; modes: Record<string, number> };

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector);
const collectionSurface = $("#collectorSurface");
const collectionMode = $("#collectionMode") as HTMLSelectElement | null;
const collectionConsent = $("#collectionConsent") as HTMLInputElement | null;
const mouseConsent = $("#mouseConsent") as HTMLInputElement | null;
const startButton = $("#startCollection") as HTMLButtonElement | null;
const pauseButton = $("#pauseCollection") as HTMLButtonElement | null;
const stopButton = $("#stopCollection") as HTMLButtonElement | null;
const retryButton = $("#retryWindow") as HTMLButtonElement | null;
const progress = $("#windowProgress") as HTMLProgressElement | null;
const progressLabel = $("#windowProgressLabel");
const collectionStatus = $("#collectionStatus");
const windowCount = $("#featureWindowCount");
const sessionCount = $("#featureSessionCount");
const exportLink = $("#exportFeatureWindows") as HTMLAnchorElement | null;
const deleteLocalDataButton = $("#deleteLocalData") as HTMLButtonElement | null;
const dataActionStatus = $("#dataActionStatus");

const collectionWindowMs = 30_000;
const mouseSegmentMs = 20;
const mouseStrokeGapMs = 250;
let deviceId = "";
let sessionId = "";
let mode: CollectionMode = "enrollment";
let sequence = 0;
let collectionActive = false;
let collectionPaused = false;
let timerId = 0;
let firstPressAt: number | null = null;
let windowStartedAt = "";
let lastPressAt: number | null = null;
let lastPressCode: string | null = null;
let keyCount = 0;
let overlaps = 0;
let intervals: number[] = [];
let holds: number[] = [];
let heldKeys = new Map<string, number>();
let trackMouse = false;
let lastPointer: { x: number; y: number } | null = null;
let lastMoveAt: number | null = null;
let segmentStart = 0;
let segmentDx = 0;
let segmentDy = 0;
let previousSpeed: number | null = null;
let previousAngle: number | null = null;
let lastClickAt: number | null = null;
let clickCount = 0;
let speeds: number[] = [];
let accelerations: number[] = [];
let turns: number[] = [];
let clickIntervals: number[] = [];
let pendingWindow: FeatureWindow | null = null;
let resumeAfterRetry = false;

function setCollectionStatus(message: string, isError = false) {
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

function average(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function standardDeviation(values: number[]) {
  if (values.length === 0) return 0;
  const average = values.reduce((sum, value) => sum + value, 0) / values.length;
  return Math.sqrt(values.reduce((sum, value) => sum + (value - average) ** 2, 0) / values.length);
}

function recordMove(dx: number, dy: number, now: number) {
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
  const speed = (distance * 1000) / elapsed;
  speeds.push(speed);
  if (previousSpeed !== null) accelerations.push((Math.abs(speed - previousSpeed) * 1000) / elapsed);
  if (distance > 0) {
    const angle = Math.atan2(segmentDy, segmentDx);
    if (previousAngle !== null) {
      const wrapped = (((angle - previousAngle + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      turns.push(Math.abs(wrapped - Math.PI));
    }
    previousAngle = angle;
  }
  previousSpeed = speed;
  segmentStart = now;
  segmentDx = 0;
  segmentDy = 0;
}

function mouseFeatures(): MouseFeatures {
  return {
    move_count: speeds.length,
    speed_mean_px_s: average(speeds),
    speed_std_px_s: standardDeviation(speeds),
    acceleration_mean_px_s2: average(accelerations),
    acceleration_std_px_s2: standardDeviation(accelerations),
    turn_angle_mean_rad: average(turns),
    click_count: clickCount,
    click_interval_mean_ms: average(clickIntervals),
    click_interval_std_ms: standardDeviation(clickIntervals),
  };
}

function makeWindow(now: number): FeatureWindow | null {
  if (firstPressAt === null || keyCount < 30 || holds.length === 0 || intervals.length === 0) return null;
  const durationMs = Math.max(1, Math.ceil(now - firstPressAt));
  const pauses = intervals.filter((gap) => gap > 1000);
  const pauseMs = pauses.reduce((sum, gap) => sum + gap, 0);
  const activeMs = Math.max(1, durationMs - pauseMs);
  const sortedIntervals = [...intervals].sort((a, b) => a - b);
  const value: FeatureWindow = {
    schema_version: 1,
    device_id: deviceId,
    session_id: sessionId,
    sequence,
    mode,
    started_at: windowStartedAt,
    duration_ms: durationMs,
    features: {
      key_count: keyCount,
      presses_per_second: (keyCount * 1000) / activeMs,
      hold_mean_ms: holds.reduce((sum, value) => sum + value, 0) / holds.length,
      hold_std_ms: standardDeviation(holds),
      press_interval_mean_ms: intervals.reduce((sum, value) => sum + value, 0) / intervals.length,
      press_interval_std_ms: standardDeviation(intervals),
      press_interval_p90_ms: sortedIntervals[Math.ceil(0.9 * sortedIntervals.length) - 1],
      pause_count: pauses.length,
      pause_fraction: pauseMs / durationMs,
      burst_count: pauses.length + 1,
      press_overlap_fraction: overlaps / intervals.length,
    },
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
      progressLabel.textContent = `${Math.min(Math.floor(elapsed / 1000), 30)} / 30 seconds · ${keyCount} presses`;
    }
    if (elapsed >= collectionWindowMs) {
      const windowValue = makeWindow(performance.now());
      if (windowValue) void saveWindow(windowValue);
      else if (progressLabel) progressLabel.textContent = `Need 30 presses and a completed hold · ${keyCount} presses`;
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
  const data = (await response.json()) as CollectionSummary;
  if (windowCount) windowCount.textContent = String(data.windows);
  if (sessionCount) sessionCount.textContent = String(data.sessions);
  if (exportLink) {
    exportLink.setAttribute("aria-disabled", data.windows === 0 ? "true" : "false");
    exportLink.tabIndex = data.windows === 0 ? -1 : 0;
  }
}

async function saveWindow(value: FeatureWindow) {
  if (pendingWindow) return;
  pendingWindow = value;
  window.clearInterval(timerId);
  if (progressLabel) progressLabel.textContent = "Saving aggregate feature window…";
  updateControls();
  await retrySave();
}

async function retrySave() {
  if (!pendingWindow) return;
  try {
    const response = await fetch("/api/feature-windows", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(pendingWindow),
    });
    if (!response.ok) {
      const result = (await response.json()) as { error?: string };
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
    pendingWindow ? "Collection stopped. Retry saving the pending aggregate window or leave it unsaved." : message,
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
    windowStartedAt = new Date().toISOString();
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
  if (pressedAt === undefined) return;
  holds.push(Math.max(0, performance.now() - pressedAt));
  heldKeys.delete(event.code);
});

collectionSurface?.addEventListener("pointermove", (event) => {
  if (!trackMouse || !collectionActive || collectionPaused || pendingWindow || event.pointerType !== "mouse") return;
  // The previous position stays in memory only to compute the next delta.
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
