type ModelSummary = {
  available: boolean;
  model_id: string | null;
  window_count: number | null;
  algorithm_label: string | null;
  algorithm: string | null;
};
type StatusSnapshot = { state: string | null; score: number | null };
type HistoryPoint = {
  t: string | null;
  score: number | null;
  threshold: number | null;
  baseline_score: number | null;
  state: string | null;
};
type SwitchMark = {
  id: string;
  switched_at: string | null;
  alert_before_switch: boolean;
  latency_seconds: number | null;
};

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector);
const pollMs = 5_000;
let pollTimer = 0;

function responseError(payload: unknown, status: number) {
  if (payload && typeof payload === "object" && "error" in payload && typeof payload.error === "string") {
    return payload.error;
  }
  return `Request failed (${status}).`;
}

function recorded(value: string | number | null | undefined) {
  return value === null || value === undefined || value === "" ? "Not recorded" : String(value);
}

const statusLabels: Record<string, string> = {
  normal: "Normal",
  not_enough_activity: "Not enough activity",
  unusual: "Unusual",
  needs_verification: "Needs verification",
  paused: "Paused",
  offline: "Offline",
};

function renderFacts(model: ModelSummary | null, snapshot: StatusSnapshot | null) {
  const modelLine = $("#demoModel");
  const samples = $("#demoSamples");
  const status = $("#demoStatus");
  if (modelLine) {
    const name = model?.available ? recorded(model.algorithm_label ?? model.algorithm) : "Not recorded";
    const version = model?.available ? recorded(model.model_id) : "Not recorded";
    modelLine.textContent = `Model: ${version}. Algorithm: ${name}.`;
  }
  if (samples) {
    samples.textContent = `Training samples: ${model?.available ? recorded(model.window_count) : "Not recorded"}.`;
  }
  if (status) {
    const label = snapshot?.state ? (statusLabels[snapshot.state] ?? snapshot.state) : "No signal";
    const score = snapshot?.score === null || snapshot?.score === undefined ? "not recorded" : String(snapshot.score);
    status.textContent = `Latest state: ${label}. Anomaly score: ${score}.`;
  }
}

function renderSwitch(mark: SwitchMark | null) {
  const status = $("#guestSwitchStatus");
  if (!status) return;
  if (!mark) {
    status.textContent = "No switch is marked yet.";
    return;
  }
  const when = mark.switched_at ?? "time not recorded";
  if (mark.latency_seconds !== null) {
    const prior = mark.alert_before_switch ? " Needs verification was already open before this switch." : "";
    status.textContent = `Switch marked at ${when}. Needs verification followed ${mark.latency_seconds} seconds later.${prior}`;
    return;
  }
  status.textContent = mark.alert_before_switch
    ? `Switch marked at ${when}. Needs verification was already open before this switch.`
    : `Switch marked at ${when}. Needs verification has not followed yet.`;
}

function renderChart(points: HistoryPoint[], marks: SwitchMark[], showBaseline: boolean) {
  const svg = $("#demoChart");
  const summary = $("#demoChartSummary");
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const scored = points.filter((point): point is HistoryPoint & { score: number } => typeof point.score === "number");
  const latest = scored[scored.length - 1];
  const baselineValues = scored.filter((point) => typeof point.baseline_score === "number");
  if (summary) {
    const base = latest
      ? `Latest anomaly score is ${latest.score}. Threshold is ${latest.threshold ?? "not recorded"}.`
      : "No scored windows yet.";
    const extra = !showBaseline
      ? ""
      : baselineValues.length === 0
        ? " No baseline score is recorded for these windows."
        : ` Baseline score is ${baselineValues[baselineValues.length - 1]?.baseline_score}.`;
    summary.textContent = `${base}${extra}`;
  }
  if (scored.length === 0) return;
  const values = scored.map((point) => point.score);
  const threshold = [...scored].reverse().find((point) => typeof point.threshold === "number")?.threshold ?? null;
  if (threshold !== null) values.push(threshold);
  if (showBaseline) {
    for (const point of scored) if (typeof point.baseline_score === "number") values.push(point.baseline_score);
  }
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const width = 640;
  const height = 200;
  const pad = 28;
  const xAt = (index: number) => pad + (index * (width - pad * 2)) / Math.max(scored.length - 1, 1);
  const yAt = (value: number) => height - pad - ((value - min) / (max - min)) * (height - pad * 2);
  const namespace = "http://www.w3.org/2000/svg";
  const add = (name: string, attrs: Record<string, string>) => {
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
    class: "score-line",
  });
  if (showBaseline && baselineValues.length > 0) {
    add("polyline", {
      points: scored
        .map((point, index) =>
          typeof point.baseline_score === "number" ? `${xAt(index)},${yAt(point.baseline_score)}` : "",
        )
        .filter((point) => point !== "")
        .join(" "),
      class: "baseline-line",
    });
  }
  const times = scored.map((point) => (point.t ? Date.parse(point.t) : Number.NaN));
  for (const mark of marks) {
    const when = mark.switched_at ? Date.parse(mark.switched_at) : Number.NaN;
    if (Number.isNaN(when)) continue;
    let index = times.findIndex((time) => !Number.isNaN(time) && time >= when);
    if (index < 0) index = scored.length - 1;
    const x = String(xAt(index));
    add("line", { x1: x, x2: x, y1: String(pad), y2: String(height - pad), class: "switch-line" });
  }
}

