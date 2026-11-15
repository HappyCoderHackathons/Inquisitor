//! Install, update, repair, and remove wizard.
//!
//! A small fixed-size window. Until the current terms are accepted it opens on a
//! terms page listing what is recorded and what is never collected; accepting
//! is saved in config.ini, and nothing is collected before it. Then two pages:
//!
//! ```text
//!  Install Inquisitor                     Remove Inquisitor
//!  Installs version 0.1.0 for ...         Removes the app from ...
//!  [x] Add to Start menu                  [ ] Also delete captured data
//!  [ ] Start with Windows
//!  [x] Launch Inquisitor when done
//!  [Remove...]   [Run once] [Install] [Cancel]     [Back]   [Remove] [Cancel]
//! ```
//!
//! The main page's action depends on `install::Mode`: Install when nothing is
//! installed, Update when another version is installed, and Repair when the same
//! version is installed or this is the installed exe. The work itself lives in
//! `install`; this module is only the window.

use std::mem::size_of;
use std::sync::atomic::{AtomicBool, Ordering};

use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    COLOR_GRAYTEXT, COLOR_WINDOW, DeleteObject, FW_NORMAL, FW_SEMIBOLD, GetSysColor,
    GetSysColorBrush, HDC, HFONT, SetBkColor, SetBkMode, SetTextColor, TRANSPARENT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    ICC_STANDARD_CLASSES, INITCOMMONCONTROLSEX, InitCommonControlsEx,
};
use windows::Win32::UI::HiDpi::{AdjustWindowRectExForDpi, GetDpiForWindow};
use windows::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, SetFocus};
use windows::Win32::UI::WindowsAndMessaging::{
    BM_GETCHECK, BM_SETCHECK, BS_AUTOCHECKBOX, BS_DEFPUSHBUTTON, BS_PUSHBUTTON, CW_USEDEFAULT,
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GWLP_USERDATA, GetMessageW,
    GetWindowLongPtrW, HMENU, IDC_ARROW, IDC_WAIT, IsDialogMessageW, LoadCursorW, MB_ICONERROR,
    MB_ICONINFORMATION, MB_OK, MESSAGEBOX_STYLE, MSG, MessageBoxW, MoveWindow, PostQuitMessage,
    RegisterClassExW, SW_HIDE, SW_SHOW, SWP_NOACTIVATE, SWP_NOZORDER, SetCursor,
    SetForegroundWindow, SetWindowLongPtrW, SetWindowPos, SetWindowTextW, ShowWindow,
    TranslateMessage, WINDOW_EX_STYLE, WINDOW_STYLE, WM_CLOSE, WM_COMMAND, WM_CTLCOLORBTN,
    WM_CTLCOLORSTATIC, WM_DESTROY, WM_DPICHANGED, WM_SETFONT, WNDCLASSEXW, WS_BORDER, WS_CAPTION,
    WS_CHILD, WS_CLIPCHILDREN, WS_MINIMIZEBOX, WS_OVERLAPPED, WS_SYSMENU, WS_TABSTOP, WS_VISIBLE,
    WS_VSCROLL,
};
use windows::core::{PCWSTR, w};

use crate::config::{Config, TERMS_VERSION};
use crate::install::{self, Mode, Options, VERSION};
use crate::ui::{self, scale, to_wide};
use crate::{icon, startup};

const CLASS: PCWSTR = w!("InquisitorWizardWnd");

// IDOK and IDCANCEL, so Enter and Escape work through IsDialogMessage.
const ID_PRIMARY: u16 = 1;
const ID_CANCEL: u16 = 2;
const ID_LEFT: u16 = 3;
const ID_RUN_ONCE: u16 = 4;
const ID_START_MENU: u16 = 10;
const ID_STARTUP: u16 = 11;
const ID_LAUNCH: u16 = 12;
const ID_DELETE_DATA: u16 = 13;
const ID_TERMS: u16 = 14;

// Static-control style from <winuser.h>.
const SS_NOPREFIX: u32 = 0x0080;
// Edit-control styles from <winuser.h>.
const ES_MULTILINE: u32 = 0x0004;
const ES_AUTOVSCROLL: u32 = 0x0040;
const ES_READONLY: u32 = 0x0800;
// Checkbox state from <winuser.h>.
const BST_CHECKED: usize = 1;

