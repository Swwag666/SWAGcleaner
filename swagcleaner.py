#!/usr/bin/env python
"""SWAGcleaner — локальная тулза для ПК.

Кратко:
- Сканер системы (программы, процессы, службы, автозагрузка).
- Эвристический советник (правила → план → подтверждение → исполнение).
- Чистка во временные файлы и мусор (только в корзину).
- Поиск дубликатов фото (точные и perceptual хеши).
- Твики (в будущем: службы, автозагрузка, UWP).

Всё работает локально. Ничего не отправляется в сеть.
Удаления и отключения только с подтверждения пользователя.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

# Включить отладочный вывод, если запрошено.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

_logger = logging.getLogger("swagcleaner")


def _resolve_project_root() -> Path:
    """Найти корень проекта относительно этого файла."""
    here = Path(__file__).resolve().parent
    # Если рабочем каталоге лежит pyproject.toml — брать его.
    candidate = Path.cwd()
    if (candidate / "pyproject.toml").exists():
        return candidate
    if (candidate / "requirements.txt").exists():
        return candidate
    # Иначе корень рядом с этим файлом.
    return here


def _setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)


def _ensure_deps() -> None:
    """Проверить наличие зависимостей и выдать понятную ошибку."""
    try:
        import PySide6  # noqa: F401
    except ImportError as e:
        _logger.error("Missing PySide6: %s", e)
        sys.exit(1)

    try:
        import psutil  # noqa: F401
    except ImportError as e:
        _logger.warning("Missing psutil: %s — some features will be limited", e)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="swagcleaner",
        description="SWAGcleaner — локальная тулза для ПК (сканер, советник, чистка, дубликаты фото).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="корневая папка проекта (по умолчанию — текущая)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="уровень логирования",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="запустить сканер системы в консоли (без UI)",
    )
    parser.add_argument(
        "--advisor",
        action="store_true",
        help="запустить советника в консоли (без UI)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        default=True,
        help="запустить графический интерфейс (по умолчанию)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="показать версию",
    )
    return parser.parse_args(argv)


def _version() -> str:
    return "0.2.0"


def _cli_scan(installed_provider):
    from core.scanner import SystemScanner
    scanner = SystemScanner(installed_provider)
    apps = scanner.scan_installed_apps()
    print(f"Найдено приложений: {len(apps)}")
    for a in apps:
        print(f"  - {a.display_name}", end="")
        if a.publisher:
            print(f" ({a.publisher})", end="")
        print()


def _cli_advisor(installed_provider):
    from core.advisor import Advisor, KnownBloatwareRule, TaskPrioritizer

    apps = installed_provider.get_installed_apps()
    # База знаний пока демонстрационная: одно правило на выдуманного издателя.
    # На реальных программах рекомендации появятся, когда наполним базу.
    rules = [KnownBloatwareRule({"Bloatware Corp"})]
    advisor = Advisor(rules)
    recommendations = advisor.analyze(apps)
    prioritizer = TaskPrioritizer()
    plan = prioritizer.prioritize(recommendations)
    print(f"Проверено программ: {len(apps)}")
    print(f"Рекомендаций: {len(plan)}")
    for p in plan:
        print(f"  - {p.get('type')} | {p.get('name')} | {p.get('risk')} | {p.get('reason')}")
    if not plan:
        print("  (база знаний пока демо-заглушка — это ожидаемо)")


def main(argv: list[str] | None = None) -> int:
    _logger.debug("swagcleaner starting")

    args = _parse_args(argv)

    if args.version:
        print(f"SWAGcleaner {_version()}")
        return 0

    _setup_logging(getattr(logging, args.log_level.upper(), logging.INFO))

    root = args.root or _resolve_project_root()
    os.chdir(root)

    _ensure_deps()

    from core.apps import KNOWN_APPS, WindowsInstalledProvider
    # KNOWN_APPS — только запасной демо-набор на случай, если реестр недоступен.
    installed_provider = WindowsInstalledProvider(KNOWN_APPS)

    if args.scan:
        _cli_scan(installed_provider)
        return 0

    if args.advisor:
        _cli_advisor(installed_provider)
        return 0

    if not args.gui:
        print("Графический режим отключён. Запустите --gui для включения.")
        return 0

    try:
        from ui.context import init_context
        from ui.main import MainWindow
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        _logger.error("Failed to start GUI: %s", e)
        sys.exit(1)

    app = QApplication([])
    context = init_context(app)
    win = MainWindow(app, context)

    win.show()
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
