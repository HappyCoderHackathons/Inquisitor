use std::fmt;
use std::mem::size_of;

use windows::Win32::Foundation::HANDLE;
use windows::Win32::UI::Input::{
    GetRawInputDeviceInfoW, GetRawInputDeviceList, RAWINPUTDEVICELIST, RID_DEVICE_INFO,
    RIDI_DEVICEINFO, RIDI_DEVICENAME, RIM_TYPEHID, RIM_TYPEKEYBOARD, RIM_TYPEMOUSE,
};

#[derive(Debug)]
pub enum DeviceKind {
    Keyboard {
        keys: u32,
        function_keys: u32,
    },
    Mouse {
        buttons: u32,
        sample_rate: u32,
    },
    Hid {
        vendor_id: u32,
        product_id: u32,
        usage_page: u16,
        usage: u16,
    },
    Unknown(u32),
}

impl fmt::Display for DeviceKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Keyboard {
                keys,
                function_keys,
            } => write!(f, "Keyboard ({keys} keys, {function_keys} function keys)"),
            Self::Mouse {
                buttons,
                sample_rate,
            } => write!(f, "Mouse ({buttons} buttons, {sample_rate} Hz)"),
            Self::Hid {
                vendor_id,
                product_id,
                usage_page,
                usage,
            } => write!(
                f,
                "HID {} (VID {vendor_id:04X}, PID {product_id:04X}, usage page {usage_page:04X}, usage {usage:04X})",
                usage_name(*usage_page, *usage)
            ),
            Self::Unknown(raw_type) => write!(f, "Unknown (type {raw_type})"),
        }
    }
}

pub(crate) fn usage_name(usage_page: u16, usage: u16) -> &'static str {
    match (usage_page, usage) {
        (0x01, 0x01) => "pointer",
        (0x01, 0x02) => "mouse",
        (0x01, 0x04) => "joystick",
        (0x01, 0x05) => "gamepad",
        (0x01, 0x06) => "keyboard",
        (0x01, 0x07) => "keypad",
        (0x01, 0x08) => "multi-axis controller",
        (0x01, 0x0D) => "portable device control",
        (0x0C, 0x01) => "consumer control",
        (0x0D, 0x01) => "digitizer",
        (0x0D, 0x02) => "pen",
        (0x0D, 0x04) => "touch screen",
        (0x0D, 0x05) => "touch pad",
        (0xFF00..=0xFFFF, _) => "vendor-defined",
        _ => "unknown",
    }
}

#[derive(Debug)]
pub struct InputDevice {
    pub name: String,
    pub kind: DeviceKind,
}

impl InputDevice {
    /// Human-readable name. Prefers the product string the device reports (e.g.
    /// "USB Receiver"). Many laptop devices only report a driver placeholder such
    /// as "HIDI2C Device", so in that case the name is built from the vendor and
    /// type instead ("Synaptics touch pad", "Wacom pen").
    pub fn display_name(&self) -> String {
        if let Some(name) = product_name(&self.name)
            && !is_generic_product(&name)
        {
            return name;
        }
        if matches!(self.kind, DeviceKind::Keyboard { .. })
            && self.name.to_ascii_uppercase().contains("\\ACPI#")
        {
            return "Built-in keyboard".to_string();
        }
        let kind = self.type_label();
        match self.vid_pid().0.and_then(vendor_name) {
            Some(vendor) if kind == "Vendor-defined" || kind == "Other" => {
                format!("{vendor} device")
            }
            Some(vendor) => format!("{vendor} {}", kind.to_lowercase()),
            None => kind,
        }
    }

    /// Vendor and product ids from the HID descriptor, or parsed from the device
    /// path (`VID_046D&PID_C52B`, `VEN_06CB`) for keyboards and mice.
    fn vid_pid(&self) -> (Option<u16>, Option<u16>) {
        if let DeviceKind::Hid {
            vendor_id,
            product_id,
            ..
        } = &self.kind
            && *vendor_id != 0
        {
            return (Some(*vendor_id as u16), Some(*product_id as u16));
        }
        let upper = self.name.to_ascii_uppercase();
        let hex_after = |key: &str| {
            upper
                .find(key)
                .and_then(|i| upper.get(i + key.len()..i + key.len() + 4))
                .and_then(|s| u16::from_str_radix(s, 16).ok())
        };
        (
            hex_after("VID_").or_else(|| hex_after("VEN_")),
            hex_after("PID_"),
        )
    }

