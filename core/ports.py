"""Порты (интерфейсы) для внешних провайдеров.

Этот модуль определяет высокоуровневые интерфейсы, которые реализуют
внешние службы, модули и провайдеры окружения.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional

from core.models import AppInfo


class InstalledProvider(ABC):
    """Поставщик информации об установленных приложениях.

    Методы возвращают списки объектов AppInfo.
    """

    @abstractmethod
    def get_installed_apps(self, skip_wow64: bool = False) -> List[AppInfo]:
        ...


class SystemProvider(ABC):
    """Поставщик информации о процессах, службах и автозагрузке.

    Все методы read-only.
    """

    @abstractmethod
    def get_processes(self) -> List[dict]:
        ...

    @abstractmethod
    def get_services(self) -> List[dict]:
        ...

    @abstractmethod
    def get_startup(self) -> List[dict]:
        ...


class TrashProvider(ABC):
    """Поставщик операций с корзиной.

    Используется для удаления файлов и папок в корзину.
    """

    @abstractmethod
    def send_to_trash(self, path: str) -> None:
        ...


class ElevateProvider(ABC):
    """Поставщик повышения прав.

    Реализует механизмы для выполнения привилегированных операций.
    """

    @abstractmethod
    def elevate(self, action: str) -> None:
        ...
