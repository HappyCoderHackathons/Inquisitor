//! The tray's connection to the website.
//!
//! A background thread owns everything network-related, so the UI thread never
//! blocks on it:
//!
//! - An outbound WebSocket (`/api/agent/ws`) carries presence: `hello` on connect
//!   and a `heartbeat` every 30 seconds, each with the pause state. The server
//!   answers with `status` (what to collect) and `revoked` when the device is
//!   removed from the account. Nothing ever connects inbound to the PC.
//! - Finished feature windows go to `POST /api/agent/windows` over HTTPS, the
//!   endpoint the website already validates, rate limits, and scores.
//! - Windows wait in a bounded queue while the website is unreachable. The oldest
//!   is dropped when it is full, and windows too old for the server are expired.
//!   Retries back off exponentially with jitter, from 1 second up to 1 minute.
//! - Fail closed: a 401 or 403, or a `revoked` message, stops uploads, clears the
//!   queue, and deletes the stored credential. The tray shows Disconnected.
//!
//! Only aggregate numeric windows and the pause flag leave the PC.

use std::collections::VecDeque;
use std::ffi::c_void;
use std::path::PathBuf;
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::json;
use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::{PostMessageW, WM_APP};

use crate::capture::unix_ms_now;
use crate::credential::{self, Credential};
use crate::features::FeatureWindow;
use crate::http::{Client, Endpoint, WebSocket};

/// Posted to the tray window whenever the link's status changes.
pub const WM_LINK: u32 = WM_APP + 2;

const PROTOCOL: u32 = 1;
const HEARTBEAT: Duration = Duration::from_secs(30);
const QUEUE_CAP: usize = 120;
// The server refuses windows that ended more than a day ago; leave an hour spare.
const QUEUE_MAX_AGE_MS: i64 = 23 * 60 * 60 * 1000;
const BACKOFF_MAX_SECS: u64 = 60;
const PAIRING_ALPHABET: &str = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const PAIRING_CODE_LEN: usize = 8;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    NotPaired,
    Connecting,
    Connected,
    /// Paired, but the website cannot be reached right now; retrying.
    Offline,
    /// The website rejected this device; the credential was deleted.
    Disconnected(String),
}

/// What the website wants collected, from its `status` message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    Idle,
    /// Enrollment windows for the active game round with this session id.
    Enrollment(String),
    Monitoring,
}

#[derive(Debug, Clone)]
pub struct Status {
    pub state: State,
    pub server: Option<String>,
    pub mode: Mode,
    pub queued: usize,
}

