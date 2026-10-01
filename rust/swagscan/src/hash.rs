use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::emit::{Events, Progress};
use crate::pathx::{extension, find_pattern, norm, win, q};
use crate::win as wapi;

pub const PARTIAL: u64 = 64 * 1024;

/// Записей хеш-кеша не больше этого: кеш живёт годами, а дом — конечен.
/// При переполнении оставляем только пути текущего скана.
const CACHE_CAP: usize = 200_000;

#[derive(Clone)]
pub struct Sz {
    pub path: String,
    pub size: u64,
    /// FILETIME последней записи: ключ кеша вместе с размером.
    pub mtime: u64,
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

fn walk_dir(
    dir_norm: &str,
    plan: &DupPlan,
    out: &Mutex<Vec<Sz>>,
    cancel: &Arc<AtomicBool>,
    stats: &Mutex<WalkStats>,
    p: &Mutex<Progress>,
    ev: &Events,
    job: &str,
    pool: &rayon::ThreadPool,
) {
    if cancel.load(Ordering::Relaxed) {
        return;
    }
    let pattern = find_pattern(dir_norm);
    let (handle, mut data) = match wapi::find_first(&pattern) {
        Ok(v) => v,
        Err(_) => {
            stats.lock().unwrap().errors += 1;
            return;
        }
    };
    // Чилды одного каталога читаются без блокировок: мьютекс берём
    // один раз на выходе — тысячи файлов на каталог не спорят за лок.
    let mut subdirs: Vec<String> = Vec::new();
    let mut local_files: u64 = 0;
    let mut local_bytes: u64 = 0;
    let mut emit_due = false;
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
                    subdirs.push(child);
                }
            } else {
                let size = wapi::find_size(&data);
                let mtime = data.ft_last_write_time.ticks();
                local_files += 1;
                local_bytes += size;
                if size >= plan.min_size && ext_ok(&child, &plan.exts) {
                    out.lock().unwrap().push(Sz { path: child, size, mtime });
                }
                if local_files % 1024 == 0 {
                    emit_due = true;
                }
            }
        }
        if cancel.load(Ordering::Relaxed) || !wapi::find_next(handle, &mut data) {
            break;
        }
    }
    wapi::find_close(handle);
    {
        let mut st = stats.lock().unwrap();
        st.files += local_files;
        st.bytes += local_bytes;
        if emit_due {
            let (files, bytes) = (st.files, st.bytes);
            p.lock()
                .unwrap()
                .emit(ev, false, job, "walk", files, None, bytes, None);
        }
    }
    // Обход каталогов параллелен: узкие ветки (один подкаталог) остаются
    // последовательными, чтобы спавн задач не съедал выгоду на мелочи.
    if subdirs.len() > 1 {
        let plan_ref = &plan;
        let pool_ptr = pool;
        pool.install(|| {
            use rayon::prelude::*;
            subdirs.par_iter().for_each(|d| {
                walk_dir(d, plan_ref, out, cancel, stats, p, ev, job, pool_ptr);
            });
        });
    } else {
        for d in subdirs {
            walk_dir(&d, plan, out, cancel, stats, p, ev, job, pool);
        }
    }
}

// ---------- хеш-кеш: повторный скан не читает неизменившиеся файлы ----------

/// Путь кеша: %LOCALAPPDATA%\SWAGcleaner\cache\dedup_hash.json.
pub fn cache_file() -> Option<PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA")?;
    Some(
        PathBuf::from(base)
            .join("SWAGcleaner")
            .join("cache")
            .join("dedup_hash.json"),
    )
}

pub type CacheMap = HashMap<String, (u64, u64, String)>;

pub fn load_cache() -> CacheMap {
    let path = match cache_file() {
        Some(p) => p,
        None => return CacheMap::new(),
    };
    let data = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return CacheMap::new(),
    };
    let v: serde_json::Value = match serde_json::from_str(&data) {
        Ok(v) => v,
        Err(_) => return CacheMap::new(),
    };
    parse_cache(&v)
}

pub fn parse_cache(v: &serde_json::Value) -> CacheMap {
    let mut map = CacheMap::new();
    let obj = match v.as_object() {
        Some(o) => o,
        None => return map,
    };
    for (k, val) in obj {
        if let serde_json::Value::Array(a) = val {
            if a.len() == 3 {
                let size = a[0].as_u64().unwrap_or(0);
                let mtime = a[1].as_u64().unwrap_or(0);
                if let Some(h) = a[2].as_str() {
                    if !h.is_empty() {
                        map.insert(k.clone(), (size, mtime, h.to_string()));
                    }
                }
            }
        }
    }
    map
}

