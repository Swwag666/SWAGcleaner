"""Эвристический советник — анализирует данные сканера и генерирует план.

Советник использует набор правил и эвристик на основе базы знаний и
возвращает список рекомендаций, которые могут быть представлены пользователю
для подтверждения перед исполнением.
"""
import json
import os
import typing as t
from pathlib import Path

from core.models import AppInfo


class AdvisorRule:
    """Базовое правило совета.

    Каждое правило получает данные об приложении/системе и возвращает
    рекомендацию, если оно применимо.
    """

    def __init__(self, name: str, description: str, action_type: str, weight: float = 1.0) -> None:
        self.name = name
        self.description = description
        self.action_type = action_type
        self.weight = weight

    def apply(self, item: t.Any) -> t.Optional[t.Dict[str, t.Any]]:
        return None

    def apply_to_app(self, app: AppInfo) -> t.Optional[t.Dict[str, t.Any]]:
        return self.apply(app)


class KnownBloatwareRule(AdvisorRule):
    """Правило для известных программ-шелла.

    Проверяет publisher / display_name и помечает как кандидата на удаление.
    """

    def __init__(self, known: t.Set[str], description: str = "Известная bloatware-программа") -> None:
        super().__init__("known_bloatware", description, "remove", weight=1.2)
        self._known = known

    def apply(self, app: AppInfo) -> t.Optional[t.Dict[str, t.Any]]:
        if app.publisher and app.publisher.strip().lower() in {k.lower() for k in self._known}:
            return {
                "type": "remove",
                "name": app.display_name,
                "reason": f"Известная программа от {app.publisher}",
                "risk": "medium",
                "weight": self.weight,
            }
        # Если правило не применилось — пробуем искать по имени приложения
        if app.display_name and app.display_name.lower() in {k.lower() for k in self._known}:
            return {
                "type": "remove",
                "name": app.display_name,
                "reason": f"Известная bloatware-программа: {app.display_name}",
                "risk": "medium",
                "weight": self.weight,
            }
        return None


class Advisor:
    """Основной советник — получает скан и генерирует список рекомендаций.

    Пример: совместно с базой знаний, правилами и эвристиками.
    """

    def __init__(self, rules: t.List[AdvisorRule], knowledge_path: Path | str | None = None) -> None:
        self._rules = rules
        self._knowledge_path = Path(knowledge_path) if knowledge_path else None

    def analyze(self, apps: t.List[AppInfo]) -> t.List[t.Dict[str, t.Any]]:
        """Возвращает список рекомендаций на основе приложений."""
        recommendations: t.List[t.Dict[str, t.Any]] = []
        for app in apps:
            for rule in self._rules:
                res = rule.apply(app)
                if res is not None:
                    recommendations.append(res)
        return recommendations


class BloatwareExplorerRule(AdvisorRule):
    """Продвинутое правило, которое рекомендует исследовать bloatware перед удалением."""

    def __init__(self) -> None:
        super().__init__("bloatware_explorer", "Рекомендуется исследовать bloatware перед удалением", "explore", weight=0.5)

    def apply(self, app: AppInfo) -> t.Optional[t.Dict[str, t.Any]]:
        if app.display_name and "bloatware" in app.display_name.lower():
            return {
                "type": "explore",
                "name": app.display_name,
                "reason": "Программа отвечает критериям bloatware — рекомендуется проверить перед удалением.",
                "risk": "low",
                "weight": self.weight,
            }
        return None


class TaskPrioritizer:
    """Правило приоритезации задач на основе весов.

    В первой итерации сортирует план по весу правила и возвращает
    отфильтрованный список задач, готовый к подтверждению пользователем.
    """

    def __init__(self) -> None:
        self._rules: t.List[AdvisorRule] = []

    def prioritize(self, tasks: t.List[t.Dict[str, t.Any]]) -> t.List[t.Dict[str, t.Any]]:
        """Возвращает отсортированный список задач по убыванию веса."""
        tasks_sorted = sorted(tasks, key=lambda t: t.get("weight", 0), reverse=True)
        return tasks_sorted[:100]
