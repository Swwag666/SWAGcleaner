"""Модуль чистки — сканирует и удаляет мусорные файлы/папки.

Первая итерация использует провайдер корзины и поддерживает только
безопасное удаление (в корзину, не окончательно).
"""
import os
import typing as t
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CleanCandidate:
    """Кандидат на очистку — файл или папка.

    size_bytes — объём, который программа очистит при удалении.
    reason — причина добавления в список.
    """
    path: Path
    size_bytes: int
    reason: str


class Cleaner:
    """Чистильщик — сканирует указанные пути на наличие мусора.

    Поддерживает только безопасное удаление в корзину.
    """

    def __init__(self, trash_provider: "TrashProvider | None" = None) -> None:
        self._trash = trash_provider

    def scan(self, paths: t.List[Path], exclude_patterns: t.List[str] | None = None) -> t.List[CleanCandidate]:
        """Сканирует пути и возвращает список кандидатов для очистки.

        exclude_patterns — исключаемые суффиксы/префиксы имён файлов/папок.
        """
        candidates: t.List[CleanCandidate] = []
        exclude = {p.lower() for p in (exclude_patterns or [])}
        for path in paths:
            if not path.exists():
                continue
            for child in _walk_candidates(path, exclude):
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0
                candidates.append(
                    CleanCandidate(
                        path=child,
                        size_bytes=size,
                        reason="мусорный файл/папка",
                    )
                )
        return candidates

    def clean(self, candidates: t.List[CleanCandidate]) -> t.List[t.Dict[str, t.Any]]:
        """Удаляет кандидатов в корзину.

        Возвращает список результатов удаления.
        """
        results: t.List[t.Dict[str, t.Any]] = []
        for c in candidates:
            try:
                if self._trash is not None:
                    self._trash.send_to_trash(str(c.path))
                    results.append({"path": str(c.path), "success": True})
                else:
                    results.append({"path": str(c.path), "success": False, "error": "Нет провайдера корзины"})
            except Exception as e:
                results.append({"path": str(c.path), "success": False, "error": str(e)})
        return results


def _walk_candidates(root: Path, exclude: t.Set[str]) -> t.Iterator[Path]:
    """Обходит дерево и фильтрует кандидатов для очистки.

    Первая итерация просто пропускает файлы, попадающие под exclude.
    """
    try:
        for entry in root.rglob("*"):
            name_lower = entry.name.lower()
            if name_lower in exclude:
                continue
            yield entry
    except OSError:
        return
