//! "Connect to your account" dialog.
//!
//! The user creates a pairing code on the website (Setup page) and types it here
//! with the website's address. The code is checked against the server's format
//! before anything is sent, then redeemed on a worker thread so the tray stays
//! responsive. On success the device credential is saved (encrypted, see
//! `credential.rs`) and the tray is told through `WM_PAIRED`.

use std::mem::size_of;
use std::sync::atomic::{AtomicIsize, Ordering};
use std::sync::{Mutex, OnceLock};

use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    COLOR_GRAYTEXT, COLOR_WINDOW, DeleteObject, FW_NORMAL, FW_SEMIBOLD, GetSysColor,
    GetSysColorBrush, HDC, HFONT, SetBkMode, SetTextColor, TRANSPARENT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::{AdjustWindowRectExForDpi, GetDpiForWindow};
use windows::Win32::UI::Input::KeyboardAndMouse::{EnableWindow, SetFocus};
use windows::Win32::UI::WindowsAndMessaging::{
    BS_DEFPUSHBUTTON, BS_PUSHBUTTON, CW_USEDEFAULT, CreateWindowExW, DefWindowProcW, DestroyWindow,
    GWLP_USERDATA, GetWindowLongPtrW, GetWindowTextLengthW, GetWindowTextW, HMENU, IDC_ARROW,
    IsWindow, LoadCursorW, MoveWindow, PostMessageW, RegisterClassExW, SW_SHOW, SendMessageW,
    SetForegroundWindow, SetWindowLongPtrW, SetWindowTextW, ShowWindow, WINDOW_EX_STYLE,
    WINDOW_STYLE, WM_APP, WM_CLOSE, WM_COMMAND, WM_CTLCOLORBTN, WM_CTLCOLORSTATIC, WM_DESTROY,
    WM_SETFONT, WNDCLASSEXW, WS_BORDER, WS_CAPTION, WS_CHILD, WS_CLIPCHILDREN, WS_OVERLAPPED,
    WS_SYSMENU, WS_TABSTOP, WS_VISIBLE,
};
use windows::core::{PCWSTR, w};

use crate::credential::{self, Credential};
use crate::http::Endpoint;
use crate::link;
use crate::ui::{self, scale, to_wide};

/// Posted to the tray after a successful pairing; `take_paired` returns the result.
pub const WM_PAIRED: u32 = WM_APP + 3;
// Posted to this window by the worker thread when the pairing request finishes.
const WM_PAIR_DONE: u32 = WM_APP + 4;

const CLASS: PCWSTR = w!("InquisitorPairWnd");
// IDOK and IDCANCEL, so Enter and Escape work through IsDialogMessage.
const ID_CONNECT: u16 = 1;
const ID_CANCEL: u16 = 2;
const ID_URL: u16 = 10;
const ID_CODE: u16 = 11;

const SS_NOPREFIX: u32 = 0x0080;
const ES_AUTOHSCROLL: u32 = 0x0080;
const ES_UPPERCASE: u32 = 0x0008;

// Client area in 96-DPI layout units.
const WIDTH: i32 = 460;
const HEIGHT: i32 = 330;

const HINT: &str = "Sign in on the website, open Setup, and create a pairing code. \
                    A code works once and expires after 10 minutes.";

static OPEN: AtomicIsize = AtomicIsize::new(0);
static PAIRED: Mutex<Option<(Endpoint, Credential)>> = Mutex::new(None);
static RESULT: Mutex<Option<Result<Credential, String>>> = Mutex::new(None);

struct PairState {
    tray: HWND,
    endpoint: Option<Endpoint>,
    dpi: u32,
    font: HFONT,
    title_font: HFONT,
    title: HWND,
    hint: HWND,
    url_label: HWND,
    url: HWND,
    code_label: HWND,
    code: HWND,
    message: HWND,
    connect: HWND,
    cancel: HWND,
}

/// The dialog's window if it is open, for the tray's IsDialogMessage call.
pub fn current() -> Option<HWND> {
    let raw = OPEN.load(Ordering::SeqCst);
    let hwnd = HWND(raw as *mut _);
    (raw != 0 && unsafe { IsWindow(Some(hwnd)) }.as_bool()).then_some(hwnd)
}

/// The website and credential from the last successful pairing, once.
pub fn take_paired() -> Option<(Endpoint, Credential)> {
    PAIRED.lock().ok()?.take()
}

