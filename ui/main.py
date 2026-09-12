"""Главное окно SWAGcleaner.

Слева — сворачивающееся боковое меню (Sidebar), справа — шапка с быстрыми
переключателями и стек страниц. Меню и шапка всегда на месте, меняется
только содержимое.

Справа от страницы стоит помощница, под ней — панель реплики (ui.character):
при смене раздела она комментирует происходящее и меняет позу. Реплику и
настроение можно заказать через Context, не зная про эти виджеты.

Смена языка, темы и шрифта идёт через Context: окно подписывается на его
сигналы и целиком перерисовывает себя, поэтому переключатели в шапке и в
настройках всегда дают одинаковый результат.
"""
from __future__ import annotations

import logging
from typing import List

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ui import icons, sounds, theme
from ui.character import MOODS, Assistant, Mascot, SpeechBox, demo_moods
from ui.context import Context
from ui.dialog import ConfirmDialog
from ui.scene import SceneStack
from ui.sidebar import Sidebar
from ui.tabs import AdvisorTab, CleanerTab, DedupTab, SettingsTab, TweaksTab
from ui.widgets import AccentBar

_LOGGER = logging.getLogger("swag.ui.main")

# Разделы приложения: имя (оно же ключ реплики персонажа), ключ названия,
# класс страницы и настроение помощницы на этом разделе.
# В покое она стоит без лупы (idle): лупа появляется только на время работы,
# позу «scan» ставит _start_work, а по окончании работы помощница возвращается в idle.
PAGES = (
    ("advisor", "tabs.advisor", AdvisorTab, "idle"),
    ("cleaner", "tabs.cleaner", CleanerTab, "idle"),
    ("dedup", "tabs.dedup", DedupTab, "idle"),
    ("tweaks", "tabs.tweaks", TweaksTab, "idle"),
    ("settings", "tabs.settings", SettingsTab, "idle"),
)


