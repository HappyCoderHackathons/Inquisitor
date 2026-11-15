//! HTTPS and WebSocket client on WinHTTP, the HTTP stack built into Windows.
//!
//! Using the OS stack instead of a Rust TLS crate means certificate checks use the
//! Windows trust store and its revocation and policy settings, proxies follow the
//! system configuration, and the exe gains no TLS dependencies. Certificate
//! verification is never turned off.
//!
//! Plain `http://` is only accepted for a loopback host, for local development.

use std::ffi::c_void;
use std::io;

use windows::Win32::Networking::WinHttp::{
    WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, WINHTTP_FLAG_SECURE, WINHTTP_OPEN_REQUEST_FLAGS,
    WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET, WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_QUERY_STATUS_CODE,
    WINHTTP_WEB_SOCKET_BUFFER_TYPE, WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE,
    WINHTTP_WEB_SOCKET_UTF8_FRAGMENT_BUFFER_TYPE, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
    WinHttpCloseHandle, WinHttpConnect, WinHttpOpen, WinHttpOpenRequest, WinHttpQueryDataAvailable,
    WinHttpQueryHeaders, WinHttpReadData, WinHttpReceiveResponse, WinHttpSendRequest,
    WinHttpSetOption, WinHttpSetTimeouts, WinHttpWebSocketClose, WinHttpWebSocketCompleteUpgrade,
    WinHttpWebSocketReceive, WinHttpWebSocketSend,
};
use windows::core::{HSTRING, PCWSTR, w};

// Milliseconds. Resolve, connect, send, and receive for ordinary requests.
const TIMEOUTS: (i32, i32, i32, i32) = (10_000, 10_000, 15_000, 15_000);
// Responses from the agent endpoints are small; refuse anything far larger.
const MAX_BODY: usize = 1024 * 1024;
const MAX_MESSAGE: usize = 64 * 1024;

/// Where the website is: scheme, host, port, and an optional path prefix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    pub secure: bool,
    pub host: String,
    pub port: u16,
    pub base_path: String,
}

impl Endpoint {
    /// Parse `https://host[:port][/prefix]`. `http://` only for localhost,
    /// 127.0.0.1, or [::1].
    pub fn parse(url: &str) -> Result<Self, String> {
        let url = url.trim();
        let (secure, rest) = if let Some(rest) = url.strip_prefix("https://") {
            (true, rest)
        } else if let Some(rest) = url.strip_prefix("http://") {
            (false, rest)
        } else {
            return Err("The address must start with https://".into());
        };
        let (authority, path) = match rest.find('/') {
            Some(i) => (&rest[..i], &rest[i..]),
            None => (rest, ""),
        };
        if authority.contains('@') || path.contains(['?', '#']) {
            return Err("Enter only the site address, such as https://example.com".into());
        }
        // `[v6]:port`, `[v6]`, `host:port`, or `host`.
        let (host, port) = if let Some(end) = authority.find(']') {
            let rest = &authority[end + 1..];
            match rest.strip_prefix(':') {
                Some(port) => (&authority[..=end], Some(port)),
                None if rest.is_empty() => (authority, None),
                None => return Err("The address is not valid".into()),
            }
        } else {
            match authority.split_once(':') {
                Some((host, port)) => (host, Some(port)),
                None => (authority, None),
            }
        };
        let port = match port {
            Some(p) => p
                .parse::<u16>()
                .ok()
                .filter(|p| *p != 0)
                .ok_or("The port number is not valid")?,
            None if secure => 443,
            None => 80,
        };
        if host.is_empty() {
            return Err("The address has no host name".into());
        }
        let loopback = matches!(host, "localhost" | "127.0.0.1" | "[::1]");
        if !secure && !loopback {
            return Err("Use https:// (http:// is only allowed for this PC, for testing)".into());
        }
        Ok(Self {
            secure,
            host: host
                .trim_start_matches('[')
                .trim_end_matches(']')
                .to_string(),
            port,
            base_path: path.trim_end_matches('/').to_string(),
        })
    }

    /// The address as the user would type it, for display.
    pub fn display(&self) -> String {
        let default = if self.secure { 443 } else { 80 };
        let host = if self.host.contains(':') {
            format!("[{}]", self.host)
        } else {
            self.host.clone()
        };
        let port = if self.port == default {
            String::new()
        } else {
            format!(":{}", self.port)
        };
        let scheme = if self.secure { "https" } else { "http" };
        format!("{scheme}://{host}{port}{}", self.base_path)
    }
}

/// An owned WinHTTP handle, closed on drop.
struct Handle(*mut c_void);

