"""Боковое меню SWAGcleaner.

Меню держится свёрнутым в узкую полоску с иконками и плавно выезжает,
когда на него наводят мышь. Если мышь ушла — через короткую задержку
оно так же плавно уезжает обратно.

Задержка нужна, чтобы меню не дёргалось, когда курсор случайно выходит
за край по пути к элементу.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import icons
from ui.context import ctx


class Sidebar(QFrame):
    """Сворачивающееся боковое меню с пунктами разделов."""

    pageSelected = Signal(int)

    COLLAPSED_WIDTH = 68
    EXPANDED_WIDTH = 246
    ANIMATION_MS = 240
    COLLAPSE_DELAY_MS = 320

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._items: List[QPushButton] = []
        self._icon_names: List[str] = []
        self._text_keys: List[str] = []
        self._colors: Dict[str, str] = {}
        self._expanded = False
        self._icon_size = 22
        self._caption_text = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 16, 0, 16)
        root.setSpacing(4)

        self._caption = QLabel(self)
        self._caption.setObjectName("sidebarCaption")
        self._caption.setContentsMargins(16, 0, 10, 10)
        root.addWidget(self._caption)
        root.addSpacing(4)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_item_clicked)

        self._nav_layout = QVBoxLayout()
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(2)
        root.addLayout(self._nav_layout)
        root.addStretch(1)

        # Плавный выезд: одновременно тянем минимальную и максимальную ширину,
        # чтобы разметка не сплющивала содержимое во время анимации.
        self._animation = QParallelAnimationGroup(self)
        for prop in (b"minimumWidth", b"maximumWidth"):
            anim = QPropertyAnimation(self, prop, self)
            anim.setDuration(self.ANIMATION_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._animation.addAnimation(anim)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(self.COLLAPSE_DELAY_MS)
        self._collapse_timer.timeout.connect(self.collapse)

        self._set_width(self.COLLAPSED_WIDTH, animate=False)

    # ---------- наполнение ----------

    def set_caption(self, text: str) -> None:
        """Подпись сверху меню (например, версия или название)."""
        self._caption_text = text
        self._caption.setText(text if self._expanded else "")

    def add_item(self, icon_name: str, text_key: str) -> QPushButton:
        """Добавить пункт меню. text_key — ключ строки локализации."""
        button = QPushButton(self)
        button.setObjectName("navItem")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setMinimumHeight(42)
        button.setIconSize(QSize(self._icon_size, self._icon_size))
        button.setToolTip(ctx().tr(text_key))

        index = len(self._items)
        self._items.append(button)
        self._icon_names.append(icon_name)
        self._text_keys.append(text_key)
        self._group.addButton(button, index)
        self._nav_layout.addWidget(button)
        return button

    def set_current(self, index: int) -> None:
        """Отметить активный пункт меню."""
        if 0 <= index < len(self._items):
            self._items[index].setChecked(True)
        self.refresh_icons()

    def current_index(self) -> int:
        return self._group.checkedId()

    def count(self) -> int:
        return len(self._items)

    # ---------- локализация и цвета ----------

    def retranslate(self) -> None:
        """Обновить подписи после смены языка."""
        for button, key in zip(self._items, self._text_keys):
            text = ctx().tr(key)
            button.setToolTip(text)
            if self._expanded:
                button.setText(text)

    def apply_colors(self, colors: Dict[str, str]) -> None:
        """Перекрасить иконки под текущую тему."""
        self._colors = dict(colors)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        """Перерисовать иконки: активная — акцентом, остальные — вторичным цветом."""
        normal = self._colors.get("text_secondary", "#92a0b2")
        active = self._colors.get("accent", "#4d8dff")
        for button, name in zip(self._items, self._icon_names):
            color = active if button.isChecked() else normal
            button.setIcon(icons.icon(name, color, self._icon_size))

    # ---------- анимация и наведение ----------

    def _set_width(self, width: int, animate: bool = True) -> None:
        if not animate:
            self._animation.stop()
            self.setMinimumWidth(width)
            self.setMaximumWidth(width)
            return
        current = self.width()
        self._animation.stop()
        for i in range(self._animation.animationCount()):
            anim = self._animation.animationAt(i)
            anim.setStartValue(current)
            anim.setEndValue(width)
        self._animation.start()

    def _set_labels_visible(self, visible: bool) -> None:
        for button, key in zip(self._items, self._text_keys):
            button.setText(ctx().tr(key) if visible else "")
        self._caption.setText(self._caption_text if visible else "")

    def expand(self) -> None:
        """Развернуть меню."""
        if self._expanded:
            return
        self._expanded = True
        self._set_labels_visible(True)
        self._set_width(self.EXPANDED_WIDTH)
        from ui import sounds as _sounds

        _sounds.play("click")

    def collapse(self) -> None:
        """Свернуть меню в полоску с иконками."""
        if not self._expanded:
            return
        self._expanded = False
        self._set_width(self.COLLAPSED_WIDTH)
        # Подписи убираем в конце анимации, чтобы текст не обрезался рывком.
        QTimer.singleShot(self.ANIMATION_MS, self._finish_collapse)

    def _finish_collapse(self) -> None:
        if not self._expanded:
            self._set_labels_visible(False)

    def is_expanded(self) -> bool:
        return self._expanded

    def enterEvent(self, event: QEnterEvent) -> None:
        self._collapse_timer.stop()
        self.expand()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._collapse_timer.start()
        super().leaveEvent(event)

    def _on_item_clicked(self, index: int) -> None:
        self.set_current(index)
        self.pageSelected.emit(index)
