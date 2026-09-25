//! MFT-режим (этап 6, R2): список файлов тома за секунды, без обхода каталогов.
//!
//! Читаем $MFT напрямую с тома (\\.\C:, нужны права администратора):
//! boot-сектор -> кластер и размер записи MFT -> runlist атрибута $DATA
//! записи 0 (самой MFT) -> образ MFT -> записи FILE -> имена ($FILE_NAME),
//! размеры ($DATA), флаги каталогов -> пути склейкой по родительским FRN.
//!
//! Зачем: FindFirstFile-обход тома на сотни тысяч файлов - минуты и пляска
//! дескрипторов; MFT - один последовательный поток чтения, «секунды на том».
//! Любая ошибка чтения/парсинга - Err строкой: клиент падает на walker.

use std::collections::HashMap;

use crate::win as wapi;

pub struct MftFile {
    pub path: String,
    pub size: u64,
    pub is_dir: bool,
}

pub struct MftStats {
    pub files: usize,
    pub dirs: usize,
    pub bytes: u64,
    pub records: usize,
    pub ms: u128,
    pub record_size: usize,
    pub cluster: u64,
    pub image_bytes: usize,
}

const ATTR_STANDARD: u32 = 0x10;
const ATTR_FILE_NAME: u32 = 0x30;
const ATTR_DATA: u32 = 0x80;
const ATTR_END: u32 = 0xFFFF_FFFF;

fn u16le(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}

fn u32le(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}

fn u64le(b: &[u8], at: usize) -> u64 {
    let mut v = [0u8; 8];
    v.copy_from_slice(&b[at..at + 8]);
    u64::from_le_bytes(v)
}

/// Runlist NTFS: цепочка (смещение кластера относительно прошлого, длина).
pub fn parse_runs(data: &[u8]) -> Vec<(i64, u64)> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos < data.len() {
        let header = data[pos];
        if header == 0 {
            break;
        }
        let len_bytes = (header & 0x0F) as usize;
        let off_bytes = ((header >> 4) & 0x0F) as usize;
        if len_bytes == 0 || pos + 1 + len_bytes + off_bytes > data.len() {
            break;
        }
        let mut length = 0u64;
        for i in 0..len_bytes {
            length |= (data[pos + 1 + i] as u64) << (8 * i);
        }
        let mut offset = 0i64;
        for i in 0..off_bytes {
            let byte = data[pos + 1 + len_bytes + i] as i64;
            offset |= (byte & 0xFF) << (8 * i);
        }
        // Знаковое расширение последнего байта смещения.
        if off_bytes > 0 && data[pos + 1 + len_bytes + off_bytes - 1] & 0x80 != 0 {
            offset -= 1i64 << (8 * off_bytes);
        }
        out.push((offset, length));
        pos += 1 + len_bytes + off_bytes;
    }
    out
}

struct NameInfo {
    parent: u64,
    name: String,
    namespace: u8,
}

