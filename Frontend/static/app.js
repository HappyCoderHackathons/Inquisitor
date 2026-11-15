"use strict";
(() => {
  // frontend/words.ts
  var WORDS = [
    "ABILITY",
    "ABOUT",
    "ABOVE",
    "ACCOUNT",
    "ACROSS",
    "ACTION",
    "ACTIVITY",
    "ACTUALLY",
    "ADDITION",
    "AFTER",
    "AGAIN",
    "ALMOST",
    "ALONE",
    "ALONG",
    "ALREADY",
    "ALSO",
    "ALWAYS",
    "AMOUNT",
    "ANALYSIS",
    "ANSWER",
    "ANYTHING",
    "APPEAR",
    "APPEARED",
    "APPLIED",
    "APPROACH",
    "AREA",
    "AREAS",
    "AROUND",
    "ASKED",
    "AVERAGE",
    "AWAY",
    "BACK",
    "BASED",
    "BASIC",
    "BASIS",
    "BECAME",
    "BECAUSE",
    "BECOME",
    "BEEN",
    "BEFORE",
    "BEGAN",
    "BEHAVIOR",
    "BEHIND",
    "BEING",
    "BELIEVE",
    "BELOW",
    "BEST",
    "BETTER",
    "BETWEEN",
    "BEYOND",
    "BODY",
    "BOOK",
    "BOOKS",
    "BOTH",
    "BROUGHT",
    "BUILDING",
    "BUSINESS",
    "CALLED",
    "CAME",
    "CAPACITY",
    "CAPITAL",
    "CARE",
    "CARRIED",
    "CASE",
    "CASES",
    "CAUSE",
    "CENTER",
    "CENTURY",
    "CERTAIN",
    "CHANGE",
    "CHANGES",
    "CHAPTER",
    "CHILD",
    "CHILDREN",
    "CHURCH",
    "CITY",
    "CLASS",
    "CLEAR",
    "CLEARLY",
    "CLINICAL",
    "CLOSE",
    "COLLEGE",
    "COME",
    "COMMON",
    "COMPANY",
    "COMPLETE",
    "COMPLEX",
    "COMPUTER",
    "CONCEPT",
    "CONFLICT",
    "CONSIDER",
    "CONSTANT",
    "CONTENT",
    "CONTINUE",
    "CONTRAST",
    "CONTROL",
    "COUNCIL",
    "COUNTRY",
    "COURSE",
    "COURT",
    "CREATED",
    "CRITICAL",
    "CULTURAL",
    "CULTURE",
    "CURRENT",
    "DATA",
    "DAYS",
    "DECISION",
    "DEFINED",
    "DEGREE",
    "DESIGN",
    "DESIGNED",
    "DEVELOP",
    "DIRECT",
    "DIRECTLY",
    "DISEASE",
    "DISTANCE",
    "DIVISION",
    "DONE",
    "DOWN",
    "DURING",
    "EARLIER",
    "EARLY",
    "ECONOMY",
    "EFFECT",
    "EFFECTS",
    "EITHER",
    "ELEMENTS",
    "ENERGY",
    "ENOUGH",
    "EVEN",
    "EVENTS",
    "EVER",
    "EVIDENCE",
    "EXAMPLE",
    "EXAMPLES",
    "EXCHANGE",
    "EXPECTED",
    "FACE",
    "FACT",
    "FACTORS",
    "FAMILY",
    "FATHER",
    "FEATURES",
    "FEELING",
    "FIELD",
    "FIGURE",
    "FINALLY",
    "FIND",
    "FIRST",
    "FIVE",
    "FORCE",
    "FORCES",
    "FORM",
    "FORMS",
    "FOUND",
    "FOUR",
    "FREE",
    "FRIENDS",
    "FULL",
    "FUNCTION",
    "FURTHER",
    "FUTURE",
    "GIVE",
    "GIVEN",
    "GOING",
    "GOOD",
    "GREAT",
    "GREATER",
    "GROUND",
    "GROUP",
    "GROUPS",
    "GROWTH",
    "HAND",
    "HANDS",
    "HAPPENED",
    "HAVE",
    "HAVING",
    "HEAD",
    "HEALTH",
    "HEART",
    "HELP",
    "HERE",
    "HIGH",
    "HIGHER",
    "HISTORY",
    "HOME",
    "HOSPITAL",
    "HOUSE",
    "HOWEVER",
    "HUMAN",
    "INCLUDE",
    "INCLUDED",
    "INCOME",
    "INCREASE",
    "INDEED",
    "INDUSTRY",
    "INSTANCE",
    "INSTEAD",
    "INTEREST",
    "INTERNAL",
    "INTO",
    "INVOLVED",
    "JOURNAL",
    "JUST",
    "KNOW",
    "KNOWN",
    "LAND",
    "LANGUAGE",
    "LARGE",
    "LAST",
    "LATER",
    "LEARNING",
    "LEAST",
    "LEFT",
    "LESS",
    "LETTER",
    "LEVEL",
    "LEVELS",
    "LIBRARY",
    "LIFE",
    "LIGHT",
    "LIKE",
    "LIKELY",
    "LIMITED",
    "LINE",
    "LITTLE",
    "LIVING",
    "LOCAL",
    "LONG",
    "LONGER",
    "LOOK",
    "LOOKED",
    "LOOKING",
    "LOVE",
    "LOWER",
    "MADE",
    "MAJOR",
    "MAJORITY",
    "MAKE",
    "MAKES",
    "MAKING",
    "MANY",
    "MARKET",
    "MARRIAGE",
    "MATERIAL",
    "MATTER",
    "MEANING",
    "MEANS",
    "MEASURES",
    "MEETING",
    "MEMBERS",
    "METHOD",
    "METHODS",
    "MIDDLE",
    "MILITARY",
    "MIND",
    "MINISTER",
    "MINUTES",
    "MODEL",
    "MODERN",
    "MOMENT",
    "MONEY",
    "MORE",
    "MORNING",
    "MOST",
    "MOTHER",
    "MOVEMENT",
    "MUCH",
    "NAME",
    "NATURAL",
    "NATURE",
    "NEED",
    "NEEDED",
    "NEEDS",
    "NEGATIVE",
    "NEXT",
    "NIGHT",
    "NORMAL",
    "NORTH",
    "NOTHING",
    "NUMBER",
    "NUMBERS",
    "OBJECT",
    "OBSERVED",
    "OBTAINED",
    "OFFICE",
    "OFTEN",
    "ONCE",
    "ONLY",
    "OPEN",
    "ORDER",
    "ORIGINAL",
    "OTHER",
    "OTHERS",
    "OUTSIDE",
    "OVER",
    "PAPER",
    "PART",
    "PARTY",
    "PATIENT",
    "PATTERN",
    "PEOPLE",
    "PERHAPS",
    "PERIOD",
    "PERSON",
    "PERSONAL",
    "PERSONS",
    "PHYSICAL",
    "PLACE",
    "PLANNING",
    "POINT",
    "POINTS",
    "POLICY",
    "POLITICS",
    "POSITION",
    "POSITIVE",
    "POSSIBLE",
    "POWER",
    "PRACTICE",
    "PREPARED",
    "PRESENCE",
    "PRESENT",
    "PRESS",
    "PRESSURE",
    "PREVIOUS",
    "PRIMARY",
    "PRIVATE",
    "PROBABLY",
    "PROBLEM",
    "PROBLEMS",
    "PROCESS",
    "PRODUCE",
    "PRODUCED",
    "PRODUCT",
    "PROGRAM",
    "PROGRAMS",
    "PROGRESS",
    "PROJECT",
    "PROPERTY",
    "PROVIDE",
    "PROVIDED",
    "PROVIDES",
    "PUBLIC",
    "PURPOSE",
    "QUALITY",
    "QUESTION",
    "QUITE",
    "RANGE",
    "RATE",
    "RATHER",
    "REACTION",
    "READING",
    "REAL",
    "REALLY",
    "REASON",
    "RECEIVED",
    "REGION",
    "RELATED",
    "RELATION",
    "RELATIVE",
    "RELIGION",
    "REMAINED",
    "REMEMBER",
    "REPORT",
    "REQUIRED",
    "REQUIRES",
    "RESEARCH",
    "RESPECT",
    "RESPONSE",
    "RESULT",
    "RETURN",
    "RETURNED",
    "REVIEW",
    "RIGHT",
    "ROLE",
    "ROOM",
    "SAID",
    "SAME",
    "SCHOOL",
    "SECOND",
    "SECTION",
    "SECURITY",
    "SEEMED",
    "SEEN",
    "SELF",
    "SENSE",
    "SEPARATE",
    "SERIES",
    "SERVICE",
    "SERVICES",
    "SEVERAL",
    "SHORT",
    "SHOWS",
    "SIDE",
    "SIMILAR",
    "SIMPLE",
    "SIMPLY",
    "SINCE",
    "SINGLE",
    "SMALL",
    "SOCIAL",
    "SOCIETY",
    "SOLUTION",
    "SOME",
    "SOMEONE",
    "SOURCE",
    "SOUTH",
    "SPACE",
    "SPECIAL",
    "SPECIES",
    "SPECIFIC",
    "STANDARD",
    "STATE",
    "STATES",
    "STILL",
    "STORY",
    "STRENGTH",
    "STRONG",
    "STUDIES",
    "STUDY",
    "SUBJECT",
    "SUCCESS",
    "SUCH",
    "SUPPORT",
    "SURFACE",
    "SYSTEM",
    "TABLE",
    "TAKE",
    "TAKEN",
    "TAKING",
    "TEACHING",
    "THAN",
    "THAT",
    "THEN",
    "THEORY",
    "THERE",
    "THEY",
    "THING",
    "THINGS",
    "THINK",
    "THINKING",
    "THIRD",
    "THIS",
    "THOUGH",
    "THOUGHT",
    "THREE",
    "THROUGH",
    "THUS",
    "TIME",
    "TIMES",
    "TODAY",
    "TOGETHER",
    "TOLD",
    "TOOK",
    "TOTAL",
    "TOWARD",
    "TOWARDS",
    "TRADE",
    "TRAINING",
    "TRUE",
    "TURNED",
    "TYPE",
    "TYPES",
    "UNDER",
    "USED",
    "USING",
    "USUALLY",
    "VALUE",
    "VALUES",
    "VARIOUS",
    "VERY",
    "VIEW",
    "VOLUME",
    "WANT",
    "WANTED",
    "WATER",
    "WELL",
    "WENT",
    "WERE",
    "WHAT",
    "WHEN",
    "WHERE",
    "WHOLE",
    "WITH",
    "WITHIN",
    "WITHOUT",
    "WOMAN",
    "WOMEN",
    "WORDS",
    "WORK",
    "WORKERS",
    "WORKING",
    "WORKS",
    "WORLD",
    "WRITING",
    "WRITTEN",
    "YEAR",
    "YEARS",
    "YOUNG"
  ];

  // frontend/app.ts
  var modes = [
    {
      id: "aim",
      name: "Aim Trainer",
      description: "Hit as many targets as you can.",
      controls: "Click or tap the target",
      keyboardOnly: false
    },
    {
      id: "tetris",
      name: "Block Stack",
      description: "Stack blocks and clear rows.",
      controls: "Arrows or WASD to move, \u2191 or W to rotate, Space to drop",
      keyboardOnly: true
    },
    {
      id: "typing",
      name: "Word Sprint",
      description: "Type each word as quickly as you can.",
      controls: "Type each word; correct letters turn green",
      keyboardOnly: false
    },
    {
      id: "arena",
      name: "Arena Rush",
      description: "Move, aim, and shoot.",
      controls: "Move with WASD or arrows. Aim with the mouse or Q and E. Fire with click, Space, or F",
      keyboardOnly: true
    }
  ];
  var ROUND_SECONDS = 150;
  var COUNTDOWN_SECONDS = 3;
  var SPRINT_BREAK_MS = 2e3;
  var ARENA_W = 720;
  var ARENA_H = 316;
  var ARENA_MAX_ENEMIES = 40;
  var ARENA_TURN_RATE = 3.2;
  var SCROLL_KEYS = /* @__PURE__ */ new Set([
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    " ",
    "PageUp",
    "PageDown",
    "Home",
    "End"
  ]);
  var $ = (s) => document.querySelector(s);
  var touchOnly = window.matchMedia("(pointer: coarse)").matches && !window.matchMedia("(any-pointer: fine)").matches;
  var playable = (mode) => !(touchOnly && mode.keyboardOnly);
  var modeOf = (id) => modes.find((mode) => mode.id === id);
  var plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
  var grid = $("#gameGrid");
  function gamePreview(id) {
    if (id === "aim")
      return `<div class="game-preview preview-aim" aria-hidden="true"><i></i><i></i><span>+</span><b></b></div>`;
    if (id === "tetris")
      return `<div class="game-preview preview-tetris" aria-hidden="true">${Array.from({ length: 20 }, (_, i) => `<i class="preview-cell${[1, 2, 5, 6, 7, 11, 12, 16, 17, 18].includes(i) ? " filled" : ""}"></i>`).join("")}</div>`;
    if (id === "typing")
      return `<div class="game-preview preview-typing" aria-hidden="true"><small>TYPE THIS WORD</small><strong><i>OR</i>BIT</strong><span>3 WORDS CLEARED</span></div>`;
    return `<div class="game-preview preview-arena" aria-hidden="true"><i class="preview-ship"></i><b></b><b></b><span></span></div>`;
  }
  grid.innerHTML = modes.map((m) => {
    const available = playable(m);
    const action = available ? `<button class="play-button" data-game="${m.id}" aria-label="Play ${m.name}">Play</button>` : `<button class="play-button" disabled aria-label="${m.name} needs a keyboard">Keyboard needed</button>`;
    return `<article class="game-card${available ? "" : " unavailable"}">${gamePreview(m.id)}<div class="card-copy"><h3>${m.name}</h3><p>${m.description}</p><div class="card-bottom"><span>${m.controls}</span>${action}</div></div></article>`;
  }).join("");
  var overlay = $("#gameOverlay");
  var stage = $("#gameStage");
  var layout = $(".arcade-layout");
  var scoreEl = $("#gameScore");
  var timerEl = $("#gameTimer");
  var startButton = $("#startGame");
  var pauseButton = $("#pauseGame");
  var hintEl = $("#gameHint");
  var gameStatusEl = $("#gameStatus");
  var returnFocus = null;
  function trapGameFocus(event) {
    if (event.key !== "Tab" || overlay.classList.contains("hidden")) return;
    const nodes = Array.from(
      overlay.querySelectorAll(
        "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']"
      )
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
  var active = null;
  var sprint = false;
  var phase = "idle";
  var remaining = ROUND_SECONDS;
  var score = 0;
  var shownScore = -1;
  var tick = 0;
  var timerId = 0;
  var gravityTimerId = 0;
  var countdownId = 0;
  var breakId = 0;
  var gamepadId = 0;
  var animation = 0;
  var isRunning = () => phase === "playing";
  var lastInput = 0;
  var intervals = [];
  var holds = [];
  var down = /* @__PURE__ */ new Map();
  var inputCounts = { keyboard: 0, controller: 0 };
  var wordIndex = 0;
  var linesCleared = 0;
  var board = [];
  var currentPiece = null;
  var wordDeck = [];
  var best = /* @__PURE__ */ new Map();
  var sprintQueue = [];
  var sprintResults = [];
  var sprintRestart = false;
  var arenaKeys = /* @__PURE__ */ new Set();
  var arenaEnemies = [];
  var arenaBullets = [];
  var arenaPlayer = { x: ARENA_W / 2, y: ARENA_H / 2 };
  var arenaAim = { x: 500, y: ARENA_H / 2 };
  var arenaCanvas = null;
  var arenaContext = null;
  var arenaGrid = null;
  var arenaBounds = null;
  var arenaLastFrame = 0;
  var arenaLastSpawn = 0;
  var arenaLastShot = 0;
  var arenaPlayerShape = [
    { x: 15, y: 0 },
    { x: -10, y: -9 },
    { x: -7, y: 0 },
    { x: -10, y: 9 }
  ];
  var tetrisCells = [];
  var tetrisShown = new Uint8Array(200);
  var tetrisFrame = new Uint8Array(200);
  var tetrisScoreEl = null;
  var tetrisLinesEl = null;
  var tetrominoes = [
    [[1, 1, 1, 1]],
    [
      [1, 1],
      [1, 1]
    ],
    [
      [0, 1, 0],
      [1, 1, 1]
    ],
    [
      [0, 1, 1],
      [1, 1, 0]
    ],
    [
      [1, 1, 0],
      [0, 1, 1]
    ],
    [
      [1, 0, 0],
      [1, 1, 1]
    ],
    [
      [0, 0, 1],
      [1, 1, 1]
    ]
  ];
  function shuffleWords(previousDeck = []) {
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
  function setScore(value) {
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
  function setDialogOpen(open) {
    overlay.classList.toggle("hidden", !open);
    overlay.setAttribute("aria-hidden", String(!open));
    document.body.classList.toggle("game-open", open);
    layout.inert = open;
    clearInterval(gamepadId);
    if (open) gamepadId = window.setInterval(pollGamepad, 100);
  }
  function openGame(id, isSprint = false) {
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
    $("#modalMode").textContent = isSprint ? `Sprint \xB7 Game ${position} of ${total}` : "";
    $("#modalTitle").textContent = m.name;
    timerEl.textContent = String(ROUND_SECONDS);
    $("#gameInstruction").textContent = m.controls;
    const pauseHint = id === "typing" ? "" : " \xB7 P to pause";
    hintEl.textContent = isSprint ? `The next game starts automatically \xB7 Esc to quit${pauseHint}` : `Esc to close${pauseHint}`;
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
  var onboardingActive = false;
  var onboardingQueue = Promise.resolve();
  var serverWarning = null;
  function setArcadeStatus(message, isError = false) {
    const status = document.getElementById("arcadeStatus");
    if (status) {
      status.textContent = message;
      status.classList.toggle("error", isError);
    }
    if (isError) setGameStatus(message, true);
  }
  function setGameStatus(message, isError = false) {
    gameStatusEl.textContent = message;
    gameStatusEl.classList.toggle("error", isError);
  }
  function reportRound(path, body = {}) {
    if (!onboardingActive) return;
    onboardingQueue = onboardingQueue.then(async () => {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      if (!response.ok) throw new Error(`Could not report this round (${response.status}).`);
    }).catch((error) => {
      serverWarning = error instanceof TypeError ? "Could not report this round. The server did not respond." : error instanceof Error ? error.message : "Could not report this round.";
      setArcadeStatus(serverWarning, true);
    });
  }
  async function checkOnboarding() {
    try {
      const response = await fetch("/api/onboarding");
      if (response.status === 404) return;
      if (!response.ok) throw new Error(`Could not check setup (${response.status}).`);
      const status = await response.json();
      onboardingActive = status.state === "collecting";
      document.querySelectorAll(".back-to-setup").forEach((link) => {
        link.hidden = !(onboardingActive && status.ready_to_train);
      });
    } catch (error) {
      serverWarning = error instanceof Error && error.message.startsWith("Could not check setup") ? error.message : "Could not check setup. The server did not respond.";
      setArcadeStatus(serverWarning, true);
    }
  }
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
    }, 1e3);
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
      showWord();
      $("#typingInput")?.focus();
    } else stage.focus();
    if (active === "tetris") renderTetris();
    timerId = window.setInterval(() => {
      if (!isRunning()) return;
      remaining--;
      timerEl.textContent = String(remaining);
      if (remaining <= 0) endRound("Time's up");
    }, 1e3);
    resumeLoops();
  }
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
    if (active === "typing") $("#typingInput")?.focus();
    else stage.focus();
    resumeLoops();
  }
  var togglePause = () => phase === "paused" ? resume() : pause();
  function showBanner(title, detail) {
    let banner = stage.querySelector(".stage-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "stage-banner";
      banner.setAttribute("role", "status");
      stage.append(banner);
    }
    banner.innerHTML = `<strong></strong><span></span>`;
    banner.querySelector("strong").textContent = title;
    banner.querySelector("span").textContent = detail;
  }
  function hideBanner() {
    stage.querySelector(".stage-banner")?.remove();
  }
  function endRound(reason) {
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
        showBanner(`${reason} \xB7 ${plural(score, "point")}`, `Next: ${modeOf(next).name}`);
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
  function showResult(reason) {
    const detail = roundDetail();
    const bestScore = active ? best.get(active) ?? score : score;
    showBanner(
      `${reason} \xB7 ${plural(score, "point")}`,
      [detail, `Best this session: ${plural(bestScore, "point")}`].filter(Boolean).join(" \xB7 ")
    );
    hintEl.textContent = "Esc to close";
    startButton.textContent = "Play again";
    startButton.classList.remove("hidden");
    startButton.focus();
  }
  function showSprintSummary() {
    const total = sprintResults.reduce((sum, result) => sum + result.score, 0);
    showBanner(
      `Sprint complete \xB7 ${plural(total, "point")}`,
      sprintResults.map((result) => `${modeOf(result.id).name} ${result.score}`).join(" \xB7 ")
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
    const btn = e.target.closest("[data-game]");
    if (btn) {
      sprintResults = [];
      sprintQueue = [];
      openGame(btn.dataset.game);
    }
  });
  $("#sprintBtn").addEventListener("click", startSprint);
  $("#closeGame").addEventListener("click", close);
  startButton.addEventListener("click", () => {
    if (sprintRestart) startSprint();
    start();
  });
  pauseButton.addEventListener("click", togglePause);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause();
  });
  window.addEventListener("blur", pause);
  window.addEventListener("resize", () => {
    arenaBounds = arenaCanvas?.getBoundingClientRect() ?? null;
  });
  function recordInput(source) {
    const now = performance.now();
    if (lastInput) intervals.push(Math.min(now - lastInput, 3e3));
    lastInput = now;
    inputCounts[source]++;
  }
  window.addEventListener("keydown", (e) => {
    if (overlay.classList.contains("hidden") || !active) return;
    if (e.key === "Escape") {
      close();
      return;
    }
    if (phase !== "idle" && phase !== "done" && SCROLL_KEYS.has(e.key)) e.preventDefault();
    if ((e.key === "p" || e.key === "P") && !e.repeat && (phase === "playing" || phase === "paused")) {
      if (active !== "typing" || phase === "paused") {
        togglePause();
        return;
      }
    }
    if (!isRunning()) return;
    const key = e.key.toLowerCase();
    if (active === "arena" && ["arrowleft", "arrowright", "arrowup", "arrowdown", "a", "d", "w", "s", "q", "e"].includes(key))
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
      holds.push(Math.min(performance.now() - t, 2e3));
      down.delete(e.code);
    }
  });
  function handleAction(key) {
    if (!active) return;
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
      stage.innerHTML = `<div class="tetris-wrap"><div class="tetris-board">${'<i class="cell"></i>'.repeat(200)}</div><div class="tetris-aside"><span>SCORE</span><b id="tetrisScore">0</b><hr><span>LINES</span><b id="tetrisLines">0</b><small>\u2190 \u2192 or A D move<br>\u2193 or S soft drop<br>\u2191 or W rotate<br>Space hard drop</small></div></div>`;
      tetrisCells = Array.from(stage.querySelectorAll(".tetris-board .cell"));
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
    const field = target.parentElement;
    const scale = 1.15 + Math.random() * 0.45;
    const radius = target.offsetWidth * scale / 2 + 4;
    const width = field.clientWidth, height = field.clientHeight;
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
  function renderTyping() {
    stage.innerHTML = `<div class="typing-game"><div class="word-label">TYPE THIS WORD</div><div class="word-prompt" id="wordPrompt"></div><div class="type-progress" id="typeProgress"></div><input class="typing-input" id="typingInput" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Start typing here\u2026" aria-label="Type the word shown" /></div>`;
    const input = $("#typingInput");
    let completionPending = false;
    input.addEventListener("input", () => {
      const word = wordDeck[wordIndex];
      const typed = input.value.toUpperCase();
      const letters = stage.querySelectorAll("#wordPrompt span");
      letters.forEach((letter, index) => {
        letter.className = index < typed.length ? typed[index] === word[index] ? "correct-letter" : "incorrect-letter" : "";
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
    const input = $("#typingInput");
    if (!prompt || !input) return;
    prompt.innerHTML = word.split("").map((letter) => `<span>${letter}</span>`).join("");
    prompt.querySelector("span")?.classList.add("current-letter");
    $("#typeProgress").textContent = `${plural(wordIndex, "word").toUpperCase()} CLEARED`;
    input.maxLength = word.length;
    input.value = "";
  }
  function initBoard() {
    board = Array.from({ length: 20 }, () => Array(10).fill(0));
    spawnPiece();
  }
  function canPlace(shape, x, y) {
    for (let rowIndex = 0; rowIndex < shape.length; rowIndex++) {
      const row = shape[rowIndex];
      for (let colIndex = 0; colIndex < row.length; colIndex++) {
        if (!row[colIndex]) continue;
        const nextX = x + colIndex, nextY = y + rowIndex;
        if (nextX < 0 || nextX >= 10 || nextY >= 20 || nextY >= 0 && board[nextY][nextX]) return false;
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
  function moveTetris(dx) {
    if (currentPiece && canPlace(currentPiece.shape, currentPiece.x + dx, currentPiece.y)) {
      currentPiece.x += dx;
      renderTetris();
    }
  }
  function rotateTetris() {
    if (!currentPiece) return;
    const shape = currentPiece.shape[0].map((_, col) => currentPiece.shape.map((row) => row[col]).reverse());
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
    piece.shape.forEach(
      (row, rowIndex) => row.forEach((cell, colIndex) => {
        if (cell) board[piece.y + rowIndex][piece.x + colIndex] = piece.type;
      })
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
  function renderArena() {
    stage.innerHTML = `<div class="arena-game"><div class="arena-hud"><span>MOVE: WASD OR ARROWS</span><span>AIM: MOUSE OR Q / E \xB7 FIRE: CLICK, SPACE, OR F</span></div><canvas class="arena-canvas" id="arenaCanvas" aria-label="Arena game field"></canvas></div>`;
    const canvas = $("#arenaCanvas");
    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    canvas.width = Math.round(ARENA_W * dpr);
    canvas.height = Math.round(ARENA_H * dpr);
    arenaCanvas = canvas;
    arenaContext = canvas.getContext("2d");
    arenaContext?.setTransform(dpr, 0, 0, dpr, 0, 0);
    arenaGrid = buildArenaGrid(dpr);
    const updateAim = (event) => {
      const bounds = arenaBounds ?? canvas.getBoundingClientRect();
      arenaAim.x = (event.clientX - bounds.left) * ARENA_W / bounds.width;
      arenaAim.y = (event.clientY - bounds.top) * ARENA_H / bounds.height;
    };
    canvas.addEventListener("pointerenter", () => arenaBounds = canvas.getBoundingClientRect());
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
  function buildArenaGrid(dpr) {
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
  function aimArena(delta) {
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
    const dx = arenaAim.x - arenaPlayer.x, dy = arenaAim.y - arenaPlayer.y;
    const distance = Math.hypot(dx, dy) || 1;
    const speed = 440;
    arenaBullets.push({ x: arenaPlayer.x, y: arenaPlayer.y, vx: dx / distance * speed, vy: dy / distance * speed });
    arenaLastShot = now;
  }
  function spawnArenaEnemy() {
    const side = Math.floor(Math.random() * 4);
    const enemy = { x: 0, y: 0, radius: 11, speed: 38 + Math.random() * 20 };
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
  function enemyTouchesPlayer(enemy, cos, sin) {
    const dx = enemy.x - arenaPlayer.x, dy = enemy.y - arenaPlayer.y;
    const localX = dx * cos + dy * sin;
    const localY = -dx * sin + dy * cos;
    let inside = false, nearestEdgeDistance = Infinity;
    for (let index = 0, previous = arenaPlayerShape.length - 1; index < arenaPlayerShape.length; previous = index++) {
      const start2 = arenaPlayerShape[previous], end = arenaPlayerShape[index];
      if (start2.y > localY !== end.y > localY && localX < (end.x - start2.x) * (localY - start2.y) / (end.y - start2.y) + start2.x)
        inside = !inside;
      const edgeX = end.x - start2.x, edgeY = end.y - start2.y;
      const edgeLength = edgeX * edgeX + edgeY * edgeY;
      const projection = Math.max(0, Math.min(1, ((localX - start2.x) * edgeX + (localY - start2.y) * edgeY) / edgeLength));
      const nearestX = start2.x + projection * edgeX, nearestY = start2.y + projection * edgeY;
      nearestEdgeDistance = Math.min(nearestEdgeDistance, (localX - nearestX) ** 2 + (localY - nearestY) ** 2);
    }
    return inside || nearestEdgeDistance <= enemy.radius * enemy.radius;
  }
  function updateArena(now) {
    if (!isRunning() || active !== "arena" || !arenaContext) return;
    const elapsed = arenaLastFrame ? Math.min((now - arenaLastFrame) / 1e3, 0.05) : 0;
    arenaLastFrame = now;
    const horizontal = Number(arenaKeys.has("arrowright") || arenaKeys.has("d")) - Number(arenaKeys.has("arrowleft") || arenaKeys.has("a"));
    const vertical = Number(arenaKeys.has("arrowdown") || arenaKeys.has("s")) - Number(arenaKeys.has("arrowup") || arenaKeys.has("w"));
    const movementLength = Math.hypot(horizontal, vertical) || 1;
    arenaPlayer.x = Math.max(13, Math.min(ARENA_W - 13, arenaPlayer.x + horizontal / movementLength * 190 * elapsed));
    arenaPlayer.y = Math.max(13, Math.min(ARENA_H - 13, arenaPlayer.y + vertical / movementLength * 190 * elapsed));
    const turn = Number(arenaKeys.has("e")) - Number(arenaKeys.has("q"));
    if (turn) aimArena(turn * ARENA_TURN_RATE * elapsed);
    const spawnDelay = Math.max(500, 1400 - score * 3);
    if (now - arenaLastSpawn >= spawnDelay && arenaEnemies.length < ARENA_MAX_ENEMIES) {
      spawnArenaEnemy();
      arenaLastSpawn = now;
    }
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
        const ex = enemy.x - bullet.x, ey = enemy.y - bullet.y;
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
    const cos = Math.cos(aimAngle), sin = Math.sin(aimAngle);
    for (const enemy of arenaEnemies) {
      const dx = arenaPlayer.x - enemy.x, dy = arenaPlayer.y - enemy.y;
      const distance = Math.hypot(dx, dy) || 1;
      enemy.x += dx / distance * enemy.speed * elapsed;
      enemy.y += dy / distance * enemy.speed * elapsed;
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
    const avg = (a) => a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0;
    const mean = avg(intervals), std = Math.sqrt(avg(intervals.map((x) => (x - mean) ** 2)));
    const d = {
      game: active,
      duration_ms: Math.min(Date.now() - tick, 6e5),
      input_count: inputCounts.keyboard + inputCounts.controller,
      keyboard_count: inputCounts.keyboard,
      controller_count: inputCounts.controller,
      mean_interval_ms: Math.round(mean),
      interval_std_ms: Math.round(std),
      mean_hold_ms: Math.round(avg(holds)),
      score,
      source: inputCounts.controller ? "mixed" : "keyboard"
    };
    setArcadeStatus("Saving this round\u2026");
    void fetch("/api/samples", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(d)
    }).then(async (response) => {
      if (!response.ok) throw new Error(`Could not save this round (${response.status}).`);
      await refreshSummary();
    }).catch((error) => {
      setArcadeStatus(
        error instanceof Error && error.message.startsWith("Could not save") ? error.message : "Could not save this round. The server did not respond.",
        true
      );
    });
  }
  async function refreshSummary() {
    setArcadeStatus("Loading recent games\u2026");
    try {
      const response = await fetch("/api/summary");
      if (!response.ok) throw new Error(`Could not load recent games (${response.status}).`);
      const data = await response.json();
      $("#sampleCount").textContent = String(data.samples);
      $("#sampleCountLabel").textContent = data.samples === 1 ? "round played" : "rounds played";
      const list = $("#recentList");
      list.innerHTML = data.recent.length ? data.recent.map(
        (item) => `<div class="recent-item"><span><b>${modes.find((mode) => mode.id === item.game)?.name || item.game}</b><small>${new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span><strong>${plural(item.score, "point")}</strong></div>`
      ).join("") : `<div class="empty">No games played yet.</div>`;
      const message = data.samples ? "Recent games loaded from this machine." : "No games saved on this machine yet.";
      setArcadeStatus(serverWarning ? `${message} ${serverWarning}` : message, Boolean(serverWarning));
    } catch (error) {
      setArcadeStatus(
        error instanceof Error && error.message.startsWith("Could not load recent games") ? error.message : "Could not load recent games. The server did not respond.",
        true
      );
    }
  }
  $("#clearBtn").addEventListener("click", async () => {
    if (!window.confirm("Clear your game history on this machine?")) return;
    setArcadeStatus("Clearing saved rounds\u2026");
    try {
      const response = await fetch("/api/samples", { method: "DELETE" });
      if (!response.ok) throw new Error(`Could not clear saved rounds (${response.status}).`);
      await refreshSummary();
    } catch (error) {
      setArcadeStatus(
        error instanceof Error && error.message.startsWith("Could not clear") ? error.message : "Could not clear saved rounds. The server did not respond.",
        true
      );
    }
  });
  var GAMEPAD_KEYS = {
    0: " ",
    1: "ArrowDown",
    2: "ArrowLeft",
    3: "ArrowRight",
    12: "ArrowUp",
    13: "ArrowDown",
    14: "ArrowLeft",
    15: "ArrowRight"
  };
  function pollGamepad() {
    const pads = navigator.getGamepads?.() || [];
    let pad = null;
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
        holds.push(performance.now() - down.get(key));
        down.delete(key);
      }
    });
  }
  void (async () => {
    await checkOnboarding();
    await refreshSummary();
  })();
})();
