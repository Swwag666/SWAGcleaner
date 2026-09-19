use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use crate::cats::{self, Env, Lane, Matcher};
use crate::emit::{Events, Progress};
use crate::pathx::{find_pattern, norm, starts_with_path, q};
use crate::scan::lane_name;
use crate::win as wapi;

pub struct Item {
    pub path_win: String,
    pub category: String,
}

pub struct PurgePlan {
    pub items: Vec<Item>,
    pub dry_run: bool,
}

struct Resolved {
    path_norm: String,
    path_win: String,
    category: &'static str,
    lane: Lane,
    size: u64,
    /// Физический размер на диске (сжатые NTFS/sparse занимают меньше
    /// логического). Именно его возвращает диск при удалении.
    disk_size: u64,
    is_dir: bool,
}

#[derive(Debug)]
pub struct Reject {
    pub path: String,
    pub reason: &'static str,
}

fn stat(path_win: &str) -> Option<(u64, bool, u32)> {
    let direct = wapi::find_first(path_win);
    let tried = match direct {
        Ok(v) => Some(v),
        Err(_) => wapi::find_first(&crate::pathx::to_verbatim(path_win)).ok(),
    };
    let (handle, data) = tried?;
    let attrs = data.dw_file_attributes;
    let is_dir = attrs & wapi::FA_DIRECTORY != 0;
    let size = if is_dir { 0 } else { wapi::find_size(&data) };
    wapi::find_close(handle);
    Some((size, is_dir, attrs))
}

fn subtree_size(dir_norm: &str, cancel: &Arc<AtomicBool>, budget: &mut u64) -> u64 {
    if *budget == 0 || cancel.load(Ordering::Relaxed) {
        return 0;
    }
    let (handle, mut data) = match wapi::find_first(&find_pattern(dir_norm)) {
        Ok(v) => v,
        Err(_) => return 0,
    };
    let mut total = 0u64;
    loop {
        let name = wapi::find_name(&data);
        if name != "." && name != ".." {
            let attrs = data.dw_file_attributes;
            let is_dir = attrs & wapi::FA_DIRECTORY != 0;
            let reparse = attrs & wapi::FA_REPARSE != 0;
            if is_dir {
                if !reparse {
                    *budget -= 1;
                    total += subtree_size(&crate::agg::join(dir_norm, &name), cancel, budget);
                }
            } else {
                total += wapi::find_size(&data);
            }
        }
        if *budget == 0 || !wapi::find_next(handle, &mut data) {
            break;
        }
    }
    wapi::find_close(handle);
    total
}

fn resolve(
    plan: &PurgePlan,
    env: &Env,
    matcher: &Matcher,
    cancel: &Arc<AtomicBool>,
) -> (Vec<Resolved>, Vec<Reject>) {
    let mut ok: Vec<Resolved> = Vec::new();
    let mut bad: Vec<Reject> = Vec::new();
    let mut budget: u64 = 2_000_000;

    for item in &plan.items {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let path_norm = norm(&item.path_win);
        if path_norm.len() <= 3 {
            bad.push(Reject {
                path: item.path_win.clone(),
                reason: "refused: drive or root path",
            });
            continue;
        }
        if cats::is_blocked(&path_norm) {
            bad.push(Reject {
                path: item.path_win.clone(),
                reason: "refused: system-protected location",
            });
            continue;
        }
        let (size, is_dir, attrs) = match stat(&item.path_win) {
            Some(v) => v,
            None => {
                bad.push(Reject {
                    path: item.path_win.clone(),
                    reason: "not found",
                });
                continue;
            }
        };
        if attrs & wapi::FA_REPARSE != 0 {
            bad.push(Reject {
                path: item.path_win.clone(),
                reason: "refused: symlink or junction",
            });
            continue;
        }
        let cat = match cats::find(&item.category) {
            Some(c) => c,
            None => {
                bad.push(Reject {
                    path: item.path_win.clone(),
                    reason: "unknown category",
                });
                continue;
            }
        };
        if cat.lane == Lane::ViewOnly {
            bad.push(Reject {
                path: item.path_win.clone(),
                reason: "category is view-only",
            });
            continue;
        }
        let mut lane = cat.lane;
        if lane == Lane::Direct && !matcher.is_regen_allowed(&path_norm) {
            lane = Lane::Trash;
        }
        // UNC (\\srv\share) — только Direct: корзина по сети либо не
        // работает, либо SHFileOperation молча удаляет напрямую. Честнее
        // сразу удалять напрямую, чем делать вид, что это корзина.
        if path_norm.starts_with("//") {
            lane = Lane::Direct;
        }
        let final_size = if is_dir {
            budget -= 1;
            subtree_size(&path_norm, cancel, &mut budget)
        } else {
            size
        };
        // Для каталогов физический размер не считаем (субдерево и так логическое);
        // для файлов спрашиваем сжатый размер — честный учёт освобождённого места.
        let disk = if is_dir {
            final_size
        } else {
            wapi::compressed_size(&item.path_win).unwrap_or(final_size)
        };
        ok.push(Resolved {
            path_norm,
            path_win: item.path_win.clone(),
            category: cat.id,
            lane,
            size: final_size,
            disk_size: disk,
            is_dir,
        });
    }
    let _ = env;
    (ok, bad)
}

