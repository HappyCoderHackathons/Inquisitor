//! Raw-input capture.
//!
//! Registers for keyboard, mouse and generic HID (joystick/gamepad) raw input and
//! emits CSV rows describing each event. The tray front end drives this via
//! `Capture::process_wm_input`; a standalone message pump is available through
//! `run()` for the CLI `capture` subcommand.
//!
//! Privacy: for keyboards we never emit the key identity or any ordered character
//! stream. Only press/release events with timing are recorded. See AGENTS.md.

use std::collections::{HashMap, HashSet};
use std::ffi::c_void;
use std::fs;
use std::io::{self, Write};
use std::mem::size_of;
use std::path::PathBuf;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use windows::core::{PCWSTR, w};
use windows::Win32::Devices::HumanInterfaceDevice::{
    HID_USAGE_GENERIC_GAMEPAD, HID_USAGE_GENERIC_JOYSTICK, HID_USAGE_GENERIC_KEYBOARD,
    HID_USAGE_GENERIC_MOUSE, HID_USAGE_GENERIC_MULTI_AXIS_CONTROLLER, HID_USAGE_PAGE_GENERIC,
    HIDP_BUTTON_CAPS, HIDP_CAPS, HIDP_STATUS_SUCCESS, HIDP_VALUE_CAPS, HidP_GetButtonCaps,
    HidP_GetCaps, HidP_GetUsageValue, HidP_GetUsages, HidP_GetValueCaps, HidP_Input,
    PHIDP_PREPARSED_DATA,
};
use windows::Win32::Foundation::{HANDLE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::{
    GetRawInputData, GetRawInputDeviceInfoW, RAWINPUT, RAWINPUTDEVICE, RAWINPUTHEADER, RAWKEYBOARD,
    RAWMOUSE, RID_INPUT, RIDEV_INPUTSINK, RIDEV_PAGEONLY, RIDI_DEVICENAME, RIDI_PREPARSEDDATA,
    RegisterRawInputDevices,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, HWND_MESSAGE, MSG,
    RegisterClassExW, TranslateMessage, WINDOW_EX_STYLE, WINDOW_STYLE, WM_INPUT, WNDCLASSEXW,
};

// Mouse flag constants (not re-exported by the windows crate at every path).
const RI_MOUSE_LEFT_BUTTON_DOWN: u16 = 0x0001;
const RI_MOUSE_LEFT_BUTTON_UP: u16 = 0x0002;
const RI_MOUSE_RIGHT_BUTTON_DOWN: u16 = 0x0004;
const RI_MOUSE_RIGHT_BUTTON_UP: u16 = 0x0008;
const RI_MOUSE_MIDDLE_BUTTON_DOWN: u16 = 0x0010;
const RI_MOUSE_MIDDLE_BUTTON_UP: u16 = 0x0020;
const RI_MOUSE_BUTTON_4_DOWN: u16 = 0x0040;
const RI_MOUSE_BUTTON_4_UP: u16 = 0x0080;
const RI_MOUSE_BUTTON_5_DOWN: u16 = 0x0100;
const RI_MOUSE_BUTTON_5_UP: u16 = 0x0200;
const RI_MOUSE_WHEEL: u16 = 0x0400;
const RI_MOUSE_HWHEEL: u16 = 0x0800;

// Keyboard flags.
const RI_KEY_BREAK: u16 = 0x01; // key up

/// Per-device bookkeeping so we can compute hold_ms (press→release) and
/// flight_ms (previous release→this press).
#[derive(Default)]
struct DeviceState {
    /// key control (opaque name) -> timestamp of last press
    press_times: HashMap<String, Instant>,
    /// timestamp of the most recent release on this device
    last_release: Option<Instant>,
}

/// Cached HID descriptor info per device.
struct HidCaps {
    data: Vec<u8>,
    button_caps: Vec<HIDP_BUTTON_CAPS>,
    value_caps: Vec<HIDP_VALUE_CAPS>,
    /// CSV `io_type` for this device, derived from its top-level HID usage.
    io_type: &'static str,
}

/// CSV `io_type` for a HID device's top-level usage. Controllers share one label;
/// digitizers and media keys get their own so rows are easy to filter.
fn hid_io_type(usage_page: u16, usage: u16) -> &'static str {
    match (usage_page, usage) {
        (0x01, 0x04 | 0x05 | 0x08) => "gamepad",
        (0x01, 0x07) => "keypad",
        (0x0C, _) => "consumer",
        (0x0D, 0x02) => "pen",
        (0x0D, 0x04) => "touchscreen",
        (0x0D, 0x05) => "touchpad",
        (0x0D, _) => "digitizer",
        _ => "hid",
    }
}

