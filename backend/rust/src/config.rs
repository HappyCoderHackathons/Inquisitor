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
    /// Allow raw input from devices the user has not explicitly approved.
    /// `false` by default per the user's requested behaviour.
    pub allow_new_connections: bool,
    /// Device system paths captured at first run. Any input from a path in this
    /// set is accepted; paths outside it need `allow_new_connections`.
    pub allowed_devices: HashSet<String>,
    /// Devices the user has explicitly silenced. Overrides `allowed_devices`.
    pub ignored_devices: HashSet<String>,
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
            allow_new_connections: false,
            allowed_devices: HashSet::new(),
            ignored_devices: HashSet::new(),
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
                "allow_new_connections" => cfg.allow_new_connections = val == "true",
                "allowed_device" => {
                    cfg.allowed_devices.insert(val.to_string());
                }
                "ignored_device" => {
                    cfg.ignored_devices.insert(val.to_string());
                }
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
        writeln!(f, "allow_new_connections={}", self.allow_new_connections)?;
        for d in &self.allowed_devices {
            writeln!(f, "allowed_device={d}")?;
        }
        for d in &self.ignored_devices {
            writeln!(f, "ignored_device={d}")?;
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
