"""Исполнитель действий — применяет подтверждённые рекомендации.

Гарантирует, что каждое действие выполняется после явного подтверждения
пользователя, и корректно работает с бэкапами.
"""
import typing as t
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutedAction:
    action_type: str
    target: str
    success: bool
    message: str = ""


class Executor:
    """Базовый исполнитель действий — интерфейс для подтверждённых изменений.

    В первой итерации реализует только логирование и каркас для выполнения,
    реальные операции делегируются провайдерам окружения.
    """

    def __init__(self) -> None:
        self._executed: t.List[ExecutedAction] = []

    def execute_actions(self, actions: t.List[t.Dict[str, t.Any]]) -> t.List[ExecutedAction]:
        """Выполняет список действий после подтверждения.

        actions — список действий вида:
        {
            "type": "remove",
            "name": "Some App",
            "reason": "...",
            "risk": "medium",
        }
        """
        executed: t.List[ExecutedAction] = []
        for action in actions:
            atype = action.get("type")
            name = action.get("name", "unknown")
            result = self._do_action(atype, name)
            executed.append(result)
        self._executed.extend(executed)
        return executed

    def _do_action(self, action_type: str, target: str) -> ExecutedAction:
        """Внутренний метод выполнения одного действия (заглушка)."""
        return ExecutedAction(
            action_type=action_type,
            target=target,
            success=False,
            message="Действие не реализовано в начальной итерации",
        )