/// Previous HID state used for change detection.
#[derive(Default)]
struct HidPrev {
    buttons: HashSet<(u16, u16)>,
    values: HashMap<(u16, u16), u32>,
}

/// One decoded event from a HID report.
enum HidEvent {
    ButtonPress(String),
    ButtonRelease(String),
    Value { control: String, value: i32 },
}

/// Where capture output goes.
pub enum CaptureOutput {
    /// %APPDATA%\inquisitor\capture-<utc timestamp>.csv.
    DefaultFile,
    /// Explicit file path.
    File(PathBuf),
    /// Standard output.
    Stdout,
}

/// Columns: iso_time,monotonic_s,io_type,device_id,event,control,hold_ms,flight_ms,dx,dy,value
const CSV_HEADER: &str =
    "iso_time,monotonic_s,io_type,device_id,event,control,hold_ms,flight_ms,dx,dy,value";

pub struct Capture {
    start: Instant,
    /// Maps raw HANDLE values to stable short ids like "kbd0", "mouse0", "hid0".
    device_ids: HashMap<isize, String>,
    /// Cached system device path per HANDLE, used for filtering by the tray.
    device_paths: HashMap<isize, String>,
    next_kbd: u32,
    next_mouse: u32,
    next_hid: u32,
    state: HashMap<String, DeviceState>,
    hid_caps: HashMap<isize, HidCaps>,
    hid_prev: HashMap<isize, HidPrev>,
    writer: io::BufWriter<Box<dyn Write>>,
    /// Instance-level header flag so swapping the writer re-emits the header.
    header_written: bool,
}

impl Capture {
    pub fn new(writer: Box<dyn Write>) -> Self {
        Self {
            start: Instant::now(),
            device_ids: HashMap::new(),
            device_paths: HashMap::new(),
            next_kbd: 0,
            next_mouse: 0,
            next_hid: 0,
            state: HashMap::new(),
            hid_caps: HashMap::new(),
            hid_prev: HashMap::new(),
            writer: io::BufWriter::new(writer),
            header_written: false,
        }
    }

    /// Replace the output writer. The next event row will be preceded by a fresh
    /// CSV header, so swapping files mid-session produces a self-describing output.
    pub fn set_writer(&mut self, writer: Box<dyn Write>) {
        let _ = self.writer.flush();
        self.writer = io::BufWriter::new(writer);
        self.header_written = false;
    }

    /// Return the cached system device path for a HANDLE, reading it from Windows on
    /// first use. Returns an empty string if the lookup fails.
    pub fn device_path(&mut self, handle: HANDLE) -> String {
        let key = handle.0 as isize;
        if let Some(p) = self.device_paths.get(&key) {
            return p.clone();
        }
        let path = read_device_path(handle).unwrap_or_default();
        self.device_paths.insert(key, path.clone());
        path
    }

    /// Process a `WM_INPUT` message. `filter` is called once with the device path;
    /// when it returns false the event is dropped without emitting a row.
    pub fn process_wm_input<F: FnOnce(&str) -> bool>(&mut self, lparam: LPARAM, filter: F) {
        let handle = windows::Win32::UI::Input::HRAWINPUT(lparam.0 as *mut c_void);
        let mut size = 0u32;
        let hdr_size = size_of::<RAWINPUTHEADER>() as u32;
        unsafe {
            GetRawInputData(handle, RID_INPUT, None, &mut size, hdr_size);
        }
        if size == 0 {
            return;
        }
        let mut buf = vec![0u8; size as usize];
        let read = unsafe {
            GetRawInputData(
                handle,
                RID_INPUT,
                Some(buf.as_mut_ptr().cast()),
                &mut size,
                hdr_size,
            )
        };
        if read == u32::MAX || (read as usize) < size_of::<RAWINPUTHEADER>() {
            return;
        }
        let raw = unsafe { &*(buf.as_ptr() as *const RAWINPUT) };
        let dev = raw.header.hDevice;

        // Caller decides whether this device is currently allowed to produce rows.
        let path = self.device_path(dev);
        if !filter(&path) {
            return;
        }

        match raw.header.dwType {
            0 => {
                // RIM_TYPEMOUSE
                let m = unsafe { &raw.data.mouse };
                self.handle_mouse(dev, m);
            }
            1 => {
                // RIM_TYPEKEYBOARD
                let k = unsafe { &raw.data.keyboard };
                self.handle_keyboard(dev, k);
            }
            2 => {
                // RIM_TYPEHID
                self.handle_hid(dev, raw);
            }
            _ => {}
        }
    }

