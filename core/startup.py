"""Модуль автозагрузки — чтение и изменение элементов автозагрузки.

В начальной итерации реализована только read-only часть.
Изменения автозагрузки требуют прав и делегируются провайдеру.
"""
import typing as t
from dataclasses import dataclass


@dataclass(frozen=True)
class StartupEntry:
    """Элемент автозагрузки — файл/реестр запуска программы."""

    name: str
    path: str
    enabled: bool
    source: str  # registry, startup_folder


class StartupManager:
    """Менеджер автозагрузки — чтение и изменение элементов.

    Для большей части операций требуется работа с реестром и прав доступа.
    """

    def __init__(self) -> None:
        self._entries: t.List[StartupEntry] = []

    def get_startup_entries(self) -> t.List[StartupEntry]:
        """Возвращает список элементов автозагрузки."""
        return list(self._entries)


# Заглушки для разных источников автозагрузки
_STANDARD_REGISTRY_LOCATIONS = (
    r"Software\Microsoft\Windows\CurrentVersion\Run",
    r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
    r"Software\Microsoft\Windows\CurrentVersion\RunServices",
    r"Software\Microsoft\Windows\CurrentVersion\RunServicesOnce",
)


def read_startup_from_registry() -> t.List[StartupEntry]:
    """Чтение элементов автозагрузки из реестра (заглушка)."""
    return []


def write_startup_to_registry(entries: t.List[StartupEntry]) -> None:
    """Запись/изменение автозагрузки в реестре (заглушка)."""
    pass
