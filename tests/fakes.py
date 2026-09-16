"""Фейки для тестов UI: сессия с «ядром», которое не трогает диск.

Окно принимает сессию параметром конструктора, поэтому тесты внедряют
FakeCoreSession: задачи не уходят в потоки и не вызывают Rust-бинарь,
а завершаются по явному вызову finish_* — так тест детерминированно
видит и «идёт работа», и «работа закончилась». Контракт повторяет
ui.session.Session: итоги уходят сигналом taskFinished.
"""
from __future__ import annotations

import typing as t

from PySide6.QtCore import QObject, Signal

from ui.session import (
    CategorySummary,
    PurgeReport,
    ScanResult,
    Session,
)


class FakeCoreSession(QObject):
    """Повторяет публичный контракт ui.session.Session без ядра и потоков."""

    busyChanged = Signal(bool)
    progressTick = Signal(int)
    taskFinished = Signal(str, object)
    taskFailed = Signal(str, str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        scan: t.Optional[ScanResult] = None,
        advisor: t.Optional[dict] = None,
        groups: t.Optional[list] = None,
        error: t.Optional[str] = None,
    ) -> None:
        super().__init__()
        self._busy = False
        self._scan = scan if scan is not None else ScanResult()
        self._advisor = advisor if advisor is not None else {"apps": 7, "recs": []}
        self._groups = groups if groups is not None else []
        self._error = error
        self._pending: t.List[str] = []
        self.purge_calls: t.List[t.Tuple[t.Any, bool]] = []
        self.cancel_requests = 0

    # ---------- контракт сессии ----------

    def is_busy(self) -> bool:
        return self._busy

    def core_available(self) -> bool:
        return True

    def cat_meta(self) -> t.Dict[str, t.Any]:
        return {
            "temp.app": {"title": "Temporary files of apps", "lane": "direct",
                         "risk": "low", "regrows": True, "admin": False},
            "installers": {"title": "Old installers in Downloads", "lane": "trash",
                           "risk": "medium", "regrows": False, "admin": False},
        }

    def last_scan(self) -> ScanResult:
        return self._scan

    def last_groups(self) -> list:
        return list(self._groups)

    def last_purge(self) -> t.Optional[PurgeReport]:
        return None

    def request_cancel(self) -> None:
        self.cancel_requests += 1

    def describe_summary(self, summary: CategorySummary,
                         with_risk: bool = True) -> str:
        lane = "в корзину" if summary.lane == "trash" else "без корзины"
        text = f"{summary.id} — {summary.files} шт; {lane}"
        if with_risk:
            text += f"; {summary.risk}"
        return text

    def describe_purge(self, report: PurgeReport) -> str:
        return f"Удалено {report.removed}, освобождено {report.freed_bytes} Б"

    # ---------- задачи: стартуют «занято», завершаются вручную ----------

    def _start(self, name: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.busyChanged.emit(True)
        if self._error is not None:
            self._busy = False
            self.busyChanged.emit(False)
            self.errorOccurred.emit(self._error)
            self.taskFailed.emit(name, self._error)
            return
        self._pending.append(name)

    def scan_advisor(self) -> None:
        self._start("advisor")

    def scan_candidates(self, roots, cat_roots) -> None:
        self._start("cleaner_scan")

    def scan_duplicates(self, roots) -> None:
        self._start("dedup")

    def purge_items(self, items, dry_run) -> None:
        self.purge_calls.append((list(items), dry_run))
        self._start("purge")

    # ---------- ручное завершение ----------

    def finish_advisor(self, result: t.Optional[dict] = None) -> None:
        self._finish("advisor", result if result is not None else self._advisor)

    def finish_candidates(self, scan: t.Optional[ScanResult] = None) -> None:
        result = scan if scan is not None else self._scan
        self._scan = result
        self._finish("cleaner_scan", result)

    def finish_duplicates(self, payload: t.Optional[dict] = None) -> None:
        self._finish("dedup", payload if payload is not None
                     else {"data": {}, "groups": self._groups})

    def finish_purge(self, report: t.Optional[PurgeReport] = None) -> None:
        self._finish("purge", report if report is not None
                     else PurgeReport(dry_run=False, removed=3, freed_bytes=1024))

    def _finish(self, name: str, result: t.Any) -> None:
        if name not in self._pending:
            return
        self._pending.remove(name)
        self._busy = False
        self.busyChanged.emit(False)
        self.taskFinished.emit(name, result)

    def raise_error(self, message: str) -> None:
        """Как настоящая сессия: ошибка снимает занятость и зовёт окно."""
        if self._busy:
            self._busy = False
            self.busyChanged.emit(False)
        self.errorOccurred.emit(message)


def make_scan(files: int = 1284, size: int = 2412 * 1024 * 1024) -> ScanResult:
    """Скан с двумя категориями: безвозвратная и корзинная, с пунктами."""
    from ui.session import CandidateItem

    return ScanResult(
        scanned=9000,
        files=files,
        bytes=size,
        summaries=[
            CategorySummary(id="temp.app", files=files - 4, bytes=size // 2,
                            lane="direct", risk="low", regrows=True),
            CategorySummary(id="installers", files=4, bytes=size // 2,
                            lane="trash", risk="medium"),
        ],
        items=[
            CandidateItem(path=r"C:\Users\one\AppData\Local\Temp\a.tmp",
                          size=1024, categories=["temp.app"]),
            CandidateItem(path=r"C:\Users\one\AppData\Local\Temp\b.tmp",
                          size=2048, categories=["temp.app"]),
            CandidateItem(path=r"C:\Users\one\Downloads\old_setup.msi",
                          size=9 * 1024 * 1024, categories=["installers"]),
        ],
    )


__all__ = ["FakeCoreSession", "make_scan", "Session"]
