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


class TestStartupFixes:
    RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
    RUNONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"

    def test_snapshot_names_unique_per_key(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        registry = MemoryRegistry()
        registry.set_value("HKCU", self.RUN, "Foo", r"C:\a.exe", 1)
        registry.set_value("HKCU", self.RUNONCE, "Foo", r"C:\b.exe", 1)
        store = BackupStore(tmp_path)
        manager = StartupManager(registry=registry, store=store)
        entries = {e.key_path: e for e in manager.get_startup_entries()
                   if e.name == "Foo"}
        s1 = manager.disable(entries[self.RUN])
        s2 = manager.disable(entries[self.RUNONCE])
        assert s1 != s2
        assert set(store.list()) == {s1, s2}

    def test_snapshot_name_traversal_safe(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupEntry, StartupManager, SOURCE_REGISTRY

        manager = StartupManager(registry=MemoryRegistry(),
                                 store=BackupStore(tmp_path))
        entry = StartupEntry(name="../../evil", path="v", enabled=True,
                             source=SOURCE_REGISTRY, hive="HKCU",
                             key_path=self.RUN, value_type=1)
        snapshot = manager.disable(entry)
        assert "/" not in snapshot and "\\" not in snapshot and ".." not in snapshot
        assert list(tmp_path.glob("*.json"))

    def test_expand_sz_type_preserved(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        registry = MemoryRegistry()
        registry.set_value("HKCU", self.RUN, "Pathy", r"%WINDIR%\x.exe", 2)
        store = BackupStore(tmp_path)
        manager = StartupManager(registry=registry, store=store)
        entry = [e for e in manager.get_startup_entries() if e.name == "Pathy"][0]
        assert entry.value_type == 2
        snapshot = manager.disable(entry)
        data = store.restore(snapshot)
        assert data["value_type"] == 2
        manager.restore(snapshot)
        assert registry.values[("HKCU", self.RUN, "Pathy")][1] == 2

    def test_failed_delete_drops_snapshot(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        class DenyRegistry(MemoryRegistry):
            def delete_value(self, hive, key_path, value_name):
                raise PermissionError("denied")

        registry = DenyRegistry()
        registry.set_value("HKLM", self.RUN, "Upd", r"C:\u.exe", 1)
        store = BackupStore(tmp_path)
        manager = StartupManager(registry=registry, store=store)
        entry = [e for e in manager.get_startup_entries() if e.name == "Upd"][0]
        with pytest.raises(PermissionError):
            manager.disable(entry)
        assert store.list() == []


class TestJournalFixes:
    def test_rotation_keeps_writing(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        path = tmp_path / "j.jsonl"
        journal = Journal(path)
        big = "x" * 3000
        for i in range(800):  # ~2.5 МБ, гарантированно за порогом ротации
            journal.log("purge", f"{big}{i}")
        rotated = tmp_path / "j.1.jsonl"
        assert rotated.exists()
        journal.log("purge", "after-rotation")
        tail = journal.tail(5)
        assert tail[0]["detail"] == "after-rotation"

    def test_tail_reads_from_end_of_big_file(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        path = tmp_path / "j.jsonl"
        journal = Journal(path)
        for i in range(300):
            journal.log("purge", f"entry-{i}")
        tail = journal.tail(3)
        assert [e["detail"] for e in tail] == ["entry-299", "entry-298", "entry-297"]

    def test_tail_survives_broken_utf8(self, tmp_path: t.Any) -> None:
        from core.journal import Journal

        path = tmp_path / "j.jsonl"
        journal = Journal(path)
        journal.log("purge", "good")
        with open(path, "ab") as fh:
            fh.write(b"\xff\xfe{bad json\xff\n")
        journal.log("purge", "good2")
        kinds = [e["detail"] for e in journal.tail()]
        assert kinds == ["good2", "good"]


class TestBackupFixes:
    def test_orphan_tmp_cleaned_on_init(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        (tmp_path / "one.json").write_text(
            '{"name": "one", "ts": 1, "kind": "k", "data": {}}', encoding="utf-8")
        (tmp_path / "orphan.deadbeef.tmp").write_text("{}", encoding="utf-8")
        store = BackupStore(tmp_path)
        assert store.list() == ["one"]
        assert not list(tmp_path.glob("*.tmp"))

    def test_unsafe_name_rejected(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        store = BackupStore(tmp_path)
        with pytest.raises(ValueError):
            store.save("../escape", {"v": 1})
        with pytest.raises(ValueError):
            store.save("a/b", {"v": 1})

    def test_two_saves_no_tmp_race(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore

        store = BackupStore(tmp_path)
        store.save("n", {"v": 1})
        store.save("n", {"v": 2})
        assert store.restore("n") == {"v": 2}
        assert not list(tmp_path.glob("*.tmp"))


class TestExecutorFixes:
    RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def test_journal_failure_does_not_break_action(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.executor import Executor

        class BrokenJournal:
            def log(self, *a: t.Any, **k: t.Any) -> None:
                raise OSError("disk full")

        registry = MemoryRegistry()
        registry.set_value("HKCU", self.RUN, "Discord", r"C:\d.exe", 1)
        executor = Executor(store=BackupStore(tmp_path / "bk"),
                            journal=BrokenJournal(), registry=registry)
        (result,) = executor.execute_actions([{
            "type": "startup_disable", "name": "Discord",
            "value": r"C:\d.exe", "hive": "HKCU", "key_path": self.RUN,
        }])
        assert result.success
        assert registry.list_values("HKCU", self.RUN) == []


class TestDedupSimilar:
    def test_identical_pair_clustered_with_both_paths(self, tmp_path: t.Any) -> None:
        from core.dedup import DuplicateScanner

        from PIL import ImageDraw

        def patterned(block: bool) -> "Image.Image":
            im = Image.new("RGB", (64, 64), (10, 10, 10))
            d = ImageDraw.Draw(im)
            if block:
                d.rectangle([0, 0, 31, 31], fill=(240, 240, 240))
            else:
                for i in range(0, 64, 8):
                    d.line([i, 0, i, 63], fill=(240, 240, 240))
            return im

        img = patterned(True)
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        c = tmp_path / "c.png"
        img.save(a)
        img.save(b)
        patterned(False).save(c)
        clusters = DuplicateScanner().scan_similar([a, b, c])
        flat = [sorted(str(p) for p in cl) for cl in clusters]
        assert [sorted([str(a), str(b)])] == flat

    def test_singleton_not_reported(self, tmp_path: t.Any) -> None:
        from core.dedup import DuplicateScanner

        a = tmp_path / "only.png"
        Image.new("RGB", (64, 64), (10, 200, 10)).save(a)
        assert DuplicateScanner().scan_similar([a]) == []


class TestServicesFixes:
    def test_unknown_mode_kept(self) -> None:
        from core.services import ServiceInfo, WindowsServiceController

        fake = [ServiceInfo("Protected", "running", "unknown")]
        controller = WindowsServiceController(provider=lambda: fake)
        assert controller.list_services()[0].start_mode == "unknown"

class TestElevate:
    def test_task_xml_escapes_path(self) -> None:
        from core import elevate
        xml = elevate._task_xml('C:\\A & B\\SWAGcleaner.exe')
        assert 'A &amp; B' in xml
        assert 'A & B\\' not in xml
        assert 'HighestAvailable' in xml
        assert '<Triggers />' in xml

    def test_maybe_elevate_skips_non_frozen(self, monkeypatch: t.Any) -> None:
        from core import elevate
        monkeypatch.setattr(elevate, 'is_frozen', lambda: False)
        assert elevate.maybe_elevate(['--gui']) is False

    def test_maybe_elevate_admin_ensures_task(self, monkeypatch: t.Any) -> None:
        from core import elevate
        calls: list[str] = []
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'is_admin', lambda: True)
        monkeypatch.setattr(elevate, 'ensure_task', lambda: calls.append('ensure'))
        assert elevate.maybe_elevate(['--gui']) is False
        assert calls == ['ensure']

    def test_maybe_elevate_via_task(self, monkeypatch: t.Any) -> None:
        from core import elevate
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'is_admin', lambda: False)
        monkeypatch.setattr(elevate, 'task_exists', lambda: True)
        monkeypatch.setattr(elevate, 'run_task', lambda: True)
        monkeypatch.setattr(elevate, 'relaunch_elevated',
                            lambda argv: (_ for _ in ()).throw(AssertionError('runas не нужен')))
        assert elevate.maybe_elevate(['--gui']) is True

    def test_maybe_elevate_first_run_runas(self, monkeypatch: t.Any) -> None:
        from core import elevate
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'is_admin', lambda: False)
        monkeypatch.setattr(elevate, 'task_exists', lambda: False)
        seen: list[list[str]] = []
        monkeypatch.setattr(elevate, 'relaunch_elevated', lambda argv: seen.append(argv) or True)
        assert elevate.maybe_elevate(['--gui']) is True
        assert seen == [['--gui']]

    def test_maybe_elevate_runas_declined(self, monkeypatch: t.Any) -> None:
        from core import elevate
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'is_admin', lambda: False)
        monkeypatch.setattr(elevate, 'task_exists', lambda: False)
        monkeypatch.setattr(elevate, 'relaunch_elevated', lambda argv: False)
        assert elevate.maybe_elevate(['--gui']) is False

    def test_register_task_writes_xml(self, monkeypatch: t.Any) -> None:
        from core import elevate
        captured: dict[str, str] = {}

        class R:
            returncode = 0
            stderr = ''
            stdout = ''

        def fake_schtasks(*args: str, timeout: int = 30) -> R:
            if '/create' in args:
                xml_path = args[args.index('/xml') + 1]
                captured['xml'] = open(xml_path, encoding='utf-16').read()
            return R()

        monkeypatch.setattr(elevate, '_schtasks', fake_schtasks)
        monkeypatch.setattr(elevate, '_create_shortcut', lambda exe: None)
        assert elevate.register_task('C:\\Tools\\SWAGcleaner.exe') is True
        assert 'C:\\Tools\\SWAGcleaner.exe' in captured['xml']

    def test_ensure_task_reregisters_on_move(self, monkeypatch: t.Any, tmp_path: t.Any) -> None:
        from core import elevate
        import sys as _sys
        calls: list[str] = []
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'task_exists', lambda: True)
        monkeypatch.setattr(elevate, 'task_command', lambda: 'C:\\old\\SWAGcleaner.exe')
        monkeypatch.setattr(elevate, 'register_task', lambda exe: calls.append(exe) or True)
        monkeypatch.setattr(elevate, '_create_shortcut', lambda exe: None)
        monkeypatch.setattr(_sys, 'executable', str(tmp_path / 'SWAGcleaner.exe'))
        elevate.ensure_task()
        assert calls == [str(tmp_path / 'SWAGcleaner.exe')]

    def test_ensure_task_quiet_when_current(self, monkeypatch: t.Any, tmp_path: t.Any) -> None:
        from core import elevate
        import sys as _sys
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(_sys, 'executable', str(tmp_path / 'SWAGcleaner.exe'))
        monkeypatch.setattr(elevate, 'task_exists', lambda: True)
        monkeypatch.setattr(elevate, 'task_command',
                            lambda: str(tmp_path / 'SWAGcleaner.exe'))
        monkeypatch.setattr(elevate, 'register_task',
                            lambda exe: (_ for _ in ()).throw(AssertionError('не надо')))
        elevate.ensure_task()
class TestStartupRestoreGuard:
    def test_restore_refuses_foreign_key(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        class MemoryRegistry:
            def __init__(self) -> None:
                self.writes: list[tuple] = []

            def list_values(self, hive, key_path): return []
            def delete_value(self, hive, key_path, value_name): pass
            def set_value(self, hive, key_path, value_name, data, value_type):
                self.writes.append((hive, key_path, value_name))

        store = BackupStore(tmp_path / "bk")
        store.save("snap-evil", {"hive": "HKLM", "key_path": r"Software\Evil",
                                 "value_name": "x", "value_data": "y",
                                 "value_type": 1})
        reg = MemoryRegistry()
        mgr = StartupManager(registry=reg, store=store)
        with pytest.raises(ValueError):
            mgr.restore("snap-evil")
        assert reg.writes == []
        assert (tmp_path / "bk" / "snap-evil.json").exists()

    def test_restore_allows_run_key(self, tmp_path: t.Any) -> None:
        from core.backup import BackupStore
        from core.startup import StartupManager

        class MemoryRegistry:
            def __init__(self) -> None:
                self.writes: list[tuple] = []

            def list_values(self, hive, key_path): return []
            def delete_value(self, hive, key_path, value_name): pass
            def set_value(self, hive, key_path, value_name, data, value_type):
                self.writes.append((hive, key_path, value_name, value_type))

        store = BackupStore(tmp_path / "bk")
        store.save("snap-ok", {"hive": "HKCU",
                               "key_path": r"Software\Microsoft\Windows\CurrentVersion\Run",
                               "value_name": "app", "value_data": "C:\\app.exe",
                               "value_type": 2})
        reg = MemoryRegistry()
        mgr = StartupManager(registry=reg, store=store)
        mgr.restore("snap-ok")
        assert reg.writes == [("HKCU",
                               r"Software\Microsoft\Windows\CurrentVersion\Run",
                               "app", 2)]


class TestElevateShortcuts:
    def test_shortcut_uses_encoded_command(self, monkeypatch: t.Any, tmp_path: t.Any) -> None:
        import base64
        import subprocess as sp
        from core import elevate

        seen: dict[str, list] = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = cmd

            class R:
                returncode = 0
                stderr = b''
            return R()

        monkeypatch.setattr(sp, 'run', fake_run)
        monkeypatch.setattr(elevate.Path, 'home', lambda: tmp_path)
        elevate._create_shortcut('C:\\Tools\\SWAGcleaner.exe')
        cmd = seen['cmd']
        assert '-EncodedCommand' in cmd
        blob = cmd[cmd.index('-EncodedCommand') + 1]
        decoded = base64.b64decode(blob).decode('utf-16-le')
        assert 'SWAGcleaner' in decoded
        assert 'UAC' in decoded
        assert '/run /tn SWAGcleaner' in decoded

    def test_ensure_task_recreates_missing_shortcut(self, monkeypatch: t.Any, tmp_path: t.Any) -> None:
        import sys
        from pathlib import Path
        from core import elevate

        calls: list[str] = []
        monkeypatch.setattr(elevate, 'is_frozen', lambda: True)
        monkeypatch.setattr(elevate, 'task_exists', lambda: True)
        monkeypatch.setattr(elevate, 'task_command', lambda: str(Path(sys.executable).resolve()))
        monkeypatch.setattr(elevate, '_shortcut_path', lambda: tmp_path / 'gone.lnk')
        monkeypatch.setattr(elevate, '_create_shortcut', lambda exe: calls.append(exe))
        elevate.ensure_task()
        assert calls  # ярлык отсутствовал - должен быть пересоздан
