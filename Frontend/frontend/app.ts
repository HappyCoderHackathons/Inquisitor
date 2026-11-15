import { WORDS } from "./words";

type GameId = "aim" | "tetris" | "typing" | "arena";
type Mode = {
  id: GameId;
  name: string;
  description: string;
  /** One controls line, used on the card, in the dialog, and in the stage. */
  controls: string;
  /** Needs a keyboard; disabled on touch-only devices. */
  keyboardOnly: boolean;
};
type TetrisPiece = { type: number; shape: number[][]; x: number; y: number };
type ArenaEnemy = { x: number; y: number; radius: number; speed: number };
type ArenaBullet = { x: number; y: number; vx: number; vy: number };
type ArenaPoint = { x: number; y: number };
type Phase = "idle" | "countdown" | "playing" | "paused" | "done";

const modes: Mode[] = [
  {
    id: "aim",
    name: "Aim Trainer",
    description: "Hit as many targets as you can.",
    controls: "Click or tap the target",
    keyboardOnly: false,
  },
  {
    id: "tetris",
    name: "Block Stack",
    description: "Stack blocks and clear rows.",
    controls: "Arrows or WASD to move, ↑ or W to rotate, Space to drop",
    keyboardOnly: true,
  },
  {
    id: "typing",
    name: "Word Sprint",
    description: "Type each word as quickly as you can.",
    controls: "Type each word; correct letters turn green",
    keyboardOnly: false,
  },
  {
    id: "arena",
    name: "Arena Rush",
    description: "Move, aim, and shoot.",
    controls: "Move with WASD or arrows. Aim with the mouse or Q and E. Fire with click, Space, or F",
    keyboardOnly: true,
  },
];
// Matches the server's GAME_SECONDS: one round covers a game's required onboarding time.
const ROUND_SECONDS = 150;
const COUNTDOWN_SECONDS = 3;
const SPRINT_BREAK_MS = 2000;
// Arena logic runs in these units; the canvas backing store is scaled for sharp drawing.
const ARENA_W = 720;
const ARENA_H = 316;
const ARENA_MAX_ENEMIES = 40;
const ARENA_TURN_RATE = 3.2; // radians per second while Q or E is held
const SCROLL_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  " ",
  "PageUp",
  "PageDown",
  "Home",
  "End",
]);

const $ = <T extends HTMLElement>(s: string) => document.querySelector(s) as T;
// Touch-only devices (no fine pointer) cannot play the keyboard games.
const touchOnly = window.matchMedia("(pointer: coarse)").matches && !window.matchMedia("(any-pointer: fine)").matches;
const playable = (mode: Mode) => !(touchOnly && mode.keyboardOnly);
const modeOf = (id: GameId) => modes.find((mode) => mode.id === id)!;
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

const grid = $("#gameGrid");
function gamePreview(id: GameId) {
  if (id === "aim")
    return `<div class="game-preview preview-aim" aria-hidden="true"><i></i><i></i><span>+</span><b></b></div>`;
  if (id === "tetris")
    return `<div class="game-preview preview-tetris" aria-hidden="true">${Array.from({ length: 20 }, (_, i) => `<i class="preview-cell${[1, 2, 5, 6, 7, 11, 12, 16, 17, 18].includes(i) ? " filled" : ""}"></i>`).join("")}</div>`;
  if (id === "typing")
    return `<div class="game-preview preview-typing" aria-hidden="true"><small>TYPE THIS WORD</small><strong><i>OR</i>BIT</strong><span>3 WORDS CLEARED</span></div>`;
  return `<div class="game-preview preview-arena" aria-hidden="true"><i class="preview-ship"></i><b></b><b></b><span></span></div>`;
}
grid.innerHTML = modes
  .map((m) => {
    const available = playable(m);
    const action = available
      ? `<button class="play-button" data-game="${m.id}" aria-label="Play ${m.name}">Play</button>`
      : `<button class="play-button" disabled aria-label="${m.name} needs a keyboard">Keyboard needed</button>`;
    return `<article class="game-card${available ? "" : " unavailable"}">${gamePreview(m.id)}<div class="card-copy"><h3>${m.name}</h3><p>${m.description}</p><div class="card-bottom"><span>${m.controls}</span>${action}</div></div></article>`;
  })
  .join("");

const overlay = $("#gameOverlay"),
  stage = $("#gameStage"),
  layout = $(".arcade-layout"),
  scoreEl = $("#gameScore"),
  timerEl = $("#gameTimer"),
  startButton = $<HTMLButtonElement>("#startGame"),
  pauseButton = $<HTMLButtonElement>("#pauseGame"),
  hintEl = $("#gameHint"),
  gameStatusEl = $("#gameStatus");
let returnFocus: HTMLElement | null = null;

function trapGameFocus(event: KeyboardEvent) {
  if (event.key !== "Tab" || overlay.classList.contains("hidden")) return;
  const nodes = Array.from(
    overlay.querySelectorAll<HTMLElement>(
      "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']",
    ),
  ).filter((node) => !node.classList.contains("hidden") && !node.hidden);
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const focused = document.activeElement;
  if (event.shiftKey && (focused === first || !overlay.contains(focused))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && focused === last) {
    event.preventDefault();
    first.focus();
  }
}
overlay.addEventListener("keydown", trapGameFocus);

let active: GameId | null = null,
  sprint = false,
  phase: Phase = "idle",
  remaining = ROUND_SECONDS,
  score = 0,
  shownScore = -1,
  tick = 0,
  timerId = 0,
  gravityTimerId = 0,
  countdownId = 0,
  breakId = 0,
  gamepadId = 0,
  animation = 0;
// "running" means a round is being played right now (not counting down, paused, or over).
const isRunning = () => phase === "playing";
let lastInput = 0,
  intervals: number[] = [],
  holds: number[] = [],
  down = new Map<string, number>();