/// Map the server's onboarding fields to what the tray should upload.
pub fn mode_from(state: Option<&str>, collection: Option<&str>, session: Option<&str>) -> Mode {
    match (state, collection, session) {
        (Some("collecting"), Some("active"), Some(id)) if !id.is_empty() => {
            Mode::Enrollment(id.to_string())
        }
        (Some("ready"), ..) => Mode::Monitoring,
        _ => Mode::Idle,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UploadOutcome {
    Sent,
    /// The server will never accept this window (invalid, duplicate, wrong mode).
    Drop,
    /// Try again later (rate limit, server or network trouble).
    Retry,
    /// The device is not authorized any more.
    FailClosed,
}

pub fn classify(status: u16) -> UploadOutcome {
    match status {
        200..=299 => UploadOutcome::Sent,
        401 | 403 => UploadOutcome::FailClosed,
        408 | 429 | 500..=599 => UploadOutcome::Retry,
        _ => UploadOutcome::Drop,
    }
}

/// Exponential backoff: 1, 2, 4, ... seconds, capped at one minute, each delay
/// scaled by a jitter factor in [0.5, 1) so many PCs do not retry in step.
#[derive(Default)]
pub struct Backoff {
    attempt: u32,
}

impl Backoff {
    pub fn next(&mut self, jitter: f64) -> Duration {
        let base = (1u64 << self.attempt.min(6)).min(BACKOFF_MAX_SECS);
        self.attempt = self.attempt.saturating_add(1);
        Duration::from_secs_f64(base as f64 * (0.5 + 0.5 * jitter.clamp(0.0, 1.0)))
    }

    pub fn reset(&mut self) {
        self.attempt = 0;
    }
}

fn jitter() -> f64 {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .subsec_nanos();
    f64::from(nanos % 1000) / 1000.0
}

/// One window ready to upload, already serialized with its device, session, and mode.
pub struct Queued {
    pub body: Vec<u8>,
    pub ended_unix_ms: i64,
}

/// Bounded offline queue (#98).
pub struct Queue {
    items: VecDeque<Queued>,
    cap: usize,
}

impl Queue {
    pub fn new(cap: usize) -> Self {
        Self {
            items: VecDeque::new(),
            cap,
        }
    }

    pub fn push(&mut self, item: Queued) {
        if self.items.len() == self.cap {
            self.items.pop_front();
        }
        self.items.push_back(item);
    }

    /// Drop windows the server would refuse as too old.
    pub fn expire(&mut self, now_unix_ms: i64) {
        self.items
            .retain(|item| now_unix_ms - item.ended_unix_ms <= QUEUE_MAX_AGE_MS);
    }

    pub fn front(&self) -> Option<&Queued> {
        self.items.front()
    }

    pub fn pop(&mut self) {
        self.items.pop_front();
    }

    pub fn clear(&mut self) {
        self.items.clear();
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

/// The JSON body for `POST /api/agent/windows`.
pub fn window_body(
    window: &FeatureWindow,
    device_id: &str,
    mode: &Mode,
    monitoring: &str,
) -> Vec<u8> {
    let (mode_name, session) = match mode {
        Mode::Enrollment(id) => ("enrollment", id.as_str()),
        _ => ("monitoring", monitoring),
    };
    json!({
        "schema_version": window.schema_version(),
        "device_id": device_id,
        "session_id": session,
        "sequence": window.upload_sequence(),
        "mode": mode_name,
        "started_at": window.started_at(),
        "duration_ms": window.duration_ms(),
        "features": window.features,
    })
    .to_string()
    .into_bytes()
}

/// Normalize a pairing code as typed: case-insensitive, spaces and dashes ignored.
/// Returns None unless it is 8 characters from the server's alphabet.
pub fn normalize_code(input: &str) -> Option<String> {
    let code: String = input
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '-')
        .collect::<String>()
        .to_ascii_uppercase();
    (code.len() == PAIRING_CODE_LEN && code.chars().all(|c| PAIRING_ALPHABET.contains(c)))
        .then_some(code)
}

/// The device name shown in the account's device list: "Inquisitor on <PC name>".
fn device_name() -> String {
    let pc: String = std::env::var("COMPUTERNAME")
        .unwrap_or_default()
        .chars()
        .filter(|c| c.is_ascii_graphic())
        .take(40)
        .collect();
    if pc.is_empty() {
        "Inquisitor tray".into()
    } else {
        format!("Inquisitor on {pc}")
    }
}

/// Redeem a pairing code. Blocks for up to the request timeouts.
pub fn pair(server: &Endpoint, code: &str) -> Result<Credential, String> {
    #[derive(Deserialize)]
    struct Paired {
        device_id: String,
        token: String,
    }
    let code = normalize_code(code)
        .ok_or("The code is 8 letters and numbers, as shown on the website.")?;
    let unreachable = |e: std::io::Error| format!("Could not reach {}: {e}", server.display());
    let client = Client::new(server.clone()).map_err(unreachable)?;
    let body = json!({ "code": code, "name": device_name() }).to_string();
    let response = client
        .request("POST", "/api/agent/pair", None, Some(body.as_bytes()))
        .map_err(unreachable)?;
    match response.status {
        201 => serde_json::from_slice::<Paired>(&response.body)
            .map(|p| Credential {
                device_id: p.device_id,
                token: p.token,
            })
            .map_err(|_| "The website sent an unexpected reply.".to_string()),
        404 => Err("That code is not valid, has expired, or was already used. \
                    Create a new code on the website and try again."
            .into()),
        429 => Err("Too many attempts. Wait a few minutes and try again.".into()),
        status => Err(format!(
            "The website did not accept the code (HTTP {status}). \
             Check the address and that sign-in is set up on the website."
        )),
    }
}

pub enum Command {
    Window(FeatureWindow),
    Paused(bool),
    Connect(Endpoint, Credential),
    Disconnect,
    Shutdown,
    SocketMessage(u64, String),
    SocketClosed(u64),
}

/// Handle to the link thread, owned by the tray.
pub struct Link {
    tx: Sender<Command>,
    status: Arc<Mutex<Status>>,
    done: Receiver<()>,
}

impl Link {
    /// Start the link thread. With a server and credential it connects at once.
    pub fn start(
        notify: HWND,
        server: Option<Endpoint>,
        credential: Option<Credential>,
        paused: bool,
    ) -> Self {
        Self::start_with(
            notify,
            server,
            credential,
            paused,
            credential::default_path(),
        )
    }

    /// `start` with the stored credential at `credential_path`, which is deleted
    /// when the device is disconnected or revoked. Tests pass a temporary path.
    fn start_with(
        notify: HWND,
        server: Option<Endpoint>,
        credential: Option<Credential>,
        paused: bool,
        credential_path: PathBuf,
    ) -> Self {
        let (tx, rx) = mpsc::channel();
        let (done_tx, done) = mpsc::channel();
        let status = Arc::new(Mutex::new(Status {
            state: State::NotPaired,
            server: None,
            mode: Mode::Idle,
            queued: 0,
        }));
        let mut worker = Worker::new(
            tx.clone(),
            status.clone(),
            notify.0 as isize,
            credential_path,
        );
        worker.paused = paused;
        if let (Some(server), Some(credential)) = (server, credential) {
            worker.connect_to(server, credential);
        }
        worker.publish();
        std::thread::Builder::new()
            .name("inquisitor-link".into())
            .spawn(move || {
                worker.run(rx);
                let _ = done_tx.send(());
            })
            .expect("spawn link thread");
        Self { tx, status, done }
    }

    pub fn status(&self) -> Status {
        self.status
            .lock()
            .map(|s| s.clone())
            .unwrap_or_else(|e| e.into_inner().clone())
    }

    pub fn send_window(&self, window: FeatureWindow) {
        let _ = self.tx.send(Command::Window(window));
    }

    pub fn set_paused(&self, paused: bool) {
        let _ = self.tx.send(Command::Paused(paused));
    }

    pub fn connect(&self, server: Endpoint, credential: Credential) {
        let _ = self.tx.send(Command::Connect(server, credential));
    }

    pub fn disconnect(&self) {
        let _ = self.tx.send(Command::Disconnect);
    }
}

impl Drop for Link {
    fn drop(&mut self) {
        // Close the socket so the website shows Offline right away, but never hold
        // up quitting for long if a request is in flight.
        let _ = self.tx.send(Command::Shutdown);
        let _ = self.done.recv_timeout(Duration::from_secs(2));
    }
}

struct Worker {
    tx: Sender<Command>,
    status: Arc<Mutex<Status>>,
    notify: isize,
    credential_path: PathBuf,
    client: Option<Client>,
    credential: Option<Credential>,
    socket: Option<Arc<WebSocket>>,
    generation: u64,
    state: State,
    paused: bool,
    mode: Mode,
    /// Session id for monitoring windows: one per tray run.
    monitoring_session: String,
    queue: Queue,
    upload_backoff: Backoff,
    upload_at: Instant,
    connect_backoff: Backoff,
    connect_at: Option<Instant>,
    heartbeat_at: Instant,
}

impl Worker {
    fn new(
        tx: Sender<Command>,
        status: Arc<Mutex<Status>>,
        notify: isize,
        credential_path: PathBuf,
    ) -> Self {
        Self {
            tx,
            status,
            notify,
            credential_path,
            client: None,
            credential: None,
            socket: None,
            generation: 0,
            state: State::NotPaired,
            paused: false,
            mode: Mode::Idle,
            monitoring_session: format!("tray-{:x}-{:x}", unix_ms_now(), std::process::id()),
            queue: Queue::new(QUEUE_CAP),
            upload_backoff: Backoff::default(),
            upload_at: Instant::now(),
            connect_backoff: Backoff::default(),
            connect_at: None,
            heartbeat_at: Instant::now(),
        }
    }

    fn run(&mut self, rx: Receiver<Command>) {
        loop {
            let wait = self
                .next_deadline()
                .saturating_duration_since(Instant::now());
            match rx.recv_timeout(wait) {
                Ok(Command::Shutdown) | Err(RecvTimeoutError::Disconnected) => break,
                Ok(command) => self.handle(command),
                Err(RecvTimeoutError::Timeout) => {}
            }
            self.service();
            self.publish();
        }
        self.close_socket();
    }

    fn next_deadline(&self) -> Instant {
        let mut next = Instant::now() + Duration::from_secs(60);
        if self.socket.is_some() {
            next = next.min(self.heartbeat_at);
        }
        if let Some(at) = self.connect_at {
            next = next.min(at);
        }
        if !self.queue.is_empty() && self.credential.is_some() {
            next = next.min(self.upload_at);
        }
        next
    }

    fn handle(&mut self, command: Command) {
        match command {
            Command::Window(window) => self.enqueue(&window),
            Command::Paused(paused) => {
                self.paused = paused;
                // Tell the website now rather than at the next heartbeat.
                self.heartbeat_at = Instant::now();
            }
            Command::Connect(server, credential) => {
                self.close_socket();
                self.queue.clear();
                self.connect_to(server, credential);
            }
            Command::Disconnect => {
                self.forget();
                self.state = State::NotPaired;
            }
            Command::SocketMessage(generation, text) if generation == self.generation => {
                self.on_message(&text)
            }
            Command::SocketClosed(generation) if generation == self.generation => {
                self.socket = None;
                self.schedule_reconnect();
            }
            Command::SocketMessage(..) | Command::SocketClosed(_) | Command::Shutdown => {}
        }
    }

    fn connect_to(&mut self, server: Endpoint, credential: Credential) {
        match Client::new(server) {
            Ok(client) => {
                self.client = Some(client);
                self.credential = Some(credential);
                self.connect_backoff.reset();
                self.connect_at = Some(Instant::now());
                self.state = State::Connecting;
            }
            Err(_) => {
                self.credential = Some(credential);
                self.state = State::Offline;
            }
        }
    }

    /// Do whatever is due: reconnect, heartbeat, upload.
    fn service(&mut self) {
        let now = Instant::now();
        if self.socket.is_none() && self.connect_at.is_some_and(|at| at <= now) {
            self.open_socket();
        }
        if self.socket.is_some() && self.heartbeat_at <= Instant::now() {
            self.send_presence("heartbeat");
        }
        while !self.queue.is_empty()
            && self.upload_at <= Instant::now()
            && self.credential.is_some()
        {
            if !self.upload_one() {
                break;
            }
        }
    }

    fn open_socket(&mut self) {
        self.connect_at = None;
        let (Some(client), Some(credential)) = (&self.client, &self.credential) else {
            return;
        };
        match client.websocket("/api/agent/ws", &credential.token) {
            Ok(Ok(socket)) => {
                let socket = Arc::new(socket);
                self.generation += 1;
                let (generation, reader, tx) = (self.generation, socket.clone(), self.tx.clone());
                std::thread::spawn(move || {
                    while let Ok(Some(text)) = reader.receive() {
                        if tx.send(Command::SocketMessage(generation, text)).is_err() {
                            return;
                        }
                    }
                    let _ = tx.send(Command::SocketClosed(generation));
                });
                self.socket = Some(socket);
                self.connect_backoff.reset();
                self.state = State::Connected;
                self.send_presence("hello");
            }
            Ok(Err(401 | 403)) => self.fail_closed(),
            Ok(Err(_)) | Err(_) => self.schedule_reconnect(),
        }
    }

    fn send_presence(&mut self, kind: &str) {
        let mut message = json!({ "v": PROTOCOL, "type": kind, "paused": self.paused });
        if kind == "hello" {
            message["agent_version"] = json!(env!("CARGO_PKG_VERSION"));
        }
        self.heartbeat_at = Instant::now() + HEARTBEAT;
        let sent = self
            .socket
            .as_ref()
            .map(|socket| socket.send(&message.to_string()));
        if let Some(Err(_)) = sent {
            self.close_socket();
            self.schedule_reconnect();
        }
    }

    fn on_message(&mut self, text: &str) {
        #[derive(Deserialize)]
        struct Message {
            v: u32,
            #[serde(rename = "type")]
            kind: String,
            state: Option<String>,
            collection: Option<String>,
            session_id: Option<String>,
        }
        let Ok(message) = serde_json::from_str::<Message>(text) else {
            return;
        };
        if message.v != PROTOCOL {
            return;
        }
        match message.kind.as_str() {
            "status" => {
                self.mode = mode_from(
                    message.state.as_deref(),
                    message.collection.as_deref(),
                    message.session_id.as_deref(),
                );
            }
            "revoked" => self.fail_closed(),
            _ => {}
        }
    }

    fn schedule_reconnect(&mut self) {
        if self.credential.is_none() {
            return;
        }
        self.state = State::Offline;
        self.connect_at = Some(Instant::now() + self.connect_backoff.next(jitter()));
    }

    fn enqueue(&mut self, window: &FeatureWindow) {
        // Nothing is uploaded unless paired and the website asked for this kind of window.
        let Some(credential) = &self.credential else {
            return;
        };
        if self.paused || self.mode == Mode::Idle {
            return;
        }
        self.queue.push(Queued {
            body: window_body(
                window,
                &credential.device_id,
                &self.mode,
                &self.monitoring_session,
            ),
            ended_unix_ms: window.ended_unix_ms(),
        });
    }

    /// Upload the oldest queued window. Returns false when uploads should pause.
    fn upload_one(&mut self) -> bool {
        self.queue.expire(unix_ms_now());
        let (Some(client), Some(credential), Some(item)) =
            (&self.client, &self.credential, self.queue.front())
        else {
            return false;
        };
        let outcome = match client.request(
            "POST",
            "/api/agent/windows",
            Some(&credential.token),
            Some(&item.body),
        ) {
            Ok(response) => classify(response.status),
            Err(_) => UploadOutcome::Retry,
        };
        self.apply_upload(outcome)
    }

    fn apply_upload(&mut self, outcome: UploadOutcome) -> bool {
        match outcome {
            UploadOutcome::Sent | UploadOutcome::Drop => {
                self.queue.pop();
                self.upload_backoff.reset();
                true
            }
            UploadOutcome::Retry => {
                self.upload_at = Instant::now() + self.upload_backoff.next(jitter());
                false
            }
            UploadOutcome::FailClosed => {
                self.fail_closed();
                false
            }
        }
    }

    /// The website no longer accepts this device (#99).
    fn fail_closed(&mut self) {
        self.forget();
        self.state = State::Disconnected("This PC was removed from the account.".into());
    }

    /// Stop everything and delete the stored credential.
    fn forget(&mut self) {
        self.close_socket();
        self.queue.clear();
        self.client = None;
        self.credential = None;
        self.connect_at = None;
        self.mode = Mode::Idle;
        let _ = credential::delete_at(&self.credential_path);
    }

    fn close_socket(&mut self) {
        if let Some(socket) = self.socket.take() {
            socket.close();
        }
        // Messages from the old socket's reader are ignored from now on.
        self.generation += 1;
    }

    /// Share the status with the UI thread and wake it when something changed.
    fn publish(&self) {
        let status = Status {
            state: self.state.clone(),
            server: self.client.as_ref().map(|c| c.endpoint().display()),
            mode: self.mode.clone(),
            queued: self.queue.len(),
        };
        let changed = {
            let mut shared = self.status.lock().unwrap_or_else(|e| e.into_inner());
            let changed = shared.state != status.state
                || shared.mode != status.mode
                || shared.server != status.server;
            *shared = status;
            changed
        };
        if changed && self.notify != 0 {
            unsafe {
                let _ = PostMessageW(
                    Some(HWND(self.notify as *mut c_void)),
                    WM_LINK,
                    WPARAM(0),
                    LPARAM(0),
                );
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn queued(ended_unix_ms: i64) -> Queued {
        Queued {
            body: Vec::new(),
            ended_unix_ms,
        }
    }

    #[test]
    fn queue_is_bounded_and_drops_expired_windows() {
        let mut queue = Queue::new(3);
        for ended in [1, 2, 3, 4] {
            queue.push(queued(ended));
        }
        assert_eq!(queue.len(), 3);
        assert_eq!(queue.front().unwrap().ended_unix_ms, 2);
        queue.expire(3 + QUEUE_MAX_AGE_MS);
        assert_eq!(queue.len(), 2);
        assert_eq!(queue.front().unwrap().ended_unix_ms, 3);
    }

    #[test]
    fn backoff_doubles_to_a_one_minute_cap_and_resets() {
        let mut backoff = Backoff::default();
        let delays: Vec<u64> = (0..9).map(|_| backoff.next(1.0).as_secs()).collect();
        assert_eq!(delays, [1, 2, 4, 8, 16, 32, 60, 60, 60]);
        assert_eq!(backoff.next(0.0), Duration::from_secs(30));
        backoff.reset();
        assert_eq!(backoff.next(0.0), Duration::from_millis(500));
    }

    #[test]
    fn upload_status_codes_map_to_actions() {
        assert_eq!(classify(201), UploadOutcome::Sent);
        assert_eq!(classify(401), UploadOutcome::FailClosed);
        assert_eq!(classify(403), UploadOutcome::FailClosed);
        assert_eq!(classify(429), UploadOutcome::Retry);
        assert_eq!(classify(502), UploadOutcome::Retry);
        assert_eq!(classify(409), UploadOutcome::Drop);
        assert_eq!(classify(422), UploadOutcome::Drop);
    }

    #[test]
    fn mode_follows_the_onboarding_status() {
        assert_eq!(
            mode_from(Some("collecting"), Some("active"), Some("round-1")),
            Mode::Enrollment("round-1".into())
        );
        assert_eq!(
            mode_from(Some("collecting"), Some("active"), None),
            Mode::Idle
        );
        assert_eq!(
            mode_from(Some("collecting"), Some("paused"), Some("round-1")),
            Mode::Idle
        );
        assert_eq!(
            mode_from(Some("ready"), Some("finished"), None),
            Mode::Monitoring
        );
        assert_eq!(mode_from(Some("training"), None, None), Mode::Idle);
        assert_eq!(mode_from(None, None, None), Mode::Idle);
    }

    #[test]
    fn pairing_codes_are_normalized_and_validated() {
        assert_eq!(normalize_code(" abcd-2345 ").as_deref(), Some("ABCD2345"));
        assert_eq!(normalize_code("ABCD234"), None);
        // 0, 1, I, and O are not in the server's alphabet.
        assert_eq!(normalize_code("ABCD2340"), None);
        assert_eq!(normalize_code("ABCDI345"), None);
    }

    #[test]
    fn rejected_upload_fails_closed_clearing_queue_and_credential() {
        let dir = std::env::temp_dir().join(format!("inquisitor-test-{}-link", std::process::id()));
        let path = dir.join("device.bin");
        let saved = Credential {
            device_id: "device-1".into(),
            token: "token-1".into(),
        };
        credential::save_to(&path, &saved).unwrap();

        let (tx, _rx) = mpsc::channel();
        let status = Arc::new(Mutex::new(Status {
            state: State::NotPaired,
            server: None,
            mode: Mode::Idle,
            queued: 0,
        }));
        let mut worker = Worker::new(tx, status.clone(), 0, path.clone());
        worker.credential = Some(saved);
        worker.state = State::Connected;
        worker.mode = Mode::Monitoring;
        worker.queue.push(queued(unix_ms_now()));
        worker.queue.push(queued(unix_ms_now()));

        // A retryable failure keeps everything.
        assert!(!worker.apply_upload(UploadOutcome::Retry));
        assert_eq!(worker.queue.len(), 2);
        assert!(path.exists());

        assert!(!worker.apply_upload(UploadOutcome::FailClosed));
        assert!(worker.queue.is_empty());
        assert!(worker.credential.is_none());
        assert!(!path.exists());
        assert!(matches!(worker.state, State::Disconnected(_)));
        assert_eq!(worker.mode, Mode::Idle);
        worker.publish();
        assert!(matches!(
            status.lock().unwrap().state,
            State::Disconnected(_)
        ));

        // Nothing is queued once disconnected.
        let window = crate::features::FeatureWindow {
            index: 0,
            started_unix_ms: unix_ms_now(),
            features: crate::features::sample_features(),
        };
        worker.enqueue(&window);
        assert!(worker.queue.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// End to end against a local hosted-mode test server with pairing code
    /// ABCD2345 seeded and `/__test/*` helper routes. Not run by default:
    /// `INQUISITOR_TEST_SERVER=http://127.0.0.1:5077 cargo test -- --ignored live_`
    #[test]
    #[ignore = "needs a local test server; set INQUISITOR_TEST_SERVER"]
    fn live_pair_socket_upload_and_revoke() {
        use serde_json::Value;
        let url = std::env::var("INQUISITOR_TEST_SERVER").expect("INQUISITOR_TEST_SERVER");
        let endpoint = Endpoint::parse(&url).unwrap();
        let credential = pair(&endpoint, "abcd-2345").unwrap();
        let client = Client::new(endpoint.clone()).unwrap();
        let get = |path: &str| -> Value {
            let response = client.request("GET", path, None, None).unwrap();
            serde_json::from_slice(&response.body).unwrap()
        };
        let post = |path: &str| {
            assert_eq!(
                client.request("POST", path, None, None).unwrap().status,
                200
            )
        };
        let device = format!("/__test/device/{}", credential.device_id);

        let socket = client
            .websocket("/api/agent/ws", &credential.token)
            .unwrap()
            .unwrap();
        socket
            .send(r#"{"v":1,"type":"hello","paused":false}"#)
            .unwrap();
        let status: Value = serde_json::from_str(&socket.receive().unwrap().unwrap()).unwrap();
        assert_eq!(
            (status["type"].as_str(), status["state"].as_str()),
            (Some("status"), Some("collecting"))
        );
        assert_eq!(get(&device)["ws_connected"], true);

        socket
            .send(r#"{"v":1,"type":"heartbeat","paused":true}"#)
            .unwrap();
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(get(&device)["agent_paused"], true);

        // A trained model: the next heartbeat reports the new status, and monitoring windows are scored.
        post(&format!("/__test/ready/{}", credential.device_id));
        socket
            .send(r#"{"v":1,"type":"heartbeat","paused":false}"#)
            .unwrap();
        let status: Value = serde_json::from_str(&socket.receive().unwrap().unwrap()).unwrap();
        let mode = mode_from(
            status["state"].as_str(),
            status["collection"].as_str(),
            status["session_id"].as_str(),
        );
        assert_eq!(mode, Mode::Monitoring);
        let window = FeatureWindow {
            index: 0,
            started_unix_ms: unix_ms_now() - 60_000,
            features: crate::features::sample_features(),
        };
        let body = window_body(&window, &credential.device_id, &mode, "tray-live-test");
        let upload = |body: &[u8]| {
            client
                .request(
                    "POST",
                    "/api/agent/windows",
                    Some(&credential.token),
                    Some(body),
                )
                .unwrap()
                .status
        };
        assert_eq!(classify(upload(&body)), UploadOutcome::Sent);
        assert_eq!(get(&device)["monitoring"], 1);
        // The same window again is a duplicate, which is dropped rather than retried.
        assert_eq!(classify(upload(&body)), UploadOutcome::Drop);

        // Revocation reaches the open socket at the next heartbeat, and uploads fail closed.
        post(&format!("/__test/revoke/{}", credential.device_id));
        socket
            .send(r#"{"v":1,"type":"heartbeat","paused":false}"#)
            .unwrap();
        let revoked: Value = serde_json::from_str(&socket.receive().unwrap().unwrap()).unwrap();
        assert_eq!(revoked["type"], "revoked");
        assert_eq!(socket.receive().unwrap(), None);
        assert_eq!(classify(upload(&body)), UploadOutcome::FailClosed);
        assert!(matches!(
            client.websocket("/api/agent/ws", &credential.token),
            Ok(Err(401))
        ));
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(get(&device)["ws_connected"], false);
    }

    /// The tray's background worker against the same test server: connects,
    /// learns its mode, uploads, reports pause, and fails closed on revocation.
    /// Needs a fresh server (the pairing code works once).
    /// `INQUISITOR_TEST_SERVER=http://127.0.0.1:5077 cargo test -- --ignored live_worker`
    #[test]
    #[ignore = "needs a local test server; set INQUISITOR_TEST_SERVER"]
    fn live_worker_uploads_and_fails_closed() {
        use serde_json::Value;
        let url = std::env::var("INQUISITOR_TEST_SERVER").expect("INQUISITOR_TEST_SERVER");
        let endpoint = Endpoint::parse(&url).unwrap();
        let credential = pair(&endpoint, "ABCD2345").unwrap();
        let client = Client::new(endpoint.clone()).unwrap();
        let get = |path: &str| -> Value {
            serde_json::from_slice(&client.request("GET", path, None, None).unwrap().body).unwrap()
        };
        let post = |path: &str| {
            assert_eq!(
                client.request("POST", path, None, None).unwrap().status,
                200
            )
        };
        let device = format!("/__test/device/{}", credential.device_id);
        let wait_for = |what: &str, check: &dyn Fn() -> bool| {
            let deadline = Instant::now() + Duration::from_secs(10);
            while !check() {
                assert!(Instant::now() < deadline, "timed out waiting for {what}");
                std::thread::sleep(Duration::from_millis(100));
            }
        };
        let dir = std::env::temp_dir().join(format!("inquisitor-test-{}-live", std::process::id()));
        let path = dir.join("device.bin");
        credential::save_to(&path, &credential).unwrap();

        // A trained model, so the hello reply puts the worker in monitoring mode.
        post(&format!("/__test/ready/{}", credential.device_id));
        let link = Link::start_with(
            HWND::default(),
            Some(endpoint),
            Some(credential.clone()),
            false,
            path.clone(),
        );
        wait_for("monitoring", &|| {
            link.status().state == State::Connected && link.status().mode == Mode::Monitoring
        });
        assert_eq!(get(&device)["ws_connected"], true);

        let window = |minutes_ago: i64| FeatureWindow {
            index: 0,
            started_unix_ms: unix_ms_now() - minutes_ago * 60_000,
            features: crate::features::sample_features(),
        };
        link.send_window(window(2));
        wait_for("upload", &|| get(&device)["monitoring"] == 1);
        link.set_paused(true);
        wait_for("pause", &|| get(&device)["agent_paused"] == true);
        link.set_paused(false);
        wait_for("resume", &|| get(&device)["agent_paused"] == false);

        post(&format!("/__test/revoke/{}", credential.device_id));
        link.send_window(window(1));
        wait_for("disconnect", &|| {
            matches!(link.status().state, State::Disconnected(_))
        });
        assert!(!path.exists(), "credential kept after revocation");
        assert_eq!(link.status().queued, 0);
        drop(link);
        assert_eq!(get(&device)["ws_connected"], false);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn window_body_carries_mode_session_and_slot_sequence() {
        let window = crate::features::FeatureWindow {
            index: 3,
            started_unix_ms: 1_767_225_600_250,
            features: crate::features::sample_features(),
        };
        let body: serde_json::Value = serde_json::from_slice(&window_body(
            &window,
            "device-1",
            &Mode::Enrollment("round-9".into()),
            "tray-run",
        ))
        .unwrap();
        assert_eq!(body["mode"], "enrollment");
        assert_eq!(body["session_id"], "round-9");
        assert_eq!(body["schema_version"], 4);
        assert_eq!(body["sequence"], 58_907_520);
        assert_eq!(body["started_at"], "2026-01-01T00:00:00.250Z");
        assert_eq!(body["features"]["key_count"], 40);
        assert_eq!(body["features"]["mouse_sample_count"], 12);
        let monitoring: serde_json::Value = serde_json::from_slice(&window_body(
            &window,
            "device-1",
            &Mode::Monitoring,
            "tray-run",
        ))
        .unwrap();
        assert_eq!(
            (
                monitoring["mode"].as_str(),
                monitoring["session_id"].as_str()
            ),
            (Some("monitoring"), Some("tray-run"))
        );
    }
}
