"""Исполнитель действий — применяет подтверждённые изменения.

Каждое действие идёт только после явного подтверждения пользователя
(вызывающая сторона — диалог), оставляет строку в журнале и снапшот
для отката там, где откат возможен. Ошибка одного действия не роняет
остальные: она становится ExecutedAction(success=False, message=...).

Поддерживаемые действия:
- startup_disable: {"type", "name", "hive", "key_path", "value"} — снапшот
  значения Run-ключа → удаление значения; для записей папки автозагрузки
  (source="startup_folder", value = путь файла) — переезд файла в бэкап;
- service_disable: {"type", "name"} — снапшот режима запуска → StartMode
  Disabled (без остановки работающей службы, правило 8.6);
- uwp_remove: {"type", "package"} — снапшот манифеста → Remove-AppxPackage
  per-user (пакет остаётся staged, откат мгновенный);
- tweak_apply: {"type", "id", "enable", "params"} — снапшот прежних
  значений всех затрагиваемых ключей → применение набора операций твика;
- startup_restore / backup_restore: {"type", "snapshot"} — вернуть из
  снапшота; маршрут (реестр/файл/служба/uwp/твик) определяется по данным
  снапшота, поэтому одна кнопка «Вернуть» покрывает все виды.
"""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

from core.startup import (
    SOURCE_FOLDER,
    SOURCE_REGISTRY,
    StartupEntry,
    StartupManager,
)


@dataclass(frozen=True)
class ExecutedAction:
    action_type: str
    target: str
    success: bool
    message: str = ""
    snapshot: str = ""


