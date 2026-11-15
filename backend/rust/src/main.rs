// Suppress the console window when launched by double-clicking. When invoked
// from a shell (with any CLI subcommand), we reattach to the parent console
// below so `inquisitor list` and `inquisitor capture` still print normally.
#![windows_subsystem = "windows"]

mod capture;
mod config;
mod devices;
mod devices_window;
mod folder_picker;
mod startup;
mod tray;

use std::path::PathBuf;

use capture::CaptureOutput;
use windows::Win32::System::Console::{ATTACH_PARENT_PROCESS, AttachConsole};

fn main() -> windows::core::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let has_subcommand = args.len() > 1;

    // CLI mode: try to reattach to the parent console so println/eprintln are
    // visible. This is a no-op when there is no parent console (double-click).
    if has_subcommand {
        unsafe {
            let _ = AttachConsole(ATTACH_PARENT_PROCESS);
        }
    }

    match args.get(1).map(String::as_str) {
        Some("capture") => capture::run(parse_capture_output(&args[2..])),
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
        Some(other) => {
            eprintln!("unknown subcommand: {other}");
            print_help();
            Ok(())
        }
        None => tray::run(),
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
    println!("  inquisitor                    Launch the tray app (default)");
    println!("  inquisitor list               List connected input devices");
    println!("  inquisitor capture            Capture to %APPDATA%\\inquisitor\\capture-<ts>.csv");
    println!("  inquisitor capture --out <p>  Capture to file <p>");
    println!("  inquisitor capture --out -    Capture to stdout");
}
