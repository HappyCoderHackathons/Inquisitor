//! Per-user install, update, and removal.
//!
//! Inquisitor installs for the current user only, so no administrator rights are
//! needed:
//!
//! - the exe is copied to `%LOCALAPPDATA%\Programs\Inquisitor\Inquisitor.exe`
//! - an "Apps & features" entry is written under
//!   `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inquisitor`
//! - an optional Start menu shortcut goes in the user's Start menu Programs folder
//! - "Start with Windows" uses the same Run entry as the tray toggle
//!
//! Captured data and `config.ini` live in `%APPDATA%\inquisitor` (or the chosen
//! data folder) and are kept on update. Removal keeps them too, unless the user
//! asks for them to be deleted.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{
    CloseHandle, ERROR_FILE_NOT_FOUND, ERROR_SUCCESS, LPARAM, WPARAM,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx, IPersistFile,
};
use windows::Win32::System::Registry::{
    HKEY, HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_DWORD, REG_OPTION_NON_VOLATILE, REG_SZ,
    REG_VALUE_TYPE, RegCloseKey, RegCreateKeyExW, RegDeleteKeyW, RegOpenKeyExW, RegQueryValueExW,
    RegSetValueExW,
};
use windows::Win32::System::Threading::{OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject};
use windows::Win32::UI::Shell::{IShellLinkW, ShellLink};
use windows::Win32::UI::WindowsAndMessaging::{
    FindWindowExW, GetWindowThreadProcessId, HWND_MESSAGE, PostMessageW, WM_CLOSE,
};
use windows::core::{HSTRING, Interface, PCWSTR, w};

use crate::config::{Config, default_app_dir};
use crate::startup;
use crate::tray;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

const UNINSTALL_KEY: PCWSTR =
    w!("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Inquisitor");

/// What the wizard offers, decided from the install state and which copy of the
/// exe is running.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    /// Nothing installed yet.
    Install,
    /// Installed, and this is a different copy (a new download or a dev build).
    /// `installed` is the version recorded in Apps & features.
    Update { installed: String },
    /// This process is the installed exe (opened from Apps & features, or with
    /// `setup`). It can repair its registration or remove itself.
    Installed,
}

pub fn mode() -> Mode {
    let running_installed = std::env::current_exe()
        .map(|exe| same_file(&exe, &installed_exe()))
        .unwrap_or(false);
    mode_for(installed_version(), running_installed)
}

fn mode_for(installed: Option<String>, running_installed: bool) -> Mode {
    match installed {
        _ if running_installed => Mode::Installed,
        Some(installed) => Mode::Update { installed },
        None => Mode::Install,
    }
}

/// Options picked in the wizard.
pub struct Options {
    /// Install folder, already passed through `normalize_install_dir`.
    pub dir: PathBuf,
    pub start_menu: bool,
    pub start_with_windows: bool,
    pub launch: bool,
}

/// `%LOCALAPPDATA%\Programs\Inquisitor`, the usual place for per-user apps.
pub fn default_install_dir() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(default_app_dir);
    base.join("Programs").join("Inquisitor")
}

/// Where Inquisitor is installed: the folder recorded in Apps & features, or
/// the default for a fresh install.
pub fn install_dir() -> PathBuf {
    read_string(UNINSTALL_KEY, w!("InstallLocation"))
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(default_install_dir)
}

/// Turn a folder the user typed or picked into the install folder. The path
/// must be absolute. An `Inquisitor` folder is added unless the path already
/// ends in one, so the app never shares a folder with other files and removal
/// can never touch a folder like Documents.
pub fn normalize_install_dir(input: &str) -> Option<PathBuf> {
    let path = PathBuf::from(input.trim().trim_matches('"'));
    if !path.is_absolute() {
        return None;
    }
    let named_ours = path
        .file_name()
        .is_some_and(|n| n.to_string_lossy().eq_ignore_ascii_case("Inquisitor"));
    Some(if named_ours {
        path
    } else {
        path.join("Inquisitor")
    })
}

/// Free space for the current user on the drive holding `dir`, in bytes. Walks
/// up to the nearest folder that exists, since the install folder may not yet.
pub fn free_space(dir: &Path) -> Option<u64> {
    let existing = dir.ancestors().find(|p| p.exists())?;
    let mut free = 0u64;
    unsafe {
        GetDiskFreeSpaceExW(
            &HSTRING::from(existing.as_os_str()),
            Some(&mut free),
            None,
            None,
        )
        .ok()?;
    }
    Some(free)
}

