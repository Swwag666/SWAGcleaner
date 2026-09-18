"""Системные smoke-тесты ядра SWAGcleaner.

Проверяют, что ядро действительно работает: модели, сканер, советник,
чистка (в подменённую корзину) и поиск дубликатов.

Ничего в системе не меняется — файлы живут во временной папке pytest,
а корзина заменена фейковым провайдером, который только запоминает пути.
"""
from __future__ import annotations

import typing as t

import pytest
from PIL import Image

from core.advisor import (
    Advisor,
    BloatwareExplorerRule,
    KnownBloatwareRule,
    TaskPrioritizer,
)
from core.apps import KNOWN_APPS, WindowsInstalledProvider
from core.cleaner import Cleaner
from core.dedup import DuplicateScanner
from core.models import AppInfo, Plan
from core.scanner import SystemScanner


class FakeTrashProvider:
    """Подменяет корзину: только запоминает пути, файлы не удаляет."""

    def __init__(self) -> None:
        self.sent: t.List[str] = []

    def send_to_trash(self, path: str) -> None:
        self.sent.append(path)


class TestModels:
    def test_app_info_roundtrip(self) -> None:
        info = AppInfo(
            display_name="Adobe Acrobat",
            install_location=r"C:\Program Files\Adobe\Acrobat",
            publisher="Adobe",
        )
        assert info.display_name == "Adobe Acrobat"
        assert info.install_root == r"C:\Program Files\Adobe\Acrobat"

    def test_app_info_without_location(self) -> None:
        assert AppInfo(display_name="X").install_root is None

    def test_plan_snapshot_none(self) -> None:
        assert Plan(snapshot=None).snapshot is None

    def test_plan_snapshot_dict_is_preserved(self) -> None:
        plan = Plan(snapshot={"apps": ["foo.exe"]})
        assert plan.snapshot == {"apps": ["foo.exe"]}

    def test_plan_snapshot_non_dict_is_wrapped(self) -> None:
        plan = Plan(snapshot=["foo.exe"])
        assert isinstance(plan.snapshot, dict)
        assert plan.snapshot["payload"] == ["foo.exe"]


class TestScanner:
    def test_scan_installed_apps(self, fake_installed_provider: t.Any) -> None:
        apps = SystemScanner(fake_installed_provider).scan_installed_apps()
        assert [a.display_name for a in apps] == [
            "Adobe Acrobat",
            "Some Bloatware App",
        ]

    def test_scan_installed_apps_skip_wow64(self, fake_installed_provider: t.Any) -> None:
        apps = SystemScanner(fake_installed_provider).scan_installed_apps(skip_wow64=True)
        assert apps == []

    def test_scan_processes_returns_rows(self, fake_installed_provider: t.Any) -> None:
        rows = SystemScanner(fake_installed_provider).scan_processes()
        assert rows
        assert all({"pid", "name"} <= set(row) for row in rows)

    def test_scan_services_and_startup(self, fake_installed_provider: t.Any) -> None:
        scanner = SystemScanner(fake_installed_provider)
        assert scanner.scan_services()
        assert scanner.scan_startup()


class TestAdvisor:
    def test_known_bloatware_rule_flags_by_publisher(self) -> None:
        apps = [AppInfo(display_name="Some Bloatware App", publisher="Bloatware Corp")]
        recs = Advisor([KnownBloatwareRule({"Bloatware Corp"})]).analyze(apps)
        assert len(recs) == 1
        assert recs[0]["type"] == "remove"
        assert recs[0]["name"] == "Some Bloatware App"
        assert recs[0]["risk"] == "medium"

    def test_known_bloatware_rule_ignores_clean_app(self) -> None:
        apps = [AppInfo(display_name="WinRAR", publisher="WinRAR")]
        assert Advisor([KnownBloatwareRule({"Bloatware Corp"})]).analyze(apps) == []

    def test_explorer_rule_suggests_check(self) -> None:
        apps = [AppInfo(display_name="HP Bloatware Helper")]
        recs = Advisor([BloatwareExplorerRule()]).analyze(apps)
        assert len(recs) == 1
        assert recs[0]["type"] == "explore"
        assert recs[0]["risk"] == "low"

    def test_prioritizer_sorts_by_weight(self) -> None:
        tasks = [
            {"name": "low", "weight": 0.5},
            {"name": "high", "weight": 1.2},
        ]
        ordered = TaskPrioritizer().prioritize(tasks)
        assert [task["name"] for task in ordered] == ["high", "low"]