// Client area in 96-DPI layout units.
const WIDTH: i32 = 500;
const HEIGHT: i32 = 300;
// The terms page is taller so most of the list fits without scrolling.
const TERMS_HEIGHT: i32 = 560;

/// Shown on the terms page. Keep it in step with what `capture` writes, and
/// raise `TERMS_VERSION` whenever what is recorded changes. The edit control
/// needs CRLF line breaks.
const TERMS: &str = "\
WHAT INQUISITOR RECORDS\r\n\
Only from devices you tick in Allow connections. Until you allow a device, \
nothing is recorded from it.\r\n\r\n\
- Keyboard: when each key goes down and comes up, and the time between keys. \
Not which key.\r\n\
- Mouse: how far it moved, button presses, and scrolling. Pens and remote \
desktop sessions that drive the pointer report a position instead of a \
distance.\r\n\
- Controllers: button presses and stick, trigger, and d-pad values.\r\n\
- Touch pads, touch screens, pens, and media keys: button presses and the \
values the device reports, which can include where a finger or pen touches \
the device.\r\n\
- For every event: the time, the kind of device, and a short device number \
that changes each session.\r\n\r\n\
WHAT IS NEVER COLLECTED\r\n\
- Which keys you press, or any text you type, including passwords\r\n\
- What you click on, or which window or app you are using\r\n\
- Window titles, websites, or anything shown on screen\r\n\
- Clipboard contents, files, microphone, camera, or location\r\n\r\n\
WHERE IT GOES\r\n\
- Records are saved as CSV files on this PC, in the data folder \
(%APPDATA%\\inquisitor unless you change it).\r\n\
- This version does not upload anything.\r\n\
- Pause or quit at any time from the tray icon, and choose which devices are \
recorded.\r\n\
- Remove in setup can delete the recorded data.";

/// Which page the wizard opens on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Start {
    /// Install, update, or repair, after the terms if they are not accepted yet.
    Main,
    /// Remove, as Apps & features does through `Inquisitor.exe uninstall`.
    Remove,
    /// Only the terms, before the tray starts. Accepting returns `RunTray`.
    Terms,
}

/// What `main` should do after the wizard closes.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    Exit,
    /// "Run once": start the tray from this exe without installing.
    RunTray,
}

// Set by the window before it closes; read by `run` after the message loop.
static RUN_TRAY: AtomicBool = AtomicBool::new(false);
static SELF_DELETE: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Copy, PartialEq, Eq)]
enum Page {
    Terms,
    Main,
    Remove,
}

struct WizardState {
    hwnd: HWND,
    mode: Mode,
    page: Page,
    /// Opened with `Start::Terms`: accepting closes the window and starts the tray.
    terms_only: bool,
    dpi: u32,
    font: HFONT,
    title_font: HFONT,
    title: HWND,
    body: HWND,
    terms: HWND,
    start_menu: HWND,
    startup: HWND,
    launch: HWND,
    delete_data: HWND,
    left: HWND,
    run_once: HWND,
    primary: HWND,
    cancel: HWND,
}

impl WizardState {
    fn scale(&self, v: i32) -> i32 {
        scale(v, self.dpi)
    }
}

