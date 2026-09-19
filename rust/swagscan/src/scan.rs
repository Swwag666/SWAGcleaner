use std::collections::{BinaryHeap, HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use crate::agg::{self, join};
use crate::cats::{self, Cat, Env, Lane, Matcher};
use crate::emit::{self, Events};
use crate::pathx::{extension, norm, parent, q, root_of, starts_with_path, win, now_unix};
use crate::win as wapi;

pub struct Counters {
    pub files: AtomicUsize,
    pub dirs: AtomicUsize,
    pub bytes: AtomicUsize,
    pub errors: AtomicUsize,
    pub reparse: AtomicUsize,
    pub pending: AtomicUsize,
}

impl Counters {
    pub fn new() -> Arc<Counters> {
        Arc::new(Counters {
            files: AtomicUsize::new(0),
            dirs: AtomicUsize::new(0),
            bytes: AtomicUsize::new(0),
            errors: AtomicUsize::new(0),
            reparse: AtomicUsize::new(0),
            pending: AtomicUsize::new(0),
        })
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum StreamKind {
    // All зарезервирован протоколом (file-события), придёт вместе с MFT-режимом.
    #[allow(dead_code)]
    All,
    Junk,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Aggregates,
    Stream(StreamKind),
}

pub struct Plan {
    pub roots: Vec<String>,
    pub max_depth: usize,
    pub follow_reparse: bool,
    pub include_hidden: bool,
    pub threads: usize,
    pub top_files: usize,
    pub samples: usize,
    pub want_categories: bool,
    pub mode: Mode,
    pub min_size: u64,
}

impl Default for Plan {
    fn default() -> Self {
        Plan {
            roots: Vec::new(),
            max_depth: usize::MAX,
            follow_reparse: false,
            include_hidden: true,
            threads: 0,
            top_files: 40,
            samples: 25,
            want_categories: false,
            mode: Mode::Aggregates,
            min_size: 0,
        }
    }
}

/// Корни обхода: каждый файл обходится ровно один раз.
/// Если один корень лежит внутри другого (USERPROFILE и USERPROFILE\Temp),
/// родитель останавливается на входе в дочерний — дочерний обходится сам.
/// Иначе одни и те же файлы классифицируются и считаются дважды.
struct RootIndex {
    sorted: Vec<(String, usize)>,
}

impl RootIndex {
    fn build(roots: &[String]) -> RootIndex {
        let mut pairs: Vec<(String, usize)> =
            roots.iter().cloned().enumerate().map(|(i, r)| (r, i)).collect();
        pairs.sort_by(|a, b| a.0.cmp(&b.0));
        pairs.dedup_by(|a, b| a.0 == b.0);
        RootIndex { sorted: pairs }
    }

    /// true, если dir_norm покрыт чьим-то другим корнем (не моим).
    fn covered_by_other(&self, dir_norm: &str, my_root: usize) -> bool {
        let i = match self.sorted.binary_search_by(|(r, _)| r.as_str().cmp(dir_norm)) {
            Ok(i) => return self.sorted[i].1 != my_root,
            Err(i) => i,
        };
        if i == 0 {
            return false;
        }
        let (r, j) = &self.sorted[i - 1];
        *j != my_root && starts_with_path(dir_norm, r)
    }
}

const SKIP_DIRS: &[&str] = &["$Recycle.Bin", "System Volume Information"];

fn skip_dir_name(name: &str) -> bool {
    let l = name.to_lowercase();
    l.starts_with("$recycle.bin")
        || l.starts_with("system volume information")
        || SKIP_DIRS.iter().any(|s| l == s.to_lowercase())
}

struct Queue {
    items: Mutex<VecDeque<(String, usize, usize, usize)>>,
}

impl Queue {
    fn new() -> Queue {
        Queue {
            items: Mutex::new(VecDeque::new()),
        }
    }
    fn push(&self, v: Vec<(String, usize, usize, usize)>, c: &Counters) {
        c.pending.fetch_add(v.len(), Ordering::SeqCst);
        self.items.lock().unwrap().extend(v);
    }
    fn pop(&self) -> Option<(String, usize, usize, usize)> {
        self.items.lock().unwrap().pop_back()
    }
    fn empty(&self) -> bool {
        self.items.lock().unwrap().is_empty()
    }
}

/// Сколько junction/symlink-переходов подряд ещё идём при follow_reparse.
/// Цикл "C:\a\link -> C:\a" без предела крутил бы обход бесконечно.
const MAX_REPARSE_CHAIN: usize = 8;

struct Local {
    own: HashMap<String, (u64, u64)>,
    ext: HashMap<String, (u64, u64)>,
    age: HashMap<String, (u64, u64)>,
    drive: HashMap<String, (u64, u64)>,
    root_bytes: HashMap<String, u64>,
    top_files: BinaryHeap<(std::cmp::Reverse<u64>, std::cmp::Reverse<i64>, String)>,
    cat_files: HashMap<&'static str, (u64, u64)>,
    cat_samples: HashMap<&'static str, BinaryHeap<(std::cmp::Reverse<u64>, String)>>,
    junk_bytes: u64,
    junk_files: u64,
}

impl Local {
    fn new() -> Local {
        Local {
            own: HashMap::new(),
            ext: HashMap::new(),
            age: HashMap::new(),
            drive: HashMap::new(),
            root_bytes: HashMap::new(),
            top_files: BinaryHeap::new(),
            cat_files: HashMap::new(),
            cat_samples: HashMap::new(),
            junk_bytes: 0,
            junk_files: 0,
        }
    }

    fn merge(&mut self, o: Local) {
        for (k, v) in o.own {
            let e = self.own.entry(k).or_insert((0, 0));
            e.0 += v.0;
            e.1 += v.1;
        }
        for (k, v) in o.ext {
            let e = self.ext.entry(k).or_insert((0, 0));
            e.0 += v.0;
            e.1 += v.1;
        }
        for (k, v) in o.age {
            let e = self.age.entry(k).or_insert((0, 0));
            e.0 += v.0;
            e.1 += v.1;
        }
        for (k, v) in o.drive {
            let e = self.drive.entry(k).or_insert((0, 0));
            e.0 += v.0;
            e.1 += v.1;
        }
        for (k, v) in o.root_bytes {
            *self.root_bytes.entry(k).or_insert(0) += v;
        }
        for (k, v) in o.cat_files {
            let e = self.cat_files.entry(k).or_insert((0, 0));
            e.0 += v.0;
            e.1 += v.1;
        }
        for v in o.top_files.into_sorted_vec() {
            self.top_files.push(v);
        }
        for (k, h) in o.cat_samples {
            let e = self.cat_samples.entry(k).or_insert_with(BinaryHeap::new);
            for v in h.into_sorted_vec() {
                e.push(v);
            }
        }
        self.junk_bytes += o.junk_bytes;
        self.junk_files += o.junk_files;
    }

    fn trim(&mut self, keep_files: usize, keep_samples: usize) {
        while self.top_files.len() > keep_files {
            self.top_files.pop();
        }
        for h in self.cat_samples.values_mut() {
            while h.len() > keep_samples {
                h.pop();
            }
        }
    }
}

pub struct Outcome {
    pub json: String,
}

fn stream_file(ev: &Events, path_norm: &str, size: u64, mtime: i64, cats: &[&'static Cat]) {
    let mut s = String::from("{\"event\":\"file\"");
    s.push_str(&format!(",\"path\":{}", q(&win(path_norm))));
    s.push_str(&format!(",\"size\":{size}"));
    s.push_str(&format!(",\"mtime\":{mtime}"));
    if !cats.is_empty() {
        let ids: Vec<String> = cats.iter().map(|c| q(c.id)).collect();
        s.push_str(&format!(",\"categories\":[{}]", ids.join(",")));
    }
    s.push('}');
    ev.send(s);
}

fn walk_one(
    dir_norm: &str,
    depth: usize,
    my_root: usize,
    reparse_depth: usize,
    plan: &Plan,
    ridx: &RootIndex,
    matcher: &Matcher,
    counters: &Arc<Counters>,
    queue: &Arc<Queue>,
    local: &mut Local,
    cancel: &Arc<AtomicBool>,
    ev: &Events,
    now: i64,
) {
    let pattern = crate::pathx::find_pattern(dir_norm);
    let (handle, mut data) = match wapi::find_first(&pattern) {
        Ok(v) => v,
        Err(_) => {
            counters.errors.fetch_add(1, Ordering::Relaxed);
            return;
        }
    };
    local.own.entry(dir_norm.to_string()).or_insert((0, 0));

    loop {
        let name = wapi::find_name(&data);
        if name != "." && name != ".." {
            let attrs = data.dw_file_attributes;
            let is_dir = attrs & wapi::FA_DIRECTORY != 0;
            let reparse = attrs & wapi::FA_REPARSE != 0;
            let hidden = attrs & wapi::FA_HIDDEN != 0;
            let system = attrs & wapi::FA_SYSTEM != 0;
            let child = join(dir_norm, &name);

            if is_dir {
                counters.dirs.fetch_add(1, Ordering::Relaxed);
                local.own.entry(child.clone()).or_insert((0, 0));
                if reparse && !plan.follow_reparse {
                    counters.reparse.fetch_add(1, Ordering::Relaxed);
                } else if reparse && reparse_depth >= MAX_REPARSE_CHAIN {
                    // Цепочка переходов слишком длинная — почти наверняка цикл.
                    counters.reparse.fetch_add(1, Ordering::Relaxed);
                } else if depth < plan.max_depth
                    && !skip_dir_name(&name)
                    && !ridx.covered_by_other(&child, my_root)
                {
                    let next_reparse = if reparse { reparse_depth + 1 } else { 0 };
                    queue.push(vec![(child, depth + 1, my_root, next_reparse)], counters);
                }
            } else if plan.include_hidden || !(hidden || system) {
                let size = wapi::find_size(&data);
                let mtime = data.ft_last_write_time.unix_seconds();
                counters.files.fetch_add(1, Ordering::Relaxed);
                counters.bytes.fetch_add(size as usize, Ordering::Relaxed);
                record_file(child, size, mtime, plan, matcher, local, ev, now);
            }
        }
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        if !wapi::find_next(handle, &mut data) {
            break;
        }
    }
    wapi::find_close(handle);
}

fn record_file(
    path_norm: String,
    size: u64,
    mtime: i64,
    plan: &Plan,
    matcher: &Matcher,
    local: &mut Local,
    ev: &Events,
    now: i64,
) {
    match plan.mode {
        Mode::Aggregates => {}
        Mode::Stream(StreamKind::All) => {
            if size >= plan.min_size {
                stream_file(ev, &path_norm, size, mtime, &[]);
            }
            return;
        }
        Mode::Stream(_) => {}
    }

    if let Some(p) = parent(&path_norm) {
        // own = dir -> (байты, ФАЙЛЫ): раньше во втором поле считались
        // подкаталоги, и top_folders в JSON называл их "files".
        let e = local.own.entry(p.to_string()).or_insert((0, 0));
        e.0 += size;
        e.1 += 1;
    }
    let ext = extension(&path_norm);
    let e = local.ext.entry(ext).or_insert((0, 0));
    e.0 += 1;
    e.1 += size;

    let secs_ago = now.saturating_sub(mtime).max(0);
    let a = local
        .age
        .entry(agg::age_bucket(secs_ago).to_string())
        .or_insert((0, 0));
    a.0 += 1;
    a.1 += size;

    let r = root_of(&path_norm);
    if !r.is_empty() {
        let d = local.drive.entry(r.clone()).or_insert((0, 0));
        d.0 += 1;
        d.1 += size;
        *local.root_bytes.entry(r).or_insert(0) += size;
    }

    if plan.mode == Mode::Aggregates {
        local.top_files.push((std::cmp::Reverse(size), std::cmp::Reverse(mtime), path_norm.clone()));
        if local.top_files.len() > plan.top_files * 8 {
            while local.top_files.len() > plan.top_files {
                local.top_files.pop();
            }
        }
    }

    if (plan.want_categories || plan.mode == Mode::Stream(StreamKind::Junk))
        && !cats::is_blocked(&path_norm)
    {
        if plan.mode == Mode::Stream(StreamKind::Junk) && size < plan.min_size {
            return;
        }
        let found = matcher.classify(&path_norm, size, mtime, now);
        if !found.is_empty() {
            local.junk_files += 1;
            local.junk_bytes += size;
            if plan.mode == Mode::Stream(StreamKind::Junk) {
                stream_file(ev, &path_norm, size, mtime, &found);
            }
            if plan.want_categories {
                for c in found {
                    let e = local.cat_files.entry(c.id).or_insert((0, 0));
                    e.0 += 1;
                    e.1 += size;
                    let h = local.cat_samples.entry(c.id).or_insert_with(BinaryHeap::new);
                    h.push((std::cmp::Reverse(size), win(&path_norm)));
                    if h.len() > plan.samples * 8 {
                        while h.len() > plan.samples {
                            h.pop();
                        }
                    }
                }
            }
        }
    }
}

pub fn run(plan: &Plan, env: &Env, ev: &Events, cancel: &Arc<AtomicBool>, job: &str) -> Outcome {
    let t0 = std::time::Instant::now();
    let now = now_unix();
    let matcher = Matcher::build(env);
    let counters = Counters::new();
    let mut seen_roots: std::collections::HashSet<String> = std::collections::HashSet::new();
    let roots: Vec<String> = plan
        .roots
        .iter()
        .map(|r| norm(r))
        .filter(|r| !r.is_empty() && seen_roots.insert(r.clone()))
        .collect();
    let ridx = RootIndex::build(&roots);
    let queue = Arc::new(Queue::new());
    queue.push(
        roots
            .iter()
            .enumerate()
            .map(|(i, r)| (r.clone(), 0usize, i, 0usize))
            .collect(),
        &counters,
    );

    let nthreads = if plan.threads > 0 {
        plan.threads
    } else {
        std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4)
            .min(12)
            .max(1)
    };

    let stop = Arc::new(AtomicBool::new(false));
    let ticker = emit::spawn_ticker(ev.clone(), job.to_string(), Arc::clone(&counters), Arc::clone(&stop));

    let mut locals: Vec<Local> = Vec::with_capacity(nthreads);
    {
        let plan_r: &Plan = plan;
        let ri_r: &RootIndex = &ridx;
        let m_r: &Matcher = &matcher;
        let c_r: &Arc<Counters> = &counters;
        let q_r: &Arc<Queue> = &queue;
        let cn_r: &Arc<AtomicBool> = cancel;
        let ev_shared: Events = ev.clone();
        std::thread::scope(|s| {
            let mut hs = Vec::with_capacity(nthreads);
            for _ in 0..nthreads {
                let ev_r: Events = ev_shared.clone();
                let h = std::thread::Builder::new()
                    .stack_size(2 << 20)
                    .spawn_scoped(s, move || {
                        let mut local = Local::new();
                        loop {
                            if cn_r.load(Ordering::Relaxed) {
                                break;
                            }
                            match q_r.pop() {
                                Some((dir, depth, my_root, reparse_depth)) => {
                                    walk_one(
                                        &dir,
                                        depth,
                                        my_root,
                                        reparse_depth,
                                        plan_r,
                                        ri_r,
                                        m_r,
                                        c_r,
                                        q_r,
                                        &mut local,
                                        cn_r,
                                        &ev_r,
                                        now,
                                    );
                                    c_r.pending.fetch_sub(1, Ordering::SeqCst);
                                }
                                None => {
                                    if c_r.pending.load(Ordering::SeqCst) == 0 && q_r.empty() {
                                        break;
                                    }
                                    std::thread::sleep(std::time::Duration::from_millis(1));
                                }
                            }
                        }
                        local
                    });
                match h {
                    Ok(h) => hs.push(h),
                    Err(_) => break,
                }
            }
            for h in hs {
                match h.join() {
                    Ok(l) => locals.push(l),
                    Err(_) => locals.push(Local::new()),
                }
            }
        });
    }
    stop.store(true, Ordering::Relaxed);
    let _ = ticker.join();

    let cancelled = cancel.load(Ordering::Relaxed);
    match plan.mode {
        Mode::Stream(kind) => {
            let mut all = Local::new();
            for l in locals.drain(..) {
                all.merge(l);
            }
            let files = counters.files.load(Ordering::Relaxed) as u64;
            let bytes = counters.bytes.load(Ordering::Relaxed) as u64;
            let json = match kind {
                StreamKind::All => format!(
                    "{{\"files\":{files},\"bytes\":{bytes},\"cancelled\":{cancelled}}}",
                    cancelled = cancelled
                ),
                StreamKind::Junk => format!(
                    "{{\"files\":{},\"bytes\":{},\"scanned\":{files},\"cancelled\":{}}}",
                    all.junk_files, all.junk_bytes, cancelled
                ),
            };
            Outcome { json }
        }
        Mode::Aggregates => {
            let mut all = Local::new();
            for l in locals.drain(..) {
                all.merge(l);
            }
            all.trim(plan.top_files, plan.samples);
            let totals = agg::rollup(&all.own);
            let mut samples_map: HashMap<&'static str, Vec<(String, u64)>> = all
                .cat_samples
                .iter()
                .map(|(k, h)| {
                    let v: Vec<(String, u64)> = h
                        .clone()
                        .into_sorted_vec()
                        .into_iter()
                        .map(|(s, p)| (p, s.0))
                        .collect();
                    (*k, v)
                })
                .collect();
            let mut age: Vec<(String, u64, u64)> = all
                .age
                .into_iter()
                .map(|(k, v)| (k, v.0, v.1))
                .collect();
            age.sort_by_key(|(k, _, _)| agg::age_order(k));
            let cat_files: Vec<(String, u64, u64)> = all
                .cat_files
                .into_iter()
                .map(|(k, v)| (k.to_string(), v.0, v.1))
                .collect();
            let aggres = agg::Aggregates {
                result: agg::IndexResult {
                    files: counters.files.load(Ordering::Relaxed) as u64,
                    dirs: counters.dirs.load(Ordering::Relaxed) as u64,
                    bytes: counters.bytes.load(Ordering::Relaxed) as u64,
                    errors: counters.errors.load(Ordering::Relaxed) as u64,
                    reparse_skipped: counters.reparse.load(Ordering::Relaxed) as u64,
                    cancelled,
                    elapsed_ms: t0.elapsed().as_millis(),
                },
                top_folders: agg::top_of(totals, plan.top_files),
                top_files: std::mem::take(&mut all.top_files)
                    .into_sorted_vec()
                    .into_iter()
                    .map(|(s, m, p)| (p, s.0, m.0))
                    .collect(),
                by_ext: agg::pairs_top(
                    std::mem::take(&mut all.ext)
                        .into_iter()
                        .map(|(k, v)| (k, v.0, v.1))
                        .collect(),
                    40,
                ),
                by_age: age,
                by_drive: agg::pairs_top(
                    std::mem::take(&mut all.drive)
                        .into_iter()
                        .map(|(k, v)| (k, v.0, v.1))
                        .collect(),
                    32,
                ),
                by_cat: {
                    let mut v = cat_files;
                    v.sort_by(|a, b| b.1.cmp(&a.1));
                    v
                },
            };
            let mut json = agg::build_json(&aggres, plan.top_files, 40);
            if plan.want_categories {
                json = inject_meta(&json, &mut samples_map, &matcher, env);
            }
            Outcome { json }
        }
    }
}

fn inject_meta(
    json: &str,
    samples: &mut HashMap<&'static str, Vec<(String, u64)>>,
    matcher: &Matcher,
    env: &Env,
) -> String {
    let mut v: serde_json::Value = match serde_json::from_str(json) {
        Ok(v) => v,
        Err(_) => return json.to_string(),
    };
    if let Some(arr) = v.get_mut("by_category").and_then(|x| x.as_array_mut()) {
        for item in arr.iter_mut() {
            let id = match item.get("category").and_then(|c| c.as_str()) {
                Some(s) => s.to_string(),
                None => continue,
            };
            let cat = match cats::find(&id) {
                Some(c) => c,
                None => continue,
            };
            item["lane"] = serde_json::json!(lane_name(cat.lane));
            item["title"] = serde_json::json!(cat.title);
            item["risk"] = serde_json::json!(cat.risk);
            item["regrows"] = serde_json::json!(cat.regrows);
            item["admin"] = serde_json::json!(cat.admin);
            item["recoverable"] = serde_json::json!(cat.lane == Lane::Trash);
            let roots: Vec<String> = cat
                .patterns
                .iter()
                .map(|p| win(&env.expand(p)))
                .collect();
            item["roots"] = serde_json::json!(roots);
            let list: Vec<serde_json::Value> = samples
                .remove(id.as_str())
                .unwrap_or_default()
                .into_iter()
                .map(|(p, s)| serde_json::json!({"path": p, "size": s}))
                .collect();
            item["samples"] = serde_json::json!(list);
        }
    }
    v["regen_allowed_prefixes"] = serde_json::json!(
        matcher
            .rule_roots()
            .into_iter()
            .map(|(p, c)| serde_json::json!({"path": p, "category": c}))
            .collect::<Vec<_>>()
    );
    v.to_string()
}

pub fn lane_name(l: Lane) -> &'static str {
    match l {
        Lane::Trash => "trash",
        Lane::Direct => "direct",
        Lane::ViewOnly => "view",
    }
}

pub fn cat_roots(env: &Env) -> Vec<(String, &'static str)> {
    let mut out: Vec<(String, &'static str)> = Vec::new();
    for c in cats::categories() {
        for p in c.patterns {
            let e = win(&env.expand(p));
            if !out.iter().any(|(x, _)| x == &e) {
                out.push((e, c.id));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn idx(v: &[&str]) -> RootIndex {
        RootIndex::build(&v.iter().map(|s| s.to_string()).collect::<Vec<String>>())
    }

    #[test]
    fn roditel_ne_zahodit_v_dochniy_koren() {
        let r = idx(&["c:/users/atiun", "c:/users/atiun/downloads"]);
        assert!(r.covered_by_other("c:/users/atiun/downloads/foo", 0));
        assert!(!r.covered_by_other("c:/users/atiun/appdata", 0));
        assert!(!r.covered_by_other("c:/users/atiun/downloads/x/y", 1));
        assert!(!r.covered_by_other("c:/users/other", 0));
    }

    #[test]
    fn granica_segmenta_a_ne_prosto_prefiks() {
        let r = idx(&["c:/a", "c:/ab"]);
        assert!(!r.covered_by_other("c:/abc/x", 0));
        assert!(r.covered_by_other("c:/ab/c/x", 0));
        assert!(!r.covered_by_other("c:/ab/c/x", 1));
    }

    #[test]
    fn odinakovie_korni_slivayutsya() {
        let r = idx(&["c:/a", "c:/a"]);
        assert_eq!(r.sorted.len(), 1);
        assert!(!r.covered_by_other("c:/a/b", 0));
    }
}