/// Size of this exe, which is everything the install copies.
pub fn required_space() -> u64 {
    std::env::current_exe()
        .and_then(fs::metadata)
        .map(|m| m.len())
        .unwrap_or(0)
}

pub fn installed_exe() -> PathBuf {
    install_dir().join("Inquisitor.exe")
}

/// `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Inquisitor.lnk`.
fn shortcut_path() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_default();
    base.join(r"Microsoft\Windows\Start Menu\Programs\Inquisitor.lnk")
}

pub fn has_shortcut() -> bool {
    shortcut_path().exists()
}

/// Version recorded in Apps & features, or None when not installed. An entry
/// whose exe has been deleted by hand counts as not installed.
pub fn installed_version() -> Option<String> {
    if !installed_exe().exists() {
        return None;
    }
    read_string(UNINSTALL_KEY, w!("DisplayVersion"))
}

/// Copy this exe into place, register it, and apply the options. Used for
/// install, update, and repair. Any running tray is closed first so its exe can
/// be replaced and so only one instance captures afterwards. When this process
/// already is the installed exe (repair from Apps & features), the copy is
/// skipped and everything else is rewritten.
pub fn install(opts: &Options) -> Result<(), String> {
    let source =
        std::env::current_exe().map_err(|e| format!("Could not find this program: {e}"))?;
    let target = installed_exe();
    stop_running_trays();

    if !same_file(&source, &target) {
        fs::create_dir_all(install_dir())
            .map_err(|e| format!("Could not create {}: {e}", install_dir().display()))?;
        copy_with_retry(&source, &target)
            .map_err(|e| format!("Could not copy to {}: {e}", target.display()))?;
    }

    write_uninstall_entry(&target).map_err(|e| format!("Could not register the app: {e}"))?;

    if opts.start_menu {
        create_shortcut(&target)
            .map_err(|e| format!("Could not add the Start menu shortcut: {e}"))?;
    } else {
        let _ = fs::remove_file(shortcut_path());
    }

    // Rewrite the Run entry either way, so an entry left by an older copy now
    // points at the installed exe.
    let startup = if opts.start_with_windows {
        startup::enable(&target)
    } else {
        startup::disable()
    };
    startup.map_err(|e| format!("Could not update Start with Windows: {e}"))?;

    if opts.launch {
        std::process::Command::new(&target)
            .arg("tray")
            .spawn()
            .map_err(|e| format!("Installed, but could not start it: {e}"))?;
    }
    Ok(())
}

/// What `remove` left for the caller to finish.
pub struct Removed {
    /// This process is the installed exe, which cannot delete itself while it
    /// runs. Call `delete_install_dir_after_exit` right before exiting.
    pub self_delete_pending: bool,
}

/// Undo everything `install` did. Captured data and settings are deleted only
/// when `delete_data` is set.
pub fn remove(delete_data: bool) -> Result<Removed, String> {
    stop_running_trays();
    startup::disable().map_err(|e| format!("Could not remove Start with Windows: {e}"))?;
    let _ = fs::remove_file(shortcut_path());
    delete_key(UNINSTALL_KEY).map_err(|e| format!("Could not unregister the app: {e}"))?;

    if delete_data {
        let config = Config::load_or_default();
        delete_data_files(&config.data_dir, &default_app_dir())
            .map_err(|e| format!("Could not delete captured data: {e}"))?;
    }

    let dir = install_dir();
    let running_inside = std::env::current_exe()
        .map(|exe| exe.parent().is_some_and(|p| same_file(p, &dir)))
        .unwrap_or(false);
    if running_inside {
        return Ok(Removed {
            self_delete_pending: true,
        });
    }
    match fs::remove_dir_all(&dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(format!("Could not delete {}: {e}", dir.display())),
    }
    Ok(Removed {
        self_delete_pending: false,
    })
}

/// Start a hidden `cmd` that waits for this process to exit and then deletes the
/// install folder. Used when the uninstall runs from the installed exe itself.
pub fn delete_install_dir_after_exit() {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let dir = install_dir();
    // Retry for up to ~10 s in case the process is slow to exit.
    let script = format!(
        "/d /c for /l %i in (1,1,10) do (ping -n 2 127.0.0.1 >nul & rmdir /s /q \"{0}\" 2>nul & if not exist \"{0}\" exit)",
        dir.display()
    );
    let _ = std::process::Command::new("cmd.exe")
        .raw_arg(script)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn();
}

