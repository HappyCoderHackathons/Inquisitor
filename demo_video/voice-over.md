# Voice-over by speaker

Record each line as its own file named by cue id, for example `vo/c06_problem.wav` (WAV, any sample rate).
Record two or three takes, keep the cleanest, and trim the silence at both ends. The build re-times the video
from your line lengths and refuses to render past 3:00, so aim for the budget below (placeholder length plus about 15%).
Read at a relaxed, even pace. If the build reports it is over, shorten the longest line or set `BREATHE` lower in `build/build.py`.

## Austin

Optional retake: `c13_train`, which currently says "isolated forest" instead of "Isolation Forest".

| Cue | Line | Budget |
| --- | --- | --- |
| `c01_title` | Hi, this is Inquisitor. | 1.7 s |
| `c02_austin` | I'm Austin Sternberg. I built the model service and tied the system together. | 5.4 s |
| `c06_problem` | A password is checked once. If someone else sits down after you unlock your PC, nothing notices. Inquisitor keeps checking whether the keyboard and mouse rhythm still looks like the person who enrolled. | 14.6 s |
| `c13_train` | Training runs on a separate, private model service. For this account, it fits an Isolation Forest and sets a threshold from the latest rounds. | 10.7 s |
| `c14_monitor` | Now it's monitoring. For this recording, a script sends synthetic windows through the same agent API. Each window is scored against the threshold, and below the line, the status stays normal. | 14.9 s |
| `c16b_gemini` | When an alert opens, Gemini suggests a few things to check, like who had access to the PC or a new keyboard. It only gets aggregate scores and feature names, never keystrokes or what's on screen. | 12.0 s (new) |
| `c18_end` | That's Inquisitor. Thanks for watching. | 2.5 s |

## Ian

| Cue | Line | Budget |
| --- | --- | --- |
| `c03_ian` | I'm Ian Rohrbacher. I built the Windows agent in Rust. | 3.8 s |
| `c08_agent` | The agent is a native Windows app written in Rust. Before anything is collected, it shows what it records and what it never collects. It installs for one user, without administrator rights. | 12.1 s |
| `c10_pair` | You enter that code in the agent. It gets its own revocable device credential, never your password, and it only connects outward, so nothing reaches into your PC. | 10.2 s |

## Conner

| Cue | Line | Budget |
| --- | --- | --- |
| `c04_conner` | I'm Conner Brunner. I built the website, the games, and the dashboard. | 3.6 s |
| `c07_privacy` | It works on timing, not content. Every thirty seconds, the agent sends counts, averages, and spreads. Never which keys, never what you typed, and never what's on screen. The privacy page lists every field. | 12.4 s |
| `c09_signup` | On the website, you create an account and get a one-time pairing code. | 3.9 s |
| `c11_games` | Enrollment runs through short calibration games. While you play, the agent sends timing windows for that round. | 6.6 s |
| `c15_switch` | This is the judge view. We mark the moment a second person takes over. Their windows score above the threshold, and a few seconds later the account shows Needs verification. | 9.6 s |

## Colin

| Cue | Line | Budget |
| --- | --- | --- |
| `c05_colin` | And I'm Colin Grant. I built the backend data model and its API. | 4.5 s |
| `c12_rounds` | The backend tracks every round and counts the usable windows. Once there are enough for training and calibration, you can train your model. | 7.8 s |
| `c16_alert` | An alert opens when three of the last four windows are unusual, so one odd window isn't enough. It's a signal to check, not proof of who's typing, and Inquisitor never locks the PC. | 11.3 s |
| `c17_devices` | You can revoke a device at any time, and delete your data and models from your account. | 4.9 s |
