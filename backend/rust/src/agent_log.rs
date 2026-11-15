//! `%APPDATA%\inquisitor\agent.log`: state changes and errors only.
//!
//! Messages are `&'static str`, so no input data, key identity, or other runtime
//! value can be formatted into a line. Panics record only their source location.
//! At `CAP_BYTES` the file is moved to `agent.log.1`, replacing the previous one,
//! so the two files together stay under twice the cap.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::panic::Location;
use std::path::PathBuf;
use std::sync::Mutex;

use crate::capture::{iso_utc, unix_ms_now};
use crate::credential;

const CAP_BYTES: u64 = 256 * 1024;

// The link thread and the UI thread both write; this keeps rotation and appends apart.
static WRITE: Mutex<()> = Mutex::new(());

pub struct AgentLog {
    path: PathBuf,
}

impl AgentLog {
    pub fn at(path: PathBuf) -> Self {
        Self { path }
    }

    /// Next to the device credential, like the outbox.
    pub fn default_path() -> PathBuf {
        credential::default_path().with_file_name("agent.log")
    }

    pub fn event(&self, message: &'static str) {
        self.write(message);
    }

    pub fn panic(&self, location: Option<&Location>) {
        match location {
            Some(at) => self.write(&format!("panic at {}:{}", at.file(), at.line())),
            None => self.write("panic"),
        }
    }

    fn write(&self, message: &str) {
        let _guard = WRITE.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(parent) = self.path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        if fs::metadata(&self.path).is_ok_and(|m| m.len() >= CAP_BYTES) {
            let _ = fs::rename(&self.path, self.path.with_extension("log.1"));
        }
        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            let _ = writeln!(file, "{} {message}", iso_utc(unix_ms_now()));
        }
    }
}

impl Default for AgentLog {
    fn default() -> Self {
        Self::at(Self::default_path())
    }
}

/// Replace the default panic output, which includes the panic message, with a
/// location-only line in the agent log.
pub fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| AgentLog::default().panic(info.location())));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rotates_at_the_cap_keeping_one_old_file() {
        let dir = std::env::temp_dir().join(format!("inquisitor-test-{}-log", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let log = AgentLog::at(dir.join("agent.log"));
        let old = dir.join("agent.log.1");
        let line = "connected to the website";
        let per_line = (iso_utc(0).len() + 1 + line.len() + 1) as u64;
        for _ in 0..CAP_BYTES.div_ceil(per_line) {
            log.event(line);
        }
        assert!(!old.exists());
        log.event(line);
        assert!(fs::metadata(&old).unwrap().len() >= CAP_BYTES);
        assert_eq!(fs::metadata(dir.join("agent.log")).unwrap().len(), per_line);

        // A second rotation replaces agent.log.1 rather than adding files.
        for _ in 0..CAP_BYTES.div_ceil(per_line) {
            log.event(line);
        }
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 2);
        assert!(fs::metadata(&old).unwrap().len() < CAP_BYTES + per_line);

        log.panic(Some(Location::caller()));
        let text = fs::read_to_string(dir.join("agent.log")).unwrap();
        let last = text.lines().last().unwrap();
        assert!(
            last.contains("panic at ") && last.contains("agent_log.rs"),
            "{text}"
        );
        let _ = fs::remove_dir_all(&dir);
    }
}