fn parse_record(rec: &[u8]) -> Option<(Vec<NameInfo>, u64, bool)> {
    if rec.len() < 48 || &rec[0..4] != b"FILE" {
        return None;
    }
    let first_attr = u16le(rec, 0x14) as usize;
    let flags = u16le(rec, 0x16);
    if flags & 0x01 == 0 {
        return None; // запись удалена: имя и размеры уже мусор
    }
    let is_dir = flags & 0x02 != 0;
    let mut names: Vec<NameInfo> = Vec::new();
    let mut size: u64 = 0;
    let mut pos = first_attr;
    while pos + 16 <= rec.len() {
        let kind = u32le(rec, pos);
        if kind == ATTR_END {
            break;
        }
        let length = u32le(rec, pos + 4) as usize;
        if length < 24 || pos + length > rec.len() {
            break;
        }
        let non_resident = rec[pos + 8] != 0;
        match kind {
            ATTR_FILE_NAME => {
                // Резидентный атрибут: значение с value_off (обычно 0x18).
                // Раскладка $FILE_NAME (4 таймстампа!): 0x00 родитель,
                // 0x08/0x10/0x18/0x20 create/alter/mft/access, 0x28 alloc,
                // 0x30 real, 0x38 flags, 0x3C reparse, 0x40 длина имени,
                // 0x41 пространство имён, 0x42 имя UTF-16.
                let value_off = u16le(rec, pos + 0x14) as usize;
                let value = &rec[pos..pos + length];
                if value_off + 0x42 <= value.len() {
                    let parent = u64le(value, value_off);
                    let name_len = value[value_off + 0x40] as usize;
                    let namespace = value[value_off + 0x41];
                    let start = value_off + 0x42;
                    if start + name_len * 2 <= value.len() {
                        let utf16: Vec<u16> = (0..name_len)
                            .map(|i| u16le(value, start + i * 2))
                            .collect();
                        let name = String::from_utf16_lossy(&utf16);
                        names.push(NameInfo { parent, name, namespace });
                    }
                }
            }
            ATTR_DATA => {
                // Только безымянный $DATA (name_len == 0): именованные потоки
                // (Zone.Identifier и т.п.) размер файла задавать не должны.
                if rec[pos + 9] == 0 {
                    size = if non_resident {
                        // real (0x30) у сжатых/разрежённых атрибутов может нести
                        // мусор в старших байтах; init (0x38) - валидная длина.
                        let real = u64le(rec, pos + 0x30);
                        let init = u64le(rec, pos + 0x38);
                        if init > 0 && init <= real {
                            init
                        } else if real < (1u64 << 44) {
                            real
                        } else {
                            init
                        }
                    } else {
                        u32le(rec, pos + 0x10) as u64
                    };
                }
            }
            ATTR_STANDARD => {}
            _ => {}
        }
        pos += length;
    }
    if names.is_empty() {
        return None;
    }
    Some((names, size, is_dir))
}

fn read_exact_at(handle: &wapi::VolumeHandle, offset: u64, buf: &mut [u8]) -> bool {
    wapi::read_at(handle, offset, buf)
}

