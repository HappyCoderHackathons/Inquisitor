//! Native Windows folder picker via `IFileOpenDialog`.
//!
//! Avoids pulling in `rfd` (which brings tokio and ~30 other crates for the
//! async surface we do not need).

use std::path::PathBuf;

use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx, CoTaskMemFree,
};
use windows::Win32::UI::Shell::{
    FOS_PICKFOLDERS, FileOpenDialog, IFileOpenDialog, SIGDN_FILESYSPATH,
};

/// Show a folder-picker dialog and return the chosen path, or None if the user
/// cancelled or the dialog could not be constructed.
pub fn pick_folder() -> Option<PathBuf> {
    unsafe {
        // Safe to call repeatedly; returns S_FALSE if this thread already set a mode.
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);

        let dialog: IFileOpenDialog =
            CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER).ok()?;
        dialog.SetOptions(FOS_PICKFOLDERS).ok()?;
        // Show returns an error on cancel; treat that as None. The dialog is
        // centered on the screen like the app's other windows.
        crate::ui::centered(|| dialog.Show(None)).ok()?;
        let item = dialog.GetResult().ok()?;
        let path_pwstr = item.GetDisplayName(SIGDN_FILESYSPATH).ok()?;
        let s = path_pwstr.to_string().ok();
        // The string was allocated by the shell with CoTaskMemAlloc; free it.
        CoTaskMemFree(Some(path_pwstr.0.cast()));
        s.map(PathBuf::from)
    }
}
