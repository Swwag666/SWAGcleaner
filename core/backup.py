"""Модуль бэкапов — сохраняет и восстанавливает снапшоты состояния.

Снапшоты живут на диске (M5): %LOCALAPPDATA%\\SWAGcleaner\\backups\\<name>.json
и переживают перезапуск — страница твиков показывает «что можно вернуть»
даже спустя недели. Каждый снапшот — JSON с метаданными:
{"name": ..., "ts": ..., "kind": "startup_disable", "data": {...}}.

BackupStore(base_dir=None) — старый режим в памяти (для тестов, которые
не хотят трогать диск); BackupStore.disk() — настоящая папка профиля.
"""
from __future__ import annotations

import json
import os
import time
import typing as t
from pathlib import Path


def default_backups_dir() -> Path:
    """Папка снапшотов: %LOCALAPPDATA%\\SWAGcleaner\\backups."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "SWAGcleaner" / "backups"


class BackupStore:
    """Хранилище бэкапов: снапшоты по имени, диск или память.

    На диске каждый снапшот — отдельный JSON; битый файл не мешает
    остальным (пропускается при чтении и листинге).
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._store: t.Dict[str, t.Any] = {}
        self._base_dir: Path | None = Path(base_dir) if base_dir else None

    @classmethod
    def disk(cls) -> "BackupStore":
        """Настоящее хранилище в профиле пользователя."""
        return cls(default_backups_dir())

    def _file(self, name: str) -> Path:
        assert self._base_dir is not None
        return self._base_dir / f"{name}.json"

    def save(self, name: str, data: t.Any, kind: str = "generic") -> None:
        """Сохранить снапшот: в память и, если есть папка, на диск."""
        payload = {"name": name, "ts": time.time(), "kind": kind, "data": data}
        self._store[name] = data
        if self._base_dir is not None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file(name).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self._file(name))

    def restore(self, name: str) -> t.Any | None:
        """Вернуть данные снапшота (без удаления — снапшот остаётся)."""
        if name in self._store:
            return self._store[name]
        if self._base_dir is not None:
            try:
                payload = json.loads(
                    self._file(name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return payload.get("data")
        return None

    def remove(self, name: str) -> None:
        """Убрать снапшот (после успешного отката он не нужен)."""
        self._store.pop(name, None)
        if self._base_dir is not None:
            try:
                self._file(name).unlink()
            except OSError:
                pass

    def list(self) -> t.List[str]:
        """Имена всех снапшотов: память + диск (диск переживает запуски)."""
        names = set(self._store)
        if self._base_dir is not None and self._base_dir.is_dir():
            for file in self._base_dir.glob("*.json"):
                names.add(file.stem)
        return sorted(names)

    def info(self, name: str) -> t.Optional[t.Dict[str, t.Any]]:
        """Метаданные снапшота (kind, ts) для показа в списке откатов."""
        if self._base_dir is not None:
            try:
                payload = json.loads(
                    self._file(name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if payload is not None:
                return {"name": name, "ts": payload.get("ts", 0.0),
                        "kind": payload.get("kind", "generic")}
        if name in self._store:
            return {"name": name, "ts": 0.0, "kind": "generic"}
        return None
