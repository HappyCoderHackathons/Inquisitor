type GameId = "aim" | "tetris" | "typing" | "wiki";
type Mode = { id: GameId; name: string; category: string; description: string; keys: string; icon: string; accent: string };
const modes: Mode[] = [
  { id: "aim", name: "Aim Trainer", category: "REFLEX / PRECISION", description: "Track the pulse. Hit as many targets as you can before they disappear.", keys: "MOUSE · A / RT", icon: "◎", accent: "coral" },
  { id: "tetris", name: "Block Stack", category: "SPATIAL / RHYTHM", description: "Rotate, slide, and clear lines as the stack picks up speed.", keys: "← → ↓ · A / B", icon: "▦", accent: "violet" },
  { id: "typing", name: "Word Sprint", category: "CADENCE / FLOW", description: "Type the shown words at your own pace. We only keep timing summaries.", keys: "KEYBOARD · GAMEPAD", icon: "⌁", accent: "mint" },
  { id: "wiki", name: "Wiki Hop", category: "SEARCH / SWITCHING", description: "Navigate a tiny knowledge maze. Reach the goal using the fewest moves.", keys: "WASD · D-PAD", icon: "↗", accent: "amber" },
];
const $ = <T extends HTMLElement>(s: string) => document.querySelector(s) as T;
const grid = $("#gameGrid");
grid.innerHTML = modes.map((m, i) => `<article class="game-card ${m.accent}" style="--delay:${i * 70}ms"><div class="card-top"><span class="card-num">0${i + 1}</span><span class="tag">${m.category}</span></div><div class="card-art art-${m.id}"><span>${m.icon}</span><i></i><i></i><i></i></div><div class="card-copy"><h3>${m.name}</h3><p>${m.description}</p><div class="card-bottom"><span>${m.keys}</span><button class="play-button" data-game="${m.id}" aria-label="Play ${m.name}">↗</button></div></div></article>`).join("");

