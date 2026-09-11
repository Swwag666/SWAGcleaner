# -*- mode: python ; coding: utf-8 -*-
"""Единая спецификация PyInstaller для SWAGcleaner.

Собирает приложение в оконном режиме — без чёрного окна консоли.
Внутрь кладём ядро (core), интерфейс (ui) и файлы локализации
ui/i18n/*.json: без них интерфейс остался бы без строк.

Два режима сборки:
    python -m PyInstaller SWAGcleaner.spec
        один файл — dist/SWAGcleaner.exe (для передачи и запуска «как есть»);
    SWAGCLEANER_ONEDIR=1 python -m PyInstaller SWAGcleaner.spec
        папка dist/SWAGcleaner/ — стартует заметно быстрее, удобно, пока
        доводишь интерфейс.

Имена Analysis, PYZ, EXE и COLLECT подставляет сам PyInstaller прямо в
пространство имён спецификации — импортировать их не нужно (и нельзя).
"""

import os

# Строки локализации и пиксельный шрифт кладём явно: это не модули,
# автоматически они в сборку не попадут (и молча отвалятся).
datas = [
    ("ui/i18n/ru.json", "ui/i18n"),
    ("ui/i18n/en.json", "ui/i18n"),
    ("assets/fonts/Handjet.ttf", "assets/fonts"),
    ("assets/fonts/OFL.txt", "assets/fonts"),
]

# Пакет ui — обычный, но PyInstaller о нём знает только через эти импорты.
hiddenimports = [
    "ui",
    "ui.context",
    "ui.main",
    "ui.theme",
    "ui.tabs",
    "ui.workers",
    "core",
]

onedir = os.environ.get("SWAGCLEANER_ONEDIR") == "1"

a = Analysis(
    ["swagcleaner.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Лишние тяжёлые модули в сборке не нужны.
    excludes=["tkinter", "unittest", "pydoc_data", "sqlite3"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if onedir:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SWAGcleaner",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="SWAGcleaner",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="SWAGcleaner",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        # UPX выключен намеренно: он медленный и портит Qt-библиотеки.
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=None,
    )
