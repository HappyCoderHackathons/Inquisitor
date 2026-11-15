// Records the browser takes against the local demo stack (demo_video/stack/start.sh).
// A scripted stand-in agent posts SYNTHETIC schema-v4 windows through the real agent API,
// so pairing, enrollment, training, scoring, and alerts all run through the app's normal paths.
import { chromium } from "playwright";
import { MongoClient } from "mongodb";
import { mkdirSync, renameSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { createHash, randomBytes } from "node:crypto";

const BASE = "http://127.0.0.1:5000";
const TAKES = join(import.meta.dirname, "..", "takes");
const STATE = join(TAKES, "state.json");
const ONLY = process.argv.slice(2);
mkdirSync(TAKES, { recursive: true });

const mongo = new MongoClient("mongodb://127.0.0.1:27027");
const db = mongo.db("inquisitor_demo");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = [];
const mark = (take, event) => log.push({ take, event, t: Date.now() });

// Seeded RNG so reruns give the same windows.
let seed = 7;
const rand = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
const jitter = (mean, spread) => Math.max(0, mean + (rand() * 2 - 1) * spread);

// Two synthetic personas. Values are plausible aggregates, not measurements of anyone.
const PERSONAS = {
  enrolled: { keys: 62, hold: 92, holdSd: 24, gap: 185, gapSd: 105, speed: 520, speedSd: 410, clicks: 14, clickGap: 1900, turn: 0.42, straight: 0.34 },
  guest: { keys: 48, hold: 138, holdSd: 41, gap: 290, gapSd: 170, speed: 910, speedSd: 690, clicks: 24, clickGap: 1100, turn: 0.71, straight: 0.18 },
};

const agent = { deviceId: "", token: "", seq: {}, clock: Date.now() - 6 * 3600_000 };

function windowFor(persona, sessionId, mode) {
  const p = PERSONAS[persona];
  const keys = Math.round(jitter(p.keys, 10));
  agent.seq[sessionId] = (agent.seq[sessionId] ?? -1) + 1;
  agent.clock += 30_000;
  return {
    schema_version: 4,
    device_id: agent.deviceId,
    session_id: sessionId,
    sequence: agent.seq[sessionId],
    mode,
    started_at: new Date(agent.clock).toISOString(),
    duration_ms: 30000,
    features: {
      key_count: keys,
      hold_sample_count: keys,
      hold_mean_ms: jitter(p.hold, 8),
      hold_std_ms: jitter(p.holdSd, 5),
      press_interval_mean_ms: jitter(p.gap, 18),
      press_interval_std_ms: jitter(p.gapSd, 15),
      mouse_sample_count: Math.round(jitter(700, 150)),
      mouse_click_count: Math.round(jitter(p.clicks, 3)),
      mouse_speed_mean_counts_s: jitter(p.speed, 50),
      mouse_speed_std_counts_s: jitter(p.speedSd, 40),
      mouse_click_interval_mean_ms: jitter(p.clickGap, 150),
      mouse_click_interval_std_ms: jitter(p.clickGap * 0.6, 100),
      mouse_turn_mean_rad: jitter(p.turn, 0.05),
      mouse_turn_std_rad: jitter(p.turn * 1.2, 0.06),
      mouse_path_straightness: Math.min(1, jitter(p.straight, 0.04)),
    },
  };
}

async function agentCall(path, body) {
  const res = await fetch(BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${agent.token}` },
    body: JSON.stringify(body ?? {}),
  });
  return { status: res.status, body: await res.json().catch(() => null) };
}

async function postWindow(persona, sessionId, mode) {
  const r = await agentCall("/api/agent/windows", windowFor(persona, sessionId, mode));
  if (r.status >= 300) console.warn("window", mode, r.status, JSON.stringify(r.body));
  return r;
}

let heartbeat;
function startHeartbeat() {
  clearInterval(heartbeat);
  heartbeat = setInterval(() => agentCall("/api/agent/heartbeat").catch(() => {}), 20_000);
}

const browser = await chromium.launch({ headless: true });

async function take(name, fn, { auth = true } = {}) {
  if (ONLY.length && !ONLY.includes(name)) return;
  console.log("take", name);
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    colorScheme: "dark",
    recordVideo: { dir: TAKES, size: { width: 1920, height: 1080 } },
    ...(auth && existsSync(STATE) ? { storageState: STATE } : {}),
  });
  await context.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => (document.documentElement.style.zoom = "1.25"));
  });
  const page = await context.newPage();
  mark(name, "start");
  await fn(page, context);
  mark(name, "end");
  await context.storageState({ path: STATE });
  const video = page.video();
  await context.close();
  renameSync(await video.path(), join(TAKES, `${name}.webm`));
}

async function glide(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 25 });
}

async function scroll(page, total, steps = 30, pause = 40) {
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, total / steps);
    await sleep(pause);
  }
}

async function browserPost(page, path, body = {}) {
  return page.evaluate(
    async ([p, b]) => {
      const r = await fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
      return { status: r.status, body: await r.json().catch(() => null) };
    },
    [path, body],
  );
}

// Off-camera: finish the remaining calibration rounds with the clock compressed.
// Rounds still start and finish through the browser API; only started_at is moved back.
async function completeRounds(page) {
  const plan = { aim: [5, 3], tetris: [5, 3], typing: [5, 3], arena: [5, 3] };
  for (const [game, counts] of Object.entries(plan)) {
    for (const count of counts) {
      const started = await browserPost(page, "/api/onboarding/rounds", { game });
      if (started.status !== 201) throw new Error(`round ${game}: ${started.status}`);
      const sessionId = started.body.session_id;
      for (let i = 0; i < count; i++) await postWindow("enrolled", sessionId, "enrollment");
      await db.collection("rounds").updateOne({ _id: sessionId }, { $set: { started_at: new Date(Date.now() - 151_000) } });
      const done = await browserPost(page, "/api/onboarding/rounds/current/finish");
      if (done.status !== 200) throw new Error(`finish ${game}: ${done.status}`);
    }
  }
}

const MON = "monitoring-" + Date.now().toString(36);

// ---- Takes ----

await take(
  "01_landing",
  async (page) => {
    await page.goto(BASE + "/");
    await sleep(2500);
    await glide(page, "h1");
    await sleep(1500);
    await scroll(page, 900, 45, 60);
    await sleep(2500);
    await scroll(page, 900, 45, 60);
    await sleep(2500);
  },
  { auth: false },
);

await take(
  "02_privacy",
  async (page) => {
    await page.goto(BASE + "/privacy");
    await sleep(2000);
    await scroll(page, 700, 35, 60);
    await sleep(3000);
    await scroll(page, 700, 35, 60);
    await sleep(3000);
  },
  { auth: false },
);

await take(
  "03_signup",
  async (page) => {
    await page.goto(BASE + "/register");
    await sleep(1500);
    await page.locator("#authName").pressSequentially("Demo User", { delay: 70 });
    await page.locator("#authEmail").pressSequentially("demo@example.com", { delay: 55 });
    await page.locator("#authPassword").pressSequentially("demo-video-only", { delay: 55 });
    await sleep(500);
    await glide(page, "#authSubmit");
    await page.click("#authSubmit");
    await page.waitForSelector("#createCode", { timeout: 15000 });
    await sleep(2500);
  },
  { auth: false },
);

await take("04_pair", async (page) => {
  await page.goto(BASE + "/onboarding");
  await sleep(1500);
  await page.locator("#pairTitle").scrollIntoViewIfNeeded();
  await glide(page, "#createCode");
  await sleep(600);
  await page.click("#createCode");
  await page.waitForFunction(() => document.querySelector("#pairingCode")?.textContent?.trim());
  const code = (await page.textContent("#pairingCode")).trim();
  writeFileSync(join(TAKES, "pairing-code.txt"), code);
  await sleep(3000);
  if (process.env.REAL_AGENT) {
    // agent_capture.py pair types this code into the tray agent. Once it pairs, the stand-in agent
    // takes over the device by rotating its token in the local demo DB.
    let device;
    while (!(device = await db.collection("devices").findOne({ pairing_state: "paired" }))) await sleep(300);
    while (!existsSync(join(TAKES, "agent-paired.txt"))) await sleep(300);
    const token = randomBytes(32).toString("base64url");
    await db.collection("devices").updateOne(
      { _id: device._id },
      { $set: { token_hash: createHash("sha256").update(token).digest("hex") }, $unset: { ws_connected: "" } },
    );
    Object.assign(agent, { deviceId: device._id, token });
  } else {
    const paired = await fetch(BASE + "/api/agent/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, name: "DEMO-PC" }),
    }).then((r) => r.json());
    Object.assign(agent, { deviceId: paired.device_id, token: paired.token });
  }
  writeFileSync(join(TAKES, "agent.json"), JSON.stringify(agent));
  startHeartbeat();
  await agentCall("/api/agent/heartbeat");
  mark("04_pair", "paired");
  await page.locator("#wizardTitle").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => !/offline/.test(document.querySelector("#agentStatus")?.textContent ?? ""), null, {
    timeout: 20000,
  });
  await sleep(3500);
});

if (!agent.token && existsSync(join(TAKES, "agent.json"))) {
  Object.assign(agent, JSON.parse((await import("node:fs")).readFileSync(join(TAKES, "agent.json"), "utf8")));
  agent.seq = {};
  startHeartbeat();
}

await take("05_game", async (page) => {
  await page.goto(BASE + "/onboarding");
  await page.locator("#wizardTitle").scrollIntoViewIfNeeded();
  await sleep(1200);
  await glide(page, "#wizardNext");
  await page.click("#wizardNext");
  await sleep(2500);
  await page.goto(BASE + "/arcade");
  await sleep(1500);
  await page.locator('[data-game="typing"]').scrollIntoViewIfNeeded();
  await glide(page, '[data-game="typing"]');
  await page.click('[data-game="typing"]');
  await sleep(1200);
  await page.click("#startGame");
  const round = await db.collection("rounds").findOne({ status: "active" }, { sort: { started_at: -1 } });
  const until = Date.now() + 12000;
  let posted = 0;
  while (Date.now() < until) {
    const word = (await page.textContent(".word-prompt").catch(() => "")).replace(/[^A-Z]/gi, "");
    if (!word) break;
    await page.locator("#typingInput").pressSequentially(word.toLowerCase(), { delay: 95 + Math.random() * 60 });
    await sleep(250);
    if (round && posted < 3) {
      await postWindow("enrolled", round._id, "enrollment");
      posted++;
    }
  }
  await sleep(800);
});

await take("06_train", async (page) => {
  await page.goto(BASE + "/onboarding");
  await completeRounds(page);
  await page.reload();
  await page.locator("#wizardTitle").scrollIntoViewIfNeeded();
  await sleep(1500);
  await glide(page, "#enrollmentProgressLabel");
  await sleep(1800);
  for (let i = 0; i < 2; i++) {
    if (await page.locator("#wizardNext").isEnabled()) {
      await glide(page, "#wizardNext");
      await page.click("#wizardNext");
      await sleep(1200);
    }
  }
  await glide(page, "#trainModel");
  await sleep(600);
  await page.click("#trainModel");
  mark("06_train", "train-click");
  await page.waitForFunction(
    () => !document.querySelector("#modelPanel")?.hasAttribute("hidden") || /trained|ready|active/i.test(document.querySelector("#onboardingState")?.textContent ?? ""),
    null,
    { timeout: 120000 },
  );
  await sleep(4000);
});

await take("07_dashboard", async (page) => {
  await page.goto(BASE + "/dashboard");
  await sleep(2000);
  for (let i = 0; i < 6; i++) {
    await postWindow("enrolled", MON, "monitoring");
    await sleep(1800);
  }
  await glide(page, "#scoreChart");
  await sleep(2000);
  await page.locator("#modelTitle").scrollIntoViewIfNeeded();
  await sleep(1000);
  await scroll(page, 500, 25, 50);
  await sleep(3500);
});

await take("08_demo_switch", async (page) => {
  await page.goto(BASE + "/demo");
  await sleep(1500);
  for (let i = 0; i < 3; i++) {
    await postWindow("enrolled", MON, "monitoring");
    await sleep(1500);
  }
  await glide(page, "#markSwitch");
  await sleep(500);
  await page.click("#markSwitch");
  mark("08_demo_switch", "switch");
  await sleep(1200);
  await scroll(page, 420, 25, 40);
  for (let i = 0; i < 6; i++) {
    await postWindow("guest", MON, "monitoring");
    mark("08_demo_switch", "guest-window");
    await sleep(1700);
  }
  await glide(page, "#demoChart");
  await sleep(3000);
});

await take("09_alert", async (page) => {
  await page.goto(BASE + "/dashboard");
  await sleep(2500);
  await glide(page, "#monitoringStatusCard");
  await sleep(2000);
  await page.locator("#alertListHeading").scrollIntoViewIfNeeded();
  await glide(page, "#alertList");
  await sleep(4000);
});

await take("10_devices", async (page) => {
  await page.goto(BASE + "/devices");
  await sleep(2500);
  await glide(page, "main button");
  await sleep(3000);
});

await take("11_gemini", async (page) => {
  // Close the open alert with normal windows, then open a fresh one so the server asks Gemini for hints.
  for (let i = 0; i < 4; i++) await postWindow("enrolled", MON, "monitoring");
  const opened = Date.now();
  for (let i = 0; i < 4; i++) await postWindow("guest", MON, "monitoring");
  let alert;
  while (!(alert = await db.collection("alerts").findOne({ status: "open", "investigation.0": { $exists: true } }))) {
    if (Date.now() - opened > 60000) throw new Error("no Gemini investigation; is GEMINI_API_KEY set?");
    await sleep(1000);
  }
  writeFileSync(join(TAKES, "gemini-hints.json"), JSON.stringify(alert.investigation, null, 1));
  await page.goto(BASE + "/dashboard");
  await sleep(2000);
  await page.locator("#alertListHeading").scrollIntoViewIfNeeded();
  await scroll(page, 250, 15, 40);
  await sleep(800);
  await glide(page, "#alertList li");
  await sleep(6000);
});

writeFileSync(join(TAKES, `log-${Date.now()}.json`), JSON.stringify(log, null, 1));
clearInterval(heartbeat);
await browser.close();
await mongo.close();
