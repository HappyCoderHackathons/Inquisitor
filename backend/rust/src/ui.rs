//! Small helpers shared by the app's windows: DPI scaling, Segoe UI fonts, and
//! centering every window on the primary screen.

use std::cell::Cell;
use std::mem::size_of;

use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW, DEFAULT_CHARSET, GetMonitorInfoW, HFONT,
    MONITOR_DEFAULTTOPRIMARY, MONITORINFO, MonitorFromPoint, OUT_DEFAULT_PRECIS,
};
use windows::Win32::System::Threading::GetCurrentThreadId;
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetWindowRect, HCBT_ACTIVATE, HHOOK, MESSAGEBOX_STYLE, MessageBoxW,
    SWP_NOACTIVATE, SWP_NOZORDER, SetWindowPos, SetWindowsHookExW, UnhookWindowsHookEx, WH_CBT,
};
use windows::core::{PCWSTR, w};

/// Convert a length in 96-DPI layout units to pixels at `dpi`.
pub fn scale(v: i32, dpi: u32) -> i32 {
    v * dpi as i32 / 96
}

pub fn to_wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Segoe UI at `pt` points for the given DPI. The caller owns the font.
pub fn font(pt: i32, weight: u32, dpi: u32) -> HFONT {
    unsafe {
        CreateFontW(
            -(pt * dpi as i32 / 72),
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
    }
}

/// Size the window to `w` x `h` pixels and center it on the primary screen's
/// work area. Every window the app opens is centered this way, never on the
/// cursor or on an owner window.
pub fn place_centered(hwnd: HWND, w: i32, h: i32) {
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    unsafe {
        // (0, 0) is always on the primary monitor.
        let monitor = MonitorFromPoint(POINT::default(), MONITOR_DEFAULTTOPRIMARY);
        let _ = GetMonitorInfoW(monitor, &mut info);
    }
    let (x, y) = centered_origin(info.rcWork, w, h);
    unsafe {
        let _ = SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE);
    }
}

/// Top-left corner that centers a `w` x `h` window in `work`.
fn centered_origin(work: RECT, w: i32, h: i32) -> (i32, i32) {
    (
        work.left + ((work.right - work.left) - w) / 2,
        work.top + ((work.bottom - work.top) - h) / 2,
    )
}

/// Center a window on the primary screen, keeping its current size.
fn center_keeping_size(hwnd: HWND) {
    let mut rc = RECT::default();
    unsafe {
        let _ = GetWindowRect(hwnd, &mut rc);
    }
    place_centered(hwnd, rc.right - rc.left, rc.bottom - rc.top);
}

thread_local! {
    static CENTER_HOOK: Cell<Option<HHOOK>> = const { Cell::new(None) };
}

/// Run `f`, centering the first window it activates on the primary screen.
/// Message boxes and the shell's folder picker place themselves relative to
/// their owner, and neither has a position option, so a CBT hook moves the
/// window as it activates. The hook is removed after that one window.
pub fn centered<T>(f: impl FnOnce() -> T) -> T {
    unsafe {
        if let Ok(hook) =
            SetWindowsHookExW(WH_CBT, Some(center_on_activate), None, GetCurrentThreadId())
        {
            CENTER_HOOK.with(|h| h.set(Some(hook)));
        }
    }
    let result = f();
    unhook();
    result
}

fn unhook() {
    if let Some(hook) = CENTER_HOOK.with(|h| h.take()) {
        unsafe {
            let _ = UnhookWindowsHookEx(hook);
        }
    }
}

extern "system" fn center_on_activate(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HCBT_ACTIVATE as i32 {
        center_keeping_size(HWND(wparam.0 as *mut _));
        unhook();
    }
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

/// A message box centered on the screen rather than on `owner`.
pub fn message_box(owner: Option<HWND>, text: &str, caption: &str, style: MESSAGEBOX_STYLE) {
    let (text, caption) = (to_wide(text), to_wide(caption));
    centered(|| unsafe {
        MessageBoxW(
            owner,
            PCWSTR(text.as_ptr()),
            PCWSTR(caption.as_ptr()),
            style,
        );
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn centered_origin_accounts_for_a_work_area_offset() {
        // A 1920x1040 work area starting at x = -1920 (a monitor left of another).
        let work = RECT {
            left: -1920,
            top: 0,
            right: 0,
            bottom: 1040,
        };
        assert_eq!(centered_origin(work, 500, 300), (-1210, 370));
    }

    #[test]
    fn scale_converts_96_dpi_layout_units() {
        assert_eq!(scale(100, 96), 100);
        assert_eq!(scale(100, 144), 150);
        assert_eq!(scale(16, 192), 32);
    }
}
