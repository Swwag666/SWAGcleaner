"""Smoke-тесты UI-каркаса SWAGcleaner (offscreen, без реального окна).

Qt поднимается в offscreen-режиме (см. QT_QPA_PLATFORM в conftest.py),
поэтому тесты идут и в обычной консоли, и в CI.

Проверяем: локализацию и тему в контексте, сборку всех пяти вкладок,
доставку сигналов воркера.
"""
from __future__ import annotations

import typing as t

from PySide6.QtWidgets import QComboBox, QPushButton

from ui.context import ctx
from ui.tabs import AdvisorTab, CleanerTab, DedupTab, SettingsTab, TweaksTab
from ui.workers import AppWorker, WorkerPool, WorkerTask


def _buttons(widget: t.Any) -> t.List[QPushButton]:
    return widget.findChildren(QPushButton)


class TestContextLocale:
    def test_switch_to_ru(self, qapp: t.Any) -> None:
        ctx().setLocale("ru")
        assert ctx().locale() == "ru"

    def test_switch_to_en(self, qapp: t.Any) -> None:
        ctx().setLocale("en")
        assert ctx().locale() == "en"

    def test_unknown_locale_falls_back_to_ru(self, qapp: t.Any) -> None:
        ctx().setLocale("de")
        assert ctx().locale() == "ru"

    def test_tr_translates_between_locales(self, qapp: t.Any) -> None:
        ctx().setLocale("en")
        assert ctx().tr("app.title") == "SWAGcleaner"
        assert ctx().tr("tabs.advisor") == "Advisor"
        ctx().setLocale("ru")
        # Русский перевод есть и отличается от самого ключа.
        assert ctx().tr("tabs.advisor") != "tabs.advisor"
        assert ctx().tr("app.title") == "SWAGcleaner"

    def test_missing_key_returns_key(self, qapp: t.Any) -> None:
        assert ctx().tr("no.such.key") == "no.such.key"

    def test_supported_locales(self, qapp: t.Any) -> None:
        assert set(ctx().supportedLocales()) == {"ru", "en"}


class TestContextTheme:
    def test_theme_switch_is_remembered(self, qapp: t.Any) -> None:
        ctx().setTheme("light")
        assert ctx().theme() == "light"
        ctx().setTheme("dark")
        assert ctx().theme() == "dark"

    def test_theme_signal_emitted_once_per_change(self, qapp: t.Any) -> None:
        ctx().setTheme("dark")
        seen: t.List[str] = []
        ctx().themeChanged.connect(seen.append)
        ctx().setTheme("light")
        assert seen == ["light"]

    def test_invalid_theme_falls_back_to_dark(self, qapp: t.Any) -> None:
        ctx().setTheme("neon")
        assert ctx().theme() == "dark"


class TestTabs:
    def test_all_five_tabs_build(self, qapp: t.Any) -> None:
        for tab_cls in (AdvisorTab, CleanerTab, DedupTab, TweaksTab, SettingsTab):
            assert tab_cls() is not None

    def test_advisor_tab_texts_and_buttons(self, qapp: t.Any) -> None:
        ctx().setLocale("ru")
        tab = AdvisorTab()
        assert tab._title() == ctx().tr("advisor.title")
        assert tab._subheading() == ctx().tr("advisor.subtitle")
        assert tab._description() == ctx().tr("advisor.scan_hint")
        assert len(_buttons(tab)) == 2
        tab.setStatus("идёт скан")
        tab.setPlan("план готов")
        assert tab._status_label.text() == "идёт скан"
        assert tab._plan_area.text() == "план готов"

    def test_cleaner_tab_status_and_candidates(self, qapp: t.Any) -> None:
        tab = CleanerTab()
        tab.setStatus("идёт скан")
        tab.setCandidates("найдено 3")
        assert tab._status_label.text() == "идёт скан"
        assert tab._candidates_area.text() == "найдено 3"

    def test_dedup_tab_status_and_groups(self, qapp: t.Any) -> None:
        tab = DedupTab()
        tab.setStatus("сканирую")
        tab.setGroups("2 группы")
        assert tab._status_label.text() == "сканирую"
        assert tab._groups_area.text() == "2 группы"

    def test_tweaks_tab_has_four_buttons(self, qapp: t.Any) -> None:
        assert len(_buttons(TweaksTab())) == 4

    def test_settings_tab_has_language_and_theme_combos(self, qapp: t.Any) -> None:
        combos = SettingsTab().findChildren(QComboBox)
        assert len(combos) == 2


class TestWorkers:
    def test_pool_is_singleton_with_four_threads(self, qapp: t.Any) -> None:
        assert WorkerPool() is WorkerPool()
        assert WorkerPool().pool.maxThreadCount() == 4

    def test_task_defaults_and_args(self, qapp: t.Any) -> None:
        task = WorkerTask(func=lambda x: x + 1, args=(2,))
        assert task.func(*task.args, **task.kwargs) == 3

    def test_worker_emits_finished(self, qapp: t.Any) -> None:
        results: t.List[t.Any] = []
        errors: t.List[str] = []
        worker = AppWorker(WorkerTask(func=lambda: 42))
        worker._signals.finished.connect(results.append)
        worker._signals.error.connect(errors.append)
        worker.run()
        assert results == [42]
        assert errors == []

    def test_worker_emits_error(self, qapp: t.Any) -> None:
        def boom() -> None:
            raise ValueError("boom")

        errors: t.List[str] = []
        worker = AppWorker(WorkerTask(func=boom))
        worker._signals.error.connect(errors.append)
        worker.run()
        assert errors == ["boom"]

    def test_worker_in_real_thread_pool(self, qapp: t.Any) -> None:
        results: t.List[t.Any] = []
        worker = AppWorker(WorkerTask(func=lambda: "done"))
        worker._signals.finished.connect(results.append)
        pool = WorkerPool().pool
        pool.start(worker)
        assert pool.waitForDone(5000)
        qapp.processEvents()
        assert results == ["done"]
