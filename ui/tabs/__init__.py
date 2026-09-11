"""Вкладки SWAGcleaner — заглушки с кнопками для MVP.

Каждая вкладка — самостоятельный виджет с заглушками кнопок.
Позже заменим на реальную логику.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QComboBox,
)

from ui.theme import heading, subheading, body, hint, button, spacer
from ui.context import ctx


class EmptyTab(QWidget):
    """Базовая вкладка с заглушкой."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        self._init()

    def _init(self) -> None:
        h = heading(self._title())
        h.setProperty("role", "secondary")
        sub = subheading(self._subheading())
        sub.setProperty("role", "secondary")
        desc = body(self._description())
        desc.setProperty("role", "secondary")
        desc.setWordWrap(True)
        self._layout.addWidget(h)
        self._layout.addWidget(sub)
        self._layout.addWidget(desc)
        self._layout.addWidget(spacer())
        self._add_buttons()
        self._layout.addStretch()

    def _title(self) -> str:
        return ""

    def _subheading(self) -> str:
        return ""

    def _description(self) -> str:
        return ""

    def _add_buttons(self) -> None:
        pass


class AdvisorTab(EmptyTab):
    """Вкладка советника — пока заглушка с видимыми кнопками и слотами.

    Планируется: скан → план → подтверждение → применение.
    """

    scanRequested = Signal()
    applyRequested = Signal()

    _status_label: QLabel | None = None
    _plan_area: QLabel | None = None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def _init(self) -> None:
        # Шапку строим сами (как в остальных вкладках): иначе кнопки
        # добавлялись бы дважды, а статус и план уезжали под растяжку.
        h = heading(self._title())
        h.setProperty("role", "secondary")
        sub = subheading(self._subheading())
        sub.setProperty("role", "secondary")
        desc = body(self._description())
        desc.setProperty("role", "secondary")
        desc.setWordWrap(True)
        self._layout.addWidget(h)
        self._layout.addWidget(sub)
        self._layout.addWidget(desc)
        # Статус-строка, видимая поверх кнопок
        self._status_label = body(self._status_text())
        self._status_label.setProperty("role", "secondary")
        self._status_label.setWordWrap(True)
        self._layout.addWidget(self._status_label)
        self._layout.addWidget(spacer())
        # План — пока только заглушка, но видимый контейнер
        self._plan_area = body(self._plan_text())
        self._plan_area.setProperty("role", "secondary")
        self._plan_area.setWordWrap(True)
        self._layout.addWidget(self._plan_area)
        self._layout.addWidget(spacer())
        self._add_buttons()
        self._layout.addStretch()

    def _status_text(self) -> str:
        return ctx().tr("advisor.scan_empty")

    def _plan_text(self) -> str:
        return ctx().tr("advisor.no_plan")

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def setPlan(self, text: str) -> None:
        if self._plan_area is not None:
            self._plan_area.setText(text)

    def _title(self) -> str:
        return ctx().tr("advisor.title")

    def _subheading(self) -> str:
        return ctx().tr("advisor.subtitle")

    def _description(self) -> str:
        return ctx().tr("advisor.scan_hint")

    def _add_buttons(self) -> None:
        scan_btn = button(ctx().tr("advisor.scan_button"), primary=True)
        scan_btn.setMinimumHeight(36)
        scan_btn.clicked.connect(self.scanRequested.emit)
        apply_btn = button(ctx().tr("advisor.apply_button"), primary=True)
        apply_btn.setMinimumHeight(36)
        apply_btn.clicked.connect(self.applyRequested.emit)
        # Кнопки приоритетнее — ставим их после статуса/плана,
        # но до растягивающего элемента.
        self._layout.addWidget(scan_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(apply_btn)


class CleanerTab(EmptyTab):
    """Вкладка чистки — пока заглушка с видимыми кнопками.

    Планируется: скан путей → список кандидатов → удаление в корзину.
    """

    scanRequested = Signal()
    cleanRequested = Signal()

    _status_label: QLabel | None = None
    _candidates_area: QLabel | None = None

    def __init__(self, parent: QWidget | None = None) -> None:
        # Метки создаёт _init(), здесь их обнулять нельзя —
        # иначе setStatus()/setCandidates() молча ничего не делают.
        super().__init__(parent)

    def _init(self) -> None:
        h = heading(self._title())
        h.setProperty("role", "secondary")
        sub = subheading(self._subheading())
        sub.setProperty("role", "secondary")
        desc = body(self._description())
        desc.setProperty("role", "secondary")
        desc.setWordWrap(True)
        self._layout.addWidget(h)
        self._layout.addWidget(sub)
        self._layout.addWidget(desc)
        self._status_label = body(self._status_text())
        self._status_label.setProperty("role", "secondary")
        self._status_label.setWordWrap(True)
        self._layout.addWidget(self._status_label)
        self._layout.addWidget(spacer())
        self._candidates_area = body(self._candidates_text())
        self._candidates_area.setProperty("role", "secondary")
        self._candidates_area.setWordWrap(True)
        self._layout.addWidget(self._candidates_area)
        self._layout.addWidget(spacer())
        self._add_buttons()
        self._layout.addStretch()

    def _status_text(self) -> str:
        return ctx().tr("cleaner.scan_empty")

    def _candidates_text(self) -> str:
        return ctx().tr("cleaner.candidates_empty")

    def _title(self) -> str:
        return ctx().tr("cleaner.title")

    def _subheading(self) -> str:
        return ctx().tr("cleaner.scan_hint")

    def _description(self) -> str:
        return ""

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def setCandidates(self, text: str) -> None:
        if self._candidates_area is not None:
            self._candidates_area.setText(text)

    def _add_buttons(self) -> None:
        scan_btn = button(ctx().tr("cleaner.scan_button"), primary=True)
        scan_btn.setMinimumHeight(36)
        scan_btn.clicked.connect(self.scanRequested.emit)
        clean_btn = button(ctx().tr("cleaner.clean_button"), primary=True)
        clean_btn.setMinimumHeight(36)
        clean_btn.clicked.connect(self.cleanRequested.emit)
        self._layout.addWidget(scan_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(clean_btn)


class DedupTab(EmptyTab):
    """Вкладка поиска дубликатов фото — заглушка с видимыми кнопками.

    Планируется: выбор папки → скан → группы → удаление в корзину.
    """

    chooseFolderRequested = Signal()
    scanRequested = Signal()
    deleteRequested = Signal()

    _status_label: QLabel | None = None
    _groups_area: QLabel | None = None

    def __init__(self, parent: QWidget | None = None) -> None:
        # См. CleanerTab: метки создаёт _init(), обнулять их нельзя.
        super().__init__(parent)

    def _init(self) -> None:
        h = heading(self._title())
        h.setProperty("role", "secondary")
        sub = subheading(self._subheading())
        sub.setProperty("role", "secondary")
        desc = body(self._description())
        desc.setProperty("role", "secondary")
        desc.setWordWrap(True)
        self._layout.addWidget(h)
        self._layout.addWidget(sub)
        self._layout.addWidget(desc)
        self._status_label = body(self._status_text())
        self._status_label.setProperty("role", "secondary")
        self._status_label.setWordWrap(True)
        self._layout.addWidget(self._status_label)
        self._layout.addWidget(spacer())
        self._groups_area = body(self._groups_text())
        self._groups_area.setProperty("role", "secondary")
        self._groups_area.setWordWrap(True)
        self._layout.addWidget(self._groups_area)
        self._layout.addWidget(spacer())
        self._add_buttons()
        self._layout.addStretch()

    def _status_text(self) -> str:
        return ctx().tr("dedup.scan_empty")

    def _groups_text(self) -> str:
        return ctx().tr("dedup.groups_empty")

    def _title(self) -> str:
        return ctx().tr("dedup.title")

    def _subheading(self) -> str:
        return ctx().tr("dedup.scan_hint")

    def _description(self) -> str:
        return ""

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def setGroups(self, text: str) -> None:
        if self._groups_area is not None:
            self._groups_area.setText(text)

    def _add_buttons(self) -> None:
        folder_btn = button(ctx().tr("dedup.folder_button"))
        folder_btn.setMinimumHeight(36)
        folder_btn.clicked.connect(self.chooseFolderRequested.emit)
        scan_btn = button(ctx().tr("dedup.scan_button"), primary=True)
        scan_btn.setMinimumHeight(36)
        scan_btn.clicked.connect(self.scanRequested.emit)
        delete_btn = button(ctx().tr("dedup.delete_button"), primary=True)
        delete_btn.setMinimumHeight(36)
        delete_btn.clicked.connect(self.deleteRequested.emit)
        self._layout.addWidget(folder_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(scan_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(delete_btn)


class TweaksTab(EmptyTab):
    """Вкладка твиков — заглушка с видимыми кнопками.

    Планируется: автозагрузка, службы, UWP, точки восстановления.
    """

    startupRequested = Signal()
    servicesRequested = Signal()
    uwpRequested = Signal()
    restoreRequested = Signal()

    def _title(self) -> str:
        return ctx().tr("tweaks.title")

    def _subheading(self) -> str:
        return ""

    def _description(self) -> str:
        return ""

    def _add_buttons(self) -> None:
        startup_btn = button(ctx().tr("tweaks.startup_button"))
        startup_btn.setMinimumHeight(36)
        startup_btn.clicked.connect(self.startupRequested.emit)
        services_btn = button(ctx().tr("tweaks.services_button"))
        services_btn.setMinimumHeight(36)
        services_btn.clicked.connect(self.servicesRequested.emit)
        uwp_btn = button(ctx().tr("tweaks.uwp_button"))
        uwp_btn.setMinimumHeight(36)
        uwp_btn.clicked.connect(self.uwpRequested.emit)
        restore_btn = button(ctx().tr("tweaks.restore_button"))
        restore_btn.setMinimumHeight(36)
        restore_btn.clicked.connect(self.restoreRequested.emit)
        self._layout.addWidget(startup_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(services_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(uwp_btn)
        self._layout.addWidget(spacer())
        self._layout.addWidget(restore_btn)


class SettingsTab(EmptyTab):
    """Вкладка настроек — заглушка с видимыми переключателями.

    Планируется: язык, тема, исключения, бэкапы, Ollama.
    """

    languageChanged = Signal(str)
    themeChanged = Signal(str)

    def _title(self) -> str:
        return ctx().tr("settings.title")

    def _subheading(self) -> str:
        return ""

    def _description(self) -> str:
        return ctx().tr("settings.backup_info")

    def _add_buttons(self) -> None:
        lang_label = subheading(ctx().tr("settings.language_label"))
        lang_combo = QComboBox()
        for code, name in ctx().supportedLocales().items():
            lang_combo.addItem(name, code)
        lang_combo.setCurrentText(ctx().supportedLocales().get(ctx().locale(), "Russian"))
        lang_combo.currentTextChanged.connect(
            lambda text: self.languageChanged.emit(lang_combo.itemData(lang_combo.findText(text)))
        )
        self._layout.addWidget(lang_label)
        self._layout.addWidget(lang_combo)
        self._layout.addWidget(spacer())
        theme_label = subheading(ctx().tr("settings.theme_label"))
        theme_combo = QComboBox()
        theme_combo.addItem(ctx().tr("settings.theme_dark"), "dark")
        theme_combo.addItem(ctx().tr("settings.theme_light"), "light")
        theme_combo.setCurrentText(ctx().tr("settings.theme_dark"))
        theme_combo.currentTextChanged.connect(
            lambda text: self.themeChanged.emit(theme_combo.itemData(theme_combo.findText(text)))
        )
        self._layout.addWidget(theme_label)
        self._layout.addWidget(theme_combo)
        self._layout.addWidget(spacer())
        backup_btn = button(ctx().tr("settings.backup_viewer"))
        backup_btn.setMinimumHeight(36)
        backup_btn.clicked.connect(self._open_backup_viewer)
        self._layout.addWidget(backup_btn)

    def _open_backup_viewer(self) -> None:
        pass