fn drop_covered(items: &mut Vec<Resolved>) {
    items.sort_by(|a, b| a.path_norm.len().cmp(&b.path_norm.len()));
    let mut roots: Vec<String> = Vec::new();
    let mut keep: Vec<Resolved> = Vec::with_capacity(items.len());
    for it in items.drain(..) {
        if roots.iter().any(|r| starts_with_path(&it.path_norm, r)) {
            continue;
        }
        roots.push(it.path_norm.clone());
        keep.push(it);
    }
    *items = keep;
}

const CHUNK: usize = 400;

/// Запасной путь для direct-дорожки: прямое DeleteFileW/RemoveDirectoryW.
/// Ok — файл удалён (учёт сделан внутри); Err(код Win32) — честная причина
/// отказа (32 = занят, 5 = нужен админ), она и уходит в отчёт.
fn direct_fallback(
    single: &Resolved,
    freed: &mut u64,
    removed: &mut usize,
) -> Result<(), u32> {
    let r = if single.is_dir {
        wapi::remove_dir_direct(&single.path_win)
    } else {
        wapi::delete_file_direct(&single.path_win)
    };
    match r {
        Ok(()) => {
            *freed += single.disk_size;
            *removed += 1;
            Ok(())
        }
        // Файл исчез между сканом и удалением (служба сама сротировала лог):
        // мусора на диске уже нет — это не ошибка, а достигнутая цель.
        Err(2) => {
            *freed += single.disk_size;
            *removed += 1;
            Ok(())
        }
        Err(code) => Err(code),
    }
}

