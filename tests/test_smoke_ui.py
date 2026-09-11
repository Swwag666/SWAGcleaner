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
import struct

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QRawFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QWidget

from ui import sounds, theme
from ui.character import (
    MOODS,
    Assistant,
    Mascot,
    SpeechBox,
    available_moods,
    available_talk_frames,
    demo_moods,
    pose_path,
    talk_frame_paths,
)
from ui.context import ctx
from ui.dialog import ConfirmDialog
from ui.main import PAGES, MainWindow
from ui.widgets import AccentBar, AnimatedNumber, StatsRow
from ui.scene import SceneStack
from ui.sidebar import Sidebar
from ui.tabs import AdvisorTab, CleanerTab, DedupTab, SettingsTab, TweaksTab
from ui.workers import AppWorker, WorkerPool, WorkerTask


def _buttons(widget: t.Any) -> t.List[QPushButton]:
    return widget.findChildren(QPushButton)


@pytest.fixture
def win(qapp: t.Any) -> t.Any:
    """Главное окно на время одного теста.

    Окно показывается: под offscreen-платформой его никто не увидит, зато
    становится осмысленным isVisible() у страниц и по-настоящему считается
    разметка — именно это и проверяет часть тестов.
    """
    window = MainWindow(qapp, ctx())
    window.resize(1100, 700)
    window.show()
    QTest.qWait(80)
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

    def test_pixel_font_size_is_in_whole_pixels(self, qapp: t.Any) -> None:
        # Именно здесь был баг: размер задавался в пунктах, 16pt ≈ 21.33 px,
        # и большая часть штрихов попадала на дробные пиксели.
        font = theme.font_for("pixel")
        assert font.pixelSize() > 0
        assert font.pointSizeF() == -1

    def test_default_font_size_is_in_points(self, qapp: t.Any) -> None:
        font = theme.font_for("default")
        assert font.pointSize() > 0
        assert font.pixelSize() == -1

    @staticmethod
    def _distance(a: QColor, b: QColor) -> int:
        return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())

    def _blur_ratio(self, theme_name: str) -> float:
        """Доля штрихов, которые легли на пол-пикселя (0 — идеальные пиксели)."""
        colors = theme.palette(theme_name)
        background, foreground = QColor(colors["bg_panel"]), QColor(colors["text_primary"])
        image = QImage(560, 54, QImage.Format_ARGB32)
        image.fill(background)
        painter = QPainter(image)
        painter.setFont(theme.font_for("pixel", theme_name))
        painter.setPen(foreground)
        painter.drawText(4, 38, "Советник Чистка 12%")
        painter.end()

        ink = blurred = 0
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixelColor(x, y)
                if self._distance(pixel, background) <= 20:
                    continue
                ink += 1
                if self._distance(pixel, foreground) > 20:
                    blurred += 1
        assert ink > 100, "текст вообще не нарисовался"
        return blurred / ink

    def test_pixel_font_has_no_blurred_strokes(self, qapp: t.Any) -> None:
        for theme_name in ("dark", "light"):
            ratio = self._blur_ratio(theme_name)
            assert ratio == 0.0, f"в теме {theme_name} размыто {ratio:.0%} штрихов"

    def test_dark_theme_uses_bolder_pixel_font(self, qapp: t.Any) -> None:
        # Светлое на тёмном кажется тоньше, поэтому на тёмной теме вес выше.
        dark = theme.font_for("pixel", "dark").weight()
        light = theme.font_for("pixel", "light").weight()
        assert dark.value > light.value

    def test_default_font_is_not_bolded(self, qapp: t.Any) -> None:
        assert theme.font_for("default", "dark").weight().value == theme.font_for("default", "light").weight().value


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


