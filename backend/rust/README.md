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

## Usage

```text
inquisitor                        List connected input devices
inquisitor capture                Capture to %APPDATA%\inquisitor\capture-<ts>.csv
inquisitor capture --out <path>   Capture to <path>
inquisitor capture --out -        Capture to stdout
inquisitor --help                 Print usage
```

`capture` prints the resolved output path to stderr and then runs until
stopped (Ctrl+C or killed). It creates the output directory if needed.

## Device listing

`inquisitor` with no arguments enumerates every device Windows reports
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

## Privacy and scope

This client follows the rules in [`../AGENTS.md`](../AGENTS.md):

- Authentication v1 is keyboard-only. Mouse and controller capture support
  the arcade in [`../../Frontend/`](../../Frontend/) and do not expand that
  scope.
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