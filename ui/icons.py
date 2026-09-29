"""Пиксельные иконки SWAGcleaner.

Рисуются кодом: сетка 8x8, каждая «X» — квадратик. Так иконки остаются
частью пиксельного стиля, масштабируются без размытия, перекрашиваются
под текущую тему и не требуют файлов-картинок.

Использование:
    pixmap("cleaner", theme_palette["text_secondary"], 24)
"""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

# Каждая иконка — восемь строк по восемь символов: "X" рисуем, "." пропускаем.
GRIDS: Dict[str, tuple[str, ...]] = {
    # Советник — искра: система изучена, есть рекомендации.
    "advisor": (
        "...X....",
        "...X....",
        ".X.X.X..",
        "..XXX...",
        "XXXXXXX.",
        "..XXX...",
        ".X.X.X..",
        "...X....",
    ),
    # Чистка — корзина.
    "cleaner": (
        "..XXXX..",
        ".XXXXXX.",
        "XXXXXXXX",
        ".XXXXXX.",
        ".X.XX.X.",
        ".X.XX.X.",
        ".XXXXXX.",
        "..XXXX..",
    ),
    # Дубликаты — два наложенных кадра.
    "dedup": (
        ".XXXXX..",
        ".X...X..",
        ".X.XXXXX",
        ".X.X...X",
        ".XXX...X",
        "...X...X",
        "...XXXXX",
        "........",
    ),
    # Твики — три ползунка.
    "tweaks": (
        "..XX....",
        "XXXXXXXX",
        "..XX....",
        "....XX..",
        "XXXXXXXX",
        "....XX..",
        "......XX",
        "XXXXXXXX",
    ),
    # Настройки — шестерёнка.
    "settings": (
        "..X..X..",
        ".XXXXXX.",
        "XX.XX.XX",
        "XX....XX",
        "XX....XX",
        "XX.XX.XX",
        ".XXXXXX.",
        "..X..X..",
    ),
    # Тёмная тема — месяц.
    "theme_dark": (
        "..XXX...",
        ".XX.....",
        "XX......",
        "XX......",
        "XX......",
        "XX......",
        ".XX.....",
        "..XXX...",
    ),
    # Светлая тема — солнце.
    "theme_light": (
        "...XX...",
        ".X.XX.X.",
        "..XXXX..",
        "XXXXXXXX",
        "XXXXXXXX",
        "..XXXX..",
        ".X.XX.X.",
        "...XX...",
    ),
    # Персонаж — голова и плечи: показать или скрыть помощницу.
    "assistant": (
        "...XX...",
        "..XXXX..",
        ".XXXXXX.",
        "XX.XX.XX",
        ".XXXXXX.",
        "..X..X..",
        ".XX..XX.",
        "XXX..XXX",
    ),
    # Язык — глобус.
    "language": (
        "..XXXX..",
        ".X....X.",
        "X..XX..X",
        "X.XXXX.X",
        "X.XXXX.X",
        "X..XX..X",
        ".X....X.",
        "..XXXX..",
    ),
    # Место — диск с бликом и дорожкой.
    "storage": (
        "XXXXXXXX",
        "X......X",
        "X......X",
        "X.XXX..X",
        "X.XXX..X",
        "X......X",
        "X......X",
        "XXXXXXXX",
    ),
}

CELLS = 8


def pixmap(name: str, color: str, size: int = 24) -> QPixmap:
    """Нарисовать иконку нужного цвета и размера."""
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    grid = GRIDS.get(name)
    if grid is None:
        return result

    step = size / CELLS
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    for row, line in enumerate(grid):
        for col, char in enumerate(line):
            if char != "X":
                continue
            x0 = int(round(col * step))
            y0 = int(round(row * step))
            x1 = int(round((col + 1) * step))
            y1 = int(round((row + 1) * step))
            painter.drawRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))
    painter.end()
    return result


def icon(name: str, color: str, size: int = 24) -> QIcon:
    """Иконка для кнопки меню."""
    return QIcon(pixmap(name, color, size))
