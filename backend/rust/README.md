# Inquisitor backend

The Windows client lives here. Rust owns native keyboard I/O, local
orchestration, and transport. Python with PyTorch will later provide ML
inference through the Rust client's aggregate JSON process boundary.

See [`../docs/architecture.md`](../docs/architecture.md) for stack decisions
and [`../AGENTS.md`](../AGENTS.md) for project rules.

## Layout

- [`rust/`](rust/): Cargo crate `inquisitor`. Lists input devices and
  captures raw input events as CSV.

Python ML code is not yet in the tree.

## Prerequisites

- Windows 10 or later. The crate uses the Win32 Raw Input API and does not
  target other platforms.
- Rust toolchain. Built with 1.91.1 on Windows 11.

## Build

From [`rust/`](rust/):

```sh
cargo build            # debug
cargo build --release  # release exe at target/release/Inquisitor.exe
```

[`.cargo/config.toml`](.cargo/config.toml) links the C runtime statically, so the
exe depends only on system DLLs and needs no Visual C++ redistributable.

## Release

Pushing a version tag publishes a GitHub release
([`release-rust.yml`](../../.github/workflows/release-rust.yml)):

1. Set `version` in `Cargo.toml`, for example `0.2.0`, and commit it to `main`.
2. Tag that commit and push the tag:

   ```sh
   git tag 0.2.0
   git push origin 0.2.0
   ```

   `v0.2.0` works too.

The workflow runs the same format, lint, and test checks as CI, then builds
the release exe and checks its embedded manifest. It fails if the tag, without
any leading `v`, is not exactly the `Cargo.toml` version, since the setup
wizard reports that version. The workflow that runs is the one in the tagged
commit, so tag a commit that already has the version set.

The release gets `Inquisitor-<version>-windows-x64.exe` and its `.sha256`
checksum, with notes generated from the merged pull requests. A tag with a
hyphen, such as `0.2.0-rc.1`, is published as a prerelease; put the same
suffix in `Cargo.toml`. The exe is not code-signed, so Windows SmartScreen may
warn on first run.

## Usage

```text
Inquisitor                        Tray app if installed, otherwise the setup wizard
Inquisitor tray                   Tray app
Inquisitor setup                  Install, update, repair, or remove
Inquisitor uninstall              Setup wizard on the remove page
Inquisitor list                   List connected input devices
Inquisitor capture                Capture to %APPDATA%\inquisitor\capture-<ts>.csv
Inquisitor capture --out <path>   Capture to <path>
Inquisitor capture --out -        Capture to stdout
Inquisitor --help                 Print usage
```

`capture` prints the resolved output path to stderr and then runs until
stopped (Ctrl+C or killed). It creates the output directory if needed.

## Install, update, repair, remove

Running `Inquisitor.exe` from anywhere other than the install folder opens the
setup wizard. A fresh install goes through three pages: terms, install
location, and options.

Terms: the first page lists what is recorded and what is never collected.
Accepting stores `terms_accepted=<version>` in `config.ini`. Nothing is
collected until then: the tray asks for the terms before it starts, drops every
event while they are not accepted, and `Inquisitor capture` refuses to run.
Raising `TERMS_VERSION` in `src/config.rs` asks everyone again.

Install location defaults to `%LOCALAPPDATA%\Programs\Inquisitor`. A different
folder can be typed or picked with Browse. An `Inquisitor` folder is always
added unless the path already ends in one. The page shows the space required
and available. Update and repair keep the folder recorded in Apps & features.

The install is for the current user only, so administrator rights are not
needed:

- The exe is copied to `<install folder>\Inquisitor.exe`.
- An entry is added to Apps & features. Its Uninstall runs
  `Inquisitor.exe uninstall` and its Modify runs `Inquisitor.exe setup`.
- Options: Start menu shortcut (on by default), Start with Windows (keeps the
  current setting), and launch when done.

The wizard's main action follows the install state: Install when nothing is
installed, Update when another version is installed, and Repair when the same
version is installed or the wizard runs from the installed exe. Any running
tray is closed first so the exe can be replaced. Run once starts the tray from
the current exe without installing.