class TestInstalledProvider:
    def test_known_apps_shape(self) -> None:
        assert KNOWN_APPS
        assert all(a.get("display_name") for a in KNOWN_APPS)

    def test_get_installed_apps_never_crashes(self) -> None:
        # На Windows читает реальный реестр, на других ОС возвращает список.
        apps = WindowsInstalledProvider(KNOWN_APPS).get_installed_apps()
        assert isinstance(apps, list)

    def test_get_installed_apps_skip_wow64(self) -> None:
        apps = WindowsInstalledProvider(KNOWN_APPS).get_installed_apps(skip_wow64=True)
        assert isinstance(apps, list)


class TestCleaner:
    def test_scan_finds_files_in_temp_dir(self, tmp_path: t.Any) -> None:
        (tmp_path / "a.tmp").write_text("x" * 10, encoding="utf-8")
        (tmp_path / "keep.tmp").write_text("y", encoding="utf-8")
        candidates = Cleaner().scan([tmp_path])
        names = {c.path.name for c in candidates}
        assert {"a.tmp", "keep.tmp"} <= names
        assert all(c.size_bytes >= 0 for c in candidates)

    def test_scan_skips_excluded_names(self, tmp_path: t.Any) -> None:
        (tmp_path / "a.tmp").write_text("x", encoding="utf-8")
        (tmp_path / "important.dat").write_text("y", encoding="utf-8")
        candidates = Cleaner().scan([tmp_path], exclude_patterns=["important.dat"])
        assert [c.path.name for c in candidates] == ["a.tmp"]

    def test_scan_ignores_missing_path(self, tmp_path: t.Any) -> None:
        assert Cleaner().scan([tmp_path / "definitely-not-here"]) == []

    def test_clean_sends_to_trash_and_reports_success(self, tmp_path: t.Any) -> None:
        target = tmp_path / "a.tmp"
        target.write_text("x", encoding="utf-8")
        trash = FakeTrashProvider()
        results = Cleaner(trash).clean(Cleaner().scan([tmp_path]))
        assert [r["success"] for r in results] == [True]
        assert trash.sent == [str(target)]
        # Фейковая корзина ничего не удаляет — файл на месте.
        assert target.exists()

    def test_clean_without_trash_provider_reports_error(self, tmp_path: t.Any) -> None:
        (tmp_path / "a.tmp").write_text("x", encoding="utf-8")
        results = Cleaner().clean(Cleaner().scan([tmp_path]))
        assert len(results) == 1
        assert results[0]["success"] is False
        assert "корзин" in results[0]["error"]


class TestDuplicateScanner:
    def test_exact_duplicates_are_grouped(self, tmp_path: t.Any) -> None:
        (tmp_path / "one.bin").write_bytes(b"same-content")
        (tmp_path / "two.bin").write_bytes(b"same-content")
        (tmp_path / "other.bin").write_bytes(b"different-content")
        groups = DuplicateScanner().scan_exact_duplicates(list(tmp_path.iterdir()))
        assert len(groups) == 1
        assert {p.name for p in groups[0]} == {"one.bin", "two.bin"}

    def test_no_exact_duplicates_returns_empty(self, tmp_path: t.Any) -> None:
        (tmp_path / "a.bin").write_bytes(b"a")
        (tmp_path / "b.bin").write_bytes(b"b")
        assert DuplicateScanner().scan_exact_duplicates(list(tmp_path.iterdir())) == []

    def test_identical_images_are_perceptual_duplicates(self, tmp_path: t.Any) -> None:
        for name in ("img1.png", "img2.png"):
            Image.new("RGB", (32, 32), (200, 30, 30)).save(tmp_path / name)
        groups = DuplicateScanner().scan_perceptual_duplicates(list(tmp_path.iterdir()))
        assert len(groups) == 1
        assert {p.name for p in groups[0]} == {"img1.png", "img2.png"}

    def test_perceptual_scan_skips_non_images(self, tmp_path: t.Any) -> None:
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        assert DuplicateScanner().scan_perceptual_duplicates(list(tmp_path.iterdir())) == []