class MainWindow(QMainWindow):
    """Главное окно приложения."""

    closed = Signal()

    # Колонка персонажа: ширина в долях от окна с разумными границами,
    # плюс длительность разворота при показе и скрытии.
    ASSISTANT_MIN_WIDTH = 150
    ASSISTANT_MAX_WIDTH = 320
    ASSISTANT_ANIMATION_MS = 220

    # ДЕМО: пока ядро не подключено, кнопки действий показывают весь конвейер
    # интерфейса — работу, прогресс, подтверждение, звуки и набегающие цифры.
    # Числа выдуманные; убрать это можно вместе с _connect_pages().
    DEMO_RESULTS = {
        "advisor": {"apps": 36, "recs": 3},
        "cleaner": {"candidates": 1284, "size": 2412.0},
        "dedup": {"groups": 17, "dupes": 63},
    }
    DEMO_WORK_MS = 1600
    WORK_TICK_MS = 60
    # Действия, которые перед показом работы спрашивают подтверждение.
    DEMO_SIGNALS = (
        ("scanRequested", False),
        ("chooseFolderRequested", False),
        ("applyRequested", True),
        ("cleanRequested", True),
        ("deleteRequested", True),
        ("startupRequested", True),
        ("servicesRequested", True),
        ("uwpRequested", True),
        ("restoreRequested", True),
    )

    def __init__(self, app: QApplication, context: Context) -> None:
        super().__init__()
        self._app = app
        self._context = context
        self._status = "ready"
        self._pages: List[QWidget] = []
        self._assistant_visible = True
        self._assistant_animation: QParallelAnimationGroup | None = None
        self._busy = False
        self._work_elapsed = 0
        self._work_page: QWidget | None = None
        self._work_name = ""
        self._work_timer = QTimer(self)
        self._work_timer.setInterval(self.WORK_TICK_MS)
        self._work_timer.timeout.connect(self._on_work_tick)

        self._build_ui()
        self._connect_context()
        self._apply_visuals()
        self.retranslate()
        self.go_to_page(0)
        # Приветствие, а по клику — пояснение к первому разделу.
        self._assistant.play(
            [
                (None, self._context.tr("character.lines.hello")),
                (PAGES[0][3], self._context.tr(f"character.lines.{PAGES[0][0]}")),
            ]
        )
        self._bind_shortcuts()

    # ---------- сборка окна ----------

    def _build_ui(self) -> None:
        self.setMinimumSize(QSize(940, 620))
        self.resize(1080, 700)

        central = QWidget(self)
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = Sidebar(central)
        self._sidebar.set_caption(self._context.tr("sidebar.caption"))
        for icon_name, text_key, _page_cls, _mood in PAGES:
            self._sidebar.add_item(icon_name, text_key)
        self._sidebar.pageSelected.connect(self.go_to_page)
        root.addWidget(self._sidebar)

        self._content = QWidget(central)
        self._content.setObjectName("content")
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_header())

        # Сцена: страница раздела и колонка с помощницей справа.
        stage = QWidget(self._content)
        stage.setObjectName("stage")
        stage_layout = QHBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)
        stage_layout.addWidget(self._build_stack(), 1)
        self._mascot = Mascot(stage)
        stage_layout.addWidget(self._mascot, 0)
        content_layout.addWidget(stage, 1)

        # Панель реплики идёт во всю ширину окна, персонаж «стоит» над ней.
        self._speech = SpeechBox(self._content)
        speech_row = QHBoxLayout()
        speech_row.setContentsMargins(28, 6, 28, 16)
        speech_row.addWidget(self._speech)
        content_layout.addLayout(speech_row)

        self._assistant = Assistant(self._mascot, self._speech, self)
        root.addWidget(self._content, 1)

        self.setCentralWidget(central)
        self._build_status_bar()
        self._apply_assistant_width()
        self._connect_pages()

    def _bind_shortcuts(self) -> None:
        """Горячие клавиши окна.

        F2 листает настроения персонажа — так позы можно посмотреть по кругу,
        не дожидаясь, когда их начнёт дёргать ядро.
        """
        self._mood_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F2), self)
        self._mood_shortcut.activated.connect(self.cycle_mood)

    def _build_header(self) -> QWidget:
        header = QWidget(self)
        header.setObjectName("header")
        self._header = header
        outer = QVBoxLayout(header)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = QWidget(header)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(28, 18, 24, 12)
        layout.setSpacing(12)

        self._header_title = QLabel(self._context.tr("app.title"), header)
        self._header_title.setProperty("role", "title")
        self._header_sub = QLabel(self._context.tr("app.subtitle"), header)
        self._header_sub.setProperty("role", "secondary")

        layout.addWidget(self._header_title)
        layout.addWidget(self._header_sub)
        layout.addStretch(1)

        self._theme_button = self._make_header_button("theme_dark")
        self._theme_button.clicked.connect(self.toggle_theme)
        self._theme_button.setIconSize(QSize(20, 20))
        layout.addWidget(self._theme_button)

        self._assistant_button = self._make_header_button("assistant")
        self._assistant_button.clicked.connect(self.toggle_assistant)
        self._assistant_button.setIconSize(QSize(20, 20))
        layout.addWidget(self._assistant_button)

        self._language_button = self._make_header_button("language")
        self._language_button.setText(self._context.locale().upper())
        self._language_button.clicked.connect(self.toggle_language)
        self._language_button.setIconSize(QSize(20, 20))
        self._language_button.setMinimumWidth(84)
        layout.addWidget(self._language_button)

        outer.addWidget(row)

        # Акцентная полоска под шапкой: выезжает при смене раздела и становится
        # индикатором занятости, пока идёт работа (ui.widgets.AccentBar).
        self._accent_bar = AccentBar(header)
        outer.addWidget(self._accent_bar)

        return header

    def _make_header_button(self, icon_name: str) -> QPushButton:
        widget = QPushButton(self)
        widget.setObjectName("headerButton")
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setMinimumHeight(36)
        widget.setIcon(icons.icon(icon_name, theme.palette(self._context.theme())["text_secondary"], 20))
        return widget

    def _build_stack(self) -> SceneStack:
        self._stack = SceneStack(self)
        for _name, _key, page_cls, _mood in PAGES:
            page = page_cls(self._stack)
            self._pages.append(page)
            self._stack.addWidget(page)
        self._stack.currentChanged.connect(self._on_page_changed)
        return self._stack

    def _build_status_bar(self) -> None:
        self._status_bar = QStatusBar(self)
        self._status_label = QLabel(self._context.tr("status.ready"), self._status_bar)
        self._status_label.setProperty("role", "secondary")
        self._status_bar.addPermanentWidget(self._status_label)
        self.setStatusBar(self._status_bar)

    # ---------- связи с контекстом ----------

    def _connect_context(self) -> None:
        self._context.languageChanged.connect(self.retranslate)
        self._context.themeChanged.connect(self._on_theme_changed)
        self._context.fontChanged.connect(self._on_font_changed)
        # Реплику и настроение помощницы можно заказать через контекст —
        # страницам и ядру не нужно знать ни про один виджет.
        self._context.speechRequested.connect(self._on_speech_requested)
        self._context.assistantMoodRequested.connect(self._mascot.set_mood)
        self._context.languageChanged.connect(self._on_language_changed)
        # Звук: проигрыватель повторяет выбор из настроек, а блип на клики
        # приходит от фильтра, который слушает нажатия всех кнопок окна.
        # Подключаемся через слот окна, а не к методу проигрывателя: контекст —
        # одиночка и живёт дольше любого окна, поэтому прямая подписка держала
        # бы ссылки на закрытые окна до самого выхода.
        self._apply_sounds(self._context.soundsEnabled())
        self._context.soundsChanged.connect(self._apply_sounds)
        sounds.attach(self._app)

    def _apply_visuals(self) -> None:
        theme.apply_theme(self._app, self._context.theme(), self._context.fontKind())
        colors = theme.palette(self._context.theme())
        self._sidebar.apply_colors(colors)
        self._accent_bar.apply_colors(colors)
        self._theme_button.setIcon(
            icons.icon(
                "theme_light" if self._context.theme() == "dark" else "theme_dark",
                colors["text_secondary"],
                20,
            )
        )
        self._language_button.setIcon(icons.icon("language", colors["text_secondary"], 20))
        self._assistant_button.setIcon(icons.icon("assistant", colors["text_secondary"], 20))
        self._refresh_sidebar_caption()

    def _refresh_sidebar_caption(self) -> None:
        self._sidebar.set_caption(self._context.tr("sidebar.caption"))

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_visuals()
        self.retranslate()
        sounds.play("page")

    def _on_font_changed(self, _font_kind: str) -> None:
        self._apply_visuals()
        self.retranslate()
        sounds.play("page")

    def _on_speech_requested(self, text: str) -> None:
        """Реплика, заказанная через контекст (например, из ядра)."""
        self._assistant.say(text)

    def _apply_sounds(self, enabled: bool) -> None:
        """Передать выбор звука проигрывателю."""
        sounds.player().setEnabled(enabled)

    def _on_language_changed(self, _locale: str) -> None:
        """После смены языка уже напечатанная реплика устарела — говорим заново."""
        sounds.play("page")
        self._assistant.say(self._context.tr("character.lines.hello"), "idle")

    # ---------- персонаж ----------

    def _character_width(self) -> int:
        """Ширина колонки персонажа: доля от окна с разумными границами.

        Считаем от размера окна, а не от ширины области содержимого: во время
        пересчёта разметки её ширина ещё не окончательная, и колонка залипала бы
        на минимальной.
        """
        return max(
            self.ASSISTANT_MIN_WIDTH,
            min(self.ASSISTANT_MAX_WIDTH, int(max(self.width(), 1) * 0.28)),
        )

    def _apply_assistant_width(self) -> None:
        """Подогнать колонку под текущий размер окна."""
        if not self._assistant_visible:
            return
        animation = self._assistant_animation
        if animation is not None and animation.state() != QAbstractAnimation.State.Stopped:
            return
        width = self._character_width()
        self._mascot.setMinimumWidth(width)
        self._mascot.setMaximumWidth(width)

    def assistant_visible(self) -> bool:
        return self._assistant_visible

    def toggle_assistant(self) -> None:
        """Показать или скрыть помощницу вместе с панелью реплики."""
        self._assistant_visible = not self._assistant_visible
        self._refresh_assistant_button()
        sounds.play("click")

        group = QParallelAnimationGroup(self)
        current_width = self._mascot.width()
        if self._assistant_visible:
            self._mascot.show()
            self._speech.show()
            self._speech.setMaximumHeight(0)
            target_width = self._character_width()
            target_height = self._speech.sizeHint().height()
        else:
            target_width = 0
            target_height = 0

        for prop in (b"minimumWidth", b"maximumWidth"):
            animation = QPropertyAnimation(self._mascot, prop, group)
            animation.setDuration(self.ASSISTANT_ANIMATION_MS)
            animation.setStartValue(current_width)
            animation.setEndValue(target_width)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(animation)

        height_animation = QPropertyAnimation(self._speech, b"maximumHeight", group)
        height_animation.setDuration(self.ASSISTANT_ANIMATION_MS)
        height_animation.setStartValue(self._speech.height())
        height_animation.setEndValue(target_height)
        height_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(height_animation)

        group.finished.connect(self._on_assistant_animation_done)
        self._assistant_animation = group
        group.start()

    def _on_assistant_animation_done(self) -> None:
        if self._assistant_visible:
            self._speech.setMaximumHeight(16777215)
            self._apply_assistant_width()
            return
        self._mascot.hide()
        self._speech.hide()

    def _refresh_assistant_button(self) -> None:
        key = "header.assistant_hide" if self._assistant_visible else "header.assistant_show"
        self._assistant_button.setToolTip(self._context.tr(key))

    def cycle_mood(self) -> str:
        """Показать по кругу позы помощницы (проверка арта без ядра).

        Листаются только настроения со своей картинкой, чтобы не показывать
        одну и ту же картинку дважды подряд.
        """
        order = demo_moods() or list(MOODS)
        current = self._mascot.mood()
        index = (order.index(current) + 1) % len(order) if current in order else 0
        mood = order[index]
        self._mascot.set_mood(mood)
        sounds.play("page")
        self.setStatus(
            f"{self._context.tr('character.mood_label')}: "
            f"{self._context.tr(f'character.moods.{mood}')}"
        )
        return mood

    # ---------- навигация ----------

    def go_to_page(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            if index == self._stack.currentIndex():
                # Клик по уже активному пункту: перехода нет, но молчать незачем.
                sounds.play("click")
                self._sidebar.set_current(index)
                return
            self._stack.setCurrentIndex(index)
            self._sidebar.set_current(index)

    def _on_page_changed(self, index: int) -> None:
        self._sidebar.set_current(index)
        sounds.play("page")
        self._mascot.enter_from_below()
        self._sweep_accent()
        self._speak_page(index)

    def _speak_page(self, index: int) -> None:
        """Реплика помощницы про раздел: свой текст и своё настроение."""
        if not (0 <= index < len(PAGES)):
            return
        name, _key, _page_cls, mood = PAGES[index]
        self._assistant.say(self._context.tr(f"character.lines.{name}"), mood)

    # ---------- занятость и демонстрация ----------

    def _connect_pages(self) -> None:
        """Подключить кнопки страниц к общему сценарию работы.

        ДЕМО: ядро ещё не подключено, поэтому кнопки показывают весь конвейер
        интерфейса на выдуманных цифрах: занятость в шапке, прогресс, диалог
        подтверждения, звуки и набегающие показатели. Убрать — вместе с
        DEMO_RESULTS.
        """
        for page in self._pages:
            for signal_name, needs_confirm in self.DEMO_SIGNALS:
                signal = getattr(page, signal_name, None)
                if signal is None:
                    continue
                signal.connect(lambda confirm=needs_confirm: self.run_demo_action(confirm))

    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Показать, что идёт работа: по полоске в шапке бежит сегмент."""
        self._busy = busy
        self._accent_bar.set_busy(busy)

    def run_demo_action(self, needs_confirm: bool = False) -> None:
        """Сценарий кнопки действия: подтверждение, работа, итог."""
        index = self._stack.currentIndex()
        if not (0 <= index < len(PAGES)):
            return
        if self._busy:
            sounds.play("error")
            return
        name, _key, _page_cls, _mood = PAGES[index]
        if needs_confirm and not self._ask_confirmation():
            # Звук отмены уже сыграл сам диалог — тут только реплика.
            self._assistant.say(self._context.tr("character.lines.cancelled"), "idle")
            return
        self._start_work(name, self._pages[index])

    def _ask_confirmation(self) -> bool:
        """Спросить подтверждение в стиле визуальной новеллы."""
        items = [
            (self._context.tr(f"demo.items.{key}"), risk)
            for key, risk in (("tmp", "low"), ("startup", "medium"), ("photo", "high"))
        ]
        return ConfirmDialog.ask(self, items)

    def _start_work(self, name: str, page: QWidget) -> None:
        """Начать работу: занятость, прогресс, реплика и звук."""
        self._work_name = name
        self._work_page = page
        self._work_elapsed = 0
        self.set_busy(True)
        sounds.play("click")
        self._assistant.say(self._context.tr("character.lines.scan"), "scan")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.scanning"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)
        self._work_timer.start()

    def _on_work_tick(self) -> None:
        page = self._work_page
        if page is None:
            self._work_timer.stop()
            return
        self._work_elapsed += self.WORK_TICK_MS
        percent = min(100, round(100 * self._work_elapsed / self.DEMO_WORK_MS))
        page.set_progress(percent)
        if percent >= 100:
            self._work_timer.stop()
            self._finish_work()

    def _finish_work(self) -> None:
        """Закончить работу: показать числа, звук готовности, реплика."""
        page, name = self._work_page, self._work_name
        self._work_page = None
        self.set_busy(False)
        if page is None:
            return
        page.set_progress(None)
        done_key = f"{name}.scan_done"
        done_text = self._context.tr(done_key)
        if done_text != done_key:
            page.setStatus(done_text)
        for key, value in self.DEMO_RESULTS.get(name, {}).items():
            page.setStats(key, value)
        sounds.play("done")
        self._assistant.say(self._context.tr("character.lines.clean"), "idle")

    def _sweep_accent(self) -> None:
        """Провести акцентную полоску под шапкой заново."""
        self._accent_bar.sweep()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        # Полоска должна занимать всю шапку и после изменения размера окна.
        # Если она как раз выезжает — правим не ширину, а цель анимации.
        # Полоска под шапкой следит за своим размером сама, а колонка
        # персонажа тянется вместе с окном.
        self._apply_assistant_width()

    def toggle_theme(self) -> None:
        self._context.setTheme("light" if self._context.theme() == "dark" else "dark")

    def toggle_language(self) -> None:
        self._context.setLocale("en" if self._context.locale() == "ru" else "ru")

    # ---------- локализация ----------

    def retranslate(self, *_args: object) -> None:
        """Перевести интерфейс заново после смены языка."""
        self.setWindowTitle(
            f"{self._context.tr('app.title')} — {self._context.tr('app.subtitle')}"
        )
        self._header_title.setText(self._context.tr("app.title"))
        self._header_sub.setText(self._context.tr("app.subtitle"))
        self._language_button.setText(self._context.locale().upper())
        self._theme_button.setToolTip(
            self._context.tr("settings.theme_light")
            if self._context.theme() == "dark"
            else self._context.tr("settings.theme_dark")
        )
        self._language_button.setToolTip(self._context.tr("header.language"))
        self._refresh_assistant_button()
        self._sidebar.retranslate()
        self._refresh_sidebar_caption()
        self._speech.retranslate()
        for page in self._pages:
            if hasattr(page, "retranslate"):
                page.retranslate()
        self._status_label.setText(self._context.tr("status.ready"))
        self._apply_visuals()

    # ---------- статус ----------

    def status(self) -> str:
        return self._status

    def setStatus(self, status: str) -> None:
        self._status = status
        self._status_label.setText(status)
