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
        let path = Self::default_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut f = fs::File::create(&path)?;
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
