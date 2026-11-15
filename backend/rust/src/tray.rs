//! System-tray front end for Inquisitor.
//!
//! Hidden message-only window hosts both the Raw Input subscription and the
//! `Shell_NotifyIcon` tray icon. A single wnd_proc dispatches input, tray and
//! menu messages against an `AppState` stored in the window's `GWLP_USERDATA`.

use std::mem::size_of;
use std::path::PathBuf;
use std::time::Instant;

use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIIF_INFO, NIM_ADD, NIM_DELETE, NIM_MODIFY,
    NIM_SETVERSION, NIN_BALLOONUSERCLICK, NOTIFYICON_VERSION, NOTIFYICONDATAW, Shell_NotifyIconW,
    ShellExecuteW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GWLP_USERDATA, GetCursorPos, GetMessageW, GetWindowLongPtrW, HMENU,
    HWND_MESSAGE, IsDialogMessageW, IsWindow, MF_CHECKED, MF_GRAYED, MF_POPUP, MF_SEPARATOR,
    MF_STRING, MF_UNCHECKED, MSG, PostMessageW, PostQuitMessage, RegisterClassExW, SW_SHOWNORMAL,
    SetForegroundWindow, SetTimer, SetWindowLongPtrW, TPM_BOTTOMALIGN, TPM_LEFTALIGN,
    TPM_RIGHTALIGN, TPM_RIGHTBUTTON, TPM_TOPALIGN, TrackPopupMenu, TranslateMessage,
    WINDOW_EX_STYLE, WINDOW_STYLE, WM_APP, WM_COMMAND, WM_CONTEXTMENU, WM_DESTROY, WM_INPUT,
    WM_LBUTTONUP, WM_NULL, WM_TIMER, WNDCLASSEXW,
};
use windows::core::{PCWSTR, w};

use crate::capture::{self, Capture};
use crate::config::Config;
use crate::credential;
use crate::devices_window;
use crate::features::Windower;
use crate::folder_picker;
use crate::http::Endpoint;
use crate::icon;
use crate::link::{self, Link, Mode, State};
use crate::pair_window;
use crate::startup;

/// Class of the tray's message-only window. The installer finds running trays
/// by this name to close them before replacing the exe.
pub const CLASS_NAME: PCWSTR = w!("InquisitorTrayWnd");

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
const ID_CONNECT: u16 = 30;
const ID_DISCONNECT: u16 = 31;
const ID_TOGGLE_STARTUP: u16 = 40;
const ID_QUIT: u16 = 50;

