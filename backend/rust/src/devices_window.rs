//! "Allow connections" window.
//!
//! A small resizable window with a themed list view: one row per connected input
//! device, a checkbox per row, and Allow all / Remove all buttons. A checked row
//! means the device is in `Config::allowed_devices` and produces capture rows.
//! Changes are saved to config.ini as soon as a box is ticked.
//!
//! The window keeps its own state (fonts, child handles, row paths) in its
//! `GWLP_USERDATA`, plus a raw pointer to the tray's `Config`. The tray owns the
//! config and destroys this window before freeing it, so the pointer stays valid
//! for the window's whole lifetime. Everything runs on the tray's UI thread.
//!
//! Identify mode: while the "Identify" button is toggled on, the tray forwards
//! the device path of every raw input event (allowed or not) through
//! `report_activity`, and the matching row is highlighted for a moment and
//! scrolled into view. Only the device path is used; key identities never reach
//! this window.

use std::mem::size_of;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use windows::core::{PCWSTR, PWSTR, w};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, COLOR_GRAYTEXT, COLOR_WINDOW, CreateFontW,
    DEFAULT_CHARSET, DeleteObject, FW_NORMAL, FW_SEMIBOLD, GetMonitorInfoW, GetSysColor,
    GetSysColorBrush, HDC, HFONT, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
    OUT_DEFAULT_PRECIS, SetBkMode, SetTextColor, TRANSPARENT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{
    CDDS_ITEMPREPAINT, CDDS_PREPAINT, CDRF_DODEFAULT, CDRF_NEWFONT, CDRF_NOTIFYITEMDRAW,
    ICC_LISTVIEW_CLASSES, ICC_STANDARD_CLASSES, INITCOMMONCONTROLSEX, InitCommonControlsEx,
    LVCF_TEXT, LVCF_WIDTH, LVCOLUMNW, LVIF_STATE, LVIF_TEXT, LVIS_STATEIMAGEMASK, LVITEMW,
    LVM_DELETEALLITEMS, LVM_ENSUREVISIBLE, LVM_GETITEMSTATE, LVM_INSERTCOLUMNW, LVM_INSERTITEMW,
    LVM_SETCOLUMNWIDTH, LVM_SETEXTENDEDLISTVIEWSTYLE, LVM_SETITEMSTATE, LVM_SETITEMTEXTW,
    LVN_ITEMCHANGED, LVS_EX_CHECKBOXES, LVS_EX_DOUBLEBUFFER, LVS_EX_FULLROWSELECT,
    NM_CUSTOMDRAW, NMHDR, NMLISTVIEW, NMLVCUSTOMDRAW, SetWindowTheme, WC_LISTVIEWW,
};
use windows::Win32::Graphics::Gdi::InvalidateRect;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::WindowsAndMessaging::{
    BS_PUSHBUTTON, CW_USEDEFAULT, CreateWindowExW, DefWindowProcW, DestroyWindow, GWLP_USERDATA,
    GetClientRect, GetCursorPos, GetWindowLongPtrW, HMENU, IDC_ARROW, IDI_APPLICATION, IsWindow,
    KillTimer, LoadCursorW, LoadIconW, MINMAXINFO, MoveWindow, RegisterClassExW, SW_RESTORE,
    SW_SHOW, SWP_NOACTIVATE, SWP_NOZORDER, SendMessageW, SetForegroundWindow, SetTimer,
    SetWindowLongPtrW, SetWindowPos, SetWindowTextW, ShowWindow, WINDOW_EX_STYLE, WINDOW_STYLE,
    WM_CLOSE, WM_COMMAND, WM_CTLCOLORBTN, WM_CTLCOLORSTATIC, WM_DESTROY, WM_DPICHANGED,
    WM_GETMINMAXINFO, WM_NOTIFY, WM_SETFONT, WM_SIZE, WM_TIMER, WNDCLASSEXW, WS_BORDER,
    WS_CAPTION, WS_CHILD, WS_CLIPCHILDREN, WS_OVERLAPPED, WS_SYSMENU, WS_TABSTOP, WS_THICKFRAME,
    WS_VISIBLE,
};

