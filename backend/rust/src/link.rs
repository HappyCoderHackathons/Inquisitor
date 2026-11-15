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
//! - Every finished summary is held on disk (`outbox.jsonl`) until it can be
//!   sent: while offline, across restarts, and while not yet connected to an
//!   account. Nothing expires. Retries back off exponentially with jitter, from
//!   1 second up to 1 minute.
//! - Fail closed: a 401 or 403, or a `revoked` message, stops uploads, clears the
//!   held summaries, and deletes the stored credential. The tray shows Disconnected.
//! - Messages from the socket wait at most `SOCKET_BACKLOG` deep; beyond that the
//!   reader stops reading until the worker catches up.
//! - State changes are recorded in `agent.log` (see `agent_log.rs`).
//!
//! Only aggregate numeric windows and the pause flag leave the PC.

use std::collections::VecDeque;
use std::ffi::c_void;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::json;
use windows::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows::Win32::UI::WindowsAndMessaging::{PostMessageW, WM_APP};

use crate::agent_log::AgentLog;
use crate::capture::unix_ms_now;
use crate::credential::{self, Credential};
use crate::features::FeatureWindow;
use crate::http::{Client, Endpoint, WebSocket};

/// Posted to the tray window whenever the link's status changes.
pub const WM_LINK: u32 = WM_APP + 2;

const PROTOCOL: u32 = 1;
const HEARTBEAT: Duration = Duration::from_secs(30);
// Held summaries never expire. This only bounds the file: 100,000 summaries is
// about a month of non-stop activity, around 70 MB.
const OUTBOX_CAP: usize = 100_000;
const BACKOFF_MAX_SECS: u64 = 60;
// Each message is at most 64 KiB (`http::MAX_MESSAGE`), so this bounds the backlog to 4 MiB.
const SOCKET_BACKLOG: usize = 64;
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

/// What a held summary is for, decided when it was made.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "purpose", rename_all = "lowercase")]
pub enum Purpose {
    /// Made while the website was collecting enrollment for this game round.
    Enrollment { session_id: String },
    /// Made while monitoring, or while unpaired, offline before the website said
    /// what to collect, or not collecting. Sent as monitoring once a model exists.
    Monitoring,
}

/// One finished summary waiting to be sent.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Held {
    pub window: FeatureWindow,
    #[serde(flatten)]
    pub purpose: Purpose,
}

impl Held {
    /// Enrollment summaries go whenever the PC is connected (the website refuses
    /// them once their round has ended). Monitoring ones wait for a trained model,
    /// so a summary made before the user connected is not refused for arriving early.
    fn sendable(&self, mode: &Mode) -> bool {
        matches!(self.purpose, Purpose::Enrollment { .. }) || *mode == Mode::Monitoring
    }
}

/// `%APPDATA%\inquisitor\outbox.jsonl`, next to the device credential.
pub fn outbox_path() -> PathBuf {
    credential::default_path().with_file_name("outbox.jsonl")
}

/// Summaries held until they can be sent, kept on disk so they survive offline
/// spells and restarts. One JSON object per line: the same aggregate numbers that
/// are uploaded, never key or event data. Nothing expires; the cap only guards the
/// disk (about a month of non-stop activity), dropping the oldest beyond it.
pub struct Outbox {
    items: VecDeque<Held>,
    path: PathBuf,
    /// The file no longer matches `items` and must be rewritten.
    dirty: bool,
}

impl Outbox {
    /// Read held summaries back. Unreadable lines are skipped.
    pub fn load(path: PathBuf) -> Self {
        let items = fs::read_to_string(&path)
            .map(|text| {
                text.lines()
                    .filter_map(|line| serde_json::from_str(line).ok())
                    .collect()
            })
            .unwrap_or_default();
        Self {
            items,
            path,
            dirty: false,
        }
    }

    pub fn push(&mut self, item: Held) {
        if self.items.len() >= OUTBOX_CAP {
            self.items.pop_front();
            self.dirty = true;
        }
        if !self.dirty {
            // Append rather than rewrite; a failed append rewrites the file later.
            let line = serde_json::to_string(&item).unwrap_or_default();
            let appended = fs::create_dir_all(self.path.parent().unwrap_or(Path::new(".")))
                .and_then(|()| {
                    let mut file = OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(&self.path)?;
                    writeln!(file, "{line}")
                });
            self.dirty = appended.is_err();
        }
        self.items.push_back(item);
    }

