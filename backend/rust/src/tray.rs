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
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW, Shell_NotifyIconW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GWLP_USERDATA, GetCursorPos, GetMessageW, GetWindowLongPtrW, HMENU,
    HWND_MESSAGE, IDI_APPLICATION, IsWindow, LoadIconW, MF_CHECKED, MF_POPUP, MF_SEPARATOR,
    MF_STRING, MF_UNCHECKED, MSG, PostMessageW, PostQuitMessage, RegisterClassExW,
    SetForegroundWindow, SetWindowLongPtrW, TPM_BOTTOMALIGN, TPM_LEFTALIGN, TPM_RIGHTALIGN,
    TPM_RIGHTBUTTON, TPM_TOPALIGN, TrackPopupMenu, TranslateMessage, WINDOW_EX_STYLE,
    WINDOW_STYLE, WM_APP, WM_COMMAND, WM_CONTEXTMENU, WM_DESTROY, WM_INPUT, WM_LBUTTONUP,
    WM_NULL, WNDCLASSEXW,
};

use crate::capture::{self, Capture};
use crate::config::Config;
use crate::devices_window;
use crate::folder_picker;
use crate::startup;

// Tray notification callback. WM_APP + N is the recommended range for app-defined
// messages per MSDN (WM_USER is reserved for controls).
const WM_TRAY: u32 = WM_APP + 1;

// Tray menu command IDs. Low 16 bits only; WM_COMMAND packs the id into wparam.lo.
const ID_PAUSE_RESUME: u16 = 10;
const ID_PAUSE_5M: u16 = 11;
const ID_PAUSE_15M: u16 = 12;
const ID_PAUSE_1H: u16 = 13;
const ID_OPEN_DATA: u16 = 20;
const ID_SET_DATA: u16 = 21;
const ID_OPEN_DEVICES: u16 = 22;
const ID_TOGGLE_STARTUP: u16 = 40;
const ID_QUIT: u16 = 50;

struct AppState {
    config: Config,
    capture: Capture,
    current_path: PathBuf,
    /// If Some, capture is paused until this instant (auto-resume on first event after).
    /// If None and `paused`, capture is paused indefinitely.
    paused: bool,
    pause_until: Option<Instant>,
    startup_enabled: bool,
    /// HWND of the "Allow connections" window if it has been opened. May be stale
    /// after the user closes it; always check with IsWindow before use.
    devices_hwnd: Option<HWND>,
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
            startup_enabled,
            devices_hwnd: None,
        }
    }

    /// Returns true if the event should be dropped. Auto-clears a timed pause once
    /// the deadline has passed. A device captures if and only if its path is in
    /// `allowed_devices`; new devices are silently dropped until the user ticks
    /// them (or "Allow all") in the Allow connections window.
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
        !self.config.allowed_devices.contains(path)
    }
}

/// Launch the tray. Blocks until the user chooses Quit.
pub fn run() -> windows::core::Result<()> {
    // No devices are whitelisted on first run. The user opens "Allow
    // connections" and either clicks "Allow all" or ticks the devices they want
    // captured. Nothing records until at least one device is allowed.
    let config = Config::load_or_default();
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
                    // Feed Identify mode before filtering, so blocked and paused
                    // devices still light up. Only the device path is passed.
                    if let Some(dh) = s.devices_hwnd {
                        devices_window::report_activity(dh, path);
                    }
                    !s.drop_event(path)
                });
                LRESULT(0)
            }
            m if m == WM_TRAY => {
                // Low-word of lparam is the mouse/keyboard event. We open the
                // menu on left-click (user preference) and also on WM_CONTEXTMENU,
                // which fires for keyboard-driven access (Shift+F10, apps key).
                let event = (lparam.0 as u32) & 0xFFFF;
                if event == WM_LBUTTONUP || event == WM_CONTEXTMENU {
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
                // Close the devices window first: it holds a pointer to
                // `state.config`, which is freed below.
                if let Some(dh) = state.devices_hwnd.take()
                    && IsWindow(Some(dh)).as_bool()
                {
                    let _ = DestroyWindow(dh);
                }
                remove_tray_icon(hwnd);
                PostQuitMessage(0);
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                drop(Box::from_raw(state_ptr));
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

fn show_menu(hwnd: HWND, state: &mut AppState) {
    // Re-sync startup state in case another tool changed the registry.
    state.startup_enabled = startup::is_enabled();

    let (pt, align) = menu_anchor();

    unsafe {
        let menu = CreatePopupMenu().unwrap();
        build_menu(menu, state);

        // Required so the menu closes on outside clicks.
        let _ = SetForegroundWindow(hwnd);
        let _ = TrackPopupMenu(
            menu,
            TPM_RIGHTBUTTON | align,
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

/// Anchor the menu at the click point and open it toward the middle of the
/// monitor, the same way Explorer's own tray menus behave. With the taskbar at
/// the bottom-right this puts the menu's bottom-right corner at the cursor, so it
/// rises up and to the left of the icon instead of covering it.
fn menu_anchor() -> (POINT, windows::Win32::UI::WindowsAndMessaging::TRACK_POPUP_MENU_FLAGS) {
    let mut pt = POINT::default();
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    unsafe {
        let _ = GetCursorPos(&mut pt);
        let monitor = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST);
        let _ = GetMonitorInfoW(monitor, &mut info);
    }
    let work = info.rcWork;
    let horizontal = if pt.x > (work.left + work.right) / 2 {
        TPM_RIGHTALIGN
    } else {
        TPM_LEFTALIGN
    };
    let vertical = if pt.y > (work.top + work.bottom) / 2 {
        TPM_BOTTOMALIGN
    } else {
        TPM_TOPALIGN
    };
    (pt, horizontal | vertical)
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

    // Opens a dedicated small window with checkboxes + Allow all / Remove all
    // buttons. Keeping that UI outside the tray menu means the user can tick
    // several devices in a row without the menu dismissing between clicks.
    append(menu, MF_STRING.0, ID_OPEN_DEVICES, "Allow connections...");

    append_sep(menu);
    let startup_flags = MF_STRING.0
        | if state.startup_enabled {
            MF_CHECKED.0
        } else {
            MF_UNCHECKED.0
        };
    append(menu, startup_flags, ID_TOGGLE_STARTUP, "Enable startup app");

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
        ID_OPEN_DEVICES => {
            state.devices_hwnd =
                devices_window::open(state.devices_hwnd, &mut state.config as *mut Config);
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
            let _ = DestroyWindow(hwnd);
        },
        _ => {}
    }
}

fn pause_for(state: &mut AppState, seconds: u64) {
    state.paused = true;
    state.pause_until = Some(Instant::now() + std::time::Duration::from_secs(seconds));
}