Remove deletes the installed exe and then its folder only if it is empty (never
a recursive delete), the shortcut, the startup entry, and the Apps
& features entry. Captured data and `config.ini` are kept unless "Also delete
captured data and settings" is ticked. That option deletes only
`capture-*.csv` files and `config.ini`, so a data folder the user picked keeps
its other files.

The app icon comes from `../../docs/icons/png/inquisitor-icon-*.png`.
`build.rs` embeds those PNGs into the exe as icon resource 1.

## Device listing

`Inquisitor list` enumerates every device Windows reports
through Raw Input: keyboards, mice, and HID devices (controllers, pens,
digitizers, media keys). Each row shows the kind, vendor and product IDs
where available, a usage label such as `gamepad` or `touch pad`, and the
system device path.

## CSV schema

Columns:

```text
iso_time,monotonic_s,io_type,device_id,event,control,hold_ms,flight_ms,dx,dy,value
```

- `iso_time`: UTC timestamp with milliseconds, e.g. `2026-11-15T03:13:19.875Z`.
- `monotonic_s`: seconds since capture start, six decimal places.
- `io_type`: `keyboard`, `mouse`, or for HID devices `gamepad`, `keypad`,
  `consumer` (media keys), `touchpad`, `touchscreen`, `pen`, `digitizer`, `hid`.
- `device_id`: short per-session id such as `kbd0`, `mouse0`, `hid0`. Stable
  within a run; not stable across runs.
- `event`: see the table below.
- `control`: named control that fired.
- `hold_ms`: milliseconds between press and release. Filled on release rows
  that pair with a prior press.
- `flight_ms`: milliseconds between the previous release and this press.
  Filled on press rows.
- `dx`, `dy`: raw mouse motion counts.
- `value`: axis value for gamepads, wheel delta for mouse scroll.

Empty fields indicate the column does not apply to that row.

### Events and controls

Keyboard:

- `event` is `press` or `release`. The specific key is never recorded.
  `control` is always `key`. `hold_ms` and `flight_ms` carry the timing.
- Auto-repeat presses are suppressed.

Mouse:

- `event = move` with `dx`, `dy`.
- `event = press` or `release`, with `control` in `button_left`,
  `button_right`, `button_middle`, `button_x1`, `button_x2`.
- `event = scroll`, with `control` in `wheel_v`, `wheel_h`, and `value` as
  the wheel delta.

Gamepad and other HID devices (controllers, keypads, media keys, touch pads,
touch screens, pens; `io_type` names the kind):

- `event = press` or `release`. `control` is `button_NN` for buttons on the
  standard HID Button usage page.
- `event = axis`. `control` is one of `axis_x`, `axis_y`, `axis_z`,
  `axis_rx`, `axis_ry`, `axis_rz`, `slider`, `dial`, `wheel`, `dpad`, or
  `value_<page>_<usage>` for non-standard usages. `value` holds the raw
  HID value.
- Axis events fire only on value change.

## Connecting to the website

The tray menu's first line shows the account connection, much like a VPN
client: Not connected, Connecting, Connected (enrolling or monitoring),
Offline, or Disconnected.

1. **Pairing.** On the website, sign in, open Setup, and create a pairing
   code. In the tray choose **Connect to account...**, enter the website
   address and the 8-character code.
   - The code is checked against the server's alphabet before it is sent,
     then redeemed at `POST /api/agent/pair`.
   - The device token the website returns is saved in
     `%APPDATA%\inquisitor\device.bin`, encrypted with DPAPI for the current
     Windows user. It is never written in plaintext.
   - The website address is kept in `config.ini` as `server_url`.
2. **Presence.** The tray keeps an outbound WebSocket at `/api/agent/ws`.
   - It sends `hello` and a `heartbeat` every 30 seconds with the pause state,
     so the website shows Connected, Paused, or Offline.
   - The website answers with what to collect: enrollment for an active game
     round, monitoring once a model is trained, otherwise nothing.
   - The message table is in [`../../docs/threat-model.md`](../../docs/threat-model.md).
