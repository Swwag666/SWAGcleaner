use crate::pathx::{extension, file_name, norm, starts_with_path};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lane {
    Trash,
    Direct,
    ViewOnly,
}

pub struct Cat {
    pub id: &'static str,
    pub title: &'static str,
    pub lane: Lane,
    pub risk: &'static str,
    pub regrows: bool,
    pub admin: bool,
    pub by_ext: &'static [&'static str],
    pub age_days: u64,
    pub min_size: u64,
    pub patterns: &'static [&'static str],
    pub name_prefix: &'static str,
    pub path_must_contain: &'static str,
}

const TEMP_APP: &Cat = &Cat {
    id: "temp.app",
    title: "Temporary files of apps",
    lane: Lane::Direct,
    risk: "low",
    regrows: true,
    admin: false,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &["%TEMP%"],
    name_prefix: "",
    path_must_contain: "",
};

const TEMP_SYS: &Cat = &Cat {
    id: "temp.system",
    title: "Windows temporary files",
    lane: Lane::Direct,
    risk: "low",
    regrows: true,
    admin: true,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &["%SystemRoot%\\Temp"],
    name_prefix: "",
    path_must_contain: "",
};

const BROWSER_CACHE: &Cat = &Cat {
    id: "cache.browsers",
    title: "Browser cache",
    lane: Lane::Direct,
    risk: "low",
    regrows: true,
    admin: false,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &[
        "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Cache",
        "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Code Cache",
        "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\GPUCache",
        "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\Service Worker\\CacheStorage",
        "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Cache",
        "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\Code Cache",
        "%LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\GPUCache",
        "%LOCALAPPDATA%\\Yandex\\YandexBrowser\\User Data\\Default\\Cache",
        "%LOCALAPPDATA%\\Yandex\\YandexBrowser\\User Data\\Default\\Code Cache",
        "%LOCALAPPDATA%\\Yandex\\YandexBrowser\\User Data\\Default\\GPUCache",
        "%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles",
    ],
    name_prefix: "",
    path_must_contain: "",
};

const CACHE_APP: &Cat = &Cat {
    id: "cache.apps",
    title: "Caches of apps",
    lane: Lane::Direct,
    risk: "low",
    regrows: true,
    admin: false,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &[
        "%LOCALAPPDATA%\\pip\\Cache",
        "%LOCALAPPDATA%\\Microsoft\\Windows\\INetCache",
        "%LOCALAPPDATA%\\NuGet\\Cache",
        "%LOCALAPPDATA%\\Composer\\Cache",
        "%APPDATA%\\npm-cache",
        "%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer",
    ],
    name_prefix: "",
    path_must_contain: "",
};

const DUMPS: &Cat = &Cat {
    id: "dumps",
    title: "Memory dumps",
    lane: Lane::Direct,
    risk: "medium",
    regrows: false,
    admin: false,
    by_ext: &["dmp", "mdmp"],
    age_days: 0,
    min_size: 0,
    patterns: &[
        "%LOCALAPPDATA%\\CrashDumps",
        "%SystemRoot%\\Minidump",
        "%SystemRoot%\\LiveKernelReports",
    ],
    name_prefix: "",
    path_must_contain: "",
};

const LOGS: &Cat = &Cat {
    id: "logs",
    title: "Logs and error reports",
    lane: Lane::Direct,
    risk: "low",
    regrows: true,
    admin: false,
    by_ext: &["log", "etl", "evtx"],
    age_days: 0,
    min_size: 0,
    patterns: &[
        "%SystemRoot%\\Logs",
        "%ProgramData%\\Microsoft\\Windows\\WER\\ReportArchive",
        "%ProgramData%\\Microsoft\\Windows\\WER\\ReportQueue",
        "%LOCALAPPDATA%\\Microsoft\\Windows\\WER",
    ],
    name_prefix: "",
    path_must_contain: "",
};

