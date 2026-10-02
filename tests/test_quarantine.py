# -*- coding: utf-8 -*-
"""Карантин: папка просмотра вместо слепого стирания.

Удаление через карантин — файлы сначала переезжают в локальную папку
SWAGcleaner со структурой путей и манифестом, их можно посмотреть
глазами, потом стереть одной кнопкой. Тесты гоняют весь конвейер:
stash → манифест → stats → clear, отказ при нехватке места, отчёт
сессии с полями карантина и окно просмотра путей перед удалением.
"""
from __future__ import annotations

import json
import typing as t
from pathlib import Path

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from core import quarantine
from ui.context import ctx
from ui.dialog import ConfirmDialog, FilePreviewDialog
from ui.session import PurgeReport, Session

# ---------------------------------------------------------------- core


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Карантин и «пользовательские» файлы — в одной песочнице tmp.

    Дефолтный корень карантина теперь живёт возле программы, а не в
    LOCALAPPDATA — тесты патчат его в песочницу, чтобы прогон не
    приносил папки в репозиторий и не зависел от прошлого прогона.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(quarantine, "_override", str(tmp_path / "quarantine"))
    return tmp_path


def _make_user_file(sandbox: Path, rel: str,
                    payload: bytes = b"x" * 128) -> Path:
    path = sandbox / "home" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


class TestQuarantineCore:
    def test_stash_moves_file_and_keeps_structure(
            self, sandbox: Path) -> None:
        src = _make_user_file(sandbox, "Temp/report.tmp")
        batch = quarantine.new_batch()
        dst, reason = quarantine.stash_file(str(src), batch)
        assert dst is not None and reason == ""
        assert not src.exists()
        assert dst.is_file() and dst.read_bytes() == b"x" * 128
        # Структура пути сохранена: путь внутри батча повторяет исходный.
        rel = dst.relative_to(batch)
        assert len(rel.parts) >= 3
        assert rel.parts[-2] == "Temp" and rel.parts[-1] == "report.tmp"

    def test_stash_refuses_missing_file(self, sandbox: Path) -> None:
        batch = quarantine.new_batch()
        dst, reason = quarantine.stash_file(
            str(sandbox / "nope.tmp"), batch)
        assert dst is None and reason

    def test_stash_collision_gets_suffix(self, sandbox: Path) -> None:
        src = _make_user_file(sandbox, "Temp/same.tmp")
        batch = quarantine.new_batch()
        first, _ = quarantine.stash_file(str(src), batch)
        src.write_bytes(b"y" * 64)
        second, _ = quarantine.stash_file(str(src), batch)
        assert first is not None and second is not None
        assert first != second
        assert second.name.startswith("same~")

    def test_new_batch_collision_suffix(self, sandbox: Path) -> None:
        first = quarantine.new_batch()
        second = quarantine.new_batch()
        # Одна секунда без смены времени: суффикс, а не совпадение.
        assert first != second

    def test_manifest_stats_clear_cycle(self, sandbox: Path) -> None:
        a = _make_user_file(sandbox, "Temp/a.tmp", b"a" * 100)
        b = _make_user_file(sandbox, "Temp/b.tmp", b"b" * 50)
        batch = quarantine.new_batch()
        quarantine.stash_file(str(a), batch)
        quarantine.stash_file(str(b), batch)
        manifest = quarantine.write_manifest(
            batch, "cleaner",
            [{"path": str(a), "category": "temp.app", "stashed": ""}])
        assert manifest.is_file()
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["kind"] == "cleaner" and data["manifest"]
        assert data["manifest"][0]["category"] == "temp.app"

        size, batches = quarantine.stats()
        assert size == 150 and batches == 1
        freed = quarantine.clear()
        assert freed == 150
        size, batches = quarantine.stats()
        assert size == 0 and batches == 0


# ---------------------------------------------------------------- session


class _NoopClient:
    """Ядро не вызывается: карантин переносит файлы сам."""

    def purge(self, items: t.Any, dry_run: bool = False,
              on_progress: t.Any = None) -> dict:
        raise AssertionError("карантин не должен звать ядро удаления")

    def cat_meta(self) -> dict:
        return {}


