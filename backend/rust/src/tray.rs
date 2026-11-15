//! System-tray front end for Inquisitor.
//!
//! Hidden message-only window hosts both the Raw Input subscription and the
//! `Shell_NotifyIcon` tray icon. A single wnd_proc dispatches input, tray and
//! menu messages against an `AppState` stored in the window's `GWLP_USERDATA`.

use std::mem::size_of;
use std::path::PathBuf;
use std::time::Instant;

use windows::core::{PCWSTR, w};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW, Shell_NotifyIconW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DispatchMessageW,
    GWLP_USERDATA, GetCursorPos, GetMessageW, GetWindowLongPtrW, HMENU, HWND_MESSAGE, IDI_APPLICATION,
    LoadIconW, MF_CHECKED, MF_POPUP, MF_SEPARATOR, MF_STRING, MF_UNCHECKED, MSG, PostMessageW,
    PostQuitMessage, RegisterClassExW, SetForegroundWindow, SetWindowLongPtrW, TPM_BOTTOMALIGN,
    TPM_LEFTALIGN, TPM_RIGHTBUTTON, TrackPopupMenu, TranslateMessage, WINDOW_EX_STYLE,
    WINDOW_STYLE, WM_APP, WM_COMMAND, WM_CONTEXTMENU, WM_DESTROY, WM_INPUT, WM_NULL,
    WM_RBUTTONUP, WNDCLASSEXW,
};

use crate::capture::{self, Capture};
use crate::config::Config;
use crate::folder_picker;
use crate::startup;

// Tray notification callback. WM_APP + N is the recommended range for app-defined
// messages per MSDN (WM_USER is reserved for controls).
const WM_TRAY: u32 = WM_APP + 1;

// Menu command IDs. Low 16 bits only; WM_COMMAND packs the id into wparam.lo.
const ID_PAUSE_RESUME: u16 = 10;
const ID_PAUSE_5M: u16 = 11;
const ID_PAUSE_15M: u16 = 12;
const ID_PAUSE_1H: u16 = 13;
const ID_OPEN_DATA: u16 = 20;
const ID_SET_DATA: u16 = 21;
const ID_TOGGLE_ALLOW_NEW: u16 = 30;
const ID_TOGGLE_STARTUP: u16 = 40;
const ID_QUIT: u16 = 50;
// Devices in the "Ignore devices" submenu are IDs IGNORE_BASE..IGNORE_BASE+N.
const IGNORE_BASE: u16 = 1000;

/// What the menu opens over: a label the user recognises plus the underlying path.
struct DeviceEntry {
    path: String,
    label: String,
}

struct AppState {
    config: Config,
    capture: Capture,
    current_path: PathBuf,
    /// If Some, capture is paused until this instant (auto-resume on first event after).
    /// If None and `paused`, capture is paused indefinitely.
    paused: bool,
    pause_until: Option<Instant>,
    /// Snapshot built fresh each time the menu opens; index matches IGNORE_BASE + i.
    connected_devices: Vec<DeviceEntry>,
    startup_enabled: bool,
}

impl AppState {
    fn new(config: Config, capture: Capture, current_path: PathBuf) -> Self {
        let startup_enabled = startup::is_enabled();
        Self {
            config,
            capture,
            current_path,
            paused: false,
            pause_until: None,
            connected_devices: Vec::new(),
            startup_enabled,
        }
    }

    /// Returns true if the event should be dropped. Auto-clears a timed pause once
    /// the deadline has passed.
    fn drop_event(&mut self, path: &str) -> bool {
        if let Some(until) = self.pause_until
            && Instant::now() >= until
        {
            self.paused = false;
            self.pause_until = None;
        }
        if self.paused {
            return true;
        }
        if self.config.ignored_devices.contains(path) {
            return true;
        }
        if self.config.allowed_devices.contains(path) {
            return false;
        }
        !self.config.allow_new_connections
    }

    fn refresh_devices(&mut self) {
        let list = crate::devices::list_input_devices().unwrap_or_default();
        self.connected_devices = list
            .into_iter()
            .map(|d| DeviceEntry {
                label: format!("{}  {}", short_kind(&d.kind), trimmed_path(&d.name)),
                path: d.name,
            })
            .collect();
        self.connected_devices.sort_by(|a, b| a.label.cmp(&b.label));
    }
}

fn short_kind(kind: &crate::devices::DeviceKind) -> &'static str {
    match kind {
        crate::devices::DeviceKind::Keyboard { .. } => "[keyboard]",
        crate::devices::DeviceKind::Mouse { .. } => "[mouse]   ",
        crate::devices::DeviceKind::Hid { .. } => "[hid]     ",
        crate::devices::DeviceKind::Unknown(_) => "[other]   ",
    }
}

/// Last path segment, for menu readability. Full path still used internally.
fn trimmed_path(p: &str) -> String {
    const MAX: usize = 60;
    if p.len() <= MAX {
        p.to_string()
    } else {
        let tail = &p[p.len() - (MAX - 3)..];
        format!("...{tail}")
    }
}

