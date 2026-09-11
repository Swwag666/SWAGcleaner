#!/usr/bin/env python
"""Дорисовка состояний помощницы из имеющихся картинок.

Персонаж пришёл двумя рисунками: она ищет мусор с лупой и она паникует.
Остальные состояния делаются из них:

    calm.png            — спокойная поза: корпус выпрямлен, лицо расслаблено;
    scan-talk-open.png  — рабочая поза с приоткрытым ртом (кадр речи);
    calm-talk-open.png  — спокойная поза с приоткрытым ртом;
    panic-talk-closed.png — паника с закрытым ртом.

Как это делается без нейросетей, честно: лицо находится измерением (маска
кожи и «дыры» внутри неё — глаза и губы), затем нужные места затираются
заливкой от границы, а поверх рисуются расслабленные глаза и рот. Корпус
спокойной позы поворачивается на несколько градусов — она перестаёт
наклоняться вперёд.

Это заготовка до отдельных рисунков: когда появятся настоящие состояния,
положите их рядом с теми же именами — приложение подхватит их само, а этот
скрипт можно удалить.

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
    "eyes": ((859, 209), (930, 233)),
    "mouth": (860, 303),
    "mouth_boxes": ((813, 272, 892, 336),),
    "lip_color": (150, 60, 62),
}

# На сколько градусов выпрямить корпус спокойной позы (против часовой).
CALM_TILT_DEGREES = 10.0

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
    """Фигуры приоткрытого рта: губы, тень внутри, зубы, язык.

    Размер считаем от расстояния между глазами: у человека рот в ширину
    примерно две трети этой величины, а приоткрытый при речи — на треть
    меньше раскрытый, чем у крика.
    """
    _, down = axes
    width = 0.44 * iod * openness
    height = 0.27 * iod * openness
    lips = ellipse_mask(shape, mouth, axes, (width, height), feather=2.0)
    inner_center = (mouth[0] + down[0] * height * 0.18, mouth[1] + down[1] * height * 0.18)
    inner = ellipse_mask(shape, inner_center, axes, (width * 0.82, height * 0.74), feather=1.6)
    teeth_center = (mouth[0] - down[0] * height * 0.34, mouth[1] - down[1] * height * 0.34)
    teeth = ellipse_mask(shape, teeth_center, axes, (width * 0.68, height * 0.16), feather=1.0)
    tongue_center = (mouth[0] + down[0] * height * 0.42, mouth[1] + down[1] * height * 0.42)
    tongue = ellipse_mask(shape, tongue_center, axes, (width * 0.52, height * 0.26), feather=1.2)
    return [
        (lips, (150, 60, 62)),
        (inner, (58, 21, 24)),
        (teeth, (214, 204, 196)),
        (tongue, (146, 66, 70)),
    ]


def relaxed_mouth_shapes(shape, axes, mouth, iod: float) -> list:
    """Спокойный рот: мягкая линия с чуть поднятыми уголками."""
    half = 0.26 * iod
    thickness = 0.07 * iod
    points = []
    for step in range(-int(half), int(half) + 1, 2):
        u = float(step)
        v = -0.045 * iod * (u / half) ** 2    # уголки чуть выше середины
        points.append((u, v, thickness * (1.0 - 0.45 * abs(u) / half)))
    line = stroke_mask(shape, axes, mouth, points, radius=thickness, feather=0.9)
    lower = []
    for step in range(-int(half * 0.66), int(half * 0.66) + 1, 2):
        u = float(step)
        v = 0.10 * iod - 0.03 * iod * (u / (half * 0.66)) ** 2
        lower.append((u, v, thickness * 0.55))
    shadow = stroke_mask(shape, axes, mouth, lower, radius=thickness * 0.55, feather=1.1)
    return [(line, (146, 64, 60)), (shadow, (196, 118, 92))]


def calm_eye_shapes(shape, axes, eye_boxes) -> list:
    """Расслабленные глаза: веко прикрывает верх, сверху тонкая линия ресниц."""
    shapes = []
    for box in eye_boxes:
        x0, y0, x1, y1 = box
        center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        width = (x1 - x0) / 2.0
        height = (y1 - y0) / 2.0
        lid_center = (
            center[0] + axes[1][0] * (-height * 0.55),
            center[1] + axes[1][1] * (-height * 0.55),
        )
        lid = ellipse_mask(shape, lid_center, axes, (width + 2.5, height * 0.95), feather=2.0)
        shapes.append((lid, None))  # None — «затереть»: заливка от границы
    return shapes


def lash_shapes(shape, axes, eye_boxes) -> list:
    """Тонкая линия века по нижнему краю прикрытого глаза."""
    shapes = []
    for box in eye_boxes:
        x0, y0, x1, y1 = box
        center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        width = (x1 - x0) / 2.0
        height = (y1 - y0) / 2.0
        points = []
        radius = max(2.0, width * 0.12)
        for step in range(-int(width), int(width) + 1, 2):
            u = float(step)
            v = -height * 0.1 + 2.2 * (u / width) ** 2
            points.append((u, v, radius))
        shapes.append(
            (stroke_mask(shape, axes, center, points, radius=radius, feather=1.0), (74, 47, 44))
        )
    return shapes


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

    Кадр после поворота уже другой, поэтому берём фигуру по содержимому,
    уменьшаем её тем же множителем, что и исходники, и ставим на холст так,
    чтобы низ фигуры оказался на той же высоте, что и раньше: иначе она
    «висит» над панелью реплики.
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


def rotate_pose(image: Image.Image) -> Image.Image:
    """Выпрямить корпус: поворот вокруг низа фигуры, чтобы она осталась на месте."""
    padded = Image.new(
        "RGBA", (image.width + 400, image.height + 400), (0, 0, 0, 0)
    )
    padded.paste(image, (200, 200))
    return padded.rotate(
        CALM_TILT_DEGREES,
        resample=Image.BICUBIC,
        center=(padded.width / 2, padded.height - 200),
    )


def calm_from_scan() -> tuple:
    """Спокойная поза: расслабить лицо и выпрямить корпус."""
    source = Image.open(RAW / "cleaner-scan.png").convert("RGBA")
    axes = face_axes(SCAN_FACE["eyes"])
    iod = eye_distance(SCAN_FACE["eyes"])
    shape = (source.height, source.width)
    rgb = np.array(source)[..., :3]
    alpha = np.array(source)[..., 3]

    # 1. Лицо: затереть гримасу и прикрыть глаза.
    mouth_mask = np.zeros(shape[:2], dtype=np.float32)
    for box in SCAN_FACE["mouth_boxes"]:
        mouth_mask = np.maximum(mouth_mask, box_mask(shape, box, axes, grow=6, feather=3.0))
    smoothed = fill_from_around(rgb, mouth_mask)

    for mask, _ in calm_eye_shapes(shape, axes, SCAN_FACE["eye_boxes"]):
        smoothed = fill_from_around(smoothed, mask)

    # 2. Поверх — расслабленный рот и веки.
    smoothed = paint(smoothed, relaxed_mouth_shapes(shape, axes, SCAN_FACE["mouth"], iod))
    smoothed = paint(smoothed, lash_shapes(shape, axes, SCAN_FACE["eye_boxes"]))

    calm_base = np.dstack([smoothed, alpha])
    calm_image = Image.fromarray(calm_base, "RGBA")

    # 3. Выпрямить корпус.
    calm = rotate_pose(calm_image)

    # 4. Тот же кадр с приоткрытым ртом: рисуем до поворота и повторяем путь.
    opened = paint(
        smoothed, open_mouth_shapes(shape, axes, SCAN_FACE["mouth"], iod, openness=0.85)
    )
    opened_image = Image.fromarray(np.dstack([opened, alpha]), "RGBA")
    return calm, rotate_pose(opened_image)


def panic_with_closed_mouth() -> Image.Image:
    source = Image.open(RAW / "cleaner-panic.png").convert("RGBA")
    axes = face_axes(PANIC_FACE["eyes"])
    shape = np.array(source).shape[:2]
    rgb = np.array(source)[..., :3]

    iod = eye_distance(PANIC_FACE["eyes"])
    mouth_mask = np.zeros(shape, dtype=np.float32)
    for box in PANIC_FACE["mouth_boxes"]:
        mouth_mask = np.maximum(mouth_mask, box_mask(shape, box, axes, grow=4, feather=3.0))
    # Крик открыт широко: затираем впадину и рисуем спокойно закрытый рот.
    cleaned = fill_from_around(rgb, mouth_mask)
    half = 0.32 * iod
    thickness = 0.085 * iod
    points = []
    for step in range(-int(half), int(half) + 1, 2):
        u = float(step)
        v = -0.06 * iod * (u / half) ** 2
        points.append((u, v, thickness * (1.0 - 0.4 * abs(u) / half)))
    lower = []
    for step in range(-int(half * 0.7), int(half * 0.7) + 1, 2):
        u = float(step)
        v = 0.13 * iod - 0.04 * iod * (u / (half * 0.7)) ** 2
        lower.append((u, v, thickness * 0.5))
    shapes = [
        (stroke_mask(shape, axes, PANIC_FACE["mouth"], points, radius=thickness, feather=0.9),
         (128, 52, 56)),
        (stroke_mask(shape, axes, PANIC_FACE["mouth"], lower, radius=thickness * 0.5, feather=1.1),
         (206, 126, 96)),
    ]
    drawn = paint(cleaned, shapes)
    result = np.array(source).copy()
    result[..., :3] = drawn
    return Image.fromarray(result)


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
    _, _, _, scan_bottom = content_bbox(scan_source)
    scan_bottom_margin = scan_source.height - scan_bottom

    # Все кадры проходят один и тот же пересчёт размера. Если открытый кадр
    # уменьшить отдельно от закрытого, при 9 кадрах в секунду фигура
    # подрагивала бы: разница в пересчёте видна глазом на краях.
    save(OUT / "scan.png", resize_full(scan_source, scan_source.height, SCAN_CANVAS))
    save(
        OUT / "scan-talk-closed.png",
        resize_full(scan_source, scan_source.height, SCAN_CANVAS),
    )
    save(
        OUT / "scan-talk-open.png",
        resize_full(scan_with_open_mouth(), scan_source.height, SCAN_CANVAS),
    )

    calm, calm_open = calm_from_scan()
    save(
        OUT / "calm.png",
        align_to_reference(calm, scan_source.height, scan_bottom_margin),
    )
    save(
        OUT / "calm-talk-closed.png",
        align_to_reference(calm, scan_source.height, scan_bottom_margin),
    )
    save(
        OUT / "calm-talk-open.png",
        align_to_reference(calm_open, scan_source.height, scan_bottom_margin),
    )

    save(
        OUT / "panic.png",
        resize_full(panic_source, panic_source.height, PANIC_CANVAS),
    )
    save(
        OUT / "panic-talk-open.png",
        resize_full(panic_source, panic_source.height, PANIC_CANVAS),
    )
    save(
        OUT / "panic-talk-closed.png",
        resize_full(panic_with_closed_mouth(), panic_source.height, PANIC_CANVAS),
    )

    print("\nготово. Приложение подхватит файлы само: настроение берёт позу")
    print("по имени, а кадры речи — «<поза>-talk-open/closed.png».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
