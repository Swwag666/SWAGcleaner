"""Карантин SWAGcleaner: удаление через просматриваемую папку.

Удаление «в никуда» не даёт второго шанса. С карантином файлы сначала
переезжают в собственную папку программы с сохранением структуры путей:
C:\\Users\\x\\Temp\\foo.tmp превращается в quarantine/<батч>/C/Users/x/Temp/foo.tmp.
Пользователь открывает папку, смотрит всё глазами, потом чистит карантин
целиком — или отдельными записями из окна карантина: восстановить на место,
стереть навсегда. Манифест батча хранит точные исходные пути и (для дублей)
группы.

Папка карантина настраивается (настройки → карантин): по умолчанию
quarantine\\ рядом с самой программой (в собранном exe - возле
SWAGcleaner.exe, в исходниках - у корня проекта), но можно указать
отдельный диск.

Если файл не перемещается — места на томе карантина под копию не хватает
или файл занят — он остаётся на месте, а его путь попадает в манифест и
в отчёт удаления: что не смогли убрать, видно явно, а не молча.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_UNSAFE = re.compile(r'[<>:"|?*\x00-\x1f]')

# Папка карантина, выбранная пользователем (None = место по умолчанию).
# Выставляет окно при старте и при смене настройки.
_override: Optional[str] = None


def set_root(path: Optional[str]) -> None:
    """Сменить папку карантина (пустая строка/None = место по умолчанию)."""
    global _override
    _override = str(path).strip() or None


def _default_root() -> Path:
    """Карантин по умолчанию - quarantine/ рядом с самой программой.

    В собранном exe папка ложится возле исполняемого файла: карантин
    видно рядом с программой, его легко открыть и он переезжает вместе
    с приложением. В исходниках - у корня проекта.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "quarantine"


def root() -> Path:
    """Папка карантина: выбранная пользователем или quarantine/ у программы."""
    if _override:
        return Path(_override)
    return _default_root()


def new_batch() -> Path:
    """Новая папка батча: по времени, с суффиксом при коллизии."""
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    batch = root() / stamp
    n = 2
    while batch.exists():
        batch = root() / f"{stamp}_{n}"
        n += 1
    batch.mkdir(parents=True, exist_ok=True)
    return batch


def _stash_path(batch: Path, src: str) -> Path:
    """Безопасный путь внутри батча, повторяющий исходную структуру.

    Диск C: становится папкой C, недопустимые в именах символы
    заменяются на подчёркивание — путь читается глазами и ищется.
    """
    norm = src.replace("/", "\\")
    parts = [p for p in norm.split("\\") if p not in ("", ".")]
    safe: List[str] = []
    for i, p in enumerate(parts):
        cleaned = _UNSAFE.sub("_", p).strip()
        if i == 0 and cleaned.endswith("_"):
            cleaned = cleaned.rstrip("_") or "DISK"
        safe.append(cleaned or "_")
    return batch.joinpath(*safe) if safe else batch / "_unnamed"


def drive_of(path: str) -> str:
    """Том пути в нижнем регистре — для сравнения «тот же диск?»."""
    return os.path.splitdrive(os.path.abspath(path))[0].lower()


def free_bytes(path: str) -> int:
    """Свободное место на томе (0 = не смогли спросить)."""
    try:
        free = ctypes.c_ulonglong(0)
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            os.path.abspath(path), None, None, ctypes.byref(free))
        return int(free.value) if ok else 0
    except Exception:  # noqa: BLE001 - не Windows или том отвалился
        return 0


