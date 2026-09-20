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
                # Нет доступа к конфигурации (защищённые службы): честный
                # "unknown", а не вранье про "manual" — иначе пользователь
                # решит, что службу можно смело дергать.
                start_mode = "unknown"
            out.append(ServiceInfo(
                name=name,
                state=state_map.get(status[1], "other"),
                start_mode=start_mode))
        return out
    finally:
        win32service.CloseServiceHandle(scm)


# Службы, без которых система не загрузится или потеряет смысл. Их отключение
# не предлагается никогда — ни по одной, ни пакетом (context.md, 8.6).
_CRITICAL_SERVICES: t.FrozenSet[str] = frozenset(n.lower() for n in (
    "Winlogon", "RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "EventLog",
    "Schedule", "Winmgmt", "ProfSvc", "UserManager", "WinDefend", "WdNisSvc",
    "Sense", "SecurityHealthService", "EventSystem", "Power", "SystemEventsBroker",
    "BrokerInfrastructure", "SamSs", "gpsvc", "Themes", "AudioSrv",
    "AudioEndpointBuilder", "CryptSvc", "Dhcp", "Dnscache", "LanmanServer",
    "LanmanWorkstation", "Netlogon", "NlaSvc", "nsi", "Wcmsvc", "WlanSvc",
))

_START_TO_INT = {"automatic": 2, "manual": 3, "disabled": 4}  # SERVICE_*_START


def _win32_set_start_mode(name: str, mode: str) -> None:
    """Поставить режим запуска службе через SCM (ChangeServiceConfigW)."""
    import win32service

    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
    try:
        handle = win32service.OpenService(
            scm, name,
            win32service.SERVICE_CHANGE_CONFIG | win32service.SERVICE_QUERY_CONFIG)
        try:
            win32service.ChangeServiceConfig(
                handle,
                win32service.SERVICE_NO_CHANGE,      # serviceType
                _START_TO_INT[mode],                 # startType
                win32service.SERVICE_NO_CHANGE,      # errorControl
                None, None, None, None, None, None)  # пути/зависимости не трогаем
        finally:
            win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(scm)


class WindowsServiceController:
    """Контроллер служб: чтение списка + точечное отключение со снапшотом."""

    def __init__(self,
                 provider: t.Optional[t.Callable[[], t.List[ServiceInfo]]] = None,
                 changer: t.Optional[t.Callable[[str, str], None]] = None
                 ) -> None:
        self._provider = provider
        # Точка записи инъектируется отдельно: тесты подсовывают заглушку,
        # боевая работа идёт в SCM. Нет changer — писать нельзя вовсе.
        self._changer = changer if changer is not None else _win32_set_start_mode
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

    def is_critical(self, name: str) -> bool:
        return name.lower() in _CRITICAL_SERVICES

    def set_start_mode(self, name: str, mode: str) -> None:
        """Сменить режим запуска одной службы. Критичные — отказ всегда."""
        if mode not in _START_TO_INT:
            raise ValueError(f"неизвестный режим запуска: {mode}")
        # Запрет касается только выключения: вернуть критичной службе рабочий
        # режим (откат) — можно и нужно.
        if mode == "disabled" and self.is_critical(name):
            raise ValueError(f"критичную службу не отключаем: {name}")
        if self._changer is None:
            raise ValueError("запись режима службы недоступна")
        self._changer(name, mode)
        self._cache = None

    def disable_service(self, name: str) -> t.Dict[str, t.Any]:
        """Отключить службу (disabled) со снапшотом прежнего режима.

        Работающая служба НЕ останавливается: отключение касается только
        автозапуска, текущий сеанс доживает до перезагрузки — так система
        не падает посреди работы (правило 8.6: точечно и обратимо).
        """
        info = self.get_service_info(name)
        if info is None:
            raise ValueError(f"служба не найдена: {name}")
        if info.start_mode == "disabled":
            raise ValueError(f"служба уже отключена: {name}")
        snapshot = {
            "kind": "service",
            "service": name,
            "prev_start_mode": info.start_mode,
            "prev_state": info.state,
        }
        self.set_start_mode(name, "disabled")
        return snapshot

    def restore_service(self, snapshot: t.Dict[str, t.Any]) -> str:
        """Вернуть службе прежний режим запуска из снапшота."""
        if not isinstance(snapshot, dict) or snapshot.get("kind") != "service":
            raise ValueError("снапшот службы не найден или битый")
        name = str(snapshot.get("service", ""))
        mode = str(snapshot.get("prev_start_mode", "manual"))
        if mode not in _START_TO_INT:
            mode = "manual"
        self.set_start_mode(name, mode)
        return name