// Closes feature windows that ended with no input after them, sends them to the
// link, and notices when a timed pause has run out.
const TICK_TIMER_ID: usize = 1;
const TICK_MS: u32 = 5_000;

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
    /// Connection to the website. None until the tray window exists.
    link: Option<Link>,
    /// Pause state last told to the link, so changes are sent once.
    paused_sent: bool,
    /// Website login opened when the user clicks the re-verify balloon.
    reverify_url: Option<String>,
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
            link: None,
            paused_sent: false,
            reverify_url: None,
        }
    }

    /// End a timed pause whose deadline has passed.
    fn expire_pause(&mut self) {
        if let Some(until) = self.pause_until
            && Instant::now() >= until
        {
            self.paused = false;
            self.pause_until = None;
        }
    }

    /// Returns true if the event should be dropped. Auto-clears a timed pause once
    /// the deadline has passed. A device captures if and only if its path is in
    /// `allowed_devices`; new devices are silently dropped until the user ticks
    /// them (or "Allow all") in the Allow connections window.
    fn drop_event(&mut self, path: &str) -> bool {
        self.expire_pause();
        // Startup asks for the terms before the tray runs; this keeps the rule
        // even if the tray is ever started another way.
        if self.paused || !self.config.terms_accepted() {
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
    let mut capture = Capture::new(writer);
    capture.set_windower(Some(Windower::new()));
    let state = Box::new(AppState::new(config, capture, current_path));

    let hinstance = unsafe { GetModuleHandleW(PCWSTR::null())? };
    let class_name = CLASS_NAME;

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

    // Connect to the website when this PC was paired before.
    let state = unsafe { &mut *state_ptr };
    let server = state
        .config
        .server_url
        .as_deref()
        .and_then(|url| Endpoint::parse(url).ok());
    state.link = Some(Link::start(hwnd, server, credential::load(), false));
    unsafe {
        SetTimer(Some(hwnd), TICK_TIMER_ID, TICK_MS, None);
    }

    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            // Tab, Enter, and Escape in the pairing dialog.
            if let Some(dialog) = pair_window::current()
                && IsDialogMessageW(dialog, &msg).as_bool()
            {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}

fn add_tray_icon(hwnd: HWND) -> windows::core::Result<()> {
    unsafe {
        let icon = icon::small();
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
        // Version 3 delivers NIN_BALLOONUSERCLICK. Version 4 would repack mouse
        // messages and break the left-click menu.
        let mut version = nid;
        version.Anonymous.uVersion = NOTIFYICON_VERSION;
        let _ = Shell_NotifyIconW(NIM_SETVERSION, &version);
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
                forward_windows(state);
                sync_pause(state);
                LRESULT(0)
            }
            WM_TIMER if wparam.0 == TICK_TIMER_ID => {
                state.expire_pause();
                let now = state.capture.monotonic_s();
                if let Some(windower) = state.capture.windower_mut() {
                    windower.tick(now);
                }
                forward_windows(state);
                sync_pause(state);
                LRESULT(0)
            }
            m if m == link::WM_LINK => {
                if let Some(link) = &state.link {
                    set_tooltip(hwnd, &tooltip(&link.status()));
                }
                LRESULT(0)
            }
            m if m == link::WM_REVERIFY => {
                if let Some(server) = state.link.as_ref().and_then(|link| link.status().server) {
                    state.reverify_url = Some(link::login_url(&server));
                    show_reverify_balloon(hwnd);
                }
                LRESULT(0)
            }
            m if m == pair_window::WM_PAIRED => {
                if let Some((endpoint, credential)) = pair_window::take_paired() {
                    state.config.server_url = Some(endpoint.display());
                    let _ = state.config.save();
                    if let Some(link) = &state.link {
                        link.connect(endpoint, credential);
                    }
                }
                LRESULT(0)
            }
            m if m == WM_TRAY => {
                // Low-word of lparam is the mouse/keyboard event. We open the
                // menu on left-click (user preference) and also on WM_CONTEXTMENU,
                // which fires for keyboard-driven access (Shift+F10, apps key).
                let event = (lparam.0 as u32) & 0xFFFF;
                if event == NIN_BALLOONUSERCLICK {
                    if let Some(url) = state.reverify_url.clone() {
                        open_url(&url);
                    }
                } else if event == WM_LBUTTONUP || event == WM_CONTEXTMENU {
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
                if let Some(dialog) = pair_window::current() {
                    let _ = DestroyWindow(dialog);
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
fn menu_anchor() -> (
    POINT,
    windows::Win32::UI::WindowsAndMessaging::TRACK_POPUP_MENU_FLAGS,
) {
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
        let _ = AppendMenuW(menu, MF_POPUP, submenu.0 as usize, PCWSTR(w.as_ptr()));
    }
}

fn build_menu(menu: HMENU, state: &AppState) {
    // Account connection first, like a VPN client: where this PC reports to.
    if let Some(link) = &state.link {
        let status = link.status();
        append(menu, MF_STRING.0 | MF_GRAYED.0, 0, &status_label(&status));
        match status.state {
            State::NotPaired | State::Disconnected(_) => {
                append(menu, MF_STRING.0, ID_CONNECT, "Connect to account...")
            }
            _ => append(menu, MF_STRING.0, ID_DISCONNECT, "Disconnect from account"),
        }
        append_sep(menu);
    }

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
        ID_CONNECT => pair_window::open(hwnd, state.config.server_url.as_deref()),
        ID_DISCONNECT => {
            // Deletes the local credential. The device stays listed on the website
            // as offline until it is removed there.
            if let Some(link) = &state.link {
                link.disconnect();
            }
        }
        ID_QUIT => unsafe {
            let _ = DestroyWindow(hwnd);
        },
        _ => {}
    }
    sync_pause(state);
}

fn pause_for(state: &mut AppState, seconds: u64) {
    state.paused = true;
    state.pause_until = Some(Instant::now() + std::time::Duration::from_secs(seconds));
}

/// Hand finished feature windows to the link, which decides whether to upload.
fn forward_windows(state: &mut AppState) {
    let ready = state
        .capture
        .windower_mut()
        .map(|w| w.take_ready())
        .unwrap_or_default();
    if let Some(link) = &state.link {
        for window in ready {
            link.send_window(window);
        }
    }
}

/// Tell the link when the pause state changes, so the website shows Paused.
fn sync_pause(state: &mut AppState) {
    if state.paused != state.paused_sent {
        state.paused_sent = state.paused;
        if let Some(link) = &state.link {
            link.set_paused(state.paused);
        }
    }
}

/// The first, greyed-out line of the tray menu.
fn status_label(status: &link::Status) -> String {
    let server = status
        .server
        .as_deref()
        .map(|s| {
            s.trim_start_matches("https://")
                .trim_start_matches("http://")
        })
        .unwrap_or("the website");
    let label = match &status.state {
        State::NotPaired => "Not connected to an account".to_string(),
        State::Connecting => format!("Connecting to {server}..."),
        State::Connected => match status.mode {
            Mode::Enrollment(_) => format!("Connected to {server}: enrolling"),
            Mode::Monitoring => format!("Connected to {server}: monitoring"),
            Mode::Idle => format!("Connected to {server}"),
        },
        State::Offline => "Offline, retrying".to_string(),
        State::Disconnected(reason) => format!("Disconnected. {reason}"),
    };
    // Summaries are held on this PC until they can be sent.
    match status.queued {
        0 => label,
        1 => format!("{label} (1 summary held)"),
        n => format!("{label} ({n} summaries held)"),
    }
}

fn tooltip(status: &link::Status) -> String {
    let short = match status.state {
        State::NotPaired => "not connected",
        State::Connecting => "connecting",
        State::Connected => "connected",
        State::Offline => "offline",
        State::Disconnected(_) => "disconnected",
    };
    format!("Inquisitor: {short}")
}

const REVERIFY_BODY: &str = "Please confirm it is you. Click to sign in on the website and re-verify. This does not lock Windows.";

fn write_utf16(dest: &mut [u16], text: &str) {
    let limit = dest.len().saturating_sub(1);
    for (slot, unit) in dest.iter_mut().take(limit).zip(text.encode_utf16()) {
        *slot = unit;
    }
}

/// Balloon only. It does not take focus, lock the session, or block input.
fn show_reverify_balloon(hwnd: HWND) {
    let mut info = [0u16; 256];
    let mut title = [0u16; 64];
    write_utf16(&mut info, REVERIFY_BODY);
    write_utf16(&mut title, "Inquisitor");
    let nid = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_INFO,
        szInfo: info,
        szInfoTitle: title,
        dwInfoFlags: NIIF_INFO,
        ..Default::default()
    };
    unsafe {
        let _ = Shell_NotifyIconW(NIM_MODIFY, &nid);
    }
}

/// Open `url` in the default browser. Only the paired site's login page is passed.
fn open_url(url: &str) {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return;
    }
    let wide = to_wide(url);
    unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            PCWSTR(wide.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        );
    }
}

fn set_tooltip(hwnd: HWND, text: &str) {
    let mut tip = [0u16; 128];
    for (i, c) in text.encode_utf16().enumerate().take(127) {
        tip[i] = c;
    }
    let nid = NOTIFYICONDATAW {
        cbSize: size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_TIP,
        szTip: tip,
        ..Default::default()
    };
    unsafe {
        let _ = Shell_NotifyIconW(NIM_MODIFY, &nid);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    const ALLOWED: &str =
        r"\\?\HID#VID_046D&PID_C52B&MI_00#7&TEST#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}";
    const OTHER: &str =
        r"\\?\HID#VID_057E&PID_2069&MI_00#7&TEST#{4d1e55b2-f16f-11cf-88cb-001111000030}";

    fn state() -> AppState {
        let config = Config {
            data_dir: std::env::temp_dir(),
            allowed_devices: [ALLOWED.to_string()].into_iter().collect(),
            terms_accepted: crate::config::TERMS_VERSION,
            server_url: None,
        };
        AppState::new(
            config,
            Capture::new(Box::new(std::io::sink())),
            PathBuf::new(),
        )
    }

    #[test]
    fn only_whitelisted_devices_are_captured() {
        let mut s = state();
        assert!(!s.drop_event(ALLOWED));
        assert!(s.drop_event(OTHER));
        // Pointer input Windows synthesizes without a device handle has no path.
        assert!(s.drop_event(""));
    }

    #[test]
    fn pause_drops_everything_until_resumed() {
        let mut s = state();
        s.paused = true;
        assert!(s.drop_event(ALLOWED));
        s.paused = false;
        assert!(!s.drop_event(ALLOWED));
    }

    #[test]
    fn timed_pause_holds_until_the_deadline() {
        let mut s = state();
        pause_for(&mut s, 60);
        assert!(s.drop_event(ALLOWED));
        assert!(s.paused);
    }

    #[test]
    fn timed_pause_auto_resumes_after_the_deadline() {
        let mut s = state();
        s.paused = true;
        s.pause_until = Instant::now().checked_sub(Duration::from_millis(1));
        assert!(!s.drop_event(ALLOWED));
        assert!(!s.paused);
        assert!(s.pause_until.is_none());
    }

    #[test]
    fn nothing_is_captured_before_the_terms_are_accepted() {
        let mut s = state();
        s.config.terms_accepted = 0;
        assert!(s.drop_event(ALLOWED));
    }

    #[test]
    fn whitelist_changes_take_effect_immediately() {
        let mut s = state();
        s.config.allowed_devices.insert(OTHER.to_string());
        assert!(!s.drop_event(OTHER));
        s.config.allowed_devices.clear();
        assert!(s.drop_event(ALLOWED));
    }
}
