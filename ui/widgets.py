"""Живые мелочи интерфейса: акцентная полоска и набегающие числа.

AccentBar — та самая полоска под шапкой. Раньше она была чистым декором:
проезжала при смене раздела и замирала. Теперь у неё есть второе состояние:
пока идёт работа, по полоске бежит сегмент, и отдельный спиннер не нужен.

AnimatedNumber и StatsRow — показатели результатов. Числа не появляются
готовыми: они «набегают» до нужного значения, и по движению видно, что данные
только что пришли.
"""
from __future__ import annotations

import math
import random
import time
from typing import Dict, Optional

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ui.context import ctx
from ui.theme import apply_role_font


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
        was_animating = self._timer.isActive()
        if abs(target - self._target) < 1e-9 and not was_animating:
            self._target = target
            self._render()
            return
        self._start = self._shown if was_animating else self._shown
        self._target = target
        self._progress = 0.0
        self._timer.start()
        # Свежая порция данных: коротко мигаем, чтобы было видно, что
        # цифра новая, а не пришла раньше времени.
        if not was_animating and target > 0:
            self.pulse()

    def pulse(self) -> None:
        """Короткая вспышка — цифра обновилась."""
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(1.0)
            self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(220)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start()

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
        apply_role_font(self._caption)
        # Переносимая подпись: без неё минимум плитки - вся строка целиком,
        # и ряд из трёх плиток не влезал в узкий вьюпорт скролла.
        self._caption.setWordWrap(True)
        layout.addWidget(self._caption)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._number = AnimatedNumber(self)
        self._number.setProperty("role", "stat")
        apply_role_font(self._number)
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


class StorageBar(QFrame):
    """Стековая полоса диска: сегменты категорий, длина пропорциональна объёму."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("storageBar")
        self.setMinimumHeight(20)
        self._segments: list[tuple[str, int]] = []

    def set_segments(self, segments) -> None:  # noqa: ANN001
        """Задать список (цвет, байты); отрицательные и нули пропускаются."""
        self._segments = [(str(c), int(b)) for c, b in segments if int(b) > 0]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if not self._segments:
            return
        total = sum(size for _, size in self._segments)
        if total <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        x = 0.0
        for color, size in self._segments:
            width = self.width() * size / total
            painter.fillRect(QRectF(x, 0, max(width, 0.0), self.height()), QColor(color))
            x += width
        painter.end()


class StorageRow(QFrame):
    """Строка «название | пропорциональная полоса | объём».

    Длина полосы нормируется по самой крупной категории: та занимает всю
    ширину, остальные показываются относительно неё.
    """

    def __init__(self, title: str, size_text: str, fraction: float,
                 color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("storageRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)

        name = QLabel(title, self)
        name.setProperty("role", "secondary")
        name.setMinimumWidth(150)
        name.setWordWrap(True)
        layout.addWidget(name)

        bar = QProgressBar(self)
        apply_role_font(bar)
        bar.setRange(0, 1000)
        bar.setValue(int(round(max(0.0, min(1.0, fraction)) * 1000)))
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        bar.setStyleSheet(
            "QProgressBar { background-color: transparent; border: none; }"
            f"QProgressBar::chunk {{ background-color: {color}; border-radius: 1px; }}"
        )
        layout.addWidget(bar, 1)

        value = QLabel(size_text, self)
        value.setProperty("role", "secondary")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setMinimumWidth(80)
        layout.addWidget(value)


def _treemap_worst(row: list[float], side: float) -> float:
    """Худшее соотношение сторон строки при укладке вдоль короткой стороны."""
    total = sum(row)
    if total <= 0.0 or side <= 0.0:
        return float("inf")
    total2 = total * total
    side2 = side * side
    worst = max((a * side2 / total2 for a in row), default=0.0)
    key = total2 / (max(row) * side2)
    return max(worst, key)


def _treemap_row(row: list[float], out: list, x: float, y: float,
                 w: float, h: float) -> tuple:
    """Уложить одну строку и вернуть остаток прямоугольника (x, y, w, h)."""
    total = sum(row)
    if w >= h:
        row_h = total / w if w > 0.0 else 0.0
        cx = x
        for area in row:
            cw = area / row_h if row_h > 0.0 else 0.0
            out.append(QRectF(cx, y, cw, row_h))
            cx += cw
        return x, y + row_h, w, max(0.0, h - row_h)
    row_w = total / h if h > 0.0 else 0.0
    cy = y
    for area in row:
        ch = area / row_w if row_w > 0.0 else 0.0
        out.append(QRectF(x, cy, row_w, ch))
        cy += ch
    return x + row_w, y, max(0.0, w - row_w), h


def _treemap_squarify(areas: list[float], row: list[float], out: list,
                      x: float, y: float, w: float, h: float) -> None:
    """Squarified treemap (Брульс и др.) по единичному квадрату."""
    if not areas:
        if row:
            _treemap_row(row, out, x, y, w, h)
        return
    item = areas[0]
    side = min(w, h)
    if not row or _treemap_worst(row, side) >= _treemap_worst(row + [item], side):
        _treemap_squarify(areas[1:], row + [item], out, x, y, w, h)
    else:
        x, y, w, h = _treemap_row(row, out, x, y, w, h)
        _treemap_squarify(areas, [], out, x, y, w, h)


class TreemapWidget(QWidget):
    """Древесная карта категорий: площадь прямоугольника - объём мусора.

    Это «WizTree-момент» страницы «Место»: вместо сухих чисел сразу видно,
    какая категория разъелась больше всего. Прямоугольники укладываются
    squarified-алгоритмом, поэтому формы близки к квадратам, а не к полоскам.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("treemap")
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self._items: list[tuple[str, str, int, str]] = []
        self._rects: list[QRectF] = []
        self._hover: int = -1
        # Раскладку считаем один раз на размер/данные, а не на каждый кадр:
        # squarify на hover раньше пересчитывался заново и сажал CPU.
        self._layout_cache: list[QRectF] = []
        self._layout_key: tuple | None = None

    def set_items(self, items) -> None:  # noqa: ANN001
        """Задать (цвет, название, байты, подпись размера); нули отбрасываются."""
        self._items = [(str(c), str(t), int(s), str(label))
                       for c, t, s, label in items if int(s) > 0]
        self._items.sort(key=lambda entry: -entry[2])
        self._hover = -1
        self._layout_key = None
        self.setToolTip("")
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        self._layout_key = None
        super().resizeEvent(event)

    def _layout(self) -> list[QRectF]:
        """Координаты прямоугольников в пикселях для текущих размеров."""
        key = (self.width(), self.height())
        if self._layout_key == key:
            return self._layout_cache
        self._layout_key = key
        if not self._items:
            self._layout_cache = []
            return self._layout_cache
        total = sum(entry[2] for entry in self._items)
        if total <= 0:
            self._layout_cache = []
            return self._layout_cache
        areas = [entry[2] / total for entry in self._items]
        unit: list[QRectF] = []
        _treemap_squarify(areas, [], unit, 0.0, 0.0, 1.0, 1.0)
        w, h = self.width(), self.height()
        self._layout_cache = [QRectF(r.x() * w, r.y() * h, r.width() * w, r.height() * h)
                              for r in unit]
        return self._layout_cache

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        self._rects = self._layout()
        if not self._rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setFont(self.font())
        metrics = self.fontMetrics()
        for index, ((color, title, _size, _label), rect) in enumerate(
                zip(self._items, self._rects)):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRect(rect)
            if rect.width() > 46 and rect.height() > 18:
                painter.setPen(QColor("#ffffff"))
                text = metrics.elidedText(title, Qt.TextElideMode.ElideRight,
                                          int(rect.width()) - 10)
                painter.drawText(rect.adjusted(5, 3, -5, -3),
                                 int(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignTop), text)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        pos = event.position()
        hover = -1
        for index, rect in enumerate(self._rects):
            if rect.contains(pos):
                hover = index
                break
        if hover != self._hover:
            self._hover = hover
            if hover >= 0 and hover < len(self._items):
                _color, title, _size, label = self._items[hover]
                self.setToolTip(f"{title} - {label}")
            else:
                self.setToolTip("")
        super().mouseMoveEvent(event)


