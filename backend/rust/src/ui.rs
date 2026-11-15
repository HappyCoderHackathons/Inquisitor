//! Small helpers shared by the app's windows: DPI scaling, Segoe UI fonts, and
//! centering on the monitor the user is working on.

use std::mem::size_of;

use windows::Win32::Foundation::{HWND, POINT};
use windows::Win32::Graphics::Gdi::{
    CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW, DEFAULT_CHARSET, GetMonitorInfoW, HFONT,
    MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint, OUT_DEFAULT_PRECIS,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetCursorPos, SWP_NOACTIVATE, SWP_NOZORDER, SetWindowPos,
};
use windows::core::w;

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

/// Size the window to `w` x `h` pixels and center it on the work area of the
/// monitor under the cursor (the one the user just clicked on).
pub fn place_centered(hwnd: HWND, w: i32, h: i32) {
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

#[cfg(test)]
mod tests {
    use super::scale;

    #[test]
    fn scale_converts_96_dpi_layout_units() {
        assert_eq!(scale(100, 96), 100);
        assert_eq!(scale(100, 144), 150);
        assert_eq!(scale(16, 192), 32);
    }
}
