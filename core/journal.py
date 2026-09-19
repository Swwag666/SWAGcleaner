"""Журнал действий — каждое действие оставляет строку на диске.

Правило из context.md (8.4/9.7): «на каждое действие — строка в журнале:
что, когда, чем закончилось». Журнал — append-only JSONL-файл:
%LOCALAPPDATA%\\SWAGcleaner\\journal\\journal.jsonl. Он не заменяет экран
журнала после удаления (тот показывает последний отчёт), а копит историю
между запусками: удаления, отключения автозагрузки, откаты.

Формат строки (одна строка = один JSON):
{"ts": 1726000000.0, "kind": "purge", "outcome": "ok", "detail": "...", ...}

Класс не знает про Qt и используется из рабочих потоков: запись атомарна
на уровне одной строки и защищена замком.
"""
from __future__ import annotations

import json
import os
import threading
import time
import typing as t
from pathlib import Path


def default_journal_dir() -> Path:
    """Папка журнала: %LOCALAPPDATA%\\SWAGcleaner\\journal (создаётся лениво)."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "SWAGcleaner" / "journal"


# Журнал выше этого размера уезжает в journal.1.jsonl (одно поколение).
_MAX_BYTES = 2 * 1024 * 1024


class Journal:
    """Append-only журнал действий в JSONL-файл.

    path=None → настоящая папка профиля; тесты передают свой путь в tmp.
    """

    def __init__(self, path: t.Optional[Path | str] = None) -> None:
        self._path = Path(path) if path is not None \
            else default_journal_dir() / "journal.jsonl"
        self._lock = threading.Lock()

    def path(self) -> Path:
        return self._path

    def log(self, kind: str, detail: str, outcome: str = "ok",
            **extra: t.Any) -> None:
        """Добавить строку: что за действие, чем закончилось, детали."""
        entry = {
            "ts": time.time(),
            "kind": kind,
            "outcome": outcome,
            "detail": detail,
            **extra,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self._path.stat().st_size > _MAX_BYTES:
                    # Ротация одним поколением: старый журнал -> .1.jsonl.
                    rotated = self._path.with_name(self._path.stem + ".1.jsonl")
                    os.replace(self._path, rotated)
            except OSError:
                pass
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, limit: int = 50) -> t.List[t.Dict[str, t.Any]]:
        """Последние записи, свежие первыми. Битые строки пропускаются.

        Читает файл С КОНЦА блоками, а не readlines() целиком: на большом
        журнале (годы работы) не грузит мегабайты ради 50 строк.
        """
        with self._lock:
            try:
                with open(self._path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    pos = fh.tell()
                    data = b""
                    # Добираем блоки, пока не наберётся limit+1 перевод строки.
                    while pos > 0 and data.count(b"\n") <= limit:
                        step = min(64 * 1024, pos)
                        pos -= step
                        fh.seek(pos)
                        data = fh.read(step) + data
            except OSError:
                return []
        entries: t.List[t.Dict[str, t.Any]] = []
        for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
            if len(entries) >= limit:
                break
        return entries
