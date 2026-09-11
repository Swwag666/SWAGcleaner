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
import tempfile
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


def _log_path() -> Path:
    """Путь к файлу лога на случай, когда консоли нет."""
    return Path(tempfile.gettempdir()) / "swagcleaner.log"


def _log_unhandled(exc_type, exc_value, exc_tb) -> None:
    """Записать необработанную ошибку в лог.

    У оконного .exe нет консоли, поэтому лог — единственный след.
    """
    _logger.critical("Необработанная ошибка", exc_info=(exc_type, exc_value, exc_tb))


def _setup_logging(level: int = logging.INFO) -> None:
    """Настроить логирование и перехват необработанных ошибок."""
    if sys.stderr is not None:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    else:
        # Оконная сборка запускается без консоли — пишем в файл,
        # иначе ошибки старта пропадут молча.
        logging.basicConfig(
            level=level,
            format=LOG_FORMAT,
            filename=_log_path(),
            encoding="utf-8",
        )
    sys.excepthook = _log_unhandled


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
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="проверить сборку: поднять окно без показа, отчитаться и выйти",
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


def _self_test() -> int:
    """Проверить, что сборка рабочая: окно, вкладки, строки локализации.

    Окно не показывается на экране (Qt поднимается в offscreen-режиме), а
    отчёт пишется ещё и в файл: у оконного .exe нет консоли, и без отчёта
    понять, что именно не так, невозможно. Возвращает 0, если всё хорошо.
    """
    lines: list[str] = []
    code = 0
    try:
        # Платформу Qt нужно выставить до импорта PySide6.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from ui import sounds
        from ui.character import available_moods, available_talk_frames, demo_moods
        from ui.context import init_context
        from ui.dialog import ConfirmDialog
        from ui.main import MainWindow
        from ui.theme import pixel_font_available

        app = QApplication([])
        context = init_context(app)
        window = MainWindow(app, context)
        window.show()

        page_count = window._stack.count()
        title = context.tr("app.title")
        advisor = context.tr("tabs.advisor")
        nav_count = window._sidebar.count()

        moods = available_moods()
        found_moods = sorted(name for name, path in moods.items() if path is not None)
        talk = available_talk_frames()
        own = demo_moods()
        speech = window._speech.full_text()

        lines.append(f"локаль: {context.locale()}")
        lines.append(f"тема: {context.theme()}")
        lines.append(f"шрифт: {context.fontKind()}")
        lines.append(f"страниц: {page_count}")
        lines.append(f"пунктов меню: {nav_count}")
        lines.append(f"app.title: {title!r}")
        lines.append(f"tabs.advisor: {advisor!r}")
        lines.append(f"позы персонажа: {', '.join(found_moods) or 'нет'}")
        lines.append(f"свои позы: {', '.join(own) or 'нет'}")
        lines.append(
            "кадры речи: "
            + ", ".join(f"{mood}={count}" for mood, count in talk.items() if count)
        )
        lines.append(f"первая реплика: {speech[:40]!r}")

        # Звук: блип собирается в памяти, для проверки аудиоустройства не нужны.
        blip = sounds.wav_bytes(880.0, 0.03)
        lines.append(f"пиксельный звук: {len(blip)} байт")
        if blip[:4] != b"RIFF" or len(blip) < 1000:
            lines.append("ОШИБКА: генератор звука не работает")
            code = 1

        # Подтверждение: окно должно собираться и знать свои пункты.
        dialog = ConfirmDialog(window, [("Проверка 1", "low"), ("Проверка 2", "high")])
        lines.append(f"пунктов в подтверждении: {dialog.count()}")
        if dialog.count() != 2:
            lines.append("ОШИБКА: окно подтверждения не собралось")
            code = 1
        dialog.deleteLater()

        # Занятость: полоска под шапкой должна уметь работать индикатором.
        window.set_busy(True)
        if not window._accent_bar.is_busy():
            lines.append("ОШИБКА: полоска занятости не включается")
            code = 1
        window.set_busy(False)

        if page_count != 5:
            lines.append(f"ОШИБКА: ожидалось 5 страниц, получилось {page_count}")
            code = 1
        if nav_count != 5:
            lines.append(f"ОШИБКА: ожидалось 5 пунктов меню, получилось {nav_count}")
            code = 1
        if advisor == "tabs.advisor":
            lines.append("ОШИБКА: строки локализации не загрузились")
            code = 1
        if not pixel_font_available():
            lines.append("ОШИБКА: пиксельный шрифт не попал в сборку")
            code = 1
        if moods["scan"] is None or moods["panic"] is None:
            lines.append("ОШИБКА: картинки персонажа не попали в сборку")
            code = 1
        if moods["calm"] is None or moods["calm"] == moods["scan"]:
            lines.append("ОШИБКА: спокойная поза не попала в сборку")
            code = 1
        # Поз без кадров речи быть не должно: иначе рот не открывается.
        silent = sorted(mood for mood, path in moods.items() if path and talk[mood] < 2)
        if silent:
            lines.append(f"ОШИБКА: нет кадров речи у поз: {', '.join(silent)}")
            code = 1
        if not speech:
            lines.append("ОШИБКА: панель реплики пуста — персонаж не заговорил")
            code = 1

        QTimer.singleShot(0, app.quit)
        app.exec()
    except Exception as exc:
        lines.append(f"ИСКЛЮЧЕНИЕ: {exc!r}")
        code = 1

    lines.append("итог: " + ("OK" if code == 0 else "FAIL"))
    report = "\n".join(lines)
    try:
        (Path(tempfile.gettempdir()) / "swagcleaner_selftest.txt").write_text(
            report, encoding="utf-8"
        )
    except OSError:
        pass
    print(report)
    return code


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

    if args.self_test:
        return _self_test()

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
