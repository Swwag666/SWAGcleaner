#!/usr/bin/env python
"""Измерение лица на рисунке помощницы.

Инструмент для тех случаев, когда приходит новый рисунок: перед тем как
рисовать на нём рот, надо точно знать, где лицо. Находится всё измерением,
а не на глаз:

    1. маска кожи  →  крупнейшая связная область в верхней части кадра = голова;
    2. внутри рамки головы ищутся «дыры» (всё, что не кожа): глаза, губы, ноздри;
    3. глаза — тёмные пятна, губы — красноватые;
    4. результат проверяется антропометрией: центр рта лежит на линии «вниз от
       середины глаз» в системе координат наклонённой головы, а расстояние
       «глаза → рот» составляет примерно 0.35–0.45 межзрачкового расстояния.

Инструмент печатает готовые константы в том виде, в котором они нужны
`tools/draw_states.py`, плюс рамку головы, размер и рамку содержимого кадра —
последнее нужно, чтобы новый кадр попал на такой же холст, как остальные,
иначе персонаж прыгает в размере при смене позы.

Запуск из корня проекта (пути — относительно assets/character):
    ./venv/Scripts/python.exe tools/measure_face.py raw/cleaner-idle.png
    ./venv/Scripts/python.exe tools/measure_face.py            # все из raw/
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "character" / "raw"


# ---------- маски ----------


def skin_mask(rgb: np.ndarray, alpha: np.ndarray, top_share: float = 0.5) -> np.ndarray:
    """Маска кожи в верхней части кадра (там, где голова)."""
    red, green, blue = rgb[..., 0].astype(np.int16), rgb[..., 1].astype(np.int16), rgb[..., 2].astype(np.int16)
    skin = (
        (alpha > 200)
        & (red > 95)
        & (red - green > 18)
        & (red - green < 105)
        & (green - blue > 5)
        & (red > blue + 30)
    )
    skin[int(rgb.shape[0] * (1.0 - top_share)) :, :] = False
    return skin


def blob(mask: np.ndarray, start: Tuple[int, int]) -> np.ndarray:
    """Связная область, содержащая точку (обход в ширину)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    start_y, start_x = start
    if not mask[start_y, start_x]:
        return seen
    queue = deque([(start_y, start_x)])
    seen[start_y, start_x] = True
    while queue:
        y, x = queue.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                queue.append((ny, nx))
    return seen


def components(mask: np.ndarray) -> List[np.ndarray]:
    """Все связные области маски."""
    seen = np.zeros_like(mask, dtype=bool)
    result: List[np.ndarray] = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        part = blob(mask, (int(y), int(x)))
        seen |= part
        result.append(part)
    return result


def head_frame(skin: np.ndarray) -> Tuple[int, int, int, int]:
    """Рамка головы: самая большая область кожи."""
    parts = components(skin)
    if not parts:
        raise SystemExit("Кожи не нашлось — проверь маску/F фон картинки.")
    biggest = max(parts, key=lambda part: int(part.sum()))
    ys, xs = np.nonzero(biggest)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1



def holes_in_head(skin: np.ndarray, box: Tuple[int, int, int, int]) -> List[Tuple[int, int, int, int, tuple]]:
    """Пятна не-кожи внутри рамки головы: (x0, y0, x1, y1, средний цвет)."""
    x0, y0, x1, y1 = box
    inner = np.zeros_like(skin)
    inner[y0:y1, x0:x1] = True
    # Крайние 6% рамки — шея и волосы, их «дырами» считать нельзя.
    margin_y = max(2, int((y1 - y0) * 0.06))
    margin_x = max(2, int((x1 - x0) * 0.06))
    inner[y0 : y0 + margin_y, :] = False
    inner[y0:y1, x0 : x0 + margin_x] = False
    inner[y0:y1, x1 - margin_x : x1] = False

    result = []
    for part in components(inner & ~skin):
        size = int(part.sum())
        if size < 40 or size > (x1 - x0) * (y1 - y0) * 0.25:
            continue
        ys, xs = np.nonzero(part)
        result.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, None))
    return result


def colours_at(rgb: np.ndarray, spots: Sequence[tuple]) -> List[Tuple[int, int, int, int, tuple]]:
    """Средний цвет каждого пятна."""
    out = []
    for x0, y0, x1, y1, _ in spots:
        patch = rgb[y0:y1, x0:x1].reshape(-1, 3)
        out.append((x0, y0, x1, y1, tuple(int(v) for v in np.round(patch.mean(axis=0)))))
    return out