/// Open the dialog, or bring it forward if it is already open.
pub fn open(tray: HWND, server_url: Option<&str>) {
    if let Some(hwnd) = current() {
        unsafe {
            let _ = SetForegroundWindow(hwnd);
        }
        return;
    }
    register_class();
    let Ok(hinstance) = (unsafe { GetModuleHandleW(PCWSTR::null()) }) else {
        return;
    };
    let style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_CLIPCHILDREN;
    let Ok(hwnd) = (unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS,
            w!("Inquisitor: Connect to your account"),
            style,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            Some(hinstance.into()),
            None,
        )
    }) else {
        return;
    };
    let child = |class: PCWSTR, text: &str, style: u32, id: u16| -> HWND {
        let wide = to_wide(text);
        unsafe {
            CreateWindowExW(
                WINDOW_EX_STYLE(0),
                class,
                PCWSTR(wide.as_ptr()),
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
    let field = ES_AUTOHSCROLL | WS_BORDER.0 | WS_TABSTOP.0;
    let st = Box::new(PairState {
        tray,
        endpoint: None,
        dpi: unsafe { GetDpiForWindow(hwnd) }.max(96),
        font: HFONT::default(),
        title_font: HFONT::default(),
        title: child(w!("STATIC"), "Connect to your account", SS_NOPREFIX, 0),
        hint: child(w!("STATIC"), HINT, SS_NOPREFIX, 0),
        url_label: child(w!("STATIC"), "Website address", SS_NOPREFIX, 0),
        url: child(w!("EDIT"), server_url.unwrap_or("https://"), field, ID_URL),
        code_label: child(w!("STATIC"), "Pairing code", SS_NOPREFIX, 0),
        code: child(w!("EDIT"), "", field | ES_UPPERCASE, ID_CODE),
        message: child(w!("STATIC"), "", SS_NOPREFIX, 0),
        connect: child(
            w!("BUTTON"),
            "Connect",
            BS_DEFPUSHBUTTON as u32 | WS_TABSTOP.0,
            ID_CONNECT,
        ),
        cancel: child(
            w!("BUTTON"),
            "Cancel",
            BS_PUSHBUTTON as u32 | WS_TABSTOP.0,
            ID_CANCEL,
        ),
    });
    let dpi = st.dpi;
    let st_ptr = Box::into_raw(st);
    unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, st_ptr as isize) };
    let st = unsafe { &mut *st_ptr };
    apply_fonts(st);

    let mut rc = RECT {
        left: 0,
        top: 0,
        right: scale(WIDTH, dpi),
        bottom: scale(HEIGHT, dpi),
    };
    unsafe {
        let _ = AdjustWindowRectExForDpi(&mut rc, style, false, WINDOW_EX_STYLE(0), dpi);
    }
    ui::place_centered(hwnd, rc.right - rc.left, rc.bottom - rc.top);
    layout(st);
    OPEN.store(hwnd.0 as isize, Ordering::SeqCst);
    unsafe {
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetForegroundWindow(hwnd);
        // Start where the user has to type: the code when the address is known.
        let _ = SetFocus(Some(if server_url.is_some() {
            st.code
        } else {
            st.url
        }));
    }
}

fn register_class() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| unsafe {
        let hinstance = GetModuleHandleW(PCWSTR::null()).unwrap_or_default();
        let wc = WNDCLASSEXW {
            cbSize: size_of::<WNDCLASSEXW>() as u32,
            lpfnWndProc: Some(wnd_proc),
            hInstance: hinstance.into(),
            hCursor: LoadCursorW(None, IDC_ARROW).unwrap_or_default(),
            hIcon: crate::icon::large(),
            hIconSm: crate::icon::small(),
            hbrBackground: GetSysColorBrush(COLOR_WINDOW),
            lpszClassName: CLASS,
            ..Default::default()
        };
        RegisterClassExW(&wc);
    });
}

fn apply_fonts(st: &mut PairState) {
    st.font = ui::font(9, FW_NORMAL.0, st.dpi);
    st.title_font = ui::font(14, FW_SEMIBOLD.0, st.dpi);
    let set = |hwnd: HWND, f: HFONT| unsafe {
        SendMessageW(
            hwnd,
            WM_SETFONT,
            Some(WPARAM(f.0 as usize)),
            Some(LPARAM(1)),
        );
    };
    set(st.title, st.title_font);
    for h in [
        st.hint,
        st.url_label,
        st.url,
        st.code_label,
        st.code,
        st.message,
        st.connect,
        st.cancel,
    ] {
        set(h, st.font);
    }
}