use crate::config::Config;
use crate::devices::{DeviceKind, list_input_devices};

const CLASS: PCWSTR = w!("InquisitorDevicesWnd");

// Child-control ids, scoped to this window.
const ID_ALLOW_ALL: u16 = 1;
const ID_REMOVE_ALL: u16 = 2;
const ID_LIST: u16 = 3;
const ID_IDENTIFY: u16 = 4;

// Identify mode: how long a row stays lit after its device's last input, how
// often expired highlights are cleared, and the highlight colour (0x00BBGGRR,
// a soft green).
const HIGHLIGHT_FOR: Duration = Duration::from_millis(700);
const FADE_TIMER_ID: usize = 1;
const FADE_TIMER_MS: u32 = 100;
const HIGHLIGHT: COLORREF = COLORREF(0x00C6_EFCE);

const HINT_IDLE: &str = "Only checked devices are recorded.";
const HINT_IDENTIFY: &str = "Use any device to highlight it below.";

// List view styles from <commctrl.h>. The windows crate exposes these as plain
// numbers in some places and typed wrappers in others, so keep them local.
const LVS_REPORT: u32 = 0x0001;
const LVS_SINGLESEL: u32 = 0x0004;
const LVS_SHOWSELALWAYS: u32 = 0x0008;
const LVS_NOSORTHEADER: u32 = 0x8000;
// Static-control styles from <winuser.h>.
const SS_NOPREFIX: u32 = 0x0080;
const SS_ENDELLIPSIS: u32 = 0x4000;

// Checkbox state-image indices stored in bits 12..15 of a list view item state.
const STATE_UNCHECKED: u32 = 1 << 12;
const STATE_CHECKED: u32 = 2 << 12;

/// One list view row. Index in `WindowState::rows` equals the list view item index.
struct Row {
    path: String,
    name: String,
    kind: String,
    id: String,
    /// Sort key so keyboards come first, then mice, then everything else.
    rank: u8,
}

struct WindowState {
    config: *mut Config,
    rows: Vec<Row>,
    title: HWND,
    hint: HWND,
    identify: HWND,
    allow_all: HWND,
    remove_all: HWND,
    list: HWND,
    font: HFONT,
    title_font: HFONT,
    dpi: u32,
    /// Set while code (not the user) changes checkboxes, so LVN_ITEMCHANGED does
    /// not rewrite the config once per row during populate / Allow all.
    suppress: bool,
    /// Identify mode is on: rows light up when their device sends input.
    listening: bool,
    /// Per-row highlight deadline, parallel to `rows`. Some(t) in the future = lit.
    lit_until: Vec<Option<Instant>>,
    /// Last time Identify mode re-enumerated devices because input came from a
    /// device missing from the list. Limits re-enumeration to once a second.
    last_refresh: Option<Instant>,
}

impl WindowState {
    fn scale(&self, v: i32) -> i32 {
        scale(v, self.dpi)
    }
}

fn scale(v: i32, dpi: u32) -> i32 {
    v * dpi as i32 / 96
}