    /// Index of the oldest summary that can be sent in `mode`.
    pub fn next_sendable(&self, mode: &Mode) -> Option<usize> {
        self.items.iter().position(|item| item.sendable(mode))
    }

    pub fn get(&self, index: usize) -> Option<&Held> {
        self.items.get(index)
    }

    pub fn remove(&mut self, index: usize) {
        if self.items.remove(index).is_some() {
            self.dirty = true;
        }
    }

    /// Rewrite the file after removals. A crash before this only means a summary
    /// is sent twice, which the website refuses as a duplicate.
    pub fn save(&mut self) {
        if !self.dirty {
            return;
        }
        if self.is_empty() {
            self.dirty = credential::delete_at(&self.path).is_err();
            return;
        }
        let mut text = String::new();
        for item in &self.items {
            text.push_str(&serde_json::to_string(item).unwrap_or_default());
            text.push('\n');
        }
        let staging = self.path.with_extension("jsonl.tmp");
        let written = fs::create_dir_all(self.path.parent().unwrap_or(Path::new(".")))
            .and_then(|()| fs::write(&staging, text))
            .and_then(|()| fs::rename(&staging, &self.path));
        self.dirty = written.is_err();
    }

    /// Forget everything held, on disconnect or revocation.
    pub fn clear(&mut self) {
        self.items.clear();
        self.dirty = true;
        self.save();
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }
}

