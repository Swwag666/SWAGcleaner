pub fn esc(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

pub fn q(s: &str) -> String {
    format!("\"{}\"", esc(s))
}

pub fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub fn strip_verbatim(path: &str) -> &str {
    if let Some(rest) = path.strip_prefix("\\\\?\\") {
        if let Some(unc) = rest.strip_prefix("UNC\\") {
            return unc;
        }
        return rest;
    }
    path
}

/// Нормализация пути СТРОКОВО, без обращения к ФС:
/// - `\` -> `/`, нижний регистр, verbatim-префиксы сняты;
/// - сегменты "." выкидываются, ".." снимает предыдущий сегмент
///   (выше корня диска/шары не поднимаемся: лишние ".." ОТБРАСЫВАЮТСЯ —
///   это закрывает обход белого списка через "..\");
/// - у каждого сегмента срезаются trailing dots/spaces
///   (семантика Win32: "dir .\file" == "dir\file", "name.txt " == "name.txt").
pub fn norm(path: &str) -> String {
    let src = strip_verbatim(path);
    let unc = path.starts_with("\\\\?\\UNC\\") || src.starts_with("\\\\");
    // UNC: //srv/share — корень, ".." не должен съедать шару/сервер.
    let min_keep = if unc { 2 } else { 0 };
    let mut segs: Vec<String> = Vec::new();
    for part in src.split(|c| c == '\\' || c == '/') {
        // Сегменты только из точек/пробелов — особые по правилам Win32:
        // "." = текущий каталог, ".." (и сводящиеся к нему ".. ", ".. .")
        // = родитель, а 3+ точек — ЛИТЕРАЛЬНОЕ имя (Windows сохраняет "..."
        // как каталог, не сводит к ".."; mkdir ... работает). Greedy-trim
        // тут нельзя: он стёр бы ".." целиком и превратил бы "..." в "..".
        if part.chars().all(|c| c == '.' || c == ' ') {
            let dots = part.chars().take_while(|c| *c == '.').count();
            if dots == 2 {
                if segs.len() > min_keep {
                    if let Some(last) = segs.last() {
                        if !last.ends_with(':') {
                            segs.pop();
                        }
                    }
                }
            } else if dots >= 3 {
                segs.push(".".repeat(dots));
            }
            continue;
        }
        // Обычный сегмент: срезаем хвостовые точки/пробелы (семантика Win32).
        let cleaned = part.trim_end_matches(['.', ' ']);
        if cleaned.is_empty() {
            continue;
        }
        segs.push(cleaned.chars().flat_map(|c| c.to_lowercase()).collect());
    }
    let mut s = String::with_capacity(src.len() + 2);
    if unc {
        s.push_str("//");
    }
    s.push_str(&segs.join("/"));
    s
}

pub fn win(path_norm: &str) -> String {
    path_norm.replace('/', "\\")
}

pub fn to_verbatim(path_win: &str) -> String {
    if path_win.starts_with("\\\\?\\") {
        path_win.to_string()
    } else if path_win.starts_with("\\\\") {
        format!("\\\\?\\UNC\\{}", &path_win[2..])
    } else {
        format!("\\\\?\\{path_win}")
    }
}

pub fn find_pattern(dir_norm: &str) -> String {
    let w = win(dir_norm);
    if w.len() > 230 {
        to_verbatim(&w) + "\\*"
    } else {
        format!("{w}\\*")
    }
}

pub fn parent(path: &str) -> Option<&str> {
    let trimmed = if path.ends_with('/') && path.len() > 1 {
        &path[..path.len() - 1]
    } else {
        path
    };
    match trimmed.rfind('/') {
        Some(0) | None => None,
        Some(i) => Some(&trimmed[..i]),
    }
}

pub fn file_name(path: &str) -> &str {
    match path.rfind('/') {
        Some(i) => &path[i + 1..],
        None => path,
    }
}

pub fn drive(path_norm: &str) -> Option<&str> {
    let b = path_norm.as_bytes();
    if b.len() >= 2 && b[1] == b':' && (b[0] as char).is_ascii_alphabetic() {
        Some(&path_norm[..2])
    } else {
        None
    }
}

pub fn root_of(path_norm: &str) -> String {
    if let Some(d) = drive(path_norm) {
        d.to_string()
    } else if path_norm.starts_with("//") {
        let rest = &path_norm[2..];
        match rest.find('/') {
            Some(host_end) => match rest[host_end + 1..].find('/') {
                Some(share_end) => path_norm[..host_end + share_end + 3].to_string(),
                None => path_norm.to_string(),
            },
            None => path_norm.to_string(),
        }
    } else {
        String::new()
    }
}

pub fn extension(path_norm: &str) -> String {
    let name = file_name(path_norm);
    match name.rfind('.') {
        Some(0) | None => String::new(),
        Some(i) => name[i + 1..].to_lowercase(),
    }
}

pub fn starts_with_path(path_norm: &str, prefix_norm: &str) -> bool {
    if prefix_norm.is_empty() {
        return false;
    }
    if path_norm.len() == prefix_norm.len() {
        return path_norm == prefix_norm;
    }
    path_norm.starts_with(prefix_norm)
        && path_norm.as_bytes().get(prefix_norm.len()) == Some(&b'/')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bytes_human(n: u64) -> String {
        const UNITS: [&str; 5] = ["B", "KB", "MB", "GB", "TB"];
        let mut v = n as f64;
        let mut i = 0;
        while v >= 1024.0 && i < UNITS.len() - 1 {
            v /= 1024.0;
            i += 1;
        }
        if i == 0 {
            format!("{n} B")
        } else {
            format!("{:.1} {}", v, UNITS[i])
        }
    }

    #[test]
    fn normalizuet_slashi_i_registr() {
        assert_eq!(norm("C:\\Temp\\A"), "c:/temp/a");
        assert_eq!(norm("C:\\Temp\\A\\"), "c:/temp/a");
    }

    #[test]
    fn snyaet_verbatim_prefiks() {
        assert_eq!(norm("\\\\?\\C:\\A\\b"), "c:/a/b");
        assert_eq!(norm("\\\\?\\UNC\\srv\\share\\x"), "//srv/share/x");
    }

    #[test]
    fn ekranizuet_kavychki_i_backslash() {
        assert_eq!(q("C:\\a\"b"), "\"C:\\\\a\\\"b\"");
    }

    #[test]
    fn roditel_disk_i_koren() {
        assert_eq!(parent("c:/users/one"), Some("c:/users"));
        assert_eq!(parent("c:/"), None);
        assert_eq!(parent("c:"), None);
        assert_eq!(drive("c:/x"), Some("c:"));
        assert_eq!(drive("x"), None);
        assert_eq!(root_of("c:/x/y"), "c:");
        assert_eq!(root_of("//srv/share/a"), "//srv/share");
    }

    #[test]
    fn sravnivaet_prefiks_tolko_po_granice_segmenta() {
        assert!(starts_with_path("c:/temp/a", "c:/temp"));
        assert!(!starts_with_path("c:/tempx/a", "c:/temp"));
        assert!(starts_with_path("c:/temp", "c:/temp"));
        assert!(!starts_with_path("c:/temp", ""));
    }

    #[test]
    fn rasshirenie_ne_beret_tochku_v_nachale() {
        assert_eq!(extension("c:/a/b.PNG"), "png");
        assert_eq!(extension("c:/a/.gitignore"), "");
        assert_eq!(extension("c:/a/b"), "");
    }

    #[test]
    fn dlinnyi_putid_get_verbatim() {
        let long = format!("c:/{}x", "a".repeat(250));
        let p = find_pattern(&long);
        assert!(p.starts_with("\\\\?\\"), "ожидается verbatim-префикс");
    }

    #[test]
    fn chitaet_chelovecheski_razmery() {
        assert_eq!(bytes_human(512), "512 B");
        assert_eq!(bytes_human(2048), "2.0 KB");
    }

    #[test]
    fn norm_shlopivaet_tochki_i_katalogi_vverh() {
        assert_eq!(norm("c:/windows/system32/../temp"), "c:/windows/temp");
        assert_eq!(norm("C:\\Windows\\System32\\.\\drivers"), "c:/windows/system32/drivers");
        assert_eq!(norm("c:/a/./b/../c"), "c:/a/c");
    }

    #[test]
    fn norm_srezaet_hvostovye_tochki_i_probely_segmentov() {
        // Win32: "dir .\file" == "dir\file", "name " == "name"
        assert_eq!(norm("c:/windows/system32 ./x"), "c:/windows/system32/x");
        assert_eq!(norm("c:/temp/name.txt "), "c:/temp/name.txt");
        assert_eq!(norm("c:/temp/name.txt."), "c:/temp/name.txt");
    }

    #[test]
    fn norm_ne_daet_podnyatsya_vyshe_kornya() {
        // Лишние ".." на корне отбрасываются: обход белого списка через
        // "c:/temp/../../windows" превращается в честный "c:/windows".
        assert_eq!(norm("c:/../windows"), "c:/windows");
        assert_eq!(norm("c:/temp/../../windows/system32"), "c:/windows/system32");
        assert_eq!(norm("c:/"), "c:");
        // ".." с хвостовыми точками/пробелами — тот же переход наверх.
        assert_eq!(norm("c:/a/b/.. ./c"), "c:/a/c");
        // "..." — литеральное имя каталога (Windows его сохраняет).
        assert_eq!(norm("c:/a/b/.../c"), "c:/a/b/.../c");
    }

    #[test]
    fn norm_unc_ne_daet_sest_sharu() {
        assert_eq!(norm("\\\\srv\\share\\a\\..\\b"), "//srv/share/b");
        assert_eq!(norm("\\\\srv\\share\\..\\..\\x"), "//srv/share/x");
        assert_eq!(norm("\\\\srv\\share\\"), "//srv/share");
    }
}
