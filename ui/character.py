"""Персонаж-комментатор в стиле визуальной новеллы.

Справа стоит помощница, снизу — панель реплики: текст печатается по буквам,
клик пропускает печать и листает очередь. Настроение меняет позу: осматривается,
думает, выдыхает от облегчения или паникует от количества мусора.

Картинки ищутся в assets/character по имени настроения, причём с откатом:
если отдельного файла для настроения нет, берётся ближайший подходящий.
Поэтому набор можно дополнять постепенно, ничего не ломая — достаточно
положить рядом файл с нужным именем.

Кадры речи (talk-closed.png / talk-open.png) необязательны. Если они лежат
рядом, печать переключает их и рот открывается; если нет — персонаж просто
покачивается в такт печати.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QElapsedTimer, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ui.context import ctx

# Картинки персонажа лежат рядом с проектом: assets/character.
# В «числовой» теме свой протагонист: assets/character/num.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "character"


def _dir_for_theme(theme: Optional[str] = None) -> Path:
    """Каталог артов под тему: числовая -> num, иначе базовая Клинни."""
    theme = theme if theme is not None else ctx().theme()
    return ASSETS_DIR / "num" if theme == "num" else ASSETS_DIR

# Настроения персонажа. Порядок важен: по нему листается демо-режим.
MOODS: Tuple[str, ...] = ("idle", "scan", "think", "calm", "panic")

# Что искать для каждого настроения: первое найденное побеждает.
# Лупа — только у поиска мусора: покой, раздумья и встреча берут позы без лупы
# (idle), поэтому на всех вкладках в покое она стоит спокойно, а поза с лупой
# появляется ровно на время сканирования. think.png нет — раздумья откатываются
# на idle, а не на calm (у calm в руках лупа).
MOOD_FILES: Dict[str, Tuple[str, ...]] = {
    "idle": ("idle.png", "calm.png"),
    "scan": ("scan.png",),
    "think": ("think.png", "idle.png", "calm.png"),
    "calm": ("calm.png", "scan.png"),
    "panic": ("panic.png",),
}

# Кадры речи ищем у той же позы: «calm-talk-open.png» и «calm-talk-closed.png».
# Если у позы своих кадров нет, смотрим общие «talk-*.png».
TALK_SUFFIXES: Tuple[str, ...] = ("closed", "half", "open")

# Частота переключения кадров речи, кадров в секунду.
TALK_FPS = 9.0


def pose_path(mood: str, theme: Optional[str] = None) -> Optional[Path]:
    """Файл картинки для настроения (с откатом) или None, если ничего нет."""
    directory = _dir_for_theme(theme)
    for name in MOOD_FILES.get(mood, ()):
        path = directory / name
        if path.is_file():
            return path
    return None


def talk_frame_paths(mood: str, theme: Optional[str] = None) -> List[Path]:
    """Кадры речи для настроения: сначала свои у позы, потом общие.

    Своих кадров нужно хотя бы два: один кадр — это не анимация, а статика.
    """
    directory = _dir_for_theme(theme)
    pose = pose_path(mood, theme)
    if pose is not None:
        own = [directory / f"{pose.stem}-talk-{suffix}.png" for suffix in TALK_SUFFIXES]
        own = [path for path in own if path.is_file()]
        if len(own) >= 2:
            return own
    generic = [directory / f"talk-{suffix}.png" for suffix in TALK_SUFFIXES]
    return [path for path in generic if path.is_file()]


def demo_moods(theme: Optional[str] = None) -> List[str]:
    """Настроения со своей картинкой (не откат на чужую).

    Нужно для демонстрации поз по F2: показывать по кругу одно и то же
    изображение трижды подряд смысла нет. Как только появится отдельный арт
    (idle.png, think.png), он попадёт в список сам — код менять не придётся.
    """
    directory = _dir_for_theme(theme)
    return [mood for mood in MOODS if (directory / f"{mood}.png").is_file()]


def available_moods(theme: Optional[str] = None) -> Dict[str, Optional[Path]]:
    """Какие настроения реально есть в сборке — для самопроверки и тестов."""
    return {mood: pose_path(mood, theme) for mood in MOODS}


def available_talk_frames(theme: Optional[str] = None) -> Dict[str, int]:
    """Сколько кадров речи нашлось для каждого настроения."""
    return {mood: len(talk_frame_paths(mood, theme)) for mood in MOODS}


class Mascot(QWidget):
    """Спрайт персонажа: рисует позу и чуть-чуть двигается, чтобы не быть фото."""

    # Настроение сменилось: окно слушает, чтобы обновить оверлей Клинни
    # на экране, когда в свёрнутом режиме ей тесно в колонке.
    moodChanged = Signal(str)

    TICK_MS = 70
    # Выезд снизу при смене вкладки: медленный подъём в стиле ВН. Тикает своим
    # таймером на 60 кадрах в секунду — на общем тике (70 мс) подъём за 0.38 с
    # давал пять рывков, отсюда и «быстро и не плавно».
    ENTER_DURATION_S = 0.9
    ENTER_FRAME_MS = 16
    ENTER_LIFT_PX = 110.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mood = "idle"
        self._speaking = False
        self._phase = 0.0
        # Выезд снизу: None — стоит на месте, иначе 0..1 (доля подъёма).
        self._enter_t: float | None = None
        self._enter_clock = QElapsedTimer()
        self._enter_timer = QTimer(self)
        self._enter_timer.setInterval(self.ENTER_FRAME_MS)
        self._enter_timer.timeout.connect(self._enter_tick)
        self._cache: Dict[str, Optional[QPixmap]] = {}
        self._talk_frames: Optional[List[QPixmap]] = None
        self._scaled_cache: Dict[Tuple[int, int, int], QPixmap] = {}
        # Минимальная ширина нулевая: колонку персонажа сворачивает окно,
        # когда помощницу скрывают.
        self.setMinimumSize(QSize(0, 160))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ---------- данные ----------

    def mood(self) -> str:
        return self._mood

    def set_mood(self, mood: str) -> None:
        """Сменить настроение (неизвестное приводится к спокойному ожиданию)."""
        normalised = mood if mood in MOODS else "idle"
        if normalised == self._mood:
            return
        self._mood = normalised
        # Кадры речи у каждой позы свои, поэтому кэш сбрасываем.
        self._talk_frames = None
        self.update()
        self.moodChanged.emit(normalised)

    def is_speaking(self) -> bool:
        return self._speaking

    def set_speaking(self, speaking: bool) -> None:
        """Включить анимацию речи — её включает панель реплики во время печати."""
        if speaking == self._speaking:
            return
        self._speaking = speaking
        self._phase = 0.0
        self.update()

    def has_talk_frames(self) -> bool:
        """Нарисованы ли отдельные кадры рта (иначе речь — только покачивание)."""
        self._load_talk_frames()
        return bool(self._talk_frames)

    def clear_cache(self) -> None:
        """Забыть загруженные картинки (после замены файлов на диске)."""
        self._cache.clear()
        self._talk_frames = None
        self._scaled_cache.clear()

    def _load_talk_frames(self) -> None:
        if self._talk_frames is not None:
            return
        frames: List[QPixmap] = []
        for path in talk_frame_paths(self._mood):
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                frames.append(pixmap)
        self._talk_frames = frames

    def _pose(self) -> Optional[QPixmap]:
        if self._mood not in self._cache:
            pixmap: Optional[QPixmap] = None
            path = pose_path(self._mood)
            if path is not None:
                candidate = QPixmap(str(path))
                pixmap = candidate if not candidate.isNull() else None
            self._cache[self._mood] = pixmap
        return self._cache[self._mood]

    def _scaled_pixmap(self, pixmap: QPixmap, width: int, height: int) -> QPixmap:
        """Ужатая копия позы под текущий размер окна (кешируется).

        Детальный арт (тысяча пикселей) сужается до нескольких сотен: честнее
        один раз ужать сглаживанием, чем каждый кадр тянуть билинейно.
        """
        key = (pixmap.cacheKey(), width, height)
        cached = self._scaled_cache.get(key)
        if cached is not None:
            return cached
        if len(self._scaled_cache) > 32:
            self._scaled_cache.clear()
        scaled = pixmap.scaled(
            width, height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scaled_cache[key] = scaled
        return scaled

    # ---------- анимация ----------

    def enter_from_below(self) -> None:
        """Начать выезд снизу (как появление в визуальной новелле)."""
        self._enter_t = 0.0
        self._enter_clock.start()
        if not self._enter_timer.isActive():
            self._enter_timer.start()
        self.update()

    def is_entering(self) -> bool:
        """Идёт ли сейчас выезд снизу."""
        return self._enter_t is not None

    def _tick(self) -> None:
        self._phase += self.TICK_MS / 1000.0
        self.update()

    def _enter_tick(self) -> None:
        """Кадр выезда: доля подъёма считается по wall-clock, а не по тикам."""
        if self._enter_t is None:
            self._enter_timer.stop()
            return
        elapsed = self._enter_clock.elapsed() / 1000.0
        progress = elapsed / self.ENTER_DURATION_S
        if progress >= 1.0:
            self._enter_t = None
            self._enter_timer.stop()
        else:
            self._enter_t = progress
        self.update()

    def _motion(self) -> Tuple[float, float, float]:
        """Смещение по X, по Y и масштаб для текущего кадра."""
        t = self._phase
        if self._speaking and self.has_talk_frames():
            # Речь как раньше: рот переключается кадрами, телу достаточно
            # лёгкого покачивания — единый стиль со спокойным дыханием.
            offset_x, offset_y, zoom = 0.0, math.sin(t * 6.0) * 1.5, 1.0
        elif self._speaking:
            # Кадров рта нет: говорим всем корпусом — кивок и дыхание.
            offset_x, offset_y, zoom = (
                0.0,
                math.sin(t * 6.0) * 2.5,
                1.0 + 0.008 * math.sin(t * 12.0),
            )
        elif self._mood == "panic":
            offset_x, offset_y, zoom = (
                math.sin(t * 14.0) * 2.5,
                math.sin(t * 9.0) * 1.5,
                1.0,
            )
        elif self._mood == "scan":
            offset_x, offset_y, zoom = (
                math.sin(t * 1.2) * 3.0,
                math.sin(t * 2.4) * 1.0,
                1.0,
            )
        elif self._mood in ("calm", "idle"):
            # Выдох: медленно опускается и чуть сжимается, потом обратно.
            exhale = (1.0 - math.cos(t * 1.05)) / 2.0
            offset_x, offset_y, zoom = 0.0, exhale * 2.0, 1.0 - 0.012 * exhale
        else:
            offset_x, offset_y, zoom = 0.0, 0.0, 1.0
        if self._enter_t is not None:
            # Выезд снизу: старт на ENTER_LIFT_PX ниже, финиш ровно на месте.
            progress = max(0.0, min(1.0, self._enter_t))
            ease = 1.0 - (1.0 - progress) ** 3
            offset_y += (1.0 - ease) * self.ENTER_LIFT_PX
        return offset_x, offset_y, zoom

    def _talk_index(self) -> int:
        self._load_talk_frames()
        frames = self._talk_frames or []
        if len(frames) < 2:
            return 0
        return int(self._phase * TALK_FPS) % len(frames)

    # ---------- отрисовка ----------

    def paintEvent(self, event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        pixmap = self._pose()
        if self._speaking and self.has_talk_frames():
            frames = self._talk_frames or []
            if frames:
                pixmap = frames[self._talk_index()]
        if pixmap is None or pixmap.isNull():
            painter.end()
            return

        # Пол под ногами: мягкая тень, чтобы персонаж не парил над панелью.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 64))
        shadow_w = max(40, int(self.width() * 0.46))
        shadow_h = max(6, int(shadow_w * 0.14))
        painter.drawEllipse(QRect(
            (self.width() - shadow_w) // 2,
            self.height() - shadow_h - 2,
            shadow_w,
            shadow_h,
        ))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        offset_x, offset_y, zoom = self._motion()
        base = min(self.width() / pixmap.width(), self.height() / pixmap.height())
        base_w = max(1, int(pixmap.width() * base))
        base_h = max(1, int(pixmap.height() * base))
        pixmap = self._scaled_pixmap(pixmap, base_w, base_h)
        width = max(1, int(pixmap.width() * zoom))
        height = max(1, int(pixmap.height() * zoom))
        x = int((self.width() - width) / 2 + offset_x)
        # Ноги стоят на нижней границе: персонаж «стоит» на панели реплики.
        y = int(self.height() - height + offset_y)
        painter.drawPixmap(QRect(x, y, width, height), pixmap)
        if self._mood == "panic" and ctx().motion() == "playful":
            self._draw_glitch(painter, x, y, width, height)
        painter.end()

    def _draw_glitch(self, painter: QPainter, x: int, y: int,
                     width: int, height: int) -> None:
        """CRT-сбой поверх спрайта: рваные полосы при панике.

        Только в режиме «игриво»: в «сдержанно» персонаж просто дрожит.
        """
        from ui.theme import palette

        accent = QColor(palette(ctx().theme(), ctx().accent()).get(
            "accent", "#4d8dff"))
        accent.setAlpha(80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        band = max(2, height // 90)
        for i in range(3):
            pos = (int(self._phase * 53) + i * (height // 3)) % max(1, height - band)
            painter.drawRect(x, y + pos, width, band)
        tear = max(2, height // 60)
        at = (int(self._phase * 31) + height // 2) % max(1, height - tear)
        painter.drawRect(x + int(math.sin(self._phase * 7) * 6), y + at,
                         max(0, width - 8), tear)

    # ---------- жизненный цикл ----------

    def showEvent(self, event) -> None:  # noqa: ANN001
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._timer.stop()
        super().hideEvent(event)


class SpeechBox(QFrame):
    """Панель реплики: имя, текст с печатью по буквам и клик «дальше»."""

    advanced = Signal()
    typingChanged = Signal(bool)
    # Новая реплика началась: окно слушает это, чтобы в свёрнутом режиме
    # продублировать текст оверлеем поверх экрана.
    said = Signal(str)

    TYPE_MS = 24
    BLINK_MS = 480

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("speechBox")
        self.setMinimumHeight(104)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._full = ""
        self._shown = 0
        self._typing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 16, 12)
        root.setSpacing(4)

        self._name = QLabel(self)
        self._name.setObjectName("speechName")
        root.addWidget(self._name)

        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(8)
        self._text = QLabel(self)
        self._text.setObjectName("speechText")
        self._text.setWordWrap(True)
        self._text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        text_row.addWidget(self._text, 1)

        self._caret = QLabel("▾", self)
        self._caret.setObjectName("speechCaret")
        self._caret.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        self._caret.setVisible(False)
        text_row.addWidget(self._caret, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(text_row, 1)

        self._type_timer = QTimer(self)
        self._type_timer.setInterval(self.TYPE_MS)
        self._type_timer.timeout.connect(self._type_tick)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(self.BLINK_MS)
        self._blink_timer.timeout.connect(self._blink_tick)

        self.retranslate()

    # ---------- состояние ----------

    def full_text(self) -> str:
        return self._full

    def shown_text(self) -> str:
        return self._full[: self._shown]

    def is_typing(self) -> bool:
        return self._typing

    def say(self, text: str) -> None:
        """Показать реплику заново: текст появляется по буквам."""
        self._full = text or ""
        self._shown = 0
        self._blink_timer.stop()
        self._caret.setVisible(False)
        if self._full:
            self.said.emit(self._full)
        self._text.setText("")
        if not self._full:
            self._set_typing(False)
            return
        self._set_typing(True)
        self._type_timer.start()

    def finish_typing(self) -> None:
        """Дописать реплику до конца мгновенно."""
        if not self._typing:
            return
        self._shown = len(self._full)
        self._text.setText(self._full)
        self._set_typing(False)

    def advance(self) -> None:
        """Клик по панели: дописать реплику или попросить следующую."""
        from ui import sounds as _sounds

        _sounds.play("click")
        if self._typing:
            self.finish_typing()
        else:
            self.advanced.emit()

    # ---------- внутреннее ----------

    def _set_typing(self, typing: bool) -> None:
        if typing == self._typing:
            return
        self._typing = typing
        if typing:
            self._type_timer.start()
        else:
            self._type_timer.stop()
            if self._full:
                self._caret.setVisible(True)
                self._blink_timer.start()
        self.typingChanged.emit(typing)

    def _type_tick(self) -> None:
        self._shown += 1
        if self._shown >= len(self._full):
            self._shown = len(self._full)
            self._text.setText(self._full)
            self._set_typing(False)
            return
        self._text.setText(self._full[: self._shown])

    def _blink_tick(self) -> None:
        if self._typing or not self._full:
            self._blink_timer.stop()
            self._caret.setVisible(False)
            return
        self._caret.setVisible(not self._caret.isVisible())

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        self.advance()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: ANN001
        """Хвостик реплики: маленький акцентный язычок на верхней кромке.

        Он стоит справа — туда, где над панелью живёт персонаж, и связывает
        её реплику с самой Клинни, а не с пустой полосой внизу окна.
        """
        super().paintEvent(event)
        from ui import theme as _theme

        accent = QColor(_theme.palette(ctx().theme(), ctx().accent()).get("accent", "#4d8dff"))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        x = self.width() - 24
        painter.drawPolygon(QPolygon([
            QPoint(x - 7, 7),
            QPoint(x + 7, 7),
            QPoint(x, 0),
        ]))
        painter.end()

    def retranslate(self) -> None:
        """Перевести подписи панели (имя персонажа, подсказка)."""
        name = ctx().tr("character.name")
        self._name.setText(name)
        self.setToolTip(f"{name}: {ctx().tr('character.hint')}")


class Assistant(QObject):
    """Связка «персонаж + панель реплики»: очередь реплик и смена настроения.

    Ядру и страницам не нужно знать про виджеты: достаточно вызвать
    say() с текстом и настроением, а очередь с несколькими репликами
    пролистывается кликом.
    """

    def __init__(self, mascot: Mascot, speech: SpeechBox, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mascot = mascot
        self._speech = speech
        self._queue: List[Tuple[Optional[str], str]] = []
        speech.typingChanged.connect(mascot.set_speaking)
        speech.advanced.connect(self.next)

    def mascot(self) -> Mascot:
        return self._mascot

    def speech(self) -> SpeechBox:
        return self._speech

    def mood(self) -> str:
        return self._mascot.mood()

    def say(self, text: str, mood: Optional[str] = None) -> None:
        """Одна реплика: очередь сбрасывается и реплика печатается сразу."""
        self._queue = []
        self._start(mood, text)

    def play(self, script: Sequence[Tuple[Optional[str], str]]) -> None:
        """Очередь реплик: первая печатается сразу, остальные — по клику."""
        self._queue = list(script)
        self.next()

    def next(self) -> None:
        """Перейти к следующей реплике очереди."""
        if not self._queue:
            return
        mood, text = self._queue.pop(0)
        self._start(mood, text)

    def pending(self) -> int:
        """Сколько реплик ещё ждёт клика."""
        return len(self._queue)

    def _start(self, mood: Optional[str], text: str) -> None:
        if mood:
            self._mascot.set_mood(mood)
        self._speech.say(text)