class TestPaletteContrast:
    """Цвета текста должны оставаться читаемыми в обеих темах."""

    MIN_CONTRAST = 4.5
    MIN_ACCENT_CONTRAST = 3.0

    @staticmethod
    def _luminance(color: str) -> float:
        raw = color.lstrip("#")
        channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4) for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def _contrast(self, foreground: str, background: str) -> float:
        first, second = self._luminance(foreground), self._luminance(background)
        high, low = max(first, second), min(first, second)
        return (high + 0.05) / (low + 0.05)

    @pytest.mark.parametrize("theme_name", ("dark", "light"))
    @pytest.mark.parametrize(
        "role", ("text_primary", "text_secondary", "text_placeholder")
    )
    def test_text_is_readable_on_panel(self, theme_name: str, role: str) -> None:
        colors = theme.palette(theme_name)
        ratio = self._contrast(colors[role], colors["bg_panel"])
        assert ratio >= self.MIN_CONTRAST, f"{theme_name}/{role}: контраст всего {ratio:.1f}"

    @pytest.mark.parametrize("theme_name", ("dark", "light"))
    def test_accent_is_visible_on_panel(self, theme_name: str) -> None:
        colors = theme.palette(theme_name)
        ratio = self._contrast(colors["accent"], colors["bg_panel"])
        assert ratio >= self.MIN_ACCENT_CONTRAST, f"{theme_name}: акцент всего {ratio:.1f}"

    @pytest.mark.parametrize("theme_name", ("dark", "light"))
    def test_base_is_not_blindingly_bright(self, theme_name: str) -> None:
        # Светлая тема не должна быть чистым белым на весь экран.
        colors = theme.palette(theme_name)
        if theme_name == "light":
            assert self._luminance(colors["bg_base"]) < self._luminance("#f0f0f0")


class TestSceneStack:
    def test_starts_on_first_page(self, win: t.Any) -> None:
        assert win._stack.count() == 5
        assert win._stack.currentIndex() == 0
        assert win._stack.currentWidget() is win._stack.widget(0)

    def test_transition_settles_on_final_state(self, win: t.Any) -> None:
        win.go_to_page(2)
        page = win._stack.widget(2)
        QTest.qWait(SceneStack.TRANSITION_MS + 120)
        assert win._stack.currentIndex() == 2
        assert page.pos().x() == 0
        assert page.graphicsEffect().opacity() == 1.0

    def test_previous_page_is_hidden_after_transition(self, win: t.Any) -> None:
        win.go_to_page(3)
        QTest.qWait(SceneStack.TRANSITION_MS + 120)
        assert not win._stack.widget(0).isVisible()

    def test_page_slides_in_from_the_side(self, win: t.Any) -> None:
        win.go_to_page(1)
        page = win._stack.widget(1)
        # Сразу после переключения страница ещё смещена вправо и прозрачна.
        assert page.pos().x() > 0
        assert page.graphicsEffect().opacity() < 1.0
        QTest.qWait(SceneStack.TRANSITION_MS + 120)
        assert page.pos().x() == 0

    def test_rapid_switching_leaves_no_ghost_pages(self, win: t.Any) -> None:
        for index in (1, 3, 4, 2):
            win.go_to_page(index)
        QTest.qWait(SceneStack.TRANSITION_MS + 200)
        visible = [i for i, page in enumerate(win._stack._pages) if page.isVisible()]
        assert visible == [2]
        assert win._stack.widget(2).graphicsEffect().opacity() == 1.0
        assert win._stack.widget(2).pos().x() == 0

    def test_same_page_switch_is_ignored(self, win: t.Any) -> None:
        win.go_to_page(1)
        QTest.qWait(SceneStack.TRANSITION_MS + 120)
        before = win._stack.widget(1).graphicsEffect().opacity()
        win.go_to_page(1)
        assert win._stack.widget(1).graphicsEffect().opacity() == before

    def test_accent_bar_sweeps_under_the_header(self, win: t.Any) -> None:
        QTest.qWait(AccentBar.SWEEP_MS + 120)
        win.go_to_page(1)
        QTest.qWait(AccentBar.SWEEP_MS + 150)
        assert win._accent_bar.maximumWidth() == win._header.width()

    def test_accent_bar_restarts_on_every_section(self, win: t.Any) -> None:
        QTest.qWait(AccentBar.SWEEP_MS + 120)
        win.go_to_page(1)
        assert win._accent_bar.maximumWidth() < win._header.width()


class TestSidebar:
    def test_collapsed_state_shows_icons_only(self, win: t.Any) -> None:
        sidebar = win._sidebar
        sidebar.collapse()
        QTest.qWait(Sidebar.ANIMATION_MS + 120)
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


# ---------- персонаж и панель реплики ----------


