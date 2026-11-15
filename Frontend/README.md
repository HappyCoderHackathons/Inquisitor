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

- The home page (`/`) states the product pitch, links to Create Account and Log In, says a Windows agent is required, and explains that scores are anomaly signals rather than identity.
- The privacy page (`/privacy`) lists the accepted feature-window fields and units, a valid sample payload, and what is never collected.
- The arcade (`/arcade`) contains the playable games and recent round summaries.
- The user dashboard (`/dashboard`) shows account, model, and agent status as not connected until those services exist. Anomaly signals are prompts to verify. The page does not name a person or quote a measured accuracy. Sign in and pair devices at `/onboarding`.
- Use the navigation on each page to move between home, activities, and the dashboard.

See [INTEGRATION_HANDOFF.md](INTEGRATION_HANDOFF.md) for dashboard code locations and the remaining authenticated WebSocket, profile, model-status, and account-deletion work.

## Collecting local feature windows

On `/dashboard`, choose `Enrollment` or `Monitoring`, check the consent box, and start collection. Focus the capture area and type naturally. After at least 30 seconds and 30 key presses, the page saves one aggregate feature window; keep typing to collect more. Pause or stop at any time. Incomplete windows are discarded.

The browser sends only windows validated against [`shared/feature-window-v1.schema.json`](../shared/feature-window-v1.schema.json) to the local Flask app. Key identities and typed content are used only transiently in the browser and are not saved or sent. Windows are stored in the local `inputprint.sqlite3`; use **Download JSONL** to export them. These are browser-collected examples, not Windows-agent captures or model detections. No model training or remote service call is made.

Collection sessions use a random local browser ID and a new session ID each time collection starts. For session-separated evaluation, stop and start a new collection session between sessions. Label who produced each collection outside the exported feature contract before using it as genuine or second-user data.

Xbox and Nintendo controllers work through the browser Gamepad API. Connect the controller and press a button once so the browser can recognize it. Keyboard controls are shown in each game. The arcade sprint rotates through four one-minute rounds; each game can also be played on its own.

## Hosted onboarding

When all Flask variables below are set, `server.py` also serves Better Auth sign-in, agent pairing, and onboarding training backed by MongoDB Atlas. If none are set, the app stays local-only. If only some are set, startup fails.

| Variable | Purpose |
| --- | --- |
| `BETTER_AUTH_URL` | Public Flask origin, e.g. `http://localhost:5000`; use HTTPS when hosted |
| `MONGODB_URI`, `MONGODB_DB` | Atlas connection string and database name |
| `ML_SERVICE_URL`, `ML_TOKEN_FILE` | Remote ML service URL and the file holding its bearer token |

Better Auth runs as a separate Node 22.18+ process. In `Frontend/`, run `npm ci`, then
set `BETTER_AUTH_SECRET` to a random secret of at least 32 characters and run `npm run auth`.
That command loads the root `.env` if present; process environment variables take precedence.
The auth process also requires `BETTER_AUTH_URL`, `MONGODB_URI`, and `MONGODB_DB`.
Flask needs its variables exported into its process environment as before; it does not load `.env` itself.
Run both processes on the same host. Only Flask should be publicly reachable;
the auth service binds to `127.0.0.1:3001` and Flask forwards the required `/api/auth/` routes.
Use the exact same hostname in the browser and `BETTER_AUTH_URL`, including during local development.

At `/onboarding`, choose **Create an account** for a new email/password account or
**Sign in** for an existing one. `/login` and `/register` open those same pages.
Empty fields, a malformed email, a missing name, and a password shorter than 8
characters are shown next to the field. `/dashboard` redirects to `/login` until
a session exists. `/`, `/arcade`, and `/privacy` stay available without an account.
Better Auth handles password hashing. No SMTP sender,
enterprise connection, or hosted login dashboard is required. Social SSO is not configured.
Email ownership is not verified and password reset is unavailable until email delivery is added.
Do not use email addresses as verified organization membership or link legacy accounts by email.

SSOJet variables and Authlib are no longer used. Existing provider sessions do not carry over.
No old data is deleted: Better Auth uses `auth_user`, `auth_account`, `auth_session`,
`auth_verification`, and its rate-limit collection. Application identities use a `better-auth:`
prefix, so old provider IDs cannot accidentally grant access to existing devices or models.
Before migrating real accounts, explicitly map verified identities and their account-scoped data.

