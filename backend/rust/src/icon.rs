//! The app icon embedded by `build.rs` as resource 1.

use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Controls::{_LI_METRIC, LIM_LARGE, LIM_SMALL, LoadIconMetric};
use windows::Win32::UI::WindowsAndMessaging::{HICON, IDI_APPLICATION, LoadIconW};
use windows::core::PCWSTR;

/// Small icon (tray, title bar), sized for the system DPI.
pub fn small() -> HICON {
    load(LIM_SMALL)
}

/// Large icon (Alt+Tab), sized for the system DPI.
pub fn large() -> HICON {
    load(LIM_LARGE)
}

/// Falls back to the stock application icon when the resource is missing, as in
/// test builds, which do not link it.
fn load(metric: _LI_METRIC) -> HICON {
    unsafe {
        if let Ok(module) = GetModuleHandleW(PCWSTR::null())
            // MAKEINTRESOURCE(1): integer resource ids travel in the pointer.
            && let Ok(icon) = LoadIconMetric(Some(module.into()), PCWSTR(std::ptr::without_provenance(1)), metric)
        {
            return icon;
        }
        LoadIconW(None, IDI_APPLICATION).unwrap_or_default()
    }
}