# ---------- разбор ----------


def report(path: Path) -> Optional[Dict[str, tuple]]:
    image = Image.open(path).convert("RGBA")
    data = np.array(image)
    rgb, alpha = data[..., :3], data[..., 3]
    box = head_frame(skin_mask(rgb, alpha))

    ys, xs = np.nonzero(alpha > 16)
    content = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    print(f"\n=== {path.name}  {image.width}x{image.height}")
    print(f"содержимое кадра: x {content[0]}..{content[2]}, y {content[1]}..{content[3]}"
          f"  ({content[2] - content[0]}x{content[3] - content[1]})")
    print(f"рамка головы: x {box[0]}..{box[2]}, y {box[1]}..{box[3]}"
          f"  ({box[2] - box[0]}x{box[3] - box[1]})")

    spots = colours_at(rgb, holes_in_head(skin_mask(rgb, alpha), box))
    if not spots:
        print("«дыр» внутри головы не нашлось — проверь картинку вручную.")
        return None

    dark, red = [], []
    for x0, y0, x1, y1, colour in spots:
        width, height = x1 - x0, y1 - y0
        luminance = 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]
        kind = "?"
        if colour[0] - colour[1] > 25 and colour[0] > 110:
            red.append((x0, y0, x1, y1, colour))
            kind = "губы"
        elif luminance < 115 and 0.5 < width / max(height, 1) < 3.0:
            dark.append((x0, y0, x1, y1, colour))
            kind = "глаз?"
        print(f"  пятно ({x0},{y0})-({x1},{y1}) {width}x{height} цвет {colour} - {kind}")

    if len(dark) < 2 or not red:
        print("не хватило данных для полной разметки (нужно 2 глаза и губы).")
        return None

    dark.sort(key=lambda spot: spot[0])
    left, right = dark[0], dark[-1]
    eyes = (
        ((left[0] + left[2]) / 2.0, (left[1] + left[3]) / 2.0),
        ((right[0] + right[2]) / 2.0, (right[1] + right[3]) / 2.0),
    )
    lips = max(red, key=lambda spot: (spot[2] - spot[0]) * (spot[3] - spot[1]))
    mouth = ((lips[0] + lips[2]) / 2.0, (lips[1] + lips[3]) / 2.0)
    iod = float(np.hypot(eyes[1][0] - eyes[0][0], eyes[1][1] - eyes[0][1]))

    print("\nготовые константы для draw_states.py:")
    print(f'    "eyes": (({eyes[0][0]:.0f}, {eyes[0][1]:.0f}), ({eyes[1][0]:.0f}, {eyes[1][1]:.0f})),')
    print(f'    "eye_boxes": ({left[:4]}, {right[:4]}),')
    print(f'    "mouth": ({mouth[0]:.0f}, {mouth[1]:.0f}),')
    print(f'    "mouth_boxes": ({lips[:4]},),')
    print(f'    "lip_color": {lips[4]},')

    mid = ((eyes[0][0] + eyes[1][0]) / 2.0, (eyes[0][1] + eyes[1][1]) / 2.0)
    below = float(np.hypot(mouth[0] - mid[0], mouth[1] - mid[1]))
    print(f"\nпроверка антропометрии:")
    print(f"  межзрачковое расстояние {iod:.0f} px")
    print(f"  рот ниже середины глаз на {below:.0f} px = {below / iod:.3f} межзрачкового"
          f"  (норма 0.35–0.45)")
    head_width = box[2] - box[0]
    head_height = box[3] - box[1]
    for label, point in (("глаз A", eyes[0]), ("глаз B", eyes[1]), ("рот", mouth)):
        print(f"  {label}: доли от головы ({(point[0] - box[0]) / head_width:.3f},"
              f" {(point[1] - box[1]) / head_height:.3f})")
    return {"head": box, "content": content, "eyes": eyes, "mouth": mouth}


def main(argv: Sequence[str]) -> int:
    names = list(argv[1:]) or ["cleaner-idle.png"]
    for name in names:
        path = Path(name)
        if not path.is_file():
            path = RAW / Path(name).name
        if not path.is_file():
            print(f"нет файла: {name}")
            continue
        report(path)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv))