/// Delete capture CSVs and `config.ini`, then the folders if they end up empty.
/// Only files Inquisitor writes are touched, because the data folder can be any
/// folder the user picked (Documents, for example).
fn delete_data_files(data_dir: &Path, app_dir: &Path) -> std::io::Result<()> {
    for dir in [data_dir, app_dir] {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries {
            let path = entry?.path();
            let name = path.file_name().unwrap_or_default().to_string_lossy();
            let ours = (name.starts_with("capture-") && name.ends_with(".csv"))
                || (dir == app_dir && name == "config.ini");
            if ours && path.is_file() {
                fs::remove_file(&path)?;
            }
        }
    }
    for dir in [data_dir, app_dir] {
        // Fails harmlessly when the folder still holds other files.
        let _ = fs::remove_dir(dir);
    }
    Ok(())
}

/// Ask every running tray (installed or not) to quit, and wait briefly for each
/// process to exit so its exe is no longer locked.
fn stop_running_trays() {
    let own_pid = std::process::id();
    let mut handles = Vec::new();
    unsafe {
        let mut after = None;
        while let Ok(hwnd) =
            FindWindowExW(Some(HWND_MESSAGE), after, tray::CLASS_NAME, PCWSTR::null())
        {
            after = Some(hwnd);
            let mut pid = 0;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid == own_pid {
                continue;
            }
            if let Ok(process) = OpenProcess(PROCESS_SYNCHRONIZE, false, pid) {
                handles.push(process);
            }
            // The tray treats WM_CLOSE like Quit: it removes its icon and exits.
            let _ = PostMessageW(Some(hwnd), WM_CLOSE, WPARAM(0), LPARAM(0));
        }
        for process in handles {
            let _ = WaitForSingleObject(process, 5000);
            let _ = CloseHandle(process);
        }
    }
}

/// `fs::copy`, retried for a few seconds while the target is still locked by a
/// process that is shutting down.
fn copy_with_retry(source: &Path, target: &Path) -> std::io::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        match fs::copy(source, target) {
            Ok(_) => return Ok(()),
            Err(_) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(250)),
            Err(e) => return Err(e),
        }
    }
}

/// Paths are compared case-insensitively after resolving them, since Windows
/// paths are case-insensitive and may be reached through different spellings.
fn same_file(a: &Path, b: &Path) -> bool {
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a
            .to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy()),
        _ => false,
    }
}

/// `"C:\...\Inquisitor.exe" <arg>`, a command Apps & features runs.
fn exe_command(exe: &Path, arg: &str) -> String {
    format!("\"{}\" {arg}", exe.display())
}

fn write_uninstall_entry(exe: &Path) -> windows::core::Result<()> {
    let size_kb = fs::metadata(exe).map(|m| m.len() / 1024).unwrap_or(0) as u32;
    unsafe {
        let mut hkey = HKEY::default();
        startup::check(RegCreateKeyExW(
            HKEY_CURRENT_USER,
            UNINSTALL_KEY,
            None,
            PCWSTR::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_SET_VALUE,
            None,
            &mut hkey,
            None,
        ))?;
        let exe_str = exe.display().to_string();
        let result = (|| {
            set_string(hkey, w!("DisplayName"), "Inquisitor")?;
            set_string(hkey, w!("DisplayVersion"), VERSION)?;
            set_string(hkey, w!("DisplayIcon"), &format!("{exe_str},0"))?;
            set_string(
                hkey,
                w!("InstallLocation"),
                &install_dir().display().to_string(),
            )?;
            set_string(hkey, w!("UninstallString"), &exe_command(exe, "uninstall"))?;
            // "Modify" in Apps & features opens the wizard, which offers Repair.
            set_string(hkey, w!("ModifyPath"), &exe_command(exe, "setup"))?;
            set_dword(hkey, w!("NoModify"), 0)?;
            set_dword(hkey, w!("NoRepair"), 1)?;
            set_dword(hkey, w!("EstimatedSize"), size_kb)
        })();
        let _ = RegCloseKey(hkey);
        result
    }
}

unsafe fn set_string(hkey: HKEY, name: PCWSTR, value: &str) -> windows::core::Result<()> {
    let wide: Vec<u16> = value.encode_utf16().chain(std::iter::once(0)).collect();
    // REG_SZ data is raw bytes, including the NUL terminator.
    let bytes = unsafe { std::slice::from_raw_parts(wide.as_ptr() as *const u8, wide.len() * 2) };
    startup::check(unsafe { RegSetValueExW(hkey, name, None, REG_SZ, Some(bytes)) })
}

