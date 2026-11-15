//! Live aggregate feature windows for upload.
//!
//! This is the same calculation `backend/ml/tray_csv.py` performs on a capture
//! CSV, run on rows as they are written, so the website receives exactly what the
//! offline converter would produce. `shared/fixtures/tray-capture.*` pins the two
//! together: a test here and one in `tests/test_tray_multimodal.py` both check it.
//!
//! - Windows are 30 seconds, anchored to the first row of the session. A window is
//!   finished when a later row (or a `tick`) passes its end; the window still open
//!   when capture stops is never sent, so no window is stretched or padded.
//! - Every window is schema v4: keyboard press count, completed holds, and the
//!   intervals between presses, plus mouse motion summed into 20 ms bins, speed,
//!   turning, path straightness, and click timing.
//! - Windows with no presses, no motion, and no clicks are skipped, so idle time
//!   never becomes zero-valued samples.
//!
//! Inputs are timings and relative motion counts only. Key identity never reaches
//! this module.
//!
//! Live use differs from the converter in one place: the converter refuses a CSV
//! with a second keyboard or mouse, while here the first keyboard seen stays the
//! keyboard source (others are ignored) and all allowed mice are summed.

use std::collections::BTreeMap;
use std::f64::consts::PI;

use serde::{Deserialize, Serialize};

use crate::capture::iso_utc;

pub const WINDOW_SECONDS: f64 = 30.0;
const WINDOW_MS: u64 = 30_000;
const MOUSE_BIN_SECONDS: f64 = 0.02;
// Consecutive motion bins further apart than this do not count as a turn.
const TURN_GAP_SECONDS: f64 = 0.25;

