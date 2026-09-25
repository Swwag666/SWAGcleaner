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
    QFileDialog,
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
        self._tweaks_loaded = False

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
        # Твики читают систему при первом заходе на страницу.
        if 0 <= index < len(PAGES) and PAGES[index][0] == "tweaks" \
                and not self._tweaks_loaded and not self.is_busy():
            self._tweaks_loaded = True
            self._start_tweaks_load()

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
            for signal_name in ("scanRequested", "cleanRequested",
                                "deleteRequested"):
                signal = getattr(page, signal_name, None)
                if signal is None:
                    continue
                needs_confirm = signal_name in self.CONFIRM_SIGNALS
                signal.connect(
                    lambda confirm=needs_confirm: self.run_action(confirm))
            # У этих двух кнопок своя семантика, а не «запустить задачу»:
            # «Папка» открывает выбор каталога, «Применить» гонит план
            # советника в деинсталляторы — обе через свои обработчики.
            choose_folder = getattr(page, "chooseFolderRequested", None)
            if choose_folder is not None:
                choose_folder.connect(self._ask_dedup_folder)
            apply_plan = getattr(page, "applyRequested", None)
            if apply_plan is not None:
                apply_plan.connect(self._ask_apply_advisor)
            # Твики: свои сигналы с аргументами, поэтому вяжутся отдельно.
            refresh = getattr(page, "refreshRequested", None)
            if refresh is not None:
                refresh.connect(self._start_tweaks_load)
            disable = getattr(page, "disableStartupRequested", None)
            if disable is not None:
                disable.connect(self._ask_disable_startup)
            restore = getattr(page, "restoreSnapshotRequested", None)
            if restore is not None:
                restore.connect(self._ask_restore_snapshot)
            disable_svc = getattr(page, "disableServiceRequested", None)
            if disable_svc is not None:
                disable_svc.connect(self._ask_disable_service)
            remove_uwp = getattr(page, "removeUwpRequested", None)
            if remove_uwp is not None:
                remove_uwp.connect(self._ask_remove_uwp)
            apply_twk = getattr(page, "applyTweakRequested", None)
            if apply_twk is not None:
                apply_twk.connect(self._ask_apply_tweak)
            preset = getattr(page, "applyPresetRequested", None)
            if preset is not None:
                preset.connect(self._ask_apply_preset)
            apps = getattr(page, "installAppsRequested", None)
            if apps is not None:
                apps.connect(self._ask_install_apps)
            act = getattr(page, "activationRequested", None)
            if act is not None:
                act.connect(self._ask_activation)
            gpe = getattr(page, "gpeditRequested", None)
            if gpe is not None:
                gpe.connect(self._ask_gpedit)
            shut_set = getattr(page, "shutdownSetRequested", None)
            if shut_set is not None:
                shut_set.connect(self._ask_shutdown_set)
            shut_cancel = getattr(page, "shutdownCancelRequested", None)
            if shut_cancel is not None:
                shut_cancel.connect(self._ask_shutdown_cancel)
            # Этап 6: зависимости - установка и детект «уже стоит».
            redists_install = getattr(page, "installRedistsRequested", None)
            if redists_install is not None:
                redists_install.connect(self._ask_install_redists)
            redists_refresh = getattr(page, "refreshRedistsRequested", None)
            if redists_refresh is not None:
                redists_refresh.connect(self._ask_redist_status)
            # Этап 7: конфиги системы - снятие, применение, файлы.
            cfg_save = getattr(page, "configSaveRequested", None)
            if cfg_save is not None:
                cfg_save.connect(self._ask_config_save)
            cfg_apply = getattr(page, "configApplyRequested", None)
            if cfg_apply is not None:
                cfg_apply.connect(self._ask_config_apply)
            cfg_export = getattr(page, "configExportRequested", None)
            if cfg_export is not None:
                cfg_export.connect(self._on_config_export)
            cfg_import = getattr(page, "configImportRequested", None)
            if cfg_import is not None:
                cfg_import.connect(self._on_config_import)
            cfg_delete = getattr(page, "configDeleteRequested", None)
            if cfg_delete is not None:
                cfg_delete.connect(self._ask_config_delete)
            cfg_refresh = getattr(page, "configRefreshRequested", None)
            if cfg_refresh is not None:
                cfg_refresh.connect(self._refresh_configs)
                page.setConfigs(self._session.config_list())
            # Настройки AI: сохранение, проверка связи, каталог моделей.
            ai_save = getattr(page, "aiSaveRequested", None)
            if ai_save is not None:
                ai_save.connect(self._on_ai_save)
            ai_test = getattr(page, "aiTestRequested", None)
            if ai_test is not None:
                ai_test.connect(self._ask_ai_test)
            ai_models = getattr(page, "aiModelsRequested", None)
            if ai_models is not None:
                ai_models.connect(self._ask_ai_models)
                page.setAiSettings(self._session.ai_settings())

    def _on_progress_tick(self, percent: int) -> None:
        page = self._work_page
        if page is not None and hasattr(page, "set_progress"):
            page.set_progress(percent)

    def _on_task_finished(self, name: str, result: object) -> None:
        """Итог задачи уже в главном потоке: раскладываем по странице.

        Обработчик итога не должен уронить окно: любое исключение внутри
        _finish_* ловится здесь, окно возвращается в свободное состояние.
        """
        page = self._work_page or self._current_page()
        try:
            if name == "advisor":
                self._finish_advisor(result, page)
            elif name == "advisor_apply":
                self._finish_advisor_apply(result, page)
            elif name == "cleaner_scan":
                self._finish_cleaner(result, page)
            elif name == "dedup":
                self._finish_dedup(result, page)
            elif name == "purge":
                self._finish_purge(result, page)
            elif name == "tweaks_load":
                self._finish_tweaks(result, page)
            elif name == "tweaks_action":
                self._finish_tweaks_action(result, page)
            elif name == "ai_test":
                self._finish_ai_test(result, page)
            elif name == "ai_models":
                self._finish_ai_models(result, page)
            elif name == "redist_status":
                self._finish_redist_status(result, page)
            elif name == "redists_install":
                self._finish_redists_install(result, page)
            elif name == "config_save":
                self._finish_config_save(result, page)
            elif name == "config_apply":
                self._finish_config_apply(result, page)
        except Exception:  # noqa: BLE001 - см. docstring
            _LOGGER.exception("обработчик итога задачи упал: %s", name)
            self._work_page = None
            self.set_busy(False)

    def _on_core_error(self, message: str) -> None:
        """Ошибка ядра: статус страницы, реплика персонажа, звук."""
        # Неудачная загрузка твиков не должна быть «навсегда»: при повторном
        # заходе на страницу перечитываем систему ещё раз.
        self._tweaks_loaded = False
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
            if not self._confirmable(name, page):
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

    def _confirmable(self, name: str, page: QWidget | None) -> bool:
        """Есть ли что подтверждать: чистке нужен скан и выбранные категории."""
        if name == "cleaner":
            if not self._session.last_scan().summaries:
                return False
            if page is not None and hasattr(page, "selected_ids"):
                return bool(page.selected_ids())
            return False
        if name == "dedup":
            return bool(self._session.last_groups())
        return False

    def _ask_confirmation(self, name: str) -> bool:
        """Спросить подтверждение в стиле визуальной новеллы.

        Диалог показывает сводку по ВЫБРАННЫМ категориям последнего скана:
        названия, объём, дорожку удаления и риск. Удаление применится ко всем
        найденным пунктам этих категорий — это сказано в примечании.
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
        page = self._current_page()
        selected = (set(page.selected_ids())
                    if page is not None and hasattr(page, "selected_ids")
                    else None)
        summaries = [s for s in scan.summaries
                     if selected is None or s.id in selected]
        items = [
            (self._session.describe_summary(summary, with_risk=False),
             summary.risk)
            for summary in summaries[:12]
        ]
        # Итог в примечании считаем по фактическому фильтру удаления —
        # основной категории пункта: сводки считают файл в каждой своей
        # категории и потому завышали обещание (найдено аудитом).
        wanted = selected if selected is not None \
            else {s.id for s in summaries}
        chosen = [i for i in scan.items if i.primary_category() in wanted]
        if chosen:
            count = len(chosen)
            size = sum(i.size for i in chosen)
        else:
            # Стрим не влез в память: точных пунктов нет, честно показываем
            # оценку по сводкам (удаление всё равно дальше откажет с
            # «пересканируйте» — обмана не будет).
            count = sum(s.files for s in summaries)
            size = sum(s.bytes for s in summaries)
        note = self._context.tr("session.purge_real").format(
            count=count, size=human_size(size))
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
        """Удаление по ВЫБРАННЫМ категориям последнего скана.

        Дорожки решает ядро. Экран категорий уже спросил, какие категории
        едут, — здесь просто фильтруем пункты по отмеченным id.
        """
        scan = self._session.last_scan()
        if scan.truncated:
            # Подтверждение было по полной сводке, а стрим донёс не всё:
            # удалять часть — обман. Просим пересканировать и сузить выбор.
            sounds.play("error")
            if hasattr(page, "setStatus"):
                page.setStatus(self._context.tr("session.need_rescan"))
            self._assistant.say(self._context.tr("session.need_rescan"), "idle")
            return
        selected = (set(page.selected_ids())
                    if hasattr(page, "selected_ids") else set())
        items = [
            {"path": item.path, "category": item.primary_category()}
            for item in scan.items
            if item.primary_category() in selected
        ]
        if not items:
            # Пункты не стримились (слишком много): удалять «вслепую» нельзя —
            # просим пересканировать и сузить выбор категориями.
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
        """Удаление дубликатов фото: из каждой группы живёт свежая копия.

        Выбор «кого оставить» (stat по mtime) делает сессия в рабочем
        потоке: здесь, в главном, он на медленном диске морозил окно.
        """
        groups = self._session.last_groups()
        if not any(len(g.paths) > 1 for g in groups):
            sounds.play("error")
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.processing"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)

        self._session.purge_duplicates(groups)

    def _ask_dedup_folder(self) -> None:
        """Кнопка «Папка»: выбрать каталог и искать дубликаты в нём."""
        if self.is_busy():
            sounds.play("error")
            return
        folder = QFileDialog.getExistingDirectory(
            self, self._context.tr("dedup.folder_button"))
        if not folder:
            return
        page = self._current_page()
        if page is None:
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.scanning"))
        if hasattr(page, "set_progress"):
            page.set_progress(0)
        self._session.scan_duplicates([folder])

    def _ask_apply_advisor(self) -> None:
        """Кнопка «Применить выбранное»: деинсталляторы программ из плана.

        Честная семантика: для каждой рекомендации «удалить» запускается
        её собственный деинсталлятор (UninstallString из реестра) — и об
        этом сказано в примечании подтверждения.
        """
        if self.is_busy():
            sounds.play("error")
            return
        page = self._current_page()
        names = self._session.advisor_removals()
        if not names:
            sounds.play("error")
            text = self._context.tr("advisor.apply_none")
            if page is not None and hasattr(page, "setStatus"):
                page.setStatus(text)
            self._assistant.say(text, "idle")
            return
        items = [(name, "medium") for name in names[:12]]
        note = self._context.tr("advisor.apply_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.processing"))
        self._session.apply_advisor()

    def _finish_advisor_apply(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        launched = result.get("launched", []) if isinstance(result, dict) else []
        text = self._context.tr("session.apply_launched").format(
            count=len(launched))
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        sounds.play("done" if launched else "error")
        self._assistant.say(text, "calm" if launched else "idle")

    def _finish_purge(self, report, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if hasattr(page, "set_progress"):
            page.set_progress(None)
        text = self._session.describe_purge(report)
        if hasattr(page, "setStatus"):
            page.setStatus(text)
        # Экран журнала после удаления: что ушло, дорожки, отказы и ошибки
        # с причинами — до следующего скана вместо списка кандидатов.
        if hasattr(page, "show_journal"):
            page.show_journal(report, text)
        sounds.play("done" if not report.failures else "error")
        self._assistant.say(text, "calm" if not report.failures else "panic")

    # ---------- твики: автозагрузка и бэкапы (M4/M5) ----------

    def _start_tweaks_load(self) -> None:
        """Обновить списки твиков: автозагрузка, службы, снапшоты."""
        page = self._current_page()
        if page is None or self.is_busy():
            return
        self._work_page = page
        self.set_busy(True)
        sounds.play("click")
        if hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("status.scanning"))
        self._session.load_tweaks()

    def _ask_disable_startup(self, entry) -> None:
        """Отключение записи автозагрузки — только через подтверждение."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(f"{entry.name} — {entry.path}", "medium")]
        note = self._context.tr("tweaks.confirm_disable_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.disable_startup(entry)

    def _ask_restore_snapshot(self, snapshot: str) -> None:
        """Возврат записи из снапшота — тоже через подтверждение."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(snapshot, "low")]
        note = self._context.tr("tweaks.confirm_restore_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.restore_backup(snapshot)

    def _ask_disable_service(self, name: str) -> None:
        """Отключение службы — по одной, через подтверждение (правило 8.6)."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(name, "medium")]
        note = self._context.tr("tweaks.confirm_service_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.disable_service(name)

    def _ask_remove_uwp(self, full_name: str) -> None:
        """Удаление UWP-пакета у пользователя — через подтверждение."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(full_name, "medium")]
        note = self._context.tr("tweaks.confirm_uwp_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.remove_uwp(full_name)

    def _ask_apply_tweak(self, tweak_id: str, enable: bool) -> None:
        """Системный твик: подтверждение с риском, затем apply со снапшотом."""
        if self.is_busy():
            sounds.play("error")
            return
        # Скрытие букв дисков - через свой диалог: сначала выбор букв.
        if tweak_id == "explorer.hide_drive_letters" and enable:
            self._ask_hide_drives()
            return
        risk = "high" if any(
            tw.get("id") == tweak_id and tw.get("risk") == "high"
            for tw in getattr(self._current_page(), "_sys_tweaks", [])) \
            else "medium"
        items = [(tweak_id, risk)]
        note = self._context.tr("tweaks.confirm_tweak_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.apply_tweak(tweak_id, enable)

    def _ask_hide_drives(self) -> None:
        """Диалог выбора букв → твик с битмаской → откат через снапшот."""
        from core.hidepart import present_drives
        from ui.dialog import HideDrivesDialog

        letters = HideDrivesDialog.ask(self, present_drives(),
                                       self._session.current_hidden_drives())
        if letters is None:
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.hide_drives(letters)

    def _ask_apply_preset(self, preset_id: str) -> None:
        """Пакет твиков: подтверждение со списком содержимого пакета."""
        if self.is_busy():
            sounds.play("error")
            return
        from core.tweaks import load_db, load_presets
        preset = next((p for p in load_presets() if p.id == preset_id), None)
        if preset is None:
            return
        names = {tw.id: (tw.name_ru or tw.name_en) for tw in load_db()}
        items = [(names.get(tid, tid), "medium") for tid in preset.tweaks]
        note = self._context.tr("tweaks.confirm_preset_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.apply_preset(preset_id)

    def _ask_install_apps(self, winget_ids: list) -> None:
        """Установка приложений через winget — с подтверждением списка."""
        if self.is_busy():
            sounds.play("error")
            return
        if not winget_ids:
            self._assistant.say(
                self._context.tr("tweaks.apps_none"), "idle")
            return
        items = [(wid, "low") for wid in winget_ids]
        note = self._context.tr("tweaks.confirm_apps_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.install_apps(winget_ids)

    def _ask_activation(self, what: str) -> None:
        """Активация: честное предупреждение, что это сторонний скрипт."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(what, "high")]
        note = self._context.tr("tweaks.confirm_activation_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.activate(what)

    def _ask_gpedit(self) -> None:
        """Доустановка gpedit на Home — долго, честно предупреждаем."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [("gpedit", "medium")]
        note = self._context.tr("tweaks.confirm_gpedit_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.install_gpedit()

    def _ask_shutdown_set(self, minutes: int) -> None:
        """Таймер выключения: подтверждение — компьютер реально выключится."""
        if self.is_busy():
            sounds.play("error")
            return
        items = [(self._context.tr("tweaks.timer_item").format(
            minutes=minutes), "high")]
        note = self._context.tr("tweaks.confirm_timer_note")
        if not ConfirmDialog.ask(self, items, note=note):
            self._assistant.say(
                self._context.tr("character.lines.cancelled"), "idle")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.schedule_shutdown(minutes)

    def _ask_shutdown_cancel(self) -> None:
        """Отмена таймера — без подтверждения, это отмена опасного."""
        if self.is_busy():
            sounds.play("error")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.cancel_shutdown()

    def _on_ai_save(self, settings: object) -> None:
        """Сохранение настроек AI: файл + горячая замена в ассистенте."""
        ok = self._session.save_ai_settings(settings)
        text = (self._context.tr("settings.ai_saved") if ok
                else self._context.tr("settings.ai_save_fail"))
        page = self._current_page()
        if page is not None and hasattr(page, "setAiStatus"):
            page.setAiStatus(text)
        self._assistant.say(text, "idle")

    def _ask_ai_test(self, settings: object) -> None:
        """Проверка связи с Ollama/API — фоном, сеть может жевать."""
        if self.is_busy():
            sounds.play("error")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.test_ai_task(settings)

    def _ask_ai_models(self, settings: object) -> None:
        """Каталог моделей сервера — фоном."""
        if self.is_busy():
            sounds.play("error")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.list_ai_models_task(settings)

    def _finish_ai_test(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        text = str(result.get("text", ""))
        if page is not None and hasattr(page, "setAiStatus"):
            page.setAiStatus(text)
        self._assistant.say(text, "idle")

    def _finish_ai_models(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        names = list(result.get("names") or [])
        error = str(result.get("error") or "")
        if page is not None and hasattr(page, "setAiModels"):
            page.setAiModels(names)
        if page is not None and hasattr(page, "setAiStatus"):
            if error:
                page.setAiStatus(
                    self._context.tr("settings.ai_models_fail").format(error=error))
            else:
                page.setAiStatus(
                    self._context.tr("settings.ai_models_count").format(
                        count=len(names)))

    # ---------- этап 6: зависимости ----------

    def _ask_redist_status(self) -> None:
        if self.is_busy():
            sounds.play("error")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        self._session.redist_status_task()

    def _ask_install_redists(self, rids: list) -> None:
        """Установка зависимостей - с подтверждением списка (миссклики)."""
        if self.is_busy():
            sounds.play("error")
            return
        from core.redists import entry
        is_ru = str(self._context.locale()).startswith("ru")
        items = [entry(r).name_ru if is_ru else entry(r).name_en
                 for r in rids if r]
        if not items:
            return
        if not ConfirmDialog.ask(
                self, items,
                note=self._context.tr("tweaks.redists_confirm_note")):
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.install_redists(rids)

    def _finish_redist_status(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if page is not None and hasattr(page, "setRedistStatus"):
            page.setRedistStatus(result.get("status") or {})

    def _finish_redists_install(self, result: dict, page: QWidget) -> None:
        text = self._context.tr("tweaks.redists_done").format(
            ok=result.get("ok", 0), total=result.get("total", 0))
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        self._assistant.say(text, "idle")
        sounds.play("done" if result.get("ok", 0) == result.get("total", 0)
                    else "error")
        # Сразу пересчитать «уже стоит»: детект дешёвый, картина честная.
        self._work_page = page
        self.set_busy(True)
        self._session.redist_status_task()

    # ---------- этап 7: конфиги системы ----------

    def _refresh_configs(self) -> None:
        page = self._current_page()
        if page is not None and hasattr(page, "setConfigs"):
            page.setConfigs(self._session.config_list())

    def _refresh_configs_on(self, page: QWidget) -> None:
        if page is not None and hasattr(page, "setConfigs"):
            page.setConfigs(self._session.config_list())

    def _ask_config_save(self, name: str, note: str) -> None:
        if self.is_busy():
            sounds.play("error")
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.config_save_task(name, note)

    def _finish_config_save(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        self._refresh_configs_on(page)
        text = self._context.tr("tweaks.configs_saved").format(
            name=result.get("name", ""), tweaks=result.get("tweaks", 0),
            apps=result.get("apps", 0), redists=result.get("redists", 0))
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        self._assistant.say(text, "idle")
        sounds.play("done")

    def _ask_config_apply(self, name: str) -> None:
        """Применение конфига - только через подтверждение со всем составом."""
        if self.is_busy():
            sounds.play("error")
            return
        try:
            cfg = self._session.config_meta(name)
        except Exception as exc:  # noqa: BLE001 - битый файл покажем словами
            page = self._current_page()
            if page is not None and hasattr(page, "setStatus"):
                page.setStatus(
                    self._context.tr("tweaks.configs_import_fail").format(
                        error=exc))
            sounds.play("error")
            return
        from core.tweaks import load_db
        risks = {tw.id: tw.risk for tw in load_db()}
        items = [self._context.tr("tweaks.configs_confirm_counts").format(
            tweaks=len(cfg["tweaks"]), apps=len(cfg["apps"]),
            redists=len(cfg["redists"]))]
        high = [x["id"] for x in cfg["tweaks"]
                if risks.get(x["id"]) == "high"]
        if high:
            items.append(self._context.tr("tweaks.configs_confirm_high").format(
                count=len(high)))
            items += [f"- {tid}" for tid in high[:10]]
        items += [f"- {x['id']}" for x in cfg["tweaks"][:25]]
        if len(cfg["tweaks"]) > 25:
            items.append(self._context.tr("tweaks.configs_confirm_more").format(
                count=len(cfg["tweaks"]) - 25))
        items += [f"- app: {a}" for a in cfg["apps"][:15]]
        items += [f"- redist: {r}" for r in cfg["redists"][:15]]
        if not ConfirmDialog.ask(
                self, items,
                note=self._context.tr("tweaks.configs_confirm_note")):
            return
        self._work_page = self._current_page()
        self.set_busy(True)
        sounds.play("click")
        self._session.config_apply_task(name)

    def _finish_config_apply(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        text = self._context.tr("tweaks.configs_applied").format(
            name=result.get("name", ""), ok=result.get("ok", 0),
            fail=result.get("fail", 0))
        fails = [r for reps in (result.get("tweaks") or [],
                                result.get("apps") or [],
                                result.get("redists") or [])
                 for r in reps if not r.get("ok")]
        if fails:
            text += "\n" + "\n".join(
                f"- {r.get('id')}: {r.get('error', '?')}" for r in fails[:10])
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        self._assistant.say(text, "idle")
        sounds.play("done" if not fails else "error")

    def _on_config_export(self, name: str) -> None:
        from PySide6.QtWidgets import QFileDialog
        from core.sysconfig import slug
        path, _ok = QFileDialog.getSaveFileName(
            self, self._context.tr("tweaks.configs_export"),
            f"{slug(name)}.json", "JSON (*.json)")
        page = self._current_page()
        if not path:
            return
        try:
            done = self._session.config_export(name, path)
        except Exception as exc:  # noqa: BLE001 - диск может быть чужим
            if page is not None and hasattr(page, "setStatus"):
                page.setStatus(
                    self._context.tr("tweaks.configs_export_fail").format(
                        error=exc))
            sounds.play("error")
            return
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("tweaks.configs_exported").format(
                path=done))
        sounds.play("done")

    def _on_config_import(self, path: str) -> None:
        from core.sysconfig import ConfigError
        page = self._current_page()
        try:
            cfg = self._session.config_import(path)
        except (ConfigError, OSError, ValueError) as exc:
            if page is not None and hasattr(page, "setStatus"):
                page.setStatus(
                    self._context.tr("tweaks.configs_import_fail").format(
                        error=exc))
            sounds.play("error")
            return
        self._refresh_configs_on(page)
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(self._context.tr("tweaks.configs_imported").format(
                name=cfg["name"]))
        sounds.play("done")

    def _ask_config_delete(self, name: str) -> None:
        if not ConfirmDialog.ask(
                self,
                [self._context.tr("tweaks.configs_delete_item").format(
                    name=name)],
                note=self._context.tr("tweaks.configs_delete_note")):
            return
        done = self._session.config_delete(name)
        self._refresh_configs()
        page = self._current_page()
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(
                self._context.tr("tweaks.configs_deleted" if done
                                 else "tweaks.configs_delete_fail").format(
                    name=name))

    def _finish_tweaks(self, result: dict, page: QWidget) -> None:
        self._work_page = None
        self.set_busy(False)
        if page is not None and hasattr(page, "set_tweaks"):
            page.set_tweaks(result.get("startup", []),
                            result.get("services", []),
                            result.get("backups", []),
                            result.get("uwp", []),
                            result.get("sys_tweaks", []))
        sounds.play("done")
        self._assistant.say(
            self._context.tr("tweaks.loaded_status").format(
                startup=len(result.get("startup", [])),
                services=len(result.get("services", [])),
                backups=len(result.get("backups", []))),
            "idle")

    def _finish_tweaks_action(self, result: dict, page: QWidget) -> None:
        """Отключение/возврат прошли: статус + обновить списки по свежему."""
        self._work_page = None
        self.set_busy(False)
        action = str(result.get("action", ""))
        if action == "tweak_apply":
            key = "tweaks.tweak_status"
        elif action == "preset":
            key = "tweaks.preset_status"
        elif action == "shutdown_set":
            key = "tweaks.shutdown_set_status"
        elif action == "shutdown_cancel":
            key = "tweaks.shutdown_cancel_status"
        elif action == "apps_install":
            key = "tweaks.apps_status"
        elif action == "activation":
            key = "tweaks.activation_status"
        elif action == "gpedit":
            key = "tweaks.gpedit_status"
        else:
            key = ("tweaks.disabled_status"
                   if action.endswith("disable") or action == "uwp_remove"
                   else "tweaks.restored_status")
        text = self._context.tr(key).format(
            name=result.get("target", ""),
            done=result.get("preset_ok", result.get("target", "")),
            total=result.get("preset_total", ""))
        if page is not None and hasattr(page, "setStatus"):
            page.setStatus(text)
        sounds.play("done")
        self._assistant.say(text, "calm")
        # Списки изменились: перечитать (новая занятость — новый цикл).
        self._session.load_tweaks()

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
            # База - детерминированный текст правил (работает без модели);
            # модель, если включена и ответила, добавляет свой абзац сверху.
            plan = str(result.get("rules_text") or "")
            if not plan:
                if result["recs"]:
                    lines = [
                        f"{r.get('display_name', r.get('name', '?'))}: "
                        f"{r.get('description', '')}"
                        for r in result["recs"][:10]
                    ]
                    plan = "\n".join(lines)
                else:
                    plan = self._context.tr("advisor.no_plan")
            ai_text = str(result.get("ai_text") or "").strip()
            if ai_text:
                plan = (self._context.tr("advisor.ai_note") + "\n"
                        + ai_text + "\n\n" + plan)
            page.setPlan(plan)
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
        if hasattr(page, "set_categories"):
            # Экран категорий: сводки из стрима скана — они полны всегда,
            # даже когда поштучный список не влез в память.
            meta = self._session.cat_meta()
            page.set_categories([
                {
                    "id": summary.id,
                    "title": str(meta.get(summary.id, {}).get("title",
                                                              summary.id)),
                    "files": summary.files,
                    "bytes": summary.bytes,
                    "lane": summary.lane,
                    "risk": summary.risk,
                    "regrows": summary.regrows,
                    "admin": summary.admin,
                }
                for summary in scan.summaries
            ])
        sounds.play("done")
        self._assistant.say(
            self._context.tr("character.lines.clean"), "idle")

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

    def _sweep_accent(self) -> None:
        """Провести акцентную полоску под шапкой заново."""
        self._accent_bar.sweep()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        """Esc во время работы — отмена текущей задачи (сигнал ядру)."""
        if event.key() == Qt.Key.Key_Escape and self.is_busy():
            self._session.request_cancel()
            if self._status_label is not None:
                self._status_label.setText(
                    self._context.tr("status.cancelling"))
            event.accept()
            return
        super().keyPressEvent(event)

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
        # Открытый модальный диалог подтверждения тоже переодевается.
        for dlg in self.findChildren(ConfirmDialog):
            dlg.retranslate()
        self._status_label.setText(self._context.tr("status.ready"))
        self._apply_visuals()

    # ---------- статус ----------

    def status(self) -> str:
        return self._status

    def setStatus(self, status: str) -> None:
        self._status = status
        self._status_label.setText(status)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        """Закрытие окна: гасим сессию (отмена задачи, стоп ядра, пул).

        Без этого swagscan.exe оставался сиротой в диспетчере задач
        (найдено контрольным аудитом): процесс жил с открытыми pipe'ами.
        """
        try:
            self._session.shutdown()
        except Exception:  # noqa: BLE001 - закрытие окна не должно падать
            pass
        super().closeEvent(event)
