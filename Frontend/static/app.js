"use strict";
(() => {
  // frontend/app.ts
  var modes = [
    { id: "aim", name: "Aim Trainer", description: "Hit as many targets as you can.", keys: "Mouse, Space, or A" },
    { id: "tetris", name: "Block Stack", description: "Stack blocks and clear rows.", keys: "Arrow keys" },
    { id: "typing", name: "Word Sprint", description: "Type each word as quickly as you can.", keys: "Keyboard" },
    { id: "arena", name: "Arena Rush", description: "Move, aim, and shoot.", keys: "WASD, Q/E, Space" }
  ];
  var $ = (s) => document.querySelector(s);
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
  grid.innerHTML = modes.map(
    (m) => `<article class="game-card">${gamePreview(m.id)}<div class="card-copy"><h3>${m.name}</h3><p>${m.description}</p><div class="card-bottom"><span>${m.keys}</span><button class="play-button" data-game="${m.id}" aria-label="Play ${m.name}">Play</button></div></div></article>`
  ).join("");
  var overlay = $("#gameOverlay");
  var stage = $("#gameStage");
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
  var running = false;
  var remaining = 60;
  var score = 0;
  var tick = 0;
  var timerId = 0;
  var gravityTimerId = 0;
  var animation = 0;
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
  var arenaKeys = /* @__PURE__ */ new Set();
  var arenaEnemies = [];
  var arenaBullets = [];
  var arenaPlayer = { x: 360, y: 158 };
  var arenaAim = { x: 500, y: 158 };
  var arenaHull = 1;
  var arenaCanvas = null;
  var arenaLastFrame = 0;
  var arenaLastSpawn = 0;
  var arenaLastShot = 0;
  var arenaPlayerShape = [
    { x: 15, y: 0 },
    { x: -10, y: -9 },
    { x: -7, y: 0 },
    { x: -10, y: 9 }
  ];
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
  var wordGoals = [
    "ORBIT",
    "VELVET",
    "LANTERN",
    "MEADOW",
    "COPPER",
    "DRIFT",
    "MANGO",
    "POCKET",
    "RIPPLE",
    "COMET",
    "GARDEN",
    "SIGNAL",
    "BRIDGE",
    "CANDLE",
    "PLANET",
    "MARBLE",
    "RIVER",
    "CASTLE",
    "BREEZE",
    "PILLOW",
    "CLOUD",
    "SPARK",
    "FOREST",
    "BUTTON",
    "CIRCLE",
    "THUNDER",
    "PEBBLE",
    "SILVER",
    "HARBOR",
    "MELLOW",
    "JOURNEY",
    "ORANGE",
    "SPRING",
    "ANCHOR",
    "STREAM",
    "GALAXY"
  ];
  function shuffleWords(previousDeck = []) {
    const shuffled = [...wordGoals];
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
  function openGame(id, isSprint = false) {
    active = id;
    sprint = isSprint;
    running = false;
    remaining = 60;
    score = 0;
    intervals = [];
    holds = [];
    down.clear();
    inputCounts = { keyboard: 0, controller: 0 };
    wordIndex = 0;
    linesCleared = 0;
    board = [];
    currentPiece = null;
    arenaKeys.clear();
    const m = modes.find((x) => x.id === id);
    $("#modalMode").textContent = isSprint ? "Sprint" : "";
    $("#modalTitle").textContent = m.name;
    $("#gameTimer").textContent = "60";
    $("#gameScore").textContent = "0";
    $("#gameInstruction").textContent = instruction(id);
    $("#gameHint").textContent = isSprint ? "Next game starts automatically \xB7 Esc to close" : "Esc to close";
    $("#startGame").classList.remove("hidden");
    $("#startGame").textContent = "Start";
    stage.className = `game-stage stage-${id}`;
    renderStage();
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    const current = document.activeElement;
    if (current instanceof HTMLElement && !overlay.contains(current)) returnFocus = current;
    $("#startGame").focus();
  }
  function instruction(id) {
    return {
      aim: "Click, tap, or press Space or A",
      tetris: "\u2190 \u2192 move \xB7 \u2193 soft drop \xB7 \u2191 rotate \xB7 SPACE hard drop",
      typing: "Type each word; correct letters turn green",
      arena: "Move with WASD or arrows. Aim with the mouse or Q and E. Fire with click, Space, or F"
    }[id];
  }
  var onboardingActive = false;
  var onboardingQueue = Promise.resolve();
  var serverWarning = null;
  function setArcadeStatus(message, isError = false) {
    const status = document.getElementById("arcadeStatus");
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("error", isError);
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
      onboardingActive = (await response.json()).state === "collecting";
    } catch (error) {
      serverWarning = error instanceof Error && error.message.startsWith("Could not check setup") ? error.message : "Could not check setup. The server did not respond.";
      setArcadeStatus(serverWarning, true);
    }
  }
  function start() {
    if (!active) return;
    reportRound("/api/onboarding/rounds", { game: active });
    clearInterval(timerId);
    clearInterval(gravityTimerId);
    cancelAnimationFrame(animation);
    running = true;
    remaining = 60;
    score = 0;
    tick = Date.now();
    intervals = [];
    holds = [];
    lastInput = 0;
    inputCounts = { keyboard: 0, controller: 0 };
    wordIndex = 0;
    linesCleared = 0;
    arenaKeys.clear();
    if (active === "typing") wordDeck = shuffleWords(wordDeck);
    $("#gameTimer").textContent = "60";
    $("#gameScore").textContent = "0";
    $("#startGame").classList.add("hidden");
    stage.focus();
    if (active === "tetris") {
      initBoard();
      gravityTimerId = window.setInterval(stepTetris, 650);
    }
    if (active === "arena") {
      arenaPlayer = { x: 360, y: 158 };
      arenaAim = { x: 500, y: 158 };
      arenaHull = 1;
      arenaEnemies = [];
      arenaBullets = [];
      arenaLastFrame = 0;
      arenaLastSpawn = 0;
      arenaLastShot = 0;
    }
    renderStage();
    if (active === "aim") spawnTarget();
    timerId = window.setInterval(() => {
      if (!running) return;
      remaining--;
      $("#gameTimer").textContent = String(remaining);
      if (remaining <= 0) finish();
    }, 1e3);
    if (active === "arena") animation = requestAnimationFrame(updateArena);
  }
  function finish() {
    running = false;
    clearInterval(timerId);
    clearInterval(gravityTimerId);
    cancelAnimationFrame(animation);
    arenaKeys.clear();
    saveSample();
    if (sprint && active) {
      const next = modes[(modes.findIndex((x) => x.id === active) + 1) % modes.length].id;
      openGame(next, true);
      start();
    } else {
      $("#gameHint").textContent = `Round complete \xB7 ${score} points`;
      $("#startGame").textContent = "Play again";
      $("#startGame").classList.remove("hidden");
      refreshSummary();
    }
  }
  function close() {
    if (running) reportRound("/api/onboarding/rounds/current/cancel");
    running = false;
    clearInterval(timerId);
    clearInterval(gravityTimerId);
    cancelAnimationFrame(animation);
    arenaKeys.clear();
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
    active = null;
    sprint = false;
    const restore = returnFocus;
    returnFocus = null;
    refreshSummary();
    restore?.focus();
  }
  $("#gameGrid").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-game]");
    if (btn) openGame(btn.dataset.game);
  });
  $("#sprintBtn").addEventListener("click", () => openGame("aim", true));
  $("#closeGame").addEventListener("click", close);
  $("#startGame").addEventListener("click", () => running ? start() : start());
  function recordInput(source) {
    const now = performance.now();
    if (lastInput) intervals.push(Math.min(now - lastInput, 3e3));
    lastInput = now;
    inputCounts[source]++;
  }
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.classList.contains("hidden")) {
      close();
      return;
    }
    if (!overlay.classList.contains("hidden") && running && active) {
      const key = e.key.toLowerCase();
      if (active === "arena" && ["arrowleft", "arrowright", "arrowup", "arrowdown", "a", "d", "w", "s"].includes(key))
        arenaKeys.add(key);
      if (active === "arena" && (key === "q" || key === "e")) aimArena(key === "q" ? -0.2 : 0.2);
      if (e.repeat) return;
      recordInput("keyboard");
      down.set(e.code, performance.now());
      handleAction(e.key);
      if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", " "].includes(e.key)) e.preventDefault();
    }
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
    if (active === "aim") {
      const focusedControl = document.activeElement instanceof HTMLButtonElement;
      if (key === " " || key.toLowerCase() === "a" || key === "Enter" && !focusedControl) hitTarget();
    }
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
    if (active === "aim") {
      stage.innerHTML = `<div class="aim-field"><div class="aim-cross">+</div><div class="target" id="target" role="button" tabindex="0" aria-label="Hit the target"></div><div class="aim-caption">SPACE, A, OR CLICK</div></div>`;
      $("#target")?.addEventListener("click", hitTarget);
    }
    if (active === "typing") {
      const word = wordDeck[wordIndex];
      stage.innerHTML = `<div class="typing-game"><div class="word-label">TYPE THIS WORD</div><div class="word-prompt">${word.split("").map((letter) => `<span>${letter}</span>`).join(
        ""
      )}</div><div class="type-progress">${wordIndex} WORDS CLEARED <span>\xB7</span> CORRECT LETTERS TURN GREEN</div><input class="typing-input" id="typingInput" maxlength="${word.length}" autocomplete="off" autocapitalize="characters" placeholder="start typing here\u2026" aria-label="Type the prompt" /></div>`;
      const input = $("#typingInput");
      const letters = stage.querySelectorAll(".word-prompt span");
      let completionPending = false;
      const updateLetters = () => {
        if (!running) return;
        const typed = input.value.toUpperCase();
        letters.forEach((letter, index) => {
          const correct = index < typed.length && typed[index] === word[index];
          letter.className = correct ? "correct-letter" : index < typed.length ? "incorrect-letter" : "";
        });
        letters[Math.min(input.value.length, word.length - 1)]?.classList.add("current-letter");
        if (typed === word && !completionPending) {
          completionPending = true;
          window.setTimeout(() => {
            if (running && active === "typing" && input.isConnected && input.value.toUpperCase() === word) {
              score += 10;
              wordIndex++;
              if (wordIndex === wordDeck.length) {
                wordDeck = shuffleWords(wordDeck);
                wordIndex = 0;
              }
              renderStage();
            }
          }, 250);
        }
      };
      input.addEventListener("input", updateLetters);
      input.focus();
      updateLetters();
    }
    if (active === "arena") {
      stage.innerHTML = `<div class="arena-game"><div class="arena-hud"><span>WASD / ARROWS MOVE</span><span>Q / E AIM \xB7 SPACE OR F FIRE</span><b id="arenaHull">HULL 1</b></div><canvas class="arena-canvas" id="arenaCanvas" width="720" height="316" tabindex="0" aria-label="Arena game field"></canvas></div>`;
      arenaCanvas = $("#arenaCanvas");
      const updateAim = (event) => {
        if (!arenaCanvas) return;
        const bounds = arenaCanvas.getBoundingClientRect();
        arenaAim = {
          x: (event.clientX - bounds.left) * arenaCanvas.width / bounds.width,
          y: (event.clientY - bounds.top) * arenaCanvas.height / bounds.height
        };
      };
      arenaCanvas.addEventListener("pointermove", updateAim);
      arenaCanvas.addEventListener("pointerdown", (event) => {
        if (event.pointerType === "mouse" && event.button === 0) {
          updateAim(event);
          fireArena();
          event.preventDefault();
        }
      });
    }
    if (active === "tetris") renderTetris();
  }
  function spawnTarget() {
    const target = $("#target");
    if (!target) return;
    target.style.left = `${8 + Math.random() * 84}%`;
    target.style.top = `${10 + Math.random() * 76}%`;
    target.style.transform = `translate(-50%, -50%) scale(${1.15 + Math.random() * 0.45})`;
  }
  function hitTarget() {
    if (!running || active !== "aim") return;
    score += 10;
    $("#gameScore").textContent = String(score);
    spawnTarget();
  }
  function initBoard() {
    board = Array.from({ length: 20 }, () => Array(10).fill(0));
    spawnPiece();
  }
  function canPlace(shape, x, y) {
    return shape.every(
      (row, rowIndex) => row.every((cell, colIndex) => {
        if (!cell) return true;
        const nextX = x + colIndex, nextY = y + rowIndex;
        return nextX >= 0 && nextX < 10 && nextY < 20 && (nextY < 0 || !board[nextY][nextX]);
      })
    );
  }
  function spawnPiece() {
    const type = Math.floor(Math.random() * tetrominoes.length) + 1;
    const shape = tetrominoes[type - 1].map((row) => [...row]);
    currentPiece = { type, shape, x: Math.floor((10 - shape[0].length) / 2), y: 0 };
    if (!canPlace(shape, currentPiece.x, currentPiece.y)) endTetris();
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
    if (!running || active !== "tetris" || !currentPiece) return;
    if (canPlace(currentPiece.shape, currentPiece.x, currentPiece.y + 1)) currentPiece.y++;
    else lockPiece();
    renderTetris();
  }
  function hardDropTetris() {
    if (!currentPiece) return;
    while (canPlace(currentPiece.shape, currentPiece.x, currentPiece.y + 1)) {
      currentPiece.y++;
      score += 2;
    }
    lockPiece();
    $("#gameScore").textContent = String(score);
    renderTetris();
  }
  function lockPiece() {
    if (!currentPiece) return;
    currentPiece.shape.forEach(
      (row, rowIndex) => row.forEach((cell, colIndex) => {
        if (cell) board[currentPiece.y + rowIndex][currentPiece.x + colIndex] = currentPiece.type;
      })
    );
    const remainingRows = board.filter((row) => row.some((cell) => !cell));
    const cleared = 20 - remainingRows.length;
    if (cleared) {
      board = Array.from({ length: cleared }, () => Array(10).fill(0)).concat(remainingRows);
      linesCleared += cleared;
      score += [0, 100, 300, 500, 800][cleared] ?? cleared * 200;
      $("#gameScore").textContent = String(score);
    }
    spawnPiece();
  }
  function endTetris() {
    if (!running) return;
    if (sprint) {
      finish();
      return;
    }
    running = false;
    clearInterval(timerId);
    clearInterval(gravityTimerId);
    cancelAnimationFrame(animation);
    saveSample();
    refreshSummary();
    $("#gameHint").textContent = `Stack topped out \xB7 ${score} points`;
    $("#startGame").textContent = "Play again";
    $("#startGame").classList.remove("hidden");
  }
  function renderTetris() {
    if (active !== "tetris") return;
    if (!board.length) initBoard();
    const visible = board.map((row) => [...row]);
    currentPiece?.shape.forEach(
      (row, rowIndex) => row.forEach((cell, colIndex) => {
        if (cell) visible[currentPiece.y + rowIndex][currentPiece.x + colIndex] = currentPiece.type;
      })
    );
    stage.innerHTML = `<div class="tetris-wrap"><div class="tetris-board">${visible.flat().map((value) => `<i class="cell${value ? ` color-${value}` : ""}"></i>`).join(
      ""
    )}</div><div class="tetris-aside"><b>${score}</b><span>POINTS</span><hr><span>LINES</span><b>${linesCleared}</b><small>\u2190 \u2192 MOVE<br>\u2193 SOFT DROP<br>\u2191 ROTATE<br>SPACE HARD DROP</small></div></div>`;
  }
  function aimArena(delta) {
    const dx = arenaAim.x - arenaPlayer.x;
    const dy = arenaAim.y - arenaPlayer.y;
    const angle = Math.atan2(dy, dx) + delta;
    const reach = Math.max(80, Math.min(180, Math.hypot(dx, dy) || 140));
    arenaAim = { x: arenaPlayer.x + Math.cos(angle) * reach, y: arenaPlayer.y + Math.sin(angle) * reach };
  }
  function fireArena() {
    const now = performance.now();
    if (!running || active !== "arena" || now - arenaLastShot < 160) return;
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
      enemy.x = Math.random() * 720;
      enemy.y = -enemy.radius;
    } else if (side === 1) {
      enemy.x = 720 + enemy.radius;
      enemy.y = Math.random() * 316;
    } else if (side === 2) {
      enemy.x = Math.random() * 720;
      enemy.y = 316 + enemy.radius;
    } else {
      enemy.x = -enemy.radius;
      enemy.y = Math.random() * 316;
    }
    arenaEnemies.push(enemy);
  }
  function enemyTouchesPlayer(enemy) {
    const angle = Math.atan2(arenaAim.y - arenaPlayer.y, arenaAim.x - arenaPlayer.x);
    const dx = enemy.x - arenaPlayer.x, dy = enemy.y - arenaPlayer.y;
    const localX = dx * Math.cos(angle) + dy * Math.sin(angle);
    const localY = -dx * Math.sin(angle) + dy * Math.cos(angle);
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
    if (!running || active !== "arena") return;
    const context = arenaCanvas?.getContext("2d");
    if (!arenaCanvas || !context) {
      console.error("Arena canvas is unavailable.");
      return;
    }
    const elapsed = arenaLastFrame ? Math.min((now - arenaLastFrame) / 1e3, 0.05) : 0;
    arenaLastFrame = now;
    const horizontal = Number(arenaKeys.has("arrowright") || arenaKeys.has("d")) - Number(arenaKeys.has("arrowleft") || arenaKeys.has("a"));
    const vertical = Number(arenaKeys.has("arrowdown") || arenaKeys.has("s")) - Number(arenaKeys.has("arrowup") || arenaKeys.has("w"));
    const movementLength = Math.hypot(horizontal, vertical) || 1;
    arenaPlayer.x = Math.max(13, Math.min(707, arenaPlayer.x + horizontal / movementLength * 190 * elapsed));
    arenaPlayer.y = Math.max(13, Math.min(303, arenaPlayer.y + vertical / movementLength * 190 * elapsed));
    const spawnDelay = Math.max(500, 1400 - score * 3);
    if (now - arenaLastSpawn >= spawnDelay) {
      spawnArenaEnemy();
      arenaLastSpawn = now;
    }
    const remainingBullets = [];
    arenaBullets.forEach((bullet) => {
      bullet.x += bullet.vx * elapsed;
      bullet.y += bullet.vy * elapsed;
      const hitIndex = arenaEnemies.findIndex(
        (enemy) => Math.hypot(enemy.x - bullet.x, enemy.y - bullet.y) < enemy.radius + 4
      );
      if (hitIndex >= 0) {
        arenaEnemies.splice(hitIndex, 1);
        score += 10;
        $("#gameScore").textContent = String(score);
      } else if (bullet.x >= 0 && bullet.x <= 720 && bullet.y >= 0 && bullet.y <= 316) remainingBullets.push(bullet);
    });
    arenaBullets = remainingBullets;
    const remainingEnemies = [];
    for (const enemy of arenaEnemies) {
      const dx = arenaPlayer.x - enemy.x, dy = arenaPlayer.y - enemy.y;
      const distance = Math.hypot(dx, dy) || 1;
      enemy.x += dx / distance * enemy.speed * elapsed;
      enemy.y += dy / distance * enemy.speed * elapsed;
      if (enemyTouchesPlayer(enemy)) {
        arenaHull = 0;
        $("#arenaHull").textContent = "DESTROYED";
        endArena();
        return;
      }
      remainingEnemies.push(enemy);
    }
    arenaEnemies = remainingEnemies;
    context.fillStyle = "#09070D";
    context.fillRect(0, 0, arenaCanvas.width, arenaCanvas.height);
    context.strokeStyle = "#393243";
    context.lineWidth = 1;
    for (let x = 36; x < arenaCanvas.width; x += 36) {
      context.beginPath();
      context.moveTo(x, 0);
      context.lineTo(x, arenaCanvas.height);
      context.stroke();
    }
    for (let y = 36; y < arenaCanvas.height; y += 36) {
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(arenaCanvas.width, y);
      context.stroke();
    }
    arenaEnemies.forEach((enemy) => {
      context.beginPath();
      context.arc(enemy.x, enemy.y, enemy.radius, 0, Math.PI * 2);
      context.fillStyle = "#FB7185";
      context.fill();
      context.beginPath();
      context.arc(enemy.x, enemy.y, 4, 0, Math.PI * 2);
      context.fillStyle = "#F7F4FC";
      context.fill();
    });
    arenaBullets.forEach((bullet) => {
      context.beginPath();
      context.arc(bullet.x, bullet.y, 3, 0, Math.PI * 2);
      context.fillStyle = "#C4B5FD";
      context.fill();
    });
    const aimAngle = Math.atan2(arenaAim.y - arenaPlayer.y, arenaAim.x - arenaPlayer.x);
    context.save();
    context.translate(arenaPlayer.x, arenaPlayer.y);
    context.rotate(aimAngle);
    context.beginPath();
    context.moveTo(arenaPlayerShape[0].x, arenaPlayerShape[0].y);
    arenaPlayerShape.slice(1).forEach((point) => context.lineTo(point.x, point.y));
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
    animation = requestAnimationFrame(updateArena);
  }
  function endArena() {
    if (!running) return;
    if (sprint) {
      finish();
      return;
    }
    running = false;
    clearInterval(timerId);
    cancelAnimationFrame(animation);
    arenaKeys.clear();
    saveSample();
    refreshSummary();
    $("#gameHint").textContent = `Hull breached \xB7 ${score} points`;
    $("#startGame").textContent = "Play again";
    $("#startGame").classList.remove("hidden");
  }
  function saveSample() {
    reportRound("/api/onboarding/rounds/current/finish");
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
      $("#sampleCount").textContent = data.samples;
      const list = $("#recentList");
      list.innerHTML = data.recent.length ? data.recent.map(
        (item) => `<div class="recent-item"><span><b>${modes.find((mode) => mode.id === item.game)?.name || item.game}</b><small>${new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span><strong>${item.score}</strong></div>`
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
  window.setInterval(() => {
    const pads = navigator.getGamepads?.() || [];
    const pad = Array.from(pads).find(Boolean);
    if (!pad) return;
    pad.buttons.forEach((button, index) => {
      const key = `pad${index}`;
      if (button.pressed) {
        if (!down.has(key)) {
          recordInput("controller");
          down.set(key, performance.now());
          if (running) {
            const map = {
              0: " ",
              1: "ArrowDown",
              2: "ArrowLeft",
              3: "ArrowRight",
              12: "ArrowUp",
              13: "ArrowDown",
              14: "ArrowLeft",
              15: "ArrowRight"
            };
            handleAction(map[index] || " ");
          }
        }
      } else if (down.has(key)) {
        holds.push(performance.now() - down.get(key));
        down.delete(key);
      }
    });
  }, 100);
  void (async () => {
    await checkOnboarding();
    await refreshSummary();
  })();
})();