/// Полный снимок тома: пути, размеры, каталоги. drive - буква ("C").
pub fn read_volume(drive: &str, cancel: &std::sync::atomic::AtomicBool) -> Result<(Vec<MftFile>, MftStats), String> {
    let started = std::time::Instant::now();
    let handle = wapi::open_volume(drive)
        .ok_or_else(|| format!("том {drive}: не открыт (нужны права администратора)"))?;
    let mut boot = [0u8; 512];
    if !read_exact_at(&handle, 0, &mut boot) {
        return Err("boot-сектор не читается".into());
    }
    if &boot[3..8] != b"NTFS " {
        return Err("том не NTFS".into());
    }
    let bytes_per_sector = u16le(&boot, 11) as u64;
    let sectors_per_cluster = boot[13] as u64;
    let cluster = bytes_per_sector * sectors_per_cluster;
    if cluster == 0 {
        return Err("кластер нулевого размера".into());
    }
    let mft_cluster = u64le(&boot, 0x30);
    let record_field = boot[0x40] as i8;
    let record_size: u64 = if record_field < 0 {
        1u64 << (-record_field as u32)
    } else {
        record_field as u64 * cluster
    };
    if record_size < 512 || record_size as usize > 1024 * 1024 {
        return Err(format!("подозрительный размер записи MFT: {record_size}"));
    }
    let record_size = record_size as usize;

    // Запись 0 = сама MFT: берём runlist её $DATA и читаем образ целиком.
    let mut rec0 = vec![0u8; record_size];
    if !read_exact_at(&handle, mft_cluster * cluster, &mut rec0) {
        return Err("запись 0 MFT не читается".into());
    }
    let runs = mft_data_runs(&rec0).ok_or("нет $DATA у записи 0 MFT")?;
    let mut image: Vec<u8> = Vec::new();
    let mut abs: i64 = 0;
    for (delta, length) in runs {
        abs += delta;
        let mut off = abs as u64 * cluster;
        let mut left = length * cluster;
        while left > 0 {
            if cancel.load(std::sync::atomic::Ordering::Relaxed) {
                return Err("отменено".into());
            }
            let chunk = left.min(4 * 1024 * 1024) as usize;
            let mut buf = vec![0u8; chunk];
            if !read_exact_at(&handle, off, &mut buf) {
                return Err("extent MFT не читается".into());
            }
            image.extend_from_slice(&buf);
            off += chunk as u64;
            left -= chunk as u64;
        }
    }

    // Проход по записям: имена, размеры, родители.
    struct Rec {
        parent: u64,
        name: String,
        size: u64,
        is_dir: bool,
    }
    let mut recs: HashMap<u64, Rec> = HashMap::new();
    let total = image.len() / record_size;
    for idx in 0..total as u64 {
        let rec = &image[(idx as usize * record_size)..((idx as usize + 1) * record_size)];
        if let Some((names, size, is_dir)) = parse_record(rec) {
            // Win32 (1) -> Win32&DOS (3) -> POSIX (0) -> что есть: DOS (2) крайний.
            let pick = names
                .iter()
                .find(|n| n.namespace == 1)
                .or_else(|| names.iter().find(|n| n.namespace == 3))
                .or_else(|| names.iter().find(|n| n.namespace == 0))
                .unwrap_or(&names[0]);
            recs.insert(
                idx,
                Rec {
                    parent: pick.parent & 0x0000_FFFF_FFFF_FFFF,
                    name: pick.name.clone(),
                    size,
                    is_dir,
                },
            );
        }
    }

    // Склейка путей от корня (запись 5); циклы и сироты режем глубиной.
    let mut out: Vec<MftFile> = Vec::with_capacity(recs.len());
    let mut files = 0usize;
    let mut dirs = 0usize;
    let mut bytes = 0u64;
    for (idx, rec) in recs.iter() {
        if *idx <= 5 {
            continue; // служебные записи: MFT, логи, корень
        }
        let mut parts: Vec<&str> = Vec::new();
        let mut cur = rec;
        let mut guard = 0;
        let mut broken = false;
        loop {
            parts.push(cur.name.as_str());
            if cur.parent == 5 || cur.parent == *idx {
                break;
            }
            match recs.get(&cur.parent) {
                Some(next) if next.name.is_empty() => {
                    // Родитель без имени (осколок удалённой записи): путь до корня
                    // не склеить честно - обрываем цепочку здесь.
                    broken = true;
                    break;
                }
                Some(next) => {
                    cur = next;
                    guard += 1;
                    if guard > 64 {
                        broken = true;
                        break;
                    }
                }
                None => {
                    broken = true;
                    break;
                }
            }
        }
        if broken {
            continue;
        }
        let mut path = format!("{}:\\", drive.to_uppercase());
        for part in parts.iter().rev() {
            path.push_str(part);
            path.push('\\');
        }
        path.pop();
        if rec.is_dir {
            dirs += 1;
        } else {
            files += 1;
            bytes += rec.size;
        }
        out.push(MftFile { path, size: rec.size, is_dir: rec.is_dir });
    }
    let stats = MftStats {
        files,
        dirs,
        bytes,
        records: total,
        ms: started.elapsed().as_millis(),
        record_size,
        cluster,
        image_bytes: image.len(),
    };
    Ok((out, stats))
}