    /// Short type label: "Keyboard", "Mouse", or the HID usage ("Touch pad", "Gamepad").
    pub fn type_label(&self) -> String {
        match &self.kind {
            DeviceKind::Keyboard { .. } => "Keyboard".to_string(),
            DeviceKind::Mouse { .. } => "Mouse".to_string(),
            DeviceKind::Hid {
                usage_page, usage, ..
            } => capitalize(usage_name(*usage_page, *usage)),
            DeviceKind::Unknown(_) => "Other".to_string(),
        }
    }

    /// Compact identifier that tells similar entries apart: "VID:PID" plus the HID
    /// collection ("Col04") when the device exposes several. Falls back to the
    /// hardware id segment of the path for devices without a VID/PID.
    pub fn id_label(&self) -> String {
        let upper = self.name.to_ascii_uppercase();
        let col = upper
            .find("&COL")
            .and_then(|i| upper.get(i + 4..i + 6))
            .map(|c| format!("Col{c}"));

        let base = match self.vid_pid() {
            (Some(v), Some(p)) => format!("{v:04X}:{p:04X}"),
            (Some(v), None) => format!("{v:04X}"),
            _ => hardware_segment(&self.name),
        };
        match col {
            Some(c) => format!("{base} · {c}"),
            None => base,
        }
    }
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().chain(chars).collect(),
        None => String::new(),
    }
}

/// Second `#`-separated segment of a device path, e.g. "DLLK0C15" from
/// `\\?\ACPI#DLLK0C15#...`. Used as a last-resort identifier. Paths without
/// that structure (such as `\\?\Microsoft HID RID\...`) are software devices.
fn hardware_segment(path: &str) -> String {
    path.split('#')
        .nth(1)
        .map(|s| s.split('&').next().unwrap_or(s).to_string())
        .unwrap_or_else(|| "Virtual".to_string())
}

/// Placeholder product strings that HID class drivers report when the device
/// itself does not supply a name. Showing these would make every row identical.
fn is_generic_product(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.starts_with("hidi2c")
        || lower.starts_with("hid-compliant")
        || lower.starts_with("usb input device")
        || lower == "hid device"
}

/// Common input-device vendors by USB vendor id, for readable fallback names.
fn vendor_name(vid: u16) -> Option<&'static str> {
    Some(match vid {
        0x045E => "Microsoft",
        0x046D => "Logitech",
        0x04F2 => "Chicony",
        0x054C => "Sony",
        0x056A => "Wacom",
        0x057E => "Nintendo",
        0x06CB => "Synaptics",
        0x0B05 => "ASUS",
        0x1038 => "SteelSeries",
        0x1532 => "Razer",
        0x17EF => "Lenovo",
        0x1B1C => "Corsair",
        0x28DE => "Valve",
        0x2DC8 => "8BitDo",
        0x413C => "Dell",
        0x8087 => "Intel",
        _ => return None,
    })
}

/// Ask the HID driver for the product string. Opening with zero access rights
/// is enough for this query and works even for devices the system holds open
/// exclusively (keyboards, mice). Returns None for non-HID paths.
pub fn product_name(path: &str) -> Option<String> {
    use windows::Win32::Devices::HumanInterfaceDevice::HidD_GetProductString;
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::Storage::FileSystem::{
        CreateFileW, FILE_FLAGS_AND_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
    };
    use windows::core::PCWSTR;

    let wide: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
    let handle = unsafe {
        CreateFileW(
            PCWSTR(wide.as_ptr()),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAGS_AND_ATTRIBUTES(0),
            None,
        )
    }
    .ok()?;

    // HID strings are capped at 126 wide chars plus terminator by the spec.
    let mut buf = [0u16; 127];
    let ok =
        unsafe { HidD_GetProductString(handle, buf.as_mut_ptr().cast(), (buf.len() * 2) as u32) };
    unsafe {
        let _ = CloseHandle(handle);
    }
    if !ok {
        return None;
    }
    let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    let name = String::from_utf16_lossy(&buf[..end]).trim().to_string();
    (!name.is_empty()).then_some(name)
}