/// The JSON body for `POST /api/agent/windows`.
pub fn window_body(held: &Held, device_id: &str, monitoring_session: &str) -> Vec<u8> {
    let window = &held.window;
    let (mode_name, session) = match &held.purpose {
        Purpose::Enrollment { session_id } => ("enrollment", session_id.as_str()),
        Purpose::Monitoring => ("monitoring", monitoring_session),
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
    pub(crate) fn start_with(
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
    /// One slot per socket message waiting to be handled; see `relay`.
    socket_backlog: Option<Receiver<()>>,
    generation: u64,
    state: State,
    paused: bool,
    mode: Mode,
    /// Session id for monitoring windows: one per tray run.
    monitoring_session: String,
    outbox: Outbox,
    upload_backoff: Backoff,
    upload_at: Instant,
    connect_backoff: Backoff,
    connect_at: Option<Instant>,
    heartbeat_at: Instant,
    log: AgentLog,
}

/// Forward socket messages to the worker. Each takes a backlog slot first, which
/// blocks once `SOCKET_BACKLOG` are waiting, so a flood from the server cannot
/// grow memory. Ends when the socket closes or the worker drops the backlog.
fn relay(
    generation: u64,
    tx: &Sender<Command>,
    backlog: &SyncSender<()>,
    mut receive: impl FnMut() -> io::Result<Option<String>>,
) {
    while let Ok(Some(text)) = receive() {
        if backlog.send(()).is_err() || tx.send(Command::SocketMessage(generation, text)).is_err() {
            return;
        }
    }
    let _ = tx.send(Command::SocketClosed(generation));
}

fn state_event(state: &State) -> &'static str {
    match state {
        State::NotPaired => "not connected to an account",
        State::Connecting => "connecting",
        State::Connected => "connected",
        State::Offline => "offline, retrying",
        State::Disconnected(_) => "disconnected: the website no longer accepts this device",
    }
}

impl Worker {
    fn new(
        tx: Sender<Command>,
        status: Arc<Mutex<Status>>,
        notify: isize,
        credential_path: PathBuf,
    ) -> Self {
        // Held summaries live next to the credential, so tests using a temporary
        // credential path get a temporary outbox too.
        let outbox = Outbox::load(credential_path.with_file_name("outbox.jsonl"));
        let log = AgentLog::at(credential_path.with_file_name("agent.log"));
        Self {
            tx,
            status,
            notify,
            credential_path,
            client: None,
            credential: None,
            socket: None,
            socket_backlog: None,
            generation: 0,
            state: State::NotPaired,
            paused: false,
            mode: Mode::Idle,
            monitoring_session: format!("tray-{:x}-{:x}", unix_ms_now(), std::process::id()),
            outbox,
            upload_backoff: Backoff::default(),
            upload_at: Instant::now(),
            connect_backoff: Backoff::default(),
            connect_at: None,
            heartbeat_at: Instant::now(),
            log,
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
        if self.credential.is_some() && self.outbox.next_sendable(&self.mode).is_some() {
            next = next.min(self.upload_at);
        }
        next
    }

    fn handle(&mut self, command: Command) {
        match command {
            Command::Window(window) => self.enqueue(&window),
            Command::Paused(paused) => {
                if paused != self.paused {
                    self.log.event(if paused { "paused" } else { "resumed" });
                }
                self.paused = paused;
                // Tell the website now rather than at the next heartbeat.
                self.heartbeat_at = Instant::now();
            }
            Command::Connect(server, credential) => {
                // Summaries held from before pairing are kept and sent once monitoring starts.
                self.log.event("paired with an account");
                self.close_socket();
                self.connect_to(server, credential);
            }
            Command::Disconnect => {
                self.forget();
                self.state = State::NotPaired;
            }
            Command::SocketMessage(generation, text) if generation == self.generation => {
                if let Some(backlog) = &self.socket_backlog {
                    let _ = backlog.try_recv();
                }
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
        while self.credential.is_some() && self.upload_at <= Instant::now() {
            let Some(index) = self.outbox.next_sendable(&self.mode) else {
                break;
            };
            if !self.upload_one(index) {
                break;
            }
        }
        self.outbox.save();
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
                let (slot, backlog) = mpsc::sync_channel(SOCKET_BACKLOG);
                std::thread::spawn(move || relay(generation, &tx, &slot, || reader.receive()));
                self.socket = Some(socket);
                self.socket_backlog = Some(backlog);
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

    /// Hold every finished summary. Its purpose is fixed now: enrollment for the
    /// active round if the website is collecting one, otherwise monitoring, which
    /// also covers summaries made while unpaired or offline before any status.
    fn enqueue(&mut self, window: &FeatureWindow) {
        if self.paused {
            return;
        }
        let purpose = match (&self.mode, &self.credential) {
            (Mode::Enrollment(session_id), Some(_)) => Purpose::Enrollment {
                session_id: session_id.clone(),
            },
            _ => Purpose::Monitoring,
        };
        self.outbox.push(Held {
            window: window.clone(),
            purpose,
        });
    }

    /// Upload one held summary. Returns false when uploads should pause.
    fn upload_one(&mut self, index: usize) -> bool {
        let (Some(client), Some(credential), Some(held)) =
            (&self.client, &self.credential, self.outbox.get(index))
        else {
            return false;
        };
        let body = window_body(held, &credential.device_id, &self.monitoring_session);
        let outcome = match client.request(
            "POST",
            "/api/agent/windows",
            Some(&credential.token),
            Some(&body),
        ) {
            Ok(response) => classify(response.status),
            Err(_) => UploadOutcome::Retry,
        };
        self.apply_upload(index, outcome)
    }

    fn apply_upload(&mut self, index: usize, outcome: UploadOutcome) -> bool {
        match outcome {
            UploadOutcome::Sent | UploadOutcome::Drop => {
                self.outbox.remove(index);
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
        self.outbox.clear();
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
        self.socket_backlog = None;
        // Messages from the old socket's reader are ignored from now on.
        self.generation += 1;
    }

    /// Share the status with the UI thread and wake it when something changed.
    fn publish(&self) {
        let status = Status {
            state: self.state.clone(),
            server: self.client.as_ref().map(|c| c.endpoint().display()),
            mode: self.mode.clone(),
            queued: self.outbox.len(),
        };
        let changed = {
            let mut shared = self.status.lock().unwrap_or_else(|e| e.into_inner());
            let state_changed = shared.state != status.state;
            let changed =
                state_changed || shared.mode != status.mode || shared.server != status.server;
            if state_changed {
                self.log.event(state_event(&status.state));
            }
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

    fn held(started_unix_ms: i64, purpose: Purpose) -> Held {
        Held {
            window: FeatureWindow {
                index: 0,
                started_unix_ms,
                features: crate::features::sample_features(),
            },
            purpose,
        }
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("inquisitor-test-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn held_summaries_survive_a_restart_and_never_expire() {
        let dir = temp_dir("outbox");
        let path = dir.join("outbox.jsonl");
        let year_old = unix_ms_now() - 365 * 24 * 60 * 60 * 1000;
        let mut outbox = Outbox::load(path.clone());
        outbox.push(held(year_old, Purpose::Monitoring));
        outbox.push(held(
            year_old + 30_000,
            Purpose::Enrollment {
                session_id: "round-1".into(),
            },
        ));
        outbox.push(held(year_old + 60_000, Purpose::Monitoring));

        // A new tray process reads them back in order, however old they are.
        let mut reloaded = Outbox::load(path.clone());
        assert_eq!(reloaded.len(), 3);
        assert_eq!(reloaded.get(0).unwrap().window.started_unix_ms, year_old);

        // Monitoring summaries wait for a trained model; enrollment ones do not.
        assert_eq!(reloaded.next_sendable(&Mode::Idle), Some(1));
        assert_eq!(
            reloaded.next_sendable(&Mode::Enrollment("round-2".into())),
            Some(1)
        );
        assert_eq!(reloaded.next_sendable(&Mode::Monitoring), Some(0));

        // Sent summaries are removed from the file too.
        reloaded.remove(1);
        reloaded.save();
        let after = Outbox::load(path.clone());
        assert_eq!(after.len(), 2);
        assert_eq!(after.next_sendable(&Mode::Idle), None);

        // A damaged line is skipped instead of losing the rest.
        fs::write(
            &path,
            format!("not json\n{}", fs::read_to_string(&path).unwrap()),
        )
        .unwrap();
        assert_eq!(Outbox::load(path.clone()).len(), 2);

        let mut cleared = Outbox::load(path.clone());
        cleared.clear();
        assert!(!path.exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn socket_messages_wait_at_most_the_backlog() {
        let (tx, rx) = mpsc::channel();
        let (slot, backlog) = mpsc::sync_channel(SOCKET_BACKLOG);
        // A server that never stops sending, to a worker that is busy.
        std::thread::spawn(move || relay(7, &tx, &slot, || Ok(Some("{}".into()))));
        let next = || rx.recv_timeout(Duration::from_millis(300));
        let mut waiting = 0;
        while let Ok(Command::SocketMessage(7, _)) = next() {
            waiting += 1;
        }
        assert_eq!(waiting, SOCKET_BACKLOG);

        // Handling one lets one more in; dropping the backlog ends the reader.
        backlog.try_recv().unwrap();
        assert!(matches!(next(), Ok(Command::SocketMessage(7, _))));
        drop(backlog);
        assert!(matches!(
            rx.recv_timeout(Duration::from_secs(5)),
            Err(RecvTimeoutError::Disconnected)
        ));
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
    fn rejected_upload_fails_closed_clearing_held_summaries_and_credential() {
        let dir = temp_dir("link");
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
        worker.outbox.push(held(unix_ms_now(), Purpose::Monitoring));
        worker.outbox.push(held(unix_ms_now(), Purpose::Monitoring));
        let outbox = dir.join("outbox.jsonl");
        assert!(outbox.exists());

        // A retryable failure keeps everything.
        assert!(!worker.apply_upload(0, UploadOutcome::Retry));
        assert_eq!(worker.outbox.len(), 2);
        assert!(path.exists());

        assert!(!worker.apply_upload(0, UploadOutcome::FailClosed));
        assert!(worker.outbox.is_empty());
        assert!(!outbox.exists());
        assert!(worker.credential.is_none());
        assert!(!path.exists());
        assert!(matches!(worker.state, State::Disconnected(_)));
        assert_eq!(worker.mode, Mode::Idle);
        worker.publish();
        assert!(matches!(
            status.lock().unwrap().state,
            State::Disconnected(_)
        ));

        // New summaries are held again for a future pairing, as monitoring.
        worker.enqueue(&held(unix_ms_now(), Purpose::Monitoring).window);
        assert_eq!(worker.outbox.get(0).unwrap().purpose, Purpose::Monitoring);
        // Paused input is never held.
        worker.handle(Command::Paused(true));
        worker.enqueue(&held(unix_ms_now(), Purpose::Monitoring).window);
        assert_eq!(worker.outbox.len(), 1);

        // The log holds the state changes, each a fixed message after a timestamp.
        let log = fs::read_to_string(dir.join("agent.log")).unwrap();
        let events: Vec<&str> = log.lines().map(|l| l.split_once(' ').unwrap().1).collect();
        assert_eq!(
            events,
            [state_event(&State::Disconnected(String::new())), "paused"]
        );
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
        let held = Held {
            window,
            purpose: Purpose::Monitoring,
        };
        let body = window_body(&held, &credential.device_id, "tray-live-test");
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

    /// Summaries made while unpaired and while offline are held on disk, survive a
    /// restart, and are sent once the PC reaches the website and a model exists,
    /// even when they are days old. Needs a fresh test server.
    /// `INQUISITOR_TEST_SERVER=http://127.0.0.1:5077 cargo test -- --ignored live_hold`
    #[test]
    #[ignore = "needs a local test server; set INQUISITOR_TEST_SERVER"]
    fn live_hold_summaries_until_connected() {
        use serde_json::Value;
        let url = std::env::var("INQUISITOR_TEST_SERVER").expect("INQUISITOR_TEST_SERVER");
        let endpoint = Endpoint::parse(&url).unwrap();
        let credential = pair(&endpoint, "ABCD2345").unwrap();
        let client = Client::new(endpoint.clone()).unwrap();
        let get = |path: &str| -> Value {
            serde_json::from_slice(&client.request("GET", path, None, None).unwrap().body).unwrap()
        };
        let device = format!("/__test/device/{}", credential.device_id);
        let wait_for = |what: &str, check: &dyn Fn() -> bool| {
            let deadline = Instant::now() + Duration::from_secs(10);
            while !check() {
                assert!(Instant::now() < deadline, "timed out waiting for {what}");
                std::thread::sleep(Duration::from_millis(100));
            }
        };
        let dir = temp_dir("live-hold");
        let path = dir.join("device.bin");
        let summary = |minutes_ago: i64| FeatureWindow {
            index: 0,
            started_unix_ms: unix_ms_now() - minutes_ago * 60_000,
            features: crate::features::sample_features(),
        };

        // Not paired yet: the summary is held, not dropped.
        let link = Link::start_with(HWND::default(), None, None, false, path.clone());
        link.send_window(summary(3 * 24 * 60));
        wait_for("held while unpaired", &|| link.status().queued == 1);
        // Paired but the website is unreachable (nothing listens on port 9).
        let dead = Endpoint::parse("http://127.0.0.1:9").unwrap();
        link.connect(dead, credential.clone());
        link.send_window(summary(2 * 24 * 60));
        wait_for("held while offline", &|| {
            link.status().queued == 2 && link.status().state == State::Offline
        });
        drop(link);
        assert_eq!(get(&device)["monitoring"], 0);

        // Back online after a restart, with a trained model: both are sent.
        assert_eq!(
            client
                .request(
                    "POST",
                    &format!("/__test/ready/{}", credential.device_id),
                    None,
                    None
                )
                .unwrap()
                .status,
            200
        );
        credential::save_to(&path, &credential).unwrap();
        let link = Link::start_with(
            HWND::default(),
            Some(endpoint),
            Some(credential.clone()),
            false,
            path.clone(),
        );
        wait_for("held summaries sent", &|| get(&device)["monitoring"] == 2);
        wait_for("outbox emptied", &|| link.status().queued == 0);
        assert!(!dir.join("outbox.jsonl").exists());
        drop(link);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn window_body_carries_mode_session_and_slot_sequence() {
        let enrollment = held(
            1_767_225_600_250,
            Purpose::Enrollment {
                session_id: "round-9".into(),
            },
        );
        let body: serde_json::Value =
            serde_json::from_slice(&window_body(&enrollment, "device-1", "tray-run")).unwrap();
        assert_eq!(body["mode"], "enrollment");
        assert_eq!(body["session_id"], "round-9");
        assert_eq!(body["schema_version"], 4);
        assert_eq!(body["sequence"], 58_907_520);
        assert_eq!(body["started_at"], "2026-01-01T00:00:00.250Z");
        assert_eq!(body["features"]["key_count"], 40);
        assert_eq!(body["features"]["mouse_sample_count"], 12);
        let monitoring: serde_json::Value = serde_json::from_slice(&window_body(
            &held(1_767_225_600_250, Purpose::Monitoring),
            "device-1",
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
