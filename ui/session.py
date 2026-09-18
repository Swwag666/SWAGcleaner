"""Сессия — единственное место встречи интерфейса и ядра.

Страницы ничего не знают про ядро: они испускают сигналы («пользователь
нажал скан») и умеют показывать (статус, пункты, числа, прогресс). Сессия
владеет клиентом Rust-ядра, гоняет задачи в WorkerPool, следит за занятостью
и отменой и раскладывает пришедшие данные по страницам. Реплики и настроение
помощницы заказываются уже существующими средствами контекста (ctx().say,
ctx().setAssistantMood), поэтому сессия не знает ни про один виджет окна.

Правила, которые ломать нельзя (context.md, раздел 8.4):
- ничего не удаляется без явного подтверждения: purge с dry_run=False
  вызывается только из _apply_* после положительного ответа диалога;
- способ удаления выбирает категория, а не путь (ядро само ведёт дорожки);
- ошибка одной задачи не роняет приложение: ошибка приходит сигналом,
  попадает в статус страницы и в реплику персонажа.
"""
from __future__ import annotations

import logging
import threading
import typing as t
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from core.swagscan import FileEvent, SwagscanClient, SwagscanError, get_client
from ui.context import ctx
from ui.workers import WorkerPool, WorkerTask, run_async

_LOGGER = logging.getLogger("swag.ui.session")

# Дорожки удаления, как их называет ядро.
LANE_TRASH = "trash"
LANE_DIRECT = "direct"

# Порог стрима кандидатов в память: выше него пункты не копим (и удаление
# из окна честно просит подождать экран категорий), но сводка по категориям
# полна всегда — она считается из самого стрима, а не из накопленного.
MAX_SHOWN_CANDIDATES = 200_000


def human_size(num_bytes: float) -> str:
    """Человеческий размер: 2 441.7 МБ, 1.2 ГБ, 512 КБ."""
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024.0 or unit == "ТБ":
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:,.1f} {unit}".replace(",", " ")
        value /= 1024.0
    return f"{value:,.1f} ТБ".replace(",", " ")


@dataclass
class CandidateItem:
    """Один кандидат на удаление: путь, размер и категории, куда он попал."""

    path: str
    size: int
    categories: t.List[str] = field(default_factory=list)

    def primary_category(self) -> str:
        return self.categories[0] if self.categories else "temp.app"


@dataclass
class CategorySummary:
    """Сводка по категории: сколько файлов, сколько байт, дорожка и риск."""

    id: str
    files: int
    bytes: int
    lane: str
    risk: str
    regrows: bool = False
    admin: bool = False

    def title(self, meta: t.Mapping[str, t.Any]) -> str:
        return str(meta.get(self.id, {}).get("title", self.id))


@dataclass
class ScanResult:
    """Итог скана кандидатов: сводки по категориям и (если их немного) пункты."""

    scanned: int = 0
    files: int = 0
    bytes: int = 0
    cancelled: bool = False
    summaries: t.List[CategorySummary] = field(default_factory=list)
    items: t.List[CandidateItem] = field(default_factory=list)
    truncated: bool = False


@dataclass
class DupGroup:
    """Группа точных дубликатов: размер одной копии и все её пути."""

    hash: str
    size: int
    paths: t.List[str] = field(default_factory=list)

    def wasted(self) -> int:
        return self.size * max(0, len(self.paths) - 1)


@dataclass
class PurgeReport:
    """Отчёт об удалении: что ушло, по каким дорожкам, что отказалось."""

    dry_run: bool
    planned: int = 0
    planned_bytes: int = 0
    removed: int = 0
    freed_bytes: int = 0
    refused: int = 0
    cancelled: bool = False
    lanes: t.Dict[str, int] = field(default_factory=dict)
    rejects: t.List[t.Dict[str, str]] = field(default_factory=list)
    failures: t.List[t.Dict[str, str]] = field(default_factory=list)

    @staticmethod
    def from_core(data: t.Mapping[str, t.Any], dry_run: bool) -> "PurgeReport":
        return PurgeReport(
            dry_run=dry_run,
            planned=int(data.get("planned", 0)),
            planned_bytes=int(data.get("planned_bytes", 0)),
            removed=int(data.get("removed", 0)),
            freed_bytes=int(data.get("freed_bytes", 0)),
            refused=int(data.get("refused", 0)),
            cancelled=bool(data.get("cancelled", False)),
            lanes={l["lane"]: int(l["files"]) for l in data.get("lanes", [])},
            rejects=list(data.get("rejects", [])),
            failures=list(data.get("failures", [])),
        )


