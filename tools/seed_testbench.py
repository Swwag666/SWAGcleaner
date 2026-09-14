"""Посев полигона реальными файлами (без синтетических заглушек).

Скрипт готовится на хосте, исполняется тоже на хосте: собирает staging-папку
D:\\vbench\\seed с двумя зонами:

  good/   — личные файлы, которые клинер НЕ ТРОГАЕТ (фото, документы):
            сюда же пишется их CRC32-манифест для сверки до/после удалений.
  junk/   — РЕАЛЬНЫЕ мусорные файлы с машины, разложенные по РЕАЛЬНЫМ папкам
            гостя (temp, Downloads-установщики, кеш Edge, логи): чтобы
            классификатор попадал по своим же паттернам, а не по выдуманным.

Каждый файл получает целевой путь внутри гостя (в виде переменных окружения,
которые раскроет seed_apply-скрипт внутри VM). Манифест seed_manifest.json
содержит: crc32, размер, mtime, целевой путь, категорию-ожидание.

Запуск на хосте:  python tools/seed_testbench.py
Копирование в VM: shared folder или VBoxManage guestcontrol (см. context.md).
Применение в VM:  python seed_apply.py D:\\seed   (тот же манифест)
"""

import argparse
import json
import os
import shutil
import sys
import time
import zlib
from pathlib import Path

STAGING = Path(r"D:\vbench\seed")
MANIFEST = STAGING / "seed_manifest.json"

# Файлы для good-зоны: ищем по всему профилю, берём первые N по каждому типу.
GOOD_PICKS = [
    ("Pictures", ("jpg", "jpeg", "png"), 12),
    ("Documents", ("docx", "xlsx", "txt", "pdf"), 10),
    ("Desktop", ("jpg", "png", "txt", "docx"), 4),
]

# Junk-зоны: (исходный глоб, шаблон, целевая папка гостя, категория-ожидание, лимит)
# installers в ядре требует Возраст ≥30 дней и ≥5 МиБ — берём только реальные
# старые инсталлеры, иначе они не станут кандидатами и тест сядет на ожидании.
JUNK_PICKS = [
    (r"%USERPROFILE%\Downloads", (".exe", ".msi"),
     r"%USERPROFILE%\Downloads", "installers", 3,
     {"min_age_days": 31, "min_size": 6 * 1024 * 1024, "sort_by_age": True}),
    # реальный temp: попадёт в temp.app (Direct, мимо корзины)
    (r"%TEMP%", None, r"%TEMP%", "temp.app", 40, {}),
    # кеш Edge: реальные файлы в реальном кеше — cache.browsers (Direct)
    (r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache\Cache_Data", None,
     r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache\Cache_Data",
     "cache.browsers", 60, {}),
    # логи: ЧИТАЕМ реальные CBS-логи (доступны на чтение), КЛАДЁМ в WER —
    # пользовательскую папку из того же паттерна категории logs (без админа
    # пишется); в %SystemRoot%\Logs гость-свинг без прав не напишет
    (r"%SystemRoot%\Logs\CBS", (".log",),
     r"%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue", "logs", 3, {}),
    # дампы: dumps (Direct)
    (r"%LOCALAPPDATA%\CrashDumps", (".dmp",), r"%LOCALAPPDATA%\CrashDumps",
     "dumps", 2, {}),
]

MIN_GOOD_SIZE = 4 * 1024          # меньше 4 КиБ неинтересно (метаданные)
MAX_GOOD_SIZE = 40 * 1024 * 1024  # одиночный good-файл до 40 МБ
MAX_TOTAL_GOOD = 400 * 1024 * 1024

# installers в ядре требует Возраст ≥30 дней и ≥5 МиБ: ставим реальным файлам
# честный возраст 400 дней (это фикстура возраста, содержимое — настоящее).
INSTALLER_AGE_DAYS = 400
INSTALLER_MIN_SIZE = 6 * 1024 * 1024
# old.large в ядре требует ≥730 дней и ≥1 ГиБ: берём реальный старый гигант
# с его НАСТОЯЩИМ mtime — если он не старше 730 дней, фикстура не пройдёт,
# и это честный факт, а не подделка.
OLDLARGE_MIN_SIZE = 1024 * 1024 * 1024
OLDLARGE_MIN_AGE_DAYS = 740


def crc32_of(path: Path) -> int:
    h = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h = zlib.crc32(chunk, h)
    return h & 0xFFFFFFFF


