"""Модуль бэкапов — сохраняет и восстанавливает снапшоты состояния.

Бэкапы хранятся в памяти в виде объектов и предназначены для последующего
восстановления после выполнения действий.
"""
import typing as t
import os
from pathlib import Path


class BackupStore:
    """Хранилище бэкапов: сохраняет снапшоты по имени и позволяет восстановить.

    Реализация в первой итерации — in-memory.
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._store: t.Dict[str, t.Any] = {}
        self._base_dir: Path | None = Path(base_dir) if base_dir else None

    def save(self, name: str, data: t.Any) -> None:
        if self._base_dir is not None:
            path = self._base_dir / f"{name}.bak"
            path.parent.mkdir(parents=True, exist_ok=True)
        self._store[name] = data

    def restore(self, name: str) -> t.Any | None:
        return self._store.get(name)

    def list(self) -> t.List[str]:
        return list(self._store.keys())