const WIN_UPDATES: &Cat = &Cat {
    id: "updates",
    title: "Windows update leftovers",
    lane: Lane::Direct,
    risk: "medium",
    regrows: true,
    admin: true,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &[
        "%SystemRoot%\\SoftwareDistribution\\Download",
        "%ProgramData%\\Microsoft\\Network\\Downloader",
    ],
    name_prefix: "",
    path_must_contain: "",
};

const INSTALLERS: &Cat = &Cat {
    id: "installers",
    title: "Old installers in Downloads",
    lane: Lane::Trash,
    risk: "medium",
    regrows: false,
    admin: false,
    by_ext: &["msi", "exe", "msix", "appx"],
    age_days: 30,
    min_size: 5 * 1024 * 1024,
    patterns: &["%USERPROFILE%\\Downloads"],
    name_prefix: "",
    path_must_contain: "",
};

const OLD_LARGE: &Cat = &Cat {
    id: "old.large",
    title: "Large files untouched for a long time",
    lane: Lane::Trash,
    risk: "medium",
    regrows: false,
    admin: false,
    by_ext: &[],
    age_days: 730,
    min_size: 1024 * 1024 * 1024,
    patterns: &["%USERPROFILE%"],
    name_prefix: "",
    path_must_contain: "",
};

// Точные дубликаты фото приходят в purge из Python-слоя (хеши считает он же),
// а не из обхода: пустые patterns означают, что в скане категория не участвует
// и не даёт правил, но purge принимает её с дорожкой «в корзину».
const DUPES_PHOTO: &Cat = &Cat {
    id: "dupes.photo",
    title: "Exact photo duplicates",
    lane: Lane::Trash,
    risk: "medium",
    regrows: false,
    admin: false,
    by_ext: &[],
    age_days: 0,
    min_size: 0,
    patterns: &[],
    name_prefix: "",
    path_must_contain: "",
};

pub fn categories() -> Vec<&'static Cat> {
    vec![
        TEMP_APP,
        TEMP_SYS,
        BROWSER_CACHE,
        CACHE_APP,
        DUMPS,
        LOGS,
        WIN_UPDATES,
        INSTALLERS,
        OLD_LARGE,
        DUPES_PHOTO,
    ]
}

pub fn find(id: &str) -> Option<&'static Cat> {
    categories().into_iter().find(|c| c.id == id)
}

pub struct Env {
    pub temp: String,
    pub systemroot: String,
    pub local: String,
    pub roaming: String,
    pub programdata: String,
    pub userprofile: String,
}

fn grab(k: &str) -> String {
    std::env::var(k).unwrap_or_default()
}

impl Env {
    pub fn detect() -> Env {
        let mut systemroot = grab("SystemRoot");
        if systemroot.is_empty() {
            systemroot = "C:\\Windows".into();
        }
        if systemroot.to_lowercase().contains("sysnative") {
            systemroot = systemroot.replace("Sysnative", "System32");
            systemroot = systemroot.replace("sysnative", "System32");
        }
        let local = grab("LOCALAPPDATA");
        let t = grab("TEMP");
        let temp = if t.is_empty() {
            format!("{local}\\Temp")
        } else {
            t
        };
        Env {
            temp,
            systemroot,
            local,
            roaming: grab("APPDATA"),
            programdata: grab("ProgramData"),
            userprofile: grab("USERPROFILE"),
        }
    }

    pub fn expand(&self, pat: &str) -> String {
        let mut s = pat.to_string();
        for (k, v) in [
            ("%TEMP%", &self.temp),
            ("%TMP%", &self.temp),
            ("%LOCALAPPDATA%", &self.local),
            ("%APPDATA%", &self.roaming),
            ("%ProgramData%", &self.programdata),
            ("%USERPROFILE%", &self.userprofile),
            ("%SystemRoot%", &self.systemroot),
            ("%WINDIR%", &self.systemroot),
        ] {
            s = s.replace(k, v);
        }
        s
    }
}

pub struct Rule {
    pub cat: &'static Cat,
    pub prefix: String,
    pub pattern: String,
    pub firefox: bool,
    pub explorer: bool,
}

pub struct Matcher {
    rules: Vec<Rule>,
    regen_prefixes: Vec<String>,
}