/// Show the wizard and block until it closes.
pub fn run(start: Start) -> windows::core::Result<Outcome> {
    let mode = install::mode();
    let icc = INITCOMMONCONTROLSEX {
        dwSize: size_of::<INITCOMMONCONTROLSEX>() as u32,
        dwICC: ICC_STANDARD_CLASSES,
    };
    unsafe {
        let _ = InitCommonControlsEx(&icc);
    }

    let hinstance = unsafe { GetModuleHandleW(PCWSTR::null())? };
    let wc = WNDCLASSEXW {
        cbSize: size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: Some(wnd_proc),
        hInstance: hinstance.into(),
        hCursor: unsafe { LoadCursorW(None, IDC_ARROW) }.unwrap_or_default(),
        hIcon: icon::large(),
        hIconSm: icon::small(),
        hbrBackground: unsafe { GetSysColorBrush(COLOR_WINDOW) },
        lpszClassName: CLASS,
        ..Default::default()
    };
    unsafe { RegisterClassExW(&wc) };

    let hwnd = unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS,
            w!("Inquisitor setup"),
            style(),
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            Some(hinstance.into()),
            None,
        )?
    };

    let child = |class: PCWSTR, style: u32, id: u16| -> HWND {
        unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                class,
                PCWSTR::null(),
                WS_CHILD | WS_VISIBLE | WINDOW_STYLE(style),
                0,
                0,
                0,
                0,
                Some(hwnd),
                Some(HMENU(id as usize as *mut _)),
                Some(hinstance.into()),
                None,
            )
        }
        .unwrap_or_default()
    };
    let check = BS_AUTOCHECKBOX as u32 | WS_TABSTOP.0;
    let button = BS_PUSHBUTTON as u32 | WS_TABSTOP.0;

    let st = Box::new(WizardState {
        hwnd,
        page: match start {
            // Removal collects nothing, so it does not need the terms.
            Start::Remove if mode != Mode::Install => Page::Remove,
            Start::Terms => Page::Terms,
            _ => main_or_terms(),
        },
        terms_only: start == Start::Terms,
        mode,
        dpi: unsafe { GetDpiForWindow(hwnd) }.max(96),
        font: HFONT::default(),
        title_font: HFONT::default(),
        title: child(w!("STATIC"), SS_NOPREFIX, 0),
        body: child(w!("STATIC"), SS_NOPREFIX, 0),
        terms: child(
            w!("EDIT"),
            ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL.0 | WS_BORDER.0 | WS_TABSTOP.0,
            ID_TERMS,
        ),
        start_menu: child(w!("BUTTON"), check, ID_START_MENU),
        startup: child(w!("BUTTON"), check, ID_STARTUP),
        launch: child(w!("BUTTON"), check, ID_LAUNCH),
        delete_data: child(w!("BUTTON"), check, ID_DELETE_DATA),
        left: child(w!("BUTTON"), button, ID_LEFT),
        run_once: child(w!("BUTTON"), button, ID_RUN_ONCE),
        primary: child(
            w!("BUTTON"),
            BS_DEFPUSHBUTTON as u32 | WS_TABSTOP.0,
            ID_PRIMARY,
        ),
        cancel: child(w!("BUTTON"), button, ID_CANCEL),
    });
    let st_ptr = Box::into_raw(st);
    unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, st_ptr as isize) };
    let st = unsafe { &mut *st_ptr };

    set_text(st.start_menu, "Add to Start menu");
    set_text(st.startup, "Start with Windows");
    set_text(st.launch, "Launch Inquisitor when done");
    set_text(st.delete_data, "Also delete captured data and settings");
    set_text(st.terms, TERMS);
    set_text(st.run_once, "Run once");
    // Fresh installs get a shortcut; otherwise keep what is there now.
    set_checked(
        st.start_menu,
        st.mode == Mode::Install || install::has_shortcut(),
    );
    set_checked(st.startup, startup::is_enabled());
    set_checked(st.launch, true);

    apply_fonts(st);
    show_page(st);
    let (w, h) = window_size(st.dpi, st.page);
    ui::place_centered(hwnd, w, h);
    layout(st);

    unsafe {
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetForegroundWindow(hwnd);
        let _ = SetFocus(Some(st.primary));
    }

    let mut msg = MSG::default();
    unsafe {
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            // Tab between controls, Enter for the default button, Escape to cancel.
            if IsDialogMessageW(hwnd, &msg).as_bool() {
                continue;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    if SELF_DELETE.load(Ordering::SeqCst) {
        install::delete_install_dir_after_exit();
    }
    Ok(if RUN_TRAY.load(Ordering::SeqCst) {
        Outcome::RunTray
    } else {
        Outcome::Exit
    })
}

fn style() -> WINDOW_STYLE {
    WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX | WS_CLIPCHILDREN
}

/// The terms page until the current terms are accepted, then the main page.
fn main_or_terms() -> Page {
    if Config::load_or_default().terms_accepted() {
        Page::Main
    } else {
        Page::Terms
    }
}

fn client_height(page: Page) -> i32 {
    if page == Page::Terms {
        TERMS_HEIGHT
    } else {
        HEIGHT
    }
}

/// Outer window size that gives the page's client area at `dpi`.
fn window_size(dpi: u32, page: Page) -> (i32, i32) {
    let mut rc = RECT {
        left: 0,
        top: 0,
        right: scale(WIDTH, dpi),
        bottom: scale(client_height(page), dpi),
    };
    unsafe {
        let _ = AdjustWindowRectExForDpi(&mut rc, style(), false, WINDOW_EX_STYLE(0), dpi);
    }
    (rc.right - rc.left, rc.bottom - rc.top)
}

fn set_text(hwnd: HWND, text: &str) {
    let wide = to_wide(text);
    unsafe {
        let _ = SetWindowTextW(hwnd, PCWSTR(wide.as_ptr()));
    }
}

fn set_checked(hwnd: HWND, checked: bool) {
    unsafe {
        windows::Win32::UI::WindowsAndMessaging::SendMessageW(
            hwnd,
            BM_SETCHECK,
            Some(WPARAM(if checked { BST_CHECKED } else { 0 })),
            Some(LPARAM(0)),
        );
    }
}

fn is_checked(hwnd: HWND) -> bool {
    let r = unsafe {
        windows::Win32::UI::WindowsAndMessaging::SendMessageW(
            hwnd,
            BM_GETCHECK,
            Some(WPARAM(0)),
            Some(LPARAM(0)),
        )
    };
    r.0 as usize == BST_CHECKED
}

fn set_visible(hwnd: HWND, visible: bool) {
    unsafe {
        let _ = ShowWindow(hwnd, if visible { SW_SHOW } else { SW_HIDE });
    }
}

/// Main-page title, body, and primary button label for a mode.
fn main_page_text(mode: &Mode, dir: &str) -> (String, String, &'static str) {
    match mode {
        Mode::Install => (
            "Install Inquisitor".into(),
            format!(
                "Installs Inquisitor {VERSION} for this user in {dir}. \
                 Administrator rights are not needed."
            ),
            "Install",
        ),
        Mode::Update { installed } if installed != VERSION => (
            "Update Inquisitor".into(),
            format!(
                "Version {installed} is installed in {dir}. This copy is version {VERSION}. \
                 Captured data and settings are kept."
            ),
            "Update",
        ),
        Mode::Update { installed } => (
            "Repair Inquisitor".into(),
            format!(
                "Version {installed} is installed in {dir}. Repair copies this exe again and \
                 restores the shortcut, startup entry, and Apps & features entry. \
                 Captured data and settings are kept."
            ),
            "Repair",
        ),
        Mode::Installed => (
            "Inquisitor is installed".into(),
            format!(
                "Version {VERSION} is installed in {dir}. Repair restores the Start menu \
                 shortcut, the startup entry, and the Apps & features entry."
            ),
            "Repair",
        ),
    }
}

/// Switch the visible controls and texts to `st.page`, resizing the window when
/// the page height changes.
fn show_page(st: &mut WizardState) {
    let dir = install::install_dir().display().to_string();
    let (main, remove, terms) = (
        st.page == Page::Main,
        st.page == Page::Remove,
        st.page == Page::Terms,
    );
    let (title, body, primary) = match st.page {
        Page::Main => main_page_text(&st.mode, &dir),
        Page::Remove => (
            "Remove Inquisitor".into(),
            format!(
                "Removes the app from {dir}, its Start menu shortcut, and its startup entry. \
                 Captured data and settings are kept unless you tick the box below."
            ),
            "Remove",
        ),
        Page::Terms => (
            "Before you start".into(),
            "Inquisitor learns your typing and pointer rhythm from how you use your input \
             devices. Read what it records and what it never collects. Nothing is collected \
             until you accept."
                .into(),
            "Accept",
        ),
    };
    set_text(st.title, &title);
    set_text(st.body, &body);
    set_text(st.primary, primary);
    set_text(st.cancel, if terms { "Decline" } else { "Cancel" });
    set_text(st.left, if main { "Remove..." } else { "Back" });

    set_visible(st.terms, terms);
    for h in [st.start_menu, st.startup, st.launch] {
        set_visible(h, main);
    }
    set_visible(st.delete_data, remove);
    // Back is always available on the remove page, and Remove only once there
    // is something installed to remove.
    set_visible(st.left, remove || (main && st.mode != Mode::Install));
    // The installed exe has nothing to run "once"; it is already installed.
    set_visible(st.run_once, main && st.mode != Mode::Installed);

    // Keep the top-left corner where it is and resize for the page.
    let mut rc = RECT::default();
    let (w, h) = window_size(st.dpi, st.page);
    unsafe {
        let _ = windows::Win32::UI::WindowsAndMessaging::GetWindowRect(st.hwnd, &mut rc);
        let _ = SetWindowPos(
            st.hwnd,
            None,
            rc.left,
            rc.top,
            w,
            h,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
    layout(st);
}

fn apply_fonts(st: &mut WizardState) {
    unsafe {
        if !st.font.is_invalid() {
            let _ = DeleteObject(st.font.into());
        }
        if !st.title_font.is_invalid() {
            let _ = DeleteObject(st.title_font.into());
        }
    }
    st.font = ui::font(9, FW_NORMAL.0, st.dpi);
    st.title_font = ui::font(14, FW_SEMIBOLD.0, st.dpi);
    let set = |hwnd: HWND, f: HFONT| unsafe {
        windows::Win32::UI::WindowsAndMessaging::SendMessageW(
            hwnd,
            WM_SETFONT,
            Some(WPARAM(f.0 as usize)),
            Some(LPARAM(1)),
        );
    };
    set(st.title, st.title_font);
    for h in [
        st.body,
        st.terms,
        st.start_menu,
        st.startup,
        st.launch,
        st.delete_data,
        st.left,
        st.run_once,
        st.primary,
        st.cancel,
    ] {
        set(h, st.font);
    }
}

fn layout(st: &WizardState) {
    let (w, h) = (st.scale(WIDTH), st.scale(client_height(st.page)));
    let m = st.scale(24);
    let (bh, gap) = (st.scale(32), st.scale(8));
    let title_h = st.scale(32);
    let body_top = m + title_h + st.scale(4);
    let body_h = st.scale(64);
    let check_top = body_top + body_h + st.scale(12);
    let check_h = st.scale(26);
    let by = h - m - bh;
    let (cancel_w, primary_w, run_w, left_w) =
        (st.scale(90), st.scale(100), st.scale(100), st.scale(100));
    unsafe {
        let _ = MoveWindow(st.title, m, m, w - 2 * m, title_h, true);
        let _ = MoveWindow(st.body, m, body_top, w - 2 * m, body_h, true);
        for (i, c) in [st.start_menu, st.startup, st.launch].iter().enumerate() {
            let _ = MoveWindow(
                *c,
                m,
                check_top + i as i32 * check_h,
                w - 2 * m,
                check_h,
                true,
            );
        }
        let _ = MoveWindow(st.delete_data, m, check_top, w - 2 * m, check_h, true);
        let terms_top = body_top + body_h + st.scale(8);
        let _ = MoveWindow(
            st.terms,
            m,
            terms_top,
            w - 2 * m,
            (by - st.scale(16) - terms_top).max(0),
            true,
        );
        let cancel_x = w - m - cancel_w;
        let primary_x = cancel_x - gap - primary_w;
        let _ = MoveWindow(st.cancel, cancel_x, by, cancel_w, bh, true);
        let _ = MoveWindow(st.primary, primary_x, by, primary_w, bh, true);
        let _ = MoveWindow(st.run_once, primary_x - gap - run_w, by, run_w, bh, true);
        let _ = MoveWindow(st.left, m, by, left_w, bh, true);
    }
}

fn message(hwnd: HWND, text: &str, style: MESSAGEBOX_STYLE) {
    let text = to_wide(text);
    unsafe {
        MessageBoxW(
            Some(hwnd),
            PCWSTR(text.as_ptr()),
            w!("Inquisitor setup"),
            MB_OK | style,
        );
    }
}

/// Run the page's main action. The window is disabled while it runs, since
/// closing a running tray can take a few seconds.
fn primary(hwnd: HWND, st: &mut WizardState) {
    if st.page == Page::Terms {
        accept_terms(hwnd, st);
        return;
    }
    unsafe {
        let _ = EnableWindow(hwnd, false);
        SetCursor(LoadCursorW(None, IDC_WAIT).ok());
    }
    let result = if st.page == Page::Main {
        let opts = Options {
            start_menu: is_checked(st.start_menu),
            start_with_windows: is_checked(st.startup),
            launch: is_checked(st.launch),
        };
        let done = match &st.mode {
            Mode::Install => "Inquisitor is installed.".to_string(),
            Mode::Update { installed } if installed != VERSION => {
                format!("Inquisitor is updated to {VERSION}.")
            }
            _ => "Inquisitor is repaired.".to_string(),
        };
        let tip = if opts.launch {
            "\n\nIt is now running in the system tray. If you do not see its icon \
             next to the clock, click the ^ arrow to show hidden icons."
        } else {
            ""
        };
        install::install(&opts).map(|()| format!("{done}{tip}"))
    } else {
        let delete = is_checked(st.delete_data);
        install::remove(delete).map(|removed| {
            SELF_DELETE.store(removed.self_delete_pending, Ordering::SeqCst);
            if delete {
                "Inquisitor and its data were removed.".to_string()
            } else {
                format!(
                    "Inquisitor was removed. Captured data was kept in {}.",
                    Config::load_or_default().data_dir.display()
                )
            }
        })
    };
    unsafe {
        let _ = EnableWindow(hwnd, true);
    }
    match result {
        Ok(text) => {
            message(hwnd, &text, MB_ICONINFORMATION);
            unsafe {
                let _ = DestroyWindow(hwnd);
            }
        }
        Err(text) => message(hwnd, &text, MB_ICONERROR),
    }
}

/// Record acceptance in config.ini, then continue to the main page, or straight
/// to the tray when only the terms were asked for.
fn accept_terms(hwnd: HWND, st: &mut WizardState) {
    let mut config = Config::load_or_default();
    config.terms_accepted = TERMS_VERSION;
    if let Err(e) = config.save() {
        message(
            hwnd,
            &format!("Could not save your choice: {e}"),
            MB_ICONERROR,
        );
        return;
    }
    if st.terms_only {
        RUN_TRAY.store(true, Ordering::SeqCst);
        unsafe {
            let _ = DestroyWindow(hwnd);
        }
        return;
    }
    st.page = Page::Main;
    show_page(st);
    unsafe {
        let _ = SetFocus(Some(st.primary));
    }
}

extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        let ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut WizardState;
        let Some(st) = ptr.as_mut() else {
            return DefWindowProcW(hwnd, msg, wparam, lparam);
        };
        match msg {
            WM_COMMAND => {
                match (wparam.0 & 0xFFFF) as u16 {
                    ID_PRIMARY => primary(hwnd, st),
                    ID_CANCEL => {
                        let _ = DestroyWindow(hwnd);
                    }
                    ID_RUN_ONCE => {
                        RUN_TRAY.store(true, Ordering::SeqCst);
                        let _ = DestroyWindow(hwnd);
                    }
                    ID_LEFT => {
                        st.page = if st.page == Page::Main {
                            Page::Remove
                        } else {
                            main_or_terms()
                        };
                        show_page(st);
                        let _ = SetFocus(Some(st.primary));
                    }
                    _ => {}
                }
                LRESULT(0)
            }
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN => {
                // Paint labels, checkboxes, and button corners on the window
                // colour instead of the default grey dialog face.
                let hdc = HDC(wparam.0 as *mut _);
                if HWND(lparam.0 as *mut _) == st.terms {
                    // The read-only terms box scrolls, so it needs an opaque
                    // background or old lines smear while scrolling.
                    let _ = SetBkColor(hdc, COLORREF(GetSysColor(COLOR_WINDOW)));
                    return LRESULT(GetSysColorBrush(COLOR_WINDOW).0 as isize);
                }
                let _ = SetBkMode(hdc, TRANSPARENT);
                if HWND(lparam.0 as *mut _) == st.body {
                    let _ = SetTextColor(hdc, COLORREF(GetSysColor(COLOR_GRAYTEXT)));
                }
                LRESULT(GetSysColorBrush(COLOR_WINDOW).0 as isize)
            }
            WM_DPICHANGED => {
                st.dpi = (wparam.0 >> 16) as u32 & 0xFFFF;
                apply_fonts(st);
                let r = &*(lparam.0 as *const RECT);
                let _ = SetWindowPos(
                    hwnd,
                    None,
                    r.left,
                    r.top,
                    r.right - r.left,
                    r.bottom - r.top,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
                layout(st);
                LRESULT(0)
            }
            WM_CLOSE => {
                let _ = DestroyWindow(hwnd);
                LRESULT(0)
            }
            WM_DESTROY => {
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                let st = Box::from_raw(ptr);
                let _ = DeleteObject(st.font.into());
                let _ = DeleteObject(st.title_font.into());
                PostQuitMessage(0);
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn main_page_action_follows_the_install_state() {
        let action = |mode: Mode| main_page_text(&mode, "dir").2;
        assert_eq!(action(Mode::Install), "Install");
        assert_eq!(
            action(Mode::Update {
                installed: "0.0.1-old".into()
            }),
            "Update"
        );
        assert_eq!(
            action(Mode::Update {
                installed: VERSION.into()
            }),
            "Repair"
        );
        assert_eq!(action(Mode::Installed), "Repair");
    }
}
