# Demo video script

Current cut: 2:52 with the team's recorded VO, hard limit 3:00. 1080p30, voiced by the four of us over a music bed, with short
caption chips where the footage needs context. Times below come from `cues.json` for the current takes. When new
takes are dropped in, the build re-times every scene from the new line lengths.

[voice-over.md](voice-over.md) has the same lines split by speaker, with a time budget for each.

## Playbook

| Time | Picture | Source | VO (cue id, speaker) | On-screen text |
| --- | --- | --- | --- | --- |
| 0:00 | Title card: logo, name, tagline | card | `c01_title` Austin: "Hi, this is Inquisitor." | Timing summaries, not typed content. |
| 0:03 | Team card: photos, names, roles | card | `c02_austin` Austin: "I'm Austin Sternberg. I built the model service and tied the system together." `c03_ian` Ian: "I'm Ian Rohrbacher. I built the Windows agent in Rust." `c04_conner` Conner: "I'm Conner Brunner. I built the website, the games, and the dashboard." `c05_colin` Colin: "And I'm Colin Grant. I built the backend data model and its API." | Names and roles |
| 0:22 | Landing page, slow scroll through "What reaches the model" and "Limitations" | `01_landing` | `c06_problem` Austin: "A password is checked once. If someone else sits down after you unlock your PC, nothing notices. Inquisitor keeps checking whether the keyboard and mouse rhythm still looks like the person who enrolled." | |
| 0:35 | Privacy page, scroll to the window and v4 feature tables | `02_privacy` | `c07_privacy` Conner: "It works on timing, not content. Every thirty seconds, the agent sends counts, averages, and spreads. Never which keys, never what you typed, and never what's on screen. The privacy page lists every field." | Only aggregate timing leaves the PC |
| 0:49 | Agent setup: terms scroll, install location, Ready to install | `agent_01..03` | `c08_agent` Ian: "The agent is a native Windows app written in Rust. Before anything is collected, it shows what it records and what it never collects. It installs for one user, without administrator rights." | Windows agent 0.7.2 setup |
| 1:03 | Create account, then Get pairing code shows the code | `03_signup`, `04_pair` | `c09_signup` Conner: "On the website, you create an account and get a one-time pairing code." | Demo account on a local test server |
| 1:11 | Agent "Connect to your account": the same code typed, Connect. Website shows Agent paired, enrollment unlocks | `agent_04_pair`, `04_pair` | `c10_pair` Ian: "You enter that code in the agent. It gets its own revocable device credential, never your password, and it only connects outward, so nothing reaches into your PC." | Agent: Connect to your account |
| 1:23 | Arcade, Word Sprint round being typed | `05_game` | `c11_games` Conner: "Enrollment runs through short calibration games. While you play, the agent sends timing windows for that round." | Word Sprint calibration round |
| 1:32 | Setup page: 30 of 30 windows, Train my model, "Setup complete. Your model is active." | `06_train` | `c12_rounds` Colin: "The backend tracks every round and counts the usable windows. Once there are enough for training and calibration, you can train your model." `c13_train` Austin: "Training runs on a separate, private model service. For this account, it fits an Isolation Forest and sets a threshold from the latest rounds." | Remaining rounds sped up for the recording |
| 1:49 | Dashboard: status Normal, score line under the threshold, model details | `07_dashboard` | `c14_monitor` Austin: "Now it's monitoring. For this recording, a script sends synthetic windows through the same agent API. Each window is scored against the threshold, and below the line, the status stays normal." | Synthetic windows sent through the agent API |
| 2:02 | Judge view: Mark the switch, score jumps above the line, "Needs verification followed 5 seconds later" | `08_demo_switch` | `c15_switch` Conner: "This is the judge view. We mark the moment a second person takes over. Their windows score above the threshold, and a few seconds later the account shows Needs verification." | Synthetic windows sent through the agent API |
| 2:15 | Dashboard: Needs verification, open alert with Expected and Unexpected | `09_alert` | `c16_alert` Colin: "An alert opens when three of the last four windows are unusual, so one odd window isn't enough. It's a signal to check, not proof of who's typing, and Inquisitor never locks the PC." | |
| 2:27 | Dashboard alert with "Things to check (suggested by Gemini)" and five suggestions | `11_gemini` | `c16b_gemini` Austin: "When an alert opens, Gemini suggests a few things to check, like who had access to the PC or a new keyboard. It only gets aggregate scores and feature names, never keystrokes or what's on screen." | Suggestions from Gemini on aggregate scores only |
| 2:40 | Devices page with Revoke | `10_devices` | `c17_devices` Colin: "You can revoke a device at any time, and delete your data and models from your account." | |
| 2:46 | End card: logo, repo URL, names, limits, music credit | card | `c18_end` Austin: "That's Inquisitor. Thanks for watching." | Research prototype. Scores are anomaly signals, not proof of identity. |