let inputCounts = { keyboard: 0, controller: 0 },
  wordIndex = 0,
  linesCleared = 0,
  board: number[][] = [],
  currentPiece: TetrisPiece | null = null;
let wordDeck: string[] = [];
const best = new Map<GameId, number>();
let sprintQueue: GameId[] = [];
let sprintResults: { id: GameId; score: number }[] = [];
/** After a finished sprint, the dialog's button starts a new sprint rather than one game. */
let sprintRestart = false;

let arenaKeys = new Set<string>(),
  arenaEnemies: ArenaEnemy[] = [],
  arenaBullets: ArenaBullet[] = [];
const arenaPlayer = { x: ARENA_W / 2, y: ARENA_H / 2 },
  arenaAim = { x: 500, y: ARENA_H / 2 };
let arenaCanvas: HTMLCanvasElement | null = null,
  arenaContext: CanvasRenderingContext2D | null = null,
  arenaGrid: HTMLCanvasElement | null = null,
  arenaBounds: DOMRect | null = null,
  arenaLastFrame = 0,
  arenaLastSpawn = 0,
  arenaLastShot = 0;
const arenaPlayerShape: ArenaPoint[] = [
  { x: 15, y: 0 },
  { x: -10, y: -9 },
  { x: -7, y: 0 },
  { x: -10, y: 9 },
];

// Block Stack: cells are built once per round and only changed cells are updated.
let tetrisCells: HTMLElement[] = [];
const tetrisShown = new Uint8Array(200);
const tetrisFrame = new Uint8Array(200);
let tetrisScoreEl: HTMLElement | null = null,
  tetrisLinesEl: HTMLElement | null = null;
const tetrominoes: number[][][] = [
  [[1, 1, 1, 1]],
  [
    [1, 1],
    [1, 1],
  ],
  [
    [0, 1, 0],
    [1, 1, 1],
  ],
  [
    [0, 1, 1],
    [1, 1, 0],
  ],
  [
    [1, 1, 0],
    [0, 1, 1],
  ],
  [
    [1, 0, 0],
    [1, 1, 1],
  ],
  [
    [0, 0, 1],
    [1, 1, 1],
  ],
];

function shuffleWords(previousDeck: string[] = []) {
  const shuffled = [...WORDS];
  for (let index = shuffled.length - 1; index > 0; index--) {
    const other = Math.floor(Math.random() * (index + 1));
    [shuffled[index], shuffled[other]] = [shuffled[other], shuffled[index]];
  }
  const repeatsPreviousDeck = shuffled.every((word, index) => word === previousDeck[index]);
  if (shuffled.length > 1 && (shuffled[0] === previousDeck[previousDeck.length - 1] || repeatsPreviousDeck)) {
    [shuffled[0], shuffled[1]] = [shuffled[1], shuffled[0]];
  }
  return shuffled;
}
wordDeck = shuffleWords();

function setScore(value: number) {
  score = value;
  if (score === shownScore) return;
  shownScore = score;
  scoreEl.textContent = String(score);
  if (tetrisScoreEl) tetrisScoreEl.textContent = String(score);
}

function clearTimers() {
  clearInterval(timerId);
  clearInterval(gravityTimerId);
  clearInterval(countdownId);
  clearTimeout(breakId);
  cancelAnimationFrame(animation);
}

function setDialogOpen(open: boolean) {
  overlay.classList.toggle("hidden", !open);
  overlay.setAttribute("aria-hidden", String(!open));
  document.body.classList.toggle("game-open", open);
  // The page behind the dialog cannot be focused or clicked while a game is open.
  layout.inert = open;
  clearInterval(gamepadId);
  if (open) gamepadId = window.setInterval(pollGamepad, 100);
}

function openGame(id: GameId, isSprint = false) {
  clearTimers();
  active = id;
  sprint = isSprint;
  sprintRestart = false;
  phase = "idle";
  remaining = ROUND_SECONDS;
  shownScore = -1;
  setScore(0);
  intervals = [];
  holds = [];
  down.clear();
  inputCounts = { keyboard: 0, controller: 0 };
  wordIndex = 0;
  linesCleared = 0;
  board = [];
  currentPiece = null;
  arenaKeys.clear();
  const m = modeOf(id);
  const position = sprintResults.length + 1;
  const total = sprintResults.length + sprintQueue.length + 1;
  $("#modalMode").textContent = isSprint ? `Sprint · Game ${position} of ${total}` : "";
  $("#modalTitle").textContent = m.name;
  timerEl.textContent = String(ROUND_SECONDS);
  $("#gameInstruction").textContent = m.controls;
  // P is a letter in Word Sprint, so there only the Pause button pauses.
  const pauseHint = id === "typing" ? "" : " · P to pause";
  hintEl.textContent = isSprint
    ? `The next game starts automatically · Esc to quit${pauseHint}`
    : `Esc to close${pauseHint}`;
  setGameStatus("");
  startButton.textContent = "Start";
  startButton.classList.remove("hidden");
  pauseButton.classList.add("hidden");
  stage.className = `game-stage stage-${id}`;
  renderStage();
  const current = document.activeElement;
  if (current instanceof HTMLElement && !overlay.contains(current)) returnFocus = current;
  setDialogOpen(true);
  startButton.focus();
}

