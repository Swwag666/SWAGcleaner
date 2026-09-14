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
        "--disk",
        nargs="*",
        metavar="ПУТЬ",
        default=None,
        help="быстрый индекс диска через Rust-ядро (без UI); без путей — свои корни",
    )
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="вместе с --disk: пройтись по мусорным корням и показать категории",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="вместе с --disk/--candidates: объяснить находки человеческим языком (AI)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="сырой JSON от ядра вместо красивого отчёта",
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


def _human(n: int) -> str:
    v = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if v < 1024 or unit == "ТБ":
            return f"{v:.1f} {unit}" if unit != "Б" else f"{int(v)} Б"
        v /= 1024
    return f"{n} Б"


def _cli_disk(roots: list[str], candidates: bool, explain: bool, as_json: bool) -> int:
    """Прогнать Rust-ядро в консоли: индекс диска или кандидаты на чистку."""
    from core.swagscan import SwagscanClient, SwagscanError

    try:
        client = SwagscanClient()
    except SwagscanError as e:
        print(f"Rust-ядро недоступно: {e}")
        return 2

    last_pct = {"v": -1}

    def on_progress(ev: dict) -> None:
        pct = int(ev.get("done", 0))
        if pct // 5000 != last_pct["v"] // 5000:
            last_pct["v"] = pct
            print(f"  ...{pct} файлов, {_human(int(ev.get('bytes', 0)))}", flush=True)

    try:
        with client:
            if candidates:
                print("Кандидаты на чистку (мусорные корни, Rust-ядро):")
                agg: dict[str, dict] = {}

                def on_file(fe) -> None:
                    for cat in fe.categories or ["(без категории)"]:
                        slot = agg.setdefault(
                            cat, {"category": cat, "files": 0, "bytes": 0, "lane": "?", "risk": "?"}
                        )
                        slot["files"] += 1
                        slot["bytes"] += fe.size

                data = client.candidates(roots or [], cat_roots=not roots,
                                         on_progress=on_progress, on_file=on_file)
                meta = client.cat_meta()
                for slot in agg.values():
                    m = meta.get(slot["category"])
                    if m:
                        slot["risk"] = m.get("risk", "?")
                        slot["lane"] = m.get("lane", "?")
                by_cat = sorted(agg.values(), key=lambda x: -x["bytes"])
                counts = {"files": data.get("files", 0), "bytes": data.get("bytes", 0)}
                report = {"candidates": data, "by_category": by_cat}
                data = {**data, "by_category": by_cat}
            else:
                print("Индекс диска (Rust-ядро):")
                data = client.index(roots or [], top=15, on_progress=on_progress)
                res = data["result"]
                counts = {"files": res["files"], "bytes": res["bytes"]}
                report = data

            if as_json:
                import json

                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 0

            print(f"файлов: {counts['files']}, размер: {_human(counts['bytes'])}")
            if not candidates:
                for f in data["top_folders"][:10]:
                    print(f"  папка {_human(f['bytes']):>10}  {f['path']}")
                print("самые крупные файлы:")
                for f in data["top_files"][:10]:
                    print(f"  файл  {_human(f['size']):>10}  {f['path']}")
                print("по расширениям:")
                for e in data["by_ext"][:8]:
                    print(f"  {e['ext'] or '(без расширения)':<12} {e['files']:>8}  {_human(e['bytes'])}")
            else:
                print("по категориям:")
                by_cat = {c["category"]: c for c in data.get("by_category", [])}
                if not by_cat:
                    print("  пусто")
                for c in sorted(by_cat.values(), key=lambda x: -x["bytes"])[:15]:
                    print(f"  {c['category']:<24} {c['files']:>8}  {_human(c['bytes']):>10}  {c['risk']}")

            if explain:
                from ai import AiAssistant, load_settings

                assistant = AiAssistant(load_settings())
                if not assistant.available():
                    print("\nAI: недоступен (выключен в настройках или сервер не отвечает) — объясняю молчанием.")
                    return 0
                text = assistant.explain(report)
                print("\nКлини объясняет:")
                print(f"  {text}" if text else "  (пусто)")
        return 0
    except SwagscanError as e:
        print(f"ядро сообщило об ошибке: {e}")
        return 1


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

    if args.disk is not None:
        roots = [str(Path(p).resolve()) for p in args.disk] if args.disk else []
        return _cli_disk(roots, args.candidates, args.explain, args.json)

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
