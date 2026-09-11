"""Фикстуры для автотестов: фейковый провайдер, реализующий
интерфейсы Windows-слоя на основе данных-ответов.
"""
import os
import sys
import typing as t
from pathlib import Path

import pytest

# Qt должен подниматься без реального окна — иначе smoke-тесты UI падают
# в обычной консоли и в CI. Переменную надо выставить до импорта PySide6.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Убедимся, что корень проекта (рядом с этим файлом) виден.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.models import AppInfo


class FakeInstalledProvider:
    """Реализация минимального провайдера установленных программ для тестов."""

    def __init__(
        self,
        apps: t.List[t.Dict[str, t.Any]] | None = None,
    ) -> None:
        self._apps = apps or []

    def get_installed_apps(self, skip_wow64: bool = False) -> t.List[AppInfo]:
        if skip_wow64:
            return []
        apps: t.List[AppInfo] = []
        for a in self._apps:
            app = AppInfo(
                display_name=a.get("display_name", ""),
                install_location=a.get("install_location"),
                publisher=a.get("publisher"),
            )
            apps.append(app)
        return apps


@pytest.fixture(scope="session")
def qapp() -> t.Any:
    """Один QApplication на весь прогон плюс инициализированный контекст.

    Контекст — одиночка, поэтому создаём его ровно один раз на сессию
    и дальше только переключаем локаль внутри тестов.
    """
    from PySide6.QtWidgets import QApplication

    from ui.context import ctx

    app = QApplication.instance() or QApplication([])
    ctx().init(app)
    yield app


@pytest.fixture
def fake_installed_provider() -> FakeInstalledProvider:
    return FakeInstalledProvider(
        apps=[
            {"display_name": "Adobe Acrobat", "install_location": r"C:\Program Files\Adobe\Acrobat", "publisher": "Adobe"},
            {"display_name": "Some Bloatware App", "install_location": r"C:\Program Files\Bloatware", "publisher": "Bloatware Corp"},
        ]
    )
