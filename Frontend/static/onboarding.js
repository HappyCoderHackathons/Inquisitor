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
    collecting: "Agent connected. Play each game until its timer is complete, then train your model.",
    training: "Collection complete. Your model is training.",
    ready: "Setup complete. Your model is active.",
    failed: "Training failed."
  };
  var element = (id) => document.getElementById(id);
  var authForm = element("authForm");
  var signingUp = false;
  element("authToggle")?.addEventListener("click", () => {
    signingUp = !signingUp;
    const name = element("authName");
    element("authNameLabel").hidden = !signingUp;
    name.required = signingUp;
    element("authPassword").autocomplete = signingUp ? "new-password" : "current-password";
    element("authSubmit").textContent = signingUp ? "Create account" : "Sign in";
    element("authToggle").textContent = signingUp ? "I already have an account" : "Create an account";
    element("authStatus").textContent = "";
  });
  async function authenticate(path, body, button) {
    const status = element("authStatus");
    button.disabled = true;
    status.classList.remove("error");
    status.textContent = "Please wait.";
    try {
      const response = await fetch(`/api/auth/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      if (!response.ok) {
        status.classList.add("error");
        status.textContent = response.status === 429 ? "Too many attempts. Wait a minute and try again." : response.status >= 500 ? "Sign-in is unavailable. Try again later." : "Could not complete the request. Check your details and try again.";
        return;
      }
      window.location.assign("/onboarding");
    } catch {
      status.classList.add("error");
      status.textContent = "Could not connect. Check your connection and try again.";
    } finally {
      button.disabled = false;
    }
  }
  authForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const fields = new FormData(authForm);
    const body = { email: String(fields.get("email")), password: String(fields.get("password")) };
    void authenticate(
      signingUp ? "sign-up/email" : "sign-in/email",
      signingUp ? { ...body, name: String(fields.get("name")) } : body,
      element("authSubmit")
    );
  });
  element("signOut")?.addEventListener("click", () => {
    void authenticate("sign-out", {}, element("signOut"));
  });
  var countdownId = 0;
  var currentDevice = null;
  var deviceBeforeCode = null;
  var waitingForPair = false;
  function startCountdown(expiresAt) {
    window.clearInterval(countdownId);
    const update = () => {
      const expiry = element("pairingExpiry");
      const button = element("createCode");
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1e3));
      if (remaining === 0) {
        window.clearInterval(countdownId);
        if (expiry) expiry.textContent = "Code expired.";
        const code = element("pairingCode");
        if (code) code.textContent = "";
        if (button) button.textContent = "Get a new code";
        waitingForPair = false;
        return;
      }
      if (expiry)
        expiry.textContent = `Expires in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
    };
    update();
    countdownId = window.setInterval(update, 1e3);
  }
  async function post(path) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  }
  async function refresh() {
    const response = await fetch("/api/onboarding");
    if (response.status === 404) return;
    if (!response.ok) throw new Error(`Could not load setup status (${response.status}).`);
    const status = await response.json();
    if (waitingForPair && status.device_id !== deviceBeforeCode) {
      waitingForPair = false;
      window.clearInterval(countdownId);
      const code = element("pairingCode");
      if (code) code.textContent = "Agent paired.";
      const expiry = element("pairingExpiry");
      if (expiry) expiry.textContent = "";
    }
    currentDevice = status.device_id;
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
    const train = element("trainModel");
    if (train) {
      train.hidden = status.state !== "collecting";
      train.disabled = !status.ready_to_train;
    }
  }
  element("createCode")?.addEventListener("click", async () => {
    const response = await post("/api/pairing-codes");
    const target = element("pairingCode");
    if (!target) return;
    if (!response.ok) {
      target.textContent = `Could not create a code (${response.status}).`;
      return;
    }
    const created = await response.json();
    target.textContent = created.code;
    deviceBeforeCode = currentDevice;
    waitingForPair = true;
    startCountdown(Date.parse(created.expires_at));
  });
  element("trainModel")?.addEventListener("click", async () => {
    const button = element("trainModel");
    button.disabled = true;
    const response = await post("/api/onboarding/train");
    if (!response.ok) {
      const label = element("onboardingState");
      if (label) label.textContent = `Could not start training (${response.status}).`;
    }
    await refresh();
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
