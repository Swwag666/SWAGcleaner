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
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.context import ctx
from ui.session import human_size
from ui.theme import body, button, card, divider, heading, hint, section, spacer, subheading
from ui.widgets import StatsRow


class EmptyTab(QWidget):
    """Базовая страница: заголовок, пояснение, область результата и кнопки."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: List[Tuple[QPushButton, str]] = []
        self._section_labels: List[Tuple[QLabel, str]] = []
        self._title_label: Optional[QLabel] = None
        self._sub_label: Optional[QLabel] = None
        self._desc_label: Optional[QLabel] = None
        self._result_frame: Optional[QFrame] = None

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
        self._add_stats()
        # Страница с растущей областью результата (экран категорий) отдаёт ей
        # всё свободное место; остальные прижимают кнопки к низу распоркой.
        if self._result_frame is not None and self._result_expands():
            self._layout.setStretchFactor(self._result_frame, 1)
        else:
            self._layout.addStretch(1)
        self._add_buttons()

    def _result_expands(self) -> bool:
        """True, если область результата должна забирать свободную высоту."""
        return False

    def _add_result_area(self) -> None:
        """Область, где появятся результаты (заполняют подклассы)."""

    # ---------- показатели и прогресс ----------

    def _stat_tiles(self) -> Tuple[Tuple[str, str, Optional[str], int], ...]:
        """Показатели страницы: ключ, подпись, единица, знаков после запятой."""
        return ()

    def _add_stats(self) -> None:
        """Ряд показателей и полоса прогресса — их заполняют, когда приходят данные."""
        self._stats = StatsRow(self)
        for key, caption_key, unit_key, decimals in self._stat_tiles():
            self._stats.add(key, caption_key, unit_key, decimals)
        if self._stat_tiles():
            self._layout.addWidget(self._stats)
            self._layout.addWidget(spacer(6))

        self._progress = QProgressBar(self)
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(18)
        self._progress.setVisible(False)
        self._layout.addWidget(self._progress)

    def stats(self) -> StatsRow:
        return self._stats

    def setStats(self, key: str, value: float) -> None:  # noqa: N802
        """Показать число: оно набежит до нужного значения само."""
        self._stats.setValue(key, value)

    def progress(self) -> QProgressBar:
        return self._progress

    def set_progress(self, percent: Optional[int]) -> None:
        """Полоса прогресса: число процентов или None, чтобы спрятать."""
        if percent is None:
            self._progress.setVisible(False)
            return
        self._progress.setVisible(True)
        self._progress.setValue(max(0, min(100, int(percent))))

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
        self._stats.retranslate()


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
        self._result_frame = frame
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

    def _stat_tiles(self) -> Tuple[Tuple[str, str, Optional[str], int], ...]:
        return (
            ("apps", "advisor.stats_apps", None, 0),
            ("recs", "advisor.stats_recs", None, 0),
        )

    def setPlan(self, text: str) -> None:
        if self._plan_area is not None:
            self._plan_area.setText(text)

    def _title(self) -> str:
        return ctx().tr("advisor.title")

    def _subheading(self) -> str:
        return ctx().tr("advisor.subtitle")

    def _description(self) -> str:
        return ctx().tr("advisor.scan_hint")


class CategoryCard(QFrame):
    """Карточка категории на экране чистки.

    Категория — первичная сущность выбора: галочка решает, поедет ли вся
    категория в удаление. Карточка показывает всё, что известно о категории:
    название (из локализации, с отступлением на заголовок ядра), сколько
    файлов и байтов нашёл скан, дорожку удаления (корзина/без корзины),
    риск и пометки «нужен администратор» / «набегает снова».
    """

    toggled = Signal(str, bool)

    def __init__(self, data: Dict[str, object],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("categoryCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._id = str(data.get("id", ""))
        self._fallback_title = str(data.get("title", self._id))
        self._files = int(data.get("files", 0))
        self._bytes = int(data.get("bytes", 0))
        self._lane = str(data.get("lane", "trash"))
        self._risk = str(data.get("risk", "medium"))
        self._regrows = bool(data.get("regrows", False))
        self._admin = bool(data.get("admin", False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._check = QCheckBox(self)
        self._check.setChecked(True)
        self._check.toggled.connect(self._on_check)
        top.addWidget(self._check, 1)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self._files_label = QLabel(self)
        self._files_label.setProperty("role", "secondary")
        bottom.addWidget(self._files_label)
        self._lane_label = QLabel(self)
        self._lane_label.setObjectName("laneBadge")
        self._lane_label.setProperty("lane", self._lane)
        bottom.addWidget(self._lane_label)
        self._risk_label = QLabel(self)
        self._risk_label.setObjectName("riskBadge")
        risk = self._risk if self._risk in ("low", "medium", "high") else "low"
        self._risk_label.setProperty("risk", risk)
        bottom.addWidget(self._risk_label)
        self._note_label = QLabel(self)
        self._note_label.setProperty("role", "hint")
        self._note_label.setWordWrap(True)
        bottom.addWidget(self._note_label, 1)
        layout.addLayout(bottom)

        self.retranslate()

    # ---------- состояние ----------

    def category_id(self) -> str:
        return self._id

    def is_checked(self) -> bool:
        return self._check.isChecked()

    def set_checked(self, on: bool) -> None:
        self._check.setChecked(on)

    def files(self) -> int:
        return self._files

    def bytes(self) -> int:
        return self._bytes

    def _on_check(self, on: bool) -> None:
        # Подсветить рамку выбранной: галочка мелкая, рамка видна издалека.
        self.setProperty("checked", "true" if on else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.toggled.emit(self._id, on)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        # Клик в любое место карточки — та же галочка.
        if event.button() == Qt.MouseButton.LeftButton:
            self._check.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---------- тексты ----------

    def _title_text(self) -> str:
        key = f"cats.{self._id.replace('.', '_')}"
        text = ctx().tr(key)
        return text if text != key else self._fallback_title

    def retranslate(self) -> None:
        self._check.setText(self._title_text())
        files = ctx().tr("cleaner.files_fmt").format(files=self._files)
        self._files_label.setText(f"{files} · {human_size(self._bytes)}")
        lane_key = "session.lane_trash_short" if self._lane == "trash" \
            else "session.lane_direct_short"
        self._lane_label.setText(ctx().tr(lane_key))
        risk = self._risk if self._risk in ("low", "medium", "high") else "low"
        self._risk_label.setText(ctx().tr(f"advisor.risk_{risk}"))
        notes: List[str] = []
        if self._admin:
            notes.append(ctx().tr("cats.admin_note"))
        if self._regrows:
            notes.append(ctx().tr("cats.regrows_note"))
        self._note_label.setText(" · ".join(notes))


class JournalPanel(QFrame):
    """Экран журнала после удаления: что ушло, по каким дорожкам, что отказало.

    Показывает ПОСЛЕДНИЙ отчёт об удалении (PurgeReport): итоговую строку,
    разбивку по дорожкам, отказы и ошибки с причинами. История между запусками
    живёт отдельно — в файле журнала (core/journal.py).
    """

    _MAX_ROWS = 12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("categoryCard")
        self._report = None
        self._summary_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        self._title_label = section(ctx().tr("journal.title"), self)
        layout.addWidget(self._title_label)
        self._summary_label = body("", self)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)
        self._cancelled_label = hint(ctx().tr("journal.cancelled"), self)
        layout.addWidget(self._cancelled_label)
        self._lanes_label = body("", self)
        self._lanes_label.setProperty("role", "secondary")
        layout.addWidget(self._lanes_label)

        scroll = QScrollArea(self)
        scroll.setObjectName("categoryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(90)
        host = QWidget(scroll)
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        self._hint_label = hint(ctx().tr("journal.hint"), self)
        layout.addWidget(self._hint_label)
        self.setVisible(False)

    def _clear_rows(self) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_row(self, text: str, role: str = "secondary") -> None:
        label = body(text, self)
        label.setProperty("role", role)
        label.setWordWrap(True)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, label)

    def show_report(self, report, summary: str) -> None:
        """Показать отчёт об удалении; summary — готовая итоговая строка."""
        self._report = report
        self._summary_text = summary
        self._render()
        self.setVisible(True)

    def _render(self) -> None:
        report = self._report
        self._title_label.setText(ctx().tr("journal.title"))
        self._hint_label.setText(ctx().tr("journal.hint"))
        self._cancelled_label.setText(ctx().tr("journal.cancelled"))
        if report is None:
            return
        self._summary_label.setText(self._summary_text)
        self._cancelled_label.setVisible(bool(report.cancelled))
        lanes = getattr(report, "lanes", {}) or {}
        lane_parts = []
        if lanes.get("trash"):
            lane_parts.append(ctx().tr("journal.lane_trash").format(
                count=lanes["trash"]))
        if lanes.get("direct"):
            lane_parts.append(ctx().tr("journal.lane_direct").format(
                count=lanes["direct"]))
        self._lanes_label.setText(" · ".join(lane_parts))
        self._lanes_label.setVisible(bool(lane_parts))

        self._clear_rows()
        rejects = list(getattr(report, "rejects", []) or [])
        failures = list(getattr(report, "failures", []) or [])
        if rejects:
            self._add_row(ctx().tr("journal.rejects").format(count=len(rejects)),
                          role="section")
            for item in rejects[:self._MAX_ROWS]:
                self._add_row(f"{item.get('path', '?')} — {item.get('reason', '')}")
            if len(rejects) > self._MAX_ROWS:
                self._add_row(ctx().tr("journal.more").format(
                    count=len(rejects) - self._MAX_ROWS), role="hint")
        if failures:
            self._add_row(ctx().tr("journal.failures").format(count=len(failures)),
                          role="section")
            for item in failures[:self._MAX_ROWS]:
                self._add_row(f"{item.get('path', '?')} — {item.get('reason', '')}")
            if len(failures) > self._MAX_ROWS:
                self._add_row(ctx().tr("journal.more").format(
                    count=len(failures) - self._MAX_ROWS), role="hint")

    def retranslate(self) -> None:
        if self.isVisible():
            self._render()
        else:
            self._title_label.setText(ctx().tr("journal.title"))
            self._hint_label.setText(ctx().tr("journal.hint"))


class CleanerTab(EmptyTab):
    """Чистка: скан → категории карточками с галочками → удаление выбранного.

    Это и есть экран категорий (этап 2): сводки строятся из стрима скана
    ядра, поэтому они полны всегда, даже когда пунктов слишком много для
    показа поштучно. Удаление идёт только по отмеченным категориям.
    """

    scanRequested = Signal()
    cleanRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._cards: List[CategoryCard] = []
        self._cards_layout: Optional[QVBoxLayout] = None
        self._scroll: Optional[QScrollArea] = None
        self._empty_label: Optional[QLabel] = None
        self._selection_label: Optional[QLabel] = None
        self._select_all_button: Optional[QPushButton] = None
        self._select_none_button: Optional[QPushButton] = None
        self._journal: Optional[JournalPanel] = None
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("cleaner.candidates_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        layout.addWidget(self._status_label)

        # Строка выбора: сводка слева, «выбрать всё / снять всё» справа.
        select_row = QHBoxLayout()
        select_row.setSpacing(10)
        self._selection_label = hint("", self)
        select_row.addWidget(self._selection_label, 1)
        self._select_all_button = self._add_button("cleaner.select_all")
        self._select_all_button.clicked.connect(lambda: self.set_all_selected(True))
        self._select_none_button = self._add_button("cleaner.select_none")
        self._select_none_button.clicked.connect(lambda: self.set_all_selected(False))
        select_row.addWidget(self._select_all_button)
        select_row.addWidget(self._select_none_button)
        layout.addLayout(select_row)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Минимум пара карточек на виду даже на низком окне (1024×768).
        self._scroll.setMinimumHeight(150)
        cards_host = QWidget(self._scroll)
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(cards_host)
        layout.addWidget(self._scroll, 1)

        self._empty_label = body(self._candidates_text(), self)
        self._empty_label.setProperty("role", "secondary")
        layout.addWidget(self._empty_label)

        # Журнал после удаления живёт в той же карточке: отчёт заменяет
        # карточки категорий до следующего скана.
        self._journal = JournalPanel(self)
        layout.addWidget(self._journal, 1)

        self._layout.addWidget(frame)
        self._refresh_selection_view()

    def _add_buttons(self) -> None:
        scan = self._add_button("cleaner.scan_button", primary=True)
        scan.clicked.connect(self.scanRequested.emit)
        clean = self._add_button("cleaner.clean_button", primary=True)
        clean.clicked.connect(self.cleanRequested.emit)
        self._add_row(scan, clean)
        # Растяжку не добавляем: её роль играет растущая область результата,
        # иначе кнопки уедут в середину страницы.

    # ---------- данные ----------

    def set_categories(self, cards: List[Dict[str, object]]) -> None:
        """Показать категории скана карточками. Пустой список — «не найдено»."""
        for card_widget in self._cards:
            card_widget.setParent(None)
            card_widget.deleteLater()
        self._cards = []
        assert self._cards_layout is not None
        for data in cards:
            card_widget = CategoryCard(data, self._cards_layout.parentWidget())
            card_widget.toggled.connect(self._on_card_toggled)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1,
                                            card_widget)
            self._cards.append(card_widget)
        # Новый скан сменяет журнал прошлого удаления.
        if self._journal is not None:
            self._journal.setVisible(False)
        for widget in (self._select_all_button, self._select_none_button):
            if widget is not None:
                widget.setVisible(True)
        self._refresh_selection_view()

    def show_journal(self, report, summary: str) -> None:
        """После удаления: карточки уступают место журналу до нового скана."""
        if self._journal is None:
            return
        self._journal.show_report(report, summary)
        for widget in (self._scroll, self._empty_label, self._selection_label,
                       self._select_all_button, self._select_none_button):
            if widget is not None:
                widget.setVisible(False)

    def journal_panel(self) -> Optional[JournalPanel]:
        return self._journal

    def selected_ids(self) -> List[str]:
        """Id отмеченных категорий — именно они поедут в удаление."""
        return [c.category_id() for c in self._cards if c.is_checked()]

    def set_all_selected(self, on: bool) -> None:
        for card_widget in self._cards:
            card_widget.set_checked(on)

    def cards(self) -> List[CategoryCard]:
        return list(self._cards)

    def _on_card_toggled(self, _category_id: str, _on: bool) -> None:
        self._refresh_selection_view()

    def _refresh_selection_view(self) -> None:
        # Пока виден журнал удаления, карточная вёрстка не трогается.
        if self._journal is not None and self._journal.isVisible():
            return
        has_cards = bool(self._cards)
        if self._scroll is not None:
            self._scroll.setVisible(has_cards)
        if self._empty_label is not None:
            self._empty_label.setVisible(not has_cards)
        for widget in (self._select_all_button, self._select_none_button):
            if widget is not None:
                widget.setEnabled(has_cards)
        if self._selection_label is not None:
            selected = [c for c in self._cards if c.is_checked()]
            files = sum(c.files() for c in selected)
            size = sum(c.bytes() for c in selected)
            self._selection_label.setText(
                ctx().tr("cleaner.selected_summary").format(
                    cats=len(selected), total=len(self._cards),
                    files=files, size=human_size(size)))
            self._selection_label.setVisible(has_cards)

    # ---------- тексты ----------

    def retranslate(self) -> None:
        super().retranslate()
        if self._empty_label is not None:
            self._empty_label.setText(self._candidates_text())
        for card_widget in self._cards:
            card_widget.retranslate()
        if self._journal is not None:
            self._journal.retranslate()
        self._refresh_selection_view()

    def _status_text(self) -> str:
        return ctx().tr("cleaner.scan_empty")

    def _candidates_text(self) -> str:
        return ctx().tr("cleaner.candidates_empty")

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def _stat_tiles(self) -> Tuple[Tuple[str, str, Optional[str], int], ...]:
        return (
            ("candidates", "cleaner.stats_candidates", None, 0),
            ("size", "cleaner.stats_size", "cleaner.size_mb", 1),
        )

    def _title(self) -> str:
        return ctx().tr("cleaner.title")

    def _subheading(self) -> str:
        return ctx().tr("cleaner.scan_hint")

    def _description(self) -> str:
        return ctx().tr("cleaner.choose_hint")


class DedupTab(EmptyTab):
    """Дубликаты фото: папка → скан → удаление в корзину."""

    chooseFolderRequested = Signal()
    scanRequested = Signal()
    deleteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._groups_area: Optional[QLabel] = None
        self._journal: Optional[JournalPanel] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("dedup.groups_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._groups_area = body(self._groups_text(), self)
        self._groups_area.setProperty("role", "secondary")
        layout.addWidget(self._status_label)
        layout.addWidget(self._groups_area)
        self._journal = JournalPanel(self)
        layout.addWidget(self._journal, 1)
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

    def _stat_tiles(self) -> Tuple[Tuple[str, str, Optional[str], int], ...]:
        return (
            ("groups", "dedup.stats_groups", None, 0),
            ("dupes", "dedup.stats_dupes", None, 0),
        )

    def setGroups(self, text: str) -> None:
        if self._groups_area is not None:
            self._groups_area.setText(text)
        # Новый скан сменяет журнал прошлого удаления.
        if self._journal is not None:
            self._journal.setVisible(False)
        if self._groups_area is not None:
            self._groups_area.setVisible(True)

    def show_journal(self, report, summary: str) -> None:
        """После удаления дублей: журнал вместо списка групп до нового скана."""
        if self._journal is None:
            return
        self._journal.show_report(report, summary)
        if self._groups_area is not None:
            self._groups_area.setVisible(False)

    def retranslate(self) -> None:
        super().retranslate()
        if self._journal is not None:
            self._journal.retranslate()

    def _title(self) -> str:
        return ctx().tr("dedup.title")

    def _subheading(self) -> str:
        return ctx().tr("dedup.subtitle")

    def _description(self) -> str:
        return ctx().tr("dedup.scan_hint")


class TweaksTab(EmptyTab):
    """Твики: автозагрузка и службы — чтение, отключение со снапшотом, откат.

    M4/M5: списки читаются из реестра и SCM по-настоящему; отключение записи
    автозагрузки идёт со снапшотом на диск (раздел «Можно вернуть» переживает
    перезапуски). Службы пока только читаются — их отключение следующим заходом.
    """

    refreshRequested = Signal()
    disableStartupRequested = Signal(object)   # StartupEntry
    restoreSnapshotRequested = Signal(str)     # имя снапшота
    disableServiceRequested = Signal(str)      # имя службы
    removeUwpRequested = Signal(str)           # PackageFullName

    _MAX_SERVICES = 60
    _MAX_UWP = 60

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._scroll: Optional[QScrollArea] = None
        self._rows_layout: Optional[QVBoxLayout] = None
        self._startup: List[object] = []
        self._services: List[object] = []
        self._backups: List[Dict[str, object]] = []
        self._uwp: List[object] = []
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("tweaks.startup_section", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        layout.addWidget(self._status_label)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setMinimumHeight(150)
        host = QWidget(self._scroll)
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)
        self._layout.addWidget(frame)

    def _add_buttons(self) -> None:
        refresh = self._add_button("tweaks.refresh_button", primary=True)
        refresh.clicked.connect(self.refreshRequested.emit)
        self._add_row(refresh)

    # ---------- данные ----------

    def set_tweaks(self, startup: List[object], services: List[object],
                   backups: List[Dict[str, object]],
                   uwp: Optional[List[object]] = None) -> None:
        """Показать списки: автозагрузка, службы, UWP, снапшоты отката."""
        self._startup = list(startup)
        self._services = list(services)
        self._backups = list(backups)
        self._uwp = list(uwp or [])
        self._render_rows()
        self.setStats("startup", len(self._startup))
        self.setStats("services", len(self._services))
        self.setStats("backups", len(self._backups))
        self.setStatus(ctx().tr("tweaks.loaded_status").format(
            startup=len(self._startup), services=len(self._services),
            backups=len(self._backups)))

    def _render_rows(self) -> None:
        assert self._rows_layout is not None
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        def put(widget: QWidget) -> None:
            # Строчки идут до растяжки: стрейч всегда последний.
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, widget)

        put(section(ctx().tr("tweaks.startup_section"), self))
        if not self._startup:
            put(hint(ctx().tr("tweaks.empty_startup"), self))
        for entry in self._startup:
            put(self._startup_row(entry))

        put(section(ctx().tr("tweaks.backups_section"), self))
        if not self._backups:
            put(hint(ctx().tr("tweaks.empty_backups"), self))
        for info in self._backups:
            put(self._backup_row(info))

        put(section(ctx().tr("tweaks.services_section"), self))
        put(hint(ctx().tr("tweaks.services_hint"), self))
        for service in self._services[:self._MAX_SERVICES]:
            put(self._service_row(service))
        if len(self._services) > self._MAX_SERVICES:
            put(hint(ctx().tr("journal.more").format(
                count=len(self._services) - self._MAX_SERVICES), self))

        put(section(ctx().tr("tweaks.uwp_section"), self))
        put(hint(ctx().tr("tweaks.uwp_hint"), self))
        if not self._uwp:
            put(hint(ctx().tr("tweaks.empty_uwp"), self))
        for pkg in self._uwp[:self._MAX_UWP]:
            put(self._uwp_row(pkg))
        if len(self._uwp) > self._MAX_UWP:
            put(hint(ctx().tr("journal.more").format(
                count=len(self._uwp) - self._MAX_UWP), self))

    def _startup_row(self, entry) -> QFrame:
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        # QSizePolicy.Ignored: у wordWrap-лейбла minimumSizeHint = ширина
        # самого длинного слова (путь!), и layout раздувает строку шире
        # вьюпорта. Ignored обнуляет этот минимум — сжиматься можно.
        name = body(entry.name, row)
        name.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        layout.addWidget(name, 2)
        path = hint(entry.path, row)
        path.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        layout.addWidget(path, 3)
        source_key = ("tweaks.source_registry" if entry.source == "registry"
                      else "tweaks.source_folder")
        layout.addWidget(hint(ctx().tr(source_key), row))
        disable = button(ctx().tr("tweaks.disable_button"), row)
        disable.setMinimumHeight(30)
        disable.clicked.connect(
            lambda _checked=False, e=entry: self.disableStartupRequested.emit(e))
        layout.addWidget(disable)
        return row

    def _backup_row(self, info: Dict[str, object]) -> QFrame:
        import time as _time

        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        name = str(info.get("name", ""))
        shown = name
        for prefix in ("startup-HKCU-", "startup-HKLM-", "service-", "uwp-"):
            if shown.startswith(prefix):
                shown = shown[len(prefix):]
        ts = float(info.get("ts", 0.0))
        when = _time.strftime("%d.%m %H:%M", _time.localtime(ts)) if ts else ""
        label = body(f"{shown} · {when}", row)
        label.setSizePolicy(QSizePolicy.Policy.Ignored,
                            QSizePolicy.Policy.Preferred)
        layout.addWidget(label, 1)
        restore = button(ctx().tr("tweaks.restore_button"), row)
        restore.setMinimumHeight(30)
        restore.clicked.connect(
            lambda _checked=False, n=name: self.restoreSnapshotRequested.emit(n))
        layout.addWidget(restore)
        return row

    def _service_row(self, service) -> QFrame:
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        name = body(service.name, row)
        name.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        layout.addWidget(name, 2)
        state_key = f"tweaks.svc_{service.state}" \
            if service.state in ("running", "stopped", "paused") \
            else "tweaks.svc_other"
        mode_key = f"tweaks.mode_{service.start_mode}" \
            if service.start_mode in ("automatic", "manual", "disabled") \
            else "tweaks.mode_unknown"
        layout.addWidget(hint(ctx().tr(state_key), row))
        layout.addWidget(hint(ctx().tr(mode_key), row))
        if service.start_mode != "disabled":
            disable = button(ctx().tr("tweaks.disable_button"), row)
            disable.setMinimumHeight(30)
            disable.clicked.connect(
                lambda _checked=False, n=service.name:
                    self.disableServiceRequested.emit(n))
            layout.addWidget(disable)
        return row

    def _uwp_row(self, pkg) -> QFrame:
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        name = body(pkg.name, row)
        name.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        layout.addWidget(name, 2)
        remove = button(ctx().tr("tweaks.uwp_remove_button"), row)
        remove.setMinimumHeight(30)
        remove.clicked.connect(
            lambda _checked=False, f=pkg.full_name:
                self.removeUwpRequested.emit(f))
        layout.addWidget(remove)
        return row

    # ---------- тексты ----------

    def _status_text(self) -> str:
        return ctx().tr("tweaks.status_hint")

    def setStatus(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def _stat_tiles(self) -> Tuple[Tuple[str, str, Optional[str], int], ...]:
        return (
            ("startup", "tweaks.stats_startup", None, 0),
            ("services", "tweaks.stats_services", None, 0),
            ("backups", "tweaks.stats_backups", None, 0),
        )

    def retranslate(self) -> None:
        super().retranslate()
        if self._status_label is not None:
            self._status_label.setText(self._status_text())
        if self._rows_layout is not None:
            self._render_rows()

    def _title(self) -> str:
        return ctx().tr("tweaks.title")

    def _subheading(self) -> str:
        return ctx().tr("tweaks.subtitle")

    def _description(self) -> str:
        return ctx().tr("tweaks.status_hint")


class SettingsTab(EmptyTab):
    """Настройки: язык, тема и шрифт применяются сразу и запоминаются."""

    languageChanged = Signal(str)
    themeChanged = Signal(str)
    fontChanged = Signal(str)
    soundsChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        self._lang_combo: Optional[QComboBox] = None
        self._theme_combo: Optional[QComboBox] = None
        self._font_combo: Optional[QComboBox] = None
        self._sounds_check: Optional[QCheckBox] = None
        super().__init__(parent)

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame

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

        self._add_section_label("settings.sounds_label", layout)
        self._sounds_check = QCheckBox(ctx().tr("settings.sounds_label"), self)
        self._sounds_check.setChecked(ctx().soundsEnabled())
        self._sounds_check.toggled.connect(self._on_sounds_toggled)
        layout.addWidget(self._sounds_check)
        layout.addWidget(hint(ctx().tr("settings.sounds_note"), self))

        self._layout.addWidget(frame)
        self._fill_combos()

    def _on_sounds_toggled(self, enabled: bool) -> None:
        ctx().setSounds(enabled)
        self.soundsChanged.emit(enabled)

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
        if self._sounds_check is not None:
            self._sounds_check.blockSignals(True)
            self._sounds_check.setText(ctx().tr("settings.sounds_label"))
            self._sounds_check.setChecked(ctx().soundsEnabled())
            self._sounds_check.blockSignals(False)

    def _title(self) -> str:
        return ctx().tr("settings.title")

    def _subheading(self) -> str:
        return ctx().tr("settings.backup_info")

    def _description(self) -> str:
        return ctx().tr("settings.ollama_note")
