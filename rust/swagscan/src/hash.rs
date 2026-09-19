use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::emit::{Events, Progress};
use crate::pathx::{extension, find_pattern, norm, win, q};
use crate::win as wapi;

pub const PARTIAL: u64 = 64 * 1024;

#[derive(Clone)]
pub struct Sz {
    pub path: String,
    pub size: u64,
}

#[derive(Default, Clone, Copy)]
pub struct WalkStats {
    pub files: u64,
    pub bytes: u64,
    pub errors: u64,
}

pub struct DupPlan {
    pub roots: Vec<String>,
    pub min_size: u64,
    pub exts: Vec<String>,
    pub threads: usize,
    pub limit_groups: usize,
}

impl Default for DupPlan {
    fn default() -> Self {
        DupPlan {
            roots: Vec::new(),
            min_size: 1024 * 1024,
            exts: Vec::new(),
            threads: 0,
            limit_groups: 2000,
        }
    }
}

fn ext_ok(path_norm: &str, exts: &[String]) -> bool {
    if exts.is_empty() {
        return true;
    }
    let e = extension(path_norm);
    exts.iter().any(|x| x.to_lowercase() == e)
}

fn walk_collect(
    dir_norm: &str,
    plan: &DupPlan,
    out: &mut Vec<Sz>,
    cancel: &Arc<AtomicBool>,
    stats: &mut WalkStats,
) {
    if cancel.load(Ordering::Relaxed) {
        return;
    }
    let pattern = find_pattern(dir_norm);
    let (handle, mut data) = match wapi::find_first(&pattern) {
        Ok(v) => v,
        Err(_) => {
            stats.errors += 1;
            return;
        }
    };
    loop {
        let name = wapi::find_name(&data);
        if name != "." && name != ".." {
            let attrs = data.dw_file_attributes;
            let is_dir = attrs & wapi::FA_DIRECTORY != 0;
            let reparse = attrs & wapi::FA_REPARSE != 0;
            let child = crate::agg::join(dir_norm, &name);
            let lower = name.to_lowercase();
            if is_dir {
                if !reparse
                    && !lower.starts_with("$recycle.bin")
                    && !lower.starts_with("system volume information")
                {
                    walk_collect(&child, plan, out, cancel, stats);
                }
            } else {
                let size = wapi::find_size(&data);
                stats.files += 1;
                stats.bytes += size;
                if size >= plan.min_size && ext_ok(&child, &plan.exts) {
                    out.push(Sz { path: child, size });
                }
            }
        }
        if cancel.load(Ordering::Relaxed) || !wapi::find_next(handle, &mut data) {
            break;
        }
    }
    wapi::find_close(handle);
}

fn read_open(path_win: &str) -> Option<(File, u64)> {
    let real = if path_win.len() > 240 && !path_win.starts_with("\\\\?\\") {
        crate::pathx::to_verbatim(path_win)
    } else {
        path_win.to_string()
    };
    let f = File::open(&real).ok()?;
    let len = f.metadata().ok()?.len();
    Some((f, len))
}

pub fn read_partial(path_win: &str) -> Option<Vec<u8>> {
    let (mut f, len) = read_open(path_win)?;
    let head = (len.min(PARTIAL)) as usize;
    let mut buf = vec![0u8; head];
    if head > 0 && f.read_exact(&mut buf).is_err() {
        return None;
    }
    if len > PARTIAL * 2 {
        let mut tail = vec![0u8; PARTIAL as usize];
        if f.seek(SeekFrom::End(-(PARTIAL as i64))).is_ok() && f.read_exact(&mut tail).is_ok() {
            buf.extend_from_slice(&tail);
        }
    } else if len > PARTIAL {
        let mut mid = vec![0u8; (len - PARTIAL) as usize];
        if f.read_exact(&mut mid).is_ok() {
            buf.extend_from_slice(&mid);
        }
    }
    Some(buf)
}

#[cfg(test)]
pub fn read_full(path_win: &str) -> Option<Vec<u8>> {
    let (mut f, len) = read_open(path_win)?;
    let mut buf = vec![0u8; len as usize];
    if len > 0 && f.read_exact(&mut buf).is_err() {
        return None;
    }
    Some(buf)
}

