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
from pathlib import Path
from typing import List

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
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
from ui.session import Session, get_session, human_size
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

    # Действия страниц, которые требуют подтверждения пользователя ДО работы.
    # Чистка и удаление — всегда с диалогом; сканы и чтение — без него.
    CONFIRM_SIGNALS = frozenset({"cleanRequested", "deleteRequested"})

    def __init__(self, app: QApplication, context: Context,
                 session: Session | None = None) -> None:
        super().__init__()
        self._app = app
        self._context = context
        self._session = session if session is not None else get_session()
        self._status = "ready"
        self._pages: List[QWidget] = []
        self._assistant_visible = True
        self._assistant_animation: QParallelAnimationGroup | None = None
        self._work_page: QWidget | None = None
        self._stub_after_work = True

        self._build_ui()
        self._connect_context()
        self._connect_session()
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

    # ---------- связь с сессией (ядро) ----------

    def session(self) -> Session:
        return self._session

    def _connect_session(self) -> None:
        """Кнопки страниц → задачи сессии; занятость, итоги и ошибки → окно.

        Итоги приходят сигналом taskFinished из рабочего потока: Qt сам
        переносит их в поток окна, поэтому виджеты трогаются только там.
        """
        self._session.busyChanged.connect(self.set_busy)
        self._session.errorOccurred.connect(self._on_core_error)
        self._session.progressTick.connect(self._on_progress_tick)
        self._session.taskFinished.connect(self._on_task_finished)
        self._session.taskFailed.connect(lambda _name, _msg: None)
        for page in self._pages:
            for signal_name in ("scanRequested", "chooseFolderRequested",
                                "applyRequested", "cleanRequested",
                                "deleteRequested"):
                signal = getattr(page, signal_name, None)
                if signal is None:
                    continue
                needs_confirm = signal_name in self.CONFIRM_SIGNALS
                signal.connect(
                    lambda confirm=needs_confirm: self.run_action(confirm))

    def _on_progress_tick(self, percent: int) -> None:
        page = self._work_page
        if page is not None and hasattr(page, "set_progress"):
            page.set_progress(percent)

    def _on_task_finished(self, name: str, result: object) -> None:
        """Итог задачи уже в главном потоке: раскладываем по странице."""
        page = self._work_page or self._current_page()
        if name == "advisor":
            self._finish_advisor(result, page)
        elif name == "cleaner_scan":
            self._finish_cleaner(result, page)
        elif name == "dedup":
            self._finish_dedup(result, page)
        elif name == "purge":
            self._finish_purge(result, page)

    def _on_core_error(self, message: str) -> None:
        """Ошибка ядра: статус страницы, реплика персонажа, звук."""
        page = self._work_page or self._current_page()
        self._work_page = None
        text = self._context.tr("session.core_error").format(error=message)
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        if page is not None and hasattr(page, "set_progress"):
            page.set_progress(None)
        sounds.play("error")
        self._assistant.say(text, "panic")

    def _current_page(self) -> QWidget | None:
        index = self._stack.currentIndex()
        if 0 <= index < len(self._pages):
            return self._pages[index]
        return None

    def is_busy(self) -> bool:
        return self._session.is_busy()

    def set_busy(self, busy: bool) -> None:
        """Показать, что идёт работа: по полоске в шапке бежит сегмент."""
        self._accent_bar.set_busy(busy)

    def run_action(self, needs_confirm: bool = False) -> None:
        """Кнопка действия: подтверждение (если нужно) → задача сессии."""
        page = self._current_page()
        if page is None:
            return
        if self.is_busy():
            sounds.play("error")
            return
        name = PAGES[self._stack.currentIndex()][0]
        if needs_confirm:
            if not self._confirmable(name):
                sounds.play("error")
                if hasattr(page, "setStatus"):
                    page.setStatus(self._context.tr("session.nothing_to_clean"))
                self._assistant.say(
                    self._context.tr("session.nothing_to_clean"), "idle")
                return
            if not self._ask_confirmation(name):
                # Звук отмены уже сыграл сам диалог — тут только реплика.
                self._assistant.say(self._context.tr("character.lines.cancelled"), "idle")
                return
            if name == "cleaner":
                self._start_purge_cleaner(page)
                return
            if name == "dedup":
                self._start_purge_dedup(page)
                return
        self._start_work(name, page)

    def _confirmable(self, name: str) -> bool:
        """Есть ли что подтверждать: чистке нужен скан, дублям — группы."""
        if name == "cleaner":
            return bool(self._session.last_scan().summaries)
        if name == "dedup":
            return bool(self._session.last_groups())
        return False

    def _ask_confirmation(self, name: str) -> bool:
        """Спросить подтверждение в стиле визуальной новеллы.

        Пока экран категорий не построен, диалог показывает сводку последнего
        скана: категории, объём, дорожку удаления и риск. Удаление применится
        ко всем найденным пунктам этих категорий — это сказано в примечании.
        """
        if name == "dedup":
            groups = self._session.last_groups()
            items = [
                (f"{human_size(g.size)} × {len(g.paths)}: "
                 f"{g.paths[0].split(chr(92))[-1]}", "medium")
                for g in groups[:12]
            ]
            wasted = sum(g.wasted() for g in groups)
            note = (self._context.tr("session.dup_keep_note") + " "
                    + self._context.tr("session.purge_real").format(
                        count=sum(len(g.paths) - 1 for g in groups),
                        size=human_size(wasted)))
            return ConfirmDialog.ask(self, items, note=note)
        scan = self._session.last_scan()
        items = [
            (self._session.describe_summary(summary, with_risk=False),
             summary.risk)
            for summary in scan.summaries[:12]
        ]
        note = self._context.tr("session.purge_real").format(
            count=scan.files, size=human_size(scan.bytes))
        return ConfirmDialog.ask(self, items, note=note)

    def _start_work(self, name: str, page: QWidget) -> None:
        """Начать настоящую работу: задача сессии, занятость, прогресс."""
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        self._assistant.say(self._context.tr("character.lines.scan"), "scan")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.scanning"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)

        if name == "advisor":
            self._session.scan_advisor()
        elif name == "cleaner":
            self._session.scan_candidates([], True)
        elif name == "dedup":
            self._session.scan_duplicates([str(Path.home())])
        else:
            # Твики и настройки: своих задач у сессии пока нет (M4/M5).
            self._work_page = None
            self.set_busy(False)
            if hasattr(page, "setStatus"):
                page.setStatus(self._context.tr("status.idle"))

    # ---------- итоги настоящих задач ----------

    def _start_purge_cleaner(self, page: QWidget) -> None:
        """Удаление по категориям последнего скана: дорожки решает ядро."""
        scan = self._session.last_scan()
        if scan.truncated:
            # Подтверждение было по полной сводке, а стрим донёс не всё:
            # удалять часть — обман. Ждём экран категорий (этап 2).
            sounds.play("error")
            if hasattr(page, "setStatus"):
                page.setStatus(self._context.tr("session.need_rescan"))
            self._assistant.say(self._context.tr("session.need_rescan"), "idle")
            return
        items = [
            {"path": item.path, "category": item.primary_category()}
            for item in scan.items
        ]
        if not items:
            # Пункты не стримились (слишком много): удалять «вслепую» нельзя —
            # просим пересканировать, экран категорий решит это по-человечески.
            sounds.play("error")
            if hasattr(page, "setStatus"):
                page.setStatus(self._context.tr("session.need_rescan"))
            self._assistant.say(self._context.tr("session.need_rescan"), "idle")
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        self._assistant.say(self._context.tr("character.lines.scan"), "scan")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.processing"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)

        self._session.purge_items(items, False)

    def _start_purge_dedup(self, page: QWidget) -> None:
        """Удаление дубликатов фото: из каждой группы живёт свежая копия."""
        groups = self._session.last_groups()
        items: List[dict] = []
        for group in groups:
            if len(group.paths) < 2:
                continue
            keep = max(group.paths, key=lambda p: Path(p).stat().st_mtime
                       if Path(p).exists() else 0)
            items.extend(
                {"path": p, "category": "dupes.photo"}
                for p in group.paths if p != keep
            )
        if not items:
            sounds.play("error")
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.processing"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)

        self._session.purge_items(items, False)

    def _finish_purge(self, report, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if hasattr(page, "set_progress"):
            page.set_progress(None)
        text = self._session.describe_purge(report)
        if hasattr(page, "setStatus"):
            page.setStatus(text)
        sounds.play("done" if not report.failures else "error")
        self._assistant.say(text, "calm" if not report.failures else "panic")

    def _finish_advisor(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if hasattr(page, "set_progress"):
            page.set_progress(None)
        page.setStats("apps", result["apps"])
        page.setStats("recs", len(result["recs"]))
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("advisor.scan_done"))
        if hasattr(page, "setPlan"):
            if result["recs"]:
                lines = [
                    f"{r.get('display_name', r.get('name', '?'))}: "
                    f"{r.get('description', '')}"
                    for r in result["recs"][:10]
                ]
                page.setPlan("\n".join(lines))
            else:
                page.setPlan(self._context.tr("advisor.no_plan"))
        sounds.play("done")
        self._assistant.say(
            self._context.tr("session.advisor_apps").format(count=result["apps"])
            + " " + self._context.tr("session.advisor_recs").format(
                count=len(result["recs"])),
            "idle",
        )

    def _finish_cleaner(self, scan, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if hasattr(page, "set_progress"):
            page.set_progress(None)
        page.setStats("candidates", scan.files)
        page.setStats("size", round(scan.bytes / (1024 * 1024), 1))
        if hasattr(page, "setStatus"):
            status = self._context.tr("cleaner.scan_done")
            if scan.cancelled:
                status = self._context.tr("session.scan_cancelled")
            page.setStatus(status)
        if hasattr(page, "setCandidates"):
            lines = [self._session.describe_summary(s) for s in scan.summaries[:10]]
            if scan.truncated:
                lines.append(self._context.tr("session.candidates_more").format(
                    count=len(scan.items), total=scan.files))
            page.setCandidates("\n".join(lines) or self._context.tr(
                "cleaner.candidates_empty"))
        sounds.play("done")
        self._assistant.say(
            self._context.tr("character.lines.clean"), "idle")
        if self._stub_after_work:
            self._show_categories_stub(scan)

    def _finish_dedup(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if hasattr(page, "set_progress"):
            page.set_progress(None)
        groups = result["groups"]
        wasted = sum(g.wasted() for g in groups)
        dupes = sum(len(g.paths) for g in groups)
        page.setStats("groups", len(groups))
        page.setStats("dupes", dupes)
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("dedup.scan_done"))
        if hasattr(page, "setGroups"):
            if groups:
                lines = [
                    f"{human_size(g.size)} × {len(g.paths)}: "
                    f"{g.paths[0].split(chr(92))[-1]}"
                    for g in groups[:10]
                ]
                page.setGroups("\n".join(lines))
            else:
                page.setGroups(self._context.tr("session.dup_none"))
        sounds.play("done")
        self._assistant.say(
            self._context.tr("session.dup_groups").format(
                count=len(groups), size=human_size(wasted)),
            "idle",
        )

    def _show_categories_stub(self, scan) -> None:
        """ВРЕМЕННАЯ ЗАГЛУШКА: итог скана стандартным окном Windows.

        Экран категорий по-настоящему делает не эта модель — см. context.md,
        раздел 2, «по промту №17». Здесь только placeholder, чтобы было видно
        место в потоке: после скана показываем сводку РЕАЛЬНЫХ категорий ядра.
        Под offscreen (тесты) не показываем: модальное окно заблокировало бы
        headless-прогон.
        """
        if QApplication.instance() is not None and \
                QApplication.instance().platformName() == "offscreen":
            return
        lines = [self._session.describe_summary(s) for s in scan.summaries[:12]]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self._context.tr("cleaner.candidates_title"))
        box.setText(
            f"Найдено {scan.files:,} объектов на {human_size(scan.bytes)}."
            .replace(",", " "))
        box.setInformativeText(
            "\n".join(lines)
            + "\n\nВременная заглушка вместо экрана категорий:\n"
              "настоящий выбор по категориям и галочкам появится следующим заходом."
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

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
