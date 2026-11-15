// Suppress the console window when launched by double-clicking. When invoked
// from a shell (with any CLI subcommand), we reattach to the parent console
// below so `inquisitor list` and `inquisitor capture` still print normally.
#![windows_subsystem = "windows"]

mod capture;
mod config;
mod credential;
mod devices;
mod devices_window;
mod features;
mod folder_picker;
mod http;
mod icon;
mod install;
mod link;
mod pair_window;
mod startup;
mod tray;
mod ui;
mod wizard;

use std::path::PathBuf;

use capture::CaptureOutput;
use windows::Win32::System::Console::{ATTACH_PARENT_PROCESS, AttachConsole};

fn main() -> windows::core::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    // Window modes never print, so they skip the console.
    let windowed = matches!(
        args.get(1).map(String::as_str),
        None | Some("tray" | "setup" | "uninstall")
    );

    // CLI mode: try to reattach to the parent console so println/eprintln are
    // visible. This is a no-op when there is no parent console (double-click).
    if !windowed {
        unsafe {
            let _ = AttachConsole(ATTACH_PARENT_PROCESS);
        }
    }

    match args.get(1).map(String::as_str) {
        Some("capture") => {
            if !config::Config::load_or_default().terms_accepted() {
                eprintln!("Accept the terms first: run `Inquisitor setup`.");
                std::process::exit(2);
            }
            capture::run(parse_capture_output(&args[2..]))
        }
        Some("list") => {
            let devices = devices::list_input_devices()?;
            eprintln!("{} connected input devices", devices.len());
            for device in &devices {
                println!("{}\n    {}", device.kind, device.name);
            }
            Ok(())
        }
        Some("--help") | Some("-h") => {
            print_help();
            Ok(())
        }
        Some("tray") => run_tray(),
        Some("setup") => run_wizard(wizard::Start::Main),
        Some("uninstall") => run_wizard(wizard::Start::Remove),
        Some(other) => {
            eprintln!("unknown subcommand: {other}");
            print_help();
            Ok(())
        }
        // Double-clicking the installed exe starts the tray. Any other copy (a
        // download or a dev build) opens the wizard, which can also run it once.
        None if install::mode() == install::Mode::Installed => run_tray(),
        None => run_wizard(wizard::Start::Main),
    }
}

/// Start the tray, asking for the terms first if they are not accepted yet
/// (for example after an update raised the terms version). Declining exits.
fn run_tray() -> windows::core::Result<()> {
    if !config::Config::load_or_default().terms_accepted() {
        return run_wizard(wizard::Start::Terms);
    }
    tray::run()
}

fn run_wizard(start: wizard::Start) -> windows::core::Result<()> {
    match wizard::run(start)? {
        wizard::Outcome::RunTray => tray::run(),
        wizard::Outcome::Exit => Ok(()),
    }
}

fn parse_capture_output(args: &[String]) -> CaptureOutput {
    let mut out = CaptureOutput::DefaultFile;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--out" | "-o" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("--out requires a path (or '-' for stdout)");
                    std::process::exit(2);
                }
                out = if args[i] == "-" {
                    CaptureOutput::Stdout
                } else {
                    CaptureOutput::File(PathBuf::from(&args[i]))
                };
            }
            other => {
                eprintln!("unknown argument: {other}");
                std::process::exit(2);
            }
        }
        i += 1;
    }
    out
}

fn print_help() {
    println!("inquisitor");
    println!();
    println!("Usage:");
    println!("  inquisitor                    Tray app if installed, otherwise the setup wizard");
    println!("  inquisitor tray               Launch the tray app");
    println!("  inquisitor setup              Install, update, repair, or remove");
    println!("  inquisitor uninstall          Open setup on the remove page");
    println!("  inquisitor list               List connected input devices");
    println!("  inquisitor capture            Capture to %APPDATA%\\inquisitor\\capture-<ts>.csv");
    println!("  inquisitor capture --out <p>  Capture to file <p>");
    println!("  inquisitor capture --out -    Capture to stdout");
}
