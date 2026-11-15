//! Embeds `app.manifest` into the exe so Windows loads Common Controls v6
//! (modern themed controls) and treats the process as per-monitor DPI aware.

fn main() {
    println!("cargo:rerun-if-changed=app.manifest");

    // The /MANIFEST* flags are MSVC linker options; skip them for other toolchains.
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() != Ok("msvc") {
        return;
    }
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("app.manifest");
    println!("cargo:rustc-link-arg-bins=/MANIFEST:EMBED");
    println!("cargo:rustc-link-arg-bins=/MANIFESTINPUT:{}", manifest.display());
}
