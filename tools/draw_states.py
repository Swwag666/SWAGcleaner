#!/usr/bin/env python
"""Сборка состояний помощницы из живых рисунков.

Исходники лежат в assets/character/raw, готовые кадры — в assets/character.
Живые позы (idle, calm, think, panic) идут как есть, кадры речи дорисовываются:

    <поза>-talk-open.png — улыбка/крик затирается заливкой от границы, поверх
        рисуется приоткрытый рот (губы с бликом, зубы с тенью, язык);
    <поза>-talk-closed.png — сам рисунок (рот уже нарисован), кроме паники:
        у неё базовый кадр — крик, поэтому закрытый рот рисуется отдельно.

Лица находятся измерением по сетке поверх голов (константы *_FACE), а не
на глаз. Все кадры приводятся к одной высоте 360 — персонаж не прыгает
в размере при смене настроения.

Запуск из корня проекта:
    ./venv/Scripts/python.exe tools/draw_states.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "assets" / "character" / "raw"
OUT = ROOT / "assets" / "character"

# Ориентиры лица в сырых картинках, в пикселях исходника.
# Найдены измерением: маска кожи → дыры внутри лица → глаза и губы.
# Проверены антропометрией: центр рта лежит ровно на линии «вниз от середины
# глаз» в системе координат наклонённой головы (расхождение меньше 3 px).
SCAN_FACE = {
    "eyes": ((801, 254), (874, 282)),
    "eye_boxes": ((774, 244, 823, 267), (854, 274, 891, 292)),
    "mouth": (814, 334),
    "mouth_boxes": ((793, 329, 837, 344), (763, 329, 797, 343)),
    "lip_color": (177, 59, 63),
}
PANIC_FACE = {
    "eyes": ((825, 210), (935, 255)),
    "mouth": (815, 300),
    "mouth_boxes": ((740, 250, 890, 350),),
    "lip_color": (150, 60, 62),
}

# Живой арт выдоха (cleaner-calm.png, 1024x1536): глаза закрыты, рот — улыбка.
# Снято сеткой поверх лица.
CALM_FACE = {
    "eyes": ((475, 200), (590, 190)),
    "mouth": (560, 240),
    "mouth_boxes": ((495, 215, 625, 265),),
    "lip_color": (170, 80, 75),
}

# Живой арт раздумий (cleaner-think.png, 1024x1536): палец у подбородка под губами,
# поэтому рамка рта узкая — палец затирать нельзя.
THINK_FACE = {
    "eyes": ((430, 185), (560, 175)),
    "mouth": (515, 232),
    "mouth_boxes": ((465, 212, 560, 246),),
    "lip_color": (170, 80, 75),
}

# Лицо в новом спокойном арте (cleaner-idle.png, 1254x1254).
# Снято измерением по сетке поверх головы: глаза ~ (615,170) и (715,195),
# рот — линия ~ (570..690, 220..250) с центром (635,235). Оси лица считаем
# по линии глаз, как и для остальных поз.
IDLE_FACE = {
    "eyes": ((615, 170), (715, 195)),
    "mouth": (635, 235),
    "mouth_boxes": ((565, 213, 698, 262),),
    "lip_color": (170, 80, 75),
}

# На сколько градусов выпрямить корпус спокойной позы (против часовой).
CALM_TILT_DEGREES = 10.0

# Паника и сканирование смотрят в другую сторону: зеркалим их по горизонтали.
# Зеркалятся готовые кадры целиком (поза + оба кадра рта одним преобразованием),
# поэтому открытый и закрытый рот остаются ровно друг на друге.
MIRROR_SCAN = True
MIRROR_PANIC = True

# Масштаб и холст готовых картинок: scan.png — это сырьё 1254x1254,
# уменьшенное до высоты 360, то есть ровно в 0.287 раза. Новые состояния
# приводим к тому же масштабу, иначе персонаж прыгал бы в размере при смене
# настроения. Паника живёт в кадре своего исходника (1536x1024 → 540x360).
REF_HEIGHT = 360
SCAN_CANVAS = (360, 360)
PANIC_CANVAS = (540, 360)


# ---------- геометрия лица ----------


def face_axes(eyes: tuple) -> tuple:
    """Оси лица: R — вдоль линии глаз, D — вниз по лицу."""
    (x1, y1), (x2, y2) = eyes
    dx, dy = float(x2 - x1), float(y2 - y1)
    length = math.hypot(dx, dy) or 1.0
    right = (dx / length, dy / length)
    return right, (-right[1], right[0])


def face_frame(shape: tuple, axes: tuple):
    """Координаты пикселей в системе лица: (u — вдоль R, v — вдоль D)."""
    right, down = axes
    yy, xx = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    return xx, yy, right, down


def ellipse_mask(shape, center, axes, semi, feather=2.5) -> np.ndarray:
    """Мягкая маска эллипса, вытянутого вдоль осей лица."""
    xx, yy, right, down = face_frame(shape, axes)
    u = (xx - center[0]) * right[0] + (yy - center[1]) * right[1]
    v = (xx - center[0]) * down[0] + (yy - center[1]) * down[1]
    distance = np.sqrt((u / semi[0]) ** 2 + (v / semi[1]) ** 2)
    band = max(feather / min(semi), 1e-6)
    return np.clip((1.0 - distance) / band + 0.5, 0.0, 1.0)


def box_mask(shape, box, axes, grow: float = 2.0, feather: float = 2.5) -> np.ndarray:
    """Маска по прямоугольнику вокруг найденной впадины."""
    x0, y0, x1, y1 = box
    center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    semi = ((x1 - x0) / 2.0 + grow, (y1 - y0) / 2.0 + grow)
    return ellipse_mask(shape, center, axes, semi, feather)


def stroke_mask(shape, axes, origin, points, radius, feather=1.2) -> np.ndarray:
    """Маска «линии»: набор кружков вдоль кривой в системе лица.

    points — шаги вперёд по лицу (u), radius — толщина; радиус у концов
    меньше, поэтому линия получается живой, а не рубленой.
    """
    mask = np.zeros(shape[:2], dtype=np.float32)
    xx, yy, _, _ = face_frame(shape, axes)
    for u, v, r in points:
        center = (
            origin[0] + axes[0][0] * u + axes[1][0] * v,
            origin[1] + axes[0][1] * u + axes[1][1] * v,
        )
        local = np.exp(-(((xx - center[0]) ** 2 + (yy - center[1]) ** 2)) / (2.0 * (r * feather) ** 2))
        mask = np.maximum(mask, local)
    return np.clip(mask, 0.0, 1.0)


# ---------- работа с пикселями ----------


def fill_from_around(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Затереть область: каждый пиксель берёт цвет границы по близости.

    Простая заливка по обратному расстоянию: она сходится к гладкой заплатке,
    которая совпадает с краями, поэтому на коже не видно прямоугольников.
    """
    middle = mask > 0.5
    if not middle.any():
        return rgb
    ring = (mask > 0.02) & (mask < 0.5)
    ys, xs = np.nonzero(middle)
    y0, y1 = max(int(ys.min()) - 14, 0), min(int(ys.max()) + 15, rgb.shape[0])
    x0, x1 = max(int(xs.min()) - 14, 0), min(int(xs.max()) + 15, rgb.shape[1])

    sub = rgb[y0:y1, x0:x1].astype(np.float32)
    sub_mask = mask[y0:y1, x0:x1]
    sub_ring = ring[y0:y1, x0:x1]

    src_y, src_x = np.nonzero(sub_ring)
    dst_y, dst_x = np.nonzero(sub_mask > 0.5)
    if len(src_y) == 0 or len(dst_y) == 0:
        return rgb
    colors = sub[src_y, src_x]
    for i in range(len(dst_y)):
        distance = (src_x - dst_x[i]) ** 2 + (src_y - dst_y[i]) ** 2
        weight = 1.0 / (distance + 1.0)
        sub[dst_y[i], dst_x[i]] = (colors * weight[:, None]).sum(axis=0) / weight.sum()

    # Чуть размыть заплатку: цвет должен переходить в кожу плавно.
    blurred = np.array(
        Image.fromarray(sub.astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.4))
    ).astype(np.float32)
    weight = sub_mask[..., None]
    sub = sub * (1.0 - weight) + blurred * weight

    result = rgb.astype(np.float32).copy()
    patch = result[y0:y1, x0:x1]
    result[y0:y1, x0:x1] = patch * (1.0 - weight) + sub * weight
    return np.clip(result, 0, 255).astype(np.uint8)