/// Launch the tray. Blocks until the user chooses Quit.
pub fn run() -> windows::core::Result<()> {
    let mut config = Config::load_or_default();

    // On first run the allowed-devices snapshot is empty. Seed it from the
    // devices currently connected so the user is not locked out of their own
    // keyboard the first time the app starts.
    let seed_snapshot = config.allowed_devices.is_empty();
    if seed_snapshot {
        if let Ok(list) = crate::devices::list_input_devices() {
            for d in list {
                config.allowed_devices.insert(d.name);
            }
        }
        let _ = config.save();
    }

    let (writer, current_path) = capture::open_timestamped_file(&config.data_dir);
    let capture = Capture::new(writer);
    let state = Box::new(AppState::new(config, capture, current_path));

    let hinstance = unsafe { GetModuleHandleW(PCWSTR::null())? };
    let class_name = w!("InquisitorTrayWnd");

    let wc = WNDCLASSEXW {
        cbSize: size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: Some(wnd_proc),
        hInstance: hinstance.into(),
        lpszClassName: class_name,
        ..Default::default()
    };
    unsafe { RegisterClassExW(&wc) };

    let hwnd = unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            class_name,
            w!("Inquisitor"),
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

    // Hand ownership of the AppState to the window. The Box is reclaimed on WM_DESTROY.
    let state_ptr = Box::into_raw(state);
    unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, state_ptr as isize) };

    capture::register_for_raw_input(hwnd)?;
    add_tray_icon(hwnd)?;

    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}

fn add_tray_icon(hwnd: HWND) -> windows::core::Result<()> {
    unsafe {
        let icon = LoadIconW(None, IDI_APPLICATION)?;
        let mut tip = [0u16; 128];
        for (i, c) in "Inquisitor".encode_utf16().enumerate().take(127) {
            tip[i] = c;
        }
        let nid = NOTIFYICONDATAW {
            cbSize: size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: hwnd,
            uID: 1,
            uFlags: NIF_ICON | NIF_MESSAGE | NIF_TIP,
            uCallbackMessage: WM_TRAY,
            hIcon: icon,
            szTip: tip,
            ..Default::default()
        };
        if !Shell_NotifyIconW(NIM_ADD, &nid).as_bool() {
            return Err(windows::core::Error::from_thread());
        }
    }
    Ok(())
}

fn remove_tray_icon(hwnd: HWND) {
    unsafe {
        let nid = NOTIFYICONDATAW {
            cbSize: size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: hwnd,
            uID: 1,
            ..Default::default()
        };
        let _ = Shell_NotifyIconW(NIM_DELETE, &nid);
    }
}

extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        let state_ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut AppState;
        if state_ptr.is_null() {
            return DefWindowProcW(hwnd, msg, wparam, lparam);
        }
        let state = &mut *state_ptr;

        match msg {
            WM_INPUT => {
                let state_for_filter: *mut AppState = state_ptr;
                state.capture.process_wm_input(lparam, |path| {
                    // Reborrow to avoid double-mut-aliasing through the closure.
                    let s = &mut *state_for_filter;
                    !s.drop_event(path)
                });
                LRESULT(0)
            }
            m if m == WM_TRAY => {
                // Low-word of lparam is the mouse/keyboard event.
                let event = (lparam.0 as u32) & 0xFFFF;
                if event == WM_RBUTTONUP || event == WM_CONTEXTMENU {
                    show_menu(hwnd, state);
                }
                LRESULT(0)
            }
            WM_COMMAND => {
                let id = (wparam.0 as u32) & 0xFFFF;
                handle_command(hwnd, state, id as u16);
                LRESULT(0)
            }
            WM_DESTROY => {
                remove_tray_icon(hwnd);
                PostQuitMessage(0);
                // Drop AppState.
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                drop(Box::from_raw(state_ptr));
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

fn show_menu(hwnd: HWND, state: &mut AppState) {
    state.refresh_devices();
    // Re-sync startup state in case another tool changed the registry.
    state.startup_enabled = startup::is_enabled();

    unsafe {
        let mut pt = POINT::default();
        let _ = GetCursorPos(&mut pt);

        let menu = CreatePopupMenu().unwrap();
        build_menu(menu, state);

        // Required so the menu closes on outside clicks.
        let _ = SetForegroundWindow(hwnd);
        let _ = TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | TPM_LEFTALIGN | TPM_BOTTOMALIGN,
            pt.x,
            pt.y,
            Some(0),
            hwnd,
            None,
        );
        // Dismiss any lingering menu by posting a no-op per MSDN.
        let _ = PostMessageW(Some(hwnd), WM_NULL, WPARAM(0), LPARAM(0));
        let _ = DestroyMenu(menu);
    }
}

fn to_wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn append(menu: HMENU, flags: u32, id: u16, label: &str) {
    let w = to_wide(label);
    unsafe {
        let _ = AppendMenuW(
            menu,
            windows::Win32::UI::WindowsAndMessaging::MENU_ITEM_FLAGS(flags),
            id as usize,
            PCWSTR(w.as_ptr()),
        );
    }
}

fn append_sep(menu: HMENU) {
    unsafe {
        let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    }
}

fn append_submenu(menu: HMENU, submenu: HMENU, label: &str) {
    let w = to_wide(label);
    unsafe {
        let _ = AppendMenuW(
            menu,
            MF_POPUP,
            submenu.0 as usize,
            PCWSTR(w.as_ptr()),
        );
    }
}

fn build_menu(menu: HMENU, state: &AppState) {
    let pause_label = if state.paused { "Resume" } else { "Pause" };
    append(menu, MF_STRING.0, ID_PAUSE_RESUME, pause_label);

    unsafe {
        let pause_submenu = CreatePopupMenu().unwrap();
        append(pause_submenu, MF_STRING.0, ID_PAUSE_5M, "5 minutes");
        append(pause_submenu, MF_STRING.0, ID_PAUSE_15M, "15 minutes");
        append(pause_submenu, MF_STRING.0, ID_PAUSE_1H, "1 hour");
        append_submenu(menu, pause_submenu, "Pause for...");
    }

    append_sep(menu);
    append(menu, MF_STRING.0, ID_OPEN_DATA, "Open data location");
    append(menu, MF_STRING.0, ID_SET_DATA, "Set data location...");

    append_sep(menu);
    let allow_new_flags = MF_STRING.0
        | if state.config.allow_new_connections {
            MF_CHECKED.0
        } else {
            MF_UNCHECKED.0
        };
    append(
        menu,
        allow_new_flags,
        ID_TOGGLE_ALLOW_NEW,
        "Allow new connections",
    );

    unsafe {
        let ignore_submenu = CreatePopupMenu().unwrap();
        if state.connected_devices.is_empty() {
            let w = to_wide("(no devices)");
            let _ = AppendMenuW(
                ignore_submenu,
                windows::Win32::UI::WindowsAndMessaging::MENU_ITEM_FLAGS(
                    MF_STRING.0 | windows::Win32::UI::WindowsAndMessaging::MF_GRAYED.0,
                ),
                0,
                PCWSTR(w.as_ptr()),
            );
        } else {
            for (i, dev) in state.connected_devices.iter().enumerate() {
                if i >= 500 {
                    break; // leave headroom before other id ranges
                }
                let flag = if state.config.ignored_devices.contains(&dev.path) {
                    MF_CHECKED.0
                } else {
                    MF_UNCHECKED.0
                };
                append(
                    ignore_submenu,
                    MF_STRING.0 | flag,
                    IGNORE_BASE + i as u16,
                    &dev.label,
                );
            }
        }
        append_submenu(menu, ignore_submenu, "Ignore devices");
    }

    append_sep(menu);
    let startup_flags = MF_STRING.0
        | if state.startup_enabled {
            MF_CHECKED.0
        } else {
            MF_UNCHECKED.0
        };
    append(menu, startup_flags, ID_TOGGLE_STARTUP, "Start with Windows");

    append_sep(menu);
    append(menu, MF_STRING.0, ID_QUIT, "Quit");
}

fn handle_command(hwnd: HWND, state: &mut AppState, id: u16) {
    match id {
        ID_PAUSE_RESUME => {
            state.paused = !state.paused;
            state.pause_until = None;
        }
        ID_PAUSE_5M => pause_for(state, 5 * 60),
        ID_PAUSE_15M => pause_for(state, 15 * 60),
        ID_PAUSE_1H => pause_for(state, 60 * 60),
        ID_OPEN_DATA => {
            let _ = std::process::Command::new("explorer")
                .arg(&state.config.data_dir)
                .spawn();
        }
        ID_SET_DATA => {
            if let Some(new_dir) = folder_picker::pick_folder() {
                state.config.data_dir = new_dir.clone();
                let _ = state.config.save();
                let (writer, path) = capture::open_timestamped_file(&new_dir);
                state.capture.set_writer(writer);
                state.current_path = path;
            }
        }
        ID_TOGGLE_ALLOW_NEW => {
            state.config.allow_new_connections = !state.config.allow_new_connections;
            let _ = state.config.save();
        }
        ID_TOGGLE_STARTUP => {
            if state.startup_enabled {
                let _ = startup::disable();
            } else if let Ok(exe) = std::env::current_exe() {
                let _ = startup::enable(&exe);
            }
            state.startup_enabled = startup::is_enabled();
        }
        ID_QUIT => unsafe {
            let _ = PostMessageW(Some(hwnd), WM_DESTROY, WPARAM(0), LPARAM(0));
        },
        other if other >= IGNORE_BASE => {
            let idx = (other - IGNORE_BASE) as usize;
            if let Some(dev) = state.connected_devices.get(idx) {
                let path = dev.path.clone();
                if state.config.ignored_devices.contains(&path) {
                    state.config.ignored_devices.remove(&path);
                } else {
                    state.config.ignored_devices.insert(path);
                }
                let _ = state.config.save();
            }
        }
        _ => {}
    }
}

fn pause_for(state: &mut AppState, seconds: u64) {
    state.paused = true;
    state.pause_until = Some(Instant::now() + std::time::Duration::from_secs(seconds));
}
