"""Минимальные модели для SWAGcleaner — кандидат в ядро.

Этот файл в первой итерации содержит только базовый набор структур:
- AppInfo — представление установленной программы
- Plan — план действий с возможным бэкапом
"""
import os
import typing as t
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AppInfo:
    """Единица информации об установленной программе.

    Сохраняет display_name, install_location, publisher.
    """
    display_name: str
    install_location: str | None = None
    publisher: str | None = None

    @property
    def install_root(self) -> str | None:
        """Директория установки без выполнения статистических проверок."""
        if self.install_location is None:
            return None
        norm = os.path.normpath(self.install_location)
        return norm


@dataclass
class Plan:
    """План действий со связью к бэкапу.

    snapshot — фрагмент состояния, который был отключён/удалён при
    выполнении плана, для возможного восстановления.
    """
    snapshot: t.Any = field(default=None)

    def __post_init__(self) -> None:
        # В первой итерации бэкап — произвольный объект.
        if self.snapshot is not None and not isinstance(self.snapshot, dict):
            # Если передан не dict и не None — пытаемся обернуть.
            object.__setattr__(self, "snapshot", {"payload": self.snapshot})
