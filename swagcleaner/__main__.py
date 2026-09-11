"""Точка входа python -m swagcleaner."""
from __future__ import annotations

import sys
from pathlib import Path

# Убедиться, что корень проекта в sys.path, чтобы импорты сработали.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from swagcleaner import main

sys.exit(main())


if __name__ == "__main__":
    # Запускать как скрипт из подпапки (например из редактора) — не привычный путь,
    # но полезно при локальном тестировании без создания пакета.
    sys.exit(main())