class TestConsoleEncoding:
    """Регресс на краш .exe в консоли с нерусской кодовой страницей.

    Найдено прогоном собранного приложения в гостевой Win11 (консоль cp437):
    print кириллицы обрывался UnicodeEncodeError и ронял процесс с кодом 20.
    """

    def test_fix_console_encoding_survives_cyrillic_in_cp1252_console(
        self, monkeypatch: t.Any
    ) -> None:
        import io

        import swagcleaner

        # Воспроизводим «английскую» консоль: cp1252 + строгие ошибки.
        raw_out = io.BytesIO()
        raw_err = io.BytesIO()
        out = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict")
        err = io.TextIOWrapper(raw_err, encoding="cp1252", errors="strict")
        monkeypatch.setattr(swagcleaner.sys, "stdout", out)
        monkeypatch.setattr(swagcleaner.sys, "stderr", err)

        # До фикса: кириллица в такой поток — это гарантированный UnicodeEncodeError.
        with pytest.raises(UnicodeEncodeError):
            out.write("Освобождено 2441.7 МБ\n")
            out.flush()

        swagcleaner._fix_console_encoding()

        # После фикса: тот же текст кодируется без исключения.
        out.write("Освобождено 2441.7 МБ, корзина\n")
        out.flush()
        err.write("итог: OK\n")
        err.flush()

        assert "2441.7" in raw_out.getvalue().decode("utf-8")
        assert out.encoding.lower() == "utf-8"

    def test_fix_console_encoding_tolerates_unwritable_streams(
        self, monkeypatch: t.Any
    ) -> None:
        import swagcleaner

        class BrokenStream:
            encoding = "cp1252"

            def reconfigure(self, *a: t.Any, **k: t.Any) -> None:
                raise OSError("поток нельзя перенастроить")

        monkeypatch.setattr(swagcleaner.sys, "stdout", BrokenStream())
        monkeypatch.setattr(swagcleaner.sys, "stderr", None)
        # Не должно бросить: оконная сборка живёт без консоли.
        swagcleaner._fix_console_encoding()


# ---------- этап 3: твики, бэкапы на диске, журнал ----------


class MemoryRegistry:
    """Реестр в памяти: тот же контракт, что у WinregRegistry, без Windows."""

    def __init__(self) -> None:
        self.values: t.Dict[t.Tuple[str, str, str], t.Tuple[str, int]] = {}

    def list_values(self, hive, key_path):
        return [(name, data, vtype)
                for (h, kp, name), (data, vtype) in sorted(self.values.items())
                if h == hive and kp == key_path]

    def delete_value(self, hive, key_path, value_name):
        self.values.pop((hive, key_path, value_name), None)

    def set_value(self, hive, key_path, value_name, data, value_type):
        self.values[(hive, key_path, value_name)] = (data, value_type)


