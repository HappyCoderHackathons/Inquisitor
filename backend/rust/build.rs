//! Embeds `app.manifest` into the exe so Windows loads Common Controls v6
//! (modern themed controls) and treats the process as per-monitor DPI aware.
//!
//! Also embeds the app icon from `docs/icons/png` as icon resource 1, so
//! Explorer, the taskbar, the tray, and Apps & features all show it. The
//! resource file is written by hand (the `.res` format is small) and passed to
//! the MSVC linker, which avoids depending on `rc.exe` or a build crate.

use std::fs;
use std::path::{Path, PathBuf};

/// Icon sizes bundled into the exe. Windows picks the closest one per DPI.
const ICON_SIZES: [u32; 6] = [16, 32, 48, 64, 128, 256];
/// Resource id of the icon group. `src/icon.rs` loads the same id.
const ICON_GROUP_ID: u16 = 1;

fn main() {
    println!("cargo:rerun-if-changed=app.manifest");

    // The /MANIFEST* flags are MSVC linker options; skip them for other toolchains.
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() != Ok("msvc") {
        return;
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let manifest = root.join("app.manifest");
    println!("cargo:rustc-link-arg-bins=/MANIFEST:EMBED");
    println!(
        "cargo:rustc-link-arg-bins=/MANIFESTINPUT:{}",
        manifest.display()
    );

    let icons = root
        .join("..")
        .join("..")
        .join("docs")
        .join("icons")
        .join("png");
    let pngs: Vec<(PathBuf, Vec<u8>)> = ICON_SIZES
        .iter()
        .map(|size| {
            let path = icons.join(format!("inquisitor-icon-{size}.png"));
            println!("cargo:rerun-if-changed={}", path.display());
            let bytes =
                fs::read(&path).unwrap_or_else(|e| panic!("reading {}: {e}", path.display()));
            (path, bytes)
        })
        .collect();

    let res = PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("icon.res");
    fs::write(&res, icon_res(&pngs)).expect("writing icon.res");
    println!("cargo:rustc-link-arg-bins={}", res.display());
}

/// Build a `.res` file holding one RT_ICON per PNG plus the RT_GROUP_ICON that
/// ties them together. PNG-compressed icon images are supported since Vista.
fn icon_res(pngs: &[(PathBuf, Vec<u8>)]) -> Vec<u8> {
    const RT_ICON: u16 = 3;
    const RT_GROUP_ICON: u16 = 14;

    let mut out = Vec::new();
    // A .res file opens with an empty entry that marks it as 32-bit.
    push_entry(&mut out, 0, 0, 0, &[]);

    let mut group = Vec::new();
    group.extend_from_slice(&0u16.to_le_bytes()); // reserved
    group.extend_from_slice(&1u16.to_le_bytes()); // type: icon
    group.extend_from_slice(&(pngs.len() as u16).to_le_bytes());

    for (i, (path, png)) in pngs.iter().enumerate() {
        let (w, h) = png_size(png).unwrap_or_else(|| panic!("{} is not a PNG", path.display()));
        let id = i as u16 + 1;
        push_entry(&mut out, RT_ICON, id, 0x1010, png);

        // GRPICONDIRENTRY: a width or height of 256 is stored as 0.
        group.push(if w >= 256 { 0 } else { w as u8 });
        group.push(if h >= 256 { 0 } else { h as u8 });
        group.push(0); // colour count
        group.push(0); // reserved
        group.extend_from_slice(&1u16.to_le_bytes()); // planes
        group.extend_from_slice(&32u16.to_le_bytes()); // bits per pixel
        group.extend_from_slice(&(png.len() as u32).to_le_bytes());
        group.extend_from_slice(&id.to_le_bytes());
    }
    push_entry(&mut out, RT_GROUP_ICON, ICON_GROUP_ID, 0x1030, &group);
    out
}

/// Append one resource with a numeric type and name, US English, padded to a
/// 4-byte boundary as the format requires.
fn push_entry(out: &mut Vec<u8>, kind: u16, name: u16, flags: u16, data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_le_bytes());
    out.extend_from_slice(&32u32.to_le_bytes()); // header size
    out.extend_from_slice(&[0xFF, 0xFF]);
    out.extend_from_slice(&kind.to_le_bytes());
    out.extend_from_slice(&[0xFF, 0xFF]);
    out.extend_from_slice(&name.to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes()); // data version
    out.extend_from_slice(&flags.to_le_bytes());
    let lang: u16 = if kind == 0 { 0 } else { 0x0409 };
    out.extend_from_slice(&lang.to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes()); // version
    out.extend_from_slice(&0u32.to_le_bytes()); // characteristics
    out.extend_from_slice(data);
    while !out.len().is_multiple_of(4) {
        out.push(0);
    }
}

/// Width and height from a PNG's IHDR chunk.
fn png_size(png: &[u8]) -> Option<(u32, u32)> {
    if png.len() < 24 || &png[..8] != b"\x89PNG\r\n\x1a\n" || &png[12..16] != b"IHDR" {
        return None;
    }
    let w = u32::from_be_bytes(png[16..20].try_into().ok()?);
    let h = u32::from_be_bytes(png[20..24].try_into().ok()?);
    Some((w, h))
}