fn to_wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Open the window, or bring an existing one forward and refresh its device list
/// (so newly plugged-in devices appear). Returns the window handle.
pub fn open(existing: Option<HWND>, config: *mut Config) -> Option<HWND> {
    if let Some(hwnd) = existing
        && unsafe { IsWindow(Some(hwnd)) }.as_bool()
    {
        unsafe {
            if let Some(st) = state_mut(hwnd) {
                populate(st);
                // Row count may have changed, which toggles the vertical scrollbar.
                layout(hwnd, st);
            }
            let _ = ShowWindow(hwnd, SW_RESTORE);
            let _ = SetForegroundWindow(hwnd);
        }
        return Some(hwnd);
    }

    init_common_controls();
    register_class();

    let hinstance = unsafe { GetModuleHandleW(PCWSTR::null()) }.ok()?;
    let hwnd = unsafe {
        CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS,
            w!("Inquisitor: Allow connections"),
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_CLIPCHILDREN,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            Some(hinstance.into()),
            None,
        )
    }
    .ok()?;

    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96);
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

    let title = child(w!("STATIC"), "Devices", SS_NOPREFIX | SS_ENDELLIPSIS, 0);
    let hint = child(w!("STATIC"), HINT_IDLE, SS_NOPREFIX | SS_ENDELLIPSIS, 0);
    let identify = child(
        w!("BUTTON"),
        "Identify",
        WS_TABSTOP.0 | BS_PUSHBUTTON as u32,
        ID_IDENTIFY,
    );
    let allow_all = child(
        w!("BUTTON"),
        "Allow all",
        WS_TABSTOP.0 | BS_PUSHBUTTON as u32,
        ID_ALLOW_ALL,
    );
    let remove_all = child(
        w!("BUTTON"),
        "Remove all",
        WS_TABSTOP.0 | BS_PUSHBUTTON as u32,
        ID_REMOVE_ALL,
    );
    let list = child(
        WC_LISTVIEWW,
        "",
        WS_TABSTOP.0
            | WS_BORDER.0
            | LVS_REPORT
            | LVS_SINGLESEL
            | LVS_SHOWSELALWAYS
            | LVS_NOSORTHEADER,
        ID_LIST,
    );

    unsafe {
        let ex = LVS_EX_CHECKBOXES | LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER;
        SendMessageW(
            list,
            LVM_SETEXTENDEDLISTVIEWSTYLE,
            Some(WPARAM(0)),
            Some(LPARAM(ex as isize)),
        );
        // "Explorer" theme gives the hover/selection highlight used by File Explorer.
        let _ = SetWindowTheme(list, w!("Explorer"), PCWSTR::null());
    }
    for (i, name) in ["Device", "Type", "ID"].iter().enumerate() {
        let wide = to_wide(name);
        let col = LVCOLUMNW {
            mask: LVCF_TEXT | LVCF_WIDTH,
            cx: scale(120, dpi),
            pszText: PWSTR(wide.as_ptr() as *mut _),
            ..Default::default()
        };
        unsafe {
            SendMessageW(
                list,
                LVM_INSERTCOLUMNW,
                Some(WPARAM(i)),
                Some(LPARAM(&col as *const _ as isize)),
            );
        }
    }

    let st = Box::new(WindowState {
        config,
        rows: Vec::new(),
        title,
        hint,
        identify,
        allow_all,
        remove_all,
        list,
        font: HFONT::default(),
        title_font: HFONT::default(),
        dpi,
        suppress: false,
        listening: false,
        lit_until: Vec::new(),
        last_refresh: None,
    });
    let st_ptr = Box::into_raw(st);
    unsafe {
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, st_ptr as isize);
    }
    let st = unsafe { &mut *st_ptr };

    apply_fonts(st);
    populate(st);
    place_centered(hwnd, st);
    layout(hwnd, st);

    unsafe {
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetForegroundWindow(hwnd);
    }
    Some(hwnd)
}

fn init_common_controls() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        let icc = INITCOMMONCONTROLSEX {
            dwSize: size_of::<INITCOMMONCONTROLSEX>() as u32,
            dwICC: ICC_LISTVIEW_CLASSES | ICC_STANDARD_CLASSES,
        };
        unsafe {
            let _ = InitCommonControlsEx(&icc);
        }
    });
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
            hIcon: LoadIconW(None, IDI_APPLICATION).unwrap_or_default(),
            hbrBackground: GetSysColorBrush(COLOR_WINDOW),
            lpszClassName: CLASS,
            ..Default::default()
        };
        RegisterClassExW(&wc);
    });
}