def _wait_finished(qapp: t.Any, session: Session,
                   timeout_ms: int = 4000) -> t.Any:
    result: t.Dict[str, t.Any] = {}
    session.taskFinished.connect(
        lambda name, payload: result.update(name=name, payload=payload))
    waited = 0
    while not result and waited < timeout_ms:
        qapp.processEvents()
        QTest.qWait(10)
        waited += 10
    return result


@pytest.fixture
def qsession() -> t.Any:
    """Сессия на тест с уборкой: пул воркеров не живёт после теста.

    Живые пулы прошлых тестов грузили офскрин-цикл и флейкали
    несвязанные кликовые тесты (SpeechBox и кнопки твиков).
    """
    session = Session(client=_NoopClient())
    yield session
    session.shutdown()


class TestQuarantineSession:
    def test_purge_items_through_quarantine(
            self, qapp: t.Any, qsession: t.Any, sandbox: Path) -> None:
        a = _make_user_file(sandbox, "Temp/a.tmp", b"a" * 300)
        b = _make_user_file(sandbox, "Cache/b.bin", b"b" * 200)
        qsession.purge_items(
            [{"path": str(a), "category": "temp.app"},
             {"path": str(b), "category": "cache"}],
            False, quarantine=True)
        result = _wait_finished(qapp, qsession)
        report = result["payload"]
        assert isinstance(report, PurgeReport)
        assert result["name"] == "purge"
        assert report.quarantined == 2
        assert report.quarantined_bytes == 500
        assert report.quarantine_dir and Path(report.quarantine_dir).is_dir()
        assert report.no_space == []
        assert report.lanes == {"quarantine": 2}
        assert not a.exists() and not b.exists()
        manifest = Path(report.quarantine_dir) / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["kind"] == "cleaner"
        assert {row["category"] for row in data["manifest"]} == \
            {"temp.app", "cache"}
        # У каждой записи есть путь внутри карантина: окно карантина
        # восстановит файл по нему.
        assert all(row["stashed"] for row in data["manifest"])

    def test_no_space_keeps_file_and_surfaces_path(
            self, qapp: t.Any, qsession: t.Any, sandbox: Path,
            monkeypatch: pytest.MonkeyPatch) -> None:
        src = _make_user_file(sandbox, "Temp/stuck.tmp", b"s" * 64)
        real_stash = quarantine.stash_file

        def picky_stash(path: str, batch: Path) -> t.Any:
            if path.endswith("stuck.tmp"):
                return None, "нет места под копию в карантине"
            return real_stash(path, batch)

        monkeypatch.setattr(quarantine, "stash_file", picky_stash)
        qsession.purge_items(
            [{"path": str(src), "category": "temp.app"}],
            False, quarantine=True)
        result = _wait_finished(qapp, qsession)
        report = result["payload"]
        assert report.quarantined == 0
        assert len(report.no_space) == 1
        assert report.no_space[0]["path"] == str(src)
        assert report.no_space[0]["reason"]
        # Файл не исчез молча: остался на месте.
        assert src.exists() and src.read_bytes() == b"s" * 64

    def test_purge_duplicates_groups_in_manifest(
            self, qapp: t.Any, qsession: t.Any, sandbox: Path) -> None:
        import os
        import time

        from ui.session import DupGroup

        keep = _make_user_file(sandbox, "Photos/keep.jpg", b"k" * 10)
        drop = _make_user_file(sandbox, "Photos/drop.jpg", b"k" * 10)
        # Явная свежесть keep: при равных mtime max брал последний путь.
        stamp = time.time()
        os.utime(keep, (stamp + 10, stamp + 10))
        os.utime(drop, (stamp, stamp))
        qsession.purge_duplicates(
            [DupGroup(hash="h", size=10, paths=[str(keep), str(drop)])],
            quarantine=True)
        result = _wait_finished(qapp, qsession)
        report = result["payload"]
        assert report.quarantined == 1
        assert not drop.exists() and keep.exists()
        manifest = Path(report.quarantine_dir) / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["kind"] == "dupes"
        group = data["groups"][0]
        assert group["keep"] == str(keep)
        assert group["removed"] == [str(drop)]

    def test_describe_purge_mentions_quarantine(self) -> None:
        report = PurgeReport(dry_run=False)
        report.quarantined = 3
        report.quarantined_bytes = 4096
        report.quarantine_dir = r"C:\q"
        report.no_space = [{"path": "x", "reason": "r"}]
        described = Session(client=_NoopClient()).describe_purge(report)
        assert ctx().tr("session.purge_quarantined").split("{")[0] in described
        assert ctx().tr("session.purge_no_space").split("{")[0] in described


