"""Smoke-тесты интерфейса SWAGcleaner (offscreen, без реального окна).

Qt поднимается в offscreen-режиме (см. QT_QPA_PLATFORM в conftest.py),
поэтому тесты идут и в обычной консоли, и в CI.

Проверяем то, что легко сломать незаметно: переключение языка, темы и шрифта,
поведение бокового меню (свёрнуто/развёрнуто, задержка сворачивания)
и то, что пиксельный шрифт действительно рисует кириллицу.
"""
from __future__ import annotations

import typing as t

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QFont, QRawFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from ui import theme
from ui.context import ctx
from ui.main import MainWindow
from ui.sidebar import Sidebar
from ui.tabs import AdvisorTab, CleanerTab, DedupTab, SettingsTab, TweaksTab
from ui.workers import AppWorker, WorkerPool, WorkerTask


def _buttons(widget: t.Any) -> t.List[QPushButton]:
    return widget.findChildren(QPushButton)


@pytest.fixture
def win(qapp: t.Any) -> t.Any:
    """Главное окно на время одного теста."""
    window = MainWindow(qapp, ctx())
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()
    # Вернуть состояние в исходное, чтобы тесты не влияли друг на друга.
    ctx().setLocale("ru")
    ctx().setTheme("dark")
    ctx().setFontKind("pixel")


# ---------- локализация ----------


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
        assert ctx().tr("tabs.advisor") != "tabs.advisor"
        assert ctx().tr("app.title") == "SWAGcleaner"

    def test_missing_key_returns_key(self, qapp: t.Any) -> None:
        assert ctx().tr("no.such.key") == "no.such.key"

    def test_supported_locales(self, qapp: t.Any) -> None:
        assert set(ctx().supportedLocales()) == {"ru", "en"}

    def test_both_locales_have_same_keys(self, qapp: t.Any) -> None:
        def flatten(data: t.Dict[str, t.Any], prefix: str = "") -> t.Set[str]:
            keys: t.Set[str] = set()
            for key, value in data.items():
                full = f"{prefix}{key}"
                if isinstance(value, dict):
                    keys |= flatten(value, full + ".")
                else:
                    keys.add(full)
            return keys

        ru = flatten(ctx().all("ru"))
        en = flatten(ctx().all("en"))
        assert ru == en


# ---------- тема ----------


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

    def test_both_palettes_have_same_colors(self, qapp: t.Any) -> None:
        assert set(theme.PALETTES["dark"]) == set(theme.PALETTES["light"])

    def test_stylesheet_differs_between_themes(self, qapp: t.Any) -> None:
        assert theme.qss("dark") != theme.qss("light")


# ---------- шрифт ----------


class TestFonts:
    def test_pixel_font_is_bundled(self, qapp: t.Any) -> None:
        assert theme.pixel_font_available(), "Handjet не подключился из assets/fonts"

    def test_pixel_font_covers_cyrillic(self, qapp: t.Any) -> None:
        # inFont()/supportsCharacter() для вариативных шрифтов врут,
        # поэтому проверяем наличие реальных глифов.
        raw = QRawFont.fromFont(QFont(theme.resolve_family("pixel"), 20))
        assert raw.familyName().lower() == "handjet"
        for char in "АяёЖПрСоветник":
            assert raw.glyphIndexesForString(char)[0] != 0, f"нет глифа для {char!r}"

    def test_default_font_is_not_pixel(self, qapp: t.Any) -> None:
        assert theme.resolve_family("default").lower() != "handjet"

    def test_font_kind_switch_and_signal(self, qapp: t.Any) -> None:
        ctx().setFontKind("pixel")
        seen: t.List[str] = []
        ctx().fontChanged.connect(seen.append)
        ctx().setFontKind("default")
        assert ctx().fontKind() == "default"
        assert seen == ["default"]

    def test_invalid_font_kind_falls_back_to_pixel(self, qapp: t.Any) -> None:
        ctx().setFontKind("comic-sans")
        assert ctx().fontKind() == "pixel"

    def test_pixel_font_is_taller_than_default(self, qapp: t.Any) -> None:
        pixel = theme.fonts_sizes("pixel")["base"]
        default = theme.fonts_sizes("default")["base"]
        assert pixel > default