def stash_file(src: str, batch: Path) -> Tuple[Optional[Path], str]:
    """Перенести файл в карантин.

    Возвращает (путь внутри карантина, причина отказа). Успех — путь
    и пустая причина; неудача — None и человекочитаемая причина:
    «нет файла», «нет места», «файл занят». Перенос внутри одного
    тома — мгновенный rename; между томами — копия со свободным
    местом под неё, иначе честный отказ без молчаливых потерь.
    """
    if not os.path.isfile(src):
        return None, "файл уже не существует"
    dst = _stash_path(batch, src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        stem, dot, ext = dst.name.partition(".")
        n = 2
        while dst.exists():
            dst = dst.with_name(f"{stem}~{n}" + (f".{ext}" if dot else ""))
            n += 1
    try:
        size = os.path.getsize(src)
    except OSError:
        return None, "размер не прочитан"
    same_volume = drive_of(src) == drive_of(str(batch))
    if not same_volume:
        free = free_bytes(str(batch))
        # Запас х2: копия может идти медленно, место тем временем уходит.
        if free and free < size * 2:
            return None, "нет места под копию в карантине"
    try:
        os.replace(src, str(dst))
        return dst, ""
    except OSError:
        pass
    try:
        shutil.move(src, str(dst))
        return dst, ""
    except (OSError, shutil.Error) as e:
        return None, f"не перенёсся: {e.__class__.__name__}"


def write_manifest(
    batch: Path,
    kind: str,
    manifest: Sequence[Dict[str, Any]],
    groups: Optional[Sequence[Dict[str, Any]]] = None,
) -> Path:
    """Манифест батча: что, откуда и (для дублей) как группировалось.

    manifest — список {path, category, stashed}: каждый перенесённый файл
    с исходным путём и путём внутри карантина. Старые манифесты (список
    прямо в поле groups) читаются тоже — записи без stashed восстановить
    нельзя, но удалить навсегда можно.
    """
    data: Dict[str, Any] = {
        "ts": time.time(),
        "kind": kind,
        "manifest": [
            {
                "path": str(item.get("path", "")),
                "category": str(item.get("category", "")),
                "stashed": str(item.get("stashed", "")),
            }
            for item in manifest
        ],
    }
    if groups:
        data["groups"] = list(groups)
    path = batch / "manifest.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _read_manifest(batch: Path) -> Dict[str, Any]:
    try:
        data = json.loads(
            (batch / "manifest.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Записи манифеста в едином виде — новые и старые форматы.

    Новый формат: {manifest: [{path, category, stashed}]}.
    Старый (чистка): список таких же словарей прямо в «groups».
    Старый (дубли): {groups: [{hash, size, keep, removed: [...]}]} —
    stashed нет, восстановление невозможно, только стирание.
    """
    items = data.get("manifest")
    if isinstance(items, list) and items and isinstance(items[0], dict) \
            and "stashed" in items[0]:
        return [dict(x) for x in items]
    legacy = data.get("groups")
    if isinstance(legacy, list) and legacy and isinstance(legacy[0], dict):
        first = legacy[0]
        if "stashed" in first:
            return [dict(x) for x in legacy]
        out: List[Dict[str, Any]] = []
        for group in legacy:
            keep = str(group.get("keep", ""))
            for path in group.get("removed", []):
                out.append({
                    "path": str(path),
                    "category": "dupes.photo",
                    "stashed": "",
                    "keep": keep,
                })
        return out
    return []


def entries() -> List[Dict[str, Any]]:
    """Все записи карантина: батч за батчем, из манифестов.

    Каждая запись: batch (папка батча), batch_name, kind, path (откуда
    файл приехал), stashed (где лежит в карантине, может быть пусто у
    старых манифестов дублей), category, size (байт, 0 если файла нет).
    """
    out: List[Dict[str, Any]] = []
    base = root()
    if not base.exists():
        return out
    try:
        batches = sorted(
            (p for p in base.iterdir() if p.is_dir()),
            key=lambda p: p.name, reverse=True)
    except OSError:
        return out
    for batch in batches:
        data = _read_manifest(batch)
        kind = str(data.get("kind", ""))
        items = _manifest_items(data)
        if not items:
            # Манифеста нет или пустой: файлы в батче всё равно покажем —
            # удалять их можно и без описи.
            try:
                files = [
                    os.path.join(dp, f)
                    for dp, _ds, fs in os.walk(batch)
                    for f in fs if f != "manifest.json"
                ]
            except OSError:
                files = []
            for stashed in files:
                out.append({
                    "batch": str(batch),
                    "batch_name": batch.name,
                    "kind": kind,
                    "path": "",
                    "category": "",
                    "stashed": stashed,
                    "size": _safe_size(stashed),
                })
            continue
        for item in items:
            stashed = str(item.get("stashed", ""))
            out.append({
                "batch": str(batch),
                "batch_name": batch.name,
                "kind": kind,
                "path": str(item.get("path", "")),
                "category": str(item.get("category", "")),
                "stashed": stashed,
                "size": _safe_size(stashed),
            })
    return out


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def restore(entry: Dict[str, Any]) -> Tuple[bool, str]:
    """Вернуть файл из карантина на исходное место.

    Отказ, а не перезапись: если на исходном пути уже кто-то живёт,
    файл остаётся в карантине, причина уходит вызывающему. Успех
    убирает запись из манифеста батча: окно карантина не показывает
    фантомов после восстановления.
    """
    stashed = str(entry.get("stashed", ""))
    original = str(entry.get("path", ""))
    if not stashed or not os.path.isfile(stashed):
        return False, "файла уже нет в карантине"
    if not original:
        return False, "исходный путь неизвестен"
    if os.path.exists(original):
        return False, "на исходном месте уже есть файл"
    target_dir = os.path.dirname(original)
    try:
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        shutil.move(stashed, original)
    except (OSError, shutil.Error) as exc:
        return False, f"не перенёсся: {exc.__class__.__name__}"
    _forget_entry(entry)
    _prune_empty_dirs(os.path.dirname(stashed))
    return True, ""


def delete_entry(entry: Dict[str, Any]) -> Tuple[bool, str]:
    """Стереть файл из карантина навсегда. Батч-папку подчищаем следом."""
    stashed = str(entry.get("stashed", ""))
    if stashed and os.path.exists(stashed):
        try:
            os.remove(stashed)
        except OSError as exc:
            return False, f"не стёрся: {exc.__class__.__name__}"
    _forget_entry(entry)
    if stashed:
        _prune_empty_dirs(os.path.dirname(stashed))
    return True, ""


def _forget_entry(entry: Dict[str, Any]) -> None:
    """Убрать запись из манифеста батча (файл уже ушёл или стёрт).

    Пустой батч удаляем целиком: окно карантина показывает только то,
    что реально лежит. Опись переписывается атомарно - поверх старой.
    """
    batch = str(entry.get("batch", ""))
    if not batch:
        return
    batch_path = Path(batch)
    data = _read_manifest(batch_path)
    if not data:
        # Манифеста не было (сиротский файл): после удаления файла батч
        # либо опустел и уйдёт, либо в нём остались другие файлы.
        _drop_batch_if_empty(batch_path)
        return
    stashed = str(entry.get("stashed", ""))
    original = str(entry.get("path", ""))
    items = _manifest_items(data)
    kept = [
        item for item in items
        if (str(item.get("stashed", "")) != stashed if stashed
            else str(item.get("path", "")) != original)
    ]
    if kept:
        data["manifest"] = kept
        data.pop("groups", None)
        try:
            (batch_path / "manifest.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass
        return
    _drop_batch_if_empty(batch_path)


def _drop_batch_if_empty(batch: Path) -> None:
    """Удалить батч, когда в нём не осталось файлов кроме манифеста."""
    try:
        leftovers = [p for p in batch.rglob("*") if p.is_file()
                     and p.name != "manifest.json"]
        if not leftovers:
            shutil.rmtree(batch, ignore_errors=True)
    except OSError:
        pass


def delete_batch(batch: str) -> Tuple[bool, int]:
    """Стереть батч карантина целиком. Возвращает (успех, байты)."""
    base = root()
    try:
        batch_path = Path(batch)
        if batch_path.parent != base:
            return False, 0
    except (ValueError, OSError):
        return False, 0
    freed = 0
    for dirpath, _dirs, files in os.walk(batch):
        for f in files:
            if f == "manifest.json":
                continue
            freed += _safe_size(os.path.join(dirpath, f))
    try:
        shutil.rmtree(batch, ignore_errors=True)
    except OSError:
        pass
    return True, freed


def _prune_empty_dirs(start: str) -> None:
    """Убрать пустые папки внутри карантина после восстановления/стирания."""
    try:
        base = str(root())
        current = Path(start)
        while str(current).startswith(base) and current != Path(base):
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except (OSError, ValueError):
        pass


def stats() -> Tuple[int, int]:
    """(байт, батчей) в карантине прямо сейчас.

    manifest.json не считается: это служебная опись, а не контент -
    освобождение должно совпадать с тем, что туда переезжало.
    """
    total = 0
    batches = 0
    base = root()
    if not base.exists():
        return 0, 0
    try:
        for child in base.iterdir():
            if child.is_dir():
                batches += 1
            for dirpath, _dirs, files in os.walk(child):
                for f in files:
                    if dirpath == str(child) and f == "manifest.json":
                        continue
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
    except OSError:
        pass
    return total, batches


def clear() -> int:
    """Стереть карантин целиком. Возвращает освобождённые байты."""
    freed, _ = stats()
    base = root()
    if base.exists():
        for child in list(base.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass
    return freed


def open_in_explorer(path: Optional[Path] = None) -> None:
    """Открыть карантин (или папку внутри) в проводнике."""
    target = path if path is not None else root()
    target.mkdir(parents=True, exist_ok=True)
    os.startfile(str(target))  # noqa: S606 - Windows-путь открытия папки