class Session(QObject):
    """Живая связь окна с ядром: задачи, занятость, отмена, раскладка данных.

    Потоки: задачи идут в WorkerPool, поэтому все сигналы ниже испускаются
    из рабочего потока — Qt сам переносит их в поток окна (queued connection).
    Виджеты из рабочих потоков дёргать нельзя: это неопределённое поведение
    (найдено в VM: статус дорисовался, плитки чисел — нет).
    """

    # Сигналы для окна: занятость, прогресс, ошибки, итоги задач.
    busyChanged = Signal(bool)
    progressTick = Signal(int)              # процент текущего скана
    taskFinished = Signal(str, object)      # имя задачи, результат
    taskFailed = Signal(str, str)           # имя задачи, текст ошибки
    errorOccurred = Signal(str)             # текст ошибки (для статуса/реплики)

    def __init__(self, client: t.Optional[SwagscanClient] = None) -> None:
        super().__init__()
        self._client = client if client is not None else get_client()
        self._pool = WorkerPool()
        self._busy = False
        self._cancel = threading.Event()
        self._meta: t.Dict[str, t.Any] = {}
        self._last_scan = ScanResult()
        self._last_groups: t.List[DupGroup] = []
        self._last_purge: t.Optional[PurgeReport] = None
        # Журнал и бэкапы ленивые: папки создаются при первом действии,
        # а не при старте окна.
        self._journal: t.Any = None
        self._store: t.Any = None

    # ---------- журнал и бэкапы (правило 8.4: след на каждое действие) ----------

    def journal(self) -> t.Any:
        if self._journal is None:
            from core.journal import Journal
            self._journal = Journal()
        return self._journal

    def backups(self) -> t.Any:
        if self._store is None:
            from core.backup import BackupStore
            self._store = BackupStore.disk()
        return self._store

    # ---------- состояние ----------

    def client(self) -> SwagscanClient:
        return self._client

    def core_available(self) -> bool:
        try:
            return self._client.available()
        except SwagscanError:
            return False

    def is_busy(self) -> bool:
        return self._busy

    def cat_meta(self) -> t.Dict[str, t.Any]:
        """Каталог категорий ядра (кешируется: он не меняется на ходу)."""
        if not self._meta:
            try:
                self._meta = dict(self._client.cat_meta())
            except SwagscanError as e:
                _LOGGER.warning("cat_meta недоступен: %s", e)
        return self._meta

    def last_scan(self) -> ScanResult:
        return self._last_scan

    def last_groups(self) -> t.List[DupGroup]:
        return list(self._last_groups)

    def last_purge(self) -> t.Optional[PurgeReport]:
        return self._last_purge

    def request_cancel(self) -> None:
        """Попросить текущую задачу остановиться: флаг + cancel ядру."""
        self._cancel.set()
        try:
            self._client.cancel()
        except Exception:  # noqa: BLE001 - отмена не должна ронять окно
            _LOGGER.warning("cancel не дошёл до ядра", exc_info=True)

    # ---------- запуск задач ----------

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.busyChanged.emit(busy)

    def _run(
        self,
        name: str,
        func: t.Callable[[], t.Any],
    ) -> None:
        """Запустить задачу в пуле, если окно не занято.

        Результат и ошибка уходят сигналами taskFinished/taskFailed: они
        испускаются из рабочего потока, а Qt доставляет их в поток окна.
        """
        if self._busy:
            return
        self._cancel.clear()
        self._set_busy(True)

        def _done(result: t.Any) -> None:
            self._set_busy(False)
            self.taskFinished.emit(name, result)

        def _error(message: str) -> None:
            self._set_busy(False)
            self.errorOccurred.emit(message)
            self.taskFailed.emit(name, message)

        run_async(
            self._pool.pool,
            WorkerTask(func=func, on_done=_done, on_error=_error),
        )

    # ---------- советник ----------

    def scan_advisor(self) -> None:
        """Реальный список установленных программ + правила советника."""

        def work() -> t.Dict[str, t.Any]:
            from core.apps import WindowsInstalledProvider
            from core.advisor import (
                Advisor,
                BloatwareExplorerRule,
                KnownBloatwareRule,
                TaskPrioritizer,
            )

            provider = WindowsInstalledProvider([])
            apps = provider.get_installed_apps()
            advisor = Advisor([KnownBloatwareRule(set()), BloatwareExplorerRule()])
            recs = TaskPrioritizer().prioritize(advisor.analyze(apps))
            return {"apps": len(apps), "recs": recs}

        self._run("advisor", work)

    # ---------- чистильщик ----------

    def scan_candidates(self, roots: t.Sequence[str], cat_roots: bool) -> None:
        """Скан кандидатов: стрим файлов из ядра складывается в сводки."""
        meta = self.cat_meta()

        def work() -> ScanResult:
            collected: t.Dict[str, CandidateItem] = {}
            per_cat: t.Dict[str, t.List[int]] = {}

            def on_file(event: FileEvent) -> None:
                if self._cancel.is_set():
                    return
                item = CandidateItem(event.path, event.size, list(event.categories))
                if len(collected) < MAX_SHOWN_CANDIDATES:
                    collected[event.path] = item
                for cat in event.categories:
                    bucket = per_cat.setdefault(cat, [0, 0])
                    bucket[0] += 1
                    bucket[1] += event.size

            def on_progress(event: dict) -> None:
                done, total = event.get("done"), event.get("total")
                if isinstance(done, int) and isinstance(total, int) and total > 0:
                    self.progressTick.emit(max(0, min(100, round(100 * done / total))))

            data = self._client.candidates(
                list(roots), cat_roots=cat_roots,
                on_progress=on_progress, on_file=on_file,
            )
            summaries = [
                CategorySummary(
                    id=cat,
                    files=counts[0],
                    bytes=counts[1],
                    lane=str(meta.get(cat, {}).get("lane", LANE_TRASH)),
                    risk=str(meta.get(cat, {}).get("risk", "medium")),
                    regrows=bool(meta.get(cat, {}).get("regrows", False)),
                    admin=bool(meta.get(cat, {}).get("admin", False)),
                )
                for cat, counts in sorted(per_cat.items(),
                                          key=lambda kv: -kv[1][1])
            ]
            result = ScanResult(
                scanned=int(data.get("scanned", 0)),
                files=int(data.get("files", 0)),
                bytes=int(data.get("bytes", 0)),
                cancelled=bool(data.get("cancelled", False)),
                summaries=summaries,
                items=list(collected.values()),
                truncated=len(collected) >= MAX_SHOWN_CANDIDATES,
            )
            self._last_scan = result
            return result

        self._run("cleaner_scan", work)

    def purge_items(self, items: t.Sequence[t.Mapping[str, str]],
                    dry_run: bool) -> None:
        """Удаление (или репетиция) выбранных пунктов. Дорожки решает ядро.

        Ядро шлёт progress-события на каждый обработанный чанк: фаза
        «planned» — это оглашение плана (ещё 0%), дальше done/total растут
        по мере удаления. Прогоняем их в progressTick — полоса на странице
        ползёт, а не стоит на нуле.
        """

        def on_progress(event: dict) -> None:
            if event.get("phase") == "planned":
                self.progressTick.emit(0)
                return
            done, total = event.get("done"), event.get("total")
            if isinstance(done, int) and isinstance(total, int) and total > 0:
                self.progressTick.emit(max(0, min(100, round(100 * done / total))))

        def work() -> PurgeReport:
            data = self._client.purge(items, dry_run=dry_run,
                                      on_progress=on_progress)
            report = PurgeReport.from_core(data, dry_run)
            self._last_purge = report
            try:
                self.journal().log(
                    "purge", self.describe_purge(report),
                    outcome="error" if report.failures else "ok",
                    dry_run=report.dry_run, planned=report.planned,
                    removed=report.removed, freed_bytes=report.freed_bytes,
                    refused=report.refused, failures=len(report.failures))
            except Exception:  # noqa: BLE001 — журнал не должен ронять удаление
                _LOGGER.warning("строка журнала не записана", exc_info=True)
            return report

        self._run("purge", work)

    # ---------- твики: автозагрузка и службы (M4), бэкапы на диске (M5) ----------

    def load_tweaks(self) -> None:
        """Прочитать автозагрузку, службы и список снапшотов для страницы."""

        def work() -> t.Dict[str, t.Any]:
            from core.services import WindowsServiceController
            from core.startup import read_startup

            entries = read_startup()
            services = WindowsServiceController().list_services()
            store = self.backups()
            backups = [info for name in store.list()
                       if (info := store.info(name)) is not None]
            return {"startup": entries, "services": services,
                    "backups": backups}

        self._run("tweaks_load", work)

    def disable_startup(self, entry: t.Any) -> None:
        """Отключить запись автозагрузки: снапшот → удаление → журнал."""

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "startup_disable",
                "name": entry.name,
                "value": entry.path,
                "hive": entry.hive,
                "key_path": entry.key_path,
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "disable", "target": entry.name,
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def restore_backup(self, snapshot: str) -> None:
        """Вернуть запись автозагрузки из снапшота; снапшот после удалить."""

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "startup_restore",
                "snapshot": snapshot,
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "restore", "target": snapshot, "snapshot": ""}

        self._run("tweaks_action", work)

    # ---------- дубликаты ----------

    def scan_duplicates(self, roots: t.Sequence[str]) -> None:
        """Точные дубликаты по BLAKE3: группы приходят событием dupgroups."""

        def work() -> t.Dict[str, t.Any]:
            groups: t.List[DupGroup] = []

            def on_groups(event: dict) -> None:
                groups.extend(
                    DupGroup(
                        hash=str(g.get("hash", "")),
                        size=int(g.get("size", 0)),
                        paths=[str(p) for p in g.get("paths", [])],
                    )
                    for g in event.get("groups", [])
                )

            data = self._client.duplicates(list(roots), min_size=64 * 1024,
                                           on_groups=on_groups)
            self._last_groups = groups
            return {"data": data, "groups": groups}

        self._run("dedup", work)

    # ---------- строки для интерфейса ----------

    def describe_summary(self, summary: CategorySummary,
                         with_risk: bool = True) -> str:
        """Строка категории для показа: название, объём, дорожка, риск.

        Риск можно не дублировать: в диалоге подтверждения он и так стоит
        цветной меткой слева от строки.
        """
        meta = self.cat_meta()
        info = meta.get(summary.id, {})
        title = str(info.get("title", summary.id))
        lane = ctx().tr("session.lane_trash") if summary.lane == LANE_TRASH \
            else ctx().tr("session.lane_direct")
        text = (f"{title} — {summary.files} шт, {human_size(summary.bytes)}; "
                f"{lane}")
        if with_risk:
            text += f"; {ctx().tr(f'advisor.risk_{summary.risk}')}"
        return text

    def describe_purge(self, report: PurgeReport) -> str:
        """Итог удаления человеческими словами: только факты.

        Дорожки в отчёте ядра — план по пунктам, а не факт удаления, поэтому
        в итоговой строке их нет: план пользователь уже видел в подтверждении.
        """
        if report.dry_run:
            return ctx().tr("session.purge_plan").format(
                count=report.planned, size=human_size(report.planned_bytes))
        parts = [
            ctx().tr("session.purge_done").format(
                count=report.removed, size=human_size(report.freed_bytes)),
        ]
        if report.refused or report.rejects:
            parts.append(ctx().tr("session.purge_refused").format(count=report.refused))
        if report.failures:
            parts.append(ctx().tr("session.purge_failed").format(count=len(report.failures)))
        return "; ".join(parts)


_session: t.Optional[Session] = None
_session_lock = threading.Lock()


def get_session() -> Session:
    """Одна сессия на приложение (одно ядро = один индекс = одна сессия)."""
    global _session
    with _session_lock:
        if _session is None:
            _session = Session()
        return _session


def set_session(session: t.Optional[Session]) -> None:
    """Подменить сессию (тесты внедряют фейковое ядро через этот вход)."""
    global _session
    with _session_lock:
        _session = session


def core_present() -> bool:
    """Есть ли ядро рядом: без него окно честно говорит «ядро не найдено»."""
    try:
        return get_client().available()
    except SwagscanError:
        return False