    fn device_id(&mut self, handle: HANDLE, prefix: &str) -> String {
        let key = handle.0 as isize;
        if let Some(id) = self.device_ids.get(&key) {
            return id.clone();
        }
        let n = match prefix {
            "kbd" => {
                let n = self.next_kbd;
                self.next_kbd += 1;
                n
            }
            "mouse" => {
                let n = self.next_mouse;
                self.next_mouse += 1;
                n
            }
            _ => {
                let n = self.next_hid;
                self.next_hid += 1;
                n
            }
        };
        let id = format!("{prefix}{n}");
        self.device_ids.insert(key, id.clone());
        id
    }

    fn now_iso(&self) -> String {
        // RFC3339-ish without a date crate: seconds since epoch with millis.
        let d = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default();
        let secs = d.as_secs();
        let millis = d.subsec_millis();
        // Compute Y-M-D h:m:s in UTC using a small civil-from-days calc.
        let days = (secs / 86_400) as i64;
        let tod = secs % 86_400;
        let (y, m, d2) = civil_from_days(days);
        let h = tod / 3600;
        let mi = (tod % 3600) / 60;
        let s = tod % 60;
        format!("{y:04}-{m:02}-{d2:02}T{h:02}:{mi:02}:{s:02}.{millis:03}Z")
    }

    fn write_row(
        &mut self,
        io_type: &str,
        device_id: &str,
        event: &str,
        control: &str,
        hold_ms: Option<u128>,
        flight_ms: Option<u128>,
        dx: Option<i32>,
        dy: Option<i32>,
        value: Option<i32>,
    ) {
        if !self.header_written {
            let _ = writeln!(self.writer, "{CSV_HEADER}");
            self.header_written = true;
        }
        let iso = self.now_iso();
        let mono = self.start.elapsed().as_secs_f64();
        let hold = hold_ms.map(|v| v.to_string()).unwrap_or_default();
        let flight = flight_ms.map(|v| v.to_string()).unwrap_or_default();
        let dx = dx.map(|v| v.to_string()).unwrap_or_default();
        let dy = dy.map(|v| v.to_string()).unwrap_or_default();
        let value = value.map(|v| v.to_string()).unwrap_or_default();
        let _ = writeln!(
            self.writer,
            "{iso},{mono:.6},{io_type},{device_id},{event},{control},{hold},{flight},{dx},{dy},{value}"
        );
        let _ = self.writer.flush();
    }

    fn handle_keyboard(&mut self, handle: HANDLE, kb: &RAWKEYBOARD) {
        let device_id = self.device_id(handle, "kbd");
        let is_up = kb.Flags & RI_KEY_BREAK != 0;
        let event = if is_up { "release" } else { "press" };
        // Opaque key id: we use the scan code for pairing press/release only.
        // This identifies which physical key, but the AGENTS.md rule forbids
        // emitting the key identity, so we emit a stable per-scancode token
        // without naming it. The scancode never leaves this function.
        let scan = kb.MakeCode as u32;
        let control = "key"; // generic label; press/release is in `event`.
        let key_token = format!("sc_{scan:04X}");

        let now = Instant::now();
        let state = self.state.entry(device_id.clone()).or_default();

        let (hold_ms, flight_ms) = if is_up {
            let hold = state
                .press_times
                .remove(&key_token)
                .map(|t| now.duration_since(t).as_millis());
            state.last_release = Some(now);
            (hold, None)
        } else {
            // Ignore auto-repeat: if the key is already down, skip the press row.
            if state.press_times.contains_key(&key_token) {
                return;
            }
            let flight = state
                .last_release
                .map(|t| now.duration_since(t).as_millis());
            state.press_times.insert(key_token.clone(), now);
            (None, flight)
        };

        self.write_row(
            "keyboard",
            &device_id.clone(),
            event,
            control,
            hold_ms,
            flight_ms,
            None,
            None,
            None,
        );
    }