def paint(rgb: np.ndarray, shapes) -> np.ndarray:
    """Наложить фигуры: список пар (маска, цвет)."""
    result = rgb.astype(np.float32).copy()
    for mask, color in shapes:
        weight = mask[..., None]
        result = result * (1.0 - weight) + np.array(color, dtype=np.float32) * weight
    return np.clip(result, 0, 255).astype(np.uint8)


def skin_tone(rgb: np.ndarray, center, radius: int = 26) -> tuple:
    """Средний цвет кожи рядом с точкой."""
    x, y = int(center[0]), int(center[1])
    patch = rgb[max(y - radius, 0) : y + radius, max(x - radius, 0) : x + radius]
    return tuple(np.round(patch.reshape(-1, patch.shape[-1])[:, :3].mean(axis=0)).astype(int))


# ---------- фигуры лица ----------


def eye_distance(eyes: tuple) -> float:
    """Расстояние между глазами: по нему считаем пропорции рта."""
    (x1, y1), (x2, y2) = eyes
    return math.hypot(x2 - x1, y2 - y1)


def open_mouth_shapes(shape, axes, mouth, iod: float, openness: float = 1.0) -> list:
    """Фигуры приоткрытого рта: губы с бликом, тёмное внутри, зубы с тенью, язык.

    Размер считаем от расстояния между глазами: у человека рот в ширину
    примерно две трети этой величины. Губы рисуются в полный размер (форму рта
    задают они), а внутренность масштабируется открытостью — так рот выглядит
    аккуратнее и в едином стиле на всех позах.
    """
    _, down = axes
    width = 0.44 * iod * (0.55 + 0.45 * openness)
    height = 0.27 * iod * (0.55 + 0.45 * openness)
    inner_height = height * openness
    lips = ellipse_mask(shape, mouth, axes, (width, height), feather=2.0)
    lip_light_center = (
        mouth[0] - down[0] * height * 0.52,
        mouth[1] - down[1] * height * 0.52,
    )
    lip_light = ellipse_mask(shape, lip_light_center, axes, (width * 0.62, height * 0.20), feather=1.2)
    inner_center = (mouth[0] + down[0] * inner_height * 0.20, mouth[1] + down[1] * inner_height * 0.20)
    inner = ellipse_mask(shape, inner_center, axes, (width * 0.80, inner_height * 0.72), feather=1.6)
    teeth_center = (mouth[0] - down[0] * inner_height * 0.30, mouth[1] - down[1] * inner_height * 0.30)
    teeth = ellipse_mask(shape, teeth_center, axes, (width * 0.66, inner_height * 0.20), feather=1.0)
    teeth_shade_center = (
        mouth[0] - down[0] * inner_height * 0.14,
        mouth[1] - down[1] * inner_height * 0.14,
    )
    teeth_shade = ellipse_mask(
        shape, teeth_shade_center, axes, (width * 0.62, inner_height * 0.07), feather=0.9
    )
    tongue_center = (mouth[0] + down[0] * inner_height * 0.44, mouth[1] + down[1] * inner_height * 0.44)
    tongue = ellipse_mask(shape, tongue_center, axes, (width * 0.50, inner_height * 0.28), feather=1.2)
    tongue_light_center = (
        mouth[0] + down[0] * inner_height * 0.38,
        mouth[1] + down[1] * inner_height * 0.38,
    )
    tongue_light = ellipse_mask(
        shape, tongue_light_center, axes, (width * 0.26, inner_height * 0.10), feather=1.0
    )
    return [
        (lips, (150, 60, 62)),
        (lip_light, (205, 110, 100)),
        (inner, (52, 19, 22)),
        (teeth, (238, 232, 224)),
        (teeth_shade, (196, 186, 178)),
        (tongue, (148, 68, 72)),
        (tongue_light, (202, 112, 116)),
    ]