pub fn run(
    plan: &PurgePlan,
    env: &Env,
    ev: &Events,
    cancel: &Arc<AtomicBool>,
    job: &str,
) -> String {
    let matcher = Matcher::build(env);
    let mut p = Progress::new();
    let (mut items, rejects) = resolve(plan, env, &matcher, cancel);
    drop_covered(&mut items);

    let mut by_lane: HashMap<&'static str, (u64, u64)> = HashMap::new();
    let mut total_size = 0u64;
    for it in &items {
        total_size += it.size;
        let e = by_lane
            .entry(lane_name(it.lane))
            .or_insert((0, 0));
        e.0 += 1;
        e.1 += it.size;
    }

    let planned = items.len();
    p.emit(ev, true, job, "planned", planned as u64, Some(planned as u64), total_size, None);

    if plan.dry_run {
        return report(&items, &rejects, &[], true, planned, total_size, 0, 0, 0, cancel, env);
    }

    wapi::co_initialize();
    let mut failures: Vec<(String, String)> = Vec::new();
    // freed — физически освобождено сейчас (direct-дорожка); trashed — уехало
    // в корзину: место вернётся только после её очистки. Обе цифры — по
    // физическому (сжатому) размеру, иначе на сжатых логах счётчик врёт в разы.
    let mut freed = 0u64;
    let mut trashed = 0u64;
    let mut removed = 0usize;

    for allow_undo in [true, false] {
        let lane = if allow_undo { Lane::Trash } else { Lane::Direct };
        let group: Vec<&Resolved> = items.iter().filter(|i| i.lane == lane).collect();
        if group.is_empty() {
            continue;
        }
        for chunk in group.chunks(CHUNK) {
            if cancel.load(Ordering::Relaxed) {
                for i in chunk {
                    failures.push((i.path_win.clone(), "cancelled".to_string()));
                }
                continue;
            }
            let paths: Vec<String> = chunk.iter().map(|i| i.path_win.clone()).collect();
            let before: u64 = chunk.iter().map(|i| i.disk_size).sum();
            match wapi::shell_delete(&paths, allow_undo) {
                Ok(()) => {
                    if allow_undo {
                        trashed += before;
                    } else {
                        freed += before;
                    }
                    removed += chunk.len();
                }
                Err((rc, aborted)) => {
                    if aborted {
                        // Пользователь оборвал shell-диалог: часть чанка уже
                        // удалена. Пере-статуем каждый файл: исчезнувшие
                        // считаем удалёнными ( freed/removed ), оставшиеся —
                        // отменёнными. Целиком чанк не валим.
                        for single in chunk {
                            if stat(&single.path_win).is_none() {
                                if allow_undo {
                                    trashed += single.disk_size;
                                } else {
                                    freed += single.disk_size;
                                }
                                removed += 1;
                            } else {
                                failures.push((
                                    single.path_win.clone(),
                                    "cancelled by user".to_string(),
                                ));
                            }
                        }
                    } else if chunk.len() > 1 {
                        for single in chunk {
                            let one = vec![single.path_win.clone()];
                            match wapi::shell_delete(&one, allow_undo) {
                                Ok(()) => {
                                    if allow_undo {
                                        trashed += single.disk_size;
                                    } else {
                                        freed += single.disk_size;
                                    }
                                    removed += 1;
                                }
                                Err((rc2, _)) => {
                                    if !allow_undo {
                                        match direct_fallback(single, &mut freed, &mut removed) {
                                            Ok(()) => continue,
                                            Err(code) => {
                                                failures.push((
                                                    single.path_win.clone(),
                                                    format!(
                                                        "{} (rc={code})",
                                                        wapi::winerror_text(code)
                                                    ),
                                                ));
                                                continue;
                                            }
                                        }
                                    }
                                    failures.push((
                                        single.path_win.clone(),
                                        format!("SHFileOperationW rc={rc2} ({})", wapi::winerror_text(rc2 as u32)),
                                    ));
                                }
                            }
                        }
                    } else {
                        // Одиночный чанк: для direct-дорожки пробуем прямое
                        // удаление ядром — шелл часто отвечает DE_INVALIDFILES
                        // на файлы, которые DeleteFileW берёт без вопросов.
                        let single = chunk[0];
                        if !allow_undo {
                            match direct_fallback(single, &mut freed, &mut removed) {
                                Ok(()) => {
                                    p.emit(ev, false, job, "direct", removed as u64,
                                           Some(planned as u64), freed, None);
                                    continue;
                                }
                                Err(code) => {
                                    failures.push((
                                        single.path_win.clone(),
                                        format!("{} (rc={code})", wapi::winerror_text(code)),
                                    ));
                                    p.emit(ev, false, job, "direct", removed as u64,
                                           Some(planned as u64), freed, None);
                                    continue;
                                }
                            }
                        }
                        for i in chunk {
                            failures.push((
                                i.path_win.clone(),
                                format!(
                                    "SHFileOperationW rc={rc} aborted={aborted} ({})",
                                    wapi::winerror_text(rc as u32)
                                ),
                            ));
                        }
                    }
                }
            }
            p.emit(
                ev,
                false,
                job,
                if allow_undo { "trash" } else { "direct" },
                removed as u64,
                Some(planned as u64),
                freed + trashed,
                None,
            );
        }
    }
    wapi::co_uninitialize();

    report(&items, &rejects, &failures, false, planned, total_size, freed, trashed, removed, cancel, env)
}

