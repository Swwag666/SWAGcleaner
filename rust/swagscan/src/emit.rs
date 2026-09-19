use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{sync_channel, TrySendError};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::pathx::q;
use crate::scan::Counters;

/// Сообщений в полёте к writer-потоку. 10k строк NDJSON — это ~1-2 МБ
/// в худшем случае; переполнение возможно только если клиент надолго
/// перестал читать, и тогда отправитель ждёт (см. send).
const QUEUE_CAP: usize = 10_000;

/// При взведённом cancel отправитель в переполненной очереди ждёт не дольше
/// этого срока, потом бросает сообщение. Живой writer освобождает очередь
/// за миллисекунды, так что до срыва доходит только при мёртвом канале.
const CANCEL_WAIT: Duration = Duration::from_secs(5);

struct Shared {
    tx: std::sync::mpsc::SyncSender<String>,
    dead: Arc<AtomicBool>,
    cancel: Arc<AtomicBool>,
}

/// Ручка writer-потока: пока жив, канал открыт. shutdown дожидается
/// доставки остатка очереди (канал должен быть закрыт — все клоны
/// Events к этому моменту дропнуты).
pub struct Writer {
    join: Option<std::thread::JoinHandle<()>>,
}

impl Writer {
    pub fn stdout(cancel: Arc<AtomicBool>) -> (Events, Writer) {
        Self::new(Box::new(std::io::stdout()), cancel)
    }

    /// Writer-поток единолично владеет выходом: рабочие потоки scan/purge
    /// только кладут строки в bounded-очередь и не блокируются на writeln.
    pub fn new(out: Box<dyn Write + Send>, cancel: Arc<AtomicBool>) -> (Events, Writer) {
        let (tx, rx) = sync_channel::<String>(QUEUE_CAP);
        let dead = Arc::new(AtomicBool::new(false));
        let dead_w = Arc::clone(&dead);
        let cancel_w = Arc::clone(&cancel);
        let join = std::thread::spawn(move || {
            let mut out = std::io::BufWriter::with_capacity(1 << 16, out);
            while let Ok(first) = rx.recv() {
                // Добираем всё, что уже лежит в канале, одной пачкой —
                // один flush на пачку вместо flush на каждую строку.
                let mut batch = vec![first];
                while let Ok(line) = rx.try_recv() {
                    batch.push(line);
                }
                let mut broken = false;
                for line in batch {
                    if out
                        .write_all(line.as_bytes())
                        .and_then(|_| out.write_all(b"\n"))
                        .is_err()
                    {
                        broken = true;
                        break;
                    }
                }
                if broken || out.flush().is_err() {
                    // Python-сторона умерла (broken pipe): помечаем writer
                    // мёртвым и взводим cancel — ядро не дорабатывает впустую.
                    dead_w.store(true, Ordering::SeqCst);
                    cancel_w.store(true, Ordering::SeqCst);
                    // Снимаем остаток, чтобы отправители не висели на полном канале.
                    while rx.recv().is_ok() {}
                    break;
                }
            }
            let _ = out.flush();
        });
        let ev = Events {
            shared: Arc::new(Shared {
                tx,
                dead,
                cancel,
            }),
        };
        (ev, Writer { join: Some(join) })
    }

    /// Дождаться, пока writer допишет очередь и завершится.
    /// Перед вызовом все клоны Events должны быть дропнуты, иначе канал
    /// не закроется и join зависнет.
    pub fn shutdown(mut self) {
        if let Some(j) = self.join.take() {
            let _ = j.join();
        }
    }
}

#[derive(Clone)]
pub struct Events {
    shared: Arc<Shared>,
}

