"""Главное окно SWAGcleaner — меню, вкладки, статус-бар, язык, тема."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ui.theme import apply_dark_theme, _qss
from ui.context import ctx, init_context
from ui.tabs import (
    AdvisorTab,
    CleanerTab,
    DedupTab,
    TweaksTab,
    SettingsTab,
)

_LOGGER = logging.getLogger("swag.ui.main")

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMessageBox


class MainWindow(QMainWindow):
    """Главное окно приложения."""

    closed = Signal()

    def __init__(self, app: QApplication, context: Context) -> None:
        super().__init__()
        self._app = app
        self._context = context
        self._status: str = "ready"
        self._init_ui()
        self._init_menu()
        self._apply_theme()
        self._apply_locale()

    def _init_ui(self) -> None:
        self.setWindowTitle(f"{self._context.tr('app.title')} — {self._context.tr('app.subtitle')}")
        self.resize(1000, 640)
        self.setMinimumSize(QSize(800, 500))
        self._create_central()
        self._create_status_bar()

    def _create_central(self) -> None:
        central = QWidget(self)
        central.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._tab_widget = QTabWidget(central)
        self._tab_widget.setTabsClosable(False)
        self._tab_widget.setUsesScrollButtons(False)
        self._tab_widget.setDocumentMode(False)
        self._tab_widget.setStyleSheet("")
        self._tab_widget.addTab(AdvisorTab(), self._context.tr("tabs.advisor"))
        self._tab_widget.addTab(CleanerTab(), self._context.tr("tabs.cleaner"))
        self._tab_widget.addTab(DedupTab(), self._context.tr("tabs.dedup"))
        self._tab_widget.addTab(TweaksTab(), self._context.tr("tabs.tweaks"))
        self._tab_widget.addTab(SettingsTab(), self._context.tr("tabs.settings"))
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tab_widget)
        self.setCentralWidget(central)

    def _on_tab_changed(self, index: int) -> None:
        pass

    def _create_status_bar(self) -> None:
        self._status_bar = QStatusBar(self)
        self._status_label = QLabel(self._context.tr("status.ready"), self._status_bar)
        self._status_label.setProperty("role", "secondary")
        self._status_bar.addPermanentWidget(self._status_label)
        self._status_bar.setStyleSheet("")
        self.setStatusBar(self._status_bar)

    def _init_menu(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(True)
        self._file_menu = menu_bar.addMenu(self._context.tr("menu.file"))
        self._edit_menu = menu_bar.addMenu(self._context.tr("menu.edit"))
        self._view_menu = menu_bar.addMenu(self._context.tr("menu.view"))
        self._help_menu = menu_bar.addMenu(self._context.tr("menu.help"))

        self._settings_action = self._view_menu.addAction(self._context.tr("menu.settings"))
        self._settings_action.triggered.connect(self._show_settings)

        # Язык
        self._lang_menu = self._file_menu.addMenu(self._context.tr("menu.language"))
        self._populate_languages()

        # О программе
        self._about_action = self._help_menu.addAction(self._context.tr("menu.about"))
        self._about_action.triggered.connect(self._show_about)

    def _populate_languages(self) -> None:
        self._lang_menu.clear()
        for code, name in self._context.supportedLocales().items():
            action = self._lang_menu.addAction(name)
            action.setData(code)
            action.triggered.connect(self._switch_language)
            if code == self._context.locale():
                action.setProperty("checked", True)

    def _switch_language(self) -> None:
        sender = self.sender()
        if sender is None:
            return
        code = sender.data()
        if code and code != self._context.locale():
            self._context.setLocale(code)
            self._apply_locale()
            self._populate_languages()
            self._refresh_tabs()

    def _refresh_tabs(self) -> None:
        self._tab_widget.setTabText(0, self._context.tr("tabs.advisor"))
        self._tab_widget.setTabText(1, self._context.tr("tabs.cleaner"))
        self._tab_widget.setTabText(2, self._context.tr("tabs.dedup"))
        self._tab_widget.setTabText(3, self._context.tr("tabs.tweaks"))
        self._tab_widget.setTabText(4, self._context.tr("tabs.settings"))
        self.setWindowTitle(f"{self._context.tr('app.title')} — {self._context.tr('app.subtitle')}")
        self._status_label.setText(self._context.tr("status.ready"))
        self._menu_bar_update()

    def _menu_bar_update(self) -> None:
        self._file_menu.setTitle(self._context.tr("menu.file"))
        self._edit_menu.setTitle(self._context.tr("menu.edit"))
        self._view_menu.setTitle(self._context.tr("menu.view"))
        self._help_menu.setTitle(self._context.tr("menu.help"))
        self._settings_action.setText(self._context.tr("menu.settings"))
        self._about_action.setText(self._context.tr("menu.about"))

    def _apply_theme(self) -> None:
        self._app.setStyleSheet(_qss())
        self._app.setFont(QFont("Segoe UI, Roboto, sans-serif", 13))

    def _apply_locale(self) -> None:
        pass

    def _show_settings(self) -> None:
        self._tab_widget.setCurrentIndex(4)

    def _show_about(self) -> None:
        from ui.theme import _qss as _unused
        _ = _unused
        from PySide6.QtWidgets import QMessageBox as MBS
        MBS.about(
            self,
            self._context.tr("menu.about"),
            self._context.tr("app.title") + "\n" + self._context.tr("app.subtitle"),
        )

    def status(self) -> str:
        return self._status

    def setStatus(self, status: str) -> None:
        self._status = status
        self._status_label.setText(status)