const overlay = $("#gameOverlay"), stage = $("#gameStage");
let active: GameId | null = null, sprint = false, running = false, remaining = 60, score = 0, tick = 0, timerId = 0, animation = 0;
let lastInput = 0, intervals: number[] = [], holds: number[] = [], down = new Map<string, number>();
let inputCounts = { keyboard: 0, controller: 0 }, wordIndex = 0, wikiPos = 0, board: number[][] = [];
const words = "orbit velvet lantern meadow copper drift mango pocket ripple comet garden signal".split(" ");
const wordGoals = ["ORBIT", "VELVET", "LANTERN", "MEADOW", "COPPER", "DRIFT", "MANGO", "POCKET", "RIPPLE", "COMET", "GARDEN", "SIGNAL"];
function openGame(id: GameId, isSprint = false) {
  active = id; sprint = isSprint; running = false; remaining = 60; score = 0; intervals = []; holds = []; down.clear(); inputCounts = { keyboard: 0, controller: 0 };
  const m = modes.find(x => x.id === id)!;
  $("#modalMode").textContent = isSprint ? "ARCADE SPRINT · ROTATING MODE" : m.category;
  $("#modalTitle").textContent = m.name; $("#gameTimer").textContent = "60"; $("#gameScore").textContent = "0";
  $("#gameInstruction").textContent = instruction(id); $("#gameHint").textContent = isSprint ? "Game rotates every 60 seconds · ESC to exit" : "Press ESC to exit";
  $("#startGame").classList.remove("hidden"); $("#startGame").textContent = "START GAME";
  stage.className = `game-stage stage-${id}`; renderStage(); overlay.classList.remove("hidden");
}
function instruction(id: GameId) { return ({ aim: "Click or tap each target", tetris: "Clear lines · ← → move · ↑ rotate", typing: "Type the word shown, then space", wiki: "Find the route to the goal" } as Record<GameId,string>)[id]; }
function start() { if (!active) return; running = true; tick = Date.now(); $("#startGame").classList.add("hidden"); stage.focus(); timerId = window.setInterval(() => { if (!running) return; remaining--; $("#gameTimer").textContent = String(remaining); if (remaining <= 0) finish(); }, 1000); if (active === "aim") spawnTarget(); if (active === "tetris") initBoard(); renderStage(); }
function finish() { running = false; clearInterval(timerId); cancelAnimationFrame(animation); saveSample(); if (sprint && active) { const next = modes[(modes.findIndex(x => x.id === active) + 1) % modes.length].id; openGame(next, true); start(); } else { $("#gameHint").textContent = `Round complete · ${score} points · sample saved`; $("#startGame").textContent = "PLAY AGAIN"; $("#startGame").classList.remove("hidden"); refreshSummary(); } }
function close() { running = false; clearInterval(timerId); cancelAnimationFrame(animation); overlay.classList.add("hidden"); active = null; sprint = false; refreshSummary(); }
$("#gameGrid").addEventListener("click", e => { const btn = (e.target as HTMLElement).closest("[data-game]") as HTMLElement | null; if (btn) openGame(btn.dataset.game as GameId); });
$("#sprintBtn").addEventListener("click", () => openGame("aim", true)); $("#closeGame").addEventListener("click", close); $("#startGame").addEventListener("click", () => running ? start() : start());
function recordInput(source: "keyboard"|"controller") { const now = performance.now(); if (lastInput) intervals.push(Math.min(now - lastInput, 3000)); lastInput = now; inputCounts[source]++; }
window.addEventListener("keydown", e => { if (e.key === "Escape" && !overlay.classList.contains("hidden")) { close(); return; } if (!overlay.classList.contains("hidden") && running && active) { if (e.repeat) return; recordInput("keyboard"); down.set(e.code, performance.now()); handleAction(e.key, "keyboard"); if (["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"," "].includes(e.key)) e.preventDefault(); } });
window.addEventListener("keyup", e => { const t = down.get(e.code); if (t) { holds.push(Math.min(performance.now() - t, 2000)); down.delete(e.code); } });
function handleAction(key: string, source: string) {
  if (!active) return;
  if (active === "aim") { if (key === " " || key.toLowerCase() === "a") hitTarget(); }
  if (active === "tetris") { if (["ArrowLeft","a","A"].includes(key)) move(-1); if (["ArrowRight","d","D"].includes(key)) move(1); if (["ArrowDown","s","S"].includes(key)) drop(); if (["ArrowUp","w","W"," "].includes(key)) rotate(); }
  if (active === "typing" && key === " ") { score += 10; wordIndex++; renderStage(); }
  if (active === "wiki") { const d: Record<string,number> = { ArrowRight:1,d:1,D:1,ArrowDown:4,s:4,S:4,ArrowLeft:-1,a:-1,A:-1,ArrowUp:-4,w:-4,W:-4 }; wikiPos = Math.max(0, Math.min(15, wikiPos + (d[key] || 0))); score++; renderStage(); if (wikiPos === 15) { score += 20; wikiPos = 0; renderStage(); } }
}
function renderStage() {
  if (!active) return;
  if (active === "aim") { stage.innerHTML = `<div class="aim-field"><div class="aim-cross">+</div><div class="target" id="target" aria-label="target"></div><div class="aim-caption">CLICK THE TARGET</div></div>`; $("#target")?.addEventListener("click", hitTarget); }
  if (active === "typing") { const w = wordGoals[wordIndex % wordGoals.length]; stage.innerHTML = `<div class="typing-game"><div class="word-label">TYPE THIS WORD</div><div class="word-prompt">${w.split("").map((c,i)=>`<span class="${i===0?"next-letter":""}">${c}</span>`).join("")}</div><div class="type-progress">${wordIndex} WORDS CLEARED <span>·</span> PRESS SPACE WHEN DONE</div><input class="typing-input" id="typingInput" autocomplete="off" autocapitalize="characters" placeholder="start typing here…" aria-label="Type the prompt" /></div>`; const input = $("#typingInput") as HTMLInputElement; input.focus(); input.addEventListener("input", () => { recordInput("keyboard"); const desired = w; if (input.value.trim().toUpperCase() === desired) { score += 10; wordIndex++; renderStage(); } }); }
  if (active === "wiki") { const tiles = ["START","MAP","FIND","LINK","FACT","IDEA","PAGE","OPEN","SEARCH","CLUE","JUMP","NOTE","READ","PATH","KNOW","GOAL"]; stage.innerHTML = `<div class="wiki-board">${tiles.map((t,i)=>`<div class="wiki-tile ${i===wikiPos?"player":""} ${i===15?"goal":""}">${i===wikiPos?"YOU":t}</div>`).join("")}</div><div class="wiki-caption">Use arrow keys or D-pad to explore the links</div>`; }
  if (active === "tetris") { if (!board.length) initBoard(); stage.innerHTML = `<div class="tetris-wrap"><div class="tetris-board">${board.flat().map(v=>`<i class="cell ${v?"filled":""}"></i>`).join("")}</div><div class="tetris-aside"><b>${score}</b><span>POINTS</span><hr><span>LINES</span><b>${Math.floor(score / 100)}</b><small>← → MOVE<br>↑ ROTATE<br>↓ DROP</small></div></div>`; }
}
function spawnTarget() { const target = $("#target"); if (!target) return; target.style.left = `${12 + Math.random()*76}%`; target.style.top = `${12 + Math.random()*70}%`; target.style.transform = `scale(${.72 + Math.random()*.55})`; }
function hitTarget() { if (!running || active !== "aim") return; score += 10; $("#gameScore").textContent = String(score); spawnTarget(); }
function initBoard() { board = Array.from({length:16},()=>Array(10).fill(0)); board[0][4] = 1; }
function move(dx:number) { const c = board[0].indexOf(1); board[0].fill(0); board[0][Math.max(0,Math.min(9,c+dx))] = 1; renderStage(); }
function rotate() { const c = board[0].indexOf(1); board[0].fill(0); board[1][c] = 1; renderStage(); }
function drop() { const r = board.findIndex(row => row.includes(1)); const c = board[r]?.indexOf(1) ?? 4; board[r][c] = 0; if (r < 15) board[r+1][c] = 1; else { board[15][c] = 1; board.unshift(Array(10).fill(0)); board.pop(); score += 20; $("#gameScore").textContent = String(score); } renderStage(); }
function saveSample() { const avg = (a:number[]) => a.length ? a.reduce((x,y)=>x+y,0)/a.length : 0; const mean = avg(intervals), std = Math.sqrt(avg(intervals.map(x => (x-mean)**2))); const d = { game: active, duration_ms: 60000, input_count: inputCounts.keyboard + inputCounts.controller, keyboard_count: inputCounts.keyboard, controller_count: inputCounts.controller, mean_interval_ms: Math.round(mean), interval_std_ms: Math.round(std), mean_hold_ms: Math.round(avg(holds)), score, source: inputCounts.controller ? "mixed" : "keyboard" }; void fetch("/api/samples", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(d) }).then(refreshSummary).catch(()=>{}); }
async function refreshSummary() { try { const d = await (await fetch("/api/summary")).json(); $("#sampleCount").textContent = d.samples; const list = $("#recentList"); list.innerHTML = d.recent.length ? d.recent.map((x:any)=>`<div class="recent-item"><span class="recent-icon">${modes.find(m=>m.id===x.game)?.icon || "·"}</span><span><b>${modes.find(m=>m.id===x.game)?.name || x.game}</b><small>${x.input_count} inputs · ${new Date(x.created_at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</small></span><strong>${x.score}</strong></div>`).join("") : `<div class="empty">Finish a game to collect your first sample.</div>`; } catch {} }
$("#clearBtn").addEventListener("click", async () => { await fetch("/api/samples",{method:"DELETE"}); refreshSummary(); });
window.setInterval(() => { const pads = navigator.getGamepads?.() || []; const pad = Array.from(pads).find(Boolean); if (pad) { $("#padState").textContent = "CONNECTED"; $("#inputStatus").textContent = "INPUT READY"; $("#controllerName").textContent = pad.id.slice(0,48); $("#controllerHint").textContent = "Controller connected · press A / Cross to interact"; pad.buttons.forEach((b, i) => { const key = `pad${i}`; if (b.pressed) { if (!down.has(key)) { recordInput("controller"); down.set(key, performance.now()); if (running) { const map: Record<number,string> = {0:" ", 1:"ArrowDown", 2:"ArrowLeft", 3:"ArrowRight", 12:"ArrowUp", 13:"ArrowDown", 14:"ArrowLeft", 15:"ArrowRight"}; handleAction(map[i] || " ", "controller"); } } } else if (down.has(key)) { holds.push(performance.now()-down.get(key)!); down.delete(key); } }); } else { $("#padState").textContent = "SEARCHING"; $("#controllerName").textContent = "Waiting for controller"; $("#controllerHint").textContent = "Connect an Xbox or Nintendo controller, then press any button."; } },100);
void refreshSummary();