## Claims check

| Claim | Verified by |
| --- | --- |
| Agent sends a summary every 30 seconds: counts, averages, spreads | Agent terms text (`backend/rust/src/wizard.rs`), `Frontend/templates/onboarding.html` warm-up list |
| Never key identities, typed text, or screen content | Agent terms "WHAT IS NEVER COLLECTED", `AGENTS.md` input boundaries |
| Privacy page lists every field | `/privacy` window and v4 feature tables, shown in the footage |
| Rust, per user, no administrator rights | Agent "Ready to install" page in the footage |
| Nothing collected until you accept | Agent terms page header text in the footage |
| One-time pairing code, revocable device credential, never the password | `POST /api/agent/pair` in `Frontend/hosted.py` (single-use code, 10 minute expiry, per-device token), `docs/architecture.md` |
| Agent only connects outward | `docs/architecture.md` data flow; the agent opens the WSS connection |
| Backend counts usable windows per round, needs training and calibration windows | `progress()` in `Frontend/hosted.py`: 20 training plus 10 calibration windows |
| Separate private model service, Isolation Forest, threshold from the latest rounds | Model details in the footage ("Algorithm: Isolation Forest"), `progress()` comment: the latest round of each game calibrates the threshold |
| Alert after three of the last four windows | `ALERT_WINDOW = 4`, `ALERT_UNUSUAL_COUNT = 3` in `Frontend/hosted.py`, dashboard footnote in the footage |
| Signal, not proof; never locks the PC | Dashboard and demo footnotes, agent terms |
| Gemini suggests things to check when an alert opens; only aggregate scores and feature names are sent | `Frontend/gemini.py` prompt and context, `add_investigation` in `Frontend/hosted.py`, `docs/privacy.md` Gemini paragraph; suggestions in the footage are a real Gemini response |
| Revoke a device, delete data and models | `/devices` Revoke, `DELETE /api/me/data` |

Words to avoid: "detects intruders", "proves", "accurate", "secure", "identifies". The guest windows are synthetic, so the
video makes no accuracy claim. The script says so out loud (`c14_monitor`) and on screen.

## How the footage was made

- Everything runs on a throwaway local stack (`stack/start.sh`): local `mongod` replica set, Better Auth, Flask, and a
  local copy of the ML service with training enabled. Nothing touched Atlas or the hosted ML host.
- The agent is the published 0.7.2 release, run with `APPDATA` and `LOCALAPPDATA` pointed at
  `C:\tmp\inquisitor-agent-demo`, `COMPUTERNAME=DEMO-PC`, and no input devices allowed, so it captured nothing.
  It paired with the code shown on the website. Then a scripted stand-in agent took over that device's token in the
  local database and posted synthetic schema-v4 windows through `/api/agent/windows`.
- Calibration rounds start and finish through the website API. Off camera, each round's start time is moved back
  150 seconds so the full enrollment fits in the recording. The model is really trained and every score is real
  output from the ML service, on synthetic data.

## Credits

Music: "Plains of Luminescence" by vitalezzz, [CC0](https://creativecommons.org/publicdomain/zero/1.0/), from
[OpenGameArt](https://opengameart.org/node/180398). CC0 needs no attribution; the end card credits it anyway.
