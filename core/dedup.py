"""Модуль поиска дубликатов файлов — точные и визуальные (перцептуальные) хеши.

Для начальной итерации поддерживает изображения JPEG/PNG/etc через Pillow
и imagehash. Перцептуальный хеш позволяет находить визуально похожие фотографии,
даже если они немного отличаются.
"""
import typing as t
from pathlib import Path

import imagehash
from PIL import Image


class DuplicateScanner:
    """Сканер дубликатов — поддерживает точные и визуальные сравнения.

    exact_hash —algo хешей: digest MD5/SHA1 для бинарных файлов.
    perceptual_hash — алгоритм perceptual_hash для изображений.
    """

    def __init__(self, perceptual_threshold: float = 0.75) -> None:
        self._perceptual_threshold = perceptual_threshold

    def scan_exact_duplicates(self, paths: t.List[Path]) -> t.List[t.List[Path]]:
        """Возвращает группы файлов-дубликатов с одинаковым точным хешом."""
        hash_to_paths: t.Dict[str, t.List[Path]] = {}
        for path in paths:
            if not path.is_file():
                continue
            try:
                digest = _file_hash(path)
                hash_to_paths.setdefault(digest, []).append(path)
            except OSError:
                continue
        return [group for group in hash_to_paths.values() if len(group) > 1]

    def scan_perceptual_duplicates(self, paths: t.List[Path]) -> t.List[t.List[Path]]:
        """Возвращает группы визуально похожих изображений."""
        hash_to_paths: t.Dict[str, t.List[Path]] = {}
        for path in paths:
            if not path.is_file():
                continue
            try:
                h = _perceptual_hash(path)
                if h is None:
                    continue
                key = str(h)
                hash_to_paths.setdefault(key, []).append(path)
            except OSError:
                continue
        return [group for group in hash_to_paths.values() if len(group) > 1]

    def scan_similar(
        self, paths: t.List[Path], max_distance: int = 5
    ) -> t.List[t.List[Path]]:
        """Находит группы изображений с 지각적 расстоянием <= max_distance.

        Работает на основе хешей и возвращает кластеры с минимальным расстоянием.
        """
        hash_to_paths: t.Dict[str, t.List[Path]] = {}
        for path in paths:
            if not path.is_file():
                continue
            try:
                h = _perceptual_hash(path)
                if h is None:
                    continue
                key = str(h)
                hash_to_paths.setdefault(key, []).append(path)
            except OSError:
                continue

        all_hashes = list(hash_to_paths.keys())
        visited: t.Set[int] = set()
        clusters: t.List[t.List[Path]] = []
        for i, h1 in enumerate(all_hashes):
            if i in visited:
                continue
            cluster: t.List[Path] = []
            queue = [i]
            visited.add(i)
            while queue:
                idx = queue.pop()
                h_current = all_hashes[idx]
                try:
                    current_hash = imagehash.hex_to_hash(h_current)
                except (imagehash.ImageHash.HashError, ValueError):
                    continue
                for j in range(len(all_hashes)):
                    if j in visited:
                        continue
                    try:
                        other_hash = imagehash.hex_to_hash(all_hashes[j])
                    except (imagehash.ImageHash.HashError, ValueError):
                        continue
                    if current_hash - other_hash <= max_distance:
                        visited.add(j)
                        cluster.extend(hash_to_paths[all_hashes[j]])
                        queue.append(j)
            if cluster:
                clusters.append(cluster)
        return clusters


def _file_hash(path: Path) -> str:
    """Возвращает MD5-хеш файла для точного сравнения."""
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _perceptual_hash(path: Path) -> t.Optional[imagehash.ImageHash]:
    """Возвращает перцептуальный хеш изображения."""
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            return imagehash.phash(img)
    except Exception:
        return None
