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
import os
import threading
import time
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

# Лёгкие задачи: короткие сетевые справочники (каталог моделей, проверка
# связи AI), вопрос Клинне и чтение описи карантина. Они не конфликтуют
# со сканами и чистками, поэтому идут РЯДОМ с тяжёлой задачей, а не ждут
# её. Гейт занятости их не касается, и их завершение не сбрасывает флаг
# занятости чужой задачи.
LIGHT_TASKS = frozenset({"ai_test", "ai_models", "ai_ask", "quarantine_list"})

# Сколько секунд результат скана чистки живёт как кеш: быстрые повторные
# нажатия «Скан» не гоняют обход дерева заново, если профиль тот же.
SCAN_CACHE_TTL = 120.0


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
    # Скан отдан из кеша (тот же профиль, ничего не менялось).
    from_cache: bool = False


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
    # Карантин: файлы перенесены в папку просмотра, а не стёрты.
    quarantined: int = 0
    quarantined_bytes: int = 0
    quarantine_dir: str = ""
    no_space: t.List[t.Dict[str, str]] = field(default_factory=list)

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
    dedupPhase = Signal(str, float)         # фаза скана дубликатов, байт пройдено
    scanPhase = Signal(str, float, float)   # фаза скана чистки, файлы, байты
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
        # Кеш повторного скана чистки: (профиль, время, результат).
        # Аннулируется любым удалением — файлы ушли, данные устарели.
        self._scan_cache: t.Optional[
            t.Tuple[t.Tuple[t.Tuple[str, ...], bool], float, ScanResult]
        ] = None
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

    # ---------- AI (этап 5: настройки в UI, модель по выбору) ----------

    def ai(self) -> t.Any:
        """Фасад AI-слоя: один на приложение, настройки из ai.json."""
        if getattr(self, "_ai", None) is None:
            from ai import AiAssistant, load_settings
            self._ai = AiAssistant(load_settings())
        return self._ai

    def ai_settings(self) -> t.Any:
        """Текущие настройки AI (для заполнения раздела настроек)."""
        return self.ai().settings

    def save_ai_settings(self, settings: t.Any) -> bool:
        """Сохранить настройки AI: файл + горячая замена в ассистенте."""
        from ai import save_settings
        ok = save_settings(settings)
        self.ai().set_settings(settings)
        self.ai().invalidate()
        try:
            self.journal().log("ai_settings", settings.provider,
                               "ok" if ok else "fail",
                               enabled=bool(settings.enabled),
                               model=settings.model)
        except Exception:  # noqa: BLE001 — журнал не должен ронять настройку
            _LOGGER.warning("строка журнала ai_settings не записалась",
                            exc_info=True)
        return ok

    def list_ai_models(self, settings: t.Any) -> t.List[str]:
        """Каталог моделей сервера; вызывается только из фонового потока."""
        from ai.provider import make_provider
        return make_provider(settings).list_models()

    def test_ai(self, settings: t.Any) -> str:
        """Проверка связи: каталог моделей или ping. Текст для человека."""
        from ai.provider import AiUnavailable, make_provider
        provider = make_provider(settings)
        try:
            names = provider.list_models(timeout_sec=5.0)
        except AiUnavailable as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 — любой сбой в человекочитаемый текст
            return str(exc)
        if names:
            return ctx().tr("settings.ai_test_ok").format(count=len(names))
        if provider.ping(timeout_sec=5.0):
            return ctx().tr("settings.ai_test_ping")
        return ctx().tr("settings.ai_test_fail")

    def test_ai_task(self, settings: t.Any) -> None:
        """Проверка связи фоном: итог taskFinished("ai_test")."""
        self._run("ai_test", lambda: {"text": self.test_ai(settings)})

    def list_ai_models_task(self, settings: t.Any) -> None:
        """Каталог моделей фоном: итог taskFinished("ai_models")."""

        def work() -> t.Dict[str, t.Any]:
            from ai.provider import AiUnavailable
            try:
                return {"names": self.list_ai_models(settings), "error": ""}
            except AiUnavailable as exc:
                return {"names": [], "error": str(exc)}

        self._run("ai_models", work)

    def ask_ai_task(self, question: str) -> None:
        """Вопрос Клинне по данным скана: фоном, мимо гейта занятости.

        Ответ собирается из того, что человек видит на экране: категории
        чистки с примерами путей, дубликаты, план советника. Генерация
        может тянуться секундами, поэтому поток — только фоновый.
        """

        def work() -> t.Dict[str, t.Any]:
            text = ""
            assistant = self.ai()
            if assistant.available():
                text = assistant.answer(self.ask_context(), question)
            return {"text": text, "question": question}

        self._run("ai_ask", work)

    def ask_context(self) -> t.Dict[str, t.Any]:
        """Снимок последних находок для ответа на вопрос.

        Клиння отвечает только по данным — без выдумок. Поэтому контекст
        это ровно то, что нашла программа: сводки категорий с парой живых
        путей (вопрос обычно «что это за файлы»), выборка групп дублей,
        план советника и итог последней чистки.
        """
        data: t.Dict[str, t.Any] = {}
        scan = self._last_scan
        if scan.summaries:
            wanted = {s.id for s in scan.summaries}
            samples: t.Dict[str, t.List[str]] = {s.id: [] for s in scan.summaries}
            for item in scan.items:
                for cat in item.categories:
                    if cat in samples and len(samples[cat]) < 3:
                        samples[cat].append(item.path)
                if all(len(v) >= 3 for v in samples.values()):
                    break
            cats = []
            for summary in scan.summaries[:40]:
                entry = {
                    "id": summary.id,
                    "files": summary.files,
                    "mb": round(summary.bytes / (1024 * 1024), 1),
                    "risk": summary.risk,
                    "lane": "trash" if summary.lane == "trash" else "direct",
                    "regrows": bool(summary.regrows),
                    "admin": bool(summary.admin),
                }
                if samples.get(summary.id):
                    entry["examples"] = samples[summary.id]
                cats.append(entry)
            data["cleaner"] = {
                "files_total": scan.files,
                "mb_total": round(scan.bytes / (1024 * 1024), 1),
                "categories": cats,
                "list_truncated": bool(scan.truncated),
            }
        if self._last_groups:
            groups = [
                {"copies": len(g.paths),
                 "mb_each": round(g.size / (1024 * 1024), 1),
                 "paths": g.paths[:4]}
                for g in self._last_groups[:12]
            ]
            wasted = sum(g.wasted() for g in self._last_groups)
            data["dupes"] = {
                "groups_total": len(self._last_groups),
                "mb_wasted": round(wasted / (1024 * 1024), 1),
                "samples": groups,
            }
        if self._last_recs:
            recs = [
                {"name": str(r.get("name", "")), "type": str(r.get("type", "")),
                 "reason": str(r.get("reason", ""))[:120]}
                for r in self._last_recs[:20]
            ]
            data["advisor_plan"] = recs
        purge = self._last_purge
        if purge is not None and not purge.dry_run:
            data["last_purge"] = {
                "removed": purge.removed,
                "freed_mb": round(purge.freed_bytes / (1024 * 1024), 1),
                "quarantined": purge.quarantined,
            }
        if not data:
            data["note"] = "сканов пока не было: данных о машине нет"
        return data

    # ---------- этап 6: зависимости и рантаймы ----------

    def redist_status_task(self) -> None:
        """Детект «уже стоит» фоном: итог taskFinished("redist_status")."""

        def work() -> t.Dict[str, t.Any]:
            from core import redists
            return {"status": redists.detect_all()}

        self._run("redist_status", work)

    def install_redists(self, rids: t.Sequence[str]) -> None:
        """Поставить выбранные зависимости winget'ом, по одной, с отменой."""

        def work() -> t.Dict[str, t.Any]:
            from core import redists
            from core.appinstall import AppInstaller
            ids = redists.winget_ids(list(rids))
            if not ids:
                return {"results": [], "ok": 0, "total": 0}
            installer = AppInstaller()

            def progress(i: int, total: int, wid: str) -> None:
                self.progressTick.emit(int((i + 1) * 100 / max(total, 1)))

            results = installer.install_many(ids, progress=progress,
                                             cancel=self._cancel)
            ok = sum(1 for r in results if r.get("ok"))
            self.journal().log("redists_install", ", ".join(ids),
                               "ok" if ok == len(results) else "fail",
                               ok=ok, total=len(results))
            return {"results": results, "ok": ok, "total": len(results)}

        self._run("redists_install", work)

    # ---------- этап 7: конфиги системы ----------

    def config_list(self) -> t.List[t.Dict[str, t.Any]]:
        from core import sysconfig
        return sysconfig.list_configs()

    def config_meta(self, name: str) -> t.Dict[str, t.Any]:
        """Полный конфиг по имени: для текста подтверждения."""
        from core import sysconfig
        return sysconfig.load(name)

    def config_save_task(self, name: str, note: str = "") -> None:
        """Снимок текущего состояния системы в конфиг, фоном."""

        def work() -> t.Dict[str, t.Any]:
            from core import redists, sysconfig
            from core.appinstall import CATALOG, AppInstaller
            from core.tweaks import TweaksEngine, current_build, load_db
            engine = TweaksEngine(store=self.backups())
            tweaks: t.List[t.Dict[str, t.Any]] = []
            for tw in load_db():
                try:
                    on = engine.status(tw) == "on"
                except Exception:  # noqa: BLE001 - твик без статуса не берём
                    on = False
                if on:
                    tweaks.append({"id": tw.id, "params": dict(tw.params or {})})
            catalog_ids = {a.winget_id for a in CATALOG}
            apps = sorted(AppInstaller().installed_ids() & catalog_ids)
            reds = [rid for rid, state in redists.detect_all().items()
                    if state is True]
            cfg = sysconfig.build_config(name, tweaks, apps, reds, note=note,
                                         build=current_build())
            path = sysconfig.save(cfg)
            self.journal().log("config_save", name, "ok",
                               tweaks=len(tweaks), apps=len(apps),
                               redists=len(reds))
            return {"name": name, "path": str(path), "tweaks": len(tweaks),
                    "apps": len(apps), "redists": len(reds)}

        self._run("config_save", work)

    def config_apply_task(self, name: str) -> None:
        """Применить конфиг поitem'но: твики со снапшотами, пакеты winget'ом."""

        def work() -> t.Dict[str, t.Any]:
            from core import redists, sysconfig
            from core.appinstall import AppInstaller
            from core.tweaks import TweaksEngine, load_db
            cfg = sysconfig.load(name)
            engine = TweaksEngine(store=self.backups())
            by_id = {tw.id: tw for tw in load_db()}

            def apply_tweak(tid: str, params: t.Dict[str, t.Any]) -> bool:
                engine.apply(by_id[tid], True, params or None)
                return True

            def install_apps(ids: t.List[str]) -> t.List[t.Dict[str, t.Any]]:
                return AppInstaller().install_many(ids, cancel=self._cancel)

            def install_reds(rids: t.List[str]) -> t.List[t.Dict[str, t.Any]]:
                return AppInstaller().install_many(
                    redists.winget_ids(rids), cancel=self._cancel)

            def progress(done: int, total: int, label: str) -> None:
                self.progressTick.emit(int(done * 100 / max(total, 1)))

            report = sysconfig.apply_config(
                cfg, apply_tweak, install_apps, install_reds,
                known_tweaks=set(by_id), progress=progress,
                cancel=self._cancel)
            self.journal().log("config_apply", name,
                               "ok" if not report["fail"] else "fail",
                               ok=report["ok"], fail=report["fail"])
            return {"name": name, **report}

        self._run("config_apply", work)

    def config_export(self, name: str, path: str) -> str:
        from core import sysconfig
        self.journal().log("config_export", name, "ok", path=path)
        return str(sysconfig.export(name, path))

    def config_import(self, path: str) -> t.Dict[str, t.Any]:
        from core import sysconfig
        cfg = sysconfig.import_config(path)
        self.journal().log("config_import", cfg["name"], "ok", path=path)
        return cfg

    def config_delete(self, name: str) -> bool:
        from core import sysconfig
        done = sysconfig.delete(name)
        self.journal().log("config_delete", name, "ok" if done else "fail")
        return done

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

    def _settle(self, name: str, _result: object) -> None:
        """Задача завершилась — окно свободно (слот главного потока).

        Лёгкие задачи занятость не снимают: она им не принадлежала.
        """
        if name in LIGHT_TASKS:
            return
        self._set_busy(False)

    def _settle_failed(self, name: str, _message: str) -> None:
        if name in LIGHT_TASKS:
            return
        self._set_busy(False)

    def _guard_progress(
        self, name: str, fn: t.Callable[[dict], None]
    ) -> t.Callable[[dict], None]:
        """Обёртка колбэка прогресса: падение становится видимой ошибкой.

        Тихая смерть колбэка (например, конвертация bytes в int для
        сигнала) раньше выглядела как «вечный скан на 0%»: исключение
        глоталось, а окно молчало. Теперь любая поломка обработки
        прогресса уходит в errorOccurred и показывается в окне.
        """
        def wrapper(event: dict) -> None:
            try:
                fn(event)
            except Exception as e:  # noqa: BLE001 — прогресс не должен молчать
                _LOGGER.exception("обработка прогресса %s упала", name)
                try:
                    self.errorOccurred.emit(
                        f"{name}: {e.__class__.__name__}: {e}")
                except Exception:  # noqa: BLE001 — последняя страховка
                    _LOGGER.error("не смогли отдать ошибку прогресса %s", name)
        return wrapper

    def _run(
        self,
        name: str,
        func: t.Callable[[], t.Any],
    ) -> None:
        """Запустить задачу в пуле.

        Тяжёлая задача требует свободного окна и выставляет флаг занятости.
        Лёгкая (см. LIGHT_TASKS) идёт рядом с любой тяжёлой: каталог моделей
        и проверка связи не трогают ни ядро, ни файлы.

        Результат и ошибка уходят сигналами taskFinished/taskFailed: они
        испускаются из рабочего потока, а Qt доставляет их в поток окна.
        """
        light = name in LIGHT_TASKS
        if not light:
            if self._busy:
                return
            self._cancel.clear()
            self._set_busy(True)

        def _done(result: t.Any) -> None:
            # Флаг занятости снимет queued-слот _settle в главном потоке.
            self.taskFinished.emit(name, result)

        def _error(message: str) -> None:
            try:
                self.errorOccurred.emit(message)
                self.taskFailed.emit(name, message)
            except Exception:  # noqa: BLE001 — ошибка не должна теряться молча
                _LOGGER.exception("не смогли отдать ошибку задачи %s", name)

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
            # Объяснение плана: правила всегда (работают офлайн и на тапке),
            # модель - только если включена и отвечает.
            from ai.rules import explain_advisor
            rules_text = explain_advisor(len(apps), recs, ctx().tr)
            ai_text = ""
            assistant = self.ai()
            if assistant.available():
                ai_text = assistant.explain({"apps": len(apps),
                                             "recs": recs})
            return {"apps": len(apps), "recs": recs,
                    "rules_text": rules_text, "ai_text": ai_text}

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
        """Скан кандидатов: стрим файлов из ядра складывается в сводки.

        Повторный скан того же профиля в течение SCAN_CACHE_TTL отдаёт
        прошлый результат мгновенно (from_cache=True): «много сканов
        подряд» не гоняет обход дерева заново, когда ничего не менялось.
        Любое удаление кеш обнуляет — файлы ушли.
        """
        meta = self.cat_meta()
        profile = (tuple(roots), bool(cat_roots))

        def work() -> ScanResult:
            cached = self._scan_cache
            if cached is not None:
                cached_profile, cached_ts, cached_result = cached
                if cached_profile == profile \
                        and (time.time() - cached_ts) < SCAN_CACHE_TTL \
                        and cached_result.summaries:
                    cached_result.from_cache = True
                    return cached_result

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
                phase = str(event.get("phase") or "")
                if phase == "walking":
                    # Обход дерева не знает общего числа файлов: проценты
                    # не растут, живость показываем счётчиком пройденного.
                    self.progressTick.emit(0)
                    self.scanPhase.emit(
                        phase,
                        float(event.get("done") or 0),
                        float(event.get("bytes") or 0))
                    return
                done, total = event.get("done"), event.get("total")
                if isinstance(done, int) and isinstance(total, int) and total > 0:
                    self.progressTick.emit(max(0, min(100, round(100 * done / total))))

            data = self._client.candidates(
                list(roots), cat_roots=cat_roots,
                on_progress=self._guard_progress("cleaner_scan", on_progress),
                on_file=on_file,
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
            self._scan_cache = (profile, time.time(), result)
            self._persist_last_scan(result)
            return result

        self._run("cleaner_scan", work)

    def _persist_last_scan(self, result: "ScanResult") -> None:
        """Сводка скана на диск: hero-карточка и превью переживают рестарт."""
        import json as _json
        import time as _time
        payload = {
            "ts": _time.time(),
            "bytes": result.bytes,
            "files": result.files,
            "cats": {s.id: s.bytes for s in result.summaries},
        }
        path = self.journal().path().parent / "last_scan.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(_json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            import os as _os
            _os.replace(tmp, path)
        except OSError:
            pass

    def last_scan_summary(self) -> t.Optional[t.Dict[str, t.Any]]:
        """Сводка прошлого скана с диска или None, если сканов не было."""
        import json as _json
        path = self.journal().path().parent / "last_scan.json"
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def total_freed(self) -> int:
        """Сколько байт освобождено за всё время по журналу purge-записей."""
        total = 0
        for entry in self.journal().tail(2000):
            if entry.get("kind") in ("purge", "purge_dupes") \
                    and entry.get("outcome") == "ok":
                total += int(entry.get("freed_bytes", 0) or 0)
        return total

    def purge_items(self, items: t.Sequence[t.Mapping[str, str]],
                    dry_run: bool,
                    quarantine: t.Optional[bool] = None) -> None:
        """Удаление (или репетиция) выбранных пунктов. Дорожки решает ядро.

        Ядро шлёт progress-события на каждый обработанный чанк: фаза
        «planned» — это оглашение плана (ещё 0%), дальше done/total растут
        по мере удаления. Прогоняем их в progressTick — полоса на странице
        ползёт, а не стоит на нуле.

        Карантин (включён в настройках, репетиция его игнорирует): файлы
        не стираются, а переезжают в папку карантина с манифестом путей.
        Не влезло или занято — файл остаётся на месте, путь приходит
        в отчёт: молчаливых потерь нет.
        """

        def on_progress(event: dict) -> None:
            if event.get("phase") == "planned":
                self.progressTick.emit(0)
                return
            done, total = event.get("done"), event.get("total")
            if isinstance(done, int) and isinstance(total, int) and total > 0:
                self.progressTick.emit(max(0, min(100, round(100 * done / total))))

        entries = [(str(it["path"]), str(it.get("category", "")))
                   for it in items]

        def work() -> PurgeReport:
            use_q = ctx().quarantineEnabled() if quarantine is None else quarantine
            if not dry_run and use_q:
                return self._quarantine_work(
                    entries, kind="cleaner",
                    on_progress=self._guard_progress("purge", on_progress))
            return self._purge_work(
                items, dry_run, self._guard_progress("purge", on_progress))

        self._run("purge", work)

    def purge_duplicates(self, groups: t.Sequence[DupGroup],
                         quarantine: t.Optional[bool] = None) -> None:
        """Удаление дублей: в каждой группе остаётся самый свежий файл.

        Выбор «кого оставить» (stat по mtime) делается здесь, в рабочем
        потоке: stat тысяч путей в главном потоке на медленном диске
        замораживал окно (найдено контрольным аудитом).

        С карантином дубли переезжают в папку просмотра группами:
        манифест батча помнит хэш группы, оставленный файл и убранные.
        """

        def work() -> PurgeReport:
            from pathlib import Path

            entries: t.List[t.Dict[str, t.Any]] = []
            for group in groups:
                if len(group.paths) < 2:
                    continue
                keep = max(
                    group.paths,
                    key=lambda p: Path(p).stat().st_mtime
                    if Path(p).exists() else 0,
                )
                entries.append({
                    "hash": group.hash,
                    "size": group.size,
                    "keep": keep,
                    "removed": [p for p in group.paths if p != keep],
                })
            use_q = ctx().quarantineEnabled() if quarantine is None else quarantine
            if use_q:
                flat = [
                    (p, "dupes.photo")
                    for e in entries for p in e["removed"]
                ]
                return self._quarantine_work(
                    flat, kind="dupes", groups=entries)
            items = [
                {"path": p, "category": "dupes.photo"}
                for e in entries for p in e["removed"]
            ]
            return self._purge_work(items, False)

        self._run("purge", work)

    def clear_quarantine(self) -> None:
        """Стереть карантин: место освобождается по-настоящему.

        Тяжёлая задача пула: папки карантина бывают большими, удаление
        рекурсивное. Итог — освобождённые байты, их покажет тост.
        """

        def work() -> int:
            from core import quarantine as qcore

            freed = qcore.clear()
            try:
                self.journal().log(
                    "quarantine", self.tr_quarantine_cleared(freed),
                    outcome="ok", freed_bytes=freed)
            except Exception:  # noqa: BLE001 — журнал не должен ронять очистку
                _LOGGER.warning("строка журнала не записана", exc_info=True)
            return freed

        self._run("quarantine_clear", work)

    def quarantine_list(self) -> None:
        """Опись карантина для окна просмотра: лёгкая, рядом со сканами.

        Чтение манифестов и подсчёт размеров — сотни записей, не тяжёлая
        работа, окно карантина не должно ждать окончания чужого скана.
        """

        def work() -> t.Dict[str, t.Any]:
            from core import quarantine as qcore

            entries = qcore.entries()
            total = sum(int(e.get("size", 0)) for e in entries)
            return {"entries": entries, "bytes": total}

        self._run("quarantine_list", work)

    def quarantine_restore(
            self, selected: t.Sequence[t.Mapping[str, t.Any]]) -> None:
        """Вернуть выбранные записи карантина на исходные места.

        Отказ не останавливает остальные: итог — список вернувшихся и
        список причин по каждой невзявшейся записи.
        """

        def work() -> t.Dict[str, t.Any]:
            from core import quarantine as qcore

            restored: t.List[t.Dict[str, t.Any]] = []
            failed: t.List[t.Dict[str, t.Any]] = []
            for entry in selected:
                if self._cancel.is_set():
                    break
                ok, reason = qcore.restore(dict(entry))
                if ok:
                    restored.append({
                        "path": str(entry.get("path", "")),
                        "size": int(entry.get("size", 0)),
                    })
                else:
                    failed.append({
                        "path": str(entry.get("path", "")),
                        "reason": reason,
                    })
            try:
                self.journal().log(
                    "quarantine_restore",
                    ctx().tr("quarantine.restore_journal").format(
                        count=len(restored)),
                    outcome="error" if failed else "ok",
                    restored=len(restored), failed=len(failed))
            except Exception:  # noqa: BLE001 — журнал не должен ронять задачу
                _LOGGER.warning("строка журнала не записана", exc_info=True)
            return {"restored": restored, "failed": failed}

        self._run("quarantine_restore", work)

    def quarantine_delete(
            self, selected: t.Sequence[t.Mapping[str, t.Any]],
            batches: t.Sequence[str] = ()) -> None:
        """Стереть выбранные записи (или батчи) карантина навсегда.

        Одиночные записи удаляются пофайлово, батчи — целиком с манифестом.
        Возвращает освобождённые байты и список отказов.
        """

        def work() -> t.Dict[str, t.Any]:
            from core import quarantine as qcore

            freed = 0
            failed: t.List[t.Dict[str, t.Any]] = []
            for entry in selected:
                if self._cancel.is_set():
                    break
                freed += int(entry.get("size", 0))
                ok, reason = qcore.delete_entry(dict(entry))
                if not ok:
                    failed.append({
                        "path": str(entry.get("path", "")),
                        "reason": reason,
                    })
            for batch in batches:
                ok, batch_freed = qcore.delete_batch(str(batch))
                if ok:
                    freed += batch_freed
                else:
                    failed.append({"path": str(batch), "reason": "батч не найден"})
            try:
                self.journal().log(
                    "quarantine_delete",
                    ctx().tr("quarantine.delete_journal").format(
                        size=human_size(freed)),
                    outcome="error" if failed else "ok",
                    freed_bytes=freed, failed=len(failed))
            except Exception:  # noqa: BLE001 — журнал не должен ронять задачу
                _LOGGER.warning("строка журнала не записана", exc_info=True)
            return {"freed": freed, "failed": failed}

        self._run("quarantine_delete", work)

    def tr_quarantine_cleared(self, freed: int) -> str:
        return ctx().tr("quarantine.cleared").format(size=human_size(freed))

    def _quarantine_work(
        self,
        entries: t.Sequence[t.Tuple[str, str]],
        kind: str,
        groups: t.Optional[t.Sequence[t.Dict[str, t.Any]]] = None,
        on_progress: t.Optional[t.Callable[[dict], None]] = None,
    ) -> PurgeReport:
        """Перенести пункты в карантин: папка просмотра вместо стирания.

        Структура путей сохраняется (C:\\Users\\x\\t.tmp -> C/Users/x/t.tmp),
        рядом лежит manifest.json с исходными путями и группами дублей.
        Место под копию проверяется заранее: не влезло — файл остаётся
        на месте и попадает в no_space отчёта, а не исчезает молча.
        """
        from core import quarantine as qcore

        batch = qcore.new_batch()
        report = PurgeReport(dry_run=False)
        report.planned = len(entries)
        report.quarantine_dir = str(batch)

        moved = 0
        moved_bytes = 0
        manifest: t.List[t.Dict[str, t.Any]] = []
        for i, (path, category) in enumerate(entries):
            if self._cancel.is_set():
                report.cancelled = True
                break
            dst, reason = qcore.stash_file(path, batch)
            if dst is None:
                report.no_space.append({"path": path, "reason": reason})
                continue
            moved += 1
            try:
                moved_bytes += os.path.getsize(dst)
            except OSError:
                pass
            manifest.append({
                "path": path,
                "category": category,
                "stashed": str(dst),
            })
            if on_progress is not None and (i % 64 == 0 or i == len(entries) - 1):
                on_progress({"phase": "quarantine", "done": i + 1,
                             "total": len(entries)})

        try:
            qcore.write_manifest(batch, kind, manifest, groups=groups)
        except Exception:  # noqa: BLE001 — манифест не должен ронять удаление
            _LOGGER.warning("манифест карантина не записан", exc_info=True)

        report.quarantined = moved
        report.quarantined_bytes = moved_bytes
        report.removed = moved
        batch_drive = qcore.drive_of(str(batch))
        freed = 0
        for entry in manifest:
            if qcore.drive_of(entry["path"]) != batch_drive:
                freed += os.path.getsize(entry["stashed"]) \
                    if os.path.exists(entry["stashed"]) else 0
        report.freed_bytes = freed
        report.lanes = {"quarantine": moved}
        report.refused = len(report.no_space)
        self._last_purge = report
        # Файлы ушли с исходных мест: кеши скана и групп больше не валидны.
        self._last_scan = ScanResult()
        self._last_groups = []
        self._scan_cache = None
        try:
            self.journal().log(
                "purge", self.describe_purge(report),
                outcome="error" if report.no_space else "ok",
                dry_run=False, planned=report.planned,
                quarantined=report.quarantined,
                quarantined_bytes=report.quarantined_bytes,
                no_space=len(report.no_space),
                quarantine_dir=report.quarantine_dir)
        except Exception:  # noqa: BLE001 — журнал не должен ронять удаление
            _LOGGER.warning("строка журнала не записана", exc_info=True)
        return report

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
            self._scan_cache = None
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

    # Экран «Дубликаты фото» ищет картинки: скан всех файлов домашней
    # папки длился бы вечно и не имел отношения к названию страницы.
    # Ядро сравнивает расширения без учёта регистра.
    PHOTO_EXTS = (
        "jpg", "jpeg", "png", "webp", "bmp", "gif",
        "tif", "tiff", "heic", "heif", "avif", "jxl",
    )

    def scan_duplicates(self, roots: t.Sequence[str],
                        exts: t.Optional[t.Sequence[str]] = None) -> None:
        """Точные дубликаты по BLAKE3: группы приходят событием dupgroups.

        Прогресс ядра идёт в progressTick, смена фазы (сбор, размер,
        быстрая сверка, полный хэш) — сигналом dedupPhase: пользователь
        видит, что скан жив, а не «вечно 0%».
        """
        exts = list(exts) if exts is not None else list(self.PHOTO_EXTS)
        phase_seen: t.List[str] = []

        def on_progress(event: dict) -> None:
            total = int(event.get("total") or 0)
            done = int(event.get("done") or 0)
            if total > 0:
                self.progressTick.emit(max(0, min(100, round(100 * done / total))))
            else:
                self.progressTick.emit(0)
            phase = str(event.get("phase") or "")
            last = phase_seen[-1] if phase_seen else ""
            # bytes уходят как float: домашняя папка легко переваливает
            # за 2^31 байт, а C++ int в сигнале ронял поток с OverflowError.
            scanned = float(event.get("bytes") or 0)
            if phase == "walk":
                # Сбор каталогов не знает общего объёма — проценты не растут.
                # Движение показываем байтами: каждый тик обновляет статус,
                # иначе долгий walk выглядит как «вечный 0%».
                if phase != last:
                    phase_seen.append(phase)
                self.dedupPhase.emit(phase, scanned)
            elif phase and phase != last:
                phase_seen.append(phase)
                self.dedupPhase.emit(phase, scanned)

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
                                           exts=exts,
                                           on_progress=self._guard_progress(
                                               "dedup", on_progress),
                                           on_groups=on_groups)
            self._last_groups = groups
            # Кеш хешей в rust: сколько файлов не перечитывались потому,
            # что размер+время файла не изменились с прошлого скана.
            try:
                cache_hits = int(data.get("cache_hits", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                cache_hits = 0
            return {"data": data, "groups": groups,
                    "cache_hits": cache_hits}

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
        Карантинный итог говорит прямо: файлы лежат в папке просмотра,
        место освободится после очистки карантина.
        """
        if report.dry_run:
            return ctx().tr("session.purge_plan").format(
                count=report.planned, size=human_size(report.planned_bytes))
        if report.quarantine_dir:
            parts = [
                ctx().tr("session.purge_quarantined").format(
                    count=report.quarantined,
                    size=human_size(report.quarantined_bytes)),
            ]
            if report.no_space:
                parts.append(ctx().tr("session.purge_no_space").format(
                    count=len(report.no_space)))
            if report.cancelled:
                parts.append(ctx().tr("session.purge_cancelled"))
            return "; ".join(parts)
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