class TestCharacterAssets:
    def test_scan_and_panic_poses_are_in_the_build(self, qapp: t.Any) -> None:
        moods = available_moods()
        assert moods["scan"] is not None
        assert moods["panic"] is not None

    def test_calm_pose_is_its_own_picture(self, qapp: t.Any) -> None:
        # Спокойная поза — отдельный арт, а не та же фотка с лупой.
        moods = available_moods()
        assert moods["calm"] is not None
        assert moods["calm"] != moods["scan"]
        assert moods["calm"].name == "calm.png"

    def test_magnifier_stays_on_the_scanning_pose(self, qapp: t.Any) -> None:
        # Поза с лупой — только у поиска мусора: покой и раздумья берут
        # спокойную картинку, иначе лупа висела бы на главной странице.
        moods = available_moods()
        assert moods["idle"] == moods["calm"]
        assert moods["think"] == moods["calm"]
        assert moods["scan"] != moods["calm"]

    def test_main_page_does_not_rest_in_scanning_pose(self, qapp: t.Any) -> None:
        from ui.main import PAGES

        advisor_mood = PAGES[0][3]
        assert advisor_mood != "scan"
        assert available_moods()[advisor_mood] == available_moods()["calm"]

    def test_every_pose_has_mouth_frames(self, qapp: t.Any) -> None:
        # Без кадров рта речь — только покачивание, и рот не открывается.
        talk = available_talk_frames()
        for mood, path in available_moods().items():
            if path is None:
                continue
            assert talk[mood] >= 2, f"у позы {mood} нет кадров речи"

    def test_talk_frames_keep_the_pose_size(self, qapp: t.Any) -> None:
        # Кадры речи должны совпадать с позой по холсту: иначе на 9 кадрах
        # в секунду фигура подпрыгивала бы при каждом открытии рта.
        for mood in demo_moods():
            pose = QImage(str(pose_path(mood)))
            for frame_path in talk_frame_paths(mood):
                frame = QImage(str(frame_path))
                assert not frame.isNull()
                assert frame.size() == pose.size()

    def test_talk_frames_differ_only_around_the_mouth(self, qapp: t.Any) -> None:
        # Разница между открытым и закрытым ртом должна сидеть в области
        # лица: если она расползлась по кадру, кадры готовили вразнобой.
        for mood in demo_moods():
            frames = talk_frame_paths(mood)
            closed = QImage(str(frames[0])).convertToFormat(QImage.Format.Format_ARGB32)
            opened = QImage(str(frames[-1])).convertToFormat(QImage.Format.Format_ARGB32)
            xs: t.List[int] = []
            ys: t.List[int] = []
            for y in range(0, closed.height(), 2):
                for x in range(0, closed.width(), 2):
                    a = closed.pixelColor(x, y)
                    b = opened.pixelColor(x, y)
                    delta = (
                        abs(a.red() - b.red())
                        + abs(a.green() - b.green())
                        + abs(a.blue() - b.blue())
                        + abs(a.alpha() - b.alpha())
                    )
                    if delta > 30:
                        xs.append(x)
                        ys.append(y)
            assert xs, f"у позы {mood} открытый и закрытый рот одинаковые"
            box = (max(xs) - min(xs)) / closed.width(), (max(ys) - min(ys)) / closed.height()
            assert box[0] < 0.35, f"{mood}: разница по ширине {box}"
            assert box[1] < 0.35, f"{mood}: разница по высоте {box}"
            # И это именно лицо, а не угол кадра.
            center_x = (min(xs) + max(xs)) / 2 / closed.width()
            center_y = (min(ys) + max(ys)) / 2 / closed.height()
            assert 0.35 < center_x < 0.85, f"{mood}: рот не там, где лицо ({center_x})"
            assert center_y < 0.55, f"{mood}: рот ниже лица ({center_y})"

    def test_unknown_mood_has_no_file(self, qapp: t.Any) -> None:
        assert pose_path("неведомое") is None

    def test_poses_have_cut_out_background(self, qapp: t.Any) -> None:
        for mood in demo_moods():
            image = QImage(str(available_moods()[mood]))
            assert not image.isNull()
            assert image.hasAlphaChannel()
            # Фон вырезан: углы должны быть прозрачными, иначе на тёмной
            # теме вокруг персонажа был бы белый квадрат.
            for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1)):
                assert image.pixelColor(x, y).alpha() < 32