impl Matcher {
    pub fn build(env: &Env) -> Matcher {
        let mut rules = Vec::new();
        for c in categories() {
            for pat in c.patterns {
                let prefix = norm(&env.expand(pat));
                if prefix.is_empty() || prefix == "//" {
                    continue;
                }
                // Переменная окружения, развернувшаяся в корень диска
                // (TEMP="C:\" -> prefix "c:") или в UNC-корень ("//srv/share"),
                // не должна превращать весь диск/шару в категорию-мусор.
                if prefix.len() <= 3 || crate::pathx::root_of(&prefix) == prefix {
                    continue;
                }
                rules.push(Rule {
                    cat: c,
                    pattern: env.expand(pat),
                    firefox: pat.contains("Firefox\\Profiles"),
                    explorer: pat.ends_with("\\Explorer"),
                    prefix,
                });
            }
        }
        rules.sort_by(|a, b| b.prefix.len().cmp(&a.prefix.len()));
        let regen_prefixes = rules
            .iter()
            .filter(|r| r.cat.lane == Lane::Direct)
            .map(|r| r.prefix.clone())
            .collect();
        Matcher { rules, regen_prefixes }
    }

    pub fn classify(&self, path_norm: &str, size: u64, mtime: i64, now: i64) -> Vec<&'static Cat> {
        let mut out: Vec<&'static Cat> = Vec::new();
        let age_days = if now > mtime { (now - mtime) / 86_400 } else { 0 };
        let ext = extension(path_norm);
        for r in &self.rules {
            if !starts_with_path(path_norm, &r.prefix) {
                continue;
            }
            if !r.cat.by_ext.is_empty() && !r.cat.by_ext.contains(&ext.as_str()) {
                continue;
            }
            if r.cat.age_days > 0 && (age_days as u64) < r.cat.age_days {
                continue;
            }
            if r.cat.min_size > 0 && size < r.cat.min_size {
                continue;
            }
            if !r.cat.name_prefix.is_empty()
                && !file_name(path_norm)
                    .starts_with(&r.cat.name_prefix.to_lowercase())
            {
                continue;
            }
            if !r.cat.path_must_contain.is_empty()
                && !path_norm.contains(&r.cat.path_must_contain.to_lowercase())
            {
                continue;
            }
            if r.firefox && !(path_norm.contains("/cache2/") || path_norm.contains("/startupcache/")) {
                continue;
            }
            if r.explorer
                && !path_norm.contains("/thumbcache_")
                && !path_norm.contains("/iconcache_")
            {
                continue;
            }
            if out.iter().any(|c| c.id == r.cat.id) {
                continue;
            }
            out.push(r.cat);
        }
        out
    }

    pub fn is_regen_allowed(&self, path_norm: &str) -> bool {
        self.regen_prefixes
            .iter()
            .any(|p| starts_with_path(path_norm, p))
    }

    pub fn rule_roots(&self) -> Vec<(String, &'static str)> {
        let mut out: Vec<(String, &'static str)> = Vec::new();
        for r in &self.rules {
            let w = crate::pathx::win(&r.pattern);
            if !out.iter().any(|(p, _)| p == &w) {
                out.push((w, r.cat.id));
            }
        }
        out
    }
}