let onboardingActive = false;
let onboardingQueue = Promise.resolve();
let serverWarning: string | null = null;
function setArcadeStatus(message: string, isError = false) {
  const status = document.getElementById("arcadeStatus");
  if (status) {
    status.textContent = message;
    status.classList.toggle("error", isError);
  }
  // Errors also show inside the dialog, which covers the page's status line.
  if (isError) setGameStatus(message, true);
}
function setGameStatus(message: string, isError = false) {
  gameStatusEl.textContent = message;
  gameStatusEl.classList.toggle("error", isError);
}
function reportRound(path: string, body: object = {}) {
  if (!onboardingActive) return;
  // Queued so a sprint's finish and next start reach the server in order.
  onboardingQueue = onboardingQueue
    .then(async () => {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`Could not report this round (${response.status}).`);
    })
    .catch((error: unknown) => {
      serverWarning =
        error instanceof TypeError
          ? "Could not report this round. The server did not respond."
          : error instanceof Error
            ? error.message
            : "Could not report this round.";
      setArcadeStatus(serverWarning, true);
    });
}
async function checkOnboarding() {
  try {
    const response = await fetch("/api/onboarding");
    if (response.status === 404) return;
    if (!response.ok) throw new Error(`Could not check setup (${response.status}).`);
    const status = (await response.json()) as { state: string; ready_to_train: boolean };
    onboardingActive = status.state === "collecting";
    // Once Train is possible, offer the way back to setup.
    document.querySelectorAll<HTMLElement>(".back-to-setup").forEach((link) => {
      link.hidden = !(onboardingActive && status.ready_to_train);
    });
  } catch (error) {
    serverWarning =
      error instanceof Error && error.message.startsWith("Could not check setup")
        ? error.message
        : "Could not check setup. The server did not respond.";
    setArcadeStatus(serverWarning, true);
  }
}

/** Reset the round and count down from 3; play begins when the countdown ends. */
function start() {
  if (!active) return;
  clearTimers();
  phase = "countdown";
  remaining = ROUND_SECONDS;
  shownScore = -1;
  setScore(0);
  intervals = [];
  holds = [];
  lastInput = 0;
  inputCounts = { keyboard: 0, controller: 0 };
  wordIndex = 0;
  linesCleared = 0;
  arenaKeys.clear();
  if (active === "typing") wordDeck = shuffleWords(wordDeck);
  if (active === "tetris") initBoard();
  if (active === "arena") {
    arenaPlayer.x = ARENA_W / 2;
    arenaPlayer.y = ARENA_H / 2;
    arenaAim.x = arenaPlayer.x + 140;
    arenaAim.y = arenaPlayer.y;
    arenaEnemies = [];
    arenaBullets = [];
    arenaLastSpawn = 0;
    arenaLastShot = 0;
  }
  timerEl.textContent = String(ROUND_SECONDS);
  setGameStatus("");
  startButton.classList.add("hidden");
  pauseButton.classList.add("hidden");
  renderStage();
  stage.focus();
  let count = COUNTDOWN_SECONDS;
  showBanner(String(count), "Get ready");
  countdownId = window.setInterval(() => {
    count--;
    if (count > 0) showBanner(String(count), "Get ready");
    else {
      clearInterval(countdownId);
      begin();
    }
  }, 1000);
}

function begin() {
  if (!active) return;
  hideBanner();
  phase = "playing";
  tick = Date.now();
  reportRound("/api/onboarding/rounds", { game: active });
  pauseButton.textContent = "Pause";
  pauseButton.classList.remove("hidden");
  if (active === "aim") {
    $("#target")?.classList.remove("hidden");
    spawnTarget();
  }
  if (active === "typing") {
    // Anything typed during the countdown is cleared; the first word starts fresh.
    showWord();
    ($("#typingInput") as HTMLInputElement | null)?.focus();
  } else stage.focus();
  if (active === "tetris") renderTetris();
  timerId = window.setInterval(() => {
    if (!isRunning()) return;
    remaining--;
    timerEl.textContent = String(remaining);
    if (remaining <= 0) endRound("Time's up");
  }, 1000);
  resumeLoops();
}

/** Gravity and the arena loop, started on play and on resume. */
function resumeLoops() {
  if (active === "tetris") gravityTimerId = window.setInterval(stepTetris, 650);
  if (active === "arena") {
    arenaBounds = arenaCanvas?.getBoundingClientRect() ?? null;
    arenaLastFrame = 0;
    if (!arenaLastSpawn) arenaLastSpawn = performance.now();
    animation = requestAnimationFrame(updateArena);
  }
}

function pause() {
  if (phase !== "playing") return;
  phase = "paused";
  clearInterval(gravityTimerId);
  cancelAnimationFrame(animation);
  arenaKeys.clear();
  pauseButton.textContent = "Resume";
  showBanner("Paused", "Press P or Resume to continue");
}
function resume() {
  if (phase !== "paused") return;
  phase = "playing";
  hideBanner();
  pauseButton.textContent = "Pause";
  if (active === "typing") ($("#typingInput") as HTMLInputElement | null)?.focus();
  else stage.focus();
  resumeLoops();
}
const togglePause = () => (phase === "paused" ? resume() : pause());

function showBanner(title: string, detail: string) {
  let banner = stage.querySelector<HTMLElement>(".stage-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.className = "stage-banner";
    banner.setAttribute("role", "status");
    stage.append(banner);
  }
  banner.innerHTML = `<strong></strong><span></span>`;
  banner.querySelector("strong")!.textContent = title;
  banner.querySelector("span")!.textContent = detail;
}
function hideBanner() {
  stage.querySelector(".stage-banner")?.remove();
}

/** Every way a round ends: the timer, a topped-out stack, or a destroyed ship. */
function endRound(reason: string) {
  if (phase !== "playing" && phase !== "paused") return;
  phase = "done";
  clearTimers();
  arenaKeys.clear();
  pauseButton.classList.add("hidden");
  saveSample();
  if (active) best.set(active, Math.max(best.get(active) ?? 0, score));
  if (sprint && active) {
    sprintResults.push({ id: active, score });
    const next = sprintQueue.shift();
    if (next) {
      showBanner(`${reason} · ${plural(score, "point")}`, `Next: ${modeOf(next).name}`);
      breakId = window.setTimeout(() => {
        openGame(next, true);
        start();
      }, SPRINT_BREAK_MS);
      return;
    }
    showSprintSummary();
    return;
  }
  showResult(reason);
}