unsafe fn state_mut<'a>(hwnd: HWND) -> Option<&'a mut WindowState> {
    let ptr = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as *mut WindowState;
    unsafe { ptr.as_mut() }
}

/// (Re)create Segoe UI fonts for the current DPI and assign them to every child.
fn apply_fonts(st: &mut WindowState) {
    let make = |pt: i32, weight: u32| unsafe {
        CreateFontW(
            -(pt * st.dpi as i32 / 72),
            0,
            0,
            0,
            weight as i32,
            0,
            0,
            0,
            DEFAULT_CHARSET,
            OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY,
            0,
            w!("Segoe UI"),
        )
    };
    let font = make(9, FW_NORMAL.0);
    let title_font = make(14, FW_SEMIBOLD.0);
    unsafe {
        if !st.font.is_invalid() {
            let _ = DeleteObject(st.font.into());
        }
        if !st.title_font.is_invalid() {
            let _ = DeleteObject(st.title_font.into());
        }
    }
    st.font = font;
    st.title_font = title_font;

    let set = |hwnd: HWND, f: HFONT| unsafe {
        SendMessageW(
            hwnd,
            WM_SETFONT,
            Some(WPARAM(f.0 as usize)),
            Some(LPARAM(1)),
        );
    };
    set(st.title, st.title_font);
    for h in [st.hint, st.identify, st.allow_all, st.remove_all, st.list] {
        set(h, st.font);
    }
}

/// Re-enumerate devices and rebuild the list view rows.
fn populate(st: &mut WindowState) {
    let config = unsafe { &*st.config };
    let mut rows: Vec<Row> = list_input_devices()
        .unwrap_or_default()
        .into_iter()
        .map(|d| Row {
            rank: match d.kind {
                DeviceKind::Keyboard { .. } => 0,
                DeviceKind::Mouse { .. } => 1,
                _ => 2,
            },
            name: d.display_name(),
            kind: d.type_label(),
            id: d.id_label(),
            path: d.name,
        })
        .collect();
    rows.sort_by(|a, b| {
        (a.rank, &a.name, &a.kind, &a.id).cmp(&(b.rank, &b.name, &b.kind, &b.id))
    });

    st.suppress = true;
    unsafe {
        SendMessageW(st.list, LVM_DELETEALLITEMS, Some(WPARAM(0)), Some(LPARAM(0)));
    }
    for (i, row) in rows.iter().enumerate() {
        let name = to_wide(&row.name);
        let item = LVITEMW {
            mask: LVIF_TEXT,
            iItem: i as i32,
            pszText: PWSTR(name.as_ptr() as *mut _),
            ..Default::default()
        };
        unsafe {
            SendMessageW(
                st.list,
                LVM_INSERTITEMW,
                Some(WPARAM(0)),
                Some(LPARAM(&item as *const _ as isize)),
            );
        }
        for (sub, text) in [(1, &row.kind), (2, &row.id)] {
            let wide = to_wide(text);
            let item = LVITEMW {
                iSubItem: sub,
                pszText: PWSTR(wide.as_ptr() as *mut _),
                ..Default::default()
            };
            unsafe {
                SendMessageW(
                    st.list,
                    LVM_SETITEMTEXTW,
                    Some(WPARAM(i)),
                    Some(LPARAM(&item as *const _ as isize)),
                );
            }
        }
        set_checked(st.list, i as isize, config.allowed_devices.contains(&row.path));
    }
    st.suppress = false;
    st.lit_until = vec![None; rows.len()];
    st.rows = rows;
}