class TestStartup:
    RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _registry(self) -> MemoryRegistry:
        registry = MemoryRegistry()
        registry.set_value("HKCU", self.RUN, "Discord",
                           r"C:\Apps\Discord\app.exe", 1)
        registry.set_value("HKLM", self.RUN, "Updater",
                           r"C:\Apps\Upd\up.exe", 1)
        return registry

    def test_read_startup_from_registry(self) -> None:
        from core.startup import read_startup

        entries = read_startup(registry=self._registry(), folders=[])
        names = {e.name for e in entries}
        assert {"Discord", "Updater"} <= names
        assert all(e.enabled and e.source == "registry" for e in entries)

    def test_read_startup_from_folders(self, tmp_path: t.Any) -> None:
        from core.startup import read_startup

        folder = tmp_path / "startup"
        folder.mkdir()
        (folder / "tool.lnk").write_text("x", encoding="utf-8")
        (folder / "desktop.ini").write_text("x", encoding="utf-8")
        entries = read_startup(registry=MemoryRegistry(), folders=[folder])
        assert [e.name for e in entries] == ["tool"]
        assert entries[0].source == "startup_folder"

    def test_disable_saves_snapshot_and_deletes(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        registry = self._registry()
        store = BackupStore(tmp_path)
        manager = StartupManager(registry=registry, store=store)
        entry = [e for e in manager.get_startup_entries()
                 if e.name == "Discord"][0]
        snapshot = manager.disable(entry)
        assert registry.list_values("HKCU", self.RUN) == []
        assert snapshot in store.list()
        data = store.restore(snapshot)
        assert data["value_name"] == "Discord"
        assert data["value_data"] == r"C:\Apps\Discord\app.exe"

    def test_restore_returns_value_and_drops_snapshot(self,
                                                      tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        registry = self._registry()
        store = BackupStore(tmp_path)
        manager = StartupManager(registry=registry, store=store)
        entry = [e for e in manager.get_startup_entries()
                 if e.name == "Discord"][0]
        snapshot = manager.disable(entry)
        manager.restore(snapshot)
        assert ("HKCU", self.RUN, "Discord") in registry.values
        assert snapshot not in store.list()

    def test_disable_folder_entry_refused(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupEntry, StartupManager

        manager = StartupManager(registry=MemoryRegistry(),
                                 store=BackupStore(tmp_path))
        entry = StartupEntry(name="tool", path=r"C:\t.lnk", enabled=True,
                             source="startup_folder")
        with pytest.raises(ValueError):
            manager.disable(entry)


class TestServices:
    def test_list_services_from_provider(self) -> None:
        from core.services import ServiceInfo, WindowsServiceController

        fake = [ServiceInfo("WaaSMedicSvc", "running", "manual"),
                ServiceInfo("Spooler", "stopped", "automatic")]
        controller = WindowsServiceController(provider=lambda: fake)
        assert controller.list_services() == fake
        assert controller.get_service_info("Spooler").start_mode == "automatic"
        assert controller.get_service_info("nope") is None

    def test_real_provider_smoke(self) -> None:
        """Настоящий SCM: читаем без изменений, список не пуст."""
        from core.services import WindowsServiceController

        services = WindowsServiceController().list_services()
        assert len(services) > 50  # на любой Windows служб сотни
        states = {s.state for s in services}
        assert "running" in states


class TestBackupStoreDisk:
    def test_snapshots_survive_recreate(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        BackupStore(tmp_path).save("one", {"v": 1}, kind="startup_disable")
        second = BackupStore(tmp_path)  # «новый запуск»: память пуста
        assert second.list() == ["one"]
        assert second.restore("one") == {"v": 1}
        info = second.info("one")
        assert info is not None and info["kind"] == "startup_disable"
        assert info["ts"] > 0

    def test_corrupt_file_is_skipped(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        store = BackupStore(tmp_path)
        store.save("good", {"v": 2})
        (tmp_path / "broken.json").write_text("{не json", encoding="utf-8")
        assert set(store.list()) == {"good", "broken"}
        assert store.restore("broken") is None
        assert store.restore("good") == {"v": 2}

    def test_remove_deletes_file(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        store = BackupStore(tmp_path)
        store.save("x", {"v": 3})
        store.remove("x")
        assert store.list() == []
        assert not (tmp_path / "x.json").exists()


class TestJournal:
    def test_log_and_tail(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        journal = Journal(tmp_path / "j.jsonl")
        journal.log("purge", "Удалено 3", removed=3)
        journal.log("startup_disable", "Discord", outcome="ok")
        tail = journal.tail()
        assert [e["kind"] for e in tail] == ["startup_disable", "purge"]
        assert tail[1]["removed"] == 3
        assert tail[0]["outcome"] == "ok"

    def test_tail_skips_corrupt_lines(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        path = tmp_path / "j.jsonl"
        journal = Journal(path)
        journal.log("purge", "ok")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{битая строка\n")
        tail = journal.tail()
        assert len(tail) == 1 and tail[0]["kind"] == "purge"

    def test_tail_without_file(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        assert Journal(tmp_path / "none.jsonl").tail() == []


class TestExecutor:
    RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def test_startup_disable_and_restore_cycle(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.executor import Executor
        from core.journal import Journal

        registry = MemoryRegistry()
        registry.set_value("HKCU", self.RUN, "Discord", r"C:\d.exe", 1)
        journal = Journal(tmp_path / "j.jsonl")
        executor = Executor(store=BackupStore(tmp_path / "bk"),
                            journal=journal, registry=registry)

        (result,) = executor.execute_actions([{
            "type": "startup_disable", "name": "Discord",
            "value": r"C:\d.exe", "hive": "HKCU", "key_path": self.RUN,
        }])
        assert result.success and result.snapshot
        assert registry.list_values("HKCU", self.RUN) == []

        (back,) = executor.execute_actions([{
            "type": "startup_restore", "snapshot": result.snapshot,
        }])
        assert back.success
        assert ("HKCU", self.RUN, "Discord") in registry.values

        kinds = [e["kind"] for e in journal.tail()]
        assert kinds == ["startup_restore", "startup_disable"]

    def test_unknown_action_fails_but_does_not_raise(self) -> None:
        from core.executor import Executor

        (result,) = Executor().execute_actions([{"type": "magic"}])
        assert not result.success

    def test_failed_action_is_logged_and_collected(self,
                                                   tmp_path: t.Any) -> None:
        from core.executor import Executor
        from core.journal import Journal

        journal = Journal(tmp_path / "j.jsonl")
        executor = Executor(journal=journal)  # без бэкапов disable не сможет
        (result,) = executor.execute_actions([{
            "type": "startup_disable", "name": "X", "value": "v",
            "hive": "HKCU", "key_path": self.RUN,
        }])
        assert not result.success
        assert executor.executed()[0].message
        assert journal.tail()[0]["outcome"] == "error"