fn layout(st: &PairState) {
    let s = |v| scale(v, st.dpi);
    let (w, h) = (s(WIDTH), s(HEIGHT));
    let m = s(24);
    let inner = w - 2 * m;
    unsafe {
        let _ = MoveWindow(st.title, m, m, inner, s(32), true);
        let _ = MoveWindow(st.hint, m, s(60), inner, s(40), true);
        let _ = MoveWindow(st.url_label, m, s(110), inner, s(20), true);
        let _ = MoveWindow(st.url, m, s(132), inner, s(28), true);
        let _ = MoveWindow(st.code_label, m, s(170), inner, s(20), true);
        let _ = MoveWindow(st.code, m, s(192), s(160), s(28), true);
        let _ = MoveWindow(st.message, m, s(228), inner, s(36), true);
        let (bw, bh, gap) = (s(100), s(32), s(8));
        let _ = MoveWindow(st.cancel, w - m - bw, h - m - bh, bw, bh, true);
        let _ = MoveWindow(st.connect, w - m - 2 * bw - gap, h - m - bh, bw, bh, true);
    }
}

fn text(hwnd: HWND) -> String {
    unsafe {
        let len = GetWindowTextLengthW(hwnd).max(0) as usize;
        let mut buf = vec![0u16; len + 1];
        let n = GetWindowTextW(hwnd, &mut buf).max(0) as usize;
        String::from_utf16_lossy(&buf[..n])
    }
}

fn set_text(hwnd: HWND, value: &str) {
    let wide = to_wide(value);
    unsafe {
        let _ = SetWindowTextW(hwnd, PCWSTR(wide.as_ptr()));
    }
}

fn busy(st: &PairState, busy: bool) {
    unsafe {
        for h in [st.url, st.code, st.connect] {
            let _ = EnableWindow(h, !busy);
        }
    }
}

/// Validate the fields, then redeem the code on a worker thread.
fn connect(hwnd: HWND, st: &mut PairState) {
    let endpoint = match Endpoint::parse(&text(st.url)) {
        Ok(endpoint) => endpoint,
        Err(e) => return set_text(st.message, &e),
    };
    let Some(code) = link::normalize_code(&text(st.code)) else {
        return set_text(
            st.message,
            "The code is 8 letters and numbers, as shown on the website.",
        );
    };
    set_text(st.message, "Connecting...");
    busy(st, true);
    st.endpoint = Some(endpoint.clone());
    let target = hwnd.0 as isize;
    std::thread::spawn(move || {
        let result = link::pair(&endpoint, &code);
        if let Ok(mut slot) = RESULT.lock() {
            *slot = Some(result);
        }
        unsafe {
            let _ = PostMessageW(
                Some(HWND(target as *mut _)),
                WM_PAIR_DONE,
                WPARAM(0),
                LPARAM(0),
            );
        }
    });
}

fn finished(hwnd: HWND, st: &mut PairState) {
    let result = RESULT.lock().ok().and_then(|mut slot| slot.take());
    busy(st, false);
    match (result, st.endpoint.take()) {
        (Some(Ok(credential)), Some(endpoint)) => {
            if let Err(e) = credential::save(&credential) {
                return set_text(st.message, &format!("Could not save the connection: {e}"));
            }
            if let Ok(mut slot) = PAIRED.lock() {
                *slot = Some((endpoint, credential));
            }
            unsafe {
                let _ = PostMessageW(Some(st.tray), WM_PAIRED, WPARAM(0), LPARAM(0));
                let _ = DestroyWindow(hwnd);
            }
        }
        (Some(Err(e)), _) => set_text(st.message, &e),
        _ => {}
    }
}

extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        let ptr = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut PairState;
        let Some(st) = ptr.as_mut() else {
            return DefWindowProcW(hwnd, msg, wparam, lparam);
        };
        match msg {
            WM_COMMAND => {
                match (wparam.0 & 0xFFFF) as u16 {
                    ID_CONNECT => connect(hwnd, st),
                    ID_CANCEL => {
                        let _ = DestroyWindow(hwnd);
                    }
                    _ => {}
                }
                LRESULT(0)
            }
            WM_PAIR_DONE => {
                finished(hwnd, st);
                LRESULT(0)
            }
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN => {
                let hdc = HDC(wparam.0 as *mut _);
                let _ = SetBkMode(hdc, TRANSPARENT);
                if HWND(lparam.0 as *mut _) == st.hint {
                    let _ = SetTextColor(hdc, COLORREF(GetSysColor(COLOR_GRAYTEXT)));
                }
                LRESULT(GetSysColorBrush(COLOR_WINDOW).0 as isize)
            }
            WM_CLOSE => {
                let _ = DestroyWindow(hwnd);
                LRESULT(0)
            }
            WM_DESTROY => {
                OPEN.store(0, Ordering::SeqCst);
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                let st = Box::from_raw(ptr);
                let _ = DeleteObject(st.font.into());
                let _ = DeleteObject(st.title_font.into());
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}
