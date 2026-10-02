"""Фикстуры для автотестов: фейковый провайдер, реализующий
интерфейсы Windows-слоя на основе данных-ответов.
"""
import os
import sys
import tempfile
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

try:  # Qt есть не везде: Linux-плечо мульти-ОС прогона идёт без PySide6.
    from PySide6.QtCore import QSettings
    HAS_QT = True
except ImportError:  # pragma: no cover - не-Windows без Qt
    QSettings = None  # type: ignore[assignment]
    HAS_QT = False

from core.models import AppInfo

# Язык, тема и шрифт сохраняются в QSettings. Во время тестов уводим их в
# временную папку: прогон не должен трогать реальные настройки пользователя
# и не должен зависеть от того, что он выбирал в прошлый раз.
if HAS_QT:
    _SETTINGS_DIR = tempfile.mkdtemp(prefix="swagcleaner-tests-")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      _SETTINGS_DIR)


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
    if not HAS_QT:
        pytest.skip("PySide6 нет: UI-тесты на этой ОС не живут")
    if not HAS_QT:
        pytest.skip("PySide6 нет: UI-тесты на этой ОС не живут")
    from PySide6.QtWidgets import QApplication

    from ui import sounds
    from ui.context import ctx

    app = QApplication.instance() or QApplication([])
    ctx().init(app)
    # Прогон не должен пищать на машине и мешать работать рядом.
    ctx().setSounds(False)
    sounds.player().setEnabled(False)
    yield app


@pytest.fixture
def fake_installed_provider() -> FakeInstalledProvider:
    return FakeInstalledProvider(
        apps=[
            {"display_name": "Adobe Acrobat", "install_location": r"C:\Program Files\Adobe\Acrobat", "publisher": "Adobe"},
            {"display_name": "Some Bloatware App", "install_location": r"C:\Program Files\Bloatware", "publisher": "Bloatware Corp"},
        ]
    )


@pytest.fixture
def win_fake(qapp: t.Any) -> t.Any:
    """Окно вместе с фейковой сессией: тест сам завершает задачи.

    Общая для всех файлов UI-тестов: и smoke, и карантин гоняют окно
    на одном и том же фейковом ядре.
    """
    from PySide6.QtTest import QTest

    from tests.fakes import FakeCoreSession
    from ui.context import ctx
    from ui.main import MainWindow

    session = FakeCoreSession()
    window = MainWindow(qapp, ctx(), session)
    window.resize(1100, 700)
    window.show()
    QTest.qWait(80)
    yield window, session
    window.close()
    window.deleteLater()
    qapp.processEvents()
    ctx().setLocale("ru")
    ctx().setTheme("dark")
    ctx().setFontKind("pixel")
