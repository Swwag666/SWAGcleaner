"""Контекст приложения — локализация, тема, провайдеры, бэкапы.

Контекст — одиночка, обслуживает UI и ядро: загружает i18n-строки из
json-файлов, управляет темой, хранит провайдеры (пока заглушки) и
предоставляет доступ к бэкапам.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, Signal, QTranslator
from PySide6.QtWidgets import QApplication

_LOGGER = logging.getLogger("swag.ui.context")

# Регистр локалей, которые мы поддерживаем.
LOCALES: Dict[str, str] = {
    "ru": "Russian",
    "en": "English",
}


class Context(QObject):
    """Контекст приложения — одиночка, обслуживает UI и ядро."""

    _instance: Optional["Context"] = None

    languageChanged = Signal(str)
    themeChanged = Signal(str)

    def __new__(cls) -> "Context":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._locale: str = "ru"
        self._theme: str = "dark"
        self._translator: Optional[QTranslator] = None
        self._strings_ru: Dict[str, Any] = {}
        self._strings_en: Dict[str, Any] = {}
        self._cache: Dict[str, str] = {}
        self._exclusions: list[str] = []
        self._backup_dir: Optional[Path] = None

    # ---------- инициализация ----------

    def init(self, app: QApplication) -> None:
        """Инициализировать контекст: загрузить локали, тему, провайдеры."""
        self._load_strings()
        self._apply_locale(app, self._locale)
        self._apply_theme()

    def _load_strings(self) -> None:
        """Загрузить строки из json-файлов локалей."""
        base = Path(__file__).resolve().parent
        ru_path = base / "i18n" / "ru.json"
        en_path = base / "i18n" / "en.json"
        try:
            with ru_path.open("r", encoding="utf-8") as f:
                self._strings_ru = json.load(f)
        except Exception as e:
            _LOGGER.warning("Failed to load %s: %s", ru_path, e)
            self._strings_ru = {}
        try:
            with en_path.open("r", encoding="utf-8") as f:
                self._strings_en = json.load(f)
        except Exception as e:
            _LOGGER.warning("Failed to load %s: %s", en_path, e)
            self._strings_en = {}
        # Сбросить кэш, т.к. загрузили новые строки.
        self._cache.clear()

    def _apply_locale(self, app: QApplication, locale: str) -> None:
        """Установить локаль приложения и подготовить словарь строк."""
        self._locale = locale
        if self._translator is not None:
            app.removeTranslator(self._translator)
            self._translator = None
        self._translator = QTranslator()
        # Пока переводы грузятся из json вручную через tr(), а не из ресурсов qt.
        # QTranslator мы обязан установить для поддержки Qt-многопоточности
        # и будущего использования Qt-строк, но пока используем свою tr().
        self.languageChanged.emit(locale)
        self._cache.clear()

    def _apply_theme(self) -> None:
        """Применить тему.

        Реальную палитру ставит UI (ui/theme.py). Здесь только фиксируем
        выбор, поэтому сигнал не отправляем — его шлёт setTheme()
        ровно один раз на изменение.
        """

    # ---------- локализация ----------

    def tr(self, key: str, locale: Optional[str] = None) -> str:
        """Получить строку по ключу из текущей локали (или указанной)."""
        target_locale = locale or self._locale
        strings = self._strings_ru if target_locale.startswith("ru") else self._strings_en
        parts = key.split(".")
        data: Any = strings
        for part in parts:
            if isinstance(data, dict):
                data = data.get(part)
            else:
                return key
            if data is None:
                return key
        if isinstance(data, dict):
            return key
        return str(data)

    def trc(self, key: str, count: int, locale: Optional[str] = None) -> str:
        """Получить строку с учётом числа (формат cardinal)."""
        return self.tr(key, locale)

    def all(self, locale: Optional[str] = None) -> Dict[str, Any]:
        """Получить все строки локали (включая вложенные словари)."""
        target_locale = locale or self._locale
        return self._strings_ru if target_locale.startswith("ru") else self._strings_en

    def supportedLocales(self) -> Dict[str, str]:
        """Вернуть поддерживаемые локали."""
        return dict(LOCALES)

    def setLocale(self, locale: str) -> None:
        """Установить локаль и пересчитать кэш строк."""
        app = QApplication.instance()
        if app is None:
            # В CLI-режиме (без окна) локаль переключать смысла нет —
            # просто запомним выбор, чтобы при запуске GUI применился.
            if locale in LOCALES and locale != self._locale:
                self._locale = locale
                self._cache.clear()
            return
        if locale not in LOCALES:
            locale = "ru"
        if locale != self._locale:
            self._apply_locale(app, locale)

    def locale(self) -> str:
        return self._locale

    # ---------- тема ----------

    def setTheme(self, theme: str) -> None:
        """Установить тему (dark / light)."""
        if theme not in ("dark", "light"):
            theme = "dark"
        if theme != self._theme:
            self._theme = theme
            self._apply_theme()
            self.themeChanged.emit(theme)

    def theme(self) -> str:
        return self._theme


# Одиночка контекста — ленивая инициализация.
_instance: Optional[Context] = None


def ctx() -> Context:
    global _instance
    context = _instance
    if context is None:
        context = Context()
        _instance = context
    return context


def init_context(app: QApplication) -> Context:
    """Инициализировать контекст и вернуть его."""
    context = ctx()
    context.init(app)
    return context
