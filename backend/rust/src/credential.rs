//! The device credential the website issues when the tray is paired.
//!
//! Stored at `%APPDATA%\inquisitor\device.bin`, encrypted with DPAPI for the
//! current Windows user, so the token is never written in plaintext and another
//! account on the PC cannot read it. Disconnecting, or the server revoking the
//! device, deletes the file.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use windows::Win32::Foundation::{HLOCAL, LocalFree};
use windows::Win32::Security::Cryptography::{
    CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN, CryptProtectData, CryptUnprotectData,
};
use windows::core::w;

use crate::config::default_app_dir;

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Credential {
    pub device_id: String,
    pub token: String,
}

// Never print the token, even in a debug log or panic message.
impl std::fmt::Debug for Credential {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Credential")
            .field("device_id", &self.device_id)
            .field("token", &"<redacted>")
            .finish()
    }
}

pub fn default_path() -> PathBuf {
    default_app_dir().join("device.bin")
}

pub fn save(credential: &Credential) -> io::Result<()> {
    save_to(&default_path(), credential)
}

/// The stored credential, or None when the tray is not paired or the file cannot
/// be decrypted (for example after it was copied from another account).
pub fn load() -> Option<Credential> {
    load_from(&default_path())
}

/// Remove the stored credential. A missing file is not an error.
pub fn delete() -> io::Result<()> {
    delete_at(&default_path())
}

pub fn save_to(path: &Path, credential: &Credential) -> io::Result<()> {
    let plain = serde_json::to_vec(credential).map_err(io::Error::other)?;
    let sealed = protect(&plain).map_err(io::Error::other)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, sealed)
}

pub fn load_from(path: &Path) -> Option<Credential> {
    let sealed = fs::read(path).ok()?;
    let plain = unprotect(&sealed).ok()?;
    serde_json::from_slice(&plain).ok()
}

pub fn delete_at(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        other => other,
    }
}

fn blob(data: &[u8]) -> CRYPT_INTEGER_BLOB {
    CRYPT_INTEGER_BLOB {
        cbData: data.len() as u32,
        pbData: data.as_ptr() as *mut u8,
    }
}

/// Copy DPAPI's output into a Vec and free the buffer it allocated.
fn take(out: CRYPT_INTEGER_BLOB) -> Vec<u8> {
    let bytes = unsafe { std::slice::from_raw_parts(out.pbData, out.cbData as usize) }.to_vec();
    unsafe {
        LocalFree(Some(HLOCAL(out.pbData.cast())));
    }
    bytes
}

fn protect(plain: &[u8]) -> windows::core::Result<Vec<u8>> {
    let input = blob(plain);
    let mut out = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptProtectData(
            &input,
            w!("Inquisitor device credential"),
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut out,
        )?;
    }
    Ok(take(out))
}

fn unprotect(sealed: &[u8]) -> windows::core::Result<Vec<u8>> {
    let input = blob(sealed);
    let mut out = CRYPT_INTEGER_BLOB::default();
    unsafe {
        CryptUnprotectData(
            &input,
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut out,
        )?;
    }
    Ok(take(out))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credential_round_trips_encrypted_and_deletes() {
        let dir = std::env::temp_dir().join(format!("inquisitor-test-{}-cred", std::process::id()));
        let path = dir.join("device.bin");
        let credential = Credential {
            device_id: "0123abcd".into(),
            token: "secret-token-value".into(),
        };
        save_to(&path, &credential).unwrap();

        let on_disk = fs::read(&path).unwrap();
        let text = String::from_utf8_lossy(&on_disk);
        assert!(
            !text.contains("secret-token-value"),
            "token stored in plaintext"
        );
        assert_eq!(load_from(&path), Some(credential.clone()));
        assert!(!format!("{credential:?}").contains("secret-token-value"));

        // A tampered file fails to decrypt instead of yielding a credential.
        let mut tampered = on_disk.clone();
        let last = tampered.len() - 1;
        tampered[last] ^= 0xFF;
        fs::write(&path, tampered).unwrap();
        assert_eq!(load_from(&path), None);

        delete_at(&path).unwrap();
        assert!(!path.exists());
        delete_at(&path).unwrap();
        let _ = fs::remove_dir_all(&dir);
    }
}
