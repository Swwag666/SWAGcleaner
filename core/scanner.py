"""Сканер системы — находит установленные приложения, процессы, службы,
элементы автозагрузки; возвращает плоские структуры без привязки к UI.

В первой итерации не удаляет ничего, не меняет состояние ПК и не требует прав.
"""
import typing as t

from core.models import AppInfo


class SystemScanner:
    """Сканер, собирающий статическую информацию о системе.

    Все методы — read-only.
    """

    def __init__(self, installed_provider: "InstalledProvider") -> None:
        self._installed_provider = installed_provider

    def scan_installed_apps(self, skip_wow64: bool = False) -> t.List[AppInfo]:
        """Возвращает список известных установленных приложений.

        skip_wow64 — флаг, используемый провайдером для исключения wow64-ключей.
        """
        return self._installed_provider.get_installed_apps(skip_wow64=skip_wow64)

    def scan_processes(self) -> t.List[t.Dict[str, t.Any]]:
        """Возвращает список запущенных процессов с базовой информацией.

        Для первой итерации — подготовленный заглушечный список без реального чтения.
        """
        return [
            {"pid": 1, "name": "system", "cpu_percent": 0.0, "memory_mb": 0.0},
            {"pid": 1234, "name": "explorer", "cpu_percent": 0.1, "memory_mb": 128.0},
        ]

    def scan_services(self) -> t.List[t.Dict[str, t.Any]]:
        """Возвращает список служб с их состоянием и типом запуска.

        Первая итерация — заглушка.
        """
        return [
            {"name": "Spooler", "state": "running", "start_mode": "manual"},
            {"name": "Themes", "state": "running", "start_mode": "automatic"},
        ]

    def scan_startup(self) -> t.List[t.Dict[str, t.Any]]:
        """Возвращает элементы автозагрузки.

        Первая итерация — заглушка.
        """
        return [
            {"name": "Adobe Updater", "enabled": True, "source": "registry"},
            {"name": "Steam", "enabled": True, "source": "startup_folder"},
        ]