function roundDetail() {
  if (active === "typing") return plural(wordIndex, "word") + " cleared";
  if (active === "tetris") return plural(linesCleared, "line") + " cleared";
  return "";
}
function showResult(reason: string) {
  const detail = roundDetail();
  const bestScore = active ? (best.get(active) ?? score) : score;
  showBanner(
    `${reason} · ${plural(score, "point")}`,
    [detail, `Best this session: ${plural(bestScore, "point")}`].filter(Boolean).join(" · "),
  );
  hintEl.textContent = "Esc to close";
  startButton.textContent = "Play again";
  startButton.classList.remove("hidden");
  startButton.focus();
}
function showSprintSummary() {
  const total = sprintResults.reduce((sum, result) => sum + result.score, 0);
  showBanner(
    `Sprint complete · ${plural(total, "point")}`,
    sprintResults.map((result) => `${modeOf(result.id).name} ${result.score}`).join(" · "),
  );
  $("#modalMode").textContent = "Sprint";
  hintEl.textContent = "Esc to close";
  startButton.textContent = "Play sprint again";
  startButton.classList.remove("hidden");
  startButton.focus();
  sprint = false;
  sprintQueue = [];
  sprintRestart = true;
}

function startSprint() {
  const order = modes.filter(playable).map((mode) => mode.id);
  sprintResults = [];
  sprintQueue = order.slice(1);
  sprintRestart = false;
  openGame(order[0], true);
}

function close() {
  if (phase === "playing" || phase === "paused" || phase === "countdown") {
    if (!window.confirm("Quit this round? It will not be saved.")) return;
    if (phase !== "countdown") reportRound("/api/onboarding/rounds/current/cancel");
  }
  phase = "idle";
  clearTimers();
  arenaKeys.clear();
  setDialogOpen(false);
  active = null;
  sprint = false;
  sprintQueue = [];
  sprintResults = [];
  const restore = returnFocus;
  returnFocus = null;
  restore?.focus();
}

grid.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest("[data-game]") as HTMLElement | null;
  if (btn) {
    sprintResults = [];
    sprintQueue = [];
    openGame(btn.dataset.game as GameId);
  }
});
$("#sprintBtn").addEventListener("click", startSprint);
$("#closeGame").addEventListener("click", close);
startButton.addEventListener("click", () => {
  if (sprintRestart) startSprint();
  start();
});
pauseButton.addEventListener("click", togglePause);
// A round in a hidden tab or unfocused window would run out unseen.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) pause();
});
window.addEventListener("blur", pause);
window.addEventListener("resize", () => {
  arenaBounds = arenaCanvas?.getBoundingClientRect() ?? null;
});

function recordInput(source: "keyboard" | "controller") {
  const now = performance.now();
  if (lastInput) intervals.push(Math.min(now - lastInput, 3000));
  lastInput = now;
  inputCounts[source]++;
}
window.addEventListener("keydown", (e) => {
  if (overlay.classList.contains("hidden") || !active) return;
  if (e.key === "Escape") {
    close();
    return;
  }
  // Keep game keys from scrolling the page, including held (repeating) keys.
  if (phase !== "idle" && phase !== "done" && SCROLL_KEYS.has(e.key)) e.preventDefault();
  if ((e.key === "p" || e.key === "P") && !e.repeat && (phase === "playing" || phase === "paused")) {
    if (active !== "typing" || phase === "paused") {
      togglePause();
      return;
    }
  }
  if (!isRunning()) return;
  const key = e.key.toLowerCase();
  if (
    active === "arena" &&
    ["arrowleft", "arrowright", "arrowup", "arrowdown", "a", "d", "w", "s", "q", "e"].includes(key)
  )
    arenaKeys.add(key);
  if (e.repeat) return;
  recordInput("keyboard");
  down.set(e.code, performance.now());
  handleAction(e.key);
});
window.addEventListener("keyup", (e) => {
  arenaKeys.delete(e.key.toLowerCase());
  const t = down.get(e.code);
  if (t) {
    holds.push(Math.min(performance.now() - t, 2000));
    down.delete(e.code);
  }
});
function handleAction(key: string) {
  if (!active) return;
  // Aim Trainer scores only clicks or taps on the target, so there is no key action.
  if (active === "arena" && (key === " " || key.toLowerCase() === "f")) fireArena();
  if (active === "tetris") {
    if (["ArrowLeft", "a", "A"].includes(key)) moveTetris(-1);
    if (["ArrowRight", "d", "D"].includes(key)) moveTetris(1);
    if (["ArrowDown", "s", "S"].includes(key)) stepTetris();
    if (["ArrowUp", "w", "W"].includes(key)) rotateTetris();
    if (key === " ") hardDropTetris();
  }
}

function renderStage() {
  if (!active) return;
  tetrisCells = [];
  tetrisScoreEl = tetrisLinesEl = null;
  arenaCanvas = arenaContext = null;
  if (active === "aim") {
    stage.innerHTML = `<div class="aim-field"><div class="aim-cross" aria-hidden="true"></div><div class="target hidden" id="target" aria-label="Target"></div><div class="aim-caption">${modeOf("aim").controls}</div></div>`;
    $("#target").addEventListener("pointerdown", (event) => {
      event.preventDefault();
      hitTarget();
    });
  }
  if (active === "typing") renderTyping();
  if (active === "arena") renderArena();
  if (active === "tetris") {
    stage.innerHTML = `<div class="tetris-wrap"><div class="tetris-board">${'<i class="cell"></i>'.repeat(200)}</div><div class="tetris-aside"><span>SCORE</span><b id="tetrisScore">0</b><hr><span>LINES</span><b id="tetrisLines">0</b><small>← → or A D move<br>↓ or S soft drop<br>↑ or W rotate<br>Space hard drop</small></div></div>`;
    tetrisCells = Array.from(stage.querySelectorAll<HTMLElement>(".tetris-board .cell"));
    tetrisShown.fill(0);
    tetrisScoreEl = $("#tetrisScore");
    tetrisLinesEl = $("#tetrisLines");
    tetrisScoreEl.textContent = String(score);
    tetrisLinesEl.textContent = String(linesCleared);
    renderTetris();
  }
}