/// Called by the tray for every raw input event while this window exists.
/// Lights up the matching row when Identify mode is on; otherwise a no-op.
pub fn report_activity(hwnd: HWND, path: &str) {
    if !unsafe { IsWindow(Some(hwnd)) }.as_bool() {
        return;
    }
    let Some(st) = (unsafe { state_mut(hwnd) }) else {
        return;
    };
    // An empty path is input Windows synthesized without a device handle (for
    // example pointer motion from a precision touch pad); it matches no row.
    if !st.listening || path.is_empty() {
        return;
    }
    let now = Instant::now();
    let mut found = st.rows.iter().position(|r| r.path == path);
    if found.is_none() && st.last_refresh.is_none_or(|t| now - t >= Duration::from_secs(1)) {
        // A device plugged in after the list was built: re-enumerate once, keeping
        // existing highlights where the same device is still present.
        st.last_refresh = Some(now);
        let lit: Vec<(String, Option<Instant>)> = st
            .rows
            .iter()
            .map(|r| r.path.clone())
            .zip(st.lit_until.iter().copied())
            .collect();
        populate(st);
        for (p, until) in lit {
            if let Some(j) = st.rows.iter().position(|r| r.path == p) {
                st.lit_until[j] = until;
            }
        }
        layout(hwnd, st);
        found = st.rows.iter().position(|r| r.path == path);
    }
    let Some(i) = found else {
        return;
    };
    let was_lit = st.lit_until[i].is_some_and(|t| t > now);
    st.lit_until[i] = Some(now + HIGHLIGHT_FOR);
    // Mouse movement arrives hundreds of times a second; only repaint and
    // scroll when a row actually changes from unlit to lit.
    if !was_lit {
        unsafe {
            SendMessageW(st.list, LVM_ENSUREVISIBLE, Some(WPARAM(i)), Some(LPARAM(0)));
            let _ = InvalidateRect(Some(st.list), None, false);
        }
    }
}

/// Turn Identify mode on or off, updating the button, hint, and fade timer.
fn set_listening(hwnd: HWND, st: &mut WindowState, on: bool) {
    st.listening = on;
    st.lit_until.iter_mut().for_each(|t| *t = None);
    let (button, hint) = if on {
        ("Stop identifying", HINT_IDENTIFY)
    } else {
        ("Identify", HINT_IDLE)
    };
    let (button, hint) = (to_wide(button), to_wide(hint));
    unsafe {
        let _ = SetWindowTextW(st.identify, PCWSTR(button.as_ptr()));
        let _ = SetWindowTextW(st.hint, PCWSTR(hint.as_ptr()));
        if on {
            SetTimer(Some(hwnd), FADE_TIMER_ID, FADE_TIMER_MS, None);
        } else {
            let _ = KillTimer(Some(hwnd), FADE_TIMER_ID);
        }
        let _ = InvalidateRect(Some(st.list), None, false);
    }
}

fn is_lit(st: &WindowState, index: usize) -> bool {
    st.lit_until
        .get(index)
        .copied()
        .flatten()
        .is_some_and(|t| t > Instant::now())
}

/// Set one row's checkbox, or every row's when `index` is -1.
fn set_checked(list: HWND, index: isize, checked: bool) {
    let item = LVITEMW {
        stateMask: LVIS_STATEIMAGEMASK,
        state: windows::Win32::UI::Controls::LIST_VIEW_ITEM_STATE_FLAGS(if checked {
            STATE_CHECKED
        } else {
            STATE_UNCHECKED
        }),
        ..Default::default()
    };
    unsafe {
        SendMessageW(
            list,
            LVM_SETITEMSTATE,
            Some(WPARAM(index as usize)),
            Some(LPARAM(&item as *const _ as isize)),
        );
    }
}

fn is_checked(list: HWND, index: usize) -> bool {
    let state = unsafe {
        SendMessageW(
            list,
            LVM_GETITEMSTATE,
            Some(WPARAM(index)),
            Some(LPARAM(LVIS_STATEIMAGEMASK.0 as isize)),
        )
    };
    state.0 as u32 & LVIS_STATEIMAGEMASK.0 == STATE_CHECKED
}

