# InputPrint Arcade

A local Flask + TypeScript arcade prototype for collecting opt-in behavioral input summaries through short game sessions.

## Run locally

1. Create and activate a Python virtual environment:
   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```
   On Windows PowerShell, use `.venv\Scripts\Activate.ps1` to activate it.
   If this folder was moved after creating `.venv`, recreate it with
   `python3 -m venv --clear .venv` before activating it again.
2. Install Python dependencies: `python3 -m pip install -r requirements.txt`
3. Install the TypeScript bundler: `npm install`
4. Build the browser app: `npm run build`. Commit the rebuilt `static/` bundles with any TypeScript change; CI fails if they are stale. Before committing, run `npm run format` and `npm run typecheck`.
5. Start Flask: `python3 server.py`
6. Open [http://127.0.0.1:5000](http://127.0.0.1:5000)

## Pages

- The home page (`/`) introduces the prototype, its four games, and its privacy scope.
- The arcade (`/arcade`) contains the playable games and recent round summaries.
- The user dashboard (`/dashboard`) shows the planned account, model, and agent status areas. SSOJet and agent/model connections are not integrated yet.
- Use the navigation on each page to move between home, activities, and the dashboard.

See [INTEGRATION_HANDOFF.md](INTEGRATION_HANDOFF.md) for dashboard code locations and the remaining authenticated WebSocket, SSOJet, model-status, and account-deletion work.

## Collecting local feature windows

On `/dashboard`, choose `Enrollment` or `Monitoring`, check the consent box, and start collection. Focus the capture area and type naturally. After at least 30 seconds and 30 key presses, the page saves one aggregate feature window; keep typing to collect more. Pause or stop at any time. Incomplete windows are discarded.

The browser sends only windows validated against [`shared/feature-window-v1.schema.json`](../shared/feature-window-v1.schema.json) to the local Flask app. Key identities and typed content are used only transiently in the browser and are not saved or sent. Windows are stored in the local `inputprint.sqlite3`; use **Download JSONL** to export them. These are browser-collected examples, not Windows-agent captures or model detections. No model training or remote service call is made.

Collection sessions use a random local browser ID and a new session ID each time collection starts. For session-separated evaluation, stop and start a new collection session between sessions. Label who produced each collection outside the exported feature contract before using it as genuine or second-user data.

Xbox and Nintendo controllers work through the browser Gamepad API. Connect the controller and press a button once so the browser can recognize it. Keyboard controls are shown in each game. The arcade sprint rotates through four one-minute rounds; each game can also be played on its own.

## Hosted onboarding

When all of these environment variables are set, `server.py` also serves SSOJet sign-in, agent pairing, and onboarding training backed by MongoDB Atlas. If none are set, the app stays local-only. If only some are set, startup fails.

| Variable | Purpose |
| --- | --- |
| `SSOJET_AUTHORITY` | SSOJet base URL; `/.well-known/openid-configuration` is appended |
| `SSOJET_CLIENT_ID`, `SSOJET_CLIENT_SECRET` | SSOJet regular web application credentials |
| `SSOJET_REDIRECT_URI` | Must exactly match the SSOJet callback, e.g. `https://<host>/auth/callback` |
| `SESSION_SECRET` | Random string that signs session cookies |
| `MONGODB_URI`, `MONGODB_DB` | Atlas connection string and database name |
| `ML_SERVICE_URL`, `ML_TOKEN_FILE` | Remote ML service URL and the file holding its bearer token |

The user signs in at `/onboarding` and gets a single-use pairing code that expires after 10 minutes. The agent redeems it at `POST /api/agent/pair` and receives a device token; only the token's SHA-256 hash is stored.

Pairing opens an onboarding enrollment for that device. Each arcade round the user starts becomes a session. The agent reads the active session from `GET /api/agent/onboarding` and uploads enrollment windows to `POST /api/agent/windows`. A finished round is credited by server time, capped at 60 seconds; cancelled rounds earn no time. `GET /api/onboarding` reports seconds per game and counts the usable windows (30 or more key presses). Aim Trainer rounds count toward time but rarely produce usable windows.

Training starts once each of the four games has 150 seconds credited and there are at least 20 training windows and 10 calibration windows. The latest keyboard-active round of each game is the calibration set. The server sends the usable windows to the ML service's genuine-only model and activates the result; the ML host must run with `ML_ALLOW_TRAINING=1`. A failed training run ends that onboarding, and pairing again starts a new one.

Store Atlas and SSOJet credentials only in deployment configuration.

## Privacy and current scope

- Game input is handled in the browser for gameplay. Input events are transient and are not sent to Flask.
- At the end of a round, the browser sends only counts and timing statistics, selected game, and score.
- The typing exercise does not send typed words. It sends the same numeric timing summary as the other activities.
- Flask stores those aggregate samples in a local SQLite database (`inputprint.sqlite3`). **Clear Data** removes them.
- This is an arcade data-collection prototype. It does not include the Windows background agent or the authentication/model pipeline described in the InputPrint project brief.

The aim trainer uses mouse clicks or the A button. Block Stack uses arrows/WASD or the gamepad face buttons and D-pad. Arena Rush uses WASD or the arrow keys to move, the mouse to aim, and left-click to fire. Word Sprint shuffles its word list each round and highlights correct letters as you type.
