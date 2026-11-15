type OnboardingStatus = {
  state: "collecting" | "training" | "ready" | "failed";
  error: string | null;
  seconds: Record<string, number>;
  required_seconds: number;
  training_windows: number;
  validation_windows: number;
  required_training_windows: number;
  required_validation_windows: number;
  ready_to_train: boolean;
  device_id: string;
};

const gameNames: Record<string, string> = {
  aim: "Aim Trainer",
  tetris: "Block Stack",
  typing: "Word Sprint",
  arena: "Arena Rush",
};
const stateText: Record<OnboardingStatus["state"], string> = {
  collecting: "Agent connected. Play each game until its timer is complete, then train your model.",
  training: "Collection complete. Your model is training.",
  ready: "Setup complete. Your model is active.",
  failed: "Training failed.",
};

const element = (id: string) => document.getElementById(id);
const authForm = element("authForm") as HTMLFormElement | null;
let signingUp = false;

function showSignUp(next: boolean) {
  signingUp = next;
  const name = element("authName") as HTMLInputElement | null;
  const label = element("authNameLabel");
  const password = element("authPassword") as HTMLInputElement | null;
  const submit = element("authSubmit");
  const toggle = element("authToggle");
  const status = element("authStatus");
  if (!name || !label || !password || !submit || !toggle || !status) return;
  label.hidden = !signingUp;
  name.required = signingUp;
  password.autocomplete = signingUp ? "new-password" : "current-password";
  submit.textContent = signingUp ? "Create account" : "Sign in";
  toggle.textContent = signingUp ? "I already have an account" : "Create an account";
  status.textContent = "";
}

element("authToggle")?.addEventListener("click", () => {
  showSignUp(!signingUp);
});

if (new URLSearchParams(window.location.search).get("mode") === "signup") showSignUp(true);

