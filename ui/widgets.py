"""Живые мелочи интерфейса: акцентная полоска и набегающие числа.

AccentBar — та самая полоска под шапкой. Раньше она была чистым декором:
проезжала при смене раздела и замирала. Теперь у неё есть второе состояние:
пока идёт работа, по полоске бежит сегмент, и отдельный спиннер не нужен.

AnimatedNumber и StatsRow — показатели результатов. Числа не появляются
готовыми: они «набегают» до нужного значения, и по движению видно, что данные
только что пришли.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.context import ctx


class AccentBar(QFrame):
    """Полоска под шапкой: проезд при смене раздела и бегунок во время работы."""

    SWEEP_MS = 340
    TICK_MS = 26
    # Длина бегущего сегмента и скорость, с которой он ползёт по полоске.
    SEGMENT_PX = 120
    SPEED_PX_PER_SEC = 900.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("accentBar")
        self.setFixedHeight(2)
        self.setMaximumWidth(0)

        self._busy = False
        self._phase = 0.0
        self._accent = QColor("#4d8dff")

        self._animation = QPropertyAnimation(self, b"maximumWidth", self)
        self._animation.setDuration(self.SWEEP_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

        if parent is not None:
            # Ширину полоске задаёт родитель (шапка), а его размер приходит
            # уже после того, как окно пересчитает разметку. Поэтому следим
            # за родителем сами: иначе на старте полоска застревала бы той
            # ширины, которая была у шапки в момент первого проезда.
            parent.installEventFilter(self)

    # ---------- оформление ----------

    def target_width(self) -> int:
        """Ширина, которую полоска должна занимать."""
        parent = self.parentWidget()
        return max(parent.width() if parent is not None else self.width(), 1)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Resize:
            self.sync_width(self.target_width())
        return False

    def apply_colors(self, colors: Dict[str, str]) -> None:
        """Взять акцентный цвет из палитры текущей темы."""
        self._accent = QColor(colors.get("accent", "#4d8dff"))
        self.update()

    # ---------- проезд ----------

    def is_sweeping(self) -> bool:
        return self._animation.state() != QPropertyAnimation.State.Stopped

    def sweep(self, full_width: Optional[int] = None) -> None:
        """Проехать слева направо (смена раздела)."""
        self._animation.stop()
        self.setMaximumWidth(0)
        self._animation.setStartValue(0)
        self._animation.setEndValue(max(full_width, 1) if full_width else self.target_width())
        self._animation.start()

    def sync_width(self, full_width: Optional[int] = None) -> None:
        """Подогнать полоску под новый размер окна."""
        width = max(full_width, 1) if full_width else self.target_width()
        if self._animation.state() == QPropertyAnimation.State.Stopped:
            self.setMaximumWidth(width)
        else:
            self._animation.setEndValue(width)

    # ---------- работа ----------

    def is_busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Включить бегущий сегмент: полоска становится индикатором занятости."""
        if busy == self._busy:
            return
        self._busy = busy
        self._phase = 0.0
        self.setProperty("mode", "busy" if busy else "normal")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

        if not busy:
            self._timer.stop()
            self.update()
            return

        # Пока идёт работа, полоска занимает всю ширину шапки, а по ней бежит
        # сегмент — «проезд» в этот момент неуместен.
        self._animation.stop()
        self.setMaximumWidth(self.target_width())
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        self._phase += self.TICK_MS / 1000.0 * self.SPEED_PX_PER_SEC
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if not self._busy:
            return
        width = self.width()
        if width <= 0:
            return
        segment = min(self.SEGMENT_PX, max(24, width // 4))
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        offset = int(self._phase) % (width + segment) - segment
        painter.drawRect(offset, 0, segment, self.height())
        painter.end()


class AnimatedNumber(QLabel):
    """Число, которое набегает до нужного значения, а не появляется готовым."""

    STEP_MS = 24
    DURATION_MS = 620

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = 0.0
        self._shown = 0.0
        self._start = 0.0
        self._decimals = 0
        self._progress = 0.0
        self._render()

        self._timer = QTimer(self)
        self._timer.setInterval(self.STEP_MS)
        self._timer.timeout.connect(self._tick)

    # ---------- состояние ----------

    def value(self) -> float:
        """Целевое значение."""
        return self._target

    def shown_value(self) -> float:
        """То, что сейчас на экране."""
        return self._shown

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def setValue(self, value: float, decimals: Optional[int] = None) -> None:  # noqa: N802
        """Задать новое значение: число само добежит до него."""
        if decimals is not None:
            self._decimals = max(0, decimals)
        target = float(value)
        if abs(target - self._target) < 1e-9 and not self._timer.isActive():
            self._target = target
            self._render()
            return
        self._start = self._shown if self._timer.isActive() else self._shown
        self._target = target
        self._progress = 0.0
        self._timer.start()

    def finish(self) -> None:
        """Доехать сразу (для скриншотов и тестов)."""
        self._timer.stop()
        self._shown = self._target
        self._render()

    def _tick(self) -> None:
        self._progress = min(1.0, self._progress + self.STEP_MS / self.DURATION_MS)
        eased = 1.0 - (1.0 - self._progress) ** 3
        self._shown = self._start + (self._target - self._start) * eased
        if self._progress >= 1.0:
            self._shown = self._target
            self._timer.stop()
        self._render()

    def _render(self) -> None:
        text = f"{self._shown:,.{self._decimals}f}".replace(",", " ")
        self.setText(text)


class StatTile(QFrame):
    """Показатель: мелкая подпись сверху, крупное набегающее число снизу."""

    def __init__(
        self,
        caption_key: str,
        unit_key: Optional[str] = None,
        decimals: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("statTile")
        self._caption_key = caption_key
        self._unit_key = unit_key

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(2)

        self._caption = QLabel(self)
        self._caption.setProperty("role", "section")
        layout.addWidget(self._caption)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._number = AnimatedNumber(self)
        self._number.setProperty("role", "stat")
        self._number.setValue(0, decimals)
        self._number.finish()
        row.addWidget(self._number)
        self._unit = QLabel(self)
        self._unit.setProperty("role", "secondary")
        row.addWidget(self._unit, 0, Qt.AlignmentFlag.AlignBaseline)
        row.addStretch(1)
        layout.addLayout(row)

        self.retranslate()

    # ---------- состояние ----------

    def setValue(self, value: float) -> None:  # noqa: N802
        self._number.setValue(value)

    def value(self) -> float:
        return self._number.value()

    def number(self) -> AnimatedNumber:
        return self._number

    def retranslate(self) -> None:
        """Перевести подпись и единицу измерения."""
        self._caption.setText(ctx().tr(self._caption_key))
        unit = ctx().tr(self._unit_key) if self._unit_key else ""
        self._unit.setText(unit)


class StatsRow(QWidget):
    """Ряд показателей: подпись — число — единица."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._tiles: Dict[str, StatTile] = {}

    def add(
        self,
        key: str,
        caption_key: str,
        unit_key: Optional[str] = None,
        decimals: int = 0,
    ) -> StatTile:
        tile = StatTile(caption_key, unit_key, decimals, self)
        self._tiles[key] = tile
        self._layout.addWidget(tile, 1)
        return tile

    def keys(self) -> tuple:
        return tuple(self._tiles)

    def tile(self, key: str) -> Optional[StatTile]:
        return self._tiles.get(key)

    def setValue(self, key: str, value: float) -> None:  # noqa: N802
        tile = self._tiles.get(key)
        if tile is not None:
            tile.setValue(value)

    def value(self, key: str) -> float:
        tile = self._tiles.get(key)
        return tile.value() if tile is not None else 0.0

    def has_values(self) -> bool:
        """Показывали ли уже какие-то числа (а не нули по умолчанию)."""
        return any(tile.value() for tile in self._tiles.values())

    def finish(self) -> None:
        """Доехать всем числам сразу."""
        for tile in self._tiles.values():
            tile.number().finish()

    def retranslate(self) -> None:
        for tile in self._tiles.values():
            tile.retranslate()