/// Блок-лист системных мест. Матч СЕГМЕНТНЫЙ: путь бьётся на сегменты по '/',
/// блок срабатывает, если какой-то сегмент ЦЕЛИКОМ равен имени из списка.
/// Это ловит и сам каталог ("c:/windows/system32"), и его содержимое,
/// но не трогает похожие имена ("winsxs-backup").
pub fn is_blocked(path_norm: &str) -> bool {
    const BLOCKED_SEGMENTS: &[&str] = &[
        "winsxs",
        "installer",
        "windowsinstaller",
        "system32",
        "syswow64",
        "boot",
        "program files",
        "program files (x86)",
        "recovery",
        "system volume information",
        "$mft",
        "$extend",
        "pagefile.sys",
        "hiberfil.sys",
        "swapfile.sys",
    ];
    if path_norm.len() <= 3 {
        return true;
    }
    for seg in path_norm.split('/') {
        if BLOCKED_SEGMENTS.contains(&seg) {
            return true;
        }
    }
    // Многосегментные правила, которые сегментным матчем не выразить.
    if path_norm.contains("/programdata/microsoft/windows defender/")
        || path_norm.ends_with("/programdata/microsoft/windows defender")
    {
        return true;
    }
    if path_norm.contains("/wine/preview/") {
        return true;
    }
    if path_norm.ends_with("/config") || path_norm.contains("/windows/config/") {
        return true;
    }
    false
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

    fn m() -> Matcher {
        Matcher::build(&env())
    }

    #[test]
    fn temp_popadaet_v_temp_app() {
        let c = m().classify("c:/users/one/appdata/local/temp/abc.tmp", 1000, 0, 100);
        assert!(c.iter().any(|x| x.id == "temp.app"));
    }

    #[test]
    fn dupes_photo_ne_uchastvuet_v_skane_no_dostupna_purge() {
        // Пустые patterns: категория не даёт правил обхода — скан её не видит.
        let c = m().classify("c:/users/one/pictures/img_0001.jpg", 5_000_000, 0, 100);
        assert!(!c.iter().any(|x| x.id == "dupes.photo"));
        // Но purge принимает её и ведёт в корзину, а не мимо неё.
        let cat = find("dupes.photo").expect("категория обязана существовать");
        assert_eq!(cat.lane, Lane::Trash);
        assert!(!cat.regrows);
        assert!(!cat.admin);
    }

    #[test]
    fn winsxs_i_system32_zablokirovany() {
        assert!(is_blocked("c:/windows/winsxs/foo/bar"));
        assert!(is_blocked("c:/windows/system32/kernel32.dll"));
        assert!(is_blocked("c:/program files/app/app.exe"));
        assert!(!is_blocked("c:/windows/temp/a.log"));
    }

    #[test]
    fn blok_segmentnyi_a_ne_podstrochnyi() {
        // Сам каталог без хвостового слэша тоже ловится.
        assert!(is_blocked("c:/windows/system32"));
        assert!(is_blocked("c:/windows/system32/drivers"));
        assert!(is_blocked("c:/windows/winsxs"));
        assert!(is_blocked("c:/boot"));
        assert!(is_blocked("c:/pagefile.sys"));
        // Похожие имена с другим сегментом — НЕ блокируются.
        assert!(!is_blocked("c:/x/winsxs-backup/y"));
        assert!(!is_blocked("c:/x/system32-backup"));
        assert!(!is_blocked("c:/x/installers-cache/y"));
        assert!(!is_blocked("c:/x/bootleg/y"));
    }

    #[test]
    fn pravilo_s_kornem_diska_ignoriruetsya() {
        // TEMP=C:\ не должен превращать весь диск в temp.app.
        let e = Env {
            temp: "C:\\".into(),
            ..env()
        };
        let mm = Matcher::build(&e);
        let c = mm.classify("c:/windows/anything.tmp", 1000, 0, 100);
        assert!(!c.iter().any(|x| x.id == "temp.app"));
        // И белый список регенерации тоже не захватывает корень.
        assert!(!mm.is_regen_allowed("c:/windows"));
        // А нормальный TEMP продолжает работать.
        let mm2 = m();
        assert!(mm2.is_regen_allowed("c:/users/one/appdata/local/temp/x"));
    }

    #[test]
    fn package_cache_bolshe_ne_direct() {
        // %ProgramData%\Package Cache — ловушка: удаление ломает Repair VS.
        let mm = m();
        let c = mm.classify("c:/programdata/package cache/{guid}/pkg.msi", 10_000_000, 0, 100);
        assert!(!c.iter().any(|x| x.id == "cache.apps"));
        assert!(!mm.is_regen_allowed("c:/programdata/package cache/x"));
    }

    #[test]
    fn installers_starse_tridcati_dnej_i_krupnee_mega() {
        let mm = m();
        let now = 1_700_000_000i64;
        let old = now - 60 * 86_400;
        let fresh = now - 2 * 86_400;
        let big = 20 * 1024 * 1024;
        assert!(mm
            .classify("c:/users/one/downloads/setup.msi", big, old, now)
            .iter()
            .any(|x| x.id == "installers"));
        assert!(!mm
            .classify("c:/users/one/downloads/setup.msi", big, fresh, now)
            .iter()
            .any(|x| x.id == "installers"));
        assert!(!mm
            .classify("c:/users/one/downloads/report.pdf", big, old, now)
            .iter()
            .any(|x| x.id == "installers"));
        assert!(!mm
            .classify("c:/users/one/downloads/tiny.msi", 4096, old, now)
            .iter()
            .any(|x| x.id == "installers"));
    }

    #[test]
    fn regeneriruemoe_tolko_v_belom_spiske() {
        let mm = m();
        assert!(mm.is_regen_allowed("c:/users/one/appdata/local/temp/x"));
        assert!(mm.is_regen_allowed("c:/windows/temp/x"));
        assert!(!mm.is_regen_allowed("c:/users/one/downloads/x"));
        assert!(!mm.is_regen_allowed("c:/users/one/pictures/1.jpg"));
    }

    #[test]
    fn chrome_kesh_ljaetsja() {
        let p = "c:/users/one/appdata/local/google/chrome/user data/default/cache/f_000123";
        assert!(m()
            .classify(p, 100, 0, 100)
            .iter()
            .any(|x| x.id == "cache.browsers"));
    }

    #[test]
    fn firefox_tolko_kesh_a_ne_cookies() {
        let mm = m();
        let hit = "c:/users/one/appdata/local/mozilla/firefox/profiles/abcd.default/cache2/entry";
        let miss = "c:/users/one/appdata/local/mozilla/firefox/profiles/abcd.default/cookies.sqlite";
        assert!(mm.classify(hit, 10, 0, 100).iter().any(|x| x.id == "cache.browsers"));
        assert!(!mm.classify(miss, 10, 0, 100).iter().any(|x| x.id == "cache.browsers"));
    }

    #[test]
    fn thumbcache_v_cache_apps() {
        let mm = m();
        let t = "c:/users/one/appdata/local/microsoft/windows/explorer/thumbcache_1024.db";
        let x = "c:/users/one/appdata/local/microsoft/windows/explorer/other.db";
        assert!(mm.classify(t, 10, 0, 100).iter().any(|c| c.id == "cache.apps"));
        assert!(!mm.classify(x, 10, 0, 100).iter().any(|c| c.id == "cache.apps"));
    }

    #[test]
    fn dokumenty_ne_musor() {
        let c = m().classify("c:/users/one/documents/report.txt", 10, 0, 100);
        assert!(!c.iter().any(|x| x.lane == Lane::Direct));
    }

    #[test]
    fn temp_system_ne_trashaet() {
        let c = m().classify("c:/windows/temp/x", 10, 0, 100);
        let hit = c.iter().find(|x| x.id == "temp.system");
        assert!(hit.is_some());
        assert_eq!(hit.unwrap().lane, Lane::Direct);
    }

    #[test]
    fn raznye_kategorii_dvajdy_ne_povtorejutsja() {
        let c = m().classify("c:/users/one/appdata/local/temp/a.log", 10, 0, 100);
        let mut ids: Vec<&str> = c.iter().map(|x| x.id).collect();
        let before = ids.len();
        ids.sort();
        ids.dedup();
        assert_eq!(before, ids.len());
    }

    #[test]
    fn staroe_krasnoe_pravilo_ne_lovit_svehee() {
        let mm = m();
        let now = 1_700_000_000i64;
        let p = "c:/users/one/videos/bigfile.mkv";
        let old = now - 900 * 86_400;
        assert!(mm.classify(p, 3 << 30, old, now).iter().any(|x| x.id == "old.large"));
        assert!(!mm.classify(p, 3 << 30, now - 10, now).iter().any(|x| x.id == "old.large"));
    }
}