fn mft_data_runs(rec: &[u8]) -> Option<Vec<(i64, u64)>> {
    if rec.len() < 48 || &rec[0..4] != b"FILE" {
        return None;
    }
    let mut pos = u16le(rec, 0x14) as usize;
    while pos + 16 <= rec.len() {
        let kind = u32le(rec, pos);
        if kind == ATTR_END {
            break;
        }
        let length = u32le(rec, pos + 4) as usize;
        if length < 24 || pos + length > rec.len() {
            break;
        }
        if kind == ATTR_DATA && rec[pos + 8] != 0 {
            let runs_off = u16le(rec, pos + 0x20) as usize;
            return Some(parse_runs(&rec[pos + runs_off..pos + length]));
        }
        pos += length;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run_bytes(pairs: &[(i64, u64)]) -> Vec<u8> {
        let mut out = Vec::new();
        for (off, len) in pairs {
            let mut lb = Vec::new();
            let mut v = *len;
            while v > 0 {
                lb.push((v & 0xFF) as u8);
                v >>= 8;
            }
            // Смещение - дополненный код little-endian, режем лишние байты.
            let mut v = *off as u64;
            let mut ob = Vec::new();
            for _ in 0..8 {
                ob.push((v & 0xFF) as u8);
                v >>= 8;
            }
            while ob.len() > 1 {
                let last = ob[ob.len() - 1];
                let prev = ob[ob.len() - 2];
                let redundant = if *off >= 0 {
                    last == 0 && prev & 0x80 == 0
                } else {
                    last == 0xFF && prev & 0x80 != 0
                };
                if !redundant {
                    break;
                }
                ob.pop();
            }
            out.push(((ob.len() as u8) << 4) | lb.len() as u8);
            out.extend_from_slice(&lb);
            out.extend_from_slice(&ob);
        }
        out.push(0);
        out
    }

    #[test]
    fn runs_roundtrip() {
        let bytes = run_bytes(&[(100, 5), (3, 7), (-2, 4)]);
        assert_eq!(parse_runs(&bytes), vec![(100, 5), (3, 7), (-2, 4)]);
    }

    #[test]
    fn record_parse_name_and_size() {
        let mut rec = vec![0u8; 1024];
        rec[0..4].copy_from_slice(b"FILE");
        rec[0x16] = 0x01; // in use, file
        // Один резидентный $FILE_NAME + один резидентный $DATA.
        let name = "file.txt";
        let utf16: Vec<u16> = name.encode_utf16().collect();
        let mut value = vec![0u8; 0x42 + utf16.len() * 2];
        value[0..8].copy_from_slice(&5u64.to_le_bytes()); // parent = root
        value[0x40] = utf16.len() as u8;
        value[0x41] = 1; // Win32
        for (i, ch) in utf16.iter().enumerate() {
            value[0x42 + i * 2..0x42 + i * 2 + 2].copy_from_slice(&ch.to_le_bytes());
        }
        let value_off = 24u16;
        let attr_len = (value_off as usize + value.len() + 7) & !7;
        rec[0x14] = 0x30; rec[0x15] = 0x00; // first attr at 0x30
        let at = 0x30usize;
        rec[at..at + 4].copy_from_slice(&ATTR_FILE_NAME.to_le_bytes());
        rec[at + 4..at + 8].copy_from_slice(&(attr_len as u32).to_le_bytes());
        rec[at + 8] = 0; // resident
        rec[at + 0x10..at + 0x14].copy_from_slice(&(value.len() as u32).to_le_bytes());
        rec[at + 0x14..at + 0x16].copy_from_slice(&value_off.to_le_bytes());
        rec[at + value_off as usize..at + value_off as usize + value.len()]
            .copy_from_slice(&value);
        // $DATA resident, size 4242.
        let at2 = at + attr_len;
        let dlen = 0x48usize;
        rec[at2..at2 + 4].copy_from_slice(&ATTR_DATA.to_le_bytes());
        rec[at2 + 4..at2 + 8].copy_from_slice(&(dlen as u32).to_le_bytes());
        rec[at2 + 8] = 0;
        rec[at2 + 0x10..at2 + 0x14].copy_from_slice(&4242u32.to_le_bytes());
        rec[at2 + dlen..at2 + dlen + 4].copy_from_slice(&ATTR_END.to_le_bytes());
        let (names, size, is_dir) = parse_record(&rec).expect("record parsed");
        assert_eq!(names[0].name, "file.txt");
        assert_eq!(names[0].parent, 5);
        assert_eq!(size, 4242);
        assert!(!is_dir);
    }
}
