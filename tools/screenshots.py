"""Снять скриншоты интерфейса в offscreen-режиме.

Запуск из корня проекта:

    QT_QPA_PLATFORM=offscreen ./venv/Scripts/python.exe tools/screenshots.py

Картинки складываются в shots/ (папка в .gitignore): их удобно смотреть
глазами, не запуская приложение. Настройки интерфейса при этом не трогаются —
язык, тема и шрифт уводятся во временную папку, а не в реальные настройки
пользователя.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402

# Изоляция настроек: прогон не должен менять выбор языка и темы у пользователя.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="swagcleaner-shots-")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui import theme  # noqa: E402
from ui.character import MOODS, Mascot  # noqa: E402
from ui.context import Context, init_context  # noqa: E402
from ui.dialog import ConfirmDialog  # noqa: E402
from ui.main import MainWindow  # noqa: E402

SHOTS_DIR = _ROOT / "shots"
# Сколько ждать, пока реплика допечатается (по 24 мс на букву плюс запас).
TYPE_WAIT_MS = 2600


def shot(widget, name: str) -> None:  # noqa: ANN001
    SHOTS_DIR.mkdir(exist_ok=True)
    widget.grab().save(str(SHOTS_DIR / f"{name}.png"))
    print("сняли", name)


def mood_strip(context: Context) -> None:
    """Полоса со всеми настроениями: сразу видно, где поза своя, а где откат."""
    cell_w, cell_h = 210, 380
    image = QImage(cell_w * len(MOODS), cell_h, QImage.Format.Format_ARGB32)
    image.fill(QColor(theme.palette("dark")["bg_base"]))
    painter = QPainter(image)
    painter.setFont(theme.font_for("pixel", "dark"))
    painter.setPen(QColor(theme.palette("dark")["text_primary"]))
    for index, mood in enumerate(MOODS):
        mascot = Mascot()
        mascot.set_mood(mood)
        mascot.resize(cell_w, cell_h - 48)
        painter.drawPixmap(index * cell_w, 44, mascot.grab())
        painter.drawText(
            index * cell_w + 14,
            28,
            f"{mood} — {context.tr(f'character.moods.{mood}')}",
        )
    painter.end()
    image.save(str(SHOTS_DIR / "12-moods.png"))
    print("сняли 12-moods")


# Демонстрационные пункты окна подтверждения: то же, что показывают кнопки.
DEMO_ITEMS = (("tmp", "low"), ("startup", "medium"), ("photo", "high"))


def demo_dialog(win: MainWindow, context: Context) -> ConfirmDialog:
    """Собрать окно подтверждения с демонстрационным списком действий."""
    items = [(context.tr(f"demo.items.{key}"), risk) for key, risk in DEMO_ITEMS]
    return ConfirmDialog(win, items)


def main() -> int:
    app = QApplication([])
    context = init_context(app)
    # Звук при съёмке выключен: скриншоты не должны пищать на машине.
    context.setSounds(False)
    win = MainWindow(app, context)
    win.resize(1100, 700)
    win.show()

    QTest.qWait(TYPE_WAIT_MS)
    shot(win, "01-dark-greeting")

    win._assistant.say(context.tr("character.lines.panic"), "panic")
    QTest.qWait(TYPE_WAIT_MS)
    shot(win, "02-dark-panic")

    win._assistant.say(context.tr("character.lines.calm"), "calm")
    QTest.qWait(TYPE_WAIT_MS)
    shot(win, "03-dark-calm")

    # Кадр на середине печати: видно, как текст появляется по буквам.
    win._assistant.say(context.tr("character.lines.dedup"), "think")
    QTest.qWait(400)
    shot(win, "04-typing-midframe")

    context.setTheme("light")
    win._assistant.say(context.tr("character.lines.advisor"), "scan")
    QTest.qWait(TYPE_WAIT_MS)
    shot(win, "05-light-scan")

    win.toggle_assistant()
    QTest.qWait(MainWindow.ASSISTANT_ANIMATION_MS + 220)
    shot(win, "06-assistant-hidden")
    win.toggle_assistant()
    QTest.qWait(MainWindow.ASSISTANT_ANIMATION_MS + 220)

    context.setLocale("en")
    win._assistant.say(context.tr("character.lines.hello"), "idle")
    QTest.qWait(TYPE_WAIT_MS)
    shot(win, "07-english")

    context.setLocale("ru")
    context.setTheme("dark")

    # Работа: бегунок в шапке, прогресс и растущие показатели.
    win.go_to_page(1)
    win._pages[1].scanRequested.emit()
    QTest.qWait(MainWindow.DEMO_WORK_MS // 2)
    shot(win, "08-busy-progress")
    QTest.qWait(MainWindow.DEMO_WORK_MS + 300)
    win._pages[1].stats().finish()
    shot(win, "09-results-counters")

    # Подтверждение в стиле ВН: затемнение, панель снизу, список действий.
    dialog = demo_dialog(win, context)
    dialog.show()
    QTest.qWait(ConfirmDialog.ANIMATION_MS + 140)
    shot(dialog, "10-confirm-dialog")
    dialog.cancel()
    dialog.deleteLater()

    win.go_to_page(4)
    QTest.qWait(600)
    shot(win, "11-settings-sounds")

    mood_strip(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
