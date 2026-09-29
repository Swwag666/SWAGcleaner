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
from pathlib import Path

# Rust-ядро кладём рядом с приложением: Python-мост ищет его в bin/ и в корне
# сборки. Без него приложение остаётся на Python-обходе — мост это переживёт.
_rust_binaries = []
for _p in (
    Path("rust/swagscan/target/release/swagscan.exe"),
    Path("rust/swagscan/target/x86_64-pc-windows-gnu/release/swagscan.exe"),
    Path("bin/swagscan.exe"),
):
    if _p.exists():
        _rust_binaries.append((str(_p), "."))
        break

# Строки локализации и пиксельный шрифт кладём явно: это не модули,
# автоматически они в сборку не попадут (и молча отвалятся).
datas = [
    ("ui/i18n/ru.json", "ui/i18n"),
    ("ui/i18n/en.json", "ui/i18n"),
    # База системных твиков: без неё страница твиков потеряет секцию «Твики
    # системы» (core/tweaks.py ищет её рядом с собой).
    ("core/tweaks_db.json", "core"),
    ("core/tweak_presets.json", "core"),
    # MAS-скрипты активации - вкладываются как есть, запускает core/activation.py
    ("assets/activation/*.cmd", "assets/activation"),
    ("assets/fonts/Handjet.ttf", "assets/fonts"),
    ("assets/fonts/OFL.txt", "assets/fonts"),
    # Картинки помощницы: каждая поза — отдельный файл. Исходники в
    # assets/character/raw в сборку не идут: они в разы тяжелее.
    ("assets/character/*.png", "assets/character"),
    # Протагонист «числовой» темы — отдельный набор поз в подпапке num.
    ("assets/character/num/*.png", "assets/character/num"),
]

# Пакет ui — обычный, но PyInstaller о нём знает только через эти импорты.
hiddenimports = [
    "ui",
    "ui.character",
    "ui.context",
    "ui.dialog",
    "ui.main",
    "ui.sounds",
    "ui.tabs",
    "ui.theme",
    "ui.widgets",
    "ui.workers",
    "core",
    "core.swagscan",
]

onedir = os.environ.get("SWAGCLEANER_ONEDIR") == "1"

a = Analysis(
    ["swagcleaner.py"],
    pathex=["."],
    binaries=_rust_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Лишние тяжёлые модули в сборке не нужны.
    excludes=[
        "tkinter", "unittest", "pydoc_data", "sqlite3",
        # Тестовый стек приезжает транзитом: numpy.testing тянет pytest, а тот
        # за собой pygments. В рантайме приложения им делать нечего, но без
        # явного исключения они ложатся в PYZ сотнями модулей.
        "pytest", "_pytest", "py", "pygments",
        # Приложение целиком живёт на QtCore/QtGui/QtWidgets — ни одного
        # обращения к QML, Quick, PDF, OpenGL, мультимедиа, 3D, графикам,
        # геолокации и Bluetooth в исходниках нет.
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
        "PySide6.QtQuickControls2", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtOpenGLWindow",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
        "PySide6.QtWebSockets", "PySide6.QtHttpServer", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtGraphs",
        "PySide6.QtGraphsWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
        "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras", "PySide6.QtPositioning", "PySide6.QtLocation",
        "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtRemoteObjects",
        "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
        "PySide6.QtSpatialAudio", "PySide6.QtTest", "PySide6.QtUiTools",
        "PySide6.QtVirtualKeyboard",
    ],
    noarchive=False,
    optimize=0,
)

# Хук PySide6 тащит DLL за плагинами, которые сами по себе весят копейки, но
# подтягивают тяжёлые библиотеки. Вырезаем эти листья вместе с их грузом:
#   qtvirtualkeyboardplugin -> Qt6VirtualKeyboard -> Qt6Quick + Qt6Qml (+Models,
#       Meta, WorkerScript)                          ~13 МБ
#   imageformats\qpdf       -> Qt6Pdf                ~4,5 МБ
#   opengl32sw.dll — программный бэкенд OpenGL, Widgets-приложение рисует
#       через raster и он ему не нужен               ~19,7 МБ
#   qdirect2d.dll — запасная платформенная библиотека рядом с qwindows.dll
#   _avif — AVIF-кодек Pillow; в приложении только PNG/JPEG
# qoffscreen.dll и qminimal.dll НЕ трогаем: на offscreen работает --self-test.
_DROP_SUBSTR = (
    "opengl32sw.dll",
    "qdirect2d.dll",
    "qtvirtualkeyboardplugin.dll",
    "qt6virtualkeyboard.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6qmlintegration.dll",
    "imageformats\\qpdf.dll",
    "qt6pdf.dll",
    "_avif.",
)


def _keep(entry) -> bool:
    dest = str(entry[0]).lower().replace("/", "\\")
    return not any(needle in dest for needle in _DROP_SUBSTR)


_dropped = [str(b[0]) for b in a.binaries if not _keep(b)]
a.binaries = [b for b in a.binaries if _keep(b)]
if _dropped:
    print(f"[spec] вырезано тяжёлых бинарников: {len(_dropped)}")
    for _name in _dropped:
        print(f"[spec]   - {_name}")

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
        uac_admin=True,
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
        uac_admin=True,
    )