    fn handle_mouse(&mut self, handle: HANDLE, m: &RAWMOUSE) {
        let device_id = self.device_id(handle, "mouse");
        let dx = m.lLastX;
        let dy = m.lLastY;

        if dx != 0 || dy != 0 {
            self.write_row(
                "mouse",
                &device_id.clone(),
                "move",
                "motion",
                None,
                None,
                Some(dx),
                Some(dy),
                None,
            );
        }

        let flags = unsafe { m.Anonymous.Anonymous.usButtonFlags };
        let data = unsafe { m.Anonymous.Anonymous.usButtonData } as i16 as i32;

        let button_events: &[(u16, &str, &str)] = &[
            (RI_MOUSE_LEFT_BUTTON_DOWN, "press", "button_left"),
            (RI_MOUSE_LEFT_BUTTON_UP, "release", "button_left"),
            (RI_MOUSE_RIGHT_BUTTON_DOWN, "press", "button_right"),
            (RI_MOUSE_RIGHT_BUTTON_UP, "release", "button_right"),
            (RI_MOUSE_MIDDLE_BUTTON_DOWN, "press", "button_middle"),
            (RI_MOUSE_MIDDLE_BUTTON_UP, "release", "button_middle"),
            (RI_MOUSE_BUTTON_4_DOWN, "press", "button_x1"),
            (RI_MOUSE_BUTTON_4_UP, "release", "button_x1"),
            (RI_MOUSE_BUTTON_5_DOWN, "press", "button_x2"),
            (RI_MOUSE_BUTTON_5_UP, "release", "button_x2"),
        ];
        for &(bit, ev, ctl) in button_events {
            if flags & bit != 0 {
                self.handle_mouse_button(&device_id, ev, ctl);
            }
        }

        if flags & RI_MOUSE_WHEEL != 0 {
            self.write_row(
                "mouse",
                &device_id.clone(),
                "scroll",
                "wheel_v",
                None,
                None,
                None,
                None,
                Some(data),
            );
        }
        if flags & RI_MOUSE_HWHEEL != 0 {
            self.write_row(
                "mouse",
                &device_id.clone(),
                "scroll",
                "wheel_h",
                None,
                None,
                None,
                None,
                Some(data),
            );
        }
    }

    fn handle_mouse_button(&mut self, device_id: &str, event: &str, control: &str) {
        let now = Instant::now();
        let state = self.state.entry(device_id.to_string()).or_default();
        let (hold_ms, flight_ms) = if event == "release" {
            let hold = state
                .press_times
                .remove(control)
                .map(|t| now.duration_since(t).as_millis());
            state.last_release = Some(now);
            (hold, None)
        } else {
            let flight = state
                .last_release
                .map(|t| now.duration_since(t).as_millis());
            state.press_times.insert(control.to_string(), now);
            (None, flight)
        };
        self.write_row(
            "mouse",
            &device_id.to_string(),
            event,
            control,
            hold_ms,
            flight_ms,
            None,
            None,
            None,
        );
    }

    fn handle_hid(&mut self, handle: HANDLE, raw: &RAWINPUT) {
        let device_id = self.device_id(handle, "hid");
        let hid = unsafe { &raw.data.hid };
        let size = hid.dwSizeHid as usize;
        let count = hid.dwCount as usize;
        if size == 0 || count == 0 {
            return;
        }

        let key = handle.0 as isize;
        if !self.hid_caps.contains_key(&key)
            && let Some(c) = load_hid_caps(handle)
        {
            self.hid_caps.insert(key, c);
            self.hid_prev.insert(key, HidPrev::default());
        }
        if !self.hid_caps.contains_key(&key) {
            return;
        }

        let io_type = self.hid_caps[&key].io_type;
        let base = hid.bRawData.as_ptr() as *mut u8;
        for i in 0..count {
            let report = unsafe { std::slice::from_raw_parts_mut(base.add(i * size), size) };
            let events = {
                let caps = self.hid_caps.get(&key).unwrap();
                let prev = self.hid_prev.get_mut(&key).unwrap();
                collect_hid_events(caps, prev, report)
            };
            for ev in events {
                self.emit_hid_event(io_type, &device_id, ev);
            }
        }
    }

