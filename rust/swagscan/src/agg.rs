use std::collections::HashMap;

use crate::pathx::{parent, q, win};

pub fn join(dir_norm: &str, name: &str) -> String {
    // dir_norm всегда lowercase; имя из ФС приходит в реальном регистре
    // («Downloads»). Без приведения child-путь смешивает регистры, и сравнения
    // с lowercase-префиксами (правила категорий, корни обхода) врут:
    // «.../Downloads» не совпадёт с «.../downloads», а между собой это один путь.
    let mut s = String::with_capacity(dir_norm.len() + 1 + name.len());
    s.push_str(dir_norm);
    s.push('/');
    s.extend(name.chars().flat_map(|c| c.to_lowercase()));
    s
}

pub fn age_bucket(secs_ago: i64) -> &'static str {
    let days = secs_ago / 86_400;
    if days < 7 {
        "lt_7d"
    } else if days < 30 {
        "7_30d"
    } else if days < 90 {
        "30_90d"
    } else if days < 365 {
        "90_365d"
    } else if days < 1_095 {
        "1_3y"
    } else {
        "gt_3y"
    }
}

pub fn age_order(k: &str) -> u8 {
    match k {
        "lt_7d" => 0,
        "7_30d" => 1,
        "30_90d" => 2,
        "90_365d" => 3,
        "1_3y" => 4,
        "gt_3y" => 5,
        _ => 9,
    }
}

pub struct IndexResult {
    pub files: u64,
    pub dirs: u64,
    pub bytes: u64,
    pub errors: u64,
    pub reparse_skipped: u64,
    pub cancelled: bool,
    pub elapsed_ms: u128,
}

pub struct Aggregates {
    pub result: IndexResult,
    pub top_folders: Vec<(String, u64, u64)>,
    pub top_files: Vec<(String, u64, i64)>,
    pub by_ext: Vec<(String, u64, u64)>,
    pub by_age: Vec<(String, u64, u64)>,
    pub by_drive: Vec<(String, u64, u64)>,
    pub by_cat: Vec<(String, u64, u64)>,
}

pub fn rollup(own: &HashMap<String, (u64, u64)>) -> HashMap<String, (u64, u64)> {
    let mut keys: Vec<&String> = own.keys().collect();
    keys.sort_by(|a, b| b.len().cmp(&a.len()));
    let mut totals: HashMap<String, (u64, u64)> = HashMap::with_capacity(own.len() * 2);
    for k in keys {
        let from_children = totals.get(k.as_str()).cloned().unwrap_or((0, 0));
        let base = own.get(k.as_str()).cloned().unwrap_or((0, 0));
        let acc = (base.0 + from_children.0, base.1 + from_children.1);
        totals.insert(k.clone(), acc);
        if let Some(p) = parent(k) {
            let e = totals.entry(p.to_string()).or_insert((0, 0));
            e.0 += acc.0;
            e.1 += acc.1;
        }
    }
    totals
}

pub fn top_of(totals: HashMap<String, (u64, u64)>, n: usize) -> Vec<(String, u64, u64)> {
    let mut v: Vec<(String, u64, u64)> = totals
        .into_iter()
        .map(|(k, val)| (k, val.0, val.1))
        .filter(|(_, b, _)| *b > 0)
        .collect();
    v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| b.2.cmp(&a.2)));
    v.truncate(n);
    v
}

pub fn pairs_top(mut v: Vec<(String, u64, u64)>, n: usize) -> Vec<(String, u64, u64)> {
    v.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| b.2.cmp(&a.2)));
    v.truncate(n);
    v
}

pub fn build_json(a: &Aggregates, top_n: usize, ext_top_n: usize) -> String {
    let r = &a.result;
    let mut s = String::from("{\"result\":{");
    s.push_str(&format!("\"files\":{}", r.files));
    s.push_str(&format!(",\"dirs\":{}", r.dirs));
    s.push_str(&format!(",\"bytes\":{}", r.bytes));
    s.push_str(&format!(",\"errors\":{}", r.errors));
    s.push_str(&format!(",\"elapsed_ms\":{}", r.elapsed_ms));
    s.push_str(&format!(",\"reparse_skipped\":{}", r.reparse_skipped));
    s.push_str(&format!(
        ",\"cancelled\":{}",
        if r.cancelled { "true" } else { "false" }
    ));
    s.push_str("},\"top_folders\":[");
    for (i, (p, b, c)) in a.top_folders.iter().take(top_n).enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!(
            "{{\"path\":{},\"bytes\":{b},\"files\":{c}}}",
            q(&win(p))
        ));
    }
    s.push_str("],\"top_files\":[");
    for (i, (p, sz, m)) in a.top_files.iter().take(top_n).enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!(
            "{{\"path\":{},\"size\":{sz},\"mtime\":{m}}}",
            q(&win(p))
        ));
    }
    s.push_str("],\"by_ext\":[");
    for (i, (e, n, b)) in a.by_ext.iter().take(ext_top_n).enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{{\"ext\":{},\"files\":{n},\"bytes\":{b}}}", q(e)));
    }
    s.push_str("],\"by_age\":[");
    for (i, (k, n, b)) in a.by_age.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{{\"bucket\":{},\"files\":{n},\"bytes\":{b}}}", q(k)));
    }
    s.push_str("],\"by_drive\":[");
    for (i, (d, n, b)) in a.by_drive.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{{\"drive\":{},\"files\":{n},\"bytes\":{b}}}", q(d)));
    }
    s.push_str("],\"by_category\":[");
    for (i, (c, n, b)) in a.by_cat.iter().take(64).enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!(
            "{{\"category\":{},\"files\":{n},\"bytes\":{b}}}",
            q(c)
        ));
    }
    s.push_str("]}");
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prisoedinyaet_imya_v_nizhnem_registre() {
        assert_eq!(join("c:/users", "Downloads"), "c:/users/downloads");
        assert_eq!(join("c:/a", "B.C"), "c:/a/b.c");
    }

    #[test]
    fn rollup_summiruet_vverh() {
        let mut own = HashMap::new();
        own.insert("c:/a".to_string(), (100u64, 2u64));
        own.insert("c:/a/b".to_string(), (50u64, 1u64));
        let t = rollup(&own);
        assert_eq!(t["c:/a/b"], (50, 1));
        assert_eq!(t["c:/a"], (150, 3));
    }

    #[test]
    fn poryadok_vozrastnyh_korzin() {
        assert!(age_order("lt_7d") < age_order("7_30d"));
        assert!(age_order("1_3y") < age_order("gt_3y"));
        assert_eq!(age_bucket(0), "lt_7d");
        assert_eq!(age_bucket(4_000 * 86_400), "gt_3y");
    }

    #[test]
    fn json_parsetsya() {
        let a = Aggregates {
            result: IndexResult {
                files: 1,
                dirs: 2,
                bytes: 3,
                errors: 0,
                reparse_skipped: 0,
                cancelled: false,
                elapsed_ms: 42,
            },
            top_folders: vec![("c:/a".into(), 10, 1)],
            top_files: vec![("c:/a/f.txt".into(), 10, 100)],
            by_ext: vec![("txt".into(), 1, 10)],
            by_age: vec![("lt_7d".into(), 1, 10)],
            by_drive: vec![("c:".into(), 1, 10)],
            by_cat: vec![("temp.app".into(), 1, 10)],
        };
        let s = build_json(&a, 10, 10);
        let v: serde_json::Value = serde_json::from_str(&s).expect("должен парситься");
        assert_eq!(v["result"]["files"], 1);
        assert_eq!(v["top_folders"][0]["path"], "c:\\a");
    }
}
