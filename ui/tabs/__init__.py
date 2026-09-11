"""Страницы SWAGcleaner — разделы бокового меню.

Пока это каркас: у каждой страницы есть заголовок, пояснение, область
результата и кнопки действий. Кнопки шлют сигналы, а к ядру их подключим
следующим шагом.

Важное про локализацию: страницы умеют переводить себя заново —
retranslate() переставляет все подписи, поэтому переключение языка
меняет интерфейс целиком, а не только пункты меню.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.context import ctx
from ui.theme import body, button, card, divider, heading, hint, section, spacer, subheading


class EmptyTab(QWidget):
    """Базовая страница: заголовок, пояснение, область результата и кнопки."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: List[Tuple[QPushButton, str]] = []
        self._section_labels: List[Tuple[QLabel, str]] = []
        self._title_label: Optional[QLabel] = None
        self._sub_label: Optional[QLabel] = None
        self._desc_label: Optional[QLabel] = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(28, 24, 28, 22)
        self._layout.setSpacing(6)
        self._build()

    # ---------- сборка ----------

    def _build(self) -> None:
        self._title_label = heading(self._title())
        self._sub_label = subheading(self._subheading())
        # Описание страницы — это полезный текст, а не сноска: делаем его
        # вторичным цветом, а не самым бледным.
        self._desc_label = body(self._description())
        self._desc_label.setProperty("role", "secondary")
        self._layout.addWidget(self._title_label)
        self._layout.addWidget(self._sub_label)
        self._layout.addWidget(self._desc_label)
        self._layout.addWidget(spacer(8))
        self._layout.addWidget(divider())
        self._layout.addWidget(spacer(8))
        self._add_result_area()
        self._layout.addStretch(1)
        self._add_buttons()

    def _add_result_area(self) -> None:
        """Область, где появятся результаты (заполняют подклассы)."""

    def _add_buttons(self) -> None:
        """Кнопки действий (заполняют подклассы)."""

    def _make_card(self) -> Tuple[QFrame, QVBoxLayout]:
        """Карточка для результатов."""
        frame = card(self)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        return frame, layout

    def _add_button(self, text_key: str, primary: bool = False) -> QPushButton:
        """Создать кнопку и запомнить её ключ перевода."""
        widget = button(ctx().tr(text_key), self, primary=primary)
        widget.setMinimumHeight(38)
        self._buttons.append((widget, text_key))
        return widget

    def _add_section_label(self, text_key: str, card_layout: QVBoxLayout) -> QLabel:
        """Заголовок блока внутри карточки (тоже переводится)."""
        label = section(ctx().tr(text_key), self)
        self._section_labels.append((label, text_key))
        card_layout.addWidget(label)
        return label

    def _add_row(self, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        self._layout.addLayout(row)
        return row

    # ---------- тексты ----------

    def _title(self) -> str:
        return ""

    def _subheading(self) -> str:
        return ""

    def _description(self) -> str:
        return ""

    def retranslate(self) -> None:
        """Перевести все подписи страницы заново."""
        if self._title_label is not None:
            self._title_label.setText(self._title())
        if self._sub_label is not None:
            self._sub_label.setText(self._subheading())
        if self._desc_label is not None:
            self._desc_label.setText(self._description())
        for label, key in self._section_labels:
            label.setText(ctx().tr(key))
        for widget, key in self._buttons:
            widget.setText(ctx().tr(key))


class AdvisorTab(EmptyTab):
    """Советник: скан → план → подтверждение пользователя."""

    scanRequested = Signal()
    applyRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._plan_area: Optional[QLabel] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._add_section_label("advisor.plan_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._plan_area = body(self._plan_text(), self)
        self._plan_area.setProperty("role", "secondary")
        layout.addWidget(self._status_label)
        layout.addWidget(self._plan_area)
        self._layout.addWidget(frame)

    def _add_buttons(self) -> None:
        scan = self._add_button("advisor.scan_button", primary=True)
        scan.clicked.connect(self.scanRequested.emit)
        apply_button = self._add_button("advisor.apply_button", primary=True)
        apply_button.clicked.connect(self.applyRequested.emit)
        self._add_row(scan, apply_button)
        self._layout.addStretch(1)

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


class CleanerTab(EmptyTab):
    """Чистка: скан путей → кандидаты → удаление только в корзину."""

    scanRequested = Signal()
    cleanRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._candidates_area: Optional[QLabel] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._add_section_label("cleaner.candidates_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._candidates_area = body(self._candidates_text(), self)
        self._candidates_area.setProperty("role", "secondary")
        layout.addWidget(self._status_label)
        layout.addWidget(self._candidates_area)
        self._layout.addWidget(frame)

    def _add_buttons(self) -> None:
        scan = self._add_button("cleaner.scan_button", primary=True)
        scan.clicked.connect(self.scanRequested.emit)
        clean = self._add_button("cleaner.clean_button", primary=True)
        clean.clicked.connect(self.cleanRequested.emit)
        self._add_row(scan, clean)
        self._layout.addStretch(1)

    def _status_text(self) -> str:
        return ctx().tr("cleaner.scan_empty")

    def _candidates_text(self) -> str:
        return ctx().tr("cleaner.candidates_empty")

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def setCandidates(self, text: str) -> None:
        if self._candidates_area is not None:
            self._candidates_area.setText(text)

    def _title(self) -> str:
        return ctx().tr("cleaner.title")

    def _subheading(self) -> str:
        return ctx().tr("cleaner.scan_hint")

    def _description(self) -> str:
        return ctx().tr("cleaner.candidates_empty")


class DedupTab(EmptyTab):
    """Дубликаты фото: папка → скан → удаление в корзину."""

    chooseFolderRequested = Signal()
    scanRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._groups_area: Optional[QLabel] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._add_section_label("dedup.groups_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._groups_area = body(self._groups_text(), self)
        self._groups_area.setProperty("role", "secondary")
        layout.addWidget(self._status_label)
        layout.addWidget(self._groups_area)
        self._layout.addWidget(frame)

    def _add_buttons(self) -> None:
        folder = self._add_button("dedup.folder_button")
        folder.clicked.connect(self.chooseFolderRequested.emit)
        scan = self._add_button("dedup.scan_button", primary=True)
        scan.clicked.connect(self.scanRequested.emit)
        delete = self._add_button("dedup.delete_button", primary=True)
        delete.clicked.connect(self.deleteRequested.emit)
        self._add_row(folder, scan, delete)
        self._layout.addStretch(1)

    def _status_text(self) -> str:
        return ctx().tr("dedup.scan_empty")

    def _groups_text(self) -> str:
        return ctx().tr("dedup.groups_empty")

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def setGroups(self, text: str) -> None:
        if self._groups_area is not None:
            self._groups_area.setText(text)

    def _title(self) -> str:
        return ctx().tr("dedup.title")

    def _subheading(self) -> str:
        return ctx().tr("dedup.subtitle")

    def _description(self) -> str:
        return ctx().tr("dedup.scan_hint")


class TweaksTab(EmptyTab):
    """Твики: автозагрузка, службы, встроенные приложения, точка восстановления."""

    startupRequested = Signal()
    servicesRequested = Signal()
    uwpRequested = Signal()
    restoreRequested = Signal()

    def _add_buttons(self) -> None:
        startup = self._add_button("tweaks.startup_button")
        startup.clicked.connect(self.startupRequested.emit)
        services = self._add_button("tweaks.services_button")
        services.clicked.connect(self.servicesRequested.emit)
        uwp = self._add_button("tweaks.uwp_button")
        uwp.clicked.connect(self.uwpRequested.emit)
        restore = self._add_button("tweaks.restore_button")
        restore.clicked.connect(self.restoreRequested.emit)
        self._add_row(startup, services)
        self._add_row(uwp, restore)
        self._layout.addStretch(1)

    def _title(self) -> str:
        return ctx().tr("tweaks.title")

    def _description(self) -> str:
        return ctx().tr("tweaks.running")


class SettingsTab(EmptyTab):
    """Настройки: язык, тема и шрифт применяются сразу и запоминаются."""

    languageChanged = Signal(str)
    themeChanged = Signal(str)
    fontChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        self._lang_combo: Optional[QComboBox] = None
        self._theme_combo: Optional[QComboBox] = None
        self._font_combo: Optional[QComboBox] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()

        self._add_section_label("settings.language_label", layout)
        self._lang_combo = QComboBox(self)
        self._lang_combo.currentIndexChanged.connect(self._on_language_selected)
        layout.addWidget(self._lang_combo)

        self._add_section_label("settings.theme_label", layout)
        self._theme_combo = QComboBox(self)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        layout.addWidget(self._theme_combo)

        self._add_section_label("settings.font_label", layout)
        self._font_combo = QComboBox(self)
        self._font_combo.currentIndexChanged.connect(self._on_font_selected)
        layout.addWidget(self._font_combo)
        layout.addWidget(hint(ctx().tr("settings.font_note"), self))

        self._layout.addWidget(frame)
        self._fill_combos()

    def _add_buttons(self) -> None:
        backup = self._add_button("settings.backup_viewer")
        backup.clicked.connect(self._open_backup_viewer)
        about = self._add_button("menu.about")
        about.clicked.connect(self._show_about)
        self._add_row(backup, about)
        self._layout.addStretch(1)

    def _show_about(self) -> None:
        """Показать окно «О программе»."""
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.about(
            self,
            ctx().tr("menu.about"),
            f"{ctx().tr('app.title')}\n{ctx().tr('app.subtitle')}",
        )

    # ---------- синхронизация со состоянием приложения ----------

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        """Выставить значение, не поднимая сигнал изменения."""
        combo.blockSignals(True)
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _fill_combos(self) -> None:
        if self._lang_combo is not None:
            self._lang_combo.blockSignals(True)
            self._lang_combo.clear()
            for code, name in ctx().supportedLocales().items():
                self._lang_combo.addItem(name, code)
            self._lang_combo.blockSignals(False)
            self._select_data(self._lang_combo, ctx().locale())

        if self._theme_combo is not None:
            self._theme_combo.blockSignals(True)
            self._theme_combo.clear()
            self._theme_combo.addItem(ctx().tr("settings.theme_dark"), "dark")
            self._theme_combo.addItem(ctx().tr("settings.theme_light"), "light")
            self._theme_combo.blockSignals(False)
            self._select_data(self._theme_combo, ctx().theme())

        if self._font_combo is not None:
            self._font_combo.blockSignals(True)
            self._font_combo.clear()
            self._font_combo.addItem(ctx().tr("settings.font_pixel"), "pixel")
            self._font_combo.addItem(ctx().tr("settings.font_default"), "default")
            self._font_combo.blockSignals(False)
            self._select_data(self._font_combo, ctx().fontKind())

    # ---------- обработчики ----------

    def _on_language_selected(self, index: int) -> None:
        if self._lang_combo is None:
            return
        code = self._lang_combo.itemData(index)
        if code:
            ctx().setLocale(str(code))
            self.languageChanged.emit(str(code))

    def _on_theme_selected(self, index: int) -> None:
        if self._theme_combo is None:
            return
        name = self._theme_combo.itemData(index)
        if name:
            ctx().setTheme(str(name))
            self.themeChanged.emit(str(name))

    def _on_font_selected(self, index: int) -> None:
        if self._font_combo is None:
            return
        kind = self._font_combo.itemData(index)
        if kind:
            ctx().setFontKind(str(kind))
            self.fontChanged.emit(str(kind))

    def _open_backup_viewer(self) -> None:
        """Открыть список бэкапов (пока заглушка)."""

    def retranslate(self) -> None:
        super().retranslate()
        self._fill_combos()

    def _title(self) -> str:
        return ctx().tr("settings.title")

    def _subheading(self) -> str:
        return ctx().tr("settings.backup_info")

    def _description(self) -> str:
        return ctx().tr("settings.ollama_note")