    fn emit_hid_event(&mut self, io_type: &str, device_id: &str, ev: HidEvent) {
        let now = Instant::now();
        match ev {
            HidEvent::ButtonPress(control) => {
                let state = self.state.entry(device_id.to_string()).or_default();
                let flight = state.last_release.map(|t| now.duration_since(t).as_millis());
                state.press_times.insert(control.clone(), now);
                self.write_row(
                    io_type, device_id, "press", &control, None, flight, None, None, None,
                );
            }
            HidEvent::ButtonRelease(control) => {
                let state = self.state.entry(device_id.to_string()).or_default();
                let hold = state
                    .press_times
                    .remove(&control)
                    .map(|t| now.duration_since(t).as_millis());
                state.last_release = Some(now);
                self.write_row(
                    io_type, device_id, "release", &control, hold, None, None, None, None,
                );
            }
            HidEvent::Value { control, value } => {
                self.write_row(
                    io_type,
                    device_id,
                    "axis",
                    &control,
                    None,
                    None,
                    None,
                    None,
                    Some(value),
                );
            }
        }
    }
}

fn load_hid_caps(handle: HANDLE) -> Option<HidCaps> {
    let mut size = 0u32;
    unsafe {
        GetRawInputDeviceInfoW(Some(handle), RIDI_PREPARSEDDATA, None, &mut size);
    }
    if size == 0 {
        return None;
    }
    let mut data = vec![0u8; size as usize];
    let written = unsafe {
        GetRawInputDeviceInfoW(
            Some(handle),
            RIDI_PREPARSEDDATA,
            Some(data.as_mut_ptr().cast()),
            &mut size,
        )
    };
    if written == u32::MAX {
        return None;
    }
    let prep = PHIDP_PREPARSED_DATA(data.as_mut_ptr() as isize);

    let mut caps = HIDP_CAPS::default();
    let status = unsafe { HidP_GetCaps(prep, &mut caps) };
    if status != HIDP_STATUS_SUCCESS {
        return None;
    }

    let mut button_caps = Vec::new();
    if caps.NumberInputButtonCaps > 0 {
        button_caps.resize(caps.NumberInputButtonCaps as usize, HIDP_BUTTON_CAPS::default());
        let mut len = caps.NumberInputButtonCaps;
        let _ =
            unsafe { HidP_GetButtonCaps(HidP_Input, button_caps.as_mut_ptr(), &mut len, prep) };
    }

    let mut value_caps = Vec::new();
    if caps.NumberInputValueCaps > 0 {
        value_caps.resize(caps.NumberInputValueCaps as usize, HIDP_VALUE_CAPS::default());
        let mut len = caps.NumberInputValueCaps;
        let _ = unsafe { HidP_GetValueCaps(HidP_Input, value_caps.as_mut_ptr(), &mut len, prep) };
    }

    Some(HidCaps {
        data,
        button_caps,
        value_caps,
        io_type: hid_io_type(caps.UsagePage, caps.Usage),
    })
}

fn collect_hid_events(caps: &HidCaps, prev: &mut HidPrev, report: &mut [u8]) -> Vec<HidEvent> {
    let mut events = Vec::new();
    let prep = PHIDP_PREPARSED_DATA(caps.data.as_ptr() as isize);

    // Buttons: for each button cap, ask HidP for the usages currently set.
    let mut current: HashSet<(u16, u16)> = HashSet::new();
    for bc in &caps.button_caps {
        let usage_page = bc.UsagePage;
        let (umin, umax) = if bc.IsRange {
            let r = unsafe { bc.Anonymous.Range };
            (r.UsageMin, r.UsageMax)
        } else {
            let nr = unsafe { bc.Anonymous.NotRange };
            (nr.Usage, nr.Usage)
        };
        let cap = (umax.saturating_sub(umin) as usize + 1).max(1);
        let mut list = vec![0u16; cap];
        let mut len = cap as u32;
        let status = unsafe {
            HidP_GetUsages(
                HidP_Input,
                usage_page,
                None,
                list.as_mut_ptr(),
                &mut len,
                prep,
                &mut *report,
            )
        };
        if status == HIDP_STATUS_SUCCESS {
            for &u in &list[..len as usize] {
                current.insert((usage_page, u));
            }
        }
    }

    let new_presses: Vec<_> = current.difference(&prev.buttons).copied().collect();
    let new_releases: Vec<_> = prev.buttons.difference(&current).copied().collect();
    for b in new_presses {
        events.push(HidEvent::ButtonPress(button_name(b)));
    }
    for b in new_releases {
        events.push(HidEvent::ButtonRelease(button_name(b)));
    }
    prev.buttons = current;

    // Values: axes, sticks, triggers, hat.
    for vc in &caps.value_caps {
        let usage_page = vc.UsagePage;
        let (umin, umax) = if vc.IsRange {
            let r = unsafe { vc.Anonymous.Range };
            (r.UsageMin, r.UsageMax)
        } else {
            let nr = unsafe { vc.Anonymous.NotRange };
            (nr.Usage, nr.Usage)
        };
        for usage in umin..=umax {
            let mut value = 0u32;
            let status = unsafe {
                HidP_GetUsageValue(
                    HidP_Input,
                    usage_page,
                    None,
                    usage,
                    &mut value,
                    prep,
                    &*report,
                )
            };
            if status == HIDP_STATUS_SUCCESS {
                let key = (usage_page, usage);
                if prev.values.get(&key).copied() != Some(value) {
                    prev.values.insert(key, value);
                    events.push(HidEvent::Value {
                        control: value_name(usage_page, usage),
                        value: value as i32,
                    });
                }
            }
        }
    }

    events
}