# ---------------------------------------------------------------- ui


def _preview_entries() -> t.List[t.Tuple[str, t.List[str]]]:
    return [
        ("Временные файлы · 2",
         [r"C:\Users\one\AppData\Local\Temp\a.tmp",
          r"C:\Users\one\AppData\Local\Temp\b.tmp"]),
        ("Кэш · 1", [r"C:\Users\one\AppData\Cache\c.bin"]),
    ]


class TestPreviewUI:
    def test_confirm_dialog_shows_files_button(self, qapp: t.Any) -> None:
        dialog = ConfirmDialog(
            None, [("категория", "low")], note="проверка",
            entries=_preview_entries())
        buttons = dialog.findChildren(QPushButton)
        label = ctx().tr("preview.open_button").split("(")[0]
        opener = next((b for b in buttons if label in b.text()), None)
        assert opener is not None
        assert "3" in opener.text()  # сумма путей всех групп

    def test_confirm_dialog_without_entries_has_no_button(
            self, qapp: t.Any) -> None:
        dialog = ConfirmDialog(None, [("категория", "low")], note="n")
        buttons = dialog.findChildren(QPushButton)
        label = ctx().tr("preview.open_button").split("(")[0]
        assert not any(label in b.text() for b in buttons)

    def test_file_preview_groups_paths(self, qapp: t.Any) -> None:
        dialog = FilePreviewDialog(None, _preview_entries())
        labels = dialog.findChildren(QLabel)
        texts = [l.text() for l in labels]
        assert any(r"C:\Users\one\AppData\Local\Temp\b.tmp" in t
                   for t in texts)
        assert any("Временные файлы" in t for t in texts)
        # Кнопка «скопировать всё» копирует все пути разом.
        buttons = dialog.findChildren(QPushButton)
        copy_btn = next(
            (b for b in buttons
             if ctx().tr("preview.copy_all").split("(")[0] in b.text()),
            None)
        assert copy_btn is not None


