# cleanpilot.spec — спецификация PyInstaller для SWAGcleaner.
# Собирает приложение в один исполняемый файл:
#   - GUI (при запуске без аргументов)
#   - CLI --scan / --advisor (без UI)
#
# Иконка и манифест Windows — позже, когда есть файлы.
#
# Примечание: PyInstaller 6.10+ требуется для корректной работы с PySide6.

from __future__ import annotations

import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Основные пакеты, которые нужно упаковать.
packages: list[str] = []

# Подмодули, которые подхватываются через collect_all.
hidden_imports: list[str] = []


def _collect(package: str) -> tuple[list[str], list[str], list[str]]:
    datas, binaries, imports = collect_all(package)
    return datas, binaries, imports


def build_spec() -> None:
    # Убедиться, что PySide6 и его компоненты собраны.
    try:
        from PyInstaller.utils.hooks import collect_all
    except ImportError:
        print("ERROR: PyInstaller 6.10+ required")
        exit(1)

    # Собрать всё из PySide6, shiboken6 и пакетов приложения.
    pyside6_packages: list[str] = []
    for pkg in ("PySide6", "shiboken6", "core", "ui", "services"):
        try:
            datas, binaries, imports = _collect(pkg)
            hidden_imports.extend(imports)
            for d in datas:
                if d not in globals().get("_collect_datas", []):
                    globals().setdefault("_collect_datas", []).append(d)
            for b in binaries:
                if b not in globals().get("_collect_binaries", []):
                    globals().setdefault("_collect_binaries", []).append(b)
        except Exception as e:
            print(f"WARNING: cannot collect {pkg}: {e}")

    # Базовый Analysis.
    a = Analysis(
        ["swagcleaner.py"],
        pathex=[],
        binaries=globals().get("_collect_binaries", []),
        datas=globals().get("_collect_datas", []),
        hiddenimports=hidden_imports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=None,
        noarchive=False,
    )

    # EXE — консольный (CLI) + without console (GUI) в одном файле.
    # Для Windows можно собрать два варианта, но здесь — один exe,
    # который сам определяет режим по аргументам.
    exe = EXE(
        a,
        name="SWAGcleaner",
        debug=False,
        bootloader_ignore_signals=False,
        strip=None,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,  # консоль для CLI; GUI запускается без --scan/--advisor
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )

    # COLLECT — для управления файлами (иконки, манифест, i18n).
    # Мы собираем в один файл, поэтому collect здесь не обязателен,
    # но для i18n лучше добавить данные как binary/datas через Analysis.
    collect = COLLECT(
        exe,
        a.datas,
        a.binaries,
        name="SWAGcleaner",
        upx=True,
        upx_exclude=[],
        distpath="dist",
        runtime_tmpdir=None,
    )

    return collect


if __name__ == "__main__":
    build_spec()
