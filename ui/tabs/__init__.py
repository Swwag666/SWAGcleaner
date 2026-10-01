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

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.context import ctx
from ui.session import human_size
from ui.theme import (
    Accordion,
    ConfigCard,
    HeroCard,
    ShimmerProgress,
    apply_role_font,
    body,
    button,
    card,
    chip,
    divider,
    fix_wrap_labels,
    heading,
    hint,
    risk_badge,
    section,
    spacer,
    subheading,
)
from ui.widgets import StatsRow, StorageBar, StorageRow, TreemapWidget


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
        # Пересчёт минимумов скролл-хостов дорогой (сотни виджетов), а
        # resizeEvent при перетаскивании рамки сыплет каждые 16мс:
        # дебаунс таймером вместо singleShot на каждый чих.
        self._layout_sync = QTimer(self)
        self._layout_sync.setSingleShot(True)
        self._layout_sync.setInterval(120)
        self._layout_sync.timeout.connect(self.sync_layout)
        self._build()

    def schedule_layout_sync(self) -> None:
        """Отложить пересчёт раскладки: серия ресайзов платит один раз."""
        if not self._layout_sync.isActive():
            self._layout_sync.start()

    def sync_layout(self) -> None:
        """Пересчитать минимумы скролл-хостов (переопределяют подклассы)."""

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

        self._progress = ShimmerProgress(self)
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
    """Советник: скан → план → подтверждение пользователя.

    Сверху живёт hero-карточка: состояние машины (когда сканировали,
    сколько мусора, сколько освобождено за всё время) и плитки быстрых
    действий - главный экран должен отвечать на вопрос «что у меня»
    до всяких кнопок.
    """

    scanRequested = Signal()
    applyRequested = Signal()
    navigateRequested = Signal(int)    # индекс страницы для плиток hero

    _HERO_TILES = (("hero.tile_cleaner", 1), ("hero.tile_tweaks", 3),
                   ("hero.tile_settings", 4))

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._plan_area: Optional[QLabel] = None
        self._hero: Optional[HeroCard] = None
        self._hero_tiles: List[Tuple[object, str]] = []
        self._scroll: Optional[QScrollArea] = None
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        # Hero + план живут в скролле: на пиксельном шрифте и низком окне
        # контент выше вьюпорта, и без скролла layout наезжал сам на себя.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Hero целиком плюс верх плана: меньше - карточка режется по живому.
        self._scroll.setMinimumHeight(240)
        host = QWidget(self._scroll)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(8)

        self._hero = HeroCard(host)
        self._hero.set_title(ctx().tr("hero.title"))
        self._hero.set_captions(ctx().tr("hero.last_scan"),
                                ctx().tr("hero.junk"),
                                ctx().tr("hero.freed"),
                                ctx().tr("hero.score"))
        self._hero.set_stats("-", "-", "-", "-")
        for key, page_index in self._HERO_TILES:
            tile = self._hero.add_tile(ctx().tr(key))
            tile.clicked.connect(
                lambda _checked=False, idx=page_index:
                self.navigateRequested.emit(idx))
            self._hero_tiles.append((tile, key))
        host_layout.addWidget(self._hero)

        frame, layout = self._make_card()
        self._add_section_label("advisor.plan_title", layout)
        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._plan_area = body(self._plan_text(), self)
        self._plan_area.setProperty("role", "secondary")
        layout.addWidget(self._status_label)
        layout.addWidget(self._plan_area)
        host_layout.addWidget(frame)
        host_layout.addStretch(1)
        self._scroll.setWidget(host)
        self._result_frame = self._scroll
        self._layout.addWidget(self._scroll, 1)

    def _add_stats(self) -> None:
        # Ряд показателей живёт внутри скролла следом за героєм: снаружи
        # при низком окне layout прижимал его к скроллу сверху и плитки
        # наезжали на карточку плана.
        super()._add_stats()
        if self._scroll is None or self._stats is None:
            return
        host = self._scroll.widget()
        if host is None or host.layout() is None:
            return
        self._stats.setParent(host)
        host.layout().insertWidget(1, self._stats)

    def hero(self) -> Optional[HeroCard]:
        return self._hero

    def set_hero_stats(self, last: str, junk: str, freed: str,
                       score: str = "0") -> None:
        if self._hero is not None:
            self._hero.set_stats(last, junk, freed, score)

    def retranslate(self) -> None:
        super().retranslate()
        if self._hero is not None:
            self._hero.set_title(ctx().tr("hero.title"))
            self._hero.set_captions(ctx().tr("hero.last_scan"),
                                    ctx().tr("hero.junk"),
                                    ctx().tr("hero.freed"),
                                    ctx().tr("hero.score"))
        for tile, key in self._hero_tiles:
            tile.setText(ctx().tr(key))

    def _add_buttons(self) -> None:
        scan = self._add_button("advisor.scan_button", primary=True)
        scan.clicked.connect(self.scanRequested.emit)
        apply_button = self._add_button("advisor.apply_button", primary=True)
        apply_button.clicked.connect(self.applyRequested.emit)
        self._add_row(scan, apply_button)
        # Растяжку не добавляем: свободное место забирает скролл с героєм
        # и планом, иначе кнопки висят в середине пустой страницы.

    def scroll_to_top(self) -> None:
        """Вернуть скролл к герою: после скана цифры важнее хвоста списка."""
        if self._scroll is not None:
            self._scroll.verticalScrollBar().setValue(0)

    def _sync_host_min(self) -> None:
        if self._scroll is None:
            return
        host = self._scroll.widget()
        if host is None or host.layout() is None:
            return
        fix_wrap_labels(host)
        host.setMinimumHeight(host.layout().minimumSize().height())

    def sync_layout(self) -> None:
        self._sync_host_min()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_layout_sync()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_layout_sync()

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

        # Полоса риска по левому краю рисует сама карточка — свойство risk
        # подхватывает QSS (border-left по цвету on/warn/danger).
        risk = self._risk if self._risk in ("low", "medium", "high") else "low"
        self.setProperty("risk", risk)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._check = QCheckBox(self)
        self._check.setChecked(True)
        self._check.toggled.connect(self._on_check)
        top.addWidget(self._check, 1)
        # Название и объём — на одной строке: при беглом скролле читается пара
        # «что это / сколько весит», а бейджи уходят в нижнюю строку.
        self._files_label = QLabel(self)
        self._files_label.setProperty("role", "secondary")
        top.addWidget(self._files_label, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._lane_label = QLabel(self)
        self._lane_label.setObjectName("laneBadge")
        self._lane_label.setProperty("lane", self._lane)
        apply_role_font(self._lane_label)
        bottom.addWidget(self._lane_label)
        self._risk_label = QLabel(self)
        self._risk_label.setObjectName("riskBadge")
        self._risk_label.setProperty("risk", risk)
        apply_role_font(self._risk_label)
        bottom.addWidget(self._risk_label)
        self._note_label = QLabel(self)
        self._note_label.setProperty("role", "hint")
        apply_role_font(self._note_label)
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
                widget.hide()
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
        self._preview_row: Optional[QHBoxLayout] = None
        self._preview_title: Optional[QLabel] = None
        self._last_summary: Dict[str, Tuple[str, int]] = {}
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("cleaner.candidates_title", layout)

        # Всё ниже заголовка живёт в одном скролле: на низком окне
        # карточка раньше сжималась и строки наезжали друг на друга.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(120)
        host = QWidget(self._scroll)
        self._cards_layout = QVBoxLayout(host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)

        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        self._cards_layout.addWidget(self._status_label)

        # Превью прошлого скана чипами: пока карточек нет, видно, где
        # обычно лежит мусор и сколько его было в байтах.
        self._preview_title = hint(ctx().tr("cleaner.preview_title"), self)
        self._cards_layout.addWidget(self._preview_title)
        self._preview_row = QHBoxLayout()
        self._preview_row.setSpacing(8)
        self._preview_row.addStretch(1)
        self._cards_layout.addLayout(self._preview_row)

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
        self._cards_layout.addLayout(select_row)

        self._empty_label = body(self._candidates_text(), self)
        self._empty_label.setProperty("role", "secondary")
        self._cards_layout.addWidget(self._empty_label)

        # Журнал после удаления живёт в том же скролле: отчёт заменяет
        # карточки категорий до следующего скана.
        self._journal = JournalPanel(self)
        self._cards_layout.addWidget(self._journal, 1)

        self._cards_layout.addStretch(1)
        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)

        self._layout.addWidget(frame)
        self._refresh_selection_view()
        self.schedule_layout_sync()

    def _add_stats(self) -> None:
        # Ряд показателей уезжает в скролл вместе с остальным контентом:
        # на низком окне каждые 80px высоты идут карточкам кандидатов,
        # а не стоят снаружи мертвым грузом.
        super()._add_stats()
        if self._scroll is None or self._stats is None:
            return
        host = self._scroll.widget()
        if host is not None and host.layout() is not None:
            self._stats.setParent(host)
            host.layout().insertWidget(0, self._stats)

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
        for card_widget in self._cards:
            card_widget.setVisible(True)
        # Новый скан сменяет журнал прошлого удаления.
        if self._journal is not None:
            self._journal.setVisible(False)
        self._refresh_selection_view()

    def set_last_summary(self, cats: Dict[str, Tuple[str, int]]) -> None:
        """Превью прошлого скана: id категории -> (заголовок, байты)."""
        self._last_summary = dict(cats)
        self._rebuild_preview()

    def _rebuild_preview(self) -> None:
        if self._preview_row is None:
            return
        while self._preview_row.count():
            item = self._preview_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for cat_id, (title, size) in sorted(
                self._last_summary.items(), key=lambda kv: -kv[1][1]):
            if size <= 0:
                continue
            self._preview_row.addWidget(
                chip(f"{title} · {human_size(size)}", self))
        self._preview_row.addStretch(1)
        self._refresh_selection_view()

    def show_journal(self, report, summary: str) -> None:
        """После удаления: карточки уступают место журналу до нового скана."""
        if self._journal is None:
            return
        self._journal.show_report(report, summary)
        for widget in (self._empty_label, self._selection_label,
                       self._preview_title, self._select_all_button,
                       self._select_none_button):
            if widget is not None:
                widget.setVisible(False)
        for card_widget in self._cards:
            card_widget.setVisible(False)
        self._sync_host_min()

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
        if not _on:
            return
        card = next((c for c in self._cards if c.category_id() == _category_id), None)
        if card is not None and (card._risk == "high" or card._lane == "direct"):
            # Выбрана опасная/необратимая категория: Клинни спохватывается.
            ctx().setAssistantMood("panic")
            QTimer.singleShot(1400, lambda: ctx().setAssistantMood("idle"))

    def _refresh_selection_view(self) -> None:
        # Пока виден журнал удаления, карточная вёрстка не трогается.
        if self._journal is not None and self._journal.isVisible():
            return
        has_cards = bool(self._cards)
        if self._empty_label is not None:
            self._empty_label.setVisible(not has_cards)
        show_preview = not has_cards and bool(self._last_summary)
        if self._preview_title is not None:
            self._preview_title.setVisible(show_preview)
        if self._preview_row is not None:
            for i in range(self._preview_row.count()):
                item = self._preview_row.itemAt(i)
                if item is not None and item.widget() is not None:
                    item.widget().setVisible(show_preview)
        # Кнопки массового выбора существуют только когда есть что
        # выбирать: до скана их вовсе нет на экране.
        for widget in (self._select_all_button, self._select_none_button):
            if widget is not None:
                widget.setVisible(has_cards)
        if self._selection_label is not None:
            selected = [c for c in self._cards if c.is_checked()]
            files = sum(c.files() for c in selected)
            size = sum(c.bytes() for c in selected)
            self._selection_label.setText(
                ctx().tr("cleaner.selected_summary").format(
                    cats=len(selected), total=len(self._cards),
                    files=files, size=human_size(size)))
            self._selection_label.setVisible(has_cards)
        self._sync_host_min()

    def _sync_host_min(self) -> None:
        """Хост скролла не ниже контента: иначе строки наезжают друг на друга."""
        if self._scroll is None:
            return
        host = self._scroll.widget()
        if host is None or host.layout() is None:
            return
        fix_wrap_labels(host)
        host.setMinimumHeight(host.layout().minimumSize().height())

    def sync_layout(self) -> None:
        self._sync_host_min()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_layout_sync()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_layout_sync()

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
        self._scroll: Optional[QScrollArea] = None
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("dedup.groups_title", layout)

        # Список групп может быть длинным: на низком окне карточка
        # сжималась и текст наезжал сам на себя - уводим в скролл.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(120)
        host = QWidget(self._scroll)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(8)

        self._status_label = body(self._status_text(), self)
        self._status_label.setProperty("role", "secondary")
        host_layout.addWidget(self._status_label)
        self._groups_area = body(self._groups_text(), self)
        self._groups_area.setProperty("role", "secondary")
        host_layout.addWidget(self._groups_area)
        self._journal = JournalPanel(self)
        host_layout.addWidget(self._journal, 1)
        host_layout.addStretch(1)

        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)
        self._layout.addWidget(frame)
        self.schedule_layout_sync()

    def _sync_host_min(self) -> None:
        if self._scroll is None:
            return
        host = self._scroll.widget()
        if host is None or host.layout() is None:
            return
        fix_wrap_labels(host)
        host.setMinimumHeight(host.layout().minimumSize().height())

    def sync_layout(self) -> None:
        self._sync_host_min()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_layout_sync()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_layout_sync()

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
        self._sync_host_min()

    def show_journal(self, report, summary: str) -> None:
        """После удаления дублей: журнал вместо списка групп до нового скана."""
        if self._journal is None:
            return
        self._journal.show_report(report, summary)
        if self._groups_area is not None:
            self._groups_area.setVisible(False)
        self._sync_host_min()

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
    """Твики: системные твики, автозагрузка, службы, UWP — со снапшотами.

    Секция «Твики системы» — декларативная база core/tweaks_db.json:
    тумблер знает своё текущее состояние (on/off/unknown), включение и
    выключение идут со снапшотом прежних значений, откат — кнопкой
    «Вернуть» в разделе «Можно вернуть».
    """

    refreshRequested = Signal()
    disableStartupRequested = Signal(object)   # StartupEntry
    restoreSnapshotRequested = Signal(str)     # имя снапшота
    disableServiceRequested = Signal(str)      # имя службы
    removeUwpRequested = Signal(str)           # PackageFullName
    applyTweakRequested = Signal(str, bool)    # id твика, включить/выключить
    applyPresetRequested = Signal(str)         # id пресета
    installAppsRequested = Signal(list)        # список winget-id
    activationRequested = Signal(str)          # windows|office|kms:<сервер>
    gpeditRequested = Signal()
    shutdownSetRequested = Signal(int)         # минуты
    shutdownCancelRequested = Signal()
    installRedistsRequested = Signal(list)     # rid зависимостей
    refreshRedistsRequested = Signal()         # пересчитать «уже стоит»
    configSaveRequested = Signal(str, str)     # имя, заметка
    configApplyRequested = Signal(str)         # имя конфига
    configExportRequested = Signal(str)        # имя конфига
    configImportRequested = Signal(str)        # путь к файлу
    configDeleteRequested = Signal(str)        # имя конфига
    configRefreshRequested = Signal()

    _MAX_SERVICES = 60
    _MAX_UWP = 60

    _TWEAK_CATEGORIES = ("explorer", "personal", "contextmenu", "telemetry",
                         "winupdate", "sysrec", "components", "security")

    def __init__(self, parent: QWidget | None = None) -> None:
        self._status_label: Optional[QLabel] = None
        self._scroll: Optional[QScrollArea] = None
        self._rows_layout: Optional[QVBoxLayout] = None
        self._startup: List[object] = []
        self._services: List[object] = []
        self._backups: List[Dict[str, object]] = []
        self._uwp: List[object] = []
        self._redist_boxes: Dict[str, QCheckBox] = {}
        self._config_apply_btn: Optional[QPushButton] = None
        self._config_delete_btn: Optional[QPushButton] = None
        self._config_export_btn: Optional[QPushButton] = None
        self._configs_meta: Dict[str, Dict[str, object]] = {}
        self._sys_tweaks: List[Dict[str, object]] = []
        self._search: Optional[QLineEdit] = None
        self._risk_filter: Optional[QComboBox] = None
        self._accordions: List[Accordion] = []
        self._config_cards: List[ConfigCard] = []
        self._config_cards_host: Optional[QVBoxLayout] = None
        self._selected_config: str = ""
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

        # Поиск по имени твика и фильтр по риску: 73 тумблера простынёй
        # не читаются, а так нужное находится за два символа.
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(ctx().tr("tweaks.search_placeholder"))
        self._search.setClearButtonEnabled(True)
        # Перерисовка строк дорогая (десятки виджетов на символ): пока
        # человек печатает запрос, держим паузу и рисуем один раз.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._render_rows)
        self._search.textChanged.connect(lambda _t: self._search_timer.start())
        search_row.addWidget(self._search, 1)
        self._risk_filter = QComboBox(self)
        self._risk_filter.setMinimumWidth(150)
        self._fill_risk_filter()
        self._risk_filter.currentIndexChanged.connect(
            lambda _i: self._render_rows())
        search_row.addWidget(self._risk_filter)
        layout.addLayout(search_row)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(150)
        host = QWidget(self._scroll)
        self._rows_layout = QVBoxLayout(host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)
        self._layout.addWidget(frame)

    def _fill_risk_filter(self) -> None:
        if self._risk_filter is None:
            return
        self._risk_filter.blockSignals(True)
        self._risk_filter.clear()
        self._risk_filter.addItem(ctx().tr("tweaks.filter_risk_all"), "all")
        for level in ("low", "medium", "high"):
            self._risk_filter.addItem(ctx().tr(f"tweaks.risk_{level}"), level)
        self._risk_filter.blockSignals(False)

    def _add_buttons(self) -> None:
        refresh = self._add_button("tweaks.refresh_button", primary=True)
        refresh.clicked.connect(self.refreshRequested.emit)
        self._add_row(refresh)

    # ---------- данные ----------

    def set_tweaks(self, startup: List[object], services: List[object],
                   backups: List[Dict[str, object]],
                   uwp: Optional[List[object]] = None,
                   sys_tweaks: Optional[List[Dict[str, object]]] = None
                   ) -> None:
        """Показать списки: системные твики, автозагрузка, службы, UWP."""
        self._startup = list(startup)
        self._services = list(services)
        self._backups = list(backups)
        self._uwp = list(uwp or [])
        self._sys_tweaks = list(sys_tweaks or [])
        self._render_rows()
        self.setStats("startup", len(self._startup))
        self.setStats("services", len(self._services))
        self.setStats("backups", len(self._backups))
        self.setStatus(ctx().tr("tweaks.loaded_status").format(
            startup=len(self._startup), services=len(self._services),
            backups=len(self._backups)))

    def _filter_ok(self, tw: Dict[str, object]) -> bool:
        """Проходит ли твик текущий поиск и фильтр риска."""
        needle = (self._search.text().strip().lower()
                  if self._search is not None else "")
        level = "all"
        if self._risk_filter is not None:
            level = str(self._risk_filter.currentData() or "all")
        if level != "all" and str(tw.get("risk", "low")) != level:
            return False
        if not needle:
            return True
        hay = " ".join(str(tw.get(key, ""))
                       for key in ("id", "name_ru", "name_en")).lower()
        return needle in hay

    def _accordion(self, title: str, count: int,
                   open: bool = False) -> Accordion:  # noqa: A002
        acc = Accordion(title, self, open=open)
        acc.set_count(count)
        acc.toggled.connect(lambda _open=False: self._sync_rows_host_min())
        self._accordions.append(acc)
        return acc

    def _sync_rows_host_min(self) -> None:
        """Хост скролла не ниже минимума контента: иначе сжатие в кашу."""
        if self._scroll is None:
            return
        host = self._scroll.widget()
        if host is None or host.layout() is None:
            return
        host.setMinimumHeight(host.layout().minimumSize().height())

    def _add_stats(self) -> None:
        super()._add_stats()
        # Ряд показателей живёт внутри скролла первым виджетом: снаружи
        # он съедал высоту вьюпорта, и аккордеоны оставались с полоску.
        if self._stats is not None and self._scroll is not None:
            host = self._scroll.widget()
            if host is not None:
                self._stats.setParent(host)
                host.layout().insertWidget(0, self._stats)

    def _render_rows(self) -> None:
        assert self._rows_layout is not None
        # Индекс 0 - постоянный ряд показателей: чистим всё после него,
        # растяжка остаётся последней.
        while self._rows_layout.count() > 2:
            item = self._rows_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                # hide сразу: deleteLater дожжётся DeferredDelete позже,
                # а снятый с раскладки виджет успевает мигнуть сиротой.
                widget.hide()
                widget.deleteLater()
        self._accordions = []
        self._config_cards = []
        self._redist_boxes = {}

        def put(widget: QWidget) -> None:
            # Строчки идут до растяжки: стрейч всегда последний.
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, widget)

        if self._sys_tweaks:
            put(hint(ctx().tr("tweaks.sys_hint"), self))
            put(self._presets_row())
            visible = [tw for tw in self._sys_tweaks if self._filter_ok(tw)]
            for cat in self._TWEAK_CATEGORIES:
                group = [tw for tw in visible if tw.get("category") == cat]
                if not group:
                    continue
                acc = self._accordion(ctx().tr(f"tweaks.cat_{cat}"),
                                      len(group), open=cat == "explorer")
                for tw in group:
                    acc.body_layout().addWidget(self._tweak_row(tw))
                put(acc)
            if visible and not any(
                    tw.get("category") in self._TWEAK_CATEGORIES
                    for tw in visible):
                put(hint(ctx().tr("tweaks.search_empty"), self))

            tools = self._accordion(ctx().tr("tweaks.acc_tools"), 4)
            tools.body_layout().addWidget(self._apps_section())
            tools.body_layout().addWidget(self._redists_section())
            tools.body_layout().addWidget(self._activation_section())
            tools.body_layout().addWidget(self._timer_section())
            put(tools)

            configs = self._accordion(ctx().tr("tweaks.configs_section"),
                                      len(self._configs_meta), open=True)
            configs.body_layout().addWidget(self._configs_section())
            put(configs)

        startup = self._accordion(ctx().tr("tweaks.startup_section"),
                                  len(self._startup))
        if not self._startup:
            startup.body_layout().addWidget(
                hint(ctx().tr("tweaks.empty_startup"), self))
        for entry in self._startup:
            startup.body_layout().addWidget(self._startup_row(entry))
        put(startup)

        backups = self._accordion(ctx().tr("tweaks.backups_section"),
                                  len(self._backups))
        if not self._backups:
            backups.body_layout().addWidget(
                hint(ctx().tr("tweaks.empty_backups"), self))
        for info in self._backups:
            backups.body_layout().addWidget(self._backup_row(info))
        put(backups)

        services = self._accordion(ctx().tr("tweaks.services_section"),
                                   len(self._services))
        services.body_layout().addWidget(
            hint(ctx().tr("tweaks.services_hint"), self))
        for service in self._services[:self._MAX_SERVICES]:
            services.body_layout().addWidget(self._service_row(service))
        if len(self._services) > self._MAX_SERVICES:
            services.body_layout().addWidget(hint(
                ctx().tr("journal.more").format(
                    count=len(self._services) - self._MAX_SERVICES), self))
        put(services)

        uwp = self._accordion(ctx().tr("tweaks.uwp_section"), len(self._uwp))
        uwp.body_layout().addWidget(hint(ctx().tr("tweaks.uwp_hint"), self))
        if not self._uwp:
            uwp.body_layout().addWidget(
                hint(ctx().tr("tweaks.empty_uwp"), self))
        for pkg in self._uwp[:self._MAX_UWP]:
            uwp.body_layout().addWidget(self._uwp_row(pkg))
        if len(self._uwp) > self._MAX_UWP:
            uwp.body_layout().addWidget(hint(ctx().tr("journal.more").format(
                count=len(self._uwp) - self._MAX_UWP), self))
        put(uwp)
        self._sync_config_buttons()
        self.schedule_layout_sync()

    def _fix_wrap_heights(self) -> None:
        """Переносимые подписи внутри скролла: высота после известной ширины.

        QLabel с wordWrap отдаёт высоту через heightForWidth, а layout
        внутри скролла спрашивает её до раскладки по ширине - подпись
        выползает на соседний виджет. Пересчитываем после раскладки.
        """
        if self._scroll is None:
            return
        host = self._scroll.widget()
        if host is None:
            return
        fix_wrap_labels(host)
        self._sync_rows_host_min()

    def sync_layout(self) -> None:
        self._fix_wrap_heights()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_layout_sync()

    def showEvent(self, event) -> None:  # noqa: N802
        # На скрытой странице подписи меряются по узкой ширине: после
        # показа пересчитываем высоты по настоящей.
        super().showEvent(event)
        self.schedule_layout_sync()

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

    def _tweak_row(self, tw: Dict[str, object]) -> QFrame:
        """Строка системного твика: имя, риск, статус, кнопка действия."""
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        is_ru = str(ctx().locale()).startswith("ru")
        name_text = str(tw.get("name_ru") if is_ru else tw.get("name_en"))
        name = body(name_text, row)
        name.setSizePolicy(QSizePolicy.Policy.Ignored,
                           QSizePolicy.Policy.Preferred)
        name.setWordWrap(True)
        layout.addWidget(name, 3)
        risk = str(tw.get("risk", "low"))
        badge = risk_badge(risk, row)
        badge.setText(ctx().tr(f"tweaks.risk_{risk}"))
        layout.addWidget(badge)
        flags: List[str] = []
        if tw.get("reboot"):
            flags.append(ctx().tr("tweaks.flag_reboot"))
        elif tw.get("explorer_restart"):
            flags.append(ctx().tr("tweaks.flag_explorer"))
        if flags:
            layout.addWidget(hint(" · ".join(flags), row))
        status = str(tw.get("status", "unknown"))
        layout.addWidget(hint(ctx().tr(f"tweaks.state_{status}"), row))
        tw_id = str(tw.get("id", ""))
        if tw.get("one_way"):
            act = button(ctx().tr("tweaks.apply_button"), row)
            act.clicked.connect(
                lambda _c=False, i=tw_id: self.applyTweakRequested.emit(i, True))
        elif status == "on":
            act = button(ctx().tr("tweaks.turn_off"), row)
            act.clicked.connect(
                lambda _c=False, i=tw_id: self.applyTweakRequested.emit(i, False))
        else:
            act = button(ctx().tr("tweaks.turn_on"), row)
            act.clicked.connect(
                lambda _c=False, i=tw_id: self.applyTweakRequested.emit(i, True))
        act.setMinimumHeight(30)
        layout.addWidget(act)
        return row

    def _presets_row(self) -> QFrame:
        """Пакеты твиков одной кнопкой: карточка с кнопками пресетов."""
        from core.tweaks import load_presets
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(hint(ctx().tr("tweaks.presets_hint"), row))
        is_ru = str(ctx().locale()).startswith("ru")
        try:
            presets = load_presets()
        except Exception:  # noqa: BLE001 — пресеты не обязаны ломать страницу
            presets = []
        layout.addWidget(self._recs_hint(presets, row))
        for preset in presets:
            line = QHBoxLayout()
            name = preset.name_ru if is_ru else preset.name_en
            btn = button(name, row)
            btn.setMinimumHeight(30)
            btn.setToolTip(preset.desc_ru if is_ru else preset.desc_en)
            btn.clicked.connect(
                lambda _c=False, pid=preset.id:
                    self.applyPresetRequested.emit(pid))
            line.addWidget(btn)
            line.addWidget(
                hint(ctx().tr("tweaks.preset_count").format(
                    count=len(preset.tweaks)), row))
            line.addStretch(1)
            layout.addLayout(line)
        return row

    def _recs_hint(self, presets: List[object], parent: QWidget) -> QLabel:
        """Правила-фундамент: какие пакеты подходят под железо (без модели)."""
        from ai.rules import recommend_tweaks, system_facts
        is_ru = str(ctx().locale()).startswith("ru")
        names = {p.id: (p.name_ru if is_ru else p.name_en) for p in presets}
        try:
            recs = recommend_tweaks(system_facts(), set(names))
        except Exception:  # noqa: BLE001 — правила не обязаны ломать страницу
            recs = []
        if not recs:
            label = hint("", parent)
            label.setVisible(False)
            return label
        reasons = "; ".join(
            f"{names.get(pid, pid)} - {ctx().tr(why)}" for pid, why in recs)
        return hint(ctx().tr("tweaks.recs_hint").format(names=reasons), parent)

    def _apps_section(self) -> QFrame:
        """Установка приложений через winget: чекбоксы каталога + кнопка."""
        from PySide6.QtWidgets import QCheckBox
        from core.appinstall import CATALOG
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(section(ctx().tr("tweaks.apps_section"), row))
        layout.addWidget(hint(ctx().tr("tweaks.apps_hint"), row))
        self._app_boxes: List[Tuple[str, object]] = []
        grid = QHBoxLayout()
        grid.setSpacing(10)
        columns: List[QVBoxLayout] = []
        for _ in range(3):
            col = QVBoxLayout()
            col.setSpacing(2)
            columns.append(col)
            grid.addLayout(col, 1)
        for i, entry in enumerate(CATALOG):
            box = QCheckBox(entry.name, row)
            columns[i % 3].addWidget(box)
            self._app_boxes.append((entry.winget_id, box))
        layout.addLayout(grid)
        install = button(ctx().tr("tweaks.apps_install_button"), row)
        install.setMinimumHeight(30)
        install.clicked.connect(self._emit_apps)
        layout.addWidget(install)
        return row

    def _emit_apps(self) -> None:
        ids = [wid for wid, box in self._app_boxes if box.isChecked()]
        self.installAppsRequested.emit(ids)

    def _redists_section(self) -> QFrame:
        """Этап 6: зависимости и рантаймы - всем, геймеру, прогеру."""
        from core.redists import GROUPS, REDISTS
        frame = QFrame(self)
        frame.setObjectName("categoryCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(section(ctx().tr("tweaks.redists_section"), frame))
        layout.addWidget(hint(ctx().tr("tweaks.redists_hint"), frame))

        groups_row = QHBoxLayout()
        for group in GROUPS:
            btn = QPushButton(ctx().tr(f"tweaks.redists_group_{group}"), frame)
            btn.setMinimumHeight(30)
            btn.clicked.connect(
                lambda _checked=False, g=group: self._pick_redist_group(g))
            groups_row.addWidget(btn)
        groups_row.addStretch(1)
        layout.addLayout(groups_row)

        grid = QGridLayout()
        grid.setSpacing(6)
        for index, entry in enumerate(REDISTS):
            label = entry.name_ru if str(ctx().locale()).startswith("ru") \
                else entry.name_en
            box = QCheckBox(label, frame)
            self._redist_boxes[entry.rid] = box
            grid.addWidget(box, index // 3, index % 3)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        refresh = QPushButton(ctx().tr("tweaks.redists_refresh"), frame)
        refresh.setMinimumHeight(30)
        refresh.clicked.connect(lambda: self.refreshRedistsRequested.emit())
        buttons.addWidget(refresh)
        install = QPushButton(ctx().tr("tweaks.redists_install"), frame)
        install.setMinimumHeight(30)
        install.clicked.connect(self._emit_redists)
        buttons.addWidget(install)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return frame

    def _pick_redist_group(self, group: str) -> None:
        from core.redists import group_ids
        wanted = set(group_ids(group))
        for rid, box in self._redist_boxes.items():
            box.setChecked(rid in wanted)

    def _emit_redists(self) -> None:
        rids = [rid for rid, box in self._redist_boxes.items()
                if box.isChecked()]
        if rids:
            self.installRedistsRequested.emit(rids)

    def setRedistStatus(self, status: Dict[str, object]) -> None:
        """Детект «уже стоит»: приписка к подписи чекбокса."""
        from core.redists import REDISTS
        for entry in REDISTS:
            box = self._redist_boxes.get(entry.rid)
            if box is None:
                continue
            base = entry.name_ru if str(ctx().locale()).startswith("ru") \
                else entry.name_en
            state = status.get(entry.rid)
            if state is True:
                box.setText(f"{base} - {ctx().tr('tweaks.redists_installed')}")
            elif state is False:
                box.setText(f"{base} - {ctx().tr('tweaks.redists_missing')}")
            else:
                box.setText(base)

    def _configs_section(self) -> QFrame:
        """Этап 7: конфиги системы - снимок, файл, применение с защитой.

        Список живёт карточками: имя, состав и дата на виду, выбор -
        кликом по карточке, применение - только явной кнопкой.
        """
        import time as _time
        frame = QFrame(self)
        frame.setObjectName("categoryCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(hint(ctx().tr("tweaks.configs_hint"), frame))

        self._config_cards_host = QVBoxLayout()
        self._config_cards_host.setSpacing(8)
        layout.addLayout(self._config_cards_host)

        row1 = QHBoxLayout()
        save = QPushButton(ctx().tr("tweaks.configs_save"), frame)
        save.setMinimumHeight(30)
        save.clicked.connect(self._ask_config_save)
        row1.addWidget(save)
        refresh = QPushButton(ctx().tr("tweaks.configs_refresh"), frame)
        refresh.setMinimumHeight(30)
        refresh.clicked.connect(lambda: self.configRefreshRequested.emit())
        row1.addWidget(refresh)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._config_apply_btn = QPushButton(
            ctx().tr("tweaks.configs_apply"), frame)
        self._config_apply_btn.setMinimumHeight(30)
        self._config_apply_btn.clicked.connect(self._emit_config_apply)
        row2.addWidget(self._config_apply_btn)
        self._config_export_btn = QPushButton(
            ctx().tr("tweaks.configs_export"), frame)
        self._config_export_btn.setMinimumHeight(30)
        self._config_export_btn.clicked.connect(self._emit_config_export)
        row2.addWidget(self._config_export_btn)
        import_btn = QPushButton(ctx().tr("tweaks.configs_import"), frame)
        import_btn.setMinimumHeight(30)
        import_btn.clicked.connect(self._ask_config_import)
        row2.addWidget(import_btn)
        self._config_delete_btn = QPushButton(
            ctx().tr("tweaks.configs_delete"), frame)
        self._config_delete_btn.setMinimumHeight(30)
        self._config_delete_btn.clicked.connect(self._emit_config_delete)
        row2.addWidget(self._config_delete_btn)
        row2.addStretch(1)
        layout.addLayout(row2)
        self._rebuild_config_cards()
        self._sync_config_buttons()
        return frame

    def _rebuild_config_cards(self) -> None:
        import time as _time
        if self._config_cards_host is None:
            return
        while self._config_cards_host.count():
            item = self._config_cards_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._config_cards = []
        if not self._configs_meta:
            self._config_cards_host.addWidget(
                hint(ctx().tr("tweaks.configs_empty"), self))
            return
        names = sorted(self._configs_meta)
        if self._selected_config not in names:
            self._selected_config = names[0] if names else ""
        for name in names:
            entry = self._configs_meta[name]
            created = float(entry.get("created", 0.0) or 0.0)
            when = _time.strftime("%d.%m.%Y", _time.localtime(created)) \
                if created else ""
            meta = ctx().tr("tweaks.configs_meta").format(
                tweaks=entry.get("tweaks", 0), apps=entry.get("apps", 0),
                redists=entry.get("redists", 0))
            if when:
                meta = f"{meta} · {when}"
            card_widget = ConfigCard(name, meta, self)
            card_widget.set_selected(name == self._selected_config)
            card_widget.clicked.connect(self._select_config)
            self._config_cards_host.addWidget(card_widget)
            self._config_cards.append(card_widget)

    def _select_config(self, name: str) -> None:
        self._selected_config = name
        for card_widget in self._config_cards:
            card_widget.set_selected(card_widget.name() == name)
        self._sync_config_buttons()

    def _sync_config_buttons(self, *_args) -> None:
        has = bool(self._selected_config) and \
            self._selected_config in self._configs_meta
        for btn in (self._config_apply_btn, self._config_export_btn,
                    self._config_delete_btn):
            if btn is not None:
                btn.setEnabled(bool(has))

    def current_config_name(self) -> str:
        return self._selected_config

    def setConfigs(self, entries: List[Dict[str, object]]) -> None:
        """Список конфигов профиля карточками; мета - для подтверждения."""
        self._configs_meta = {str(e["name"]): e for e in entries}
        names = [str(e["name"]) for e in entries]
        if self._selected_config not in names:
            self._selected_config = names[0] if names else ""
        self._rebuild_config_cards()
        self._sync_config_buttons()

    def _ask_config_save(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, ctx().tr("tweaks.configs_save_title"),
            ctx().tr("tweaks.configs_save_label"))
        name = name.strip()
        if ok and name:
            self.configSaveRequested.emit(name, "")

    def _ask_config_import(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ok = QFileDialog.getOpenFileName(
            self, ctx().tr("tweaks.configs_import"), "",
            "SWAGcleaner config (*.json);;All files (*)")
        if path:
            self.configImportRequested.emit(path)

    def _emit_config_apply(self) -> None:
        name = self.current_config_name()
        if name:
            self.configApplyRequested.emit(name)

    def _emit_config_export(self) -> None:
        name = self.current_config_name()
        if name:
            self.configExportRequested.emit(name)

    def _emit_config_delete(self) -> None:
        name = self.current_config_name()
        if name:
            self.configDeleteRequested.emit(name)

    def _activation_section(self) -> QFrame:
        """Активация Windows (HWID), Office (Ohook) и ручной KMS."""
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        layout.addWidget(section(ctx().tr("tweaks.act_section"), row))
        layout.addWidget(hint(ctx().tr("tweaks.act_hint"), row))
        line = QHBoxLayout()
        for key, what in (("tweaks.act_windows", "windows"),
                          ("tweaks.act_office", "office"),
                          ("tweaks.act_kms", "kms:kms.digiboy.ir")):
            btn = button(ctx().tr(key), row)
            btn.setMinimumHeight(30)
            btn.clicked.connect(
                lambda _c=False, w=what: self.activationRequested.emit(w))
            line.addWidget(btn)
        gpedit_btn = button(ctx().tr("tweaks.gpedit_button"), row)
        gpedit_btn.setMinimumHeight(30)
        gpedit_btn.setToolTip(ctx().tr("tweaks.gpedit_hint"))
        gpedit_btn.clicked.connect(self.gpeditRequested.emit)
        line.addWidget(gpedit_btn)
        line.addStretch(1)
        layout.addLayout(line)
        return row

    def _timer_section(self) -> QFrame:
        """Таймер выключения: минуты + поставить/отменить."""
        from PySide6.QtWidgets import QSpinBox
        row = QFrame(self)
        row.setObjectName("categoryCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        layout.addWidget(section(ctx().tr("tweaks.timer_section"), row))
        spin = QSpinBox(row)
        spin.setRange(1, 1440)
        spin.setValue(60)
        spin.setSuffix(" " + ctx().tr("tweaks.timer_minutes"))
        spin.setMinimumHeight(30)
        self._shutdown_spin = spin
        layout.addWidget(spin)
        set_btn = button(ctx().tr("tweaks.timer_set"), row)
        set_btn.setMinimumHeight(30)
        set_btn.clicked.connect(
            lambda _c=False: self.shutdownSetRequested.emit(spin.value()))
        layout.addWidget(set_btn)
        cancel_btn = button(ctx().tr("tweaks.timer_cancel"), row)
        cancel_btn.setMinimumHeight(30)
        cancel_btn.clicked.connect(self.shutdownCancelRequested.emit)
        layout.addWidget(cancel_btn)
        layout.addStretch(1)
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
        if self._search is not None:
            self._search.setPlaceholderText(
                ctx().tr("tweaks.search_placeholder"))
        self._fill_risk_filter()
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
    accentChanged = Signal(str)
    motionChanged = Signal(str)
    soundsChanged = Signal(bool)
    aiSaveRequested = Signal(object)     # AiSettings из виджетов
    aiTestRequested = Signal(object)     # проверить связь
    aiModelsRequested = Signal(object)   # подтянуть каталог моделей

    def __init__(self, parent: QWidget | None = None) -> None:
        self._lang_combo: Optional[QComboBox] = None
        self._theme_combo: Optional[QComboBox] = None
        self._accent_combo: Optional[QComboBox] = None
        self._motion_combo: Optional[QComboBox] = None
        self._font_combo: Optional[QComboBox] = None
        self._sounds_check: Optional[QCheckBox] = None
        self._accordions: List[Accordion] = []
        self._ai_enabled: Optional[QCheckBox] = None
        self._ai_provider: Optional[QComboBox] = None
        self._ai_url: Optional[QLineEdit] = None
        self._ai_model: Optional[QComboBox] = None
        self._ai_key: Optional[QLineEdit] = None
        self._ai_timeout: Optional[QSpinBox] = None
        self._ai_status: Optional[QLabel] = None
        self._ai_remote_warn: Optional[QLabel] = None
        self._ai_key_label: Optional[QLabel] = None
        self._settings_scroll: Optional[QScrollArea] = None
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame

        # Аккордеоны в скролле: раскрытые секции выше вьюпорта на низких
        # окнах, без скролла layout наезжал секциями друг на друга.
        scroll = QScrollArea(frame)
        scroll.setObjectName("categoryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(scroll)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(8)
        self._settings_scroll = scroll

        appearance = Accordion(ctx().tr("settings.acc_appearance"), self,
                               open=True)
        app_layout = appearance.body_layout()
        self._accordions = [appearance]

        self._add_section_label("settings.language_label", app_layout)
        self._lang_combo = QComboBox(self)
        self._lang_combo.currentIndexChanged.connect(self._on_language_selected)
        app_layout.addWidget(self._lang_combo)

        self._add_section_label("settings.theme_label", app_layout)
        self._theme_combo = QComboBox(self)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_selected)
        app_layout.addWidget(self._theme_combo)

        self._add_section_label("settings.accent_label", app_layout)
        self._accent_combo = QComboBox(self)
        self._accent_combo.currentIndexChanged.connect(self._on_accent_selected)
        app_layout.addWidget(self._accent_combo)

        self._add_section_label("settings.motion_label", app_layout)
        self._motion_combo = QComboBox(self)
        self._motion_combo.currentIndexChanged.connect(self._on_motion_selected)
        app_layout.addWidget(self._motion_combo)
        app_layout.addWidget(hint(ctx().tr("settings.motion_note"), self))

        self._add_section_label("settings.font_label", app_layout)
        self._font_combo = QComboBox(self)
        self._font_combo.currentIndexChanged.connect(self._on_font_selected)
        app_layout.addWidget(self._font_combo)
        app_layout.addWidget(hint(ctx().tr("settings.font_note"), self))

        self._add_section_label("settings.sounds_label", app_layout)
        self._sounds_check = QCheckBox(ctx().tr("settings.sounds_label"), self)
        self._sounds_check.setChecked(ctx().soundsEnabled())
        self._sounds_check.toggled.connect(self._on_sounds_toggled)
        app_layout.addWidget(self._sounds_check)
        app_layout.addWidget(hint(ctx().tr("settings.sounds_note"), self))
        host_layout.addWidget(appearance)
        appearance.toggled.connect(lambda _open=False: self._sync_host_min())

        ai_acc = Accordion(ctx().tr("settings.acc_ai"), self, open=True)
        self._accordions.append(ai_acc)
        self._add_ai_section(ai_acc.body_layout())
        host_layout.addWidget(ai_acc)
        ai_acc.toggled.connect(lambda _open=False: self._sync_host_min())
        host_layout.addStretch(1)

        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self._layout.addWidget(frame)
        self._fill_combos()
        self.schedule_layout_sync()

    def _sync_host_min(self) -> None:
        """Хост скролла держит высоту контента: иначе виджет сжимается.

        QScrollArea с widgetResizable тянет виджет к размеру вьюпорта и
        смотрит только на minimumSize - выставляем его от минимума
        внутреннего layout, тогда вместо сжатия появляется полоса прокрутки.
        """
        if self._settings_scroll is None:
            return
        host = self._settings_scroll.widget()
        if host is None or host.layout() is None:
            return
        fix_wrap_labels(host)
        host.setMinimumHeight(host.layout().minimumSize().height())

    def sync_layout(self) -> None:
        self._sync_host_min()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.schedule_layout_sync()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.schedule_layout_sync()

    def _add_ai_section(self, layout: QVBoxLayout) -> None:
        """AI: провайдер, адрес, модель (каталог с сервера), ключ, таймаут."""
        self._add_section_label("settings.ai_section", layout)
        layout.addWidget(hint(ctx().tr("settings.ai_hint"), self))

        self._ai_enabled = QCheckBox(ctx().tr("settings.ai_enabled"), self)
        layout.addWidget(self._ai_enabled)

        self._add_section_label("settings.ai_provider_label", layout)
        self._ai_provider = QComboBox(self)
        self._ai_provider.addItem(ctx().tr("settings.ai_provider_ollama"),
                                  "ollama")
        self._ai_provider.addItem(ctx().tr("settings.ai_provider_openai"),
                                  "openai")
        self._ai_provider.addItem(ctx().tr("settings.ai_provider_anthropic"),
                                  "anthropic")
        self._ai_provider.currentIndexChanged.connect(self._on_ai_changed)
        layout.addWidget(self._ai_provider)

        self._add_section_label("settings.ai_url_label", layout)
        self._ai_url = QLineEdit(self)
        self._ai_url.setPlaceholderText("http://localhost:11434")
        self._ai_url.textEdited.connect(self._on_ai_changed)
        layout.addWidget(self._ai_url)

        self._add_section_label("settings.ai_model_label", layout)
        model_row = QHBoxLayout()
        self._ai_model = QComboBox(self)
        self._ai_model.setEditable(True)
        model_row.addWidget(self._ai_model, 1)
        self._ai_models_btn = QPushButton(ctx().tr("settings.ai_models_refresh"), self)
        self._ai_models_btn.setMinimumHeight(30)
        self._ai_models_btn.clicked.connect(self._emit_ai_models)
        model_row.addWidget(self._ai_models_btn)
        layout.addLayout(model_row)

        self._ai_key_label = self._add_section_label("settings.ai_key_label",
                                                     layout)
        self._ai_key = QLineEdit(self)
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setPlaceholderText("sk-...")
        self._ai_key.textEdited.connect(self._on_ai_changed)
        layout.addWidget(self._ai_key)

        self._add_section_label("settings.ai_timeout_label", layout)
        self._ai_timeout = QSpinBox(self)
        self._ai_timeout.setRange(5, 600)
        self._ai_timeout.setValue(60)
        layout.addWidget(self._ai_timeout)

        self._ai_remote_warn = hint(ctx().tr("settings.ai_remote_warn"), self)
        self._ai_remote_warn.setVisible(False)
        layout.addWidget(self._ai_remote_warn)

        buttons = QHBoxLayout()
        self._ai_test_btn = QPushButton(ctx().tr("settings.ai_test"), self)
        self._ai_test_btn.setMinimumHeight(30)
        self._ai_test_btn.clicked.connect(self._emit_ai_test)
        buttons.addWidget(self._ai_test_btn)
        save = QPushButton(ctx().tr("settings.ai_save"), self)
        save.setMinimumHeight(30)
        save.clicked.connect(self._emit_ai_save)
        buttons.addWidget(save)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._ai_status = hint("", self)
        layout.addWidget(self._ai_status)

    # ---------- AI: виджеты -> настройки и обратно ----------

    def _on_ai_changed(self, *_args) -> None:
        """Провайдер/адрес поменялись: видимость ключа и предупреждения."""
        if self._ai_key is not None and self._ai_provider is not None:
            needs_key = self._ai_provider.currentData() in ("openai", "anthropic")
            self._ai_key.setVisible(bool(needs_key))
            if self._ai_key_label is not None:
                self._ai_key_label.setVisible(bool(needs_key))
        if self._ai_remote_warn is not None:
            settings = self.collect_ai_settings()
            self._ai_remote_warn.setVisible(bool(settings.is_remote()))

    def collect_ai_settings(self) -> t.Any:
        """Собрать AiSettings из виджетов (импорт тут, чтобы не тащить ai в шапку)."""
        from ai.provider import AiSettings
        model = ""
        if self._ai_model is not None:
            model = self._ai_model.currentText().strip()
        return AiSettings(
            provider=str(self._ai_provider.currentData() or "ollama")
            if self._ai_provider is not None else "ollama",
            base_url=(self._ai_url.text().strip().rstrip("/")
                      if self._ai_url is not None else "") or "http://localhost:11434",
            model=model or "llama3.1",
            api_key=self._ai_key.text().strip() if self._ai_key is not None else "",
            timeout_sec=self._ai_timeout.value() if self._ai_timeout is not None else 60,
            enabled=bool(self._ai_enabled.isChecked())
            if self._ai_enabled is not None else False,
        )

    def setAiSettings(self, settings: t.Any) -> None:
        """Заполнить виджет из настроек, не поднимая сигналы сохранения."""
        if self._ai_enabled is not None:
            self._ai_enabled.blockSignals(True)
            self._ai_enabled.setChecked(bool(settings.enabled))
            self._ai_enabled.blockSignals(False)
        if self._ai_provider is not None:
            self._ai_provider.blockSignals(True)
            index = self._ai_provider.findData(settings.provider)
            self._ai_provider.setCurrentIndex(max(index, 0))
            self._ai_provider.blockSignals(False)
        if self._ai_url is not None:
            self._ai_url.blockSignals(True)
            self._ai_url.setText(settings.base_url)
            self._ai_url.blockSignals(False)
        if self._ai_model is not None:
            self._ai_model.blockSignals(True)
            self._ai_model.clear()
            self._ai_model.addItem(settings.model)
            self._ai_model.setCurrentText(settings.model)
            self._ai_model.blockSignals(False)
        if self._ai_key is not None:
            self._ai_key.blockSignals(True)
            self._ai_key.setText(settings.api_key)
            self._ai_key.blockSignals(False)
        if self._ai_timeout is not None:
            self._ai_timeout.blockSignals(True)
            self._ai_timeout.setValue(int(settings.timeout_sec))
            self._ai_timeout.blockSignals(False)
        self._on_ai_changed()

    def setAiModels(self, names: t.List[str]) -> None:
        """Каталог моделей с сервера в выпадающий список (текущая остаётся)."""
        if self._ai_model is None:
            return
        current = self._ai_model.currentText().strip()
        self._ai_model.blockSignals(True)
        self._ai_model.clear()
        for name in names:
            self._ai_model.addItem(name)
        if current and self._ai_model.findText(current) < 0:
            self._ai_model.insertItem(0, current)
        self._ai_model.setCurrentText(current)
        self._ai_model.blockSignals(False)

    def setAiStatus(self, text: str) -> None:
        if self._ai_status is not None:
            self._ai_status.setText(text)

    def setAiBusy(self, busy: bool) -> None:  # noqa: N802 - как у виджетов Qt
        """Погасить/вернуть кнопки сетевых запросов AI на время хождения.

        Сетевые запросы AI лёгкие и идут мимо общего гейта занятости:
        занятость здесь показывает себя - кнопка «в работе», а не молчаливо
        проглоченный клик.
        """
        for attr in ("_ai_models_btn", "_ai_test_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(not busy)
        if busy and self._ai_status is not None:
            self._ai_status.setText(ctx().tr("settings.ai_busy"))

    def _emit_ai_save(self) -> None:
        self.aiSaveRequested.emit(self.collect_ai_settings())

    def _emit_ai_test(self) -> None:
        self.aiTestRequested.emit(self.collect_ai_settings())

    def _emit_ai_models(self) -> None:
        self.aiModelsRequested.emit(self.collect_ai_settings())

    def _on_sounds_toggled(self, enabled: bool) -> None:
        ctx().setSounds(enabled)
        self.soundsChanged.emit(enabled)

    def _add_buttons(self) -> None:
        backup = self._add_button("settings.backup_viewer")
        backup.clicked.connect(self._open_backup_viewer)
        about = self._add_button("menu.about")
        about.clicked.connect(self._show_about)
        self._add_row(backup, about)

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
            for mode in ctx().themeModes():
                self._theme_combo.addItem(ctx().tr(f"settings.theme_{mode}"), mode)
            self._theme_combo.blockSignals(False)
            self._select_data(self._theme_combo, ctx().themeMode())

        if self._accent_combo is not None:
            self._accent_combo.blockSignals(True)
            self._accent_combo.clear()
            for name in ctx().accentIds():
                self._accent_combo.addItem(ctx().tr(f"settings.accent_{name}"), name)
            self._accent_combo.blockSignals(False)
            self._select_data(self._accent_combo, ctx().accent())

        if self._motion_combo is not None:
            self._motion_combo.blockSignals(True)
            self._motion_combo.clear()
            for level in ctx().motionLevels():
                self._motion_combo.addItem(ctx().tr(f"settings.motion_{level}"), level)
            self._motion_combo.blockSignals(False)
            self._select_data(self._motion_combo, ctx().motion())

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

    def _on_accent_selected(self, index: int) -> None:
        if self._accent_combo is None:
            return
        name = self._accent_combo.itemData(index)
        if name:
            ctx().setAccent(str(name))
            self.accentChanged.emit(str(name))

    def _on_theme_selected(self, index: int) -> None:
        if self._theme_combo is None:
            return
        mode = self._theme_combo.itemData(index)
        if mode:
            ctx().setThemeMode(str(mode))
            self.themeChanged.emit(ctx().theme())

    def _on_motion_selected(self, index: int) -> None:
        if self._motion_combo is None:
            return
        level = self._motion_combo.itemData(index)
        if level:
            ctx().setMotion(str(level))
            self.motionChanged.emit(str(level))

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
        if len(self._accordions) >= 2:
            self._accordions[0].set_title(ctx().tr("settings.acc_appearance"))
            self._accordions[1].set_title(ctx().tr("settings.acc_ai"))
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


class PlaceTab(EmptyTab):
    """«Место» — куда ушёл мусор: стековая полоса и пропорциональные бары.

    Отвечает на главный вопрос забитого диска: какая категория самая жирная.
    Данные приходят из сводки прошлого скана (то же, что к hero-карточке) —
    без отдельного прохода по диску.
    """

    _CAT_COLORS = (
        "#5b86c9", "#4a9e8c", "#b08a52", "#b06a6a",
        "#8a7fb8", "#5b9aa8", "#a87f5e", "#7a9e6b",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        self._scroll: Optional[QScrollArea] = None
        self._bar: Optional[StorageBar] = None
        self._treemap: Optional[TreemapWidget] = None
        self._rows_layout: Optional[QVBoxLayout] = None
        self._empty_label: Optional[QLabel] = None
        self._status_label: Optional[QLabel] = None
        super().__init__(parent)

    def _result_expands(self) -> bool:
        return True

    def _add_result_area(self) -> None:
        frame, layout = self._make_card()
        self._result_frame = frame
        self._add_section_label("storage.card_title", layout)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("categoryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setMinimumHeight(120)
        host = QWidget(self._scroll)
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(8)

        self._status_label = hint(ctx().tr("storage.hint"), self)
        host_layout.addWidget(self._status_label)

        self._treemap = TreemapWidget(self)
        host_layout.addWidget(self._treemap)

        self._bar = StorageBar(self)
        host_layout.addWidget(self._bar)

        self._empty_label = body(ctx().tr("storage.empty"), self)
        self._empty_label.setProperty("role", "secondary")
        host_layout.addWidget(self._empty_label)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(0)
        host_layout.addLayout(self._rows_layout)
        host_layout.addStretch(1)

        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)
        self._layout.addWidget(frame)

    def set_storage(self, cats: Dict[str, Tuple[str, int]]) -> None:
        """Показать распределение мусора: id -> (название, байты)."""
        items = [(cid, title, int(size))
                 for cid, (title, size) in cats.items() if int(size) > 0]
        items.sort(key=lambda item: -item[2])
        if self._rows_layout is not None:
            while self._rows_layout.count():
                entry = self._rows_layout.takeAt(0)
                widget = entry.widget()
                if widget is not None:
                    widget.deleteLater()
        has_data = bool(items)
        biggest = items[0][2] if items else 0
        segments = []
        treemap_items = []
        for index, (_cid, title, size) in enumerate(items):
            color = self._CAT_COLORS[index % len(self._CAT_COLORS)]
            segments.append((color, size))
            treemap_items.append((color, title, size, human_size(size)))
            if self._rows_layout is not None:
                fraction = (size / biggest) if biggest else 0.0
                self._rows_layout.addWidget(
                    StorageRow(title, human_size(size), fraction, color, self))
        if self._bar is not None:
            self._bar.set_segments(segments)
            self._bar.setVisible(has_data)
        if self._treemap is not None:
            self._treemap.set_items(treemap_items)
            self._treemap.setVisible(has_data)
        if self._empty_label is not None:
            self._empty_label.setVisible(not has_data)

    def retranslate(self) -> None:
        super().retranslate()
        if self._status_label is not None:
            self._status_label.setText(ctx().tr("storage.hint"))
        if self._empty_label is not None:
            self._empty_label.setText(ctx().tr("storage.empty"))

    def _title(self) -> str:
        return ctx().tr("storage.title")

    def _subheading(self) -> str:
        return ctx().tr("storage.subtitle")

    def _description(self) -> str:
        return ctx().tr("storage.description")
