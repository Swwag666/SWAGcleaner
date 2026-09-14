use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::pathx::q;
use crate::scan::Counters;

pub struct Writer {
    out: Box<dyn Write + Send>,
}

impl Writer {
    pub fn stdout() -> Writer {
        Writer {
            out: Box::new(std::io::BufWriter::with_capacity(1 << 16, std::io::stdout())),
        }
    }

    pub fn into_events(self) -> Events {
        Events {
            inner: Arc::new(Mutex::new(self)),
        }
    }
}

#[derive(Clone)]
pub struct Events {
    inner: Arc<Mutex<Writer>>,
}

impl Events {
    pub fn send(&self, json: String) {
        if let Ok(mut w) = self.inner.lock() {
            let _ = writeln!(w.out, "{json}");
            let _ = w.out.flush();
        }
    }

    pub fn send_buffered(&self, json: String) {
        if let Ok(mut w) = self.inner.lock() {
            let _ = writeln!(w.out, "{json}");
        }
    }

    pub fn result_ok(&self, id: &str, data: String) {
        self.send(format!(
            "{{\"event\":\"result\",\"id\":{},\"ok\":true,\"data\":{}}}",
            q(id),
            data
        ));
    }

    pub fn result_err(&self, id: &str, msg: &str) {
        self.send(format!(
            "{{\"event\":\"result\",\"id\":{},\"ok\":false,\"error\":{}}}",
            q(id),
            q(msg)
        ));
    }

    pub fn log(&self, level: &str, msg: &str) {
        self.send(format!(
            "{{\"event\":\"log\",\"level\":{},\"msg\":{}}}",
            q(level),
            q(msg)
        ));
    }

    pub fn progress(&self, job: &str, seq: u64, phase: &str, done: u64, total: Option<u64>, bytes: u64, step: Option<&str>, elapsed_ms: u128) {
        let total_json = match total {
            Some(t) => t.to_string(),
            None => "null".to_string(),
        };
        let step_json = match step {
            Some(s) => format!(",\"step\":{}", q(s)),
            None => String::new(),
        };
        self.send(format!(
            "{{\"event\":\"progress\",\"job\":{},\"seq\":{},\"phase\":{},\"done\":{done},\"total\":{total_json},\"bytes\":{bytes},\"elapsed_ms\":{elapsed_ms}{step_json}}}",
            q(job),
            seq,
            q(phase)
        ));
    }

    pub fn cancelled(&self, id: &str, job: &str) {
        self.send(format!(
            "{{\"event\":\"cancelled\",\"id\":{},\"job\":{}}}",
            q(id),
            q(job)
        ));
    }
}

pub struct Progress {
    started: Instant,
    last: Instant,
    pub interval: Duration,
    seq: u64,
}

impl Progress {
    pub fn new() -> Progress {
        let now = Instant::now();
        Progress {
            started: now,
            last: now - Duration::from_secs(3600),
            interval: Duration::from_millis(300),
            seq: 0,
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn emit(
        &mut self,
        ev: &Events,
        force: bool,
        job: &str,
        phase: &str,
        done: u64,
        total: Option<u64>,
        bytes: u64,
        step: Option<&str>,
    ) {
        let now = Instant::now();
        if !force && now.duration_since(self.last) < self.interval {
            return;
        }
        self.last = now;
        self.seq += 1;
        ev.progress(
            job,
            self.seq,
            phase,
            done,
            total,
            bytes,
            step,
            now.duration_since(self.started).as_millis(),
        );
    }
}

impl Default for Progress {
    fn default() -> Self {
        Progress::new()
    }
}

pub fn spawn_ticker(
    ev: Events,
    job: String,
    counters: Arc<Counters>,
    stop: Arc<AtomicBool>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut p = Progress::new();
        while !stop.load(Ordering::Relaxed) {
            for _ in 0..15 {
                if stop.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            p.emit(
                &ev,
                false,
                &job,
                "walking",
                counters.files.load(Ordering::Relaxed) as u64,
                None,
                counters.bytes.load(Ordering::Relaxed) as u64,
                None,
            );
        }
    })
}