class Executor:
    """Исполнитель подтверждённых действий: бэкап → действие → журнал."""

    def __init__(self,
                 store: t.Optional[t.Any] = None,
                 journal: t.Optional[t.Any] = None,
                 registry: t.Optional[t.Any] = None,
                 services: t.Optional[t.Any] = None,
                 uwp: t.Optional[t.Any] = None,
                 tweaks_engine: t.Optional[t.Any] = None) -> None:
        self._store = store
        self._journal = journal
        self._startup = StartupManager(registry=registry, store=store)
        # Контроллеры ленивые: SCM и PowerShell поднимаются только когда
        # действие действительно дошло до исполнения.
        self._services = services
        self._uwp = uwp
        self._tweaks_engine = tweaks_engine
        self._executed: t.List[ExecutedAction] = []

    def executed(self) -> t.List[ExecutedAction]:
        return list(self._executed)

    def _services_ctl(self) -> t.Any:
        if self._services is None:
            from core.services import WindowsServiceController
            self._services = WindowsServiceController()
        return self._services

    def _uwp_ctl(self) -> t.Any:
        if self._uwp is None:
            from core.uwp import UwpController
            self._uwp = UwpController()
        return self._uwp

    def _engine(self) -> t.Any:
        if self._tweaks_engine is None:
            from core.tweaks import TweaksEngine
            self._tweaks_engine = TweaksEngine(store=self._store)
        return self._tweaks_engine

    def _log(self, action: ExecutedAction) -> None:
        if self._journal is None:
            return
        try:
            self._journal.log(
                action.action_type,
                action.target,
                outcome="ok" if action.success else "error",
                message=action.message, snapshot=action.snapshot)
        except Exception:  # журнал не должен ломать само действие
            pass

    def execute_actions(self, actions: t.List[t.Dict[str, t.Any]]) -> t.List[ExecutedAction]:
        """Выполнить список действий; каждое уже подтверждено пользователем."""
        results = [self._do_action(a) for a in actions]
        self._executed.extend(results)
        return results

    def _do_action(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        atype = str(action.get("type", ""))
        target = str(action.get("name", action.get("snapshot", "?")))
        try:
            if atype == "startup_disable":
                return self._disable_startup(action)
            if atype in ("startup_restore", "backup_restore"):
                return self._restore_backup(action)
            if atype == "service_disable":
                return self._disable_service(action)
            if atype == "uwp_remove":
                return self._remove_uwp(action)
            if atype == "tweak_apply":
                return self._apply_tweak(action)
        except Exception as exc:  # noqa: BLE001 — ошибка действия не роняет список
            result = ExecutedAction(atype, target, False, str(exc))
            self._log(result)
            return result
        result = ExecutedAction(atype, target, False,
                                "неизвестный тип действия")
        self._log(result)
        return result

    def _disable_startup(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        source = str(action.get("source", SOURCE_REGISTRY))
        entry = StartupEntry(
            name=str(action.get("name", "")),
            path=str(action.get("value", "")),
            enabled=True,
            source=source if source in (SOURCE_REGISTRY, SOURCE_FOLDER)
                  else SOURCE_REGISTRY,
            hive=str(action.get("hive", "")),
            key_path=str(action.get("key_path", "")),
            value_type=int(action.get("value_type", 1) or 1),
        )
        snapshot = self._startup.disable(entry)
        result = ExecutedAction("startup_disable", entry.name, True,
                                "запись отключена", snapshot=snapshot)
        self._log(result)
        return result

    def _disable_service(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        if self._store is None:
            raise ValueError("нет хранилища бэкапов — отключать нельзя")
        name = str(action.get("name", ""))
        snapshot_data = self._services_ctl().disable_service(name)
        snapshot = f"service-{name}"
        self._store.save(snapshot, snapshot_data, kind="service_disable")
        result = ExecutedAction("service_disable", name, True,
                                "служба отключена (после перезагрузки)",
                                snapshot=snapshot)
        self._log(result)
        return result

    def _remove_uwp(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        if self._store is None:
            raise ValueError("нет хранилища бэкапов — удалять нельзя")
        full_name = str(action.get("package", ""))
        snapshot_data = self._uwp_ctl().remove_package(full_name)
        snapshot = f"uwp-{snapshot_data.get('name', full_name)}"
        self._store.save(snapshot, snapshot_data, kind="uwp_remove")
        result = ExecutedAction("uwp_remove", str(snapshot_data.get("name", full_name)),
                                True, "приложение удалено у пользователя",
                                snapshot=snapshot)
        self._log(result)
        return result

    def _apply_tweak(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        """Твик из базы: снапшот прежних значений → операции."""
        from core.tweaks import load_db

        tweak_id = str(action.get("id", ""))
        enable = bool(action.get("enable", True))
        params = action.get("params") or {}
        tweaks = {tw.id: tw for tw in load_db()}
        tweak = tweaks.get(tweak_id)
        if tweak is None:
            raise ValueError(f"твик не найден в базе: {tweak_id}")
        snapshot = self._engine().apply(tweak, enable, params)
        result = ExecutedAction(
            "tweak_apply", tweak_id, True,
            "твик применён" if enable else "твик выключен",
            snapshot=snapshot)
        self._log(result)
        return result

    def _restore_backup(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        """Откат по снапшоту: маршрут определяется данными, а не именем."""
        if self._store is None:
            raise ValueError("нет хранилища бэкапов")
        snapshot = str(action.get("snapshot", ""))
        data = self._store.restore(snapshot)
        if not isinstance(data, dict):
            raise ValueError(f"снапшот не найден или битый: {snapshot}")
        kind = str(data.get("kind", ""))
        if kind == "service":
            name = self._services_ctl().restore_service(data)
            self._store.remove(snapshot)
            result = ExecutedAction("service_restore", name, True,
                                    "режим запуска службы возвращён")
            self._log(result)
            return result
        if kind == "uwp":
            name = self._uwp_ctl().restore_package(data)
            self._store.remove(snapshot)
            result = ExecutedAction("uwp_restore", name, True,
                                    "приложение возвращено")
            self._log(result)
            return result
        if kind == "tweak":
            tweak_id = self._engine().restore(snapshot)
            result = ExecutedAction("tweak_restore", tweak_id, True,
                                    "твик возвращён как было")
            self._log(result)
            return result
        # Реестр и файл автозагрузки разбирает StartupManager сам.
        self._startup.restore(snapshot)
        result = ExecutedAction("startup_restore", snapshot, True,
                                "запись возвращена")
        self._log(result)
        return result