pub fn list_input_devices() -> windows::core::Result<Vec<InputDevice>> {
    let entry_size = size_of::<RAWINPUTDEVICELIST>() as u32;
    let mut count = 0u32;

    if unsafe { GetRawInputDeviceList(None, &mut count, entry_size) } == u32::MAX {
        return Err(windows::core::Error::from_thread());
    }

    let mut list = vec![RAWINPUTDEVICELIST::default(); count as usize];
    let written = unsafe { GetRawInputDeviceList(Some(list.as_mut_ptr()), &mut count, entry_size) };
    if written == u32::MAX {
        return Err(windows::core::Error::from_thread());
    }
    list.truncate(written as usize);

    Ok(list
        .iter()
        .map(|entry| InputDevice {
            name: device_name(entry.hDevice).unwrap_or_else(|| "<unknown>".to_string()),
            kind: device_kind(entry.hDevice, entry.dwType.0),
        })
        .collect())
}

fn device_name(device: HANDLE) -> Option<String> {
    let mut len = 0u32;
    unsafe { GetRawInputDeviceInfoW(Some(device), RIDI_DEVICENAME, None, &mut len) };
    if len == 0 {
        return None;
    }

    let mut buffer = vec![0u16; len as usize];
    let copied = unsafe {
        GetRawInputDeviceInfoW(
            Some(device),
            RIDI_DEVICENAME,
            Some(buffer.as_mut_ptr().cast()),
            &mut len,
        )
    };
    if copied == u32::MAX {
        return None;
    }

    let end = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
    Some(String::from_utf16_lossy(&buffer[..end]))
}