# ---------- страницы ----------


class TestPages:
    def test_all_five_pages_build(self, qapp: t.Any) -> None:
        for page_cls in (AdvisorTab, CleanerTab, DedupTab, TweaksTab, SettingsTab):
            assert page_cls() is not None

    def test_advisor_page_content(self, qapp: t.Any) -> None:
        ctx().setLocale("ru")
        page = AdvisorTab()
        assert page._title() == ctx().tr("advisor.title")
        assert len(_buttons(page)) == 2
        page.setStatus("идёт скан")
        page.setPlan("план готов")
        assert page._status_label.text() == "идёт скан"
        assert page._plan_area.text() == "план готов"

    def test_cleaner_page_content(self, qapp: t.Any) -> None:
        page = CleanerTab()
        page.setStatus("идёт скан")
        page.setCandidates("найдено 3")
        assert page._status_label.text() == "идёт скан"
        assert page._candidates_area.text() == "найдено 3"

    def test_dedup_page_content(self, qapp: t.Any) -> None:
        page = DedupTab()
        page.setStatus("сканирую")
        page.setGroups("2 группы")
        assert page._status_label.text() == "сканирую"
        assert page._groups_area.text() == "2 группы"

    def test_tweaks_page_has_four_buttons(self, qapp: t.Any) -> None:
        assert len(_buttons(TweaksTab())) == 4

    def test_settings_page_has_three_controls(self, qapp: t.Any) -> None:
        combos = SettingsTab().findChildren(QComboBox)
        assert len(combos) == 3  # язык, тема, шрифт

    def test_settings_switches_language(self, qapp: t.Any) -> None:
        ctx().setLocale("ru")
        page = SettingsTab()
        page._lang_combo.setCurrentIndex(page._lang_combo.findData("en"))
        assert ctx().locale() == "en"
        page.deleteLater()

    def test_settings_switches_theme(self, qapp: t.Any) -> None:
        ctx().setTheme("dark")
        page = SettingsTab()
        page._theme_combo.setCurrentIndex(page._theme_combo.findData("light"))
        assert ctx().theme() == "light"
        page.deleteLater()

    def test_settings_switches_font(self, qapp: t.Any) -> None:
        ctx().setFontKind("pixel")
        page = SettingsTab()
        page._font_combo.setCurrentIndex(page._font_combo.findData("default"))
        assert ctx().fontKind() == "default"
        page.deleteLater()

    def test_retranslate_changes_page_text(self, qapp: t.Any) -> None:
        ctx().setLocale("ru")
        page = AdvisorTab()
        ru_title = page._title_label.text()
        ctx().setLocale("en")
        page.retranslate()
        assert page._title_label.text() != ru_title
        assert page._title_label.text() == ctx().tr("advisor.title")
        assert page._buttons[0][0].text() == ctx().tr("advisor.scan_button")
        page.deleteLater()


# ---------- боковое меню ----------


