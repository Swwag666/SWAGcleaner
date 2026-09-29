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

from PySide6.QtCore import QObject, QSettings, QTranslator, Qt, Signal
from PySide6.QtWidgets import QApplication

from ui.theme import ACCENT_IDS, FONT_KINDS, THEMES, register_bundled_fonts

_LOGGER = logging.getLogger("swag.ui.context")

# Регистр локалей, которые мы поддерживаем.
LOCALES: Dict[str, str] = {
    "ru": "Russian",
    "en": "English",
}

# Уровни анимации: «playful» — частицы/глитч/параллакс, «restrained» —
# только плавные фейды и базовые hover.
MOTION_LEVELS: tuple = ("playful", "restrained")

# Режимы темы: system — за ОСью, остальные — явный выбор.
THEME_MODES: tuple = ("system", "dark", "light", "mono", "num")


class Context(QObject):
    """Контекст приложения — одиночка, обслуживает UI и ядро."""

    _instance: Optional["Context"] = None

    languageChanged = Signal(str)
    themeChanged = Signal(str)
    fontChanged = Signal(str)
    # Реплика персонажа и его настроение. Ядру и страницам не нужно знать про
    # виджеты: достаточно попросить контекст — окно покажет реплику само.
    speechRequested = Signal(str)
    assistantMoodRequested = Signal(str)
    # Пиксельные звуки интерфейса: включаются и выключаются в настройках.
    soundsChanged = Signal(bool)
    # Акцентная схема (синий/фиолет/изумруд) и интенсивность анимаций.
    accentChanged = Signal(str)
    motionChanged = Signal(str)
    # Счёт чистоты: растёт после каждой успешной очистки, шкала 0-100.
    cleanlinessChanged = Signal(int)

    def __new__(cls) -> "Context":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self._locale: str = "ru"
        self._theme: str = "dark"
        self._theme_mode: str = "system"
        self._font_kind: str = "pixel"
        self._sounds: bool = True
        self._accent: str = "blue"
        self._motion: str = "playful"
        self._cleanliness: int = 0
        self._translator: Optional[QTranslator] = None
        self._strings_ru: Dict[str, Any] = {}
        self._strings_en: Dict[str, Any] = {}
        self._cache: Dict[str, str] = {}
        self._exclusions: list[str] = []
        self._backup_dir: Optional[Path] = None
        self._settings: Optional[QSettings] = None

    # ---------- инициализация ----------

    def init(self, app: QApplication) -> None:
        """Инициализировать контекст: шрифты, локали, сохранённый выбор."""
        register_bundled_fonts()
        self._load_strings()
        self._load_saved_choice()
        self._apply_locale(app, self._locale)
        # Тема от системы: тёмная/светлая решается ОСью, а не руками.
        self._resolve_theme()
        self._apply_theme()
        app.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)

    # ---------- сохранённые настройки ----------

    def _store(self) -> QSettings:
        """Хранилище настроек приложения (язык, тема, шрифт)."""
        if self._settings is None:
            self._settings = QSettings("SWAGcleaner", "SWAGcleaner")
        return self._settings

    def _remember(self, key: str, value: str) -> None:
        store = self._store()
        store.setValue(f"interface/{key}", value)
        store.sync()

    def _load_saved_choice(self) -> None:
        """Прочитать язык, режим темы, шрифт, акцент и уровень анимаций."""
        store = self._store()
        locale = str(store.value("interface/locale", self._locale))
        theme_mode = str(store.value("interface/theme_mode", self._theme_mode))
        font_kind = str(store.value("interface/font", self._font_kind))
        accent = str(store.value("interface/accent", self._accent))
        motion = str(store.value("interface/motion", self._motion))
        self._locale = locale if locale in LOCALES else "ru"
        self._theme_mode = theme_mode if theme_mode in THEME_MODES else "system"
        self._font_kind = font_kind if font_kind in FONT_KINDS else "pixel"
        self._accent = accent if accent in ACCENT_IDS else "blue"
        self._motion = motion if motion in MOTION_LEVELS else "playful"
        # Счёт чистоты копится между запусками.
        try:
            self._cleanliness = max(0, min(100, int(
                store.value("interface/cleanliness", 0))))
        except (TypeError, ValueError):
            self._cleanliness = 0
        # Флаг читаем строками и булевым: QSettings в ini-файле возвращает
        # строку, в реестре Windows — настоящее значение.
        sounds = store.value("interface/sounds", "true")
        self._sounds = str(sounds).strip().lower() not in ("", "0", "false", "no")

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
                self._remember("locale", locale)
            return
        if locale not in LOCALES:
            locale = "ru"
        if locale != self._locale:
            self._apply_locale(app, locale)
            self._remember("locale", locale)

    def locale(self) -> str:
        return self._locale

    # ---------- тема ----------

    def setTheme(self, theme: str) -> None:
        """Форсировать тему (dark / light / mono) - для тестов и отладки."""
        if theme not in THEMES:
            theme = "dark"
        if theme != self._theme:
            self._theme = theme
            self._apply_theme()
            self.themeChanged.emit(theme)

    def theme(self) -> str:
        return self._theme

    def themeMode(self) -> str:
        """Режим темы: system (авто ОС) или явный dark/light/mono."""
        return self._theme_mode

    def themeModes(self) -> tuple:
        return tuple(THEME_MODES)

    def setThemeMode(self, mode: str) -> None:  # noqa: N802
        """Выбрать режим темы и пересчитать текущую тему."""
        mode = str(mode)
        if mode not in THEME_MODES:
            mode = "system"
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._remember("theme_mode", mode)
        self._resolve_theme()

    def _system_theme(self) -> str:
        """Тёмная у пользователя ОСили светлая (по Qt colorScheme)."""
        app = QApplication.instance()
        if app is not None:
            try:
                scheme = app.styleHints().colorScheme()
                if scheme == Qt.ColorScheme.Light:
                    return "light"
                if scheme == Qt.ColorScheme.Dark:
                    return "dark"
            except Exception:
                pass
        return "dark"

    def _resolve_theme(self) -> None:
        """Рассчитать тему по режиму и оповестить UI об изменении."""
        if self._theme_mode == "system":
            resolved = self._system_theme()
        else:
            resolved = self._theme_mode
        if resolved != self._theme:
            self._theme = resolved
            self._apply_theme()
            self.themeChanged.emit(resolved)

    def _on_system_theme_changed(self, _scheme) -> None:  # noqa: ANN001
        self._resolve_theme()

    # ---------- шрифт ----------

    def setFontKind(self, kind: str) -> None:
        """Выбрать шрифт интерфейса: pixel (по умолчанию) или default."""
        if kind not in FONT_KINDS:
            kind = "pixel"
        if kind != self._font_kind:
            self._font_kind = kind
            self._remember("font", kind)
            self.fontChanged.emit(kind)

    def fontKind(self) -> str:
        return self._font_kind

    # ---------- звук ----------

    def soundsEnabled(self) -> bool:  # noqa: N802
        return self._sounds

    def setSounds(self, enabled: bool) -> None:  # noqa: N802
        """Включить или выключить пиксельные звуки и запомнить выбор."""
        enabled = bool(enabled)
        if enabled == self._sounds:
            return
        self._sounds = enabled
        self._remember("sounds", "true" if enabled else "false")
        self.soundsChanged.emit(enabled)

    # ---------- акцент и анимации ----------

    def accent(self) -> str:
        return self._accent

    def setAccent(self, accent: str) -> None:  # noqa: N802
        """Выбрать акцентную схему (blue / violet / emerald)."""
        if accent not in ACCENT_IDS:
            accent = "blue"
        if accent == self._accent:
            return
        self._accent = accent
        self._remember("accent", accent)
        self.accentChanged.emit(accent)

    def motion(self) -> str:
        return self._motion

    def accentIds(self) -> tuple:
        """Доступные акцентные схемы (для комбо в настройках)."""
        return tuple(ACCENT_IDS)

    def motionLevels(self) -> tuple:
        """Доступные уровни анимации (для комбо в настройках)."""
        return tuple(MOTION_LEVELS)

    # ---------- счёт чистоты ----------

    def cleanliness(self) -> int:
        return self._cleanliness

    def addCleanliness(self, delta: int) -> None:  # noqa: N802
        """Начислить очки за очистку (шкала 0-100, кап - сто)."""
        delta = int(delta)
        if delta == 0:
            return
        new = max(0, min(100, self._cleanliness + delta))
        if new == self._cleanliness:
            return
        self._cleanliness = new
        self._remember("cleanliness", str(new))
        self.cleanlinessChanged.emit(new)

    def setMotion(self, motion: str) -> None:  # noqa: N802
        """Выбрать интенсивность анимаций (playful / restrained)."""
        if motion not in MOTION_LEVELS:
            motion = "playful"
        if motion == self._motion:
            return
        self._motion = motion
        self._remember("motion", motion)
        self.motionChanged.emit(motion)

    # ---------- персонаж ----------

    def say(self, text: str) -> None:
        """Попросить персонажа сказать реплику."""
        if text:
            self.speechRequested.emit(text)

    def setAssistantMood(self, mood: str) -> None:  # noqa: N802 - как у остальных set*
        """Попросить персонажа сменить настроение (idle / scan / calm / panic…)."""
        self.assistantMoodRequested.emit(mood)


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
