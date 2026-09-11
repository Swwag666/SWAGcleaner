"""Модуль cleanpilot — спецификация PyInstaller для SWAGcleaner.

Если PyInstaller не установлен, модуль ничего не экспортирует.
При импорте без установленного PyInstaller модуль останется пустым.
"""
from __future__ import annotations

import sys

try:
    import PyInstaller  # noqa: F401
except ImportError:
    PyInstaller = None  # type: ignore[assignment]

if PyInstaller is not None:
    try:
        from PyInstaller.build_api import Analysis, EXE, COLLECT  # noqa: F401
    except ImportError:
        try:
            from PyInstaller.building.api import Analysis, EXE, COLLECT  # noqa: F401
        except ImportError:
            sys.exit("PyInstaller >= 6.0 is required (Analysis, EXE, COLLECT).")
    from cleanpilot.cleanpilot_spec import build_spec  # noqa: F401
