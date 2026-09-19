"""Подтверждение в стиле визуальной новеллы.

Экран затемняется, снизу поднимается панель со списком «что именно будет
сделано» и двумя кнопками. Список — не абстрактное «продолжить?», а перечень
конкретных действий с пометкой риска: пользователь видит, на что соглашается.

Диалог всегда накрывает окно целиком: затемнение на часть экрана выглядит
как сбой, а не как пауза. Поэтому размер берётся у родителя и повторяется,
если окно меняют.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QEasingCurve, QObject, QPropertyAnimation, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import sounds
from ui.context import ctx
from ui.theme import palette
from ui.widgets import AnimatedNumber

# Виды риска: как подписать действие и каким цветом.
RISK_KEYS = {
    "low": "advisor.risk_low",
    "medium": "advisor.risk_medium",
    "high": "advisor.risk_high",
}


class ConfirmDialog(QDialog):
    """Список действий с двумя кнопками поверх затемнённого окна."""

    PANEL_MARGIN = 40
    ANIMATION_MS = 220

    def __init__(
        self,
        parent: QWidget | None,
        items: Sequence[Tuple[str, str]],
        headline: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        # Диалог сам держит фокус и ловит Enter/Escape: иначе фокус случайно
        # остаётся на «Отмене», и Enter жмёт её вместо подтверждения.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("confirmDialog")

        self._items = [(text, risk) for text, risk in items]
        self._headline = headline
        self._note_text = note
        self._risk_labels: List[QLabel] = []
        self._slide: Optional[QPropertyAnimation] = None

        self._panel = QFrame(self)
        self._panel.setObjectName("dialogPanel")
        self._build_panel(headline, note)
        self._panel.resize(self._panel.sizeHint())

        if parent is not None:
            parent.installEventFilter(self)

    # ---------- сборка ----------

    def _build_panel(self, headline: Optional[str], note: Optional[str]) -> None:
        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        self._title = QLabel(self._headline or ctx().tr("confirm.title"), self._panel)
        self._title.setProperty("role", "title")
        head_row.addWidget(self._title)
        head_row.addStretch(1)

        self._count = AnimatedNumber(self._panel)
        self._count.setProperty("role", "stat")
        self._count.setValue(len(self._items))
        self._count.finish()
        head_row.addWidget(self._count)
        self._count_caption = QLabel(ctx().tr("confirm.count"), self._panel)
        self._count_caption.setProperty("role", "secondary")
        head_row.addWidget(self._count_caption)
        layout.addLayout(head_row)

        self._note = QLabel(
            self._note_text if self._note_text is not None else ctx().tr("confirm.note"),
            self._panel,
        )
        self._note.setProperty("role", "hint")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)

        # Список действий — в прокрутке: длинный перечень (сотня пунктов)
        # не раздувает панель за экран, а листается внутри неё.
        self._scroll = QScrollArea(self._panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)
        for text, risk in self._items:
            host_layout.addWidget(self._build_item(host, text, risk))
        host_layout.addStretch(1)
        self._scroll.setWidget(host)
        layout.addWidget(self._scroll, 1)

        layout.addSpacing(4)
        self._divider = QFrame(self._panel)
        self._divider.setObjectName("divider")
        self._divider.setFixedHeight(1)
        layout.addWidget(self._divider)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self._cancel_button = QPushButton(ctx().tr("confirm.cancel"), self._panel)
        self._cancel_button.setMinimumHeight(38)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self._cancel_button)

        self._confirm_button = QPushButton(ctx().tr("confirm.apply"), self._panel)
        self._confirm_button.setProperty("role", "primary")
        self._confirm_button.setMinimumHeight(38)
        self._confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confirm_button.clicked.connect(self.confirm)
        buttons.addWidget(self._confirm_button)
        layout.addLayout(buttons)

        # Enter/Escape работают из любого фокуса: шорткат окна перехватывает
        # клавишу раньше кнопки, поэтому «случайный» фокус не может подтвердить
        # или отменить диалог вопреки намерению клавиши.
        for key, slot in (
            (Qt.Key.Key_Return, self.confirm),
            (Qt.Key.Key_Enter, self.confirm),
            (Qt.Key.Key_Escape, self.cancel),
        ):
            shortcut = QShortcut(QKeySequence(key), self,
                                 context=Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    def _build_item(self, parent: QWidget, text: str, risk: str) -> QFrame:
        """Строка списка: пометка риска слева, само действие справа."""
        risk = risk if risk in RISK_KEYS else "low"
        row = QFrame(parent)
        row.setObjectName("dialogItem")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(12)

        badge = QLabel(ctx().tr(RISK_KEYS[risk]), row)
        badge.setObjectName("riskBadge")
        badge.setProperty("risk", risk)
        badge.setMinimumWidth(74)
        badge.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._risk_labels.append(badge)
        layout.addWidget(badge)

        label = QLabel(text, row)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        return row

    # ---------- содержимое ----------

    def items(self) -> List[Tuple[str, str]]:
        return list(self._items)

    def count(self) -> int:
        return len(self._items)

    def panel(self) -> QFrame:
        return self._panel

    def confirm_button(self) -> QPushButton:
        return self._confirm_button

    def cancel_button(self) -> QPushButton:
        return self._cancel_button

    # ---------- показ ----------

    def showEvent(self, event) -> None:  # noqa: ANN001
        self._fit_to_parent()
        super().showEvent(event)
        # Диалог обязан забрать активацию сам: без неё клавиатура остаётся у
        # главного окна и Enter/Escape до подтверждения не доходят.
        self.activateWindow()
        self.raise_()
        # Фокус на самом диалоге: Enter и Escape обрабатывает keyPressEvent
        # (подтвердить/отменить), а не кнопка, которой фокус достался случайно.
        # Кнопки по-прежнему достижимы Tab-ом и кликом. Активация окна приходит
        # асинхронно и может отдать фокус первой кнопке — перехватываем снова.
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._take_focus)
        # Панель поднимается снизу — как диалог в визуальной новелле.
        final = self._panel.pos()
        self._slide = QPropertyAnimation(self._panel, b"pos", self)
        self._slide.setDuration(self.ANIMATION_MS)
        self._slide.setStartValue(QPoint(final.x(), self.height()))
        self._slide.setEndValue(final)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._drop_slide)
        self._slide.start()

    def _take_focus(self) -> None:
        """Забрать фокус у кнопки, если активация окна отдала его ей."""
        if self.focusWidget() is not self:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _drop_slide(self) -> None:
        """Отпустить завершённую анимацию, чтобы она не висела на диалоге."""
        if self._slide is not None:
            self._slide.deleteLater()
            self._slide = None

    def retranslate(self) -> None:
        """Статичные подписи диалога на языке, актуальном прямо сейчас.

        Строки пунктов приходят от вызывающего уже готовыми — их не трогаем;
        меняем только собственные надписи (и только если они не были заданы
        явным текстом при создании).
        """
        if self._headline is None:
            self._title.setText(ctx().tr("confirm.title"))
        self._count_caption.setText(ctx().tr("confirm.count"))
        if self._note_text is None:
            self._note.setText(ctx().tr("confirm.note"))
        self._cancel_button.setText(ctx().tr("confirm.cancel"))
        self._confirm_button.setText(ctx().tr("confirm.apply"))
        for badge in self._risk_labels:
            risk = badge.property("risk") or "low"
            badge.setText(ctx().tr(RISK_KEYS.get(risk, RISK_KEYS["low"])))

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())
        height = min(self._panel.sizeHint().height(), max(parent.height() - 2 * self.PANEL_MARGIN, 120))
        width = max(parent.width() - 2 * self.PANEL_MARGIN, 260)
        self._panel.resize(width, height)
        self._panel.move(self.PANEL_MARGIN, parent.height() - height - self.PANEL_MARGIN)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._fit_to_parent()
        return False

    def paintEvent(self, event) -> None:  # noqa: ANN001
        """Затемнение поверх окна: цвет берём из палитры текущей темы."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette(ctx().theme())["scrim"]))
        painter.end()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
            return
        super().keyPressEvent(event)

    # ---------- результат ----------

    def confirm(self) -> None:
        sounds.play("done")
        self.accept()

    def cancel(self) -> None:
        sounds.play("cancel")
        self.reject()

    @staticmethod
    def ask(
        parent: QWidget | None,
        items: Sequence[Tuple[str, str]],
        headline: Optional[str] = None,
        note: Optional[str] = None,
    ) -> bool:
        """Спросить пользователя и вернуть, подтвердил ли он действия."""
        dialog = ConfirmDialog(parent, items, headline, note)
        try:
            return dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()
