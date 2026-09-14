"""Сверка манифеста до/после операции клинера внутри виртуалки.

Три режима:

  verify_manifest.py before <applied.json> <out-before.json>
      снять фактическое состояние всех файлов манифеста (good + junk) — «до».

  verify_manifest.py after <out-before.json> <out-after.json>
      снять состояние ещё раз — «после».

  verify_manifest.py check <out-before.json> <out-after.json>
      вердикт, и.exit code 0 если всё честно:
        • каждый good-файл жив, размер и crc32 совпадают байт-в-байт;
        • каждый junk-файл либо отсутствует (удалён), либо жив (если удаляли
          только отмеченное — тогда отсутствие считается по списку targets);
        • ни одного «пропал без удаления» и ни одного «изменился» — это ЧП.

С опцией --expect-deleted filelist.txt проверяет, что удалились ровно файлы
из списка (список строит CLI-обвязка полигона из результата purge).

Запуск в госте: python verify_manifest.py ...
"""

import json
import sys
import zlib
from pathlib import Path


def crc32_of(path: Path) -> int:
    h = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h = zlib.crc32(chunk, h)
    return h & 0xFFFFFFFF


def snapshot(ref_path: Path, out_path: Path) -> int:
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    state = {}
    missing = 0
    for rec in ref["files"]:
        p = Path(rec["path"])
        if p.is_file():
            st = p.stat()
            state[rec["path"]] = {
                "kind": rec["kind"],
                "categories": rec.get("categories") or [],
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "crc32": crc32_of(p),
            }
        else:
            state[rec["path"]] = None
            missing += 1
    out_path.write_text(json.dumps({"files": state}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"снимок {out_path}: файлов {len(state)}, отсутствует {missing}")
    return 0


def check(before_path: Path, after_path: Path, expect_file: Path | None) -> int:
    before = json.loads(before_path.read_text(encoding="utf-8"))["files"]
    after = json.loads(after_path.read_text(encoding="utf-8"))["files"]
    expect = None
    if expect_file:
        expect = {l.strip().replace("\\", "/").lower()
                  for l in expect_file.read_text(encoding="utf-8").splitlines()
                  if l.strip()}

    problems: list[str] = []
    deleted: list[str] = []
    survived: list[str] = []
    good_ok = 0

    for path, b in before.items():
        a = after.get(path)
        kind = (b or a or {}).get("kind", "?")
        if kind == "good":
            if a is None:
                problems.append(f"good-файл ПРОПАЛ: {path}")
            elif a["crc32"] != b["crc32"] or a["size"] != b["size"]:
                problems.append(f"good-файл ИЗМЕНИЛСЯ: {path}")
            else:
                good_ok += 1
        elif kind == "junk":
            if b is not None and a is None:
                deleted.append(path)
            elif a is not None:
                survived.append(path)

    if expect is not None:
        norm_del = {d.replace("\\", "/").lower() for d in deleted}
        missed = expect - norm_del
        extra = norm_del - expect
        for m in sorted(missed):
            problems.append(f"не удалился ожидаемый junk: {m}")
        for x in sorted(extra):
            problems.append(f"удалён НЕожиданный файл: {x}")

    print(f"good целых: {good_ok}/{sum(1 for v in before.values() if v and v['kind']=='good')}")
    print(f"junk удалено: {len(deleted)}, выжило: {len(survived)}")
    if survived and expect is None:
        print("выжившие junk (удаляли не всё — норма при выборочном удалении):")
        for s in survived[:10]:
            print(f"  {s}")
    if problems:
        print("ПРОВАЛ СВЕРКИ:")
        for p in problems:
            print(f"  !! {p}")
        return 1
    print("СВЕРКА ЧИСТАЯ")
    return 0


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    mode = sys.argv[1]
    args = [Path(a) for a in sys.argv[2:]]
    if mode == "before":
        return snapshot(args[0], args[1])
    if mode == "after":
        ref = json.loads(args[0].read_text(encoding="utf-8"))
        fake = args[0].parent / "_ref_files.json"
        recs = ref["files"]
        if isinstance(recs, dict):  # снимок before.json: путь -> состояние
            items = [{"path": p, "kind": v["kind"], "categories": v.get("categories") or []}
                     for p, v in recs.items() if v]
        else:  # applied.json: список записей посева
            items = [{"path": r["path"], "kind": r["kind"], "categories": r.get("categories") or []}
                     for r in recs]
        fake.write_text(json.dumps({"files": items}), encoding="utf-8")
        return snapshot(fake, args[1])
    if mode == "check":
        expect = args[2] if len(args) > 2 else None
        return check(args[0], args[1], expect)
    print(f"неизвестный режим: {mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
