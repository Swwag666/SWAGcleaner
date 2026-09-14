mod agg;
mod cats;
mod emit;
mod hash;
mod pathx;
mod purge;
mod scan;
mod win;

use std::io::{BufRead, BufReader, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use cats::Env;
use emit::Events;
use pathx::{norm, q, win};
use scan::{Mode, Plan, StreamKind};

const PROTOCOL: &str = "swagscan-ndjson/1";

fn usage_lines(ev: &Events) {
    ev.send(format!(
        "{{\"event\":\"hello\",\"protocol\":\"{PROTOCOL}\",\"commands\":[\"capabilities\",\"help\",\"index\",\"categories\",\"candidates\",\"duplicates\",\"purge\",\"empty_bin\",\"drives\",\"progress\"],\"platform\":\"windows\",\"long_paths\":true}}"
    ));
    ev.send(
        "{\"event\":\"capabilities\",\"mft\":false,\"long_paths\":true,\"lanes\":[\"trash\",\"direct\"],\"streams\":\"ndjson\",\"hash\":\"blake3\",\"size_units\":\"bytes\"}"
            .to_string(),
    );
}

fn str_field(v: &serde_json::Value, key: &str) -> Option<String> {
    v.get(key).and_then(|x| x.as_str()).map(|s| s.to_string())
}

fn u64_field(v: &serde_json::Value, key: &str, def: u64) -> u64 {
    v.get(key).and_then(|x| x.as_u64()).unwrap_or(def)
}

fn usize_field(v: &serde_json::Value, key: &str, def: usize) -> usize {
    v.get(key)
        .and_then(|x| x.as_u64())
        .map(|x| x as usize)
        .unwrap_or(def)
}

fn bool_field(v: &serde_json::Value, key: &str, def: bool) -> bool {
    v.get(key).and_then(|x| x.as_bool()).unwrap_or(def)
}

fn str_array(v: &serde_json::Value, key: &str) -> Vec<String> {
    v.get(key)
        .and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

fn default_roots(env: &Env) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for b in b'C'..=b'Z' {
        let root = format!("{}:\\", b as char);
        if win::disk_free(&root).is_some() {
            out.push(root);
        }
    }
    if out.is_empty() {
        if !env.userprofile.is_empty() {
            out.push(env.userprofile.clone());
        } else {
            out.push("C:\\".to_string());
        }
    }
    out
}

fn resolve_roots(v: &serde_json::Value, env: &Env) -> Vec<String> {
    let roots = str_array(v, "roots");
    if !roots.is_empty() {
        return roots.iter().map(|r| norm(r)).collect();
    }
    if bool_field(v, "all_drives", false) {
        return default_roots(env).iter().map(|r| norm(r)).collect();
    }
    if bool_field(v, "cat_roots", false) {
        return scan::cat_roots(env)
            .into_iter()
            .filter(|(p, _)| std::path::Path::new(p).is_dir())
            .map(|(p, _)| norm(&p))
            .collect();
    }
    let m = cats::Matcher::build(env);
    m.rule_roots()
        .into_iter()
        .filter(|(p, _)| std::path::Path::new(p).is_dir())
        .map(|(p, _)| norm(&p))
        .collect()
}

fn drives_json() -> String {
    let mut items: Vec<String> = Vec::new();
    for b in b'A'..=b'Z' {
        let root = format!("{}:\\", b as char);
        if let Some(sp) = win::disk_free(&root) {
            let vi = win::volume_info(&root);
            let fs = vi.as_ref().map(|v| v.fs_name.clone()).unwrap_or_default();
            let label = vi.map(|v| v.label).unwrap_or_default();
            items.push(format!(
                "{{\"drive\":{},\"fs\":{},\"label\":{},\"total\":{},\"free\":{},\"free_total\":{}}}",
                q(&root),
                q(&fs),
                q(&label),
                sp.total,
                sp.free_to_caller,
                sp.total_free
            ));
        }
    }
    format!("[{}]", items.join(","))
}

fn handle(
    cmd: &serde_json::Value,
    ev: &Events,
    cancel: &Arc<AtomicBool>,
    env: &Env,
    id: &str,
) {
    let method = str_field(cmd, "cmd").unwrap_or_default();
    match method.as_str() {
        "capabilities" | "help" => {
            usage_lines(ev);
            ev.result_ok(id, format!("{{\"protocol\":\"{PROTOCOL}\"}}"));
        }
        "index" => {
            let mut plan = Plan::default();
            plan.roots = resolve_roots(cmd, env);
            plan.max_depth = usize_field(cmd, "max_depth", usize::MAX);
            plan.follow_reparse = bool_field(cmd, "follow_reparse", false);
            plan.include_hidden = bool_field(cmd, "include_hidden", true);
            plan.threads = usize_field(cmd, "threads", 0);
            plan.top_files = usize_field(cmd, "top", 40).clamp(1, 5000);
            plan.want_categories = bool_field(cmd, "categories", true);
            plan.mode = Mode::Aggregates;
            if plan.roots.is_empty() {
                ev.result_err(id, "no readable roots");
                return;
            }
            let out = scan::run(&plan, env, ev, cancel, "index");
            if out.cancelled {
                ev.cancelled(id, "index");
            }
            ev.result_ok(id, out.json);
        }
        "categories" => {
            let list = scan::cat_roots(env);
            let arr: Vec<String> = list
                .iter()
                .map(|(p, c)| format!("{{\"path\":{},\"category\":{}}}", q(p), q(c)))
                .collect();
            ev.result_ok(id, format!("[{}]", arr.join(",")));
        }
        "cat_meta" => {
            let arr: Vec<String> = cats::categories()
                .iter()
                .map(|c| {
                    format!(
                        "{{\"id\":{},\"title\":{},\"lane\":{},\"risk\":{},\"regrows\":{},\"admin\":{}}}",
                        q(c.id),
                        q(c.title),
                        q(scan::lane_name(c.lane)),
                        q(c.risk),
                        if c.regrows { "true" } else { "false" },
                        if c.admin { "true" } else { "false" }
                    )
                })
                .collect();
            ev.result_ok(id, format!("[{}]", arr.join(",")));
        }
        "candidates" => {
            let mut plan = Plan::default();
            plan.roots = resolve_roots(cmd, env);
            plan.threads = usize_field(cmd, "threads", 0);
            plan.include_hidden = bool_field(cmd, "include_hidden", true);
            plan.min_size = u64_field(cmd, "min_size", 0);
            plan.mode = Mode::Stream(StreamKind::Junk);
            if plan.roots.is_empty() {
                ev.result_err(id, "no readable roots");
                return;
            }
            let out = scan::run(&plan, env, ev, cancel, "candidates");
            if out.cancelled {
                ev.cancelled(id, "candidates");
            }
            ev.result_ok(id, out.json);
        }
        "duplicates" => {
            let mut dp = hash::DupPlan::default();
            dp.roots = resolve_roots(cmd, env);
            dp.min_size = u64_field(cmd, "min_size", 1024 * 1024);
            dp.exts = str_array(cmd, "exts");
            dp.threads = usize_field(cmd, "threads", 0);
            dp.limit_groups = usize_field(cmd, "limit_groups", 2000).clamp(1, 50_000);
            if dp.roots.is_empty() {
                ev.result_err(id, "no readable roots");
                return;
            }
            let data = hash::run(&dp, ev, cancel, "duplicates");
            ev.result_ok(id, data);
        }
        "purge" => {
            let raw = cmd.get("items").and_then(|x| x.as_array()).cloned();
            let items: Vec<purge::Item> = match raw {
                Some(a) => a
                    .iter()
                    .filter_map(|x| {
                        let path = match str_field(x, "path") {
                            Some(p) => p,
                            None => return None,
                        };
                        let category = str_field(x, "category").unwrap_or_else(|| "manual".into());
                        Some(purge::Item {
                            path_win: win(&norm(&path)),
                            category,
                        })
                    })
                    .collect(),
                None => Vec::new(),
            };
            if items.is_empty() {
                ev.result_err(id, "no items");
                return;
            }
            let plan = purge::PurgePlan {
                items,
                dry_run: bool_field(cmd, "dry_run", true),
            };
            let data = purge::run(&plan, env, ev, cancel, "purge");
            ev.result_ok(id, data);
        }
        "empty_bin" => {
            if !bool_field(cmd, "confirm", false) {
                ev.result_err(id, "needs confirm=true");
                return;
            }
            let drive = str_field(cmd, "drive");
            win::co_initialize();
            let r = win::empty_recycle_bin(drive.as_deref());
            win::co_uninitialize();
            match r {
                Ok(()) => ev.result_ok(id, "{\"emptied\":true}".to_string()),
                Err(rc) => ev.result_err(id, &format!("SHEmptyRecycleBinW rc={rc}")),
            }
        }
        "drives" => {
            ev.result_ok(id, drives_json());
        }
        other => ev.result_err(id, &format!("unknown cmd: {other}")),
    }
}

fn id_of(v: &serde_json::Value) -> String {
    match v.get("id") {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => "0".to_string(),
    }
}

fn main() {
    let env = Env::detect();
    let ev = emit::Writer::stdout().into_events();
    usage_lines(&ev);

    let cancel = Arc::new(AtomicBool::new(false));
    let (tx, rx) = std::sync::mpsc::channel::<String>();

    std::thread::spawn({
        let cancel = Arc::clone(&cancel);
        move || {
            let stdin = std::io::stdin();
            let mut reader = BufReader::new(stdin.lock());
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {}
                }
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if trimmed.contains("\"cancel\"") {
                    cancel.store(true, Ordering::SeqCst);
                    continue;
                }
                if tx.send(trimmed.to_string()).is_err() {
                    break;
                }
            }
        }
    });

    loop {
        let line = match rx.recv() {
            Ok(l) => l,
            Err(_) => break,
        };
        let v: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                ev.result_err("0", &format!("bad json: {e}"));
                continue;
            }
        };
        let cmd = match str_field(&v, "cmd") {
            Some(c) => c,
            None => {
                ev.result_err("0", "missing cmd");
                continue;
            }
        };
        if cmd == "quit" || cmd == "exit" {
            ev.log("info", "quit requested");
            break;
        }
        if cmd == "cancel" {
            cancel.store(true, Ordering::SeqCst);
            continue;
        }
        if cmd == "ping" {
            ev.result_ok(&id_of(&v), "{\"pong\":true}".to_string());
            continue;
        }
        cancel.store(false, Ordering::SeqCst);
        let id = id_of(&v);
        handle(&v, &ev, &cancel, &env, &id);
    }
    let _ = std::io::stdout().flush();
}
