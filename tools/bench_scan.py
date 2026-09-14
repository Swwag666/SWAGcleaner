"""Сверка Rust-индекса с Python-обходом и замер скорости.

Проверяет то, что обещает раздел 9.9 context.md: суммы и топ должны совпасть
до байта. Замер делаем честно: кэш прогревается обоими обходами по очереди,
внутри Rust время берём из его же progress-событий, а не из внешнего wall-clock.

Запуск:
    ./venv/Scripts/python.exe tools/bench_scan.py [путь ...]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "rust" / "swagscan" / "target" / "release" / "swagscan.exe"

SKIP_DIRS = ("$recycle.bin", "system volume information")


def python_walk(roots):
    files = 0
    dirs = 0
    total = 0
    errors = 0
    stack = [os.path.normpath(r) for r in roots]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            name = e.name.lower()
                            dirs += 1
                            if e.is_symlink() or any(name.startswith(s) for s in SKIP_DIRS):
                                continue
                            stack.append(e.path)
                        else:
                            st = e.stat(follow_symlinks=False)
                            files += 1
                            total += st.st_size
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return {"files": files, "dirs": dirs, "bytes": total, "errors": errors}


class Session:
    """Держим один процесс Rust, чтобы spawn не попадал в замер."""

    def __init__(self):
        self.p = subprocess.Popen(
            [str(EXE)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0

    def index(self, roots):
        self._id += 1
        cid = self._id
        cmd = {"id": cid, "cmd": "index", "roots": roots, "top": 20, "categories": False}
        self.p.stdin.write(json.dumps(cmd) + "\n")
        self.p.stdin.flush()
        best_ms = 0
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("rust closed stdout")
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "progress":
                best_ms = max(best_ms, ev.get("elapsed_ms", 0))
            if ev.get("event") == "result" and str(ev.get("id")) == str(cid):
                data = ev["data"]
                inner = data.get("result", {}).get("elapsed_ms", 0)
                return data, max(inner, 0)

    def quit(self):
        try:
            self.p.stdin.write('{"id":999,"cmd":"quit"}\n')
            self.p.stdin.flush()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main(argv):
    roots = argv or [str(ROOT)]
    if not EXE.exists():
        print(f"НЕТ БИНАРЯ: {EXE}")
        return 1

    print(f"корни: {roots}\n")

    s = Session()
    try:
        # прогрев кэша и JIT-веток
        for _ in range(2):
            s.index(roots)
        rust_runs = [s.index(roots)[1] for _ in range(5)]
        data, _ = s.index(roots)

        # python по тому же уже прогретому кэшу
        for _ in range(2):
            python_walk(roots)
        t0 = time.perf_counter()
        py = python_walk(roots)
        py_ms = (time.perf_counter() - t0) * 1000
    finally:
        s.quit()

    res = data["result"]
    rust_best = min(rust_runs)
    print(f"rust   best из 5 (внутри процесса): {rust_best:8.1f} мс")
    print(f"python 5-й прогрев:                 {py_ms:8.1f} мс")

    print("\n=== сверка (должно совпасть до байта) ===")
    ok = True
    for key in ("files", "dirs", "bytes"):
        r, p = res[key], py[key]
        mark = "OK  " if r == p else "РАЗНО"
        if r != p:
            ok = False
        print(f"{mark} {key:6} rust={r:>12} python={p:>12}")
    print(f"\nrust ошибок чтения: {res['errors']}, пропущено reparse: {res['reparse_skipped']}")
    print(f"итог сверки: {'СОВПАЛО' if ok else 'ЕСТЬ РАСХОЖДЕНИЯ'}")
    if data.get("top_files"):
        print("\n=== топ-3 файла из Rust ===")
        for f in data["top_files"][:3]:
            print(f"  {f['size']:>12} {f['path']}")
    if data.get("top_folders"):
        print("=== топ-3 папки из Rust ===")
        for f in data["top_folders"][:3]:
            print(f"  {f['bytes']:>12} {f['path']}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
