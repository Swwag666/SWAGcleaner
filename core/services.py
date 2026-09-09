"""Реализация служб для Windows — чтение, изменение служб.

Для большей части операций, связанных со службами, тестируется только
read-only чтение и бэкап, изменения не выполняются в начальной итерации.
"""
import typing as t
from dataclasses import dataclass

# В первой итерации без реальных вызовов WinAPI.
# Реализации служб здесь — заглушки.


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    state: str  # running, stopped, auto, manual
    start_mode: str  # automatic, manual, disabled


class WindowsServiceController:
    """Контроллер служб — read-only в начальной итерации.

    Позволяет получать список служб, их состояние и режим запуска.
    """

    def __init__(self) -> None:
        self._services: t.List[ServiceInfo] = []

    def list_services(self) -> t.List[ServiceInfo]:
        """Возвращает известные службы с их состоянием.

        Реальная реализация будет читать win32service.
        """
        return self._services.copy()

    def get_service_info(self, name: str) -> t.Optional[ServiceInfo]:
        """Возвращает информацию о конкретной службе по имени."""
        for svc in self._services:
            if svc.name == name:
                return svc
        return None