// WinHTTP handles may be used from any thread. A WebSocket is read on one thread
// and written on another, which WinHTTP supports (one send and one receive at a time).
unsafe impl Send for Handle {}
unsafe impl Sync for Handle {}

impl Handle {
    fn new(raw: *mut c_void) -> io::Result<Self> {
        if raw.is_null() {
            Err(io::Error::last_os_error())
        } else {
            Ok(Self(raw))
        }
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            let _ = WinHttpCloseHandle(self.0);
        }
    }
}

pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}

pub struct Client {
    endpoint: Endpoint,
    // Field order: the connection closes before the session it belongs to.
    connect: Handle,
    _session: Handle,
}

fn win_err(e: windows::core::Error) -> io::Error {
    io::Error::other(e.message())
}

impl Client {
    pub fn new(endpoint: Endpoint) -> io::Result<Self> {
        let agent = HSTRING::from(format!("Inquisitor/{}", env!("CARGO_PKG_VERSION")));
        unsafe {
            let session = Handle::new(WinHttpOpen(
                &agent,
                WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                PCWSTR::null(),
                PCWSTR::null(),
                0,
            ))?;
            let (resolve, connect, send, receive) = TIMEOUTS;
            WinHttpSetTimeouts(session.0, resolve, connect, send, receive).map_err(win_err)?;
            let connect = Handle::new(WinHttpConnect(
                session.0,
                &HSTRING::from(endpoint.host.as_str()),
                endpoint.port,
                0,
            ))?;
            Ok(Self {
                endpoint,
                connect,
                _session: session,
            })
        }
    }

    pub fn endpoint(&self) -> &Endpoint {
        &self.endpoint
    }

    fn open(&self, method: PCWSTR, path: &str) -> io::Result<Handle> {
        let object = HSTRING::from(format!("{}{path}", self.endpoint.base_path));
        let flags = if self.endpoint.secure {
            WINHTTP_FLAG_SECURE
        } else {
            WINHTTP_OPEN_REQUEST_FLAGS(0)
        };
        unsafe {
            Handle::new(WinHttpOpenRequest(
                self.connect.0,
                method,
                &object,
                PCWSTR::null(),
                PCWSTR::null(),
                std::ptr::null(),
                flags,
            ))
        }
    }

    fn headers(bearer: Option<&str>, json: bool) -> Vec<u16> {
        let mut headers = String::new();
        if let Some(token) = bearer {
            headers.push_str(&format!("Authorization: Bearer {token}\r\n"));
        }
        if json {
            headers.push_str("Content-Type: application/json\r\n");
        }
        headers.encode_utf16().collect()
    }

    /// Send one request and read the whole response.
    pub fn request(
        &self,
        method: &str,
        path: &str,
        bearer: Option<&str>,
        json: Option<&[u8]>,
    ) -> io::Result<Response> {
        let verb = HSTRING::from(method);
        let request = self.open(PCWSTR(verb.as_ptr()), path)?;
        let headers = Self::headers(bearer, json.is_some());
        let body = json.unwrap_or_default();
        unsafe {
            WinHttpSendRequest(
                request.0,
                (!headers.is_empty()).then_some(headers.as_slice()),
                (!body.is_empty()).then_some(body.as_ptr().cast()),
                body.len() as u32,
                body.len() as u32,
                0,
            )
            .map_err(win_err)?;
            WinHttpReceiveResponse(request.0, std::ptr::null_mut()).map_err(win_err)?;
        }
        let status = status_code(&request)?;
        let mut body = Vec::new();
        loop {
            let mut available = 0u32;
            unsafe { WinHttpQueryDataAvailable(request.0, &mut available) }.map_err(win_err)?;
            if available == 0 {
                break;
            }
            if body.len() + available as usize > MAX_BODY {
                return Err(io::Error::other("response is too large"));
            }
            let start = body.len();
            body.resize(start + available as usize, 0);
            let mut read = 0u32;
            unsafe {
                WinHttpReadData(
                    request.0,
                    body[start..].as_mut_ptr().cast(),
                    available,
                    &mut read,
                )
            }
            .map_err(win_err)?;
            body.truncate(start + read as usize);
            if read == 0 {
                break;
            }
        }
        Ok(Response { status, body })
    }