3. **Uploads.** Finished 30-second windows from allowed devices go to
   `POST /api/agent/windows` over HTTPS.
   - They are computed live by `src/features.rs`, the same calculation as
     `backend/ml/tray_csv.py`. Both are tested against
     `shared/fixtures/tray-capture.*`.
   - Every window is schema v4: keyboard and mouse aggregates together, the
     only kind the website accepts.
   - Nothing is collected while paused, and nothing is sent before the terms
     are accepted and the PC is paired.
4. **Held until sent.** Every finished window is held on disk in
   `%APPDATA%\inquisitor\outbox.jsonl` until it is sent: while offline,
   across restarts, and before the PC is paired. Nothing expires; the website
   accepts agent windows of any age, and duplicates are still refused.
   - A window made during an enrollment round is sent to that round. The
     website refuses it if the round has already ended.
   - Every other window, including those made while unpaired, offline before
     the first status, or while the website was not collecting, is sent for
     monitoring once a model is trained.
   - The menu shows how many are held. The file holds the same aggregate
     numbers that are uploaded; it is capped at 100,000 windows (about a
     month of non-stop activity), dropping the oldest beyond that.
   - Uploads and reconnects back off from 1 second to 1 minute, with jitter.
5. **Fail closed.** A 401 or 403, or a `revoked` message, stops uploads,
   deletes the held windows, and deletes the stored credential.
   - **Disconnect from account** does the same locally. The device stays on
     the website's device list, shown offline, until it is revoked there.
   - Removing the app also deletes the credential and the held windows.

Without an account the tray works fully offline: it captures to the CSV files
in the data folder (**Open data location** in the menu), which are never
uploaded, and holds summaries in case a PC is connected later.

Transport is WinHTTP, the HTTP stack built into Windows, so TLS uses the
Windows certificate store and system proxy settings, and certificate checks
are never disabled. `http://` is accepted only for localhost, for testing.

Two end-to-end tests run against a local hosted-mode server and are skipped by
default. Start [`../../scripts/agent_link_test_server.py`](../../scripts/agent_link_test_server.py)
(in-memory database, test only; its docstring has the command), then:

```sh
INQUISITOR_TEST_SERVER=http://127.0.0.1:5077 cargo test -- --ignored live_
```

Each pairs with the server's single-use code, so restart the server between the
two tests.

## Privacy and scope

This client follows the rules in [`../AGENTS.md`](../AGENTS.md):

- Authentication v1 uses keyboard and mouse aggregates, sent together as
  schema v4 and covered by the setup terms. Controller and other HID capture
  stays in the local CSV and does not expand that scope.
- Keyboards emit press and release with timing only. The key identity,
  typed content, and ordered event stream are not persisted, logged, or
  transmitted. The scan code is used transiently in memory to pair press
  and release events for one key and is never written to the CSV.
- Only aggregate numeric features may enter model requests or telemetry.
  The raw CSV in `%APPDATA%\inquisitor\` is development data. Any
  component that ships data off the PC must aggregate it first.

## Known limits

- Default output is `%APPDATA%\inquisitor\`. Falls back to the current
  directory if `APPDATA` is unset.
- Each run creates a fresh file. Appending to a rolling log is not wired up.
- Gamepad decoding uses the standard HID parser (`HidP_*`). Devices with
  proprietary protocols, including Nintendo Switch controllers in their
  native mode, publish only vendor-defined values and will not decode into
  named controls. Running them through Steam Input or an equivalent
  translation layer makes them look like a standard HID gamepad.
- Hot-plug is not handled explicitly during capture. Devices plugged in
  after capture starts are picked up when they next send input; removed
  devices are not noted.
- The capture singleton uses a module-level `static mut`. Safe because the
  window procedure runs on the thread that initialises it, but it should
  move to a thread-local or `GWLP_USERDATA` for a cleaner model later.