function spawnTarget() {
  const target = $("#target");
  if (!target) return;
  const field = target.parentElement!;
  const scale = 1.15 + Math.random() * 0.45;
  // Keep the whole scaled target inside the field.
  const radius = (target.offsetWidth * scale) / 2 + 4;
  const width = field.clientWidth,
    height = field.clientHeight;
  const x = radius + Math.random() * Math.max(0, width - 2 * radius);
  const y = radius + Math.random() * Math.max(0, height - 2 * radius - 28);
  target.style.left = `${x}px`;
  target.style.top = `${y}px`;
  target.style.transform = `translate(-50%, -50%) scale(${scale})`;
}
function hitTarget() {
  if (!isRunning() || active !== "aim") return;
  setScore(score + 10);
  spawnTarget();
}

// Word Sprint: the stage is built once per round; each new word updates it in place.
function renderTyping() {
  stage.innerHTML = `<div class="typing-game"><div class="word-label">TYPE THIS WORD</div><div class="word-prompt" id="wordPrompt"></div><div class="type-progress" id="typeProgress"></div><input class="typing-input" id="typingInput" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Start typing here…" aria-label="Type the word shown" /></div>`;
  const input = $("#typingInput") as HTMLInputElement;
  let completionPending = false;
  input.addEventListener("input", () => {
    const word = wordDeck[wordIndex];
    const typed = input.value.toUpperCase();
    const letters = stage.querySelectorAll("#wordPrompt span");
    letters.forEach((letter, index) => {
      letter.className =
        index < typed.length ? (typed[index] === word[index] ? "correct-letter" : "incorrect-letter") : "";
    });
    letters[Math.min(typed.length, word.length - 1)]?.classList.add("current-letter");
    if (!isRunning() || typed !== word || completionPending) return;
    completionPending = true;
    window.setTimeout(() => {
      completionPending = false;
      if (!isRunning() || active !== "typing" || input.value.toUpperCase() !== word) return;
      setScore(score + 10);
      wordIndex++;
      if (wordIndex === wordDeck.length) {
        wordDeck = shuffleWords(wordDeck);
        wordIndex = 0;
      }
      showWord();
    }, 250);
  });
  showWord();
}
function showWord() {
  const word = wordDeck[wordIndex];
  const prompt = $("#wordPrompt");
  const input = $("#typingInput") as HTMLInputElement | null;
  if (!prompt || !input) return;
  prompt.innerHTML = word
    .split("")
    .map((letter) => `<span>${letter}</span>`)
    .join("");
  prompt.querySelector("span")?.classList.add("current-letter");
  $("#typeProgress").textContent = `${plural(wordIndex, "word").toUpperCase()} CLEARED`;
  input.maxLength = word.length;
  input.value = "";
}

function initBoard() {
  board = Array.from({ length: 20 }, () => Array(10).fill(0));
  spawnPiece();
}
function canPlace(shape: number[][], x: number, y: number) {
  for (let rowIndex = 0; rowIndex < shape.length; rowIndex++) {
    const row = shape[rowIndex];
    for (let colIndex = 0; colIndex < row.length; colIndex++) {
      if (!row[colIndex]) continue;
      const nextX = x + colIndex,
        nextY = y + rowIndex;
      if (nextX < 0 || nextX >= 10 || nextY >= 20 || (nextY >= 0 && board[nextY][nextX])) return false;
    }
  }
  return true;
}
function spawnPiece() {
  const type = Math.floor(Math.random() * tetrominoes.length) + 1;
  const shape = tetrominoes[type - 1].map((row) => [...row]);
  currentPiece = { type, shape, x: Math.floor((10 - shape[0].length) / 2), y: 0 };
  if (!canPlace(shape, currentPiece.x, currentPiece.y)) endRound("Stack topped out");
}
function moveTetris(dx: number) {
  if (currentPiece && canPlace(currentPiece.shape, currentPiece.x + dx, currentPiece.y)) {
    currentPiece.x += dx;
    renderTetris();
  }
}
function rotateTetris() {
  if (!currentPiece) return;
  const shape = currentPiece.shape[0].map((_, col) => currentPiece!.shape.map((row) => row[col]).reverse());
  for (const offset of [0, -1, 1, -2, 2]) {
    if (canPlace(shape, currentPiece.x + offset, currentPiece.y)) {
      currentPiece.shape = shape;
      currentPiece.x += offset;
      renderTetris();
      return;
    }
  }
}
function stepTetris() {
  if (!isRunning() || active !== "tetris" || !currentPiece) return;
  if (canPlace(currentPiece.shape, currentPiece.x, currentPiece.y + 1)) currentPiece.y++;
  else lockPiece();
  renderTetris();
}
function hardDropTetris() {
  if (!currentPiece) return;
  let dropped = 0;
  while (canPlace(currentPiece.shape, currentPiece.x, currentPiece.y + 1)) {
    currentPiece.y++;
    dropped++;
  }
  setScore(score + dropped * 2);
  lockPiece();
  renderTetris();
}
function lockPiece() {
  if (!currentPiece) return;
  const piece = currentPiece;
  piece.shape.forEach((row, rowIndex) =>
    row.forEach((cell, colIndex) => {
      if (cell) board[piece.y + rowIndex][piece.x + colIndex] = piece.type;
    }),
  );
  const remainingRows = board.filter((row) => row.some((cell) => !cell));
  const cleared = 20 - remainingRows.length;
  if (cleared) {
    board = Array.from({ length: cleared }, () => Array(10).fill(0)).concat(remainingRows);
    linesCleared += cleared;
    if (tetrisLinesEl) tetrisLinesEl.textContent = String(linesCleared);
    setScore(score + ([0, 100, 300, 500, 800][cleared] ?? cleared * 200));
  }
  spawnPiece();
}
/** Draw the board plus the falling piece, touching only cells that changed. */
function renderTetris() {
  if (active !== "tetris" || tetrisCells.length !== 200) return;
  for (let row = 0; row < 20; row++) {
    const source = board[row];
    for (let col = 0; col < 10; col++) tetrisFrame[row * 10 + col] = source ? source[col] : 0;
  }
  const piece = currentPiece;
  if (piece && phase !== "idle") {
    for (let rowIndex = 0; rowIndex < piece.shape.length; rowIndex++) {
      const y = piece.y + rowIndex;
      if (y < 0 || y >= 20) continue;
      for (let colIndex = 0; colIndex < piece.shape[rowIndex].length; colIndex++)
        if (piece.shape[rowIndex][colIndex]) tetrisFrame[y * 10 + piece.x + colIndex] = piece.type;
    }
  }
  for (let index = 0; index < 200; index++) {
    const value = tetrisFrame[index];
    if (value === tetrisShown[index]) continue;
    tetrisShown[index] = value;
    tetrisCells[index].className = value ? `cell color-${value}` : "cell";
  }
}

