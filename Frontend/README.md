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
4. Build the browser app: `npm run build`
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

## Privacy and current scope

- Game input is handled in the browser for gameplay. Input events are transient and are not sent to Flask.
- At the end of a round, the browser sends only counts and timing statistics, selected game, and score.
- The typing exercise does not send typed words. It sends the same numeric timing summary as the other activities.
- Flask stores those aggregate samples in a local SQLite database (`inputprint.sqlite3`). **Clear Data** removes them.
- This is an arcade data-collection prototype. It does not include the Windows background agent or the authentication/model pipeline described in the InputPrint project brief.

The aim trainer uses mouse clicks or the A button. Block Stack uses arrows/WASD or the gamepad face buttons and D-pad. Arena Rush uses WASD or the arrow keys to move, the mouse to aim, and left-click to fire. Word Sprint shuffles its word list each round and highlights correct letters as you type.
