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

pub fn usage_name(usage_page: u16, usage: u16) -> &'static str {
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