async function markSwitch() {
  const status = $("#guestSwitchStatus");
  try {
    const response = await fetch("/api/demo/switches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error(responseError(await response.json().catch(() => null), response.status));
    await refreshDemo();
  } catch (error) {
    if (status) {
      status.textContent =
        error instanceof TypeError
          ? "Could not mark the switch. The server did not respond."
          : error instanceof Error
            ? error.message
            : "Could not mark the switch.";
    }
  }
}

async function refreshDemo() {
  const summary = $("#demoChartSummary");
  let modelResponse: Response;
  try {
    modelResponse = await fetch("/api/model");
  } catch {
    const status = $("#demoStatus");
    if (status) status.textContent = "Could not load the demo. The server did not respond.";
    return;
  }
  if (modelResponse.status === 404) {
    window.clearInterval(pollTimer);
    pollTimer = 0;
    return;
  }
  const model = modelResponse.ok ? ((await modelResponse.json()) as ModelSummary) : null;
  let snapshot: StatusSnapshot | null = null;
  try {
    const statusResponse = await fetch("/api/monitoring/status");
    if (statusResponse.ok) snapshot = (await statusResponse.json()) as StatusSnapshot;
  } catch {
    const status = $("#demoStatus");
    if (status) status.textContent = "Could not load the demo. The server did not respond.";
  }
  renderFacts(model, snapshot);
  let points: HistoryPoint[] = [];
  try {
    const history = await fetch("/api/monitoring/history?limit=60");
    if (history.ok) points = ((await history.json()) as { points: HistoryPoint[] }).points ?? [];
    else if (summary) summary.textContent = `Could not load the anomaly score (${history.status}).`;
  } catch {
    if (summary) summary.textContent = "Could not load the anomaly score. The server did not respond.";
  }
  let marks: SwitchMark[] = [];
  try {
    const switches = await fetch("/api/demo/switches");
    if (switches.ok) marks = ((await switches.json()) as { switches: SwitchMark[] }).switches ?? [];
    else {
      const status = $("#guestSwitchStatus");
      if (status) status.textContent = `Could not load switch markers (${switches.status}).`;
    }
  } catch {
    const status = $("#guestSwitchStatus");
    if (status) status.textContent = "Could not load switch markers. The server did not respond.";
  }
  renderSwitch(marks[marks.length - 1] ?? null);
  const showBaseline = document.querySelector<HTMLInputElement>("#baselineToggle")?.checked === true;
  renderChart(points, marks, showBaseline);
}

const toggle = $("#baselineToggle");
toggle?.addEventListener("change", () => void refreshDemo());
$("#markSwitch")?.addEventListener("click", () => {
  const button = $("#markSwitch") as HTMLButtonElement | null;
  if (button) button.disabled = true;
  void markSwitch().finally(() => {
    if (button) button.disabled = false;
  });
});
void refreshDemo();
pollTimer = window.setInterval(() => void refreshDemo(), pollMs);