/// Size the window for the current DPI and center it on the work area of the
/// monitor under the cursor (the one the user just clicked the tray icon on).
fn place_centered(hwnd: HWND, st: &WindowState) {
    let (w, h) = (st.scale(640), st.scale(460));
    let mut cursor = POINT::default();
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    unsafe {
        let _ = GetCursorPos(&mut cursor);
        let monitor = MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST);
        let _ = GetMonitorInfoW(monitor, &mut info);
    }
    let work = info.rcWork;
    let x = work.left + ((work.right - work.left) - w) / 2;
    let y = work.top + ((work.bottom - work.top) - h) / 2;
    unsafe {
        let _ = SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE);
    }
}

/// Position children for the current client size:
///
/// ```text
///  Devices                     [Identify] [Allow all] [Remove all]
///  Only checked devices are recorded.
///  +-----------------------------------------------------------+
///  | [x] Device name            Type          ID               |
///  +-----------------------------------------------------------+
/// ```
fn layout(hwnd: HWND, st: &WindowState) {
    let mut rc = RECT::default();
    unsafe {
        let _ = GetClientRect(hwnd, &mut rc);
    }
    let (w, h) = (rc.right, rc.bottom);
    let m = st.scale(16);
    let (bw, bh, gap) = (st.scale(100), st.scale(32), st.scale(8));
    // Wide enough for "Stop identifying" so the button does not resize on toggle.
    let iw = st.scale(130);
    let title_h = st.scale(28);
    let hint_h = st.scale(20);
    let text_w = (w - 2 * m - iw - 2 * bw - 3 * gap).max(0);
    let list_top = m + title_h + hint_h + st.scale(12);
    let by = m + st.scale(6);

    unsafe {
        let _ = MoveWindow(st.title, m, m, text_w, title_h, true);
        let _ = MoveWindow(st.hint, m, m + title_h, text_w, hint_h, true);
        let _ = MoveWindow(st.identify, w - m - 2 * bw - iw - 2 * gap, by, iw, bh, true);
        let _ = MoveWindow(st.allow_all, w - m - 2 * bw - gap, by, bw, bh, true);
        let _ = MoveWindow(st.remove_all, w - m - bw, by, bw, bh, true);
        let _ = MoveWindow(st.list, m, list_top, w - 2 * m, (h - list_top - m).max(0), true);

        // Split the list's client width (which already excludes a vertical
        // scrollbar when one is showing) so the columns never overflow:
        // Device 42%, Type 26%, ID gets the rest.
        let mut lc = RECT::default();
        let _ = GetClientRect(st.list, &mut lc);
        let inner = lc.right.max(0);
        let device = inner * 42 / 100;
        let kind = inner * 26 / 100;
        for (col, width) in [(0usize, device), (1, kind), (2, inner - device - kind)] {
            SendMessageW(
                st.list,
                LVM_SETCOLUMNWIDTH,
                Some(WPARAM(col)),
                Some(LPARAM(width as isize)),
            );
        }
    }
}

fn save(st: &WindowState) {
    let _ = unsafe { &*st.config }.save();
}