class TestMascot:
    def test_default_mood_is_idle(self, qapp: t.Any) -> None:
        assert Mascot().mood() == "idle"

    def test_unknown_mood_falls_back_to_idle(self, qapp: t.Any) -> None:
        mascot = Mascot()
        mascot.set_mood("неведомое")
        assert mascot.mood() == "idle"

    def test_mood_switch_keeps_pose_available(self, qapp: t.Any) -> None:
        mascot = Mascot()
        for mood in MOODS:
            mascot.set_mood(mood)
            assert mascot.mood() == mood

    def test_speaking_toggles_with_typing(self, qapp: t.Any) -> None:
        speech = SpeechBox()
        mascot = Mascot()
        Assistant(mascot, speech)
        speech.say("Привет")
        assert mascot.is_speaking()
        speech.finish_typing()
        assert not mascot.is_speaking()

    def test_mascot_paints_something_visible(self, win: t.Any) -> None:
        win._mascot.set_mood("panic")
        image = QImage(win._mascot.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        win._mascot.render(image)
        ink = sum(
            1
            for y in range(0, image.height(), 6)
            for x in range(0, image.width(), 6)
            if image.pixelColor(x, y).alpha() > 0
        )
        assert ink > 0


class TestSpeechBox:
    def test_text_is_typed_letter_by_letter(self, qapp: t.Any) -> None:
        box = SpeechBox()
        box.say("Привет, мир")
        assert box.is_typing()
        assert box.shown_text() == ""
        QTest.qWait(80)
        assert 0 < len(box.shown_text()) < len(box.full_text())
        box.finish_typing()
        assert box.shown_text() == box.full_text()
        assert not box.is_typing()

    def test_first_click_skips_typing_second_asks_for_more(self, qapp: t.Any) -> None:
        box = SpeechBox()
        asked: t.List[bool] = []
        box.advanced.connect(lambda: asked.append(True))
        box.say("Раз, два, три")
        box.advance()
        assert box.shown_text() == box.full_text()
        assert asked == []
        box.advance()
        assert asked == [True]

    def test_empty_line_types_nothing(self, qapp: t.Any) -> None:
        box = SpeechBox()
        box.say("")
        assert box.full_text() == ""
        assert not box.is_typing()
        assert box.shown_text() == ""

    def test_name_and_tooltip_follow_locale(self, qapp: t.Any) -> None:
        box = SpeechBox()
        assert box._name.text() == ctx().tr("character.name")
        assert ctx().tr("character.hint") in box.toolTip()
        ctx().setLocale("en")
        box.retranslate()
        assert box._name.text() == "Clini"
        ctx().setLocale("ru")

    def test_every_character_line_is_translated(self, qapp: t.Any) -> None:
        for locale in ("ru", "en"):
            for key in (
                "hello",
                "advisor",
                "cleaner",
                "dedup",
                "tweaks",
                "settings",
                "scan",
                "clean",
                "calm",
                "panic",
            ):
                value = ctx().tr(f"character.lines.{key}", locale)
                assert value != f"character.lines.{key}"
                assert value.strip()


class TestAssistantQueue:
    def test_script_advances_on_click(self, qapp: t.Any) -> None:
        speech = SpeechBox()
        mascot = Mascot()
        assistant = Assistant(mascot, speech)
        assistant.play([("scan", "первая"), ("panic", "вторая")])
        assert speech.full_text() == "первая"
        assert mascot.mood() == "scan"
        assert assistant.pending() == 1
        speech.finish_typing()
        speech.advance()
        assert speech.full_text() == "вторая"
        assert mascot.mood() == "panic"
        assert assistant.pending() == 0

    def test_say_replaces_the_queue(self, qapp: t.Any) -> None:
        speech = SpeechBox()
        mascot = Mascot()
        assistant = Assistant(mascot, speech)
        assistant.play([("scan", "первая"), ("panic", "вторая")])
        assistant.say("одна", "calm")
        assert assistant.pending() == 0
        assert speech.full_text() == "одна"
        assert mascot.mood() == "calm"

    def test_advance_on_empty_queue_keeps_last_line(self, qapp: t.Any) -> None:
        speech = SpeechBox()
        assistant = Assistant(Mascot(), speech)
        assistant.say("последняя", "idle")
        speech.finish_typing()
        speech.advance()
        assert speech.full_text() == "последняя"


class TestWindowAssistant:
    def test_window_starts_with_a_greeting(self, win: t.Any) -> None:
        assert win._speech.full_text() == ctx().tr("character.lines.hello")

    def test_each_page_has_its_line_and_mood(self, win: t.Any) -> None:
        for index in (1, 2, 3, 4, 0):
            win.go_to_page(index)
            name, _key, _page_cls, mood = PAGES[index]
            assert win._speech.full_text() == ctx().tr(f"character.lines.{name}")
            assert win._mascot.mood() == mood

    @staticmethod
    def _in_window(win: t.Any, widget: t.Any) -> QRect:
        """Геометрия виджета в координатах окна."""
        return widget.geometry().translated(widget.mapTo(win, QPoint(0, 0)))

    def test_character_stands_beside_the_page_not_on_it(self, win: t.Any) -> None:
        mascot = self._in_window(win, win._mascot)
        speech = self._in_window(win, win._speech)
        stack = self._in_window(win, win._stack)
        # Персонаж и панель реплики не должны перекрываться между собой…
        assert not mascot.intersects(speech)
        # …а колонка персонажа не должна наезжать на страницу раздела.
        assert mascot.left() >= stack.right() - 1

    def test_hide_and_show_assistant(self, win: t.Any) -> None:
        page_width = win._stack.width()
        win.toggle_assistant()
        QTest.qWait(MainWindow.ASSISTANT_ANIMATION_MS + 140)
        assert not win.assistant_visible()
        assert not win._mascot.isVisible()
        assert not win._speech.isVisible()
        assert win._stack.width() > page_width

        win.toggle_assistant()
        QTest.qWait(MainWindow.ASSISTANT_ANIMATION_MS + 140)
        assert win.assistant_visible()
        assert win._mascot.isVisible()
        assert win._speech.isVisible()
        assert win._mascot.width() == win._character_width()

    def test_assistant_button_has_hint(self, win: t.Any) -> None:
        assert win._assistant_button.toolTip() == ctx().tr("header.assistant_hide")
        win.toggle_assistant()
        assert win._assistant_button.toolTip() == ctx().tr("header.assistant_show")
        win.toggle_assistant()

    def test_cycle_mood_walks_through_every_drawn_pose(self, win: t.Any) -> None:
        # Листаются только позы со своим артом: idle, think и calm рисуются
        # одной картинкой, показывать её трижды подряд незачем.
        order = demo_moods()
        assert order
        seen = {win.cycle_mood() for _ in range(len(order))}
        assert seen == set(order)

    def test_cycle_mood_never_repeats_the_same_picture(self, win: t.Any) -> None:
        pictures = []
        for _ in range(len(demo_moods())):
            pictures.append(pose_path(win.cycle_mood()))
        assert len(set(pictures)) == len(pictures)

    def test_every_mood_from_the_core_is_still_accepted(self, win: t.Any) -> None:
        # Демонстрация сузилась до нарисованных поз, но словарь настроений
        # для ядра остался полным: скан, раздумья и покой никуда не делись.
        for mood in MOODS:
            win._mascot.set_mood(mood)
            assert win._mascot.mood() == mood

    def test_context_can_order_a_line_and_a_mood(self, win: t.Any) -> None:
        ctx().say("Реплика из ядра")
        assert win._speech.full_text() == "Реплика из ядра"
        ctx().setAssistantMood("panic")
        assert win._mascot.mood() == "panic"

    def test_language_change_repeats_greeting_in_new_language(self, win: t.Any) -> None:
        ru_line = win._speech.full_text()
        ctx().setLocale("en")
        assert win._speech.full_text() == ctx().tr("character.lines.hello")
        assert win._speech.full_text() != ru_line


# ---------- живая полоска, числа и подтверждение ----------


class TestAccentBar:
    def test_sweep_ends_with_full_width(self, qapp: t.Any) -> None:
        bar = AccentBar()
        bar.resize(400, 2)
        bar.sweep(400)
        QTest.qWait(AccentBar.SWEEP_MS + 80)
        assert bar.maximumWidth() == 400
        assert not bar.is_sweeping()

    def test_busy_mode_runs_a_segment(self, qapp: t.Any) -> None:
        host = QWidget()
        host.resize(500, 400)
        bar = AccentBar(host)
        bar.resize(500, 2)
        assert not bar.is_busy()
        bar.set_busy(True)
        assert bar.is_busy()
        assert bar.property("mode") == "busy"
        assert bar.maximumWidth() == host.width()
        assert bar._timer.isActive()
        bar.set_busy(False)
        assert bar.property("mode") == "normal"
        assert not bar._timer.isActive()

    def test_bar_keeps_up_with_the_header(self, win: t.Any) -> None:
        # На старте шапка ещё не разложена, и полоска однажды застревала
        # шириной 100 пикселей на всю сессию — проверяем, что не застревает.
        QTest.qWait(AccentBar.SWEEP_MS + 120)
        assert win._accent_bar.maximumWidth() == win._header.width()
        win.resize(1240, 720)
        QTest.qWait(150)
        assert win._accent_bar.maximumWidth() == win._header.width()
        assert win._accent_bar.width() == win._header.width()

    def test_busy_bar_paints_without_errors(self, qapp: t.Any) -> None:
        host = QWidget()
        host.resize(300, 200)
        bar = AccentBar(host)
        # Ширину выставляем после set_busy: до этого полоска намеренно
        # сжата в ноль (maximumWidth == 0), пока не проедет первый раз.
        bar.set_busy(True)
        bar.resize(300, 2)
        bar.apply_colors(theme.palette("dark"))
        assert bar.width() == 300
        image = QImage(bar.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        bar.render(image)
        ink = sum(
            1 for x in range(image.width()) if image.pixelColor(x, 0).alpha() > 0
        )
        assert ink > 0


class TestAnimatedNumber:
    def test_number_creeps_up_instead_of_appearing(self, qapp: t.Any) -> None:
        number = AnimatedNumber()
        number.setValue(1000)
        assert number.value() == 1000
        assert number.is_animating()
        QTest.qWait(AnimatedNumber.STEP_MS * 3)
        assert 0 < number.shown_value() < 1000
        number.finish()
        assert number.shown_value() == 1000
        assert not number.is_animating()

    def test_thousands_are_grouped_and_decimals_kept(self, qapp: t.Any) -> None:
        number = AnimatedNumber()
        number.setValue(1284)
        number.finish()
        assert number.text() == "1 284"
        number.setValue(2412.5, decimals=1)
        number.finish()
        assert number.text() == "2 412.5"

    def test_same_value_does_not_restart_animation(self, qapp: t.Any) -> None:
        number = AnimatedNumber()
        number.setValue(7)
        number.finish()
        number.setValue(7)
        assert not number.is_animating()


class TestStatsRow:
    def test_tiles_keep_values_and_keys(self, qapp: t.Any) -> None:
        row = StatsRow()
        row.add("apps", "advisor.stats_apps")
        row.add("size", "cleaner.stats_size", "cleaner.size_mb", 1)
        assert row.keys() == ("apps", "size")
        assert not row.has_values()
        row.setValue("apps", 36)
        row.setValue("size", 2412.0)
        assert row.value("apps") == 36
        assert row.value("size") == 2412.0
        assert row.has_values()
        row.setValue("нет такого", 5)

    def test_captions_follow_locale(self, qapp: t.Any) -> None:
        row = StatsRow()
        tile = row.add("apps", "advisor.stats_apps")
        ctx().setLocale("en")
        row.retranslate()
        assert "apps" not in row.tile("apps")._caption.text()
        assert row.tile("apps")._caption.text() == ctx().tr("advisor.stats_apps")
        ctx().setLocale("ru")
        row.retranslate()
        assert tile._caption.text() == ctx().tr("advisor.stats_apps")


class TestConfirmDialog:
    ITEMS = [("Первое действие", "low"), ("Второе действие", "medium")]

    def test_dialog_lists_every_action(self, win: t.Any) -> None:
        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        QTest.qWait(40)
        assert dialog.count() == len(self.ITEMS)
        assert [text for text, _risk in dialog.items()] == [t for t, _ in self.ITEMS]
        assert len(dialog.findChildren(QLabel)) >= len(self.ITEMS)
        dialog.deleteLater()

    def test_scrim_color_comes_from_palette(self, qapp: t.Any) -> None:
        for theme_name in theme.PALETTES:
            color = QColor(theme.palette(theme_name)["scrim"])
            assert color.isValid()
            assert color.alpha() > 0

    def test_panel_covers_the_bottom_of_the_window(self, win: t.Any) -> None:
        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        QTest.qWait(ConfirmDialog.ANIMATION_MS + 60)
        panel = dialog.panel().geometry()
        assert dialog.rect().contains(panel)
        assert panel.left() >= ConfirmDialog.PANEL_MARGIN - 1
        assert panel.bottom() <= dialog.height() - ConfirmDialog.PANEL_MARGIN + 1
        dialog.deleteLater()

    def test_buttons_confirm_and_cancel(self, win: t.Any) -> None:
        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        dialog.cancel_button().click()
        assert dialog.result() == ConfirmDialog.DialogCode.Rejected
        dialog.deleteLater()

        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        dialog.confirm_button().click()
        assert dialog.result() == ConfirmDialog.DialogCode.Accepted
        dialog.deleteLater()

    def test_escape_cancels_and_enter_confirms(self, win: t.Any) -> None:
        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.result() == ConfirmDialog.DialogCode.Rejected
        dialog.deleteLater()

        dialog = ConfirmDialog(win, self.ITEMS)
        dialog.show()
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        assert dialog.result() == ConfirmDialog.DialogCode.Accepted
        dialog.deleteLater()

    def test_ask_returns_the_pressed_answer(self, win: t.Any) -> None:
        def press(confirm: bool) -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, ConfirmDialog):
                    widget.confirm() if confirm else widget.cancel()

        QTimer.singleShot(60, lambda: press(True))
        assert ConfirmDialog.ask(win, self.ITEMS) is True
        QTimer.singleShot(60, lambda: press(False))
        assert ConfirmDialog.ask(win, self.ITEMS) is False

    def test_demo_items_are_translated(self, qapp: t.Any) -> None:
        for locale in ("ru", "en"):
            for key in ("tmp", "startup", "photo"):
                value = ctx().tr(f"demo.items.{key}", locale)
                assert value != f"demo.items.{key}"
                assert value.strip()


# ---------- звуки ----------


class _RecordingPlayer(sounds.SoundPlayer):
    """Проигрыватель-шпион: помнит, какие звуки просили сыграть."""

    def __init__(self) -> None:
        super().__init__(True)
        self.events: t.List[str] = []

    def play(self, event: str) -> None:
        self.events.append(event)


class TestSounds:
    def test_generated_blip_is_a_valid_wav(self, qapp: t.Any) -> None:
        data = sounds.wav_bytes(880.0, 0.02)
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WAVE"
        assert data[12:16] == b"fmt "
        assert data[36:40] == b"data"
        assert struct.unpack("<I", data[4:8])[0] == len(data) - 8
        assert struct.unpack("<I", data[40:44])[0] == len(data) - 44

    def test_waveform_changes_with_length(self, qapp: t.Any) -> None:
        short = sounds.wav_bytes(440.0, 0.01)
        long = sounds.wav_bytes(440.0, 0.05)
        assert len(long) > len(short)

    def test_disabled_player_stays_silent(self, qapp: t.Any) -> None:
        player = sounds.SoundPlayer(False)
        player.play("дичь")  # даже неизвестное событие не должно падать
        assert player.isEnabled() is False
        assert player._cache == {}

    def test_every_event_has_a_tone(self, qapp: t.Any) -> None:
        for name in ("click", "page", "done", "cancel"):
            frequency, seconds = sounds.EVENTS[name]
            assert frequency > 0 and seconds > 0

    def test_click_filter_skips_navigation_items(
        self, qapp: t.Any, monkeypatch: t.Any
    ) -> None:
        # Подменяем проигрыватель шпионом и возвращаем настоящий после теста.
        recorder = _RecordingPlayer()
        monkeypatch.setattr(sounds, "_player", recorder)

        filter_ = sounds.ClickSoundFilter()
        button = QPushButton("действие")
        filter_.eventFilter(button, QEvent(QEvent.Type.MouseButtonPress))
        assert recorder.events == ["click"]

        nav = QPushButton("раздел")
        nav.setObjectName("navItem")
        filter_.eventFilter(nav, QEvent(QEvent.Type.MouseButtonPress))
        assert recorder.events == ["click"]

        filter_.eventFilter(QLabel("подпись"), QEvent(QEvent.Type.MouseButtonPress))
        assert recorder.events == ["click"]

        filter_.eventFilter(button, QEvent(QEvent.Type.KeyPress))
        assert recorder.events == ["click"]

    def test_settings_checkbox_turns_sounds_off_and_on(self, win: t.Any) -> None:
        page = win._pages[4]
        ctx().setSounds(True)
        page.retranslate()
        assert page._sounds_check.isChecked()
        assert sounds.player().isEnabled()

        page._sounds_check.setChecked(False)
        assert not ctx().soundsEnabled()
        assert not sounds.player().isEnabled()

        page._sounds_check.setChecked(True)
        assert ctx().soundsEnabled()
        assert sounds.player().isEnabled()
        ctx().setSounds(False)


# ---------- показ работы на страницах ----------


class TestWorkFlow:
    def test_every_page_has_its_own_stat_tiles(self, win: t.Any) -> None:
        advisor = win._pages[0].stats()
        assert advisor.keys() == ("apps", "recs")
        assert win._pages[1].stats().keys() == ("candidates", "size")
        assert win._pages[2].stats().keys() == ("groups", "dupes")
        # У твиков и настроек показателей нет — это нормально.
        assert win._pages[3].stats().keys() == ()
        assert win._pages[4].stats().keys() == ()

    def test_demo_numbers_match_the_tiles(self, win: t.Any) -> None:
        for index, (name, _key, _cls, _mood) in enumerate(PAGES):
            page = win._pages[index]
            for key in MainWindow.DEMO_RESULTS.get(name, {}):
                assert key in page.stats().keys()

    def test_progress_hides_by_default(self, win: t.Any) -> None:
        for page in win._pages:
            assert not page.progress().isVisible()

    def test_scan_button_runs_the_whole_pipeline(self, win: t.Any) -> None:
        win.go_to_page(1)
        page = win._pages[1]
        page.scanRequested.emit()
        assert win.is_busy()
        assert win._accent_bar.is_busy()
        assert page.progress().isVisible()
        assert page.progress().value() == 0
        assert win._speech.full_text() == ctx().tr("character.lines.scan")

        QTest.qWait(MainWindow.DEMO_WORK_MS + 400)
        assert not win.is_busy()
        assert not page.progress().isVisible()
        assert page.stats().value("candidates") == MainWindow.DEMO_RESULTS["cleaner"]["candidates"]
        assert page.stats().value("size") == MainWindow.DEMO_RESULTS["cleaner"]["size"]
        assert page._status_label.text() == ctx().tr("cleaner.scan_done")
        assert win._mascot.mood() == "calm"

    def test_second_action_waits_while_busy(self, win: t.Any) -> None:
        win.go_to_page(1)
        page = win._pages[1]
        page.scanRequested.emit()
        assert win.is_busy()
        progress = page.progress().value()
        page.scanRequested.emit()
        assert page.progress().value() == progress
        QTest.qWait(MainWindow.DEMO_WORK_MS + 400)

    def test_action_button_asks_for_confirmation(self, win: t.Any) -> None:
        win.go_to_page(1)
        page = win._pages[1]

        def answer(confirm: bool) -> None:
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, ConfirmDialog):
                    widget.confirm() if confirm else widget.cancel()

        # Отмена: работы нет, персонаж успокаивается.
        QTimer.singleShot(60, lambda: answer(False))
        page.cleanRequested.emit()
        assert not win.is_busy()
        assert win._speech.full_text() == ctx().tr("character.lines.cancelled")

        # Подтверждение: начинается работа и заполняются показатели.
        QTimer.singleShot(60, lambda: answer(True))
        page.cleanRequested.emit()
        assert win.is_busy()
        QTest.qWait(MainWindow.DEMO_WORK_MS + 400)
        assert not win.is_busy()
        assert page.stats().value("candidates") == MainWindow.DEMO_RESULTS["cleaner"]["candidates"]


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
