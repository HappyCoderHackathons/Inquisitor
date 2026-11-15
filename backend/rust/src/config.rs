//! Persistent tray settings.
//!
//! Stored at `%APPDATA%\inquisitor\config.ini` in a tiny key=value format so we
//! do not pull in serde. Unknown keys are ignored on load, which keeps older
//! configs forward-compatible.

use std::collections::HashSet;
use std::fs;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

/// Everything the tray needs to remember across restarts.
pub struct Config {
    /// Directory where capture CSVs land.
    pub data_dir: PathBuf,
    /// Device system paths the user has whitelisted. Only these devices produce
    /// rows; everything else is dropped. Starts empty on first run so no device
    /// captures until the user ticks it (or Allow all) from the tray.
    pub allowed_devices: HashSet<String>,
}

impl Config {
    /// `%APPDATA%\inquisitor\config.ini`.
    pub fn default_path() -> PathBuf {
        default_app_dir().join("config.ini")
    }

    /// Load the config if it exists, otherwise return defaults (fresh install).
    pub fn load_or_default() -> Self {
        match Self::load(&Self::default_path()) {
            Ok(c) => c,
            Err(_) => Self::defaults(),
        }
    }

    pub fn defaults() -> Self {
        Self {
            data_dir: default_app_dir(),
            allowed_devices: HashSet::new(),
        }
    }

    pub fn load(path: &Path) -> io::Result<Self> {
        let f = fs::File::open(path)?;
        let reader = BufReader::new(f);
        let mut cfg = Self::defaults();
        for line in reader.lines() {
            let line = line?;
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some(eq) = line.find('=') else { continue };
            let key = line[..eq].trim();
            let val = line[eq + 1..].trim();
            match key {
                "data_dir" => cfg.data_dir = PathBuf::from(val),
                "allowed_device" => {
                    cfg.allowed_devices.insert(val.to_string());
                }
                // `allow_new_connections` and `ignored_device` from older configs
                // are silently dropped: the menu now models everything through
                // `allowed_devices` only.
                _ => {}
            }
        }
        Ok(cfg)
    }

    /// Write the config atomically enough for our needs (create+rename not used; a
    /// short file written in one call is good enough here).
    pub fn save(&self) -> io::Result<()> {
        self.save_to(&Self::default_path())
    }

    /// Write the config to an explicit path. `save` uses the default location;
    /// tests use a temp directory so they never touch the user's real config.
    pub fn save_to(&self, path: &Path) -> io::Result<()> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut f = fs::File::create(path)?;
        writeln!(f, "# Inquisitor config")?;
        writeln!(f, "data_dir={}", self.data_dir.display())?;
        for d in &self.allowed_devices {
            writeln!(f, "allowed_device={d}")?;
        }
        Ok(())
    }
}

/// `%APPDATA%\inquisitor`, or the current directory if APPDATA is unset.
pub fn default_app_dir() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("inquisitor")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Fresh per-test directory under the system temp dir, removed on drop.
    struct TempDir(PathBuf);

    impl TempDir {
        fn new(name: &str) -> Self {
            let dir =
                std::env::temp_dir().join(format!("inquisitor-test-{}-{name}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            Self(dir)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    // Real device paths contain '#', '&', '{' and spaces, so use one verbatim.
    const PATH_A: &str =
        r"\\?\HID#VID_046D&PID_C52B&MI_00#7&7988632&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}";
    const PATH_B: &str = r"\\?\Microsoft HID RID\000D_0002\0";

    #[test]
    fn defaults_start_with_an_empty_whitelist() {
        assert!(Config::defaults().allowed_devices.is_empty());
    }

    #[test]
    fn save_then_load_round_trips() {
        let tmp = TempDir::new("round-trip");
        let path = tmp.0.join("nested").join("config.ini");
        let cfg = Config {
            data_dir: PathBuf::from(r"C:\Users\Some One\Capture Data"),
            allowed_devices: [PATH_A, PATH_B].iter().map(|s| s.to_string()).collect(),
        };
        cfg.save_to(&path).unwrap();

        let loaded = Config::load(&path).unwrap();
        assert_eq!(loaded.data_dir, cfg.data_dir);
        assert_eq!(loaded.allowed_devices, cfg.allowed_devices);
    }

    #[test]
    fn load_skips_comments_blanks_and_legacy_keys() {
        let tmp = TempDir::new("legacy");
        let path = tmp.0.join("config.ini");
        let text = format!(
            "# header\n\n  data_dir = D:\\data  \nallow_new_connections=true\n\
             ignored_device={PATH_B}\nallowed_device={PATH_A}\nnot a key value line\n"
        );
        fs::write(&path, text).unwrap();

        let cfg = Config::load(&path).unwrap();
        assert_eq!(cfg.data_dir, PathBuf::from(r"D:\data"));
        // Legacy ignored_device entries must not become allowed.
        assert_eq!(cfg.allowed_devices.len(), 1);
        assert!(cfg.allowed_devices.contains(PATH_A));
    }

    #[test]
    fn hash_inside_a_value_is_not_a_comment() {
        let tmp = TempDir::new("hash");
        let path = tmp.0.join("config.ini");
        fs::write(&path, format!("allowed_device={PATH_A}\n")).unwrap();
        assert!(
            Config::load(&path)
                .unwrap()
                .allowed_devices
                .contains(PATH_A)
        );
    }

    #[test]
    fn load_reports_a_missing_file() {
        let tmp = TempDir::new("missing");
        assert!(Config::load(&tmp.0.join("nope.ini")).is_err());
    }
}