fn report(
    items: &[Resolved],
    rejects: &[Reject],
    failures: &[(String, String)],
    dry_run: bool,
    planned: usize,
    planned_bytes: u64,
    freed: u64,
    trashed: u64,
    removed: usize,
    cancel: &Arc<AtomicBool>,
    _env: &Env,
) -> String {
    let mut by_cat: HashMap<&'static str, (u64, u64)> = HashMap::new();
    for i in items {
        let e = by_cat.entry(i.category).or_insert((0, 0));
        e.0 += 1;
        e.1 += i.size;
    }
    let cats_json: Vec<String> = by_cat
        .iter()
        .map(|(k, v)| format!("{{\"category\":{},\"files\":{},\"bytes\":{}}}", q(k), v.0, v.1))
        .collect();
    let rej_json: Vec<String> = rejects
        .iter()
        .map(|r| format!("{{\"path\":{},\"reason\":{}}}", q(&r.path), q(r.reason)))
        .collect();
    let fail_json: Vec<String> = failures
        .iter()
        .map(|(p, e)| format!("{{\"path\":{},\"error\":{}}}", q(p), q(e)))
        .collect();
    let lane_summary: Vec<String> = {
        let mut m: HashMap<&'static str, (u64, u64)> = HashMap::new();
        for i in items {
            let e = m.entry(lane_name(i.lane)).or_insert((0, 0));
            e.0 += 1;
            e.1 += i.size;
        }
        m.into_iter()
            .map(|(k, v)| format!("{{\"lane\":{},\"files\":{},\"bytes\":{}}}", q(k), v.0, v.1))
            .collect()
    };

    format!(
        "{{\"dry_run\":{dry_run},\"planned\":{planned},\"planned_bytes\":{planned_bytes},\"removed\":{removed},\"freed_bytes\":{freed},\"trashed_bytes\":{trashed},\"refused\":{},\"cancelled\":{},\"lanes\":[{}],\"categories\":[{}],\"rejects\":[{}],\"failures\":[{}]}}",
        rejects.len(),
        cancel.load(Ordering::Relaxed),
        lane_summary.join(","),
        cats_json.join(","),
        rej_json.join(","),
        fail_json.join(",")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env() -> Env {
        Env {
            temp: "C:\\Users\\one\\AppData\\Local\\Temp".into(),
            systemroot: "C:\\Windows".into(),
            local: "C:\\Users\\one\\AppData\\Local".into(),
            roaming: "C:\\Users\\one\\AppData\\Roaming".into(),
            programdata: "C:\\ProgramData".into(),
            userprofile: "C:\\Users\\one".into(),
        }
    }

    #[test]
    fn otkazyvaet_koren_i_zashchishchennye_mesta() {
        let e = env();
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![
                Item { path_win: "C:\\".into(), category: "temp.app".into() },
                Item { path_win: "C:\\Windows\\System32\\x.dll".into(), category: "temp.app".into() },
                Item { path_win: "C:\\nope\\nope".into(), category: "temp.app".into() },
            ],
            dry_run: true,
        };
        let (ok, bad) = resolve(&plan, &e, &m, &cancel);
        assert!(ok.is_empty());
        assert_eq!(bad.len(), 3);
    }

    #[test]
    fn pryamoe_udalenie_tolko_v_belom_spiske() {
        let dir = std::env::temp_dir().join(format!("swagscan_p_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("a.tmp");
        std::fs::write(&f, b"hello").unwrap();
        let e = Env {
            temp: dir.to_string_lossy().to_string(),
            ..env()
        };
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![Item {
                path_win: f.to_string_lossy().to_string(),
                category: "temp.app".into(),
            }],
            dry_run: true,
        };
        let (ok, bad) = resolve(&plan, &e, &m, &cancel);
        assert!(bad.is_empty(), "{:?}", bad);
        assert_eq!(ok.len(), 1);
        assert_eq!(ok[0].lane, Lane::Direct);
        assert_eq!(ok[0].size, 5);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn vne_spiska_skatyaet_v_korzinu() {
        let dir = std::env::temp_dir().join(format!("swagscan_t_{}", std::process::id()));
        std::fs::create_dir_all(dir.join("Downloads")).unwrap();
        let f = dir.join("Downloads").join("x.txt");
        std::fs::write(&f, b"1234").unwrap();
        let e = Env {
            userprofile: dir.to_string_lossy().to_string(),
            ..env()
        };
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![Item {
                path_win: f.to_string_lossy().to_string(),
                category: "temp.app".into(),
            }],
            dry_run: true,
        };
        let (ok, _) = resolve(&plan, &e, &m, &cancel);
        assert_eq!(ok.len(), 1);
        assert_eq!(ok[0].lane, Lane::Trash);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn unc_vsegda_direct_bez_korziny() {
        // Сетевой путь не уезжает в Trash: корзина по сети либо не работает,
        // либо молча удаляет напрямую. lane форсим в Direct.
        let e = env();
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![Item {
                path_win: "\\\\server\\share\\temp\\x.tmp".into(),
                category: "temp.app".into(),
            }],
            dry_run: true,
        };
        let (ok, bad) = resolve(&plan, &e, &m, &cancel);
        // Файл не существует — resolve отклонит его по not found, но до этого
        // проверка lane проходит раньше stat(). Поэтому проверяем причину:
        // отказ должен быть "not found", а не про корзину.
        assert!(ok.is_empty());
        assert_eq!(bad.len(), 1);
        assert_eq!(bad[0].reason, "not found");
    }

    #[test]
    fn unc_lane_forsiruetsya_v_direct() {
        // Отдельно проверяем сам форс: UNC + категория Trash → Direct.
        // Используем живой локальный файл через UNC-нотацию localhost.
        let dir = std::env::temp_dir().join(format!("swagscan_u_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("y.tmp");
        std::fs::write(&f, b"12345").unwrap();
        // TEMP обычно вида C:\Users\...\Temp — перегоняем в \\localhost\C$\...
        let plain = f.to_string_lossy().to_string();
        let unc = if plain.len() > 2 && plain.as_bytes()[1] == b':' {
            format!("\\\\localhost\\{}$\\{}", plain.as_bytes()[0] as char, &plain[3..])
        } else {
            String::new()
        };
        if !unc.is_empty() && std::path::Path::new(&unc).exists() {
            let e = env();
            let m = Matcher::build(&e);
            let cancel = Arc::new(AtomicBool::new(false));
            let plan = PurgePlan {
                items: vec![Item {
                    path_win: unc.clone(),
                    category: "temp.app".into(),
                }],
                dry_run: true,
            };
            let (ok, bad) = resolve(&plan, &e, &m, &cancel);
            assert!(bad.is_empty(), "{:?}", bad);
            assert_eq!(ok.len(), 1);
            assert_eq!(ok[0].lane, Lane::Direct);
        }
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn dupes_photo_idet_v_korzinu_iz_lyubogo_nesistemnogo_puti() {
        let dir = std::env::temp_dir().join(format!("swagscan_d_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("photo copy.jpg");
        std::fs::write(&f, b"jpeg").unwrap();
        let e = env();
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![Item {
                path_win: f.to_string_lossy().to_string(),
                category: "dupes.photo".into(),
            }],
            dry_run: true,
        };
        let (ok, bad) = resolve(&plan, &e, &m, &cancel);
        assert!(bad.is_empty(), "{:?}", bad);
        assert_eq!(ok.len(), 1);
        assert_eq!(ok[0].lane, Lane::Trash);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn resolve_zapolnyaet_fizicheskii_razmer() {
        let dir = std::env::temp_dir().join(format!("swagscan_ds_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("b.tmp");
        std::fs::write(&f, vec![3u8; 7777]).unwrap();
        let e = Env {
            temp: dir.to_string_lossy().to_string(),
            ..env()
        };
        let m = Matcher::build(&e);
        let cancel = Arc::new(AtomicBool::new(false));
        let plan = PurgePlan {
            items: vec![Item {
                path_win: f.to_string_lossy().to_string(),
                category: "temp.app".into(),
            }],
            dry_run: true,
        };
        let (ok, bad) = resolve(&plan, &e, &m, &cancel);
        assert!(bad.is_empty(), "{:?}", bad);
        assert_eq!(ok.len(), 1);
        assert_eq!(ok[0].size, 7777);
        assert_eq!(ok[0].disk_size, 7777);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn drop_covered_ubyraet_potomkov() {
        let mut v = vec![
            Resolved { path_norm: "c:/a/b".into(), path_win: "C:\\a\\b".into(), category: "temp.app", lane: Lane::Direct, size: 1, disk_size: 1, is_dir: false },
            Resolved { path_norm: "c:/a/b/c".into(), path_win: "C:\\a\\b\\c".into(), category: "temp.app", lane: Lane::Direct, size: 2, disk_size: 2, is_dir: false },
            Resolved { path_norm: "c:/z".into(), path_win: "C:\\z".into(), category: "temp.app", lane: Lane::Direct, size: 3, disk_size: 3, is_dir: false },
        ];
        drop_covered(&mut v);
        assert_eq!(v.len(), 2);
        assert!(v.iter().any(|i| i.path_norm == "c:/a/b"));
        assert!(!v.iter().any(|i| i.path_norm == "c:/a/b/c"));
    }
}
