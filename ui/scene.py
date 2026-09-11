"""Стек страниц со сменой «кадра».

Разделы переключаются не мгновенно, а как сцены в визуальной новелле:
уходящая страница коротко растворяется, приходящая наплывает справа
и проявляется. По длительности это быстрее, чем успевает надоесть,
но достаточно, чтобы переход читался.

Заменяет обычный QStackedWidget, потому что тот сам распоряжается
геометрией детей и не даёт двигать страницу во время анимации.
Здесь геометрию выставляем вручную, зато позиция и прозрачность
страницы свободны.
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSize,
    Signal,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class SceneStack(QWidget):
    """Стек страниц с плавной сменой кадра."""

    currentChanged = Signal(int)

    TRANSITION_MS = 260
    FADE_OUT_MS = 150
    SLIDE_PX = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pages: List[QWidget] = []
        self._effects: Dict[QWidget, QGraphicsOpacityEffect] = {}
        self._current = -1
        self._group: QParallelAnimationGroup | None = None
        self._outgoing: QWidget | None = None

    # ---------- состав ----------

    def addWidget(self, page: QWidget) -> None:  # noqa: N802 - как у QStackedWidget
        page.setParent(self)
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(1.0)
        page.setGraphicsEffect(effect)
        page.hide()
        page.setGeometry(0, 0, self.width(), self.height())
        self._effects[page] = effect
        self._pages.append(page)

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int) -> QWidget:
        return self._pages[index]

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def currentWidget(self) -> QWidget | None:  # noqa: N802
        if 0 <= self._current < len(self._pages):
            return self._pages[self._current]
        return None

    # ---------- геометрия ----------

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(720, 480)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        for page in self._pages:
            page.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    # ---------- переключение ----------

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not (0 <= index < len(self._pages)) or index == self._current:
            return

        previous = self.currentWidget()
        page = self._pages[index]
        self._current = index

        # Если предыдущий переход ещё идёт — доигрываем его мгновенно,
        # иначе анимации накладываются и страницы застревают полупрозрачными.
        self._settle()

        page.setGeometry(0, 0, self.width(), self.height())
        page.move(self.SLIDE_PX, 0)
        page.show()
        page.raise_()
        self._effects[page].setOpacity(0.0)

        group = QParallelAnimationGroup(self)

        fade_in = QPropertyAnimation(self._effects[page], b"opacity", group)
        fade_in.setDuration(self.TRANSITION_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(fade_in)

        slide_in = QPropertyAnimation(page, b"pos", group)
        slide_in.setDuration(self.TRANSITION_MS)
        slide_in.setStartValue(QPoint(self.SLIDE_PX, 0))
        slide_in.setEndValue(QPoint(0, 0))
        slide_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(slide_in)

        if previous is not None and previous is not page:
            # Уходящая гаснет быстрее, чтобы две страницы почти не накладывались.
            fade_out = QPropertyAnimation(self._effects[previous], b"opacity", group)
            fade_out.setDuration(self.FADE_OUT_MS)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
            group.addAnimation(fade_out)
            self._outgoing = previous

        group.finished.connect(self._on_finished)
        self._group = group
        group.start()
        self.currentChanged.emit(index)

    def _on_finished(self) -> None:
        self._settle()

    def _settle(self) -> None:
        """Привести страницы в конечное состояние и остановить анимацию."""
        if self._group is not None and self._group.state() != QAbstractAnimation.State.Stopped:
            self._group.stop()
        outgoing = self._outgoing
        self._outgoing = None
        if outgoing is not None:
            outgoing.hide()
            outgoing.move(0, 0)
            self._effects[outgoing].setOpacity(1.0)
        page = self.currentWidget()
        if page is not None:
            page.move(0, 0)
            self._effects[page].setOpacity(1.0)
            page.show()
            page.raise_()
        for other in self._pages:
            if other is not page:
                other.hide()
