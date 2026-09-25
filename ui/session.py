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

from PySide6.QtCore import QObject, Qt, Signal

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
MAX_SHOWN_CANDIDATES = 50_000


def human_size(num_bytes: float) -> str:
    """Человеческий размер: 2 441.7 МБ, 1.2 ГБ, 512 КБ.

    Единицы локализованы (units.*): английская локаль пишет B/KB/MB/GB/TB.
    """
    value = float(num_bytes)
    keys = ("units.b", "units.kb", "units.mb", "units.gb", "units.tb")
    for key in keys:
        unit = ctx().tr(key)
        if value < 1024.0 or key == "units.tb":
            if key == "units.b":
                return f"{int(value)} {unit}"
            return f"{value:,.1f} {unit}".replace(",", " ")
        value /= 1024.0
    return f"{value:,.1f} {ctx().tr('units.tb')}".replace(",", " ")


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
    trashed_bytes: int = 0
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
            trashed_bytes=int(data.get("trashed_bytes", 0)),
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
        self._last_recs: t.List[t.Dict[str, t.Any]] = []
        self._last_uninstallers: t.Dict[str, str] = {}
        # Журнал и бэкапы ленивые: папки создаются при первом действии,
        # а не при старте окна.
        self._journal: t.Any = None
        self._store: t.Any = None
        # Сброс занятости — ТОЛЬКО в главном потоке: воркер шлёт сигнал,
        # queued-слот щёлкает флагом. Иначе окно могло стартовать новую
        # задачу до прихода taskFinished от старой (гонка потоков).
        self.taskFinished.connect(self._settle, Qt.ConnectionType.QueuedConnection)
        self.taskFailed.connect(self._settle_failed, Qt.ConnectionType.QueuedConnection)

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

    def shutdown(self) -> None:
        """Закрытие окна: мягкая отмена, подождать пул, погасить ядро.

        Без этого swagscan.exe оставался сиротой после закрытия окна
        (найдено аудитом): процесс жил и держал stdin/stdout.
        """
        self._cancel.set()
        try:
            self._client.cancel()
        except Exception:
            pass
        try:
            self._pool.shutdown()
        except Exception:
            pass
        try:
            self._client.stop()
        except Exception:
            pass

    # ---------- запуск задач ----------

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        self.busyChanged.emit(busy)

    def _settle(self, _name: str, _result: object) -> None:
        """Задача завершилась — окно свободно (слот главного потока)."""
        self._set_busy(False)

    def _settle_failed(self, _name: str, _message: str) -> None:
        self._set_busy(False)

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
            # Флаг занятости снимет queued-слот _settle в главном потоке.
            self.taskFinished.emit(name, result)

        def _error(message: str) -> None:
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
            # План применяется позже кнопкой «Применить»: запоминаем и
            # рекомендации, и штатные деинсталляторы найденных программ.
            self._last_recs = recs
            self._last_uninstallers = {
                app.display_name: app.uninstall_string
                for app in apps if app.uninstall_string
            }
            return {"apps": len(apps), "recs": recs}

        self._run("advisor", work)

    def apply_advisor(self) -> None:
        """Применить план советника: штатные деинсталляторы для remove-реков.

        Честная семантика кнопки «Применить»: мы не переcкачиваем список
        и не притворяемся удалением — для каждой программы из плана с
        рекомендацией «удалить» запускается её собственный деинсталлятор
        (UninstallString из реестра), подтверждение внутри него.
        """

        def work() -> t.Dict[str, t.Any]:
            import shlex
            import subprocess

            launched: t.List[str] = []
            for rec in self._last_recs:
                if rec.get("type") != "remove":
                    continue
                name = str(rec.get("name", ""))
                uninstaller = self._last_uninstallers.get(name)
                if not uninstaller:
                    continue
                try:
                    # Без shell=True: строка из реестра парсится в argv и не
                    # имеет шанса развернуться в командную подстановку под
                    # админским токеном приложения.
                    argv = shlex.split(uninstaller, posix=False)
                    if not argv:
                        continue
                    argv[0] = argv[0].strip('"')
                    subprocess.Popen(argv)
                    launched.append(name)
                except (OSError, ValueError):
                    _LOGGER.warning("деинсталлятор не стартовал: %s", name)
            try:
                self.journal().log(
                    "advisor_apply",
                    ctx().tr("session.apply_launched").format(
                        count=len(launched)),
                    outcome="ok", launched=len(launched))
            except Exception:  # noqa: BLE001 — журнал не должен ронять задачу
                _LOGGER.warning("строка журнала не записана", exc_info=True)
            return {"launched": launched}

        self._run("advisor_apply", work)

    def advisor_removals(self) -> t.List[str]:
        """Имена программ плана с рекомендацией «удалить» и деинсталлятором."""
        return [
            str(rec.get("name", ""))
            for rec in self._last_recs
            if rec.get("type") == "remove"
            and str(rec.get("name", "")) in self._last_uninstallers
        ]

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
            return self._purge_work(items, dry_run, on_progress)

        self._run("purge", work)

    def purge_duplicates(self, groups: t.Sequence[DupGroup]) -> None:
        """Удаление дублей: в каждой группе остаётся самый свежий файл.

        Выбор «кого оставить» (stat по mtime) делается здесь, в рабочем
        потоке: stat тысяч путей в главном потоке на медленном диске
        замораживал окно (найдено контрольным аудитом).
        """

        def work() -> PurgeReport:
            from pathlib import Path

            items: t.List[t.Dict[str, str]] = []
            for group in groups:
                if len(group.paths) < 2:
                    continue
                keep = max(
                    group.paths,
                    key=lambda p: Path(p).stat().st_mtime
                    if Path(p).exists() else 0,
                )
                items.extend(
                    {"path": p, "category": "dupes.photo"}
                    for p in group.paths if p != keep
                )
            return self._purge_work(items, False)

        self._run("purge", work)

    def _purge_work(
        self,
        items: t.Sequence[t.Mapping[str, str]],
        dry_run: bool,
        on_progress: t.Optional[t.Callable[[dict], None]] = None,
    ) -> PurgeReport:
        """Общая часть удаления: ядро, отчёт, журнал, сброс кешей.

        После настоящего (не репетиционного) удаления кеши скана и групп
        дублей обнуляются: файлы уже ушли, и повторное удаление по старому
        списку честно отрапортует «уже нет» вместо мнимого успеха.
        """
        data = self._client.purge(items, dry_run=dry_run,
                                  on_progress=on_progress)
        report = PurgeReport.from_core(data, dry_run)
        self._last_purge = report
        if not dry_run:
            self._last_scan = ScanResult()
            self._last_groups = []
        try:
            self.journal().log(
                "purge", self.describe_purge(report),
                outcome="error" if report.failures else "ok",
                dry_run=report.dry_run, planned=report.planned,
                removed=report.removed, freed_bytes=report.freed_bytes,
                trashed_bytes=report.trashed_bytes,
                refused=report.refused, failures=len(report.failures))
        except Exception:  # noqa: BLE001 — журнал не должен ронять удаление
            _LOGGER.warning("строка журнала не записана", exc_info=True)
        return report

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
            try:
                from core.uwp import UwpController
                uwp = UwpController().list_packages()
            except Exception:  # noqa: BLE001 — AppX может не быть, странице не мешает
                _LOGGER.warning("список UWP не прочитан", exc_info=True)
                uwp = []
            sys_tweaks: t.List[t.Dict[str, t.Any]] = []
            try:
                from core.tweaks import TweaksEngine, load_db

                engine = TweaksEngine(store=store)
                for tw in engine.available(load_db()):
                    sys_tweaks.append({
                        "id": tw.id, "category": tw.category,
                        "name_en": tw.name_en, "name_ru": tw.name_ru,
                        "risk": tw.risk, "reboot": tw.reboot,
                        "explorer_restart": tw.explorer_restart,
                        "one_way": not tw.off, "params": list(tw.params),
                        "note": tw.note, "status": engine.status(tw),
                    })
            except Exception:  # noqa: BLE001 — база не обязана ломать страницу
                _LOGGER.warning("база твиков не прочитана", exc_info=True)
            return {"startup": entries, "services": services,
                    "backups": backups, "uwp": uwp, "sys_tweaks": sys_tweaks}

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
                "source": getattr(entry, "source", "registry"),
                "hive": entry.hive,
                "key_path": entry.key_path,
                "value_type": getattr(entry, "value_type", 1),
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "disable", "target": entry.name,
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def disable_service(self, name: str) -> None:
        """Отключить службу (StartMode Disabled) со снапшотом прежнего режима.

        Работающая служба не останавливается — отключение вступит после
        перезагрузки, так система не падает посреди сеанса.
        """

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "service_disable",
                "name": name,
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "service_disable", "target": name,
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def remove_uwp(self, full_name: str) -> None:
        """Удалить UWP-пакет у текущего пользователя со снапшотом манифеста."""

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "uwp_remove",
                "package": full_name,
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "uwp_remove", "target": result.target,
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def apply_tweak(self, tweak_id: str, enable: bool,
                    params: t.Optional[t.Dict[str, str]] = None) -> None:
        """Применить/выключить системный твик со снапшотом прежних значений."""

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "tweak_apply",
                "id": tweak_id,
                "enable": enable,
                "params": params or {},
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "tweak_apply", "target": tweak_id,
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def apply_preset(self, preset_id: str) -> None:
        """Применить пакет твиков: каждый со своим снапшотом, отчёт по каждому."""

        def work() -> t.Dict[str, t.Any]:
            from core.tweaks import TweaksEngine, load_db, load_presets

            engine = TweaksEngine(store=self.backups())
            presets = {p.id: p for p in load_presets()}
            preset = presets.get(preset_id)
            if preset is None:
                raise ValueError(f"пресет не найден: {preset_id}")
            results = engine.apply_preset(preset, load_db())
            ok = sum(1 for r in results if r["ok"])
            for r in results:
                self.journal().log("tweak_preset_item", r["id"],
                                   "ok" if r["ok"] else "fail",
                                   preset=preset_id,
                                   snapshot=r.get("snapshot", ""),
                                   error=r.get("error", ""))
            return {"action": "preset", "target": preset_id,
                    "preset_ok": ok, "preset_total": len(results)}

        self._run("tweaks_action", work)

    def hide_drives(self, letters: t.Sequence[str]) -> None:
        """Скрыть буквы дисков: битмаска → твик explorer.hide_drive_letters."""

        def work() -> t.Dict[str, t.Any]:
            from core.hidepart import letters_to_mask
            from core.executor import Executor

            mask = letters_to_mask(letters)
            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "tweak_apply",
                "id": "explorer.hide_drive_letters",
                "enable": True,
                "params": {"mask": str(mask)},
            }])[0]
            if not result.success:
                raise RuntimeError(result.message)
            return {"action": "tweak_apply",
                    "target": "explorer.hide_drive_letters",
                    "snapshot": result.snapshot}

        self._run("tweaks_action", work)

    def current_hidden_drives(self) -> t.List[str]:
        """Какие буквы сейчас скрыты (для диалога). Синхронно, быстро."""
        try:
            from core.hidepart import mask_to_letters
            from core.tweaks import RegistryOps
            cur = RegistryOps().get_value(
                "HKCU",
                r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
                "NoDrives")
            if cur is None:
                return []
            return mask_to_letters(int(cur[0]))
        except Exception:  # noqa: BLE001 — диалог откроется с пустым выбором
            return []

    def schedule_shutdown(self, minutes: int) -> None:
        """Поставить таймер выключения."""

        def work() -> t.Dict[str, t.Any]:
            from core.power import ShutdownTimer
            seconds = ShutdownTimer().schedule(minutes)
            self.journal().log("shutdown_timer", str(minutes))
            return {"action": "shutdown_set", "target": str(minutes),
                    "snapshot": "", "seconds": seconds}

        self._run("tweaks_action", work)

    def cancel_shutdown(self) -> None:
        """Отменить отложенное выключение."""

        def work() -> t.Dict[str, t.Any]:
            from core.power import ShutdownTimer
            ShutdownTimer().cancel()
            self.journal().log("shutdown_timer_cancel", "")
            return {"action": "shutdown_cancel", "target": "", "snapshot": ""}

        self._run("tweaks_action", work)

    def install_apps(self, winget_ids: t.Sequence[str]) -> None:
        """Поставить выбранные приложения через winget (с отменой)."""

        def work() -> t.Dict[str, t.Any]:
            from core.appinstall import AppInstaller
            installer = AppInstaller()
            if not installer.winget_available():
                raise RuntimeError("winget не найден в системе")
            results = installer.install_many(list(winget_ids),
                                             cancel=self._cancel)
            ok = sum(1 for r in results if r["ok"])
            self.journal().log("apps_install", ",".join(winget_ids),
                               extra_ok=ok, total=len(results))
            return {"action": "apps_install", "target": f"{ok}/{len(results)}",
                    "snapshot": "", "apps_results": results}

        self._run("tweaks_action", work)

    def activate(self, what: str) -> None:
        """Активация: windows (HWID) | office (Ohook) | kms:<сервер>."""

        def work() -> t.Dict[str, t.Any]:
            from core.activation import Activator
            act = Activator()
            if what == "windows":
                res = act.activate_windows()
            elif what == "office":
                res = act.activate_office()
            elif what.startswith("kms:"):
                res = act.kms_activate(what[4:])
            else:
                raise ValueError(f"неизвестный вид активации: {what}")
            self.journal().log("activation", what,
                               "ok" if res.get("ok") else "fail",
                               status=str(res.get("status")))
            return {"action": "activation",
                    "target": f"{what}:{res.get('status', '?')}",
                    "snapshot": "", "activation": res}

        self._run("tweaks_action", work)

    def install_gpedit(self) -> None:
        """Доустановить редактор групповых политик на Home-редакции."""

        def work() -> t.Dict[str, t.Any]:
            from core.gpedit import install_gpedit
            res = install_gpedit()
            self.journal().log("gpedit", f"{res['ok']}/{res['total']}",
                               "ok" if not res["failed"] else "fail")
            return {"action": "gpedit",
                    "target": f"{res['ok']}/{res['total']}",
                    "snapshot": "", "gpedit": res}

        self._run("tweaks_action", work)

    def restore_backup(self, snapshot: str) -> None:
        """Вернуть из снапшота: запись реестра, файл папки, службу или UWP.

        Маршрут отката выбирается по данным снапшота (kind), так что одна
        кнопка «Вернуть» на странице твиков покрывает все виды действий.
        """

        def work() -> t.Dict[str, t.Any]:
            from core.executor import Executor

            executor = Executor(store=self.backups(), journal=self.journal())
            result = executor.execute_actions([{
                "type": "backup_restore",
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
        if report.trashed_bytes:
            parts.append(ctx().tr("session.purge_trashed").format(
                size=human_size(report.trashed_bytes)))
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
