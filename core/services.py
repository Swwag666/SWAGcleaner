"""Службы Windows — реальный список через Service Control Manager.

Читаем состояние и режим запуска всех служб (win32service). Это только
чтение: отключение служб списком сознательно не делаем (context.md, 8.6),
а точечное отключение со снапшотом — отдельный этап.

Провайдер инъектируется: настоящий ходит в SCM, в тестах подсовывается
список из памяти — так тесты не зависят от машины, где они бегут.
"""
from __future__ import annotations

import typing as t
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceInfo:
    name: str
    state: str       # running | stopped | paused | other
    start_mode: str  # automatic | manual | disabled


def _win32_list_services() -> t.List[ServiceInfo]:
    """Настоящее чтение: EnumServicesStatus + QueryServiceConfigW.

    EnumServicesStatus (без Ex): в pywin32 у Ex-версии нестандартная
    сигнатура, классическая читается надёжно и даёт то же: имя, состояние.
    """
    import win32service

    scm = win32service.OpenSCManager(
        None, None,
        win32service.SC_MANAGER_ENUMERATE_SERVICE | win32service.SC_MANAGER_CONNECT)
    try:
        statuses = win32service.EnumServicesStatus(
            scm, win32service.SERVICE_WIN32, win32service.SERVICE_STATE_ALL)

        state_map = {
            win32service.SERVICE_RUNNING: "running",
            win32service.SERVICE_STOPPED: "stopped",
            win32service.SERVICE_PAUSED: "paused",
        }
        start_map = {
            win32service.SERVICE_AUTO_START: "automatic",
            win32service.SERVICE_DEMAND_START: "manual",
            win32service.SERVICE_DISABLED: "disabled",
        }
        out: t.List[ServiceInfo] = []
        for name, _display, status in statuses:
            try:
                handle = win32service.OpenService(
                    scm, name, win32service.SERVICE_QUERY_CONFIG)
                try:
                    config = win32service.QueryServiceConfig(handle)
                    start_mode = start_map.get(config[1], "manual")
                finally:
                    win32service.CloseServiceHandle(handle)
            except Exception:
                # Нет доступа к конфигурации (защищённые службы) — честно
                # показываем состояние, режим помечаем как неизвестный.
                start_mode = "manual"
            out.append(ServiceInfo(
                name=name,
                state=state_map.get(status[1], "other"),
                start_mode=start_mode))
        return out
    finally:
        win32service.CloseServiceHandle(scm)


class WindowsServiceController:
    """Контроллер служб: чтение списка служб с их состоянием."""

    def __init__(self,
                 provider: t.Optional[t.Callable[[], t.List[ServiceInfo]]] = None
                 ) -> None:
        self._provider = provider
        self._cache: t.Optional[t.List[ServiceInfo]] = None

    def list_services(self) -> t.List[ServiceInfo]:
        """Список служб: реестр SCM, свежие сверху по имени."""
        if self._provider is not None:
            return self._provider()
        if self._cache is None:
            self._cache = sorted(_win32_list_services(), key=lambda s: s.name)
        return list(self._cache)

    def get_service_info(self, name: str) -> t.Optional[ServiceInfo]:
        for svc in self.list_services():
            if svc.name == name:
                return svc
        return None
