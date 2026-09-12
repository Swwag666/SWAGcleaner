#!/usr/bin/env python
"""Вырезание фона у новых артов помощницы.

Фон — почти чёрный по краям плюс яркие глитчи (полосы, свечения), которые
касаются границ кадра. Персонаж границ не касается (со всех сторон поля фона).
Поэтому два прохода:

1. Заливка фона от границ через карту краёв: идём везде, кроме пикселей с градиентом
   выше порога. Шум фона (градиент ~1) проходится, силуэты (18+, даже тёмные) держат.
2. Квантование (32 цвета) + удаление компонент, касающихся границ, но только для
   квантов темнее LUMA_GATE: убивает яркие куски фона (полосы, свечение, панели).
   Белое (рубашка, блики) гейт не трогает никогда. Персонаж и летающие детали
   границ не касаются и остаются.

Запуск из корня проекта:
    ./venv/Scripts/python.exe tools/cutout_bg.py
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "character" / "raw"

# Файл, порог края (градиент), предел яркости зоны заливки.
JOBS = (
    ("_new-panic.png", 6.0, 150.0),
    ("_new-calm.png", 6.0, 150.0),
    ("_new-think.png", 6.0, 150.0),
)

# Сколько цветов в квантовании для второго прохода и гейт яркости: кванты светлее
# гейта (рубашка, блики) второй проход не трогает никогда.
QUANT_COLORS = 32
LUMA_GATE = 195.0


def flood_dark_bg(rgb: np.ndarray, edge: float, lum_limit: float) -> np.ndarray:
    """Первый проход: заливка фона от границ, не пересекая края.

    Порог измерен: шум фона даёт градиент до 1.4, силуэты (даже тёмные ботинки
    на тёмном) — от 18 и выше. Край считается по градиенту яркости, цветовой
    допуск не нужен: мягкие тени внутри персонажа недостижимы, пока цел силуэт.
    """
    height, width, _ = rgb.shape
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    gy, gx = np.gradient(lum)
    wall = (np.abs(gx) + np.abs(gy)) > edge

    corners = np.stack(
        [
            rgb[0:5, 0:5].reshape(-1, 3),
            rgb[0:5, -5:].reshape(-1, 3),
            rgb[-5:, 0:5].reshape(-1, 3),
            rgb[-5:, -5:].reshape(-1, 3),
        ]
    ).mean(axis=(0, 1))

    bg = np.zeros((height, width), dtype=bool)
    queue = deque()

    def maybe_seed(y: int, x: int) -> None:
        if float(np.abs(rgb[y, x] - corners).sum()) < 40.0 * 3:
            queue.append((y, x))

    for x in range(width):
        maybe_seed(0, x)
        maybe_seed(height - 1, x)
    for y in range(height):
        maybe_seed(y, 0)
        maybe_seed(y, width - 1)

    while queue:
        y, x = queue.popleft()
        if bg[y, x]:
            continue
        bg[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if (
                0 <= ny < height
                and 0 <= nx < width
                and not bg[ny, nx]
                and not wall[ny, nx]
                and lum[ny, nx] < lum_limit
            ):
                queue.append((ny, nx))
    return bg


def drop_border_components(labels: np.ndarray, palette_luma: np.ndarray) -> np.ndarray:
    """Второй проход: убить касающееся границ, но только тёмные кванты."""
    from collections import deque as _deque

    height, width = labels.shape
    condemned = np.zeros_like(labels, dtype=bool)
    seen = np.zeros_like(labels, dtype=bool)
    for sy, sx in (
        [(0, x) for x in range(width)]
        + [(height - 1, x) for x in range(width)]
        + [(y, 0) for y in range(height)]
        + [(y, width - 1) for y in range(height)]
    ):
        if seen[sy, sx]:
            continue
        colour = labels[sy, sx]
        if palette_luma[colour] >= LUMA_GATE:
            seen[sy, sx] = True
            continue
        queue = _deque([(sy, sx)])
        seen[sy, sx] = True
        blob = []
        while queue:
            y, x = queue.popleft()
            blob.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < height
                    and 0 <= nx < width
                    and not seen[ny, nx]
                    and labels[ny, nx] == colour
                ):
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        for y, x in blob:
            condemned[y, x] = True
    return condemned


def cutout(path: Path, edge: float, lum_limit: float) -> Image.Image:
    image = Image.open(path).convert("RGB")
    rgb = np.array(image).astype(np.float32)

    dark_bg = flood_dark_bg(rgb, edge, lum_limit)

    # Квантование медианным срезом: Pillow умеет только степени двойки,
    # поэтому режем до ближайшей сверху и лишнее не трогаем.
    quant = image.quantize(colors=1 << (QUANT_COLORS - 1).bit_length(), method=Image.Quantize.MEDIANCUT)
    palette = np.array(quant.getpalette()[: 256 * 3]).reshape(-1, 3).astype(np.float32)
    palette_luma = 0.2126 * palette[:, 0] + 0.7152 * palette[:, 1] + 0.0722 * palette[:, 2]
    labels = np.array(quant)
    bright_bg = drop_border_components(labels, palette_luma)
    # Тёмный фон из первого прохода тоже считается фоном.
    doomed = bright_bg | dark_bg

    alpha = np.where(doomed, 0, 255).astype(np.uint8)
    mask = Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(0.8))
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def main() -> int:
    for name, edge, lum_limit in JOBS:
        path = RAW / name
        if not path.is_file():
            print(f"{name}: нет файла, пропускаю")
            continue
        result = cutout(path, edge, lum_limit)
        alpha = np.array(result)[..., 3]
        print(f"{name}: фон снят, непрозрачных {int((alpha > 128).sum())} px")
        result.save(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
