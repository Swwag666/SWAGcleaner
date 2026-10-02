"""Таймер выключения: shutdown /s /t <сек> и отмена shutdown /a.

Самописное, без зашитых пресетов: любое число минут, честная отмена и учёт
того, что знаем мы только про таймеры, которые поставили сами (shutdown не
умеет «показать оставшееся»).
"""
from __future__ import annotations

import subprocess
import time
import typing as t

MAX_MINUTES = 24 * 60


class ShutdownTimer:
    """Постановка/отмена отложенного выключения. runner инъектируется."""

    def __init__(self, runner: t.Optional[t.Callable[..., t.Any]] = None,
                 clock: t.Callable[[], float] = time.monotonic) -> None:
        self._runner = runner if runner is not None else subprocess.run
        self._clock = clock
        self._set_at: t.Optional[float] = None
        self._seconds = 0

    def schedule(self, minutes: int) -> int:
        """Поставить выключение через N минут; вернуть секунды."""
        if minutes < 1 or minutes > MAX_MINUTES:
            raise ValueError(f"минуты вне 1..{MAX_MINUTES}: {minutes}")
        seconds = minutes * 60
        result = self._runner(["shutdown", "/s", "/t", str(seconds)],
                              capture_output=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"shutdown rc={result.returncode}")
        self._set_at = self._clock()
        self._seconds = seconds
        return seconds

    def cancel(self) -> None:
        """Отменить отложенное выключение (shutdown /a)."""
        result = self._runner(["shutdown", "/a"], capture_output=True,
                              timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"shutdown /a rc={result.returncode}")
        self._set_at = None
        self._seconds = 0

    def remaining(self) -> t.Optional[int]:
        """Осталось секунд по нашему таймеру; None — мы ничего не ставили."""
        if self._set_at is None:
            return None
        left = int(self._seconds - (self._clock() - self._set_at))
        return max(left, 0)