fn button_name((page, usage): (u16, u16)) -> String {
    if page == 0x09 {
        format!("button_{usage:02}")
    } else {
        format!("button_{page:04x}_{usage:02x}")
    }
}

fn value_name(page: u16, usage: u16) -> String {
    match (page, usage) {
        (0x01, 0x30) => "axis_x".to_string(),
        (0x01, 0x31) => "axis_y".to_string(),
        (0x01, 0x32) => "axis_z".to_string(),
        (0x01, 0x33) => "axis_rx".to_string(),
        (0x01, 0x34) => "axis_ry".to_string(),
        (0x01, 0x35) => "axis_rz".to_string(),
        (0x01, 0x36) => "slider".to_string(),
        (0x01, 0x37) => "dial".to_string(),
        (0x01, 0x38) => "wheel".to_string(),
        (0x01, 0x39) => "dpad".to_string(),
        _ => format!("value_{page:04x}_{usage:02x}"),
    }
}

/// Read a device's system path by HANDLE. Public so the tray can snapshot
/// current device paths at startup.
pub fn read_device_path(handle: HANDLE) -> Option<String> {
    let mut len = 0u32;
    unsafe {
        GetRawInputDeviceInfoW(Some(handle), RIDI_DEVICENAME, None, &mut len);
    }
    if len == 0 {
        return None;
    }
    let mut buffer = vec![0u16; len as usize];
    let copied = unsafe {
        GetRawInputDeviceInfoW(
            Some(handle),
            RIDI_DEVICENAME,
            Some(buffer.as_mut_ptr().cast()),
            &mut len,
        )
    };
    if copied == u32::MAX {
        return None;
    }
    let end = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
    Some(String::from_utf16_lossy(&buffer[..end]))
}

/// Subscribe the given window to raw input from every kind of user-input device:
/// keyboards, keypads, mice, controllers (gamepad, joystick, multi-axis), media
/// keys (whole consumer page) and digitizers (whole page: touch pads, touch
/// screens, pens). `RIDEV_INPUTSINK` lets the hidden window receive events
/// without focus.
///
/// Digitizers matter because precision touch pads and pens move the pointer as a
/// synthesized mouse with no device handle; only their own digitizer reports
/// identify the physical device. Subscribing does not record anything by
/// itself: the tray drops events from devices that are not whitelisted.
pub fn register_for_raw_input(hwnd: HWND) -> windows::core::Result<()> {
    const HID_USAGE_GENERIC_KEYPAD: u16 = 0x07;
    const HID_USAGE_PAGE_CONSUMER: u16 = 0x0C;
    const HID_USAGE_PAGE_DIGITIZER: u16 = 0x0D;
    let devices = [
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_KEYPAD,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
        // RIDEV_PAGEONLY subscribes every top-level collection on the page; it
        // requires usUsage = 0.
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_CONSUMER,
            usUsage: 0,
            dwFlags: RIDEV_INPUTSINK | RIDEV_PAGEONLY,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_DIGITIZER,
            usUsage: 0,
            dwFlags: RIDEV_INPUTSINK | RIDEV_PAGEONLY,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_KEYBOARD,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_MOUSE,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_GAMEPAD,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_JOYSTICK,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
        RAWINPUTDEVICE {
            usUsagePage: HID_USAGE_PAGE_GENERIC,
            usUsage: HID_USAGE_GENERIC_MULTI_AXIS_CONTROLLER,
            dwFlags: RIDEV_INPUTSINK,
            hwndTarget: hwnd,
        },
    ];
    unsafe { RegisterRawInputDevices(&devices, size_of::<RAWINPUTDEVICE>() as u32) }
}

