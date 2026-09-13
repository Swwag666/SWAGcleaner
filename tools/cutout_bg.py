#!/usr/bin/env python
"""Подготовка сырых артов помощницы: альфа, шум вырезания, каноническое имя.

Картинки приходят уже с вырезанным фоном, поэтому инструмент больше не угадывает,
где фон: он берёт альфу картинки как есть и только убирает шум вырезания.

1. Вуаль (альфа 1..2, невидимые крохи по всему фону) — в ноль: это остатки
   вырезания, в кадре им делать нечего.
2. Плотное тело (альфа 250..254) — в 255. Вырезалка оставила персонажа чуть
   прозрачным, и на тёмной теме сквозь него еле заметно просвечивает фон.

Если альфы в картинке нет (фон зашит в неё), фон заливается от границ кадра по
цвету угла. Это работает для однотонного фона; про сложный (полосы, глитчи,
панели) инструмент честно пишет, что не умеет, — такой фон надо вырезать до.

Прошлый вариант этого инструмента (заливка по карте краёв, квантование и списки
прямоугольников-добивок) удалён вместе с артами с зашитым фоном: он прорубал в
персонаже дыры там, где тёмная одежда похожа на тёмный фон.

Запуск из корня проекта:
    ./venv/Scripts/python.exe tools/cutout_bg.py

Положи картинку в assets/character/raw под каноническим именем (cleaner-panic.png,
cleaner-calm.png, cleaner-think.png, cleaner-idle.png, cleaner-scan.png) — инструмент
отработает её на месте и коротко отчитается цифрами.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "character" / "raw"

# Файл и допуск цвета для заливки однотонного фона (если альфы в картинке нет).
JOBS = (
    ("cleaner-panic.png", 26.0),
    ("cleaner-calm.png", 26.0),
    ("cleaner-think.png", 26.0),
    ("cleaner-idle.png", 26.0),
    ("cleaner-scan.png", 26.0),
)

# Альфа 1..2 — невидимый шум вырезания по фону, обнуляем.
VEIL_MAX = 2
# Альфа 250..254 — плотное тело, подтягиваем к полной непрозрачности.
SOLID_MIN = 250


def clean_alpha(alpha: np.ndarray) -> np.ndarray:
    """Убрать шум вырезания: вуаль в ноль, плотное тело в полную непрозрачность."""
    result = alpha.copy()
    result[result <= VEIL_MAX] = 0
    result[result >= SOLID_MIN] = 255
    return result


def flat_background(rgb: np.ndarray, tolerance: float) -> np.ndarray:
    """Залить однотонный фон от границ кадра: True — это фон.

    Тянемся от рамки по пикселям, отличающимся от цвета угла не сильнее допуска.
    Сложный фон так не снять — он и не снимется: останутся непрозрачные куски,
    и в отчёте это будет видно по числу оставшихся пикселей.
    """
    height, width, _ = rgb.shape
    corner = np.stack(
        [
            rgb[0:5, 0:5].reshape(-1, 3),
            rgb[0:5, -5:].reshape(-1, 3),
            rgb[-5:, 0:5].reshape(-1, 3),
            rgb[-5:, -5:].reshape(-1, 3),
        ]
    ).mean(axis=(0, 1))

    background = np.zeros((height, width), dtype=bool)
    queue: deque = deque()
    for x in range(width):
        queue.append((0, x))
        queue.append((height - 1, x))
    for y in range(height):
        queue.append((y, 0))
        queue.append((y, width - 1))

    while queue:
        y, x = queue.popleft()
        if background[y, x]:
            continue
        if float(np.abs(rgb[y, x] - corner).max()) > tolerance:
            continue
        background[y, x] = True
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < height and 0 <= nx < width and not background[ny, nx]:
                queue.append((ny, nx))
    return background


def prepare(path: Path, tolerance: float) -> tuple[Image.Image, str]:
    """Открыть арт, привести альфу в порядок и рассказать, что сделано."""
    image = Image.open(path).convert("RGBA")
    alpha = np.array(image)[..., 3]

    if int(alpha.min()) == 255:
        # Альфы нет вовсе: фон зашит в картинку, заливаем от границ.
        background = flat_background(np.array(image)[..., :3].astype(np.float32), tolerance)
        filled = int(background.sum())
        result = image.copy()
        result.putalpha(Image.fromarray(np.where(background, 0, 255).astype(np.uint8)))
        return result, f"альфы в картинке нет: фон залит от границ, снято {filled} px"

    veil = int(((alpha > 0) & (alpha <= VEIL_MAX)).sum())
    solid = int(((alpha >= SOLID_MIN) & (alpha < 255)).sum())
    result = image.copy()
    result.putalpha(Image.fromarray(clean_alpha(alpha)))
    return result, f"вуаль в ноль: {veil} px, тело в сплошное: {solid} px"


def main() -> int:
    for name, tolerance in JOBS:
        path = RAW / name
        if not path.is_file():
            print(f"{name}: нет файла, пропускаю")
            continue
        result, note = prepare(path, tolerance)
        alpha = np.array(result)[..., 3]
        body = int((alpha > 0).sum())
        print(f"{name}: {result.width}x{result.height}, непрозрачных {body} px — {note}")
        result.save(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