async function authenticate(path: string, body: Record<string, string>, button: HTMLButtonElement) {
  const status = element("authStatus")!;
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Please wait.";
  try {
    const response = await fetch(`/api/auth/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      status.classList.add("error");
      status.textContent =
        response.status === 429
          ? "Too many attempts. Wait a minute and try again."
          : response.status >= 500
            ? "Sign-in is unavailable. Try again later."
            : "Could not complete the request. Check your details and try again.";
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
    element("authSubmit") as HTMLButtonElement,
  );
});
element("signOut")?.addEventListener("click", () => {
  void authenticate("sign-out", {}, element("signOut") as HTMLButtonElement);
});
let countdownId = 0;
let currentDevice: string | null = null;
let deviceBeforeCode: string | null = null;
let waitingForPair = false;

function startCountdown(expiresAt: number) {
  window.clearInterval(countdownId);
  const update = () => {
    const expiry = element("pairingExpiry");
    const button = element("createCode");
    const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
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
  countdownId = window.setInterval(update, 1000);
}

async function post(path: string) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
}

async function refresh() {
  let response: Response;
  try {
    response = await fetch("/api/onboarding");
  } catch {
    throw new Error("Could not load setup status. The server did not respond.");
  }
  const stateLabel = element("onboardingState");
  if (response.status === 404) {
    if (stateLabel?.textContent === "Loading setup status…") {
      stateLabel.classList.remove("error");
      stateLabel.textContent = "Waiting for the agent to connect.";
    }
    return;
  }
  if (!response.ok) throw new Error(`Could not load setup status (${response.status}).`);
  const status = (await response.json()) as OnboardingStatus;
  // A new onboarding device means the code shown on this page was just redeemed.
  if (waitingForPair && status.device_id !== deviceBeforeCode) {
    waitingForPair = false;
    window.clearInterval(countdownId);
    const code = element("pairingCode");
    if (code) code.textContent = "Agent paired.";
    const expiry = element("pairingExpiry");
    if (expiry) expiry.textContent = "";
  }
  currentDevice = status.device_id;
  const revoke = element("revokeDevice");
  if (revoke) revoke.hidden = false;
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
      }),
    );
  }
  const windows = element("windowProgress");
  if (windows) {
    windows.textContent =
      `Keyboard-active windows: ${status.training_windows} / ${status.required_training_windows} training, ` +
      `${status.validation_windows} / ${status.required_validation_windows} calibration. ` +
      "If time is complete but windows are short, keep playing Word Sprint or Block Stack.";
  }
  const play = element("playGames");
  if (play) play.hidden = status.state !== "collecting";
  const train = element("trainModel") as HTMLButtonElement | null;
  if (train) {
    train.hidden = status.state !== "collecting";
    train.disabled = !status.ready_to_train;
  }
}

element("createCode")?.addEventListener("click", async () => {
  const button = element("createCode") as HTMLButtonElement | null;
  const target = element("pairingCode");
  if (button) {
    button.disabled = true;
    button.textContent = "Creating code…";
  }
  try {
    const response = await post("/api/pairing-codes");
    if (!target) return;
    if (!response.ok) {
      target.textContent =
        response.status >= 500
          ? "Could not create a code. The server is unavailable."
          : `Could not create a code (${response.status}).`;
      return;
    }
    const created = (await response.json()) as { code: string; expires_at: string };
    target.textContent = created.code;
    deviceBeforeCode = currentDevice;
    waitingForPair = true;
    startCountdown(Date.parse(created.expires_at));
  } catch {
    if (target) target.textContent = "Could not create a code. The server did not respond.";
  } finally {
    if (button) {
      button.disabled = false;
      if (button.textContent === "Creating code…") button.textContent = "Get pairing code";
    }
  }
});

element("trainModel")?.addEventListener("click", async () => {
  const button = element("trainModel") as HTMLButtonElement;
  const label = element("onboardingState");
  button.disabled = true;
  if (label) {
    label.classList.remove("error");
    label.textContent = "Starting training…";
  }
  try {
    const response = await post("/api/onboarding/train");
    if (!response.ok) {
      if (label) {
        label.classList.add("error");
        label.textContent =
          response.status >= 500
            ? "Could not start training. The server is unavailable."
            : `Could not start training (${response.status}).`;
      }
      button.disabled = false;
      return;
    }
  } catch {
    if (label) {
      label.classList.add("error");
      label.textContent = "Could not start training. The server did not respond.";
    }
    button.disabled = false;
    return;
  }
  try {
    await refresh();
  } catch (error) {
    if (label) {
      label.classList.add("error");
      label.textContent = error instanceof Error ? error.message : "Could not load setup status.";
    }
  }
});

async function dataAction(button: HTMLButtonElement, question: string, request: () => Promise<Response>) {
  if (!window.confirm(question)) return;
  const status = element("dataStatus")!;
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Please wait.";
  try {
    const response = await request();
    if (!response.ok) {
      status.classList.add("error");
      status.textContent =
        response.status === 502
          ? "The model service is unavailable, so nothing was deleted. Try again later."
          : `Could not complete the request (${response.status}).`;
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

element("revokeDevice")?.addEventListener("click", () => {
  if (!currentDevice) return;
  const device = currentDevice;
  void dataAction(
    element("revokeDevice") as HTMLButtonElement,
    "Disconnect this agent? It will need a new pairing code to reconnect.",
    () => post(`/api/devices/${encodeURIComponent(device)}/revoke`),
  );
});
element("deleteData")?.addEventListener("click", () => {
  void dataAction(
    element("deleteData") as HTMLButtonElement,
    "Delete all calibration and monitoring data and trained models? This cannot be undone.",
    () => fetch("/api/me/data", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" }),
  );
});

if (element("gameProgress")) {
  const tick = () =>
    refresh().catch((error) => {
      const label = element("onboardingState");
      if (!label) return;
      label.classList.add("error");
      label.textContent =
        error instanceof Error ? error.message : "Could not load setup status. The server did not respond.";
    });
  void tick();
  window.setInterval(tick, 5000);
}
