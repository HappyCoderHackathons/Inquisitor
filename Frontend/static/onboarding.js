"use strict";
(() => {
  // frontend/onboarding.ts
  var gameNames = {
    aim: "Aim Trainer",
    tetris: "Block Stack",
    typing: "Word Sprint",
    arena: "Arena Rush"
  };
  var stateText = {
    collecting: "Agent connected. Play each game until its timer is complete.",
    training: "Collection complete. Your model is training.",
    ready: "Setup complete. Your model is active.",
    failed: "Training failed."
  };
  var element = (id) => document.getElementById(id);
  async function post(path) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  }
  async function refresh() {
    const response = await fetch("/api/onboarding");
    if (response.status === 404) return;
    if (!response.ok) throw new Error(`Could not load setup status (${response.status}).`);
    const status = await response.json();
    const stateLabel = element("onboardingState");
    if (stateLabel) {
      stateLabel.textContent = status.error ? `${stateText[status.state]} ${status.error}` : stateText[status.state];
      stateLabel.classList.toggle("error", status.state === "failed");
    }
    const list = element("gameProgress");
    if (list) {
      list.replaceChildren(
        ...Object.entries(status.seconds).map(([game, seconds]) => {
          const item = document.createElement("li");
          item.textContent = `${gameNames[game] ?? game}: ${Math.min(seconds, status.required_seconds)} / ${status.required_seconds} s`;
          return item;
        })
      );
    }
    const windows = element("windowProgress");
    if (windows) {
      windows.textContent = `Keyboard-active windows: ${status.training_windows} / ${status.required_training_windows} training, ${status.validation_windows} / ${status.required_validation_windows} calibration. If time is complete but windows are short, keep playing Word Sprint or Block Stack.`;
    }
    const play = element("playGames");
    if (play) play.hidden = status.state !== "collecting";
  }
  element("createCode")?.addEventListener("click", async () => {
    const response = await post("/api/pairing-codes");
    const target = element("pairingCode");
    if (!target) return;
    if (!response.ok) {
      target.textContent = `Could not create a code (${response.status}).`;
      return;
    }
    target.textContent = (await response.json()).code;
  });
  if (element("gameProgress")) {
    const tick = () => refresh().catch((error) => {
      const label = element("onboardingState");
      if (label) label.textContent = error instanceof Error ? error.message : "Could not load setup status.";
    });
    void tick();
    window.setInterval(tick, 5e3);
  }
})();