/// One capture row: the CSV columns this module needs.
pub struct Row<'a> {
    pub monotonic_s: f64,
    pub io_type: &'a str,
    pub device_id: &'a str,
    pub event: &'a str,
    pub hold_ms: Option<f64>,
    pub dx: Option<i32>,
    pub dy: Option<i32>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KeyboardFeatures {
    pub key_count: usize,
    pub hold_sample_count: usize,
    pub hold_mean_ms: f64,
    pub hold_std_ms: f64,
    pub press_interval_mean_ms: f64,
    pub press_interval_std_ms: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MouseFeatures {
    pub mouse_sample_count: usize,
    pub mouse_click_count: usize,
    pub mouse_speed_mean_counts_s: f64,
    pub mouse_speed_std_counts_s: f64,
    pub mouse_click_interval_mean_ms: f64,
    pub mouse_click_interval_std_ms: f64,
    pub mouse_turn_mean_rad: f64,
    pub mouse_turn_std_rad: f64,
    pub mouse_path_straightness: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Features {
    #[serde(flatten)]
    pub keyboard: KeyboardFeatures,
    #[serde(flatten)]
    pub mouse: MouseFeatures,
}

/// Plausible feature values for tests that only need a well-formed window.
#[cfg(test)]
pub fn sample_features() -> Features {
    Features {
        keyboard: KeyboardFeatures {
            key_count: 40,
            hold_sample_count: 40,
            hold_mean_ms: 100.0,
            hold_std_ms: 10.0,
            press_interval_mean_ms: 200.0,
            press_interval_std_ms: 20.0,
        },
        mouse: MouseFeatures {
            mouse_sample_count: 12,
            mouse_click_count: 2,
            mouse_speed_mean_counts_s: 300.0,
            mouse_speed_std_counts_s: 50.0,
            mouse_click_interval_mean_ms: 400.0,
            mouse_click_interval_std_ms: 30.0,
            mouse_turn_mean_rad: 1.0,
            mouse_turn_std_rad: 0.4,
            mouse_path_straightness: 0.5,
        },
    }
}

/// A finished window, without the device, session, and mode the link adds when
/// it uploads.
/// Serializable so the link can hold it on disk until it can be sent.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FeatureWindow {
    /// Window number since the session's first row, as the converter counts.
    pub index: u64,
    pub started_unix_ms: i64,
    pub features: Features,
}

impl FeatureWindow {
    /// Every window the tray builds is schema v4, the only kind the website accepts.
    pub const fn schema_version(&self) -> u8 {
        4
    }

    pub fn started_at(&self) -> String {
        iso_utc(self.started_unix_ms)
    }

    pub const fn duration_ms(&self) -> u64 {
        WINDOW_MS
    }

    /// Sequence number for upload: the 30-second slot of the window's start since
    /// the Unix epoch. Windows in one run start 30 s apart and a new run starts
    /// after the previous run's last sent window ended, so the number stays unique
    /// per device and session across tray restarts, which the server requires.
    pub fn upload_sequence(&self) -> u64 {
        (self.started_unix_ms.max(0) as u64) / WINDOW_MS
    }
}

pub struct Windower {
    origin: Option<f64>,
    origin_unix_ms: i64,
    index: u64,
    keyboard: Option<String>,
    presses: Vec<f64>,
    holds: Vec<f64>,
    bins: BTreeMap<i64, (f64, f64)>,
    clicks: Vec<f64>,
    ready: Vec<FeatureWindow>,
}

impl Windower {
    pub fn new() -> Self {
        Self {
            origin: None,
            origin_unix_ms: 0,
            index: 0,
            keyboard: None,
            presses: Vec::new(),
            holds: Vec::new(),
            bins: BTreeMap::new(),
            clicks: Vec::new(),
            ready: Vec::new(),
        }
    }

    /// Feed one row. `unix_ms` is the row's wall-clock time; only the first row's
    /// is kept, as the anchor for every window's `started_at`.
    pub fn push(&mut self, row: &Row, unix_ms: i64) {
        let now = row.monotonic_s;
        let origin = *self.origin.get_or_insert_with(|| {
            self.origin_unix_ms = unix_ms;
            now
        });
        self.tick(now);

        if row.io_type == "mouse" {
            match row.event {
                "press" => self.clicks.push(now),
                "move" => {
                    let bin = ((now - origin - self.index as f64 * WINDOW_SECONDS)
                        / MOUSE_BIN_SECONDS) as i64;
                    let entry = self.bins.entry(bin).or_insert((0.0, 0.0));
                    entry.0 += f64::from(row.dx.unwrap_or(0));
                    entry.1 += f64::from(row.dy.unwrap_or(0));
                }
                _ => {}
            }
        }

        if row.io_type != "keyboard" {
            return;
        }
        let source = self
            .keyboard
            .get_or_insert_with(|| row.device_id.to_string());
        if source != row.device_id {
            return;
        }
        match row.event {
            "press" => self.presses.push(now),
            "release" => {
                // Holds that began in an earlier window are left out, as in the converter.
                if let Some(hold) = row.hold_ms.filter(|h| h.is_finite() && *h >= 0.0)
                    && now - hold / 1000.0 >= origin + self.index as f64 * WINDOW_SECONDS
                {
                    self.holds.push(hold);
                }
            }
            _ => {}
        }
    }

    /// Finish every window that ended before `monotonic_s`, so a window is sent
    /// on time even when no input follows it.
    pub fn tick(&mut self, monotonic_s: f64) {
        let Some(origin) = self.origin else {
            return;
        };
        while monotonic_s - origin >= (self.index + 1) as f64 * WINDOW_SECONDS {
            self.finish();
            self.index += 1;
        }
    }

    /// Windows finished since the last call.
    pub fn take_ready(&mut self) -> Vec<FeatureWindow> {
        std::mem::take(&mut self.ready)
    }

    fn finish(&mut self) {
        let gaps: Vec<f64> = self
            .presses
            .windows(2)
            .map(|p| (p[1] - p[0]) * 1000.0)
            .collect();
        let keyboard = KeyboardFeatures {
            key_count: self.presses.len(),
            hold_sample_count: self.holds.len(),
            hold_mean_ms: mean(&self.holds),
            hold_std_ms: pstdev(&self.holds),
            press_interval_mean_ms: mean(&gaps),
            press_interval_std_ms: pstdev(&gaps),
        };
        let mouse = self.mouse_features();
        let active =
            keyboard.key_count > 0 || mouse.mouse_sample_count > 0 || mouse.mouse_click_count > 0;
        if active {
            self.ready.push(FeatureWindow {
                index: self.index,
                started_unix_ms: self.origin_unix_ms + self.index as i64 * WINDOW_MS as i64,
                features: Features { keyboard, mouse },
            });
        }
        self.presses.clear();
        self.holds.clear();
        self.bins.clear();
        self.clicks.clear();
    }

    fn mouse_features(&self) -> MouseFeatures {
        let mut speeds = Vec::new();
        let mut turns = Vec::new();
        let mut previous: Option<(i64, f64)> = None;
        let (mut path, mut total_x, mut total_y) = (0.0, 0.0, 0.0);
        for (&index, &(dx, dy)) in &self.bins {
            let distance = dx.hypot(dy);
            if distance == 0.0 {
                continue;
            }
            speeds.push(distance / MOUSE_BIN_SECONDS);
            let angle = dy.atan2(dx);
            if let Some((prev_index, prev_angle)) = previous
                && (index - prev_index) as f64 * MOUSE_BIN_SECONDS <= TURN_GAP_SECONDS
            {
                turns.push(((angle - prev_angle + PI).rem_euclid(2.0 * PI) - PI).abs());
            }
            previous = Some((index, angle));
            path += distance;
            total_x += dx;
            total_y += dy;
        }
        let click_gaps: Vec<f64> = self
            .clicks
            .windows(2)
            .map(|c| (c[1] - c[0]) * 1000.0)
            .collect();
        MouseFeatures {
            mouse_sample_count: speeds.len(),
            mouse_click_count: self.clicks.len(),
            mouse_speed_mean_counts_s: mean(&speeds),
            mouse_speed_std_counts_s: pstdev(&speeds),
            mouse_click_interval_mean_ms: mean(&click_gaps),
            mouse_click_interval_std_ms: pstdev(&click_gaps),
            mouse_turn_mean_rad: mean(&turns),
            mouse_turn_std_rad: pstdev(&turns),
            mouse_path_straightness: if path > 0.0 {
                (total_x.hypot(total_y) / path).min(1.0)
            } else {
                0.0
            },
        }
    }
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    }
}