class ParticleBurst(QWidget):
    """Пиксельные квадратики, разлетающиеся из центра и гаснущие.

    Оверлей поверх окна: перехватывает клики насквозь и сам прячется, когда
    все частицы умирают. Цвета - акцент, «on» и белый - читаются в любой теме.
    """

    GRAVITY = 220.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._parts: list[dict] = []
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def burst(self, count: int = 36) -> None:
        """Разлет из центра нижней половины окна."""
        if not self.parent():
            return
        w = max(1, self.parent().width())
        h = max(1, self.parent().height())
        self.resize(w, h)
        cx, cy = w / 2.0, h * 0.55
        rnd = random.Random()
        self._parts = []
        for _ in range(count):
            ang = rnd.uniform(0.0, math.tau)
            speed = rnd.uniform(60.0, 260.0)
            self._parts.append({
                "x": cx, "y": cy,
                "vx": math.cos(ang) * speed,
                "vy": math.sin(ang) * speed - 60.0,
                "size": rnd.choice((3, 4, 5, 6)),
                "life": rnd.uniform(0.55, 1.0),
                "max": 1.0,
                "hue": rnd.choice(("accent", "on", "accent", "white")),
            })
        self._last = time.monotonic()
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, now - self._last)
        self._last = now
        alive: list[dict] = []
        for particle in self._parts:
            particle["life"] -= dt
            if particle["life"] <= 0.0:
                continue
            particle["vy"] += self.GRAVITY * dt
            particle["x"] += particle["vx"] * dt
            particle["y"] += particle["vy"] * dt
            alive.append(particle)
        self._parts = alive
        if not self._parts:
            self._timer.stop()
            self.hide()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if not self._parts:
            return
        from ui.theme import palette

        colors = palette(ctx().theme(), ctx().accent())
        accent = QColor(colors["accent"])
        on_color = QColor(colors["on"])
        white = QColor("#ffffff")
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        for particle in self._parts:
            t = max(0.0, min(1.0, particle["life"] / particle["max"]))
            if particle["hue"] == "accent":
                base = accent
            elif particle["hue"] == "on":
                base = on_color
            else:
                base = white
            color = QColor(base)
            color.setAlpha(int(220 * t))
            size = int(particle["size"])
            painter.setBrush(color)
            painter.drawRect(int(particle["x"]), int(particle["y"]), size, size)
        painter.end()