extern "system" fn wnd_proc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        let Some(st) = state_mut(hwnd) else {
            return DefWindowProcW(hwnd, msg, wparam, lparam);
        };
        match msg {
            WM_SIZE => {
                layout(hwnd, st);
                LRESULT(0)
            }
            WM_GETMINMAXINFO => {
                let info = &mut *(lparam.0 as *mut MINMAXINFO);
                info.ptMinTrackSize = POINT {
                    x: st.scale(460),
                    y: st.scale(320),
                };
                LRESULT(0)
            }
            WM_DPICHANGED => {
                // Moving between monitors with different scaling: adopt the new DPI,
                // rebuild fonts, and take the size Windows suggests.
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
                layout(hwnd, st);
                LRESULT(0)
            }
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN => {
                // Paint labels and button corners on the window colour instead of
                // the default grey dialog face.
                let hdc = HDC(wparam.0 as *mut _);
                let _ = SetBkMode(hdc, TRANSPARENT);
                if HWND(lparam.0 as *mut _) == st.hint {
                    let _ = SetTextColor(hdc, COLORREF(GetSysColor(COLOR_GRAYTEXT)));
                }
                LRESULT(GetSysColorBrush(COLOR_WINDOW).0 as isize)
            }
            WM_TIMER if wparam.0 == FADE_TIMER_ID => {
                // Clear highlights whose deadline passed; repaint only if any did.
                let now = Instant::now();
                let mut changed = false;
                for t in st.lit_until.iter_mut() {
                    if t.is_some_and(|until| until <= now) {
                        *t = None;
                        changed = true;
                    }
                }
                if changed {
                    let _ = InvalidateRect(Some(st.list), None, false);
                }
                LRESULT(0)
            }
            WM_COMMAND => {
                match (wparam.0 & 0xFFFF) as u16 {
                    ID_IDENTIFY => set_listening(hwnd, st, !st.listening),
                    ID_ALLOW_ALL => {
                        let config = &mut *st.config;
                        for row in &st.rows {
                            config.allowed_devices.insert(row.path.clone());
                        }
                        st.suppress = true;
                        set_checked(st.list, -1, true);
                        st.suppress = false;
                        save(st);
                    }
                    ID_REMOVE_ALL => {
                        // Empties the whitelist entirely, including devices that
                        // are not plugged in right now.
                        (*st.config).allowed_devices.clear();
                        st.suppress = true;
                        set_checked(st.list, -1, false);
                        st.suppress = false;
                        save(st);
                    }
                    _ => {}
                }
                LRESULT(0)
            }
            WM_NOTIFY => {
                let hdr = &*(lparam.0 as *const NMHDR);
                if hdr.idFrom == ID_LIST as usize && hdr.code == NM_CUSTOMDRAW {
                    // Paint lit rows with the highlight colour; everything else
                    // draws normally.
                    let cd = &mut *(lparam.0 as *mut NMLVCUSTOMDRAW);
                    if cd.nmcd.dwDrawStage == CDDS_PREPAINT {
                        return LRESULT(CDRF_NOTIFYITEMDRAW as isize);
                    }
                    if cd.nmcd.dwDrawStage == CDDS_ITEMPREPAINT
                        && is_lit(st, cd.nmcd.dwItemSpec)
                    {
                        cd.clrTextBk = HIGHLIGHT;
                        return LRESULT(CDRF_NEWFONT as isize);
                    }
                    return LRESULT(CDRF_DODEFAULT as isize);
                }
                if hdr.idFrom == ID_LIST as usize && hdr.code == LVN_ITEMCHANGED && !st.suppress {
                    let nm = &*(lparam.0 as *const NMLISTVIEW);
                    let image_changed =
                        (nm.uNewState ^ nm.uOldState) & LVIS_STATEIMAGEMASK.0 != 0;
                    if nm.uChanged.0 & LVIF_STATE.0 != 0
                        && image_changed
                        && let Some(row) = st.rows.get(nm.iItem as usize)
                    {
                        let config = &mut *st.config;
                        if is_checked(st.list, nm.iItem as usize) {
                            config.allowed_devices.insert(row.path.clone());
                        } else {
                            config.allowed_devices.remove(&row.path);
                        }
                        save(st);
                    }
                }
                LRESULT(0)
            }
            WM_CLOSE => {
                let _ = DestroyWindow(hwnd);
                LRESULT(0)
            }
            WM_DESTROY => {
                let _ = KillTimer(Some(hwnd), FADE_TIMER_ID);
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                let st = Box::from_raw(st as *mut WindowState);
                let _ = DeleteObject(st.font.into());
                let _ = DeleteObject(st.title_font.into());
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}
