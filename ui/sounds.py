"""Пиксельные звуки SWAGcleaner.

Короткие «блипы» собираются прямо в коде: это WAV в памяти, никаких файлов
в сборке. Звук можно выключить в настройках.

Играем через winsound из стандартной библиотеки Windows. Так сделано
намеренно: он не тянет в сборку плагины Qt и, главное, не опрашивает звуковые
устройства — перебор устройств Qt на некоторых машинах подвисает на секунды,
а нам нужны доли миллисекунды. На других системах звука просто не будет.
"""
from __future__ import annotations

import logging
import math
import struct
from typing import Dict, Optional

try:  # на не-Windows звука не будет, всё остальное работает как обычно
    import winsound
except ImportError:  # pragma: no cover - Windows и есть целевая платформа
    winsound = None  # type: ignore[assignment]

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractButton, QApplication

_LOGGER = logging.getLogger("swag.ui.sounds")

SAMPLE_RATE = 22050

# Каждое событие — свой короткий тон: частота в герцах и длительность в секундах.
# Частоты подобраны так, чтобы звук читался как «пиксельный»: короткие
# прямоугольные волны, без длинного хвоста.
EVENTS: Dict[str, tuple[float, float]] = {
    "click": (880.0, 0.035),
    "page": (660.0, 0.050),
    "done": (1320.0, 0.090),
    "cancel": (330.0, 0.060),
    "error": (220.0, 0.120),
}


def wav_bytes(frequency: float, seconds: float, amplitude: float = 0.25) -> bytes:
    """Собрать короткий прямоугольный тон как WAV прямо в памяти.

    Прямоугольная волна (а не синус) — это и есть тот самый «пиксельный»
    оттенок. Затухание в конце убирает щелчок при обрыве звука.
    """
    frames = max(1, int(SAMPLE_RATE * seconds))
    samples = bytearray()
    for index in range(frames):
        value = math.sin(2.0 * math.pi * frequency * index / SAMPLE_RATE)
        square = 1.0 if value >= 0.0 else -1.0
        envelope = 1.0 - index / frames
        samples += struct.pack("<h", int(square * envelope * amplitude * 32767))

    payload = bytes(samples)
    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(payload))
    return header + payload


class SoundPlayer:
    """Проигрыватель коротких звуков интерфейса.

    Флаг включённости живёт здесь, а не в настройках: виджетам и диалогам
    достаточно вызвать play(), чтобы не знать, включён ли звук.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._cache: Dict[str, bytes] = {}

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802 - как у виджетов Qt
        self._enabled = bool(enabled)

    def isEnabled(self) -> bool:  # noqa: N802
        return self._enabled

    def play(self, event: str) -> None:
        if not self._enabled or winsound is None:
            return
        data = self._cache.get(event)
        if data is None:
            params = EVENTS.get(event)
            if params is None:
                _LOGGER.debug("Неизвестный звук: %s", event)
                return
            data = wav_bytes(*params)
            self._cache[event] = data
        try:
            # SND_MEMORY: играем из памяти. Буфер держим в кэше — winsound
            # читает его асинхронно, и без ссылки он мог бы исчезнуть.
            winsound.PlaySound(
                data,
                winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception as exc:  # pragma: no cover - зависит от звуковой системы
            _LOGGER.debug("Звук %s не сыграл: %s", event, exc)


class ClickSoundFilter(QObject):
    """Блип на нажатие любой кнопки.

    Ставится фильтром на приложение, чтобы не править каждую кнопку:
    звук получают и страницы, и шапка, и диалоги. Пункты бокового меню
    пропускаем — у них свой звук перехода раздела.
    """

    def eventFilter(self, widget: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(widget, QAbstractButton)
            and widget.objectName() != "navItem"
        ):
            player().play("click")
        return False


_player: Optional[SoundPlayer] = None
_filter: Optional[ClickSoundFilter] = None


def player(enabled: bool = True) -> SoundPlayer:
    """Общий проигрыватель приложения."""
    global _player
    if _player is None:
        _player = SoundPlayer(enabled)
    return _player


def play(event: str) -> None:
    """Сыграть событие интерфейса (если звук не выключен)."""
    player().play(event)


def attach(app: QApplication) -> ClickSoundFilter:
    """Подключить блип на клики ко всему приложению."""
    global _filter
    if _filter is None:
        _filter = ClickSoundFilter(app)
        app.installEventFilter(_filter)
    return _filter