The user signs in at `/onboarding` and gets a single-use pairing code that expires after 10 minutes. The agent redeems it at `POST /api/agent/pair`, sending the HID usage category returned by Rust's `devices::usage_name` as `name`. The device record stores that category, the owning `user_id`, pairing state, the token's SHA-256 hash, and `last_heartbeat_at`. Heartbeat time starts as null and is updated on authenticated agent requests. The current Rust client does not yet call this pairing endpoint.

Pairing opens an onboarding enrollment for that device. Each arcade round the user starts becomes an `EnrollmentSession` record in the `rounds` collection, linked to its device and onboarding. Sessions have `active`, `completed`, or `cancelled` status; `valid_window_count` counts windows with 30 or more key presses. The agent reads the active session from `GET /api/agent/onboarding` and uploads enrollment windows to `POST /api/agent/windows`. A finished round is credited by server time, capped at 60 seconds; cancelled rounds earn no time. `GET /api/onboarding` reports seconds per game and counts the usable windows. Aim Trainer rounds count toward time but rarely produce usable windows.

The **Train my model** button turns on once each of the four games has 150 seconds credited and there are at least 20 training windows and 10 calibration windows (`POST /api/onboarding/train` returns 409 before then). The latest keyboard-active round of each game is the calibration set. The server sends the usable windows to the ML service's genuine-only model and activates the result; the ML host must run with `ML_ALLOW_TRAINING=1`. A failed training run ends that onboarding, and pairing again starts a new one.

After training, the agent sends `monitoring` windows to the same upload endpoint. Each is scored by the active model, and the result is stored in `monitoring_windows`. Monitoring windows before training, and enrollment windows after it, return 409.

Agent uploads accept strict shared feature-window schemas v1-v4, are limited to 64 KiB and 60 requests per minute per device, and reject duplicate device/session/sequence tuples. Audit logs contain request metadata and outcomes only, not feature values or payload bodies.

Better Auth validates its signed HttpOnly session cookie against MongoDB on each authenticated request. Sessions expire after 12 hours without refresh; sign-out revokes the server-side session. Session cookies are `SameSite=Lax`, and `Secure` when the public origin is HTTPS. Flask forwards every Set-Cookie header from authentication responses. Authentication and pairing APIs reject cross-site JSON requests. Every other state-changing route rejects a cross-site `Sec-Fetch-Site` value and an `Origin` other than the request host or `BETTER_AUTH_URL`. A missing `Origin` stays allowed for the agent and other non-browser clients. Responses set `Content-Security-Policy` (with `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, and `Strict-Transport-Security` only on HTTPS. The endpoint checklist and threat model are in [the threat model](../docs/threat-model.md). Requests are rate-limited, returning 429, at 30 per minute per IP for auth posts, 10 per 10 minutes per IP for pairing attempts, and 60 per minute per device for window uploads. Better Auth also applies its own database-backed endpoint limits. Behind a reverse proxy, configure Werkzeug's `ProxyFix` for the exact trusted proxy count so limits use the client address; never trust arbitrary forwarded IP headers.

The application `users` collection stores the namespaced account ID, email, creation time, and privacy preferences. Keyboard collection, mouse collection, and model-training preferences default to `false`; they are separate from Better Auth's `auth_user` records. The `consent_records` collection is indexed by user, scope, and grant time. Its records contain a consent scope, UTC `granted_at`, and nullable UTC `revoked_at` timestamp. This change defines and initializes the collections; consent-management endpoints and UI are not yet implemented.

Store Atlas credentials and `BETTER_AUTH_SECRET` only in deployment configuration. Run
`npm run test:auth` for Better Auth credential/session checks and the existing Python
unittest suite for Flask's auth boundary and account/device isolation. Auth tests use
the in-memory adapter; live Atlas connectivity still needs a deployment check.

## Privacy and current scope

- Game input is handled in the browser for gameplay. Input events are transient and are not sent to Flask.
- At the end of a round, the browser sends only counts and timing statistics, selected game, and score.
- The typing exercise does not send typed words. It sends the same numeric timing summary as the other activities.
- Flask stores those aggregate samples in a local SQLite database (`inputprint.sqlite3`). **Clear Data** removes them.
- Arcade rounds still store only local aggregate samples in SQLite. The feature-window contract used by the agent and the model is the one on `/privacy`. Sign-in and pairing stay at `/onboarding` when hosted auth is configured. `/onboarding?mode=signup` opens account creation.

The aim trainer uses mouse clicks or the A button. Block Stack uses arrows/WASD or the gamepad face buttons and D-pad. Arena Rush uses WASD or the arrow keys to move, the mouse to aim, and left-click to fire. Word Sprint shuffles its word list each round and highlights correct letters as you type.