class TestScanPhaseStatus:
    def test_walking_phase_sets_live_status(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        win, session = win_fake
        win.go_to_page(1)  # чистка
        page = win._pages[1]
        session.scanPhase.emit("walking", 5.0, 1024.0)
        text = page._status_label.text()
        assert ctx().tr("cleaner.phase_walking") in text
        assert "5" in text
        assert "1.0" in text  # human_size(1024)


class TestQuarantineSettings:
    def test_settings_page_has_quarantine_section(
            self, qapp: t.Any, win_fake: t.Any,
            monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        win, _session = win_fake
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        win.go_to_page(5)  # настройки
        settings = win._pages[5]
        assert settings._quarantine_check is not None
        assert settings._quarantine_check.isChecked() \
            == ctx().quarantineEnabled()
        # Переключение живёт в контексте (переживает перезапуск).
        before = ctx().quarantineEnabled()
        settings._quarantine_check.setChecked(not before)
        assert ctx().quarantineEnabled() is (not before)
        ctx().setQuarantine(before)

    def test_refresh_quarantine_counts_batches(
            self, qapp: t.Any, win_fake: t.Any, sandbox: Path) -> None:
        win, _session = win_fake
        win.go_to_page(5)
        settings = win._pages[5]
        _make_user_file(sandbox, "Temp/a.tmp", b"a" * 100)
        batch = quarantine.new_batch()
        quarantine.stash_file(
            str(sandbox / "home" / "Temp" / "a.tmp"), batch)
        settings.refresh_quarantine()
        text = settings._quarantine_size.text()
        assert ctx().tr("quarantine.size_line").split(":")[0] in text
        assert "1" in text
        quarantine.clear()

    def test_clear_action_runs_session_task(
            self, qapp: t.Any, win_fake: t.Any,
            monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        win, session = win_fake
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        # Прямой вызов задачи минуя модальный диалог подтверждения.
        win._session.clear_quarantine()
        assert session.is_busy()
        session.finish_quarantine_clear(2048)
        QTest.qWait(30)
        qapp.processEvents()
        assert not session.is_busy()


class TestPurgeToasts:
    def test_finish_purge_reports_quarantine_dir(
            self, qapp: t.Any, win_fake: t.Any,
            monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        win, session = win_fake
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        win.go_to_page(1)
        page = win._pages[1]
        report = PurgeReport(dry_run=False, removed=2)
        report.quarantined = 2
        report.quarantined_bytes = 2048
        report.quarantine_dir = str(tmp_path / "q")
        report.no_space = [{"path": "x", "reason": "r"}]
        Path(report.quarantine_dir).mkdir(parents=True)
        win._work_page = page
        win._finish_purge(report, page)
        assert page._status_label.text()
        # Статус упоминает карантин: пользователь видит, куда уехали файлы.
        assert "карантин" in page._status_label.text().lower()
        # Файлы легли — окно карантина раскрывается само и перечитывает
        # список записей (лёгкая задача уже ушла в сессию).
        assert win._quarantine_dialog is not None
        assert session.quarantine_calls[-1][0] == "list"


# ------------------------------------------------- окно карантина: ядро

class TestQuarantineEntriesCore:
    def _stash_two(self, sandbox: Path) -> t.List[Path]:
        a = _make_user_file(sandbox, "Temp/a.tmp", b"a" * 120)
        b = _make_user_file(sandbox, "Temp/b.tmp", b"b" * 60)
        batch = quarantine.new_batch()
        stashed = []
        manifest = []
        for src in (a, b):
            dst, _reason = quarantine.stash_file(str(src), batch)
            assert dst is not None
            stashed.append(dst)
            manifest.append({
                "path": str(src), "category": "temp.app", "stashed": str(dst)})
        quarantine.write_manifest(batch, "cleaner", manifest)
        return stashed

    def test_entries_restore_delete_cycle(self, sandbox: Path) -> None:
        stashed = self._stash_two(sandbox)
        rows = quarantine.entries()
        assert len(rows) == 2
        assert sum(r["size"] for r in rows) == 180
        # Восстановление возвращает файл на место и убирает запись.
        first = rows[0]
        ok, reason = quarantine.restore(first)
        assert ok and reason == ""
        assert Path(first["path"]).is_file()
        assert not Path(first["stashed"]).exists()
        remaining = quarantine.entries()
        assert len(remaining) == 1
        # Стирание второй записи навсегда освобождает карантин.
        ok, _reason = quarantine.delete_entry(remaining[0])
        assert ok
        assert quarantine.entries() == []
        size, batches = quarantine.stats()
        assert size == 0 and batches == 0
        assert all(s.exists() is False for s in stashed[1:])

    def test_restore_refuses_occupied_target(self, sandbox: Path) -> None:
        self._stash_two(sandbox)
        rows = quarantine.entries()
        target = Path(rows[0]["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"other")
        ok, reason = quarantine.restore(rows[0])
        # Перезапись запрещена: файл в карантине остался на месте.
        assert not ok and reason
        assert Path(rows[0]["stashed"]).is_file()

    def test_entries_list_orphan_files_without_manifest(
            self, sandbox: Path) -> None:
        batch = quarantine.new_batch()
        orphan = batch / "C" / "Users" / "x" / "loose.tmp"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"o" * 32)
        rows = quarantine.entries()
        assert len(rows) == 1
        assert rows[0]["path"] == ""
        assert rows[0]["stashed"].endswith("loose.tmp")
        # Восстановление недоступно, а стирание работает.
        ok, _ = quarantine.restore(rows[0])
        assert not ok
        ok, _ = quarantine.delete_entry(rows[0])
        assert ok
        assert quarantine.entries() == []

    def test_entries_read_legacy_dupes_manifest(
            self, sandbox: Path) -> None:
        batch = quarantine.new_batch()
        batch.joinpath("manifest.json").write_text(
            json.dumps({
                "kind": "dupes",
                "groups": [{"hash": "h", "size": 5,
                            "keep": r"C:\k.jpg",
                            "removed": [r"C:\d.jpg"]}],
            }, ensure_ascii=False), encoding="utf-8")
        rows = quarantine.entries()
        assert len(rows) == 1
        # Старому формату stashed неизвестен: восстановить нельзя.
        assert rows[0]["path"] == r"C:\d.jpg"
        assert rows[0]["stashed"] == ""
        ok, _reason = quarantine.restore(rows[0])
        assert not ok

    def test_set_root_override(self, sandbox: Path) -> None:
        alt = sandbox / "alt-quarantine"
        quarantine.set_root(str(alt))
        try:
            assert quarantine.root() == alt
            batch = quarantine.new_batch()
            assert str(batch).startswith(str(alt))
        finally:
            quarantine.set_root(None)
        assert quarantine.root() != alt

    def test_default_root_sits_next_to_program(
            self, sandbox: Path, monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path) -> None:
        """Дефолтный карантин - quarantine/ рядом с exe, не в AppData."""
        import sys

        monkeypatch.setattr(quarantine, "_override", None)
        exe = tmp_path / "app" / "SWAGcleaner.exe"
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        assert quarantine.root() == exe.parent / "quarantine"

    def test_default_root_in_sources(
            self, sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Без frozen корень - quarantine/ у корня проекта."""
        from pathlib import Path as _P

        monkeypatch.setattr(quarantine, "_override", None)
        expected = _P(quarantine.__file__).resolve().parent.parent / "quarantine"
        assert quarantine.root() == expected

    def test_delete_batch_guards_outside_root(
            self, sandbox: Path) -> None:
        outside = sandbox / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        ok, freed = quarantine.delete_batch(str(outside))
        assert not ok and freed == 0
        assert outside.exists()


# ------------------------------------------------- окно карантина: ui

class TestConfirmDialogFilesMode:
    def _dialog(self, parent: t.Any = None) -> t.Any:
        from ui.dialog import MODE_FILES
        return ConfirmDialog(parent, [("категория", "low")],
                              note="n", mode=MODE_FILES)

    def test_confirm_returns_quarantine_action(
            self, qapp: t.Any) -> None:
        dialog = self._dialog()
        assert dialog.confirm_button().text() \
            == ctx().tr("confirm.quarantine")
        dialog.confirm()
        assert dialog.action() == "quarantine"

    def test_danger_button_returns_delete(self, qapp: t.Any) -> None:
        dialog = self._dialog()
        dialog.delete_forever()
        assert dialog.action() == "delete"

    def test_cancel_returns_empty(self, qapp: t.Any) -> None:
        dialog = self._dialog()
        dialog.cancel()
        assert dialog.action() == ""

    def test_ask_row_emits_question(self, qapp: t.Any) -> None:
        from PySide6.QtCore import Qt
        dialog = self._dialog()
        assert dialog.ask_widget() is not None
        caught: t.List[str] = []
        dialog.asked.connect(caught.append)
        dialog.ask_widget().setText("что это за файлы?")
        QTest.keyClick(dialog.ask_widget(), Qt.Key.Key_Return)
        assert caught == ["что это за файлы?"]
        assert dialog.ask_widget().text() == ""

    def test_set_answer_truncates_long_text(self, qapp: t.Any) -> None:
        dialog = self._dialog()
        dialog.set_answer("д" * 500)
        label = dialog.findChildren(QLabel)
        texts = [l.text() for l in label if l.text()]
        long_answers = [t for t in texts if t.endswith("...") and len(t) >= 400]
        assert long_answers

    def test_mini_mascot_syncs_with_window(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        win, _session = win_fake
        dialog = self._dialog(win)
        mini = dialog.mascot_widget()
        assert mini is not None
        win._mascot.set_mood("panic")
        QTest.qWait(20)
        qapp.processEvents()
        assert mini.mood() == "panic"


class TestConfirmDialogApplyTextAndSize:
    def test_apply_text_changes_action_button(
            self, qapp: t.Any) -> None:
        dialog = ConfirmDialog(None, [("пункт", "low")],
                                apply_text=ctx().tr("quarantine.move_button"))
        assert dialog.confirm_button().text() \
            == ctx().tr("quarantine.move_button")
        # retranslate не сбивает свой текст кнопки.
        dialog.retranslate()
        assert dialog.confirm_button().text() \
            == ctx().tr("quarantine.move_button")

    def test_panel_keeps_spacious_height(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        """Короткий список не сжимает панель в щель: высота с полом."""
        win, _session = win_fake
        dialog = ConfirmDialog(win, [("пункт", "low")])
        dialog._fit_to_parent()
        floor = int(win.height() * ConfirmDialog.MIN_FRACTION)
        assert dialog.panel().height() >= floor
        assert dialog.panel().height() \
            <= win.height() - 2 * ConfirmDialog.PANEL_MARGIN


class TestQuarantineDialog:
    def _entries(self) -> t.List[t.Dict[str, t.Any]]:
        return [
            {"batch": "b1", "batch_name": "2025-01-01_00-00-00",
             "kind": "cleaner", "path": r"C:\Users\x\Temp\a.tmp",
             "category": "temp.app", "stashed": r"C:\q\a.tmp", "size": 120},
            {"batch": "b1", "batch_name": "2025-01-01_00-00-00",
             "kind": "cleaner", "path": r"C:\Users\x\Temp\b.tmp",
             "category": "temp.app", "stashed": r"C:\q\b.tmp", "size": 60},
        ]

    def _dialog(self, qapp: t.Any) -> t.Any:
        from ui.dialog import QuarantineDialog
        dialog = QuarantineDialog(None)
        dialog.resize(900, 640)
        dialog.set_entries(self._entries(), 180)
        return dialog

    def test_entries_render_and_selection(
            self, qapp: t.Any) -> None:
        dialog = self._dialog(qapp)
        assert len(dialog.selected_entries()) == 2
        from PySide6.QtWidgets import QCheckBox
        boxes = dialog.findChildren(QCheckBox)
        assert len(boxes) == 2
        boxes[0].setChecked(False)
        selected = dialog.selected_entries()
        assert len(selected) == 1
        assert selected[0]["path"].endswith("b.tmp")
        # Кнопки гаснут при пустом выборе.
        boxes[1].setChecked(False)
        assert not dialog.selected_entries()

    def test_buttons_gated_by_selection(self, qapp: t.Any) -> None:
        dialog = self._dialog(qapp)
        from PySide6.QtWidgets import QCheckBox
        boxes = dialog.findChildren(QCheckBox)
        for box in boxes:
            box.setChecked(False)
        assert not dialog._restore_button.isEnabled()
        assert not dialog._delete_button.isEnabled()
        boxes[0].setChecked(True)
        assert dialog._restore_button.isEnabled()
        assert dialog._delete_button.isEnabled()
        assert "(1)" in dialog._restore_button.text()

    def test_delete_requires_explicit_confirm(
            self, qapp: t.Any, monkeypatch: pytest.MonkeyPatch) -> None:
        dialog = self._dialog(qapp)
        received: t.List[t.List[t.Dict[str, t.Any]]] = []
        dialog.deleteRequested.connect(received.append)
        # Отмена подтверждения: ничего не улетает.
        monkeypatch.setattr(
            "ui.dialog.ConfirmDialog.ask_purge",
            staticmethod(lambda *a, **k: ""))
        dialog._emit_delete()
        assert received == []
        # Явное «удалить навсегда»: выбранные записи уходят сигналом.
        monkeypatch.setattr(
            "ui.dialog.ConfirmDialog.ask_purge",
            staticmethod(lambda *a, **k: "delete"))
        dialog._emit_delete()
        assert len(received) == 1 and len(received[0]) == 2

    def test_ask_row_and_answer(self, qapp: t.Any) -> None:
        dialog = self._dialog(qapp)
        caught: t.List[str] = []
        dialog.asked.connect(caught.append)
        dialog.ask_widget().setText("что тут лежит?")
        dialog._emit_asked()
        assert caught == ["что тут лежит?"]
        dialog.set_answer("тут твои временные файлы")
        assert dialog._answer_label.text() == "тут твои временные файлы"
        assert dialog._answer_label.isVisibleTo(dialog._panel)

    def test_restore_unavailable_hint(self, qapp: t.Any) -> None:
        from ui.dialog import QuarantineDialog
        dialog = QuarantineDialog(None)
        dialog.set_entries([{
            "batch": "b1", "batch_name": "old", "kind": "dupes",
            "path": r"C:\d.jpg", "category": "dupes.photo",
            "stashed": "", "size": 0,
        }], 0)
        labels = dialog.findChildren(QLabel)
        assert any(ctx().tr("quarantine.restore_unavailable") in l.text()
                   for l in labels)


class TestQuarantineWindowFlow:
    def test_open_window_runs_list_task(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        win, session = win_fake
        win._open_quarantine_window()
        dialog = win._quarantine_dialog
        assert dialog is not None and dialog.isVisible()
        assert ("list", None) in session.quarantine_calls
        session.finish_quarantine_list({
            "entries": [{
                "batch": "b1", "batch_name": "b1", "kind": "cleaner",
                "path": r"C:\Users\x\Temp\a.tmp", "category": "temp.app",
                "stashed": r"C:\q\a.tmp", "size": 120,
            }],
            "bytes": 120,
        })
        QTest.qWait(30)
        qapp.processEvents()
        assert dialog._total_label.text()
        assert len(dialog.selected_entries()) == 1
        # Повторное открытие не плодит второе окно.
        win._open_quarantine_window()
        assert win._quarantine_dialog is dialog

    def test_restore_request_runs_session_task(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        win, session = win_fake
        win._open_quarantine_window()
        dialog = win._quarantine_dialog
        entry = {
            "batch": "b1", "batch_name": "b1", "kind": "cleaner",
            "path": r"C:\Users\x\Temp\a.tmp", "category": "temp.app",
            "stashed": r"C:\q\a.tmp", "size": 120,
        }
        # Открытие уже запустило list: завершаем его, чтобы окно не
        # считалось занятым, и только тогда просим восстановить.
        session.finish_quarantine_list(
            {"entries": [entry], "bytes": 120})
        QTest.qWait(30)
        qapp.processEvents()
        dialog.restoreRequested.emit([entry])
        assert session.quarantine_calls[-1] == ("restore", [entry])
        session.finish_quarantine_restore({
            "restored": [{"path": entry["path"], "size": 120}],
            "failed": [],
        })
        QTest.qWait(30)
        qapp.processEvents()
        # После итога окно перезаливает список: снова задача list.
        assert session.quarantine_calls[-1] == ("list", None)

    def test_delete_request_runs_session_task(
            self, qapp: t.Any, win_fake: t.Any) -> None:
        win, session = win_fake
        win._open_quarantine_window()
        dialog = win._quarantine_dialog
        entry = {
            "batch": "b1", "batch_name": "b1", "kind": "cleaner",
            "path": r"C:\Users\x\Temp\a.tmp", "category": "temp.app",
            "stashed": r"C:\q\a.tmp", "size": 120,
        }
        session.finish_quarantine_list(
            {"entries": [entry], "bytes": 120})
        QTest.qWait(30)
        qapp.processEvents()
        dialog.deleteRequested.emit([entry])
        assert session.quarantine_calls[-1] == ("delete", [entry])
        session.finish_quarantine_delete({"freed": 120, "failed": []})
        QTest.qWait(30)
        qapp.processEvents()
        assert session.quarantine_calls[-1] == ("list", None)

    def test_quarantine_folder_signal_moves_root(
            self, qapp: t.Any, win_fake: t.Any,
            monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        win, _session = win_fake
        before = ctx().quarantineDir()
        target = str(tmp_path / "qdir")
        win._context.quarantineRootChanged.emit(target)
        from core import quarantine as qcore
        assert str(qcore.root()) == target
        win._context.quarantineRootChanged.emit("")
        assert str(qcore.root()) != target
        if before != ctx().quarantineDir():
            ctx().setQuarantineDir(before)
