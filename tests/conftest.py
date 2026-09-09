"""Фикстуры для автотестов: фейковый провайдер, реализующий
интерфейсы Windows-слоя на основе данных-ответов."""
import platform
import typing as t

import pytest

from core.models import AppInfo


class FakeInstalledProvider:
    """Реализация минимального провайдера установленных программ для тестов."""

    def __init__(
        self,
        apps: t.List[t.Dict[str, t.Any]] | None = None,
    ) -> None:
        self._apps = apps or []

    def get_installed_apps(self) -> t.List[AppInfo]:
        apps: t.List[AppInfo] = []
        for a in self._apps:
            app = AppInfo(
                display_name=a.get("display_name", ""),
                install_location=a.get("install_location"),
                publisher=a.get("publisher"),
            )
            apps.append(app)
        return apps


@pytest.fixture
def fake_installed_provider() -> FakeInstalledProvider:
    return FakeInstalledProvider(
        apps=[
            {"display_name": "Adobe Acrobat", "install_location": r"C:\Program Files\Adobe\Acrobat", "publisher": "Adobe"},
            {"display_name": "Some Bloatware App", "install_location": r"C:\Program Files\Bloatware", "publisher": "Bloatware Corp"},
        ]
    )
