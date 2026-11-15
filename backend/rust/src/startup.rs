//! Windows "Run on login" integration.
//!
//! Adds or removes a value under HKCU\Software\Microsoft\Windows\CurrentVersion\Run.
//! Writing to HKCU does not require admin rights; the entry runs on user login.

use std::path::Path;

use windows::Win32::Foundation::{ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, WIN32_ERROR};
use windows::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ, RegCloseKey, RegDeleteValueW,
    RegOpenKeyExW, RegQueryValueExW, RegSetValueExW,
};
use windows::core::{Error, HRESULT, Result, w};

const RUN_KEY: windows::core::PCWSTR = w!("Software\\Microsoft\\Windows\\CurrentVersion\\Run");
const APP_NAME: windows::core::PCWSTR = w!("Inquisitor");

/// Convert a Win32 error code into a `windows::core::Result<()>`.
fn check(err: WIN32_ERROR) -> Result<()> {
    if err == ERROR_SUCCESS {
        Ok(())
    } else {
        Err(Error::from_hresult(HRESULT::from_win32(err.0)))
    }
}

/// Is Inquisitor currently registered to run at login?
pub fn is_enabled() -> bool {
    unsafe {
        let mut hkey = HKEY::default();
        if RegOpenKeyExW(HKEY_CURRENT_USER, RUN_KEY, Some(0), KEY_READ, &mut hkey) != ERROR_SUCCESS
        {
            return false;
        }
        let res = RegQueryValueExW(hkey, APP_NAME, None, None, None, None);
        let _ = RegCloseKey(hkey);
        res == ERROR_SUCCESS
    }
}

/// Add Inquisitor to HKCU\...\Run with the given exe path.
pub fn enable(exe_path: &Path) -> Result<()> {
    let wide: Vec<u16> = exe_path
        .to_string_lossy()
        .encode_utf16()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        let mut hkey = HKEY::default();
        check(RegOpenKeyExW(
            HKEY_CURRENT_USER,
            RUN_KEY,
            Some(0),
            KEY_SET_VALUE,
            &mut hkey,
        ))?;
        // RegSetValueExW takes raw bytes for REG_SZ (including the NUL terminator).
        let bytes = std::slice::from_raw_parts(wide.as_ptr() as *const u8, wide.len() * 2);
        let r = RegSetValueExW(hkey, APP_NAME, None, REG_SZ, Some(bytes));
        let _ = RegCloseKey(hkey);
        check(r)?;
    }
    Ok(())
}

/// Remove Inquisitor from HKCU\...\Run. Missing entry is treated as success.
pub fn disable() -> Result<()> {
    unsafe {
        let mut hkey = HKEY::default();
        check(RegOpenKeyExW(
            HKEY_CURRENT_USER,
            RUN_KEY,
            Some(0),
            KEY_SET_VALUE,
            &mut hkey,
        ))?;
        let r = RegDeleteValueW(hkey, APP_NAME);
        let _ = RegCloseKey(hkey);
        if r == ERROR_SUCCESS || r == ERROR_FILE_NOT_FOUND {
            Ok(())
        } else {
            check(r)
        }
    }
}
