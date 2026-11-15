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
  collection: "active" | "paused" | "finished";
  agent: "active" | "paused" | "offline";
  usable_windows: number;
  duration_seconds: number;
  required_windows: number;
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
let agentOffline = true;
let wizardStep = 0;
const wizardSteps = ["warmup", "typing", "activity", "train"] as const;

function showWizard(index: number, announce = false) {
  if (agentOffline && index > 0) {
    if (announce) {
      const note = element("enrollmentControlStatus");
      if (note) {
        note.classList.add("error");
        note.textContent = "The agent is offline. Stay on Warm-up until it is active or paused.";
      }
    }
    index = 0;
  }
  wizardStep = Math.min(Math.max(index, 0), wizardSteps.length - 1);
  wizardSteps.forEach((name, step) => {
    const panel = document.querySelector(`[data-step-panel="${name}"]`);
    if (panel instanceof HTMLElement) panel.hidden = step !== wizardStep;
    const tab = document.querySelector(`[data-step-tab="${name}"]`);
    if (tab instanceof HTMLButtonElement) {
      if (step === wizardStep) tab.setAttribute("aria-current", "step");
      else tab.removeAttribute("aria-current");
      tab.disabled = agentOffline && step > 0;
    }
  });
  const back = element("wizardBack") as HTMLButtonElement | null;
  const next = element("wizardNext") as HTMLButtonElement | null;
  if (back) back.hidden = wizardStep === 0;
  if (next) {
    next.hidden = wizardStep === wizardSteps.length - 1;
    next.disabled = agentOffline;
  }
}

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

if (new URLSearchParams(window.location.search).get("mode") === "signup") showSignUp(true);

function showFieldError(inputId: string, errorId: string, message: string) {
  const input = element(inputId) as HTMLInputElement | null;
  const error = element(errorId);
  if (!input || !error) return;
  input.setAttribute("aria-invalid", message ? "true" : "false");
  error.textContent = message;
}

function emailMessage(value: string) {
  const email = value.trim();
  if (!email) return "Enter an email address.";
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return "Enter an email address like name@example.com.";
  }
  return "";
}

function passwordMessage(value: string) {
  if (!value) return "Enter a password.";
  if (value.length < 8) return "Use at least 8 characters.";
  if (value.length > 128) return "Use at most 128 characters.";
  return "";
}

function nameMessage(value: string) {
  const name = value.trim();
  if (!name) return "Enter your name.";
  if (name.length > 100) return "Use at most 100 characters.";
  return "";
}

function validateAuth() {
  const email = element("authEmail") as HTMLInputElement | null;
  const password = element("authPassword") as HTMLInputElement | null;
  const name = element("authName") as HTMLInputElement | null;
  if (!email || !password) return false;
  const emailError = emailMessage(email.value);
  const passwordError = passwordMessage(password.value);
  const nameError = signingUp && name ? nameMessage(name.value) : "";
  showFieldError("authEmail", "emailError", emailError);
  showFieldError("authPassword", "passwordError", passwordError);
  if (signingUp) showFieldError("authName", "nameError", nameError);
  const firstInvalid = nameError ? name : emailError ? email : passwordError ? password : null;
  firstInvalid?.focus();
  return !emailError && !passwordError && !nameError;
}

for (const [inputId, errorId, message] of [
  ["authEmail", "emailError", emailMessage],
  ["authPassword", "passwordError", passwordMessage],
  ["authName", "nameError", nameMessage],
] as const) {
  element(inputId)?.addEventListener("blur", () => {
    if (inputId === "authName" && !signingUp) return;
    const input = element(inputId) as HTMLInputElement | null;
    if (input) showFieldError(inputId, errorId, message(input.value));
  });
}

function afterAuth(path: string) {
  if (path === "sign-out") {
    window.location.assign("/onboarding");
    return;
  }
  window.location.assign(authForm?.dataset.next === "/dashboard" ? "/dashboard" : "/onboarding");
}

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
    afterAuth(path);
  } catch {
    status.classList.add("error");
    status.textContent = "Could not connect. Check your connection and try again.";
  } finally {
    button.disabled = false;
  }
}

