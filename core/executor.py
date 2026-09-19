"""Исполнитель действий — применяет подтверждённые изменения.

Каждое действие идёт только после явного подтверждения пользователя
(вызывающая сторона — диалог), оставляет строку в журнале и снапшот
для отката там, где откат возможен. Ошибка одного действия не роняет
остальные: она становится ExecutedAction(success=False, message=...).

Поддерживаемые действия:
- startup_disable: {"type", "name", "hive", "key_path", "value"} —
  снапшот значения Run-ключа → удаление значения;
- startup_restore: {"type", "snapshot"} — вернуть значение из снапшота.
"""
from __future__ import annotations

import typing as t
from dataclasses import dataclass

from core.startup import StartupEntry, StartupManager, SOURCE_REGISTRY


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
                 registry: t.Optional[t.Any] = None) -> None:
        self._store = store
        self._journal = journal
        self._startup = StartupManager(registry=registry, store=store)
        self._executed: t.List[ExecutedAction] = []

    def executed(self) -> t.List[ExecutedAction]:
        return list(self._executed)

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
            if atype == "startup_restore":
                return self._restore_startup(action)
        except Exception as exc:  # noqa: BLE001 — ошибка действия не роняет список
            result = ExecutedAction(atype, target, False, str(exc))
            self._log(result)
            return result
        result = ExecutedAction(atype, target, False,
                                "неизвестный тип действия")
        self._log(result)
        return result

    def _disable_startup(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        entry = StartupEntry(
            name=str(action.get("name", "")),
            path=str(action.get("value", "")),
            enabled=True,
            source=SOURCE_REGISTRY,
            hive=str(action.get("hive", "")),
            key_path=str(action.get("key_path", "")),
            value_type=int(action.get("value_type", 1) or 1),
        )
        snapshot = self._startup.disable(entry)
        result = ExecutedAction("startup_disable", entry.name, True,
                                "запись отключена", snapshot=snapshot)
        self._log(result)
        return result

    def _restore_startup(self, action: t.Dict[str, t.Any]) -> ExecutedAction:
        snapshot = str(action.get("snapshot", ""))
        self._startup.restore(snapshot)
        result = ExecutedAction("startup_restore", snapshot, True,
                                "запись возвращена")
        self._log(result)
        return result
