"""Главное окно SWAGcleaner.

Слева — сворачивающееся боковое меню (Sidebar), справа — шапка с быстрыми
переключателями и стек страниц. Меню и шапка всегда на месте, меняется
только содержимое.

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
    QPropertyAnimation,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ui import icons, theme
from ui.context import Context
from ui.scene import SceneStack
from ui.sidebar import Sidebar
from ui.tabs import AdvisorTab, CleanerTab, DedupTab, SettingsTab, TweaksTab

_LOGGER = logging.getLogger("swag.ui.main")

# Разделы приложения: иконка, ключ названия, класс страницы.
PAGES = (
    ("advisor", "tabs.advisor", AdvisorTab),
    ("cleaner", "tabs.cleaner", CleanerTab),
    ("dedup", "tabs.dedup", DedupTab),
    ("tweaks", "tabs.tweaks", TweaksTab),
    ("settings", "tabs.settings", SettingsTab),
)


class MainWindow(QMainWindow):
    """Главное окно приложения."""

    closed = Signal()

    # Длительность проезда акцентной полоски под шапкой.
    ACCENT_SWEEP_MS = 340

    def __init__(self, app: QApplication, context: Context) -> None:
        super().__init__()
        self._app = app
        self._context = context
        self._status = "ready"
        self._pages: List[QWidget] = []

        self._build_ui()
        self._connect_context()
        self._apply_visuals()
        self.retranslate()
        self.go_to_page(0)

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
        for icon_name, text_key, _page_cls in PAGES:
            self._sidebar.add_item(icon_name, text_key)
        self._sidebar.pageSelected.connect(self.go_to_page)
        root.addWidget(self._sidebar)

        content = QWidget(central)
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self._build_stack(), 1)
        root.addWidget(content, 1)

        self.setCentralWidget(central)
        self._build_status_bar()

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

        self._language_button = self._make_header_button("language")
        self._language_button.setText(self._context.locale().upper())
        self._language_button.clicked.connect(self.toggle_language)
        self._language_button.setIconSize(QSize(20, 20))
        self._language_button.setMinimumWidth(84)
        layout.addWidget(self._language_button)

        outer.addWidget(row)

        # Акцентная полоска под шапкой. Она выезжает слева направо каждый раз,
        # когда меняется раздел — тот самый «переход сцены».
        self._accent_bar = QFrame(header)
        self._accent_bar.setObjectName("accentBar")
        self._accent_bar.setFixedHeight(2)
        self._accent_bar.setMaximumWidth(0)
        outer.addWidget(self._accent_bar)

        self._accent_animation = QPropertyAnimation(self._accent_bar, b"maximumWidth", self)
        self._accent_animation.setDuration(self.ACCENT_SWEEP_MS)
        self._accent_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

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
        for _icon, _key, page_cls in PAGES:
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

    def _apply_visuals(self) -> None:
        theme.apply_theme(self._app, self._context.theme(), self._context.fontKind())
        colors = theme.palette(self._context.theme())
        self._sidebar.apply_colors(colors)
        self._theme_button.setIcon(
            icons.icon(
                "theme_light" if self._context.theme() == "dark" else "theme_dark",
                colors["text_secondary"],
                20,
            )
        )
        self._language_button.setIcon(icons.icon("language", colors["text_secondary"], 20))
        self._refresh_sidebar_caption()

    def _refresh_sidebar_caption(self) -> None:
        self._sidebar.set_caption(self._context.tr("sidebar.caption"))

    def _on_theme_changed(self, _theme: str) -> None:
        self._apply_visuals()
        self.retranslate()

    def _on_font_changed(self, _font_kind: str) -> None:
        self._apply_visuals()
        self.retranslate()

    # ---------- навигация ----------

    def go_to_page(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            self._stack.setCurrentIndex(index)
            self._sidebar.set_current(index)

    def _on_page_changed(self, index: int) -> None:
        self._sidebar.set_current(index)
        self._sweep_accent()

    def _sweep_accent(self) -> None:
        """Провести акцентную полоску под шапкой заново."""
        full_width = max(self._header.width(), 1)
        self._accent_animation.stop()
        self._accent_bar.setMaximumWidth(0)
        self._accent_animation.setStartValue(0)
        self._accent_animation.setEndValue(full_width)
        self._accent_animation.start()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        # Полоска должна занимать всю шапку и после изменения размера окна.
        # Если она как раз выезжает — правим не ширину, а цель анимации.
        full_width = max(self._header.width(), 1)
        if self._accent_animation.state() == QAbstractAnimation.State.Stopped:
            self._accent_bar.setMaximumWidth(full_width)
        else:
            self._accent_animation.setEndValue(full_width)

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
        self._sidebar.retranslate()
        self._refresh_sidebar_caption()
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