unsafe fn set_dword(hkey: HKEY, name: PCWSTR, value: u32) -> windows::core::Result<()> {
    let bytes = value.to_le_bytes();
    startup::check(unsafe { RegSetValueExW(hkey, name, None, REG_DWORD, Some(&bytes)) })
}

fn read_string(key: PCWSTR, name: PCWSTR) -> Option<String> {
    unsafe {
        let mut hkey = HKEY::default();
        if RegOpenKeyExW(HKEY_CURRENT_USER, key, Some(0), KEY_READ, &mut hkey) != ERROR_SUCCESS {
            return None;
        }
        let mut buf = [0u16; 128];
        let mut len = (buf.len() * 2) as u32;
        let mut kind = REG_VALUE_TYPE::default();
        let r = RegQueryValueExW(
            hkey,
            name,
            None,
            Some(&mut kind),
            Some(buf.as_mut_ptr() as *mut u8),
            Some(&mut len),
        );
        let _ = RegCloseKey(hkey);
        if r != ERROR_SUCCESS || kind != REG_SZ {
            return None;
        }
        let chars = (len as usize / 2).min(buf.len());
        let s = String::from_utf16_lossy(&buf[..chars]);
        Some(s.trim_end_matches('\0').to_string())
    }
}

fn delete_key(key: PCWSTR) -> windows::core::Result<()> {
    let r = unsafe { RegDeleteKeyW(HKEY_CURRENT_USER, key) };
    if r == ERROR_FILE_NOT_FOUND {
        Ok(())
    } else {
        startup::check(r)
    }
}

fn create_shortcut(exe: &Path) -> windows::core::Result<()> {
    let path = shortcut_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    unsafe {
        // Safe to call repeatedly; returns S_FALSE if this thread already set a mode.
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        let link: IShellLinkW = CoCreateInstance(&ShellLink, None, CLSCTX_INPROC_SERVER)?;
        link.SetPath(&HSTRING::from(exe.as_os_str()))?;
        link.SetArguments(w!("tray"))?;
        link.SetDescription(w!("Inquisitor tray app"))?;
        if let Some(dir) = exe.parent() {
            link.SetWorkingDirectory(&HSTRING::from(dir.as_os_str()))?;
        }
        link.cast::<IPersistFile>()?
            .Save(&HSTRING::from(path.as_os_str()), true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mode_depends_on_install_state_and_running_copy() {
        assert_eq!(mode_for(None, false), Mode::Install);
        assert_eq!(
            mode_for(Some("0.1.0".into()), false),
            Mode::Update {
                installed: "0.1.0".into()
            }
        );
        assert_eq!(mode_for(Some("0.1.0".into()), true), Mode::Installed);
    }

    #[test]
    fn exe_command_quotes_paths_with_spaces() {
        let exe = Path::new(r"C:\Users\Some One\AppData\Local\Programs\Inquisitor\Inquisitor.exe");
        assert_eq!(
            exe_command(exe, "uninstall"),
            r#""C:\Users\Some One\AppData\Local\Programs\Inquisitor\Inquisitor.exe" uninstall"#
        );
    }

    #[test]
    fn delete_data_removes_only_inquisitor_files() {
        let root =
            std::env::temp_dir().join(format!("inquisitor-test-{}-remove", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let data = root.join("picked folder");
        let app = root.join("inquisitor");
        fs::create_dir_all(&data).unwrap();
        fs::create_dir_all(&app).unwrap();
        fs::write(data.join("capture-20261115T031319Z.csv"), "x").unwrap();
        fs::write(data.join("notes.txt"), "keep").unwrap();
        fs::write(data.join("config.ini"), "keep").unwrap();
        fs::write(app.join("capture-20261115T031320Z.csv"), "x").unwrap();
        fs::write(app.join("config.ini"), "x").unwrap();

        delete_data_files(&data, &app).unwrap();

        // The picked folder keeps the user's own files, including one that only
        // shares a name with ours.
        assert!(data.join("notes.txt").exists());
        assert!(data.join("config.ini").exists());
        assert!(!data.join("capture-20261115T031319Z.csv").exists());
        // The app folder held only our files, so it is gone.
        assert!(!app.exists());
        let _ = fs::remove_dir_all(&root);
    }
}