// Arena Rush
function renderArena() {
  stage.innerHTML = `<div class="arena-game"><div class="arena-hud"><span>MOVE: WASD OR ARROWS</span><span>AIM: MOUSE OR Q / E · FIRE: CLICK, SPACE, OR F</span></div><canvas class="arena-canvas" id="arenaCanvas" aria-label="Arena game field"></canvas></div>`;
  const canvas = $("#arenaCanvas") as HTMLCanvasElement;
  // Draw in 720 by 316 units, with a backing store sized for the screen's pixel density.
  const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  canvas.width = Math.round(ARENA_W * dpr);
  canvas.height = Math.round(ARENA_H * dpr);
  arenaCanvas = canvas;
  arenaContext = canvas.getContext("2d");
  arenaContext?.setTransform(dpr, 0, 0, dpr, 0, 0);
  arenaGrid = buildArenaGrid(dpr);
  const updateAim = (event: PointerEvent) => {
    const bounds = arenaBounds ?? canvas.getBoundingClientRect();
    arenaAim.x = ((event.clientX - bounds.left) * ARENA_W) / bounds.width;
    arenaAim.y = ((event.clientY - bounds.top) * ARENA_H) / bounds.height;
  };
  canvas.addEventListener("pointerenter", () => (arenaBounds = canvas.getBoundingClientRect()));
  canvas.addEventListener("pointermove", updateAim);
  canvas.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button === 0) {
      updateAim(event);
      fireArena();
      event.preventDefault();
    }
  });
  drawArena();
}
/** The background grid never changes, so draw it once and copy it each frame. */
function buildArenaGrid(dpr: number) {
  const gridCanvas = document.createElement("canvas");
  gridCanvas.width = Math.round(ARENA_W * dpr);
  gridCanvas.height = Math.round(ARENA_H * dpr);
  const context = gridCanvas.getContext("2d");
  if (!context) return null;
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.fillStyle = "#09070D";
  context.fillRect(0, 0, ARENA_W, ARENA_H);
  context.strokeStyle = "#393243";
  context.lineWidth = 1;
  context.beginPath();
  for (let x = 36; x < ARENA_W; x += 36) {
    context.moveTo(x + 0.5, 0);
    context.lineTo(x + 0.5, ARENA_H);
  }
  for (let y = 36; y < ARENA_H; y += 36) {
    context.moveTo(0, y + 0.5);
    context.lineTo(ARENA_W, y + 0.5);
  }
  context.stroke();
  return gridCanvas;
}
function aimArena(delta: number) {
  const dx = arenaAim.x - arenaPlayer.x;
  const dy = arenaAim.y - arenaPlayer.y;
  const angle = Math.atan2(dy, dx) + delta;
  const reach = Math.max(80, Math.min(180, Math.hypot(dx, dy) || 140));
  arenaAim.x = arenaPlayer.x + Math.cos(angle) * reach;
  arenaAim.y = arenaPlayer.y + Math.sin(angle) * reach;
}
function fireArena() {
  const now = performance.now();
  if (!isRunning() || active !== "arena" || now - arenaLastShot < 160) return;
  const dx = arenaAim.x - arenaPlayer.x,
    dy = arenaAim.y - arenaPlayer.y;
  const distance = Math.hypot(dx, dy) || 1;
  const speed = 440;
  arenaBullets.push({ x: arenaPlayer.x, y: arenaPlayer.y, vx: (dx / distance) * speed, vy: (dy / distance) * speed });
  arenaLastShot = now;
}
function spawnArenaEnemy() {
  const side = Math.floor(Math.random() * 4);
  const enemy: ArenaEnemy = { x: 0, y: 0, radius: 11, speed: 38 + Math.random() * 20 };
  if (side === 0) {
    enemy.x = Math.random() * ARENA_W;
    enemy.y = -enemy.radius;
  } else if (side === 1) {
    enemy.x = ARENA_W + enemy.radius;
    enemy.y = Math.random() * ARENA_H;
  } else if (side === 2) {
    enemy.x = Math.random() * ARENA_W;
    enemy.y = ARENA_H + enemy.radius;
  } else {
    enemy.x = -enemy.radius;
    enemy.y = Math.random() * ARENA_H;
  }
  arenaEnemies.push(enemy);
}
/** Whether an enemy overlaps the ship, given the ship's heading as cos and sin. */
function enemyTouchesPlayer(enemy: ArenaEnemy, cos: number, sin: number) {
  const dx = enemy.x - arenaPlayer.x,
    dy = enemy.y - arenaPlayer.y;
  const localX = dx * cos + dy * sin;
  const localY = -dx * sin + dy * cos;
  let inside = false,
    nearestEdgeDistance = Infinity;
  for (let index = 0, previous = arenaPlayerShape.length - 1; index < arenaPlayerShape.length; previous = index++) {
    const start = arenaPlayerShape[previous],
      end = arenaPlayerShape[index];
    if (
      start.y > localY !== end.y > localY &&
      localX < ((end.x - start.x) * (localY - start.y)) / (end.y - start.y) + start.x
    )
      inside = !inside;
    const edgeX = end.x - start.x,
      edgeY = end.y - start.y;
    const edgeLength = edgeX * edgeX + edgeY * edgeY;
    const projection = Math.max(0, Math.min(1, ((localX - start.x) * edgeX + (localY - start.y) * edgeY) / edgeLength));
    const nearestX = start.x + projection * edgeX,
      nearestY = start.y + projection * edgeY;
    nearestEdgeDistance = Math.min(nearestEdgeDistance, (localX - nearestX) ** 2 + (localY - nearestY) ** 2);
  }
  return inside || nearestEdgeDistance <= enemy.radius * enemy.radius;
}
function updateArena(now: number) {
  if (!isRunning() || active !== "arena" || !arenaContext) return;
  const elapsed = arenaLastFrame ? Math.min((now - arenaLastFrame) / 1000, 0.05) : 0;
  arenaLastFrame = now;
  const horizontal =
    Number(arenaKeys.has("arrowright") || arenaKeys.has("d")) -
    Number(arenaKeys.has("arrowleft") || arenaKeys.has("a"));
  const vertical =
    Number(arenaKeys.has("arrowdown") || arenaKeys.has("s")) - Number(arenaKeys.has("arrowup") || arenaKeys.has("w"));
  const movementLength = Math.hypot(horizontal, vertical) || 1;
  arenaPlayer.x = Math.max(13, Math.min(ARENA_W - 13, arenaPlayer.x + (horizontal / movementLength) * 190 * elapsed));
  arenaPlayer.y = Math.max(13, Math.min(ARENA_H - 13, arenaPlayer.y + (vertical / movementLength) * 190 * elapsed));
  // Q and E turn the aim smoothly while held.
  const turn = Number(arenaKeys.has("e")) - Number(arenaKeys.has("q"));
  if (turn) aimArena(turn * ARENA_TURN_RATE * elapsed);

  const spawnDelay = Math.max(500, 1400 - score * 3);
  if (now - arenaLastSpawn >= spawnDelay && arenaEnemies.length < ARENA_MAX_ENEMIES) {
    spawnArenaEnemy();
    arenaLastSpawn = now;
  }

  // Bullets: move, hit at most one enemy, and drop when off the field. Arrays are compacted in place.
  let kept = 0;
  let gained = 0;
  for (let index = 0; index < arenaBullets.length; index++) {
    const bullet = arenaBullets[index];
    bullet.x += bullet.vx * elapsed;
    bullet.y += bullet.vy * elapsed;
    let hit = -1;
    for (let other = 0; other < arenaEnemies.length; other++) {
      const enemy = arenaEnemies[other];
      const reach = enemy.radius + 4;
      const ex = enemy.x - bullet.x,
        ey = enemy.y - bullet.y;
      if (ex * ex + ey * ey < reach * reach) {
        hit = other;
        break;
      }
    }
    if (hit >= 0) {
      arenaEnemies[hit] = arenaEnemies[arenaEnemies.length - 1];
      arenaEnemies.pop();
      gained += 10;
    } else if (bullet.x >= 0 && bullet.x <= ARENA_W && bullet.y >= 0 && bullet.y <= ARENA_H)
      arenaBullets[kept++] = bullet;
  }
  arenaBullets.length = kept;
  if (gained) setScore(score + gained);

  const aimAngle = Math.atan2(arenaAim.y - arenaPlayer.y, arenaAim.x - arenaPlayer.x);
  const cos = Math.cos(aimAngle),
    sin = Math.sin(aimAngle);
  for (const enemy of arenaEnemies) {
    const dx = arenaPlayer.x - enemy.x,
      dy = arenaPlayer.y - enemy.y;
    const distance = Math.hypot(dx, dy) || 1;
    enemy.x += (dx / distance) * enemy.speed * elapsed;
    enemy.y += (dy / distance) * enemy.speed * elapsed;
    if (enemyTouchesPlayer(enemy, cos, sin)) {
      drawArena();
      endRound("Ship destroyed");
      return;
    }
  }
  drawArena();
  animation = requestAnimationFrame(updateArena);
}
function drawArena() {
  const context = arenaContext;
  if (!context) return;
  if (arenaGrid) context.drawImage(arenaGrid, 0, 0, ARENA_W, ARENA_H);
  else {
    context.fillStyle = "#09070D";
    context.fillRect(0, 0, ARENA_W, ARENA_H);
  }
  context.fillStyle = "#FB7185";
  context.beginPath();
  for (const enemy of arenaEnemies) {
    context.moveTo(enemy.x + enemy.radius, enemy.y);
    context.arc(enemy.x, enemy.y, enemy.radius, 0, Math.PI * 2);
  }
  context.fill();
  context.fillStyle = "#F7F4FC";
  context.beginPath();
  for (const enemy of arenaEnemies) {
    context.moveTo(enemy.x + 4, enemy.y);
    context.arc(enemy.x, enemy.y, 4, 0, Math.PI * 2);
  }
  context.fill();
  context.fillStyle = "#C4B5FD";
  context.beginPath();
  for (const bullet of arenaBullets) {
    context.moveTo(bullet.x + 3, bullet.y);
    context.arc(bullet.x, bullet.y, 3, 0, Math.PI * 2);
  }
  context.fill();
  const aimAngle = Math.atan2(arenaAim.y - arenaPlayer.y, arenaAim.x - arenaPlayer.x);
  context.save();
  context.translate(arenaPlayer.x, arenaPlayer.y);
  context.rotate(aimAngle);
  context.beginPath();
  context.moveTo(arenaPlayerShape[0].x, arenaPlayerShape[0].y);
  for (let index = 1; index < arenaPlayerShape.length; index++)
    context.lineTo(arenaPlayerShape[index].x, arenaPlayerShape[index].y);
  context.closePath();
  context.fillStyle = "#A3E635";
  context.fill();
  context.restore();
  context.beginPath();
  context.arc(arenaAim.x, arenaAim.y, 9, 0, Math.PI * 2);
  context.moveTo(arenaAim.x - 13, arenaAim.y);
  context.lineTo(arenaAim.x + 13, arenaAim.y);
  context.moveTo(arenaAim.x, arenaAim.y - 13);
  context.lineTo(arenaAim.x, arenaAim.y + 13);
  context.strokeStyle = "#C4B5FD";
  context.stroke();
}