impl Events {
    /// Поставить строку в очередь writer'а. Порядок сообщений от одного
    /// отправителя сохраняется (FIFO канала). Переполненная очередь
    /// блокирует отправителя, но не навсегда: смерть writer'а (broken pipe)
    /// или долгий cancel прерывают ожидание, сообщение молча роняется.
    pub fn send(&self, json: String) {
        if self.shared.dead.load(Ordering::SeqCst) {
            return;
        }
        let mut msg = json;
        let mut waited = Duration::ZERO;
        loop {
            match self.shared.tx.try_send(msg) {
                Ok(()) => return,
                Err(TrySendError::Disconnected(_)) => return,
                Err(TrySendError::Full(m)) => {
                    msg = m;
                    if self.shared.dead.load(Ordering::SeqCst) {
                        return;
                    }
                    if self.shared.cancel.load(Ordering::SeqCst) {
                        waited += Duration::from_millis(5);
                        if waited >= CANCEL_WAIT {
                            return;
                        }
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
            }
        }
    }

    /// id — готовый JSON-литерал (число как число, строка как строка):
    /// клиент с числовым id должен сматчить ответ по точному значению.
    pub fn result_ok(&self, id_json: &str, data: String) {
        self.send(format!(
            "{{\"event\":\"result\",\"id\":{id_json},\"ok\":true,\"data\":{data}}}"
        ));
    }

    pub fn result_err(&self, id_json: &str, msg: &str) {
        self.send(format!(
            "{{\"event\":\"result\",\"id\":{id_json},\"ok\":false,\"error\":{}}}",
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::ErrorKind;
    use std::sync::Mutex;

    struct Sink {
        buf: Arc<Mutex<Vec<u8>>>,
    }

    impl Write for Sink {
        fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
            self.buf.lock().unwrap().extend_from_slice(data);
            Ok(data.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn lines_of(buf: &Arc<Mutex<Vec<u8>>>) -> Vec<String> {
        let raw = buf.lock().unwrap().clone();
        String::from_utf8(raw)
            .unwrap()
            .lines()
            .map(|s| s.to_string())
            .collect()
    }

    #[test]
    fn poryadok_soobshcheniy_sohranyaetsya() {
        let buf = Arc::new(Mutex::new(Vec::new()));
        let cancel = Arc::new(AtomicBool::new(false));
        let (ev, writer) = Writer::new(
            Box::new(Sink { buf: Arc::clone(&buf) }),
            Arc::clone(&cancel),
        );
        let mut handles = Vec::new();
        for t in 0..4 {
            let evc = ev.clone();
            handles.push(std::thread::spawn(move || {
                for i in 0..250 {
                    evc.send(format!("t{t}-{i}"));
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        drop(ev);
        writer.shutdown();
        let lines = lines_of(&buf);
        assert_eq!(lines.len(), 1000);
        for t in 0..4 {
            let seq: Vec<usize> = lines
                .iter()
                .filter_map(|l| {
                    l.strip_prefix(&format!("t{t}-"))
                        .map(|n| n.parse::<usize>().unwrap())
                })
                .collect();
            assert_eq!(seq.len(), 250);
            assert!(seq.windows(2).all(|w| w[0] < w[1]), "порядок потока {t} нарушен");
        }
    }

    struct Broken;

    impl Write for Broken {
        fn write(&mut self, _data: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::new(ErrorKind::BrokenPipe, "closed"))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Err(std::io::Error::new(ErrorKind::BrokenPipe, "closed"))
        }
    }

    #[test]
    fn zapis_v_zakrytyi_pipe_ne_panikuet_i_ostaet_yadro() {
        let cancel = Arc::new(AtomicBool::new(false));
        let (ev, writer) = Writer::new(Box::new(Broken), Arc::clone(&cancel));
        for i in 0..100 {
            ev.send(format!("line {i}"));
        }
        // Ждём, пока writer пометит себя мёртвым (максимум пару секунд).
        let t0 = Instant::now();
        while !ev.shared.dead.load(Ordering::SeqCst) && t0.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(ev.shared.dead.load(Ordering::SeqCst), "writer не пометился мёртвым");
        assert!(cancel.load(Ordering::SeqCst), "cancel должен взводиться при broken pipe");
        // Отправка в мёртвый канал не висит и не паникует.
        for i in 0..10_000 {
            ev.send(format!("late {i}"));
        }
        drop(ev);
        writer.shutdown();
    }
}
