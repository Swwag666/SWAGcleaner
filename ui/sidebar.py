"""Боковое меню SWAGcleaner.

Меню держится свёрнутым в узкую полоску с иконками и плавно выезжает,
когда на него наводят мышь. Если мышь ушла — через короткую задержку
оно так же плавно уезжает обратно.

Задержка нужна, чтобы меню не дёргалось, когда курсор случайно выходит
за край по пути к элементу.

Меню — плавающий слой поверх контента (become_overlay), а не элемент
лейаута: выезд меняет только собственную геометрию, и центральная
страница со сотнями строк не перелопачивает разметку на каждом кадре.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui import icons
from ui.context import ctx
from ui.theme import apply_role_font


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

        # Бренд-блок: иконка всегда видна, название — только в развёрнутом
        # меню. Сворачивание прячет только текст, не сам блок.
        self._brand = QFrame(self)
        self._brand.setObjectName("sidebarBrand")
        brand_row = QHBoxLayout(self._brand)
        brand_row.setContentsMargins(12, 2, 12, 6)
        brand_row.setSpacing(8)
        self._brand_icon = QLabel(self._brand)
        self._brand_icon.setFixedSize(26, 26)
        brand_row.addWidget(self._brand_icon)
        self._brand_title = QLabel("SWAGcleaner", self._brand)
        self._brand_title.setProperty("role", "title")
        apply_role_font(self._brand_title)
        brand_row.addWidget(self._brand_title)
        brand_row.addStretch(1)
        root.addWidget(self._brand)

        self._caption = QLabel(self)
        self._caption.setObjectName("sidebarCaption")
        apply_role_font(self._caption)
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

        # Статус привилегий внизу меню: цветная точка и подпись.
        self._admin_row = QHBoxLayout()
        self._admin_row.setContentsMargins(16, 6, 10, 0)
        self._admin_row.setSpacing(6)
        self._admin_dot = QLabel(self)
        self._admin_dot.setObjectName("statusDot")
        self._admin_dot.setFixedSize(8, 8)
        self._admin_row.addWidget(self._admin_dot)
        self._admin_label = QLabel("", self)
        self._admin_label.setObjectName("sidebarStatus")
        apply_role_font(self._admin_label)
        self._admin_row.addWidget(self._admin_label)
        self._admin_row.addStretch(1)
        root.addLayout(self._admin_row)

        # Плавный выезд: анимируем собственную геометрию (только ширину),
        # контент под меню не перестраивается вовсе.
        self._animation = QPropertyAnimation(self, b"geometry", self)
        self._animation.setDuration(self.ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

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

    def set_admin_level(self, is_admin: bool) -> None:
        """Показать, с какими правами работает приложение.

        is_admin=None можно не передавать: точка рисуется серым до тех
        пор, пока уровень не определён.
        """
        if is_admin is None:
            self._admin_dot.setProperty("kind", "unknown")
            self._admin_label.setText("")
        else:
            key = "sidebar.admin" if is_admin else "sidebar.user"
            self._admin_dot.setProperty("kind", "admin" if is_admin else "user")
            self._admin_label.setText(ctx().tr(key) if self._expanded else "")
        style = self._admin_dot.style()
        style.unpolish(self._admin_dot)
        style.polish(self._admin_dot)

    def add_item(self, icon_name: str, text_key: str) -> QPushButton:
        """Добавить пункт меню. text_key — ключ строки локализации."""
        button = QPushButton(self)
        button.setObjectName("navItem")
        button.setCheckable(True)
        apply_role_font(button)
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
        self._brand_icon.setPixmap(
            icons.pixmap("cleaner", self._colors.get("accent", "#4d8dff"), 22)
        )

    def refresh_icons(self) -> None:
        """Перерисовать иконки: активная — акцентом, остальные — вторичным цветом."""
        normal = self._colors.get("text_secondary", "#92a0b2")
        active = self._colors.get("accent", "#4d8dff")
        for button, name in zip(self._items, self._icon_names):
            color = active if button.isChecked() else normal
            button.setIcon(icons.icon(name, color, self._icon_size))

    # ---------- анимация и наведение ----------

    def become_overlay(self) -> None:
        """Работать плавающим слоем поверх контента.

        Меню не в лейауте: своё место (COLLAPSED_WIDTH) под него держит
        отдельный рельс в лейауте, а само меню живёт абсолютной геометрией
        поверх страницы. Выезд анимирует только свою ширину — ни один
        виджет центральной страницы не получает relayout, и на тяжёлых
        страницах (твики со сотнями строк) выезд не подлагивает.
        """
        parent = self.parentWidget()
        if parent is None:
            return
        parent.installEventFilter(self)
        self._apply_geometry()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self._apply_geometry()
        return False

    def _apply_geometry(self) -> None:
        """Меню всегда прижато к левому краю и во всю высоту родителя."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, self.width(), parent.height())

    def _set_width(self, width: int, animate: bool = True) -> None:
        parent = self.parentWidget()
        height = parent.height() if parent is not None else 0
        if height <= 0:
            height = self.height()
        current = self.width()
        self._animation.stop()
        if not animate:
            self.setGeometry(0, 0, width, height)
            return
        self._animation.setStartValue(QRect(0, 0, current, height))
        self._animation.setEndValue(QRect(0, 0, width, height))
        self._animation.start()

    def _set_labels_visible(self, visible: bool) -> None:
        for button, key in zip(self._items, self._text_keys):
            button.setText(ctx().tr(key) if visible else "")
        self._caption.setText(self._caption_text if visible else "")
        self._brand_title.setText("SWAGcleaner" if visible else "")
        if self._admin_label is not None and self._admin_label.text():
            key = "sidebar.admin" if self._admin_dot.property("kind") == "admin" \
                else "sidebar.user"
            self._admin_label.setText(ctx().tr(key) if visible else "")

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