def expand(glob: str) -> Path:
    return Path(os.path.expandvars(glob))


def pick_good() -> list[Path]:
    """Собираем реальные личные файлы: фото/док-ты из профиля."""
    profile = Path(os.path.expandvars(r"%USERPROFILE%"))
    picked: list[Path] = []
    total = 0
    for sub, exts, limit in GOOD_PICKS:
        root = profile / sub
        if not root.is_dir():
            continue
        n = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in {"node_modules", ".git", "appdata", "onedrive"}]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower().lstrip(".") not in exts:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if not (MIN_GOOD_SIZE <= st.st_size <= MAX_GOOD_SIZE):
                    continue
                picked.append(p)
                total += st.st_size
                n += 1
                if n >= limit or total > MAX_TOTAL_GOOD:
                    break
            if n >= limit or total > MAX_TOTAL_GOOD:
                break
    return picked


def pick_junk(glob: str, exts, limit: int, opts: dict) -> list[Path]:
    root = expand(glob)
    if not root.is_dir():
        return []
    min_age = opts.get("min_age_days", 0)
    min_size = opts.get("min_size", 0)
    one_dir = opts.get("one_dir", False)
    sort_by_age = opts.get("sort_by_age", False)
    now = time.time()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            if exts and Path(name).suffix.lower() not in exts:
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size < max(min_size, 1):
                continue
            if min_age and (now - st.st_mtime) < min_age * 86400:
                continue
            out.append(p)
        if sort_by_age:
            out.sort(key=lambda p: p.stat().st_mtime)  # самые старые первыми
    return out[:limit]


def expected_categories(src: Path, cat: str) -> list[str]:
    """Честное ожидание: какие категории ядро ДОЛЖНО присвоить этому файлу
    по своим же порогам, посчитанным от реальных mtime/размера."""
    want = [cat]
    try:
        st = src.stat()
    except OSError:
        return want
    age_days = (time.time() - st.st_mtime) / 86400
    if cat == "installers" and age_days >= 730 and st.st_size >= 1024 * 1024 * 1024:
        want.append("old.large")
    return want


def stage(src: Path, zone: str, rel: Path) -> Path:
    dst = STAGING / zone / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)  # copy2 сохраняет mtime — пороги 30/730 дней честные
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="пересобрать staging с нуля")
    args = ap.parse_args()

    if args.reset and STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    t0 = time.monotonic()

    good = pick_good()
    print(f"good: {len(good)} файлов выбрано")
    for src in good:
        rel = Path(str(src).replace(":", "_"))
        dst = stage(src, "good", rel)
        records.append({
            "kind": "good",
            "staged": str(dst.relative_to(STAGING)).replace("\\", "/"),
            "guest_path": str(Path(r"%USERPROFILE%") / "SWAGbench" / "good" / rel),
            "size": dst.stat().st_size,
            "mtime": int(dst.stat().st_mtime),
            "crc32": crc32_of(dst),
        })

    junk_count = 0
    for glob, exts, target, cat, limit, opts in JUNK_PICKS:
        srcs = pick_junk(glob, exts, limit, opts)
        print(f"junk[{cat}]: {len(srcs)} файлов из {glob}")
        junk_count += len(srcs)
        taken: set[str] = set()
        for src in srcs:
            rel = Path(str(src).replace(":", "_"))
            dst = stage(src, "junk", rel)
            name = dst.name
            # разные директории-источники могут дать одно имя (temp разбросан
            # по подпапкам): в одной гостевой папке файлы перезаписали бы друг
            # друга, и сверка перестала бы быть сверкой — суффиксим коллизии
            while name.lower() in taken:
                name = name + ".bench"
            taken.add(name.lower())
            records.append({
                "kind": "junk",
                "categories": expected_categories(src, cat),
                "staged": str(dst.relative_to(STAGING)).replace("\\", "/"),
                "guest_path": str(Path(target) / name),
                "size": dst.stat().st_size,
                "mtime": int(dst.stat().st_mtime),
                "crc32": crc32_of(dst),
            })

    manifest = {
        "created_unix": int(time.time()),
        "host": os.environ.get("COMPUTERNAME", "?"),
        "counts": {"good": len(good), "junk": junk_count},
        "files": records,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    total_bytes = sum(r["size"] for r in records)
    print(f"staging: {STAGING}  файлов: {len(records)}  "
          f"объём: {total_bytes / 2**20:.1f} МБ  за {time.monotonic() - t0:.1f} с")
    print(f"манифест: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