    /// Open a WebSocket. Returns the HTTP status instead when the server refuses
    /// the upgrade (for example 401 for a revoked device).
    pub fn websocket(&self, path: &str, bearer: &str) -> io::Result<Result<WebSocket, u16>> {
        let request = self.open(w!("GET"), path)?;
        let headers = Self::headers(Some(bearer), false);
        unsafe {
            WinHttpSetOption(
                Some(request.0.cast_const()),
                WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET,
                None,
            )
            .map_err(win_err)?;
            // Messages can be minutes apart; the link's heartbeat detects a dead
            // connection, so receive waits without a timeout.
            let (resolve, connect, send, _) = TIMEOUTS;
            WinHttpSetTimeouts(request.0, resolve, connect, send, 0).map_err(win_err)?;
            WinHttpSendRequest(request.0, Some(headers.as_slice()), None, 0, 0, 0)
                .map_err(win_err)?;
            WinHttpReceiveResponse(request.0, std::ptr::null_mut()).map_err(win_err)?;
        }
        let status = status_code(&request)?;
        if status != 101 {
            return Ok(Err(status));
        }
        let socket = Handle::new(unsafe { WinHttpWebSocketCompleteUpgrade(request.0, None) })?;
        Ok(Ok(WebSocket(socket)))
    }
}

fn status_code(request: &Handle) -> io::Result<u16> {
    let mut status = 0u32;
    let mut len = size_of::<u32>() as u32;
    unsafe {
        WinHttpQueryHeaders(
            request.0,
            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
            PCWSTR::null(),
            Some((&mut status as *mut u32).cast()),
            &mut len,
            std::ptr::null_mut(),
        )
    }
    .map_err(win_err)?;
    Ok(status as u16)
}

fn ws_result(code: u32) -> io::Result<()> {
    if code == 0 {
        Ok(())
    } else {
        Err(io::Error::from_raw_os_error(code as i32))
    }
}

/// An open WebSocket. `receive` and `send` may be called from different threads.
pub struct WebSocket(Handle);

impl WebSocket {
    pub fn send(&self, text: &str) -> io::Result<()> {
        ws_result(unsafe {
            WinHttpWebSocketSend(
                self.0.0,
                WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
                Some(text.as_bytes()),
            )
        })
    }

    /// The next text message, or None once the socket is closed.
    pub fn receive(&self) -> io::Result<Option<String>> {
        let mut message = Vec::new();
        let mut buf = [0u8; 4096];
        loop {
            let mut read = 0u32;
            let mut kind = WINHTTP_WEB_SOCKET_BUFFER_TYPE::default();
            ws_result(unsafe {
                WinHttpWebSocketReceive(
                    self.0.0,
                    buf.as_mut_ptr().cast(),
                    buf.len() as u32,
                    &mut read,
                    &mut kind,
                )
            })?;
            if kind == WINHTTP_WEB_SOCKET_CLOSE_BUFFER_TYPE {
                return Ok(None);
            }
            message.extend_from_slice(&buf[..read as usize]);
            if message.len() > MAX_MESSAGE {
                return Err(io::Error::other("message is too large"));
            }
            if kind == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE {
                return String::from_utf8(message)
                    .map(Some)
                    .map_err(io::Error::other);
            }
            if kind != WINHTTP_WEB_SOCKET_UTF8_FRAGMENT_BUFFER_TYPE {
                // Binary frames are not part of the protocol.
                return Err(io::Error::other("unexpected binary message"));
            }
        }
    }

    /// Start a normal close. A blocked `receive` on another thread then returns.
    pub fn close(&self) {
        unsafe {
            let _ = WinHttpWebSocketClose(self.0.0, 1000, None, 0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoint_accepts_https_and_loopback_http_only() {
        let e = Endpoint::parse(" https://app.example.test/ ").unwrap();
        assert_eq!(
            e,
            Endpoint {
                secure: true,
                host: "app.example.test".into(),
                port: 443,
                base_path: String::new()
            }
        );
        assert_eq!(e.display(), "https://app.example.test");

        let e = Endpoint::parse("https://app.example.test:8443/inquisitor/").unwrap();
        assert_eq!((e.port, e.base_path.as_str()), (8443, "/inquisitor"));
        assert_eq!(e.display(), "https://app.example.test:8443/inquisitor");

        let e = Endpoint::parse("http://127.0.0.1:5000").unwrap();
        assert_eq!((e.secure, e.port), (false, 5000));
        assert!(Endpoint::parse("http://[::1]:5000").is_ok());

        for bad in [
            "http://app.example.test",
            "ftp://app.example.test",
            "app.example.test",
            "https://",
            "https://app.example.test:0",
            "https://app.example.test:99999",
            "https://user@app.example.test",
            "https://app.example.test/?next=x",
        ] {
            assert!(Endpoint::parse(bad).is_err(), "{bad}");
        }
    }
}
