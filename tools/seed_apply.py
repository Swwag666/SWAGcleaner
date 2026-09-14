"""Применение staging-посева внутри виртуалки (запускать в госте).

Читает seed_manifest.json, собранный tools/seed_testbench.py на хосте, и
раскладывает файлы по НАСТОЯЩИМ путям гостя:

  good → %USERPROFILE%\\SWAGbench\\good\\...   (личные файлы, трогать нельзя)
  junk → реальные мусорные места (%TEMP%, Downloads, кеш Edge, CrashDumps)
         с сохранением mtime, чтобы пороги возраста (30/730 дней) играли честно.

Одновременно пишет applied.json — фактическое состояние на диске (путь, размер,
crc32), чтобы verify_manifest.py сверял «до» и «после» без пересчёта вручную.

Запуск в госте:  python seed_apply.py D:\\seed
"""

import hashlib
import json
import os
import shutil
import sys
import zlib
from pathlib import Path


def crc32_of(path: Path) -> int:
    h = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h = zlib.crc32(chunk, h)
    return h & 0xFFFFFFFF


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: python seed_apply.py <staging-папка>")
        return 2
    staging = Path(sys.argv[1])
    manifest_path = staging / "seed_manifest.json"
    if not manifest_path.is_file():
        print(f"нет манифеста: {manifest_path}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    applied = []
    copied = skipped = failed = collided = 0
    for rec in manifest["files"]:
        src = staging / rec["staged"]
        dst = Path(os.path.expandvars(rec["guest_path"]))
        if not src.is_file():
            print(f"НЕТ staged-файла: {src}")
            failed += 1
            continue
        # Коллизия: в госте уже есть файл с таким именем, но не из нашего
        # посева (размер или mtime не совпадают) — не перезаписываем чужое,
        # иначе тест удалит системный файл. Кладём рядом с суффиксом .bench.
        if dst.exists():
            try:
                dst_st = dst.stat()
            except OSError:
                dst_st = None
            if dst_st is not None and not (dst_st.st_size == rec["size"]
                                           and int(dst_st.st_mtime) == rec["mtime"]):
                alt = dst.with_name(dst.name + ".bench")
                print(f"коллизия: {dst} не наш — кладу {alt.name}")
                dst = alt
                collided += 1
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and dst.stat().st_size == rec["size"] \
                    and int(dst.stat().st_mtime) == rec["mtime"]:
                skipped += 1
            else:
                shutil.copy2(src, dst)  # mtime сохраняется
                copied += 1
            st = dst.stat()
            applied.append({
                "kind": rec["kind"],
                "categories": rec.get("categories") or [],
                "path": str(dst),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "crc32": crc32_of(dst),
                "expected_crc32": rec["crc32"],
            })
        except OSError as e:
            print(f"не положил {dst}: {e}")
            failed += 1

    out = staging / "applied.json"
    out.write_text(json.dumps({"files": applied}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    good = sum(1 for a in applied if a["kind"] == "good")
    junk = sum(1 for a in applied if a["kind"] == "junk")
    print(f"скопировано {copied}, уже было {skipped}, коллизий {collided}, сбоев {failed}")
    print(f"good на диске: {good}, junk на диске: {junk}")
    print(f"фактическое состояние: {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
