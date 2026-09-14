"""Хранение настроек AI-слоя в JSON-файле.

Файл лежит в `%APPDATA%\\SWAGcleaner\\ai.json` (если APPDATA нет — берётся
LOCALAPPDATA, а если не было и его — пишется рядом с домашней папкой, чтобы
на не-Windows машинах тесты и запуск не падали).

Битый или нечитаемый JSON чинится молча: человек получает настройки по
умолчанию, а кривой файл перезаписывается при первом сохранении. Никаких
окон с текстом «не удалось разобрать конфиг» — это не разговор с пользователем.
"""
from __future__ import annotations

import logging
import os
import typing as t
from pathlib import Path

from .provider import AiSettings

LOGGER = logging.getLogger("swag.ai.config")

APP_DIR_NAME = "SWAGcleaner"
CONFIG_FILE_NAME = "ai.json"


def config_dir() -> Path:
    """Папка, где живёт конфиг: APPDATA → LOCALAPPDATA → домашняя."""
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            return Path(raw) / APP_DIR_NAME
    return Path.home() / ("." + APP_DIR_NAME.lower())


def config_path(path: t.Optional[Path | str] = None) -> Path:
    """Полный путь к файлу настроек; `path` перебивает всё — для тестов.

    Каталог понимается как каталог (в него кладётся `ai.json`), путь с расширением —
    как готовый файл. Так тесты могут передавать и tmp_path, и конкретный файл.
    """
    if path is None:
        return config_dir() / CONFIG_FILE_NAME
    candidate = Path(path)
    if candidate.is_dir():
        return candidate / CONFIG_FILE_NAME
    if candidate.suffix:
        return candidate
    return candidate / CONFIG_FILE_NAME


def load_settings(path: t.Optional[Path | str] = None) -> AiSettings:
    """Читает настройки AI; нет файла, битый файл, нет прав — дефолты.

    `path` нужен тестам и редким ручным проверкам: в UI его не передают.
    """
    target = config_path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AiSettings()
    except OSError as exc:
        LOGGER.warning("AI: конфиг %s не читается: %s", target, exc)
        return AiSettings()
    except UnicodeError as exc:
        LOGGER.warning("AI: конфиг %s в чужой кодировке: %s", target, exc)
        return AiSettings()
    return AiSettings.from_json(text)


def save_settings(settings: AiSettings, path: t.Optional[Path | str] = None) -> bool:
    """Сохраняет настройки. True — файл на месте и он читается обратно.

    Пишет через временный файл и переименованием: так даже при падении
    посреди записи у человека остаётся либо старый конфиг, либо новый.
    """
    target = config_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(settings.to_json(), encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        LOGGER.warning("AI: настройки не сохранились в %s: %s", target, exc)
        try:
            target.with_name(target.name + ".tmp").unlink()
        except OSError:
            pass
        return False