fn device_kind(device: HANDLE, raw_type: u32) -> DeviceKind {
    let mut info = RID_DEVICE_INFO {
        cbSize: size_of::<RID_DEVICE_INFO>() as u32,
        ..Default::default()
    };
    let mut size = info.cbSize;
    let copied = unsafe {
        GetRawInputDeviceInfoW(
            Some(device),
            RIDI_DEVICEINFO,
            Some((&mut info as *mut RID_DEVICE_INFO).cast()),
            &mut size,
        )
    };
    if copied == u32::MAX {
        return DeviceKind::Unknown(raw_type);
    }

    unsafe {
        match info.dwType {
            RIM_TYPEKEYBOARD => DeviceKind::Keyboard {
                keys: info.Anonymous.keyboard.dwNumberOfKeysTotal,
                function_keys: info.Anonymous.keyboard.dwNumberOfFunctionKeys,
            },
            RIM_TYPEMOUSE => DeviceKind::Mouse {
                buttons: info.Anonymous.mouse.dwNumberOfButtons,
                sample_rate: info.Anonymous.mouse.dwSampleRate,
            },
            RIM_TYPEHID => DeviceKind::Hid {
                vendor_id: info.Anonymous.hid.dwVendorId,
                product_id: info.Anonymous.hid.dwProductId,
                usage_page: info.Anonymous.hid.usUsagePage,
                usage: info.Anonymous.hid.usUsage,
            },
            other => DeviceKind::Unknown(other.0),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Instance ids contain "TEST" so CreateFileW never resolves them to a real
    // device; display_name therefore always exercises the fallback naming.
    fn device(path: &str, kind: DeviceKind) -> InputDevice {
        InputDevice {
            name: path.to_string(),
            kind,
        }
    }

    fn keyboard() -> DeviceKind {
        DeviceKind::Keyboard {
            keys: 101,
            function_keys: 12,
        }
    }

    fn mouse() -> DeviceKind {
        DeviceKind::Mouse {
            buttons: 2,
            sample_rate: 0,
        }
    }

    fn hid(vendor_id: u32, product_id: u32, usage_page: u16, usage: u16) -> DeviceKind {
        DeviceKind::Hid {
            vendor_id,
            product_id,
            usage_page,
            usage,
        }
    }

    #[test]
    fn id_label_uses_hid_ids_and_collection() {
        let d = device(
            r"\\?\HID#VEN_06CB&Col02#5&TEST&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}",
            hid(0x06CB, 0xCF06, 0x0D, 0x05),
        );
        assert_eq!(d.id_label(), "06CB:CF06 · Col02");
    }

    #[test]
    fn id_label_parses_vid_pid_from_path_for_keyboards_and_mice() {
        let kb = device(
            r"\\?\HID#VID_046D&PID_C52B&MI_00#7&TEST&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            keyboard(),
        );
        assert_eq!(kb.id_label(), "046D:C52B");

        let ms = device(
            r"\\?\HID#VEN_06CB&Col01#5&TEST&0&0000#{378de44c-56ef-11d1-bc8c-00a0c91405dd}",
            mouse(),
        );
        assert_eq!(ms.id_label(), "06CB · Col01");
    }

    #[test]
    fn id_label_falls_back_to_hardware_segment_or_virtual() {
        let acpi = device(
            r"\\?\ACPI#DLLK0C15#4&TEST&0#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            keyboard(),
        );
        assert_eq!(acpi.id_label(), "DLLK0C15");

        let rid = device(r"\\?\Microsoft HID RID\000D_0002\0", hid(0, 0, 0x0D, 0x02));
        assert_eq!(rid.id_label(), "Virtual");
    }

    #[test]
    fn display_name_falls_back_to_vendor_and_type() {
        let touch = device(
            r"\\?\HID#VEN_06CB&Col02#5&TEST&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}",
            hid(0x06CB, 0xCF06, 0x0D, 0x05),
        );
        assert_eq!(touch.display_name(), "Synaptics touch pad");

        let vendor = device(
            r"\\?\HID#WACF3233&Col06#5&TEST&0&0005#{4d1e55b2-f16f-11cf-88cb-001111000030}",
            hid(0x056A, 0x4A1F, 0xFF11, 0x02),
        );
        assert_eq!(vendor.display_name(), "Wacom device");

        let unknown_vendor = device(
            r"\\?\HID#VID_ABCD&PID_0001#7&TEST&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}",
            hid(0xABCD, 0x0001, 0x01, 0x05),
        );
        assert_eq!(unknown_vendor.display_name(), "Gamepad");
    }

    #[test]
    fn display_name_for_builtin_and_plain_devices() {
        let acpi = device(
            r"\\?\ACPI#DLLK0C15#4&TEST&0#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            keyboard(),
        );
        assert_eq!(acpi.display_name(), "Built-in keyboard");

        let ms = device(
            r"\\?\HID#WACF3233&Col03#5&TEST&0&0002#{4d1e55b2-f16f-11cf-88cb-001111000030}",
            mouse(),
        );
        assert_eq!(ms.display_name(), "Mouse");
    }

    #[test]
    fn type_label_capitalizes_hid_usage() {
        assert_eq!(device("x", keyboard()).type_label(), "Keyboard");
        assert_eq!(
            device("x", hid(1, 1, 0x0D, 0x04)).type_label(),
            "Touch screen"
        );
        assert_eq!(
            device("x", hid(1, 1, 0xFF00, 0x01)).type_label(),
            "Vendor-defined"
        );
        assert_eq!(device("x", DeviceKind::Unknown(9)).type_label(), "Other");
    }

    #[test]
    fn generic_driver_names_are_detected() {
        for name in [
            "HIDI2C Device",
            "HID-compliant mouse",
            "USB Input Device",
            "HID device",
        ] {
            assert!(is_generic_product(name), "{name}");
        }
        for name in ["USB Receiver", "Pro Controller", "Xbox Wireless Controller"] {
            assert!(!is_generic_product(name), "{name}");
        }
    }

    #[test]
    fn vendor_lookup() {
        assert_eq!(vendor_name(0x046D), Some("Logitech"));
        assert_eq!(vendor_name(0x057E), Some("Nintendo"));
        assert_eq!(vendor_name(0x0000), None);
    }

    #[test]
    fn product_name_is_none_for_paths_that_do_not_exist() {
        assert_eq!(product_name(r"\\?\HID#VID_0000&PID_0000#TEST#{0}"), None);
    }

    #[test]
    fn listing_devices_succeeds() {
        // CI runners may have no input devices; the call itself must still work.
        assert!(list_input_devices().is_ok());
    }
}
