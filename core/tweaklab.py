"""Полигон твиков: автономный прогон всей базы на живой системе.

Для каждого твика: статус до → применить → статус после → откат по снапшоту
→ статус после отката. Результат - JSON-отчёт по каждому твику. Думан на
виртуалку: если системе прилетит - откатываем снапшот машины, а не реестра.

Медленные односторонние действия (SFC, DISM, compact, WinSxS) - отдельным
флагом: по умолчанию полигон прогоняет только быстрые обратимые твики.
"""
from __future__ import annotations

import json
import time
import typing as t
from pathlib import Path

# Долгие операции: включаются только с include_slow=True.
SLOW_IDS = frozenset({
    "sysrec.sfc_scannow",
    "sysrec.dism_restorehealth",
    "sysrec.compact_os",
    "components.winsxs_cleanup",
    "components.netfx3",
    "components.directplay",
})


def run_lab(report_path: t.Union[str, Path],
            include_slow: bool = False,
            engine: t.Optional[t.Any] = None,
            store: t.Optional[t.Any] = None,
            log: t.Optional[t.Callable[[str], None]] = print) -> t.Dict[str, t.Any]:
    """Прогнать все доступные твики: apply → status → restore → status."""
    from core.backup import BackupStore
    from core.tweaks import TweaksEngine, load_db

    if store is None:
        store = BackupStore(Path(tempdir_safe()) / "swag-tweaklab")
    if engine is None:
        # Медленный режим: sfc/dism/compact могут жевать по полчаса.
        engine = TweaksEngine(store=store,
                              cmd_timeout=3600 if include_slow else 300)

    tweaks = engine.available(load_db())
    results: t.List[t.Dict[str, t.Any]] = []
    started = time.time()

    for i, tw in enumerate(tweaks):
        entry: t.Dict[str, t.Any] = {
            "id": tw.id, "category": tw.category, "risk": tw.risk,
            "one_way": not tw.off,
        }
        if tw.id in SLOW_IDS and not include_slow:
            entry["verdict"] = "skipped_slow"
            results.append(entry)
            if log:
                log(f"[{i + 1}/{len(tweaks)}] {tw.id}: пропуск (медленный)")
            continue
        if log:
            log(f"[{i + 1}/{len(tweaks)}] {tw.id}...")
        try:
            before = engine.status(tw)
            entry["status_before"] = before
            snap = engine.apply(tw, True)
            entry["apply"] = "ok"
            entry["snapshot"] = snap
            after = engine.status(tw)
            entry["status_after"] = after
            engine.restore(snap)
            entry["restored"] = True
            entry["status_restored"] = engine.status(tw)
            # Вердикт: для тумблеров ждём on после apply и возврат статуса;
            # для односторонних/cmd-твиков честный критерий - сам факт
            # успешного выполнения операций и чистого отката. Параметризованные
            # твики (маска букв, цвета) статусом не судим: эталона нет.
            if tw.params or not tw.off:
                ok = True
            else:
                ok = after in ("on", "unknown") and entry["status_restored"] in (
                    before, "unknown")
            entry["verdict"] = "ok" if ok else "state_mismatch"
        except Exception as exc:  # noqa: BLE001 — отчёт должен доехать весь
            entry["apply"] = "fail"
            entry["error"] = str(exc)
            entry["verdict"] = "fail"
        results.append(entry)

    done = sum(1 for r in results if r["verdict"] == "ok")
    failed = [r["id"] for r in results if r["verdict"] == "fail"]
    mismatch = [r["id"] for r in results if r["verdict"] == "state_mismatch"]
    skipped = [r["id"] for r in results if r["verdict"] == "skipped_slow"]
    report = {
        "started": started,
        "duration_sec": round(time.time() - started, 1),
        "total": len(results), "ok": done, "failed": failed,
        "state_mismatch": mismatch, "skipped_slow": skipped,
        "results": results,
    }
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
    if log:
        log(f"полигон: {done}/{len(results)} ok, "
            f"fail={len(failed)}, mismatch={len(mismatch)}, "
            f"пропущено медленных={len(skipped)}")
    return report


def tempdir_safe() -> str:
    import tempfile
    return tempfile.gettempdir()