/// Хеш из кеша, если размер и время записи файла не дрогнули.
pub fn cache_hit(cache: &CacheMap, s: &Sz) -> Option<String> {
    let (size, mtime, hash) = cache.get(&s.path)?;
    if *size == s.size && *mtime == s.mtime {
        Some(hash.clone())
    } else {
        None
    }
}

pub fn save_cache(cache: &CacheMap, seen: &dyn Fn(&str) -> bool) -> bool {
    let path = match cache_file() {
        Some(p) => p,
        None => return false,
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut kept: CacheMap = cache.clone();
    if kept.len() > CACHE_CAP {
        kept.retain(|k, _| seen(k));
    }
    let mut obj = serde_json::Map::new();
    for (k, (size, mtime, hash)) in kept.iter() {
        obj.insert(
            k.clone(),
            serde_json::json!([size, mtime, hash]),
        );
    }
    let body = serde_json::Value::Object(obj).to_string();
    let tmp = path.with_extension("json.tmp");
    if std::fs::write(&tmp, body).is_err() {
        return false;
    }
    std::fs::rename(&tmp, &path).is_ok()
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
    // Тик на первом элементе, каждые 256 и на последнем: финальный 100%
    // иначе не приходил, если количество файлов не кратно 256.
    if n != 1 && n % 256 != 0 && n != total {
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
    // Стартовая строка фазы ходьбы: без неё до первого события клиент
    // молчит, и пользователь не понимает, жив ли скан.
    p.emit(ev, true, job, "walk", 0, None, 0, None);

    // Полный параллелизм душил интерфейс окна: BLAKE3 на всех ядрах
    // оставлял главному потоку процессора обрывки. Половина ядер,
    // максимум 6 — скан заметно быстрее одного потока, но окно живёт.
    // Тот же пул гоняет и обход каталогов: параллельные
    // FindFirstFileExW держат очередь запросов к NTFS полной, одиночный
    // обход упирался в латентность диска на каждом каталоге.
    let threads = if plan.threads > 0 {
        plan.threads
    } else {
        let avail = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        (avail / 2).clamp(2, 6)
    };
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(threads)
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());

    let items: Mutex<Vec<Sz>> = Mutex::new(Vec::new());
    let stats_m = Mutex::new(WalkStats::default());
    let p_m = Mutex::new(Progress::new());
    for r in &plan.roots {
        let rn = norm(r);
        if rn.is_empty() {
            continue;
        }
        walk_dir(&rn, plan, &items, cancel, &stats_m, &p_m, ev, job, &pool);
    }
    let mut items: Vec<Sz> = items.into_inner().unwrap();
    let stats = stats_m.into_inner().unwrap();
    let scanned_files = stats.files;
    let scanned_bytes = stats.bytes;

    p.emit(ev, true, job, "size_pass", scanned_files, None, scanned_bytes, Some("by size"));

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

    // Хеш-кеш: у файла не дрогнули ни размер, ни время записи — хеш
    // прошлого скана годится и сейчас, файл вообще не читаем.
    let cache = load_cache();
    let mut cached: HashMap<usize, String> = HashMap::new();
    for (i, s) in candidates.iter().enumerate() {
        if let Some(h) = cache_hit(&cache, s) {
            cached.insert(i, h);
        }
    }
    let cache_hits = cached.len();

    {
        let dn = Arc::clone(&done);
        let pp = Arc::clone(&partial);
        let cn = Arc::clone(cancel);
        let evc = ev.clone();
        let jc = job.to_string();
        let uncached: Vec<(usize, &Sz)> = candidates
            .iter()
            .enumerate()
            .filter(|(i, _)| !cached.contains_key(i))
            .collect();
        let uncached_total = uncached.len();
        pool.install(|| {
            use rayon::prelude::*;
            uncached.par_iter().for_each(|(i, s)| {
                if cn.load(Ordering::Relaxed) {
                    return;
                }
                if let Some(buf) = read_partial(&win(&s.path)) {
                    let h = hash_bytes(&buf);
                    pp.lock()
                        .unwrap()
                        .entry(h)
                        .or_insert_with(Vec::new)
                        .push(*i);
                }
                let n = dn.fetch_add(1, Ordering::Relaxed) + 1;
                throttled(&evc, &jc, "partial_hash", n, uncached_total);
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
    // Хиты кеша сразу становятся полными хешами: они уже пережили полное
    // чтение в прошлом скане, вторая фаза их не касается.
    {
        let mut ff = full.lock().unwrap();
        for (i, h) in cached.iter() {
            ff.entry(h.clone())
                .or_insert_with(Vec::new)
                .push(candidates[*i].clone());
        }
    }
    // Свежие хеши лягут сюда: (path, size, mtime, hash) для записи кеша.
    let fresh: Arc<Mutex<Vec<(String, u64, u64, String)>>> =
        Arc::new(Mutex::new(Vec::new()));
    {
        let dn = Arc::clone(&done2);
        let ff = Arc::clone(&full);
        let fr = Arc::clone(&fresh);
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
                    fr.lock().unwrap().push((
                        s.path.clone(),
                        s.size,
                        s.mtime,
                        h.clone(),
                    ));
                    ff.lock()
                        .unwrap()
                        .entry(h)
                        .or_insert_with(Vec::new)
                        .push(s.clone());
                }
                let n = dn.fetch_add(1, Ordering::Relaxed) + 1;
                throttled(&evc, &jc, "full_hash", n, second_total);
            });
        });
    }

    // Кеш обновляется только полными (не отменёнными) сканами: половинный
    // прогон записал бы хеши только части кандидатов.
    let cancelled = cancel.load(Ordering::Relaxed);
    let mut cache_saved = false;
    if !cancelled {
        let mut next = cache;
        let fresh_list = fresh.lock().unwrap().clone();
        let seen: std::collections::HashSet<String> =
            candidates.iter().map(|s| s.path.clone()).collect();
        for (path, size, mtime, h) in fresh_list {
            next.insert(path, (size, mtime, h));
        }
        cache_saved = save_cache(&next, &|k| seen.contains(k));
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
        "{{\"scanned_files\":{scanned_files},\"scanned_bytes\":{scanned_bytes},\"candidates\":{cand_total},\"second_pass\":{second_total},\"groups\":{},\"wasted_bytes\":{wasted},\"cache_hits\":{cache_hits},\"cache_saved\":{cache_saved},\"cancelled\":{}}}",
        groups.len(),
        cancelled
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
    fn kesch_chitaetsya_i_doroga_okazyvaetsya_zamenoj() {
        let v: serde_json::Value =
            serde_json::json!({"c:/a/1.bin": [10, 20, "h1"], "c:/a/2.bin": "мусор"});
        let cache = parse_cache(&v);
        assert_eq!(cache.len(), 1);
        let hit = Sz { path: "c:/a/1.bin".into(), size: 10, mtime: 20 };
        assert_eq!(cache_hit(&cache, &hit).unwrap(), "h1");
        // Размер дрогнул — прошлый хеш больше не валиден.
        let grown = Sz { path: "c:/a/1.bin".into(), size: 11, mtime: 20 };
        assert!(cache_hit(&cache, &grown).is_none());
        // Время записи дрогнуло — тоже.
        let touched = Sz { path: "c:/a/1.bin".into(), size: 10, mtime: 21 };
        assert!(cache_hit(&cache, &touched).is_none());
    }

    #[test]
    fn save_i_zagruzka_kesha_krugooborotom() {
        let mut cache = CacheMap::new();
        cache.insert("c:/zz_test.bin".into(), (7, 8, "zz".into()));
        if save_cache(&cache, &|_| true) {
            let loaded = load_cache();
            let entry = loaded.get("c:/zz_test.bin").expect("запись вернулась");
            assert_eq!(entry.2, "zz");
            if let Some(p) = cache_file() {
                let _ = std::fs::remove_file(p);
            }
        }
    }

    #[test]
    fn paralleljnyj_obhod_vidit_fajly_vo_vetkah() {
        let dir = std::env::temp_dir().join(format!("swagscan_w_{}", std::process::id()));
        std::fs::create_dir_all(dir.join("a")).unwrap();
        std::fs::create_dir_all(dir.join("b")).unwrap();
        std::fs::write(dir.join("a/1.bin"), vec![1u8; 5]).unwrap();
        std::fs::write(dir.join("b/2.bin"), vec![2u8; 5]).unwrap();

        let cancel = Arc::new(AtomicBool::new(false));
        let (ev, writer) = crate::emit::Writer::new(
            Box::new(std::io::sink()),
            Arc::clone(&cancel),
        );
        let plan = DupPlan {
            roots: vec![],
            min_size: 0,
            exts: vec![],
            threads: 2,
            limit_groups: 10,
        };
        let out = Mutex::new(Vec::new());
        let stats = Mutex::new(WalkStats::default());
        let p = Mutex::new(Progress::new());
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(2)
            .build()
            .unwrap();
        let root = norm(dir.to_str().unwrap());
        walk_dir(&root, &plan, &out, &cancel, &stats, &p, &ev, "test", &pool);
        let out = out.into_inner().unwrap();
        assert_eq!(out.len(), 2);
        assert!(out.iter().all(|s| s.mtime > 0));
        drop(ev);
        writer.shutdown();
        std::fs::remove_dir_all(&dir).ok();
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