function saveSample() {
  reportRound("/api/onboarding/rounds/current/finish");
  onboardingQueue = onboardingQueue.then(checkOnboarding);
  const avg = (a: number[]) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0);
  const mean = avg(intervals),
    std = Math.sqrt(avg(intervals.map((x) => (x - mean) ** 2)));
  const d = {
    game: active,
    duration_ms: Math.min(Date.now() - tick, 600000),
    input_count: inputCounts.keyboard + inputCounts.controller,
    keyboard_count: inputCounts.keyboard,
    controller_count: inputCounts.controller,
    mean_interval_ms: Math.round(mean),
    interval_std_ms: Math.round(std),
    mean_hold_ms: Math.round(avg(holds)),
    score,
    source: inputCounts.controller ? "mixed" : "keyboard",
  };
  setArcadeStatus("Saving this round…");
  void fetch("/api/samples", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(d),
  })
    .then(async (response) => {
      if (!response.ok) throw new Error(`Could not save this round (${response.status}).`);
      await refreshSummary();
    })
    .catch((error: unknown) => {
      setArcadeStatus(
        error instanceof Error && error.message.startsWith("Could not save")
          ? error.message
          : "Could not save this round. The server did not respond.",
        true,
      );
    });
}
type RecentGame = { game: string; created_at: string; score: number };
async function refreshSummary() {
  setArcadeStatus("Loading recent games…");
  try {
    const response = await fetch("/api/summary");
    if (!response.ok) throw new Error(`Could not load recent games (${response.status}).`);
    const data = (await response.json()) as { samples: number; recent: RecentGame[] };
    $("#sampleCount").textContent = String(data.samples);
    $("#sampleCountLabel").textContent = data.samples === 1 ? "round played" : "rounds played";
    const list = $("#recentList");
    list.innerHTML = data.recent.length
      ? data.recent
          .map(
            (item) =>
              `<div class="recent-item"><span><b>${modes.find((mode) => mode.id === item.game)?.name || item.game}</b><small>${new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span><strong>${plural(item.score, "point")}</strong></div>`,
          )
          .join("")
      : `<div class="empty">No games played yet.</div>`;
    const message = data.samples ? "Recent games loaded from this machine." : "No games saved on this machine yet.";
    setArcadeStatus(serverWarning ? `${message} ${serverWarning}` : message, Boolean(serverWarning));
  } catch (error) {
    setArcadeStatus(
      error instanceof Error && error.message.startsWith("Could not load recent games")
        ? error.message
        : "Could not load recent games. The server did not respond.",
      true,
    );
  }
}
$("#clearBtn").addEventListener("click", async () => {
  if (!window.confirm("Clear your game history on this machine?")) return;
  setArcadeStatus("Clearing saved rounds…");
  try {
    const response = await fetch("/api/samples", { method: "DELETE" });
    if (!response.ok) throw new Error(`Could not clear saved rounds (${response.status}).`);
    await refreshSummary();
  } catch (error) {
    setArcadeStatus(
      error instanceof Error && error.message.startsWith("Could not clear")
        ? error.message
        : "Could not clear saved rounds. The server did not respond.",
      true,
    );
  }
});

const GAMEPAD_KEYS: Record<number, string> = {
  0: " ",
  1: "ArrowDown",
  2: "ArrowLeft",
  3: "ArrowRight",
  12: "ArrowUp",
  13: "ArrowDown",
  14: "ArrowLeft",
  15: "ArrowRight",
};
/** Polled only while a game is open. */
function pollGamepad() {
  const pads = navigator.getGamepads?.() || [];
  let pad: Gamepad | null = null;
  for (const candidate of pads) if (candidate) pad = pad ?? candidate;
  if (!pad) return;
  pad.buttons.forEach((button, index) => {
    const key = `pad${index}`;
    if (button.pressed) {
      if (!down.has(key)) {
        recordInput("controller");
        down.set(key, performance.now());
        if (isRunning() && GAMEPAD_KEYS[index]) handleAction(GAMEPAD_KEYS[index]);
      }
    } else if (down.has(key)) {
      holds.push(performance.now() - down.get(key)!);
      down.delete(key);
    }
  });
}

void (async () => {
  await checkOnboarding();
  await refreshSummary();
})();