/// Полное BLAKE3 без загрузки файла в память: буфер 4 МБ, потоково.
/// На 20-ГБ файлах и 8 потоках rayon read_full давал бы многократные
/// гигабайты одновременно — тут пик памяти постоянный.
pub fn hash_file(path_win: &str) -> Option<String> {
    let (mut f, _len) = read_open(path_win)?;
    let mut hasher = blake3::Hasher::new();
    let mut buf = vec![0u8; 4 << 20];
    loop {
        match f.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                hasher.update(&buf[..n]);
            }
            Err(_) => return None,
        }
    }
    Some(hasher.finalize().to_hex().to_string())
}

fn hash_bytes(data: &[u8]) -> String {
    blake3::hash(data).to_hex().to_string()
}

fn throttled(ev: &Events, job: &str, phase: &str, n: usize, total: usize) {
    if n != 1 && n % 256 != 0 {
        return;
    }
    ev.progress(
        job,
        (n / 256) as u64,
        phase,
        n as u64,
        Some(total as u64),
        0,
        None,
        0,
    );
}

pub fn run(plan: &DupPlan, ev: &Events, cancel: &Arc<AtomicBool>, job: &str) -> String {
    let mut p = Progress::new();
    let mut items: Vec<Sz> = Vec::new();
    let mut stats = WalkStats::default();
    for r in &plan.roots {
        let rn = norm(r);
        if rn.is_empty() {
            continue;
        }
        walk_collect(&rn, plan, &mut items, cancel, &mut stats);
    }
    let scanned_files = stats.files;
    let scanned_bytes = stats.bytes;

    p.emit(ev, true, job, "size_pass", scanned_files, None, scanned_bytes, Some("by size"));

    let threads = if plan.threads > 0 {
        plan.threads
    } else {
        std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4)
            .min(8)
            .max(1)
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    let mut by_size: HashMap<u64, Vec<Sz>> = HashMap::new();
    for s in items.drain(..) {
        by_size.entry(s.size).or_insert_with(Vec::new).push(s);
    }
    let candidates: Vec<Sz> = by_size
        .into_values()
        .filter(|v| v.len() > 1)
        .flat_map(|v| v.into_iter())
        .collect();
    let cand_total = candidates.len();

    let done = Arc::new(AtomicUsize::new(0));
    let partial: Arc<Mutex<HashMap<String, Vec<usize>>>> = Arc::new(Mutex::new(HashMap::new()));
    {
        let dn = Arc::clone(&done);
        let pp = Arc::clone(&partial);
        let cn = Arc::clone(cancel);
        let evc = ev.clone();
        let jc = job.to_string();
        let cand = &candidates;
        pool.install(|| {
            use rayon::prelude::*;
            cand.par_iter().enumerate().for_each(|(i, s)| {
                if cn.load(Ordering::Relaxed) {
                    return;
                }
                if let Some(buf) = read_partial(&win(&s.path)) {
                    let h = hash_bytes(&buf);
                    pp.lock()
                        .unwrap()
                        .entry(h)
                        .or_insert_with(Vec::new)
                        .push(i);
                }
                let n = dn.fetch_add(1, Ordering::Relaxed) + 1;
                throttled(&evc, &jc, "partial_hash", n, cand_total);
            });
        });
    }

    let second: Vec<Sz> = {
        let pp = partial.lock().unwrap();
        let mut v = Vec::new();
        for g in pp.values().filter(|v| v.len() > 1) {
            for i in g {
                v.push(candidates[*i].clone());
            }
        }
        v
    };
    let second_total = second.len();
    p.emit(ev, true, job, "full_hash", 0, Some(second_total as u64), 0, Some("blake3"));

    let done2 = Arc::new(AtomicUsize::new(0));
    let full: Arc<Mutex<HashMap<String, Vec<Sz>>>> = Arc::new(Mutex::new(HashMap::new()));
    {
        let dn = Arc::clone(&done2);
        let ff = Arc::clone(&full);
        let cn = Arc::clone(cancel);
        let sec = &second;
        let evc = ev.clone();
        let jc = job.to_string();
        pool.install(|| {
            use rayon::prelude::*;
            sec.par_iter().for_each(|s| {
                if cn.load(Ordering::Relaxed) {
                    return;
                }
                if let Some(h) = hash_file(&win(&s.path)) {
                    ff.lock().unwrap().entry(h).or_insert_with(Vec::new).push(s.clone());
                }
                let n = dn.fetch_add(1, Ordering::Relaxed) + 1;
                throttled(&evc, &jc, "full_hash", n, second_total);
            });
        });
    }

    let mut groups: Vec<(String, u64, Vec<String>)> = Vec::new();
    {
        let mut ff = full.lock().unwrap();
        for (h, list) in ff.iter_mut() {
            if list.len() < 2 {
                continue;
            }
            list.sort_by(|a, b| a.path.cmp(&b.path));
            let size = list[0].size;
            let paths: Vec<String> = list.drain(..).map(|s| win(&s.path)).collect();
            groups.push((h.clone(), size, paths));
        }
    }
    groups.sort_by(|a, b| {
        let wa = a.1 * (a.2.len() as u64 - 1);
        let wb = b.1 * (b.2.len() as u64 - 1);
        wb.cmp(&wa)
    });
    // wasted считаем по ВСЕМ группам до усечения выдачи.
    let wasted: u64 = groups
        .iter()
        .map(|(_, size, paths)| size * (paths.len() as u64 - 1))
        .sum();
    groups.truncate(plan.limit_groups);

    let mut body = String::from("[");
    for (i, (h, size, paths)) in groups.iter().enumerate() {
        if i > 0 {
            body.push(',');
        }
        body.push_str(&format!(
            "{{\"hash\":{},\"size\":{size},\"count\":{},\"paths\":[{}]}}",
            q(h),
            paths.len(),
            paths.iter().map(|p| q(p)).collect::<Vec<String>>().join(",")
        ));
    }
    body.push(']');

    ev.send(format!(
        "{{\"event\":\"dupgroups\",\"job\":{},\"wasted_bytes\":{wasted},\"groups\":{body}}}",
        q(job)
    ));

    format!(
        "{{\"scanned_files\":{scanned_files},\"scanned_bytes\":{scanned_bytes},\"candidates\":{cand_total},\"second_pass\":{second_total},\"groups\":{},\"wasted_bytes\":{wasted},\"cancelled\":{}}}",
        groups.len(),
        cancel.load(Ordering::Relaxed)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filtr_po_rasshireniyu() {
        assert!(ext_ok("c:/a/b.jpg", &[]));
        assert!(ext_ok("c:/a/b.jpg", &["JPG".to_string()]));
        assert!(!ext_ok("c:/a/b.txt", &["jpg".to_string()]));
    }

    #[test]
    fn chitaet_malenkii_fail_tselikom() {
        let dir = std::env::temp_dir().join(format!("swagscan_h_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("s.bin");
        std::fs::write(&f, vec![7u8; 10]).unwrap();
        let b = read_partial(f.to_str().unwrap()).expect("читается");
        assert_eq!(b.len(), 10);
        let c = read_full(f.to_str().unwrap()).expect("читается");
        assert_eq!(c.len(), 10);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn hash_stabilen() {
        assert_eq!(hash_bytes(b"abc"), hash_bytes(b"abc"));
        assert_ne!(hash_bytes(b"abc"), hash_bytes(b"abd"));
    }

    #[test]
    fn potokovyj_hash_sovpadaet_s_polnym_chteniem() {
        // Стриминг-хеш обязан давать тот же BLAKE3, что и read_full.
        let dir = std::env::temp_dir().join(format!("swagscan_hs_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("big.bin");
        // Больше одного буфера (4 МБ), чтобы цикл чтения прошёл несколько раз.
        let mut data = Vec::with_capacity((5 << 20) + 12345);
        let mut x: u64 = 0x12345678;
        for _ in 0..data.capacity() {
            x = x.wrapping_mul(6364136223846793005).wrapping_add(1);
            data.push((x >> 33) as u8);
        }
        std::fs::write(&f, &data).unwrap();
        let p = f.to_str().unwrap();
        assert_eq!(hash_file(p).unwrap(), hash_bytes(&data));
        std::fs::remove_dir_all(&dir).ok();
    }
}