fn open_output(output: CaptureOutput) -> (Box<dyn Write>, String) {
    match output {
        CaptureOutput::Stdout => (Box::new(io::stdout()), "<stdout>".to_string()),
        CaptureOutput::DefaultFile => {
            let path = default_output_path(&default_data_dir());
            let w = open_file(&path);
            (w, path.display().to_string())
        }
        CaptureOutput::File(path) => {
            if let Some(parent) = path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let w = open_file(&path);
            (w, path.display().to_string())
        }
    }
}

fn open_file(path: &std::path::Path) -> Box<dyn Write> {
    match fs::File::create(path) {
        Ok(f) => Box::new(f),
        Err(e) => {
            eprintln!("failed to open {}: {e}", path.display());
            std::process::exit(1);
        }
    }
}

/// `<data_dir>\capture-<utc timestamp>.csv`, creating the directory if needed.
pub fn default_output_path(data_dir: &std::path::Path) -> PathBuf {
    let _ = fs::create_dir_all(data_dir);
    data_dir.join(format!("capture-{}.csv", utc_timestamp_compact()))
}

/// `%APPDATA%\inquisitor`, or the current directory if APPDATA is unset.
pub fn default_data_dir() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("inquisitor")
}

/// Open a fresh CSV file inside `data_dir`, returning the open writer and the chosen path.
/// Used by the tray when it starts capture or switches data directories.
pub fn open_timestamped_file(data_dir: &std::path::Path) -> (Box<dyn Write>, PathBuf) {
    let path = default_output_path(data_dir);
    let w = open_file(&path);
    (w, path)
}

fn utc_timestamp_compact() -> String {
    let d = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = d.as_secs();
    let days = (secs / 86_400) as i64;
    let tod = secs % 86_400;
    let (y, m, d2) = civil_from_days(days);
    let h = tod / 3600;
    let mi = (tod % 3600) / 60;
    let s = tod % 60;
    format!("{y:04}{m:02}{d2:02}-{h:02}{mi:02}{s:02}Z")
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    // Howard Hinnant's civil_from_days (UTC).
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

// --- CLI capture subcommand -------------------------------------------------
//
// The `inquisitor capture` subcommand runs its own hidden window and message loop.
// It uses a single global Capture with no filter and no pause; the tray has its
// own window and its own Capture instance.

static mut CLI_CAPTURE: Option<Capture> = None;

fn cli_capture_ptr() -> *mut Option<Capture> {
    &raw mut CLI_CAPTURE
}

/// Start the CLI capture loop on this thread. Blocks until the message loop exits.
pub fn run(output: CaptureOutput) -> windows::core::Result<()> {
    let (writer, path_desc) = open_output(output);
    unsafe {
        *cli_capture_ptr() = Some(Capture::new(writer));
    }

    let hinstance = unsafe { GetModuleHandleW(PCWSTR::null())? };
    let class_name = w!("InquisitorCliCaptureWnd");

    let wc = WNDCLASSEXW {
        cbSize: size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: Some(cli_wnd_proc),
        hInstance: hinstance.into(),
        lpszClassName: class_name,
        ..Default::default()
    };
    unsafe { RegisterClassExW(&wc) };

    let hwnd = unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            class_name,
            w!("inquisitor"),
            WINDOW_STYLE(0),
            0,
            0,
            0,
            0,
            Some(HWND_MESSAGE),
            None,
            Some(hinstance.into()),
            None,
        )?
    };

    register_for_raw_input(hwnd)?;

    eprintln!("Capturing raw input to {path_desc}. Ctrl+C to stop.");

    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}

extern "system" fn cli_wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if msg == WM_INPUT
        && let Some(cap) = unsafe { (*cli_capture_ptr()).as_mut() }
    {
        // CLI mode runs with no filter and no pause.
        cap.process_wm_input(lparam, |_| true);
    }
    unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
}