class TestSidebar:
    def test_starts_collapsed_with_icons_only(self, win: t.Any) -> None:
        sidebar = win._sidebar
        assert not sidebar.is_expanded()
        assert sidebar.count() == 5
        assert sidebar.minimumWidth() == Sidebar.COLLAPSED_WIDTH
        assert all(button.text() == "" for button in sidebar._items)

    def test_every_item_has_icon_and_tooltip(self, win: t.Any) -> None:
        for button, key in zip(win._sidebar._items, win._sidebar._text_keys):
            assert not button.icon().isNull()
            assert button.toolTip() == ctx().tr(key)

    def test_expand_animates_to_full_width_and_shows_labels(self, win: t.Any) -> None:
        sidebar = win._sidebar
        sidebar.expand()
        QTest.qWait(Sidebar.ANIMATION_MS + 80)
        assert sidebar.is_expanded()
        assert sidebar.width() == Sidebar.EXPANDED_WIDTH
        assert sidebar._items[0].text() == ctx().tr("tabs.advisor")

    def test_leave_starts_collapse_timer(self, win: t.Any) -> None:
        sidebar = win._sidebar
        sidebar.expand()
        QTest.qWait(Sidebar.ANIMATION_MS + 80)
        QApplication.sendEvent(sidebar, QEvent(QEvent.Type.Leave))
        assert sidebar._collapse_timer.isActive()

    def test_enter_cancels_pending_collapse(self, win: t.Any) -> None:
        sidebar = win._sidebar
        sidebar.expand()
        QTest.qWait(Sidebar.ANIMATION_MS + 80)
        QApplication.sendEvent(sidebar, QEvent(QEvent.Type.Leave))
        assert sidebar._collapse_timer.isActive()
        QApplication.sendEvent(sidebar, QEvent(QEvent.Type.Enter))
        assert not sidebar._collapse_timer.isActive()

    def test_collapses_back_to_icon_rail(self, win: t.Any) -> None:
        sidebar = win._sidebar
        sidebar.expand()
        QTest.qWait(Sidebar.ANIMATION_MS + 80)
        sidebar.collapse()
        QTest.qWait(Sidebar.ANIMATION_MS + 120)
        assert not sidebar.is_expanded()
        assert sidebar.width() == Sidebar.COLLAPSED_WIDTH
        assert all(button.text() == "" for button in sidebar._items)

    def test_click_selects_page_and_emits(self, win: t.Any) -> None:
        seen: t.List[int] = []
        win._sidebar.pageSelected.connect(seen.append)
        win._sidebar._on_item_clicked(2)
        assert seen == [2]
        assert win._stack.currentIndex() == 2
        assert win._sidebar.current_index() == 2


# ---------- главное окно ----------


class TestMainWindow:
    def test_window_has_five_pages_and_nav_items(self, win: t.Any) -> None:
        assert win._stack.count() == 5
        assert win._sidebar.count() == 5

    def test_go_to_page_switches_stack(self, win: t.Any) -> None:
        win.go_to_page(3)
        assert win._stack.currentIndex() == 3
        assert win._sidebar.current_index() == 3

    def test_header_buttons_toggle_theme_and_language(self, win: t.Any) -> None:
        ctx().setTheme("dark")
        ctx().setLocale("ru")
        win.toggle_theme()
        assert ctx().theme() == "light"
        win.toggle_language()
        assert ctx().locale() == "en"
        assert win._language_button.text() == "EN"

    def test_theme_change_repaints_interface(self, win: t.Any) -> None:
        ctx().setTheme("dark")
        assert win._app.styleSheet() == theme.qss("dark", ctx().fontKind())
        ctx().setTheme("light")
        assert win._app.styleSheet() == theme.qss("light", ctx().fontKind())

    def test_font_change_repaints_interface(self, win: t.Any) -> None:
        ctx().setFontKind("default")
        assert win._app.styleSheet() == theme.qss(ctx().theme(), "default")
        assert win._app.font().family().lower() != "handjet"
        ctx().setFontKind("pixel")
        assert win._app.font().family().lower() == "handjet"

    def test_retranslate_updates_header_and_sidebar(self, win: t.Any) -> None:
        ctx().setLocale("ru")
        win.retranslate()
        ru_sub = win._header_sub.text()
        assert ru_sub == ctx().tr("app.subtitle")
        assert win._sidebar._caption_text == ctx().tr("sidebar.caption")
        ctx().setLocale("en")
        win.retranslate()
        assert win._header_sub.text() != ru_sub
        assert win._header_title.text() == "SWAGcleaner"

    def test_window_title_contains_app_name(self, win: t.Any) -> None:
        win.retranslate()
        assert "SWAGcleaner" in win.windowTitle()

    def test_status_keeps_value(self, win: t.Any) -> None:
        win.setStatus("работаю")
        assert win.status() == "работаю"


# ---------- воркеры ----------


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