authForm?.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!validateAuth() || !authForm) return;
  const fields = new FormData(authForm);
  const body = { email: String(fields.get("email")).trim(), password: String(fields.get("password")) };
  void authenticate(
    signingUp ? "sign-up/email" : "sign-in/email",
    signingUp ? { ...body, name: String(fields.get("name")).trim() } : body,
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
    agentOffline = true;
    const agent = element("agentStatus");
    if (agent) agent.textContent = "Agent status: offline";
    showWizard(0);
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
  const required = status.required_windows;
  const progressBar = element("enrollmentProgress") as HTMLProgressElement | null;
  const progressLabel = element("enrollmentProgressLabel");
  if (progressBar) {
    progressBar.max = required;
    progressBar.value = Math.min(status.usable_windows, required);
    progressBar.textContent = `${status.usable_windows} of ${required} usable windows`;
  }
  if (progressLabel) {
    progressLabel.textContent = `${status.usable_windows} of ${required} usable windows · ${status.duration_seconds} s recorded`;
  }
  agentOffline = status.agent === "offline";
  const agent = element("agentStatus");
  if (agent) agent.textContent = `Agent status: ${status.agent}`;
  const collecting = status.state === "collecting";
  const pause = element("pauseEnrollment") as HTMLButtonElement | null;
  const resume = element("resumeEnrollment") as HTMLButtonElement | null;
  const discard = element("discardEnrollment") as HTMLButtonElement | null;
  const finish = element("finishEnrollment") as HTMLButtonElement | null;
  if (pause) pause.disabled = !collecting || status.collection !== "active";
  if (resume) resume.disabled = !collecting || status.collection !== "paused";
  if (discard) discard.disabled = !collecting;
  if (finish) finish.disabled = !collecting || status.collection === "finished";
  showWizard(agentOffline ? 0 : wizardStep);
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

async function enrollmentControl(path: string, confirmText: string | null) {
  if (confirmText && !window.confirm(confirmText)) return;
  const status = element("enrollmentControlStatus");
  if (status) {
    status.classList.remove("error");
    status.textContent = "Please wait.";
  }
  try {
    const response = await post(path);
    if (!response.ok) {
      if (status) {
        status.classList.add("error");
        status.textContent =
          response.status >= 500
            ? "Could not update enrollment. The server is unavailable."
            : `Could not update enrollment (${response.status}).`;
      }
      return;
    }
  } catch {
    if (status) {
      status.classList.add("error");
      status.textContent = "Could not update enrollment. The server did not respond.";
    }
    return;
  }
  try {
    await refresh();
    if (status && status.textContent === "Please wait.") status.textContent = "";
  } catch (error) {
    if (status) {
      status.classList.add("error");
      status.textContent = error instanceof Error ? error.message : "Could not load setup status.";
    }
  }
}

element("pauseEnrollment")?.addEventListener("click", () => {
  void enrollmentControl("/api/onboarding/pause", null);
});
element("resumeEnrollment")?.addEventListener("click", () => {
  void enrollmentControl("/api/onboarding/resume", null);
});
element("discardEnrollment")?.addEventListener("click", () => {
  void enrollmentControl(
    "/api/onboarding/discard",
    "Discard collected enrollment windows for this setup? The account and the paired agent stay.",
  );
});
element("finishEnrollment")?.addEventListener("click", () => {
  void enrollmentControl(
    "/api/onboarding/finish",
    "Finish enrollment? The agent stops sending enrollment windows. Training can still use the windows already collected.",
  );
});
document.querySelectorAll("[data-step-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    const name = (tab as HTMLElement).dataset.stepTab;
    showWizard(wizardSteps.indexOf(name as (typeof wizardSteps)[number]), true);
  });
});
element("wizardBack")?.addEventListener("click", () => showWizard(wizardStep - 1));
element("wizardNext")?.addEventListener("click", () => showWizard(wizardStep + 1, true));

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
