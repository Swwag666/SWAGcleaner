"""Подтверждение в стиле визуальной новеллы.

Экран затемняется, снизу поднимается панель со списком «что именно будет
сделано» и кнопками. Список — не абстрактное «продолжить?», а перечень
конкретных действий с пометкой риска: пользователь видит, на что соглашается.

Для действий с файлами (чистка, дубликаты) диалог предлагает два пути:
«В карантин» (файлы переедут в папку просмотра) и «Удалить навсегда»
(безвозвратно). Рядом — Клиння: её настроение и анимация синхронны с
главным окном, и у неё можно спросить «что это за файлы?» прямо из
диалога, не закрывая его: ответ прилетает и сюда, и в реплику приложения.

Диалог всегда накрывает окно целиком: затемнение на часть экрана выглядит
как сбой, а не как пауза. Поэтому размер берётся у родителя и повторяется,
если окно меняют.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QEasingCurve, QObject, QPropertyAnimation, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import sounds
from ui.character import Mascot
from ui.context import ctx
from ui.theme import apply_role_font, palette
from ui.widgets import AnimatedNumber

# Виды риска: как подписать действие и каким цветом.
RISK_KEYS = {
    "low": "advisor.risk_low",
    "medium": "advisor.risk_medium",
    "high": "advisor.risk_high",
}

# Режимы диалога: «действие» — одна кнопка «Применить»; «файлы» —
# выбор между карантином и безвозвратным удалением.
MODE_ACTION = "action"
MODE_FILES = "files"


class FilePreviewDialog(QDialog):
    """Просмотр точных путей перед удалением: что именно уйдёт.

    Список сгруппирован (категории чистки или группы дублей), каждый
    путь можно выделить и скопировать. Одна кнопка кладёт в буфер
    все пути сразу — для разбора в проводнике или блокноте.
    """

    def __init__(
        self,
        parent: QWidget | None,
        entries: Sequence[Tuple[str, Sequence[str]]],
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setObjectName("confirmDialog")
        self._entries = [(head, list(paths)) for head, paths in entries]
        self._build()

    def _build(self) -> None:
        total = sum(len(paths) for _head, paths in self._entries)
        panel = QFrame(self)
        panel.setObjectName("dialogPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        title = QLabel(ctx().tr("preview.title"), panel)
        title.setProperty("role", "title")
        apply_role_font(title)
        head_row.addWidget(title)
        head_row.addStretch(1)
        count = AnimatedNumber(panel)
        count.setProperty("role", "stat")
        apply_role_font(count)
        count.setValue(total)
        count.finish()
        head_row.addWidget(count)
        caption = QLabel(ctx().tr("preview.count"), panel)
        caption.setProperty("role", "secondary")
        head_row.addWidget(caption)
        layout.addLayout(head_row)

        hint = QLabel(ctx().tr("preview.hint"), panel)
        hint.setProperty("role", "hint")
        apply_role_font(hint)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(4)
        for head, paths in self._entries:
            group_row = QFrame(host)
            group_row.setObjectName("dialogItem")
            group_layout = QVBoxLayout(group_row)
            group_layout.setContentsMargins(12, 8, 12, 8)
            group_layout.setSpacing(2)
            head_label = QLabel(head, group_row)
            head_label.setProperty("role", "secondary")
            apply_role_font(head_label)
            head_label.setWordWrap(True)
            group_layout.addWidget(head_label)
            for path in paths:
                path_label = QLabel(path, group_row)
                path_label.setProperty("role", "hint")
                path_label.setWordWrap(True)
                path_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                group_layout.addWidget(path_label)
            host_layout.addWidget(group_row)
        host_layout.addStretch(1)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        copy_button = QPushButton(ctx().tr("preview.copy_all"), panel)
        copy_button.setMinimumHeight(38)
        copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_button.clicked.connect(self._copy_all)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        close_button = QPushButton(ctx().tr("confirm.cancel"), panel)
        close_button.setMinimumHeight(38)
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        self._panel = panel

    def _copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        paths = [p for _head, ps in self._entries for p in ps]
        QApplication.clipboard().setText("\n".join(paths))
        sounds.play("click")

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self._fit_to_parent()
        self.activateWindow()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _fit_to_parent(self) -> None:
        if self.parentWidget() is None:
            return
        pw = self.parentWidget().width()
        ph = self.parentWidget().height()
        # Окно предпросмотра занимает почти весь родителя: длинные списки
        # путей должны быть видны крупными кусками, а не щелью в треть окна.
        w = min(pw - 2 * self.PANEL_MARGIN, 1100)
        h = min(ph - 2 * self.PANEL_MARGIN, int(ph * 0.88))
        self.setGeometry(
            (pw - w) // 2, (ph - h) // 2, w, h)

    PANEL_MARGIN = 40


class ConfirmDialog(QDialog):
    """Список действий с кнопками поверх затемнённого окна.

    mode="files": «В карантин» (Enter) / «Удалить навсегда» / «Отмена».
    mode="action": одна кнопка «Применить», как раньше.

    В файловом режиме панель держит мини-Клинню (настроение и речь
    синхронны с окном-родителем) и поле вопроса к ней: ответ приходит
    и в реплику приложения, и строкой сюда.
    """

    PANEL_MARGIN = 40
    ANIMATION_MS = 220
    MASCOT_SIZE = 104
    # Доля высоты родителя, ниже которой панель не сжимается: простор
    # для списка с найденным важнее плотной компоновки.
    MIN_FRACTION = 0.62

    asked = Signal(str)

    def __init__(
        self,
        parent: QWidget | None,
        items: Sequence[Tuple[str, str]],
        headline: Optional[str] = None,
        note: Optional[str] = None,
        entries: Optional[Sequence[Tuple[str, Sequence[str]]]] = None,
        mode: str = MODE_ACTION,
        apply_text: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        # Диалог сам держит фокус и ловит Enter/Escape: иначе фокус случайно
        # остаётся на «Отмене», и Enter жмёт её вместо подтверждения.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("confirmDialog")

        self._mode = MODE_FILES if mode == MODE_FILES else MODE_ACTION
        self._items = [(text, risk) for text, risk in items]
        self._headline = headline
        self._note_text = note
        self._apply_text = apply_text
        self._entries = [(head, list(paths))
                         for head, paths in (entries or [])]
        self._risk_labels: List[QLabel] = []
        self._slide: Optional[QPropertyAnimation] = None
        self._action = ""

        self._panel = QFrame(self)
        self._panel.setObjectName("dialogPanel")
        self._build_panel(headline, note)
        self._panel.resize(self._panel.sizeHint())

        if self._mode == MODE_FILES:
            self._bind_mascot(parent)
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
        apply_role_font(self._title)
        head_row.addWidget(self._title)
        head_row.addStretch(1)

        self._count = AnimatedNumber(self._panel)
        self._count.setProperty("role", "stat")
        apply_role_font(self._count)
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
        apply_role_font(self._note)
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

        # Клиння в файловом режиме: рядом с полем вопроса, анимация та же,
        # что в окне и оверлее — диалог «дышит» вместе с приложением.
        self._ask_edit: Optional[QLineEdit] = None
        self._ask_button: Optional[QPushButton] = None
        self._answer_label: Optional[QLabel] = None
        self._mini: Optional[Mascot] = None
        if self._mode == MODE_FILES:
            layout.addLayout(self._build_ask_row())

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        if self._entries:
            files_button = QPushButton(
                ctx().tr("preview.open_button").format(
                    count=sum(len(ps) for _h, ps in self._entries)),
                self._panel)
            files_button.setMinimumHeight(38)
            files_button.setCursor(Qt.CursorShape.PointingHandCursor)
            files_button.clicked.connect(self._show_files)
            buttons.addWidget(files_button)
        buttons.addStretch(1)
        self._cancel_button = QPushButton(ctx().tr("confirm.cancel"), self._panel)
        self._cancel_button.setMinimumHeight(38)
        self._cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self._cancel_button)

        if self._mode == MODE_FILES:
            self._confirm_button = QPushButton(
                ctx().tr("confirm.quarantine"), self._panel)
            self._confirm_button.setProperty("role", "primary")
            self._confirm_button.setMinimumHeight(38)
            self._confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._confirm_button.clicked.connect(self.confirm)
            buttons.addWidget(self._confirm_button)
            self._delete_button = QPushButton(
                ctx().tr("confirm.delete_forever"), self._panel)
            self._delete_button.setProperty("role", "danger")
            self._delete_button.setMinimumHeight(38)
            self._delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._delete_button.clicked.connect(self.delete_forever)
            buttons.addWidget(self._delete_button)
        else:
            self._confirm_button = QPushButton(
                self._apply_text or ctx().tr("confirm.apply"), self._panel)
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

    def _build_ask_row(self) -> QHBoxLayout:
        """Клиння + поле вопроса: ответ виден прямо в диалоге."""
        row = QHBoxLayout()
        row.setSpacing(12)
        self._mini = Mascot(self._panel)
        self._mini.setFixedSize(self.MASCOT_SIZE, self.MASCOT_SIZE + 18)
        row.addWidget(self._mini)

        column = QVBoxLayout()
        column.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._ask_edit = QLineEdit(self._panel)
        self._ask_edit.setPlaceholderText(ctx().tr("speech.ask_placeholder"))
        self._ask_edit.setToolTip(ctx().tr("speech.ask_hint"))
        self._ask_edit.returnPressed.connect(self._emit_asked)
        self._ask_edit.setMinimumHeight(38)
        bar.addWidget(self._ask_edit, 1)
        self._ask_button = QPushButton(ctx().tr("speech.ask_button"), self._panel)
        self._ask_button.setProperty("role", "primary")
        self._ask_button.setMinimumHeight(38)
        self._ask_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ask_button.clicked.connect(self._emit_asked)
        bar.addWidget(self._ask_button)
        column.addLayout(bar)

        self._answer_label = QLabel("", self._panel)
        self._answer_label.setProperty("role", "hint")
        apply_role_font(self._answer_label)
        self._answer_label.setWordWrap(True)
        self._answer_label.setVisible(False)
        column.addWidget(self._answer_label)
        row.addLayout(column, 1)
        return row

    def _bind_mascot(self, parent: QWidget | None) -> None:
        """Синхронизировать мини-Клинню с главной: настроение и речь."""
        if self._mini is None:
            return
        main_mascot = getattr(parent, "_mascot", None)
        main_speech = getattr(parent, "_speech", None)
        if main_mascot is not None:
            main_mascot.moodChanged.connect(self._mini.set_mood)
            self._mini.set_mood(main_mascot.mood())
        if main_speech is not None:
            main_speech.typingChanged.connect(self._mini.set_speaking)
            self._mini.set_speaking(main_speech.is_typing())

    def _emit_asked(self) -> None:
        if self._ask_edit is None:
            return
        question = self._ask_edit.text().strip()
        if not question:
            return
        self._ask_edit.clear()
        self.asked.emit(question)

    def ask_widget(self) -> Optional[QLineEdit]:
        return self._ask_edit

    def ask_button(self) -> Optional[QPushButton]:
        return self._ask_button

    def mascot_widget(self) -> Optional[Mascot]:
        return self._mini

    def set_answer(self, text: str) -> None:
        """Показать ответ Клинни внутри диалога (пришёл из окна-родителя)."""
        if self._answer_label is None:
            return
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > 400:
            text = text[:400] + "..."
        self._answer_label.setText(text)
        self._answer_label.setVisible(True)

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
        apply_role_font(badge)
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

    def _show_files(self) -> None:
        dialog = FilePreviewDialog(self, self._entries)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

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
        # Клиння выпрыгивает снизу следом за панелью — тот же «скачок»,
        # что в колонке и оверлее.
        if self._mini is not None:
            self._mini.enter_from_below()

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
        if self._mode == MODE_FILES:
            self._confirm_button.setText(ctx().tr("confirm.quarantine"))
            self._delete_button.setText(ctx().tr("confirm.delete_forever"))
        else:
            self._confirm_button.setText(
                self._apply_text or ctx().tr("confirm.apply"))
        if self._ask_edit is not None:
            self._ask_edit.setPlaceholderText(ctx().tr("speech.ask_placeholder"))
            self._ask_edit.setToolTip(ctx().tr("speech.ask_hint"))
        if self._ask_button is not None:
            self._ask_button.setText(ctx().tr("speech.ask_button"))
        for badge in self._risk_labels:
            risk = badge.property("risk") or "low"
            badge.setText(ctx().tr(RISK_KEYS.get(risk, RISK_KEYS["low"])))

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())
        available = max(parent.height() - 2 * self.PANEL_MARGIN, 120)
        # Панель не должна быть щелевидной при коротком списке: список
        # с найденным занимает заметную часть окна, его видно целиком.
        preferred = max(self._panel.sizeHint().height(),
                        int(parent.height() * self.MIN_FRACTION))
        height = min(preferred, available)
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
        self._action = "quarantine" if self._mode == MODE_FILES else "apply"
        self.accept()

    def delete_forever(self) -> None:
        """Безвозвратное удаление: осознанный клик по красной кнопке."""
        sounds.play("error")
        self._action = "delete"
        self.accept()

    def cancel(self) -> None:
        sounds.play("cancel")
        self._action = ""
        self.reject()

    def action(self) -> str:
        """Что выбрал пользователь: quarantine / delete / apply / ''."""
        return self._action

    @staticmethod
    def ask(
        parent: QWidget | None,
        items: Sequence[Tuple[str, str]],
        headline: Optional[str] = None,
        note: Optional[str] = None,
        entries: Optional[Sequence[Tuple[str, Sequence[str]]]] = None,
        apply_text: Optional[str] = None,
    ) -> bool:
        """Спросить пользователя и вернуть, подтвердил ли он действия."""
        dialog = ConfirmDialog(parent, items, headline, note, entries,
                               apply_text=apply_text)
        try:
            return dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            dialog.deleteLater()

    @staticmethod
    def ask_purge(
        parent: QWidget | None,
        items: Sequence[Tuple[str, str]],
        headline: Optional[str] = None,
        note: Optional[str] = None,
        entries: Optional[Sequence[Tuple[str, Sequence[str]]]] = None,
    ) -> str:
        """Спросить, как удалять файлы.

        Возвращает "quarantine" (в папку просмотра), "delete" (навсегда)
        или "" (отмена).
        """
        dialog = ConfirmDialog(parent, items, headline, note, entries,
                               mode=MODE_FILES)
        # Вопрос из диалога уходит в тот же канал, что и вопрос из
        # оверлея: у родителя (окна) есть _on_ai_question.
        handler = getattr(parent, "_on_ai_question", None)
        if handler is not None:
            dialog.asked.connect(handler)
        try:
            dialog.exec()
            return dialog.action()
        finally:
            dialog.deleteLater()


class QuarantineDialog(QDialog):
    """Окно карантина: посмотреть, что лежит, и распорядиться.

    Записи батчей с галочками: восстановить на исходное место или стереть
    навсегда. Клиння рядом — с той же анимацией, что в окне и оверлее,
    и полем вопроса: «что это за файлы?» можно задать, не закрывая окно.
    """

    PANEL_MARGIN = 40
    ANIMATION_MS = 220
    MASCOT_SIZE = 104
    # Доля высоты родителя, ниже которой панель не сжимается: список
    # записей должен занимать заметную часть окна.
    MIN_FRACTION = 0.62

    asked = Signal(str)
    restoreRequested = Signal(list)  # noqa: N815 - Qt-стиль имён сигналов
    deleteRequested = Signal(list)  # noqa: N815
    openRequested = Signal()  # noqa: N815

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("confirmDialog")

        self._entries: List[Dict[str, Any]] = []
        self._rows: List[Tuple[Dict[str, Any], QCheckBox]] = []
        self._slide: Optional[QPropertyAnimation] = None

        self._panel = QFrame(self)
        self._panel.setObjectName("dialogPanel")
        self._build_panel()
        self._panel.resize(self._panel.sizeHint())
        self._bind_mascot(parent)
        if parent is not None:
            parent.installEventFilter(self)

    def _build_panel(self) -> None:
        from ui.session import human_size

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(10)

        head_row = QHBoxLayout()
        head_row.setSpacing(10)
        self._title = QLabel(ctx().tr("quarantine.window_title"), self._panel)
        self._title.setProperty("role", "title")
        apply_role_font(self._title)
        head_row.addWidget(self._title)
        head_row.addStretch(1)
        self._total_label = QLabel(ctx().tr("quarantine.empty_size"), self._panel)
        self._total_label.setProperty("role", "secondary")
        head_row.addWidget(self._total_label)
        layout.addLayout(head_row)

        self._status = QLabel(ctx().tr("quarantine.loading"), self._panel)
        self._status.setProperty("role", "hint")
        apply_role_font(self._status)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._scroll = QScrollArea(self._panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(6)
        self._host_layout.addStretch(1)
        self._scroll.setWidget(self._host)
        layout.addWidget(self._scroll, 1)

        layout.addSpacing(4)
        divider = QFrame(self._panel)
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Клиння и вопрос — как в подтверждении удаления.
        self._mini = Mascot(self._panel)
        self._mini.setFixedSize(self.MASCOT_SIZE, self.MASCOT_SIZE + 18)
        ask_row = QHBoxLayout()
        ask_row.setSpacing(12)
        ask_row.addWidget(self._mini)
        column = QVBoxLayout()
        column.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._ask_edit = QLineEdit(self._panel)
        self._ask_edit.setPlaceholderText(ctx().tr("speech.ask_placeholder"))
        self._ask_edit.setToolTip(ctx().tr("speech.ask_hint"))
        self._ask_edit.returnPressed.connect(self._emit_asked)
        self._ask_edit.setMinimumHeight(38)
        bar.addWidget(self._ask_edit, 1)
        self._ask_button = QPushButton(ctx().tr("speech.ask_button"), self._panel)
        self._ask_button.setProperty("role", "primary")
        self._ask_button.setMinimumHeight(38)
        self._ask_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ask_button.clicked.connect(self._emit_asked)
        bar.addWidget(self._ask_button)
        column.addLayout(bar)
        self._answer_label = QLabel("", self._panel)
        self._answer_label.setProperty("role", "hint")
        apply_role_font(self._answer_label)
        self._answer_label.setWordWrap(True)
        self._answer_label.setVisible(False)
        column.addWidget(self._answer_label)
        ask_row.addLayout(column, 1)
        layout.addLayout(ask_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._open_button = QPushButton(ctx().tr("quarantine.open_folder"), self._panel)
        self._open_button.setMinimumHeight(38)
        self._open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_button.clicked.connect(self.openRequested.emit)
        buttons.addWidget(self._open_button)
        buttons.addStretch(1)
        self._restore_button = QPushButton(
            ctx().tr("quarantine.restore_button"), self._panel)
        self._restore_button.setProperty("role", "primary")
        self._restore_button.setMinimumHeight(38)
        self._restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_button.setEnabled(False)
        self._restore_button.clicked.connect(self._emit_restore)
        buttons.addWidget(self._restore_button)
        self._delete_button = QPushButton(
            ctx().tr("quarantine.delete_selected"), self._panel)
        self._delete_button.setProperty("role", "danger")
        self._delete_button.setMinimumHeight(38)
        self._delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self._emit_delete)
        buttons.addWidget(self._delete_button)
        self._close_button = QPushButton(ctx().tr("confirm.cancel"), self._panel)
        self._close_button.setMinimumHeight(38)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.clicked.connect(self.reject)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        for key, slot in (
            (Qt.Key.Key_Escape, self.reject),
        ):
            shortcut = QShortcut(QKeySequence(key), self,
                                 context=Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)

    def _bind_mascot(self, parent: QWidget | None) -> None:
        main_mascot = getattr(parent, "_mascot", None)
        main_speech = getattr(parent, "_speech", None)
        if main_mascot is not None:
            main_mascot.moodChanged.connect(self._mini.set_mood)
            self._mini.set_mood(main_mascot.mood())
        if main_speech is not None:
            main_speech.typingChanged.connect(self._mini.set_speaking)
            self._mini.set_speaking(main_speech.is_typing())

    # ---------- данные ----------

    def set_entries(self, entries: Sequence[Dict[str, Any]],
                    total_bytes: int) -> None:
        """Показать записи карантина (пришли из сессии)."""
        from ui.session import human_size

        self._entries = [dict(e) for e in entries]
        self._rows = []
        # Пересобираем содержимое скролла: старый host выбрасываем целиком.
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(6)
        if not self._entries:
            self._status.setText(ctx().tr("quarantine.empty"))
        else:
            self._status.setText(ctx().tr("quarantine.window_hint"))
        for entry in self._entries:
            self._host_layout.addWidget(self._build_row(entry))
        self._host_layout.addStretch(1)
        self._scroll.setWidget(self._host)
        self._total_label.setText(
            ctx().tr("quarantine.size_line").format(
                size=human_size(total_bytes),
                batches=len({e.get("batch_name", "") for e in self._entries})))
        self._sync_buttons()

    def _build_row(self, entry: Dict[str, Any]) -> QFrame:
        from ui.session import human_size

        row = QFrame(self._host)
        row.setObjectName("dialogItem")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(2)

        name = str(entry.get("path") or entry.get("stashed") or "")
        base = name.replace("/", "\\").split("\\")[-1] or "?"
        box = QCheckBox(base, row)
        box.setChecked(True)
        box.toggled.connect(self._sync_buttons)
        layout.addWidget(box)

        origin = str(entry.get("path") or "")
        meta_bits = []
        if origin:
            meta_bits.append(origin)
        size = int(entry.get("size", 0) or 0)
        if size:
            meta_bits.append(human_size(size))
        batch = str(entry.get("batch_name", ""))
        if batch:
            meta_bits.append(batch)
        meta = QLabel(" · ".join(meta_bits), row)
        meta.setProperty("role", "hint")
        apply_role_font(meta)
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(meta)

        can_restore = bool(origin) and bool(entry.get("stashed"))
        if not can_restore:
            hint = QLabel(ctx().tr("quarantine.restore_unavailable"), row)
            hint.setProperty("role", "hint")
            apply_role_font(hint)
            layout.addWidget(hint)

        self._rows.append((entry, box))
        return row

    def selected_entries(self) -> List[Dict[str, Any]]:
        return [dict(entry) for entry, box in self._rows if box.isChecked()]

    def _sync_buttons(self) -> None:
        count = sum(1 for _e, box in self._rows if box.isChecked())
        self._restore_button.setEnabled(count > 0)
        self._delete_button.setEnabled(count > 0)
        suffix = f" ({count})" if count else ""
        self._restore_button.setText(
            ctx().tr("quarantine.restore_button") + suffix)
        self._delete_button.setText(
            ctx().tr("quarantine.delete_selected") + suffix)

    def _emit_restore(self) -> None:
        selected = self.selected_entries()
        if selected:
            sounds.play("click")
            self.restoreRequested.emit(selected)

    def _emit_delete(self) -> None:
        from ui.session import human_size

        selected = self.selected_entries()
        if not selected:
            return
        # Второй вопрос — тот же список с кнопкой «Стереть навсегда».
        items = [(
            f"{(str(e.get('path') or e.get('stashed') or '')).replace('/', chr(92)).split(chr(92))[-1]}"
            f" · {human_size(int(e.get('size', 0) or 0))}", "high")
            for e in selected[:20]]
        if len(selected) > 20:
            items.append((ctx().tr("quarantine.more_items").format(
                count=len(selected) - 20), "low"))
        action = ConfirmDialog.ask_purge(
            self, items, headline=ctx().tr("quarantine.delete_title"),
            note=ctx().tr("quarantine.delete_note"))
        if action != "delete":
            return
        self.deleteRequested.emit(selected)

    def _emit_asked(self) -> None:
        question = self._ask_edit.text().strip()
        if not question:
            return
        self._ask_edit.clear()
        self.asked.emit(question)

    def set_answer(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        if len(text) > 400:
            text = text[:400] + "..."
        self._answer_label.setText(text)
        self._answer_label.setVisible(True)

    def ask_widget(self) -> QLineEdit:
        return self._ask_edit

    def ask_button(self) -> QPushButton:
        return self._ask_button

    def mascot_widget(self) -> Mascot:
        return self._mini

    def setStatusText(self, text: str) -> None:  # noqa: N802
        self._status.setText(text)

    # ---------- показ ----------

    def showEvent(self, event) -> None:  # noqa: ANN001
        self._fit_to_parent()
        super().showEvent(event)
        self.activateWindow()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        final = self._panel.pos()
        self._slide = QPropertyAnimation(self._panel, b"pos", self)
        self._slide.setDuration(self.ANIMATION_MS)
        self._slide.setStartValue(QPoint(final.x(), self.height()))
        self._slide.setEndValue(final)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.start()
        self._mini.enter_from_below()

    def retranslate(self) -> None:
        self._title.setText(ctx().tr("quarantine.window_title"))
        self._status.setText(ctx().tr("quarantine.window_hint"))
        self._open_button.setText(ctx().tr("quarantine.open_folder"))
        self._close_button.setText(ctx().tr("confirm.cancel"))
        self._ask_edit.setPlaceholderText(ctx().tr("speech.ask_placeholder"))
        self._ask_edit.setToolTip(ctx().tr("speech.ask_hint"))
        self._ask_button.setText(ctx().tr("speech.ask_button"))
        self._sync_buttons()

    def _fit_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())
        available = max(parent.height() - 2 * self.PANEL_MARGIN, 120)
        preferred = max(self._panel.sizeHint().height(),
                        int(parent.height() * self.MIN_FRACTION))
        height = min(preferred, available)
        width = max(parent.width() - 2 * self.PANEL_MARGIN, 260)
        self._panel.resize(width, height)
        self._panel.move(self.PANEL_MARGIN, parent.height() - height - self.PANEL_MARGIN)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self._fit_to_parent()
        return False

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(palette(ctx().theme())["scrim"]))
        painter.end()


class HideDrivesDialog(QDialog):
    """Выбор букв дисков для скрытия: чекбоксы по существующим дискам.

    B и C не предлагаются (системный и исторический флоппи — прятать их
    себе дороже). Возвращает список букв или None при отмене.
    """

    def __init__(self, parent: QWidget | None, drives: Sequence[str],
                 current: Sequence[str]) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox, QGridLayout
        self.setModal(True)
        self.setWindowTitle(ctx().tr("tweaks.hidepart_title"))
        layout = QVBoxLayout(self)
        note = QLabel(ctx().tr("tweaks.hidepart_note"), self)
        note.setWordWrap(True)
        layout.addWidget(note)
        grid = QGridLayout()
        self._boxes: List[Tuple[str, object]] = []
        for i, letter in enumerate(drives):
            box = QCheckBox(f"{letter}:", self)
            box.setChecked(letter in current)
            grid.addWidget(box, i // 4, i % 4)
            self._boxes.append((letter, box))
        layout.addLayout(grid)
        if not drives:
            layout.addWidget(QLabel(ctx().tr("tweaks.hidepart_empty"), self))
        buttons = QHBoxLayout()
        ok = QPushButton(ctx().tr("tweaks.hidepart_apply"), self)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(ctx().tr("confirm.cancel"), self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def letters(self) -> List[str]:
        return [letter for letter, box in self._boxes if box.isChecked()]

    @staticmethod
    def ask(parent: QWidget | None, drives: Sequence[str],
            current: Sequence[str]) -> Optional[List[str]]:
        dialog = HideDrivesDialog(parent, drives, current)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.letters()
            return None
        finally:
            dialog.deleteLater()
