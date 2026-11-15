mod capture;
mod devices;

use std::path::PathBuf;

use capture::CaptureOutput;

fn main() -> windows::core::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("capture") => capture::run(parse_capture_output(&args[2..])),
        Some("--help") | Some("-h") => {
            print_help();
            Ok(())
        }
        _ => {
            let devices = devices::list_input_devices()?;
            eprintln!("{} connected input devices", devices.len());
            for device in &devices {
                println!("{}\n    {}", device.kind, device.name);
            }
            eprintln!(
                "\nRun `inquisitor capture` to stream raw input as CSV to \
                 %APPDATA%\\inquisitor\\, or `inquisitor capture --out -` for stdout."
            );
            Ok(())
        }
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
    println!("  inquisitor                    List connected input devices");
    println!("  inquisitor capture            Capture to %APPDATA%\\inquisitor\\capture-<ts>.csv");
    println!("  inquisitor capture --out <p>  Capture to file <p>");
    println!("  inquisitor capture --out -    Capture to stdout");
}
