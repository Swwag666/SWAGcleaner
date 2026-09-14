"""Прогон клинера внутри виртуалки (запускать в госте, embedded Python).

Полный цикл проверки на «похожей на настоящую» системе:

  1. читает applied.json (что реально посеяно seed_apply.py);
  2. сканирует cat_roots через swagscan.exe (SWAGSCAN_BIN в окружении);
  3. сверяет: каждый seeded-junk найден кандидатом, good-файлы — НЕ кандидаты;
  4. dry-run purge по найденному junk (план, ничего не трогает);
  5. реальный purge (только дорожка Trash и Direct по категории);
  6. снимает состояние после и печатает вердикт: junk исчез, good целы.

Ничего не подтверждать интерактивом нельзя — здесь это автотест полигона,
не пользовательский путь; удаление идёт только по заранее посеянным файлам.

Запуск в госте:
  set SWAGSCAN_BIN=D:\\tools\\swagscan.exe
  python run_vm_test.py D:\\seed [--purge]

Без --purge — только анализ и dry-run (безопасная репетиция).
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.swagscan import SwagscanClient  # noqa: E402


def norm(p: str) -> str:
    return p.replace("\\", "/").lower()


def load_applied(seed_dir: Path):
    data = json.loads((seed_dir / "applied.json").read_text(encoding="utf-8"))
    good, junk = {}, {}
    for rec in data["files"]:
        (good if rec["kind"] == "good" else junk)[norm(rec["path"])] = rec
    return good, junk


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: python run_vm_test.py <seed-папка> [--purge]")
        return 2
    seed_dir = Path(sys.argv[1])
    do_purge = "--purge" in sys.argv

    good, junk = load_applied(seed_dir)
    print(f"посеяно: good {len(good)}, junk {len(junk)}")

    found: dict[str, list[str]] = {}
    total_bytes = 0
    with SwagscanClient() as c:
        meta = c.cat_meta()

        def on_file(f):
            nonlocal total_bytes
            k = norm(f.path)
            found.setdefault(k, []).extend(f.categories)
            total_bytes += f.size

        result = c.candidates([], cat_roots=True, on_file=on_file)
        print(f"скан: просмотрено {result['scanned']}, "
              f"кандидатов {result['files']}, {result['bytes']/2**20:.1f} МБ")

        # --- сверка 1: seeded junk обязан найтись в ожидаемой категории ---
        missing = []
        wrong_cat = []
        for path, rec in junk.items():
            cats = found.get(path)
            want = rec.get("categories") or []
            if cats is None:
                missing.append(path)
            elif want and not all(c in cats for c in want):
                wrong_cat.append((path, want, cats))

        # --- сверка 2: good файлы НЕ должны быть кандидатами ---
        good_hits = [p for p in good if p in found]

        print(f"\njunk найдено как кандидат: {len(junk) - len(missing)}/{len(junk)}")
        for p in missing[:15]:
            print(f"  НЕ НАЙДЕН: {p}")
        for p, want, got in wrong_cat[:15]:
            print(f"  НЕ ТА КАТЕГОРИЯ: {p} ждали {want}, ядро говорит {got}")
        print(f"good ошибочно в кандидатах: {len(good_hits)}")
        for p in good_hits[:15]:
            print(f"  ЛОЖНЫЙ МУСОР: {p}")

        if missing or wrong_cat or good_hits:
            print("\nВЕРДИКТ: сверка категорий ПРОВАЛЕНА — фиксить ядро, не удалять")
            return 1

        if not do_purge:
            plan = c.purge([{"path": r["path"], "category": (r.get("categories") or ["temp.app"])[0]}
                            for r in junk.values()], dry_run=True)
            print(f"\n[репетиция] dry-run план: {json.dumps(plan, ensure_ascii=False)[:400]}")
            print("реальный purge не запускался (--purge не указан)")
            return 0

        # --- реальное удаление по дорожкам ---
        items = [{"path": r["path"], "category": (r.get("categories") or ["temp.app"])[0]}
                 for r in junk.values()]
        out = c.purge(items, dry_run=False)
        lanes = {l["lane"]: l["files"] for l in out.get("lanes", [])}
        print(f"\npurge: удалено {out.get('removed')}, "
              f"освобождено {out.get('freed_bytes', 0)/2**20:.1f} МБ, "
              f"дорожки {lanes}, отказов {out.get('refused')}, "
              f"ошибок {len(out.get('failures', []))}")
        for rej in out.get("rejects", [])[:10]:
            print(f"  отказ: {rej['path']} — {rej['reason']}")
        for f in out.get("failures", [])[:10]:
            print(f"  ошибка: {f['path']} — {f['error']}")

    # --- сверка 3: junk исчез, good целы ---
    gone = alive = changed = 0
    for path, rec in junk.items():
        p = Path(os.path.expandvars(rec["path"]))
        if p.exists():
            alive += 1
        else:
            gone += 1
    good_broken = 0
    for path, rec in good.items():
        p = Path(os.path.expandvars(rec["path"]))
        if not p.exists():
            good_broken += 1
            print(f"  !! good ПРОПАЛ: {p}")
    print(f"\nпосле purge: junk удалён {gone}/{len(junk)}, выжил {alive}, "
          f"good повреждено {good_broken}/{len(good)}")
    if alive:
        for path, rec in junk.items():
            if Path(os.path.expandvars(rec["path"])).exists():
                print(f"  выжил: {rec['path']}")
    ok = gone == len(junk) and good_broken == 0
    print("\nВЕРДИКТ:", "ПОЛИГОН ЧИСТ" if ok else "ЕСТЬ ОТСТУПЛЕНИЯ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