/// Population standard deviation, as Python's `statistics.pstdev`.
fn pstdev(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let m = mean(values);
    (values.iter().map(|v| (v - m).powi(2)).sum::<f64>() / values.len() as f64).sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE_CSV: &str = include_str!("../../../shared/fixtures/tray-capture.csv");
    const EXPECTED_V4: &str = include_str!("../../../shared/fixtures/tray-capture.v4.json");
    // 2026-01-01T00:00:00.250Z, the fixture's first row.
    const FIXTURE_START_MS: i64 = 1_767_225_600_250;

    /// Replay the synthetic fixture through the windower, as the tray would live.
    fn replay() -> Vec<FeatureWindow> {
        let mut windower = Windower::new();
        for line in FIXTURE_CSV.lines().skip(1) {
            let c: Vec<&str> = line.split(',').collect();
            let row = Row {
                monotonic_s: c[1].parse().unwrap(),
                io_type: c[2],
                device_id: c[3],
                event: c[4],
                hold_ms: c[6].parse().ok(),
                dx: c[8].parse().ok(),
                dy: c[9].parse().ok(),
            };
            windower.push(&row, FIXTURE_START_MS);
        }
        windower.take_ready()
    }

    fn assert_close(name: &str, actual: f64, expected: f64) {
        let tolerance = 1e-9 * expected.abs().max(1.0);
        assert!(
            (actual - expected).abs() <= tolerance,
            "{name}: {actual} != {expected}"
        );
    }

    #[test]
    fn windows_match_the_python_converter() {
        let expected: Vec<serde_json::Value> = serde_json::from_str(EXPECTED_V4).unwrap();
        let actual = replay();
        assert_eq!(actual.len(), expected.len());
        for (window, want) in actual.iter().zip(&expected) {
            assert_eq!(window.index, want["sequence"].as_u64().unwrap());
            assert_eq!(
                u64::from(window.schema_version()),
                want["schema_version"].as_u64().unwrap()
            );
            assert_eq!(window.duration_ms(), want["duration_ms"].as_u64().unwrap());
            assert_eq!(
                window.started_unix_ms,
                FIXTURE_START_MS + window.index as i64 * 30_000
            );
            let got = serde_json::to_value(&window.features).unwrap();
            let want = want["features"].as_object().unwrap();
            assert_eq!(got.as_object().unwrap().len(), want.len());
            for (name, value) in want {
                assert_close(name, got[name].as_f64().unwrap(), value.as_f64().unwrap());
            }
        }
    }

    #[test]
    fn tick_finishes_a_window_without_further_input() {
        let mut windower = Windower::new();
        for (t, event) in [(0.0, "press"), (0.1, "release"), (0.5, "press")] {
            let row = Row {
                monotonic_s: t,
                io_type: "keyboard",
                device_id: "kbd0",
                event,
                hold_ms: (event == "release").then_some(100.0),
                dx: None,
                dy: None,
            };
            windower.push(&row, 1_000);
        }
        windower.tick(29.9);
        assert!(windower.take_ready().is_empty());
        windower.tick(30.0);
        let ready = windower.take_ready();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].features.keyboard.key_count, 2);
        assert_eq!(ready[0].started_at(), "1970-01-01T00:00:01.000Z");
    }

    #[test]
    fn upload_sequence_is_the_thirty_second_slot() {
        let window = |started_unix_ms| FeatureWindow {
            index: 0,
            started_unix_ms,
            features: sample_features(),
        };
        assert_eq!(window(FIXTURE_START_MS).upload_sequence(), 58_907_520);
        assert_eq!(
            window(FIXTURE_START_MS + 30_000).upload_sequence(),
            58_907_521
        );
    }
}