# ---------- сборка состояний ----------


def content_bbox(image: Image.Image, threshold: int = 16) -> tuple:
    alpha = np.array(image)[..., 3] > threshold
    ys, xs = np.nonzero(alpha)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def resize_full(image: Image.Image, source_height: int, canvas: tuple) -> Image.Image:
    """Уменьшить весь кадр целиком: так же, как подготовлены scan.png и panic.png.

    Кадр исходника сохраняется полностью (вместе с полями), поэтому персонаж
    занимает в файле ровно то же место и тот же размер.
    """
    scale = REF_HEIGHT / source_height
    scaled = image.resize(
        (max(1, round(image.width * scale)), REF_HEIGHT), Image.LANCZOS
    )
    if scaled.size == canvas:
        return scaled
    out = Image.new("RGBA", canvas, (0, 0, 0, 0))
    out.paste(scaled, ((canvas[0] - scaled.width) // 2, 0), scaled)
    return out


def align_to_reference(
    image: Image.Image,
    source_height: int,
    source_bottom_margin: int,
    canvas: tuple = SCAN_CANVAS,
) -> Image.Image:
    """Поставить повёрнутую фигуру на такой же холст в том же масштабе.

    Оставлена для совместимости, новые позы идут через normalize_pose.
    """
    scale = REF_HEIGHT / source_height
    content = image.crop(content_bbox(image))
    scaled = content.resize(
        (max(1, round(content.width * scale)), max(1, round(content.height * scale))),
        Image.LANCZOS,
    )
    out = Image.new("RGBA", canvas, (0, 0, 0, 0))
    x = (canvas[0] - scaled.width) // 2
    y = canvas[1] - scaled.height - max(0, round(source_bottom_margin * scale))
    out.paste(scaled, (x, max(0, y)), scaled)
    return out


# Целевой рост фигуры на холсте: все позы стоят одинаково, иначе на экране
# одна выше другой (холсты разного формата масштабируются по минимуму).
TARGET_CONTENT_H = 340


def normalize_pose(
    frames: list, canvas: tuple, ref_box: tuple | None = None
) -> list:
    """Привести кадры позы к одному росту и поставить на общий холст.

    Рамка содержимого берётся по базовому кадру и применяется ко всем кадрам
    позы одинаково: иначе открытый рот прыгал бы относительно закрытого.
    Фигура стоит низом на одной линии — ноги не висят.
    """
    base = frames[0]
    # Рамку берём по плотным пикселям (порог 64): слабый ореол по краю иначе
    # входит в размер, и рост гуляет на несколько пикселей между позами.
    box = ref_box or content_bbox(base, threshold=64)
    content0 = base.crop(box)
    # Доводка в два прохода: после пересчёта полутона оседают, и замер по
    # плотным пикселям может дать на пару пикселей меньше — поправляем масштаб.
    scale = TARGET_CONTENT_H / content0.height
    for _ in range(2):
        probe = content0.resize(
            (max(1, round(content0.width * scale)), max(1, round(content0.height * scale))),
            Image.LANCZOS,
        )
        solid = np.array(probe)[..., 3] > 64
        ys, _xs = np.nonzero(solid)
        got = int(ys.max() - ys.min() + 1)
        if abs(got - TARGET_CONTENT_H) <= 1 or got <= 0:
            break
        scale *= TARGET_CONTENT_H / got
    out_frames = []
    for frame in frames:
        content = frame.crop(box)
        scaled = content.resize(
            (
                max(1, round(content.width * scale)),
                max(1, round(content.height * scale)),
            ),
            Image.LANCZOS,
        )
        out = Image.new("RGBA", canvas, (0, 0, 0, 0))
        x = (canvas[0] - scaled.width) // 2
        y = canvas[1] - scaled.height
        out.paste(scaled, (x, max(0, y)), scaled)
        out_frames.append(out)
    return out_frames


def scan_with_open_mouth(openness: float = 1.0) -> Image.Image:
    """Рабочая поза с приоткрытым ртом: гримасу затираем, рисуем говорящий рот."""
    source = Image.open(RAW / "cleaner-scan.png").convert("RGBA")
    rgb = np.array(source)[..., :3]
    axes = face_axes(SCAN_FACE["eyes"])
    iod = eye_distance(SCAN_FACE["eyes"])
    shape = rgb.shape

    mouth_mask = np.zeros(shape[:2], dtype=np.float32)
    for box in SCAN_FACE["mouth_boxes"]:
        mouth_mask = np.maximum(mouth_mask, box_mask(shape, box, axes, grow=5, feather=2.5))

    cleaned = fill_from_around(rgb, mouth_mask)
    drawn = paint(
        cleaned, open_mouth_shapes(shape, axes, SCAN_FACE["mouth"], iod, openness=openness)
    )
    result = np.array(source).copy()
    result[..., :3] = drawn
    return Image.fromarray(result)


def live_open_mouth(source_name: str, face: dict, openness: float) -> Image.Image:
    """Живой арт с приоткрытым ртом: улыбку затираем, рисуем речь поверх."""
    source = Image.open(RAW / source_name).convert("RGBA")
    rgb = np.array(source)[..., :3]
    axes = face_axes(face["eyes"])
    iod = eye_distance(face["eyes"])
    shape = rgb.shape

    mouth_mask = np.zeros(shape[:2], dtype=np.float32)
    for box in face["mouth_boxes"]:
        mouth_mask = np.maximum(mouth_mask, box_mask(shape, box, axes, grow=4, feather=2.5))

    cleaned = fill_from_around(rgb, mouth_mask)
    drawn = paint(cleaned, open_mouth_shapes(shape, axes, face["mouth"], iod, openness=openness))
    result = np.array(source).copy()
    result[..., :3] = drawn
    return Image.fromarray(result)


def panic_with_closed_mouth() -> Image.Image:
    source = Image.open(RAW / "cleaner-panic.png").convert("RGBA")
    axes = face_axes(PANIC_FACE["eyes"])
    shape = np.array(source).shape[:2]
    rgb = np.array(source)[..., :3]

    # Крик открыт широко: заливать его бессмысленно — заливка тянет тёмную
    # красноту по всему пятну. Вместо этого кладём непрозрачные сомкнутые губы
    # поверх крика: они целиком его перекрывают, затем щель и блик.
    iod = eye_distance(PANIC_FACE["eyes"])
    mouth = PANIC_FACE["mouth"]
    _, down = axes
    lips = ellipse_mask(shape, mouth, axes, (0.46 * iod, 0.34 * iod), feather=2.5)
    drawn = paint(rgb, [(lips, (150, 60, 62))])
    half = 0.30 * iod
    thickness = 0.055 * iod
    points = []
    for step in range(-int(half), int(half) + 1, 2):
        u = float(step)
        v = -0.05 * iod * (u / half) ** 2
        points.append((u, v, thickness * (1.0 - 0.4 * abs(u) / half)))
    slit = stroke_mask(shape, axes, mouth, points, radius=thickness, feather=0.9)
    glow_center = (mouth[0] + down[0] * 0.12 * iod, mouth[1] + down[1] * 0.12 * iod)
    glow = ellipse_mask(shape, glow_center, axes, (0.20 * iod, 0.06 * iod), feather=1.1)
    drawn = paint(
        drawn,
        [
            (slit, (108, 44, 48)),
            (glow, (205, 118, 104)),
        ],
    )
    result = np.array(source).copy()
    result[..., :3] = drawn
    return Image.fromarray(result)


def idle_with_open_mouth(openness: float = 0.6) -> Image.Image:
    """Новый спокойный арт с приоткрытым ртом: линию губ затираем, рисуем речь.

    Рот держим небольшим (0.6): единый стиль со сдержанной мимикой остальных поз.
    """
    source = Image.open(RAW / "cleaner-idle.png").convert("RGBA")
    rgb = np.array(source)[..., :3]
    axes = face_axes(IDLE_FACE["eyes"])
    iod = eye_distance(IDLE_FACE["eyes"])
    shape = rgb.shape

    mouth_mask = np.zeros(shape[:2], dtype=np.float32)
    for box in IDLE_FACE["mouth_boxes"]:
        mouth_mask = np.maximum(mouth_mask, box_mask(shape, box, axes, grow=4, feather=2.5))

    cleaned = fill_from_around(rgb, mouth_mask)
    drawn = paint(
        cleaned, open_mouth_shapes(shape, axes, IDLE_FACE["mouth"], iod, openness=openness)
    )
    result = np.array(source).copy()
    result[..., :3] = drawn
    return Image.fromarray(result)


def maybe_mirror(image: Image.Image, enabled: bool) -> Image.Image:
    """Отзеркалить кадр по горизонтали, если включён разворот позы."""
    if not enabled:
        return image
    return image.transpose(Image.FLIP_LEFT_RIGHT)


def report(path: Path, image: Image.Image) -> None:
    """Короткий отчёт по готовому файлу: размер и что вышло в области рта."""
    print(f"{path.name}: {image.width}x{image.height}")


def changed_share(before: Path, after: Image.Image) -> str:
    """Насколько новый кадр отличается от того, что лежал раньше."""
    if not before.exists():
        return "новый файл"
    old = np.array(Image.open(before).convert("RGBA"))[..., :3].astype(np.float32)
    new = np.array(after.convert("RGBA"))[..., :3].astype(np.float32)
    if old.shape != new.shape:
        return "был другого размера"
    diff = np.abs(old - new).sum(axis=2)
    return f"отличие от старого: {int((diff > 30).sum())} px, среднее {diff.mean():.2f}"


def save(path: Path, image: Image.Image) -> None:
    """Сохранить кадр и коротко отчитаться."""
    print(f"{path.name}: {image.width}x{image.height} | {changed_share(path, image)}")
    image.save(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    scan_source = Image.open(RAW / "cleaner-scan.png").convert("RGBA")
    panic_source = Image.open(RAW / "cleaner-panic.png").convert("RGBA")
    idle_source = Image.open(RAW / "cleaner-idle.png").convert("RGBA")
    calm_source = Image.open(RAW / "cleaner-calm.png").convert("RGBA")
    think_source = Image.open(RAW / "cleaner-think.png").convert("RGBA")

    # Все позы — один рост фигуры и низ на одной линии: на экране персонаж
    # не прыгает в размере при смене настроения. Кадры одной позы делят рамку
    # базового кадра: иначе открытый рот дрожал бы относительно закрытого.
    # Стоячие позы стоят на общем холсте 360x360 — масштаб на экране у них
    # получается одинаковый при любом размере окна.
    scan_frames = normalize_pose(
        [scan_source, scan_source, scan_with_open_mouth()], SCAN_CANVAS
    )
    scan_frames = [maybe_mirror(frame, MIRROR_SCAN) for frame in scan_frames]
    save(OUT / "scan.png", scan_frames[0])
    save(OUT / "scan-talk-closed.png", scan_frames[1])
    save(OUT / "scan-talk-open.png", scan_frames[2])

    # Живой выдох: закрытый кадр = сам рисунок (улыбка уже нарисована),
    # открытый дорисован тем же конвейером.
    calm_frames = normalize_pose(
        [
            calm_source,
            calm_source,
            live_open_mouth("cleaner-calm.png", CALM_FACE, openness=0.7),
        ],
        SCAN_CANVAS,
    )
    save(OUT / "calm.png", calm_frames[0])
    save(OUT / "calm-talk-closed.png", calm_frames[1])
    save(OUT / "calm-talk-open.png", calm_frames[2])

    # Живые раздумья: палец у подбородка, поэтому рот узкий и маленький.
    think_frames = normalize_pose(
        [
            think_source,
            think_source,
            live_open_mouth("cleaner-think.png", THINK_FACE, openness=0.55),
        ],
        SCAN_CANVAS,
    )
    save(OUT / "think.png", think_frames[0])
    save(OUT / "think-talk-closed.png", think_frames[1])
    save(OUT / "think-talk-open.png", think_frames[2])

    # Паника на коленях — поза широкая, поэтому холст шире, но рост фигуры
    # тот же: на экране она ниже только из-за ширины (так и задумано позой).
    panic_frames = normalize_pose(
        [panic_source, panic_source, panic_with_closed_mouth()], PANIC_CANVAS
    )
    panic_frames = [maybe_mirror(frame, MIRROR_PANIC) for frame in panic_frames]
    save(OUT / "panic.png", panic_frames[0])
    save(OUT / "panic-talk-open.png", panic_frames[1])
    save(OUT / "panic-talk-closed.png", panic_frames[2])

    # Новое спокойствие, которое встречает пользователя: живой рисунок idle.
    # Закрытый кадр = сама поза (рот-линия уже нарисован), открытый дорисован.
    idle_frames = normalize_pose(
        [idle_source, idle_source, idle_with_open_mouth()], SCAN_CANVAS
    )
    save(OUT / "idle.png", idle_frames[0])
    save(OUT / "idle-talk-closed.png", idle_frames[1])
    save(OUT / "idle-talk-open.png", idle_frames[2])

    print("\nготово. Приложение подхватит файлы само: настроение берёт позу")
    print("по имени, а кадры речи — «<поза>-talk-open/closed.png».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
