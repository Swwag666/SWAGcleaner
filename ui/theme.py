"""Оформление SWAGcleaner: две палитры, шрифты и один генератор стилей.

Палитры:
    dark  — глубокий графит с синим акцентом (по умолчанию);
    light — светлая, тот же синий акцент.

Шрифты:
    pixel   — пиксельный Handjet (лежит в assets/fonts, умеет кириллицу),
              он же ставится без сглаживания, чтобы буквы оставались резкими;
    default — обычный системный шрифт, для тех, кому пиксельный не нравится.

Стили генерируются одной функцией qss(): цвета и размеры подставляются
из палитры и выбранного шрифта, поэтому переключение темы и шрифта —
это просто повторный вызов apply_theme().
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget

_LOGGER = logging.getLogger("swag.ui.theme")

THEMES = ("dark", "light")
FONT_KINDS = ("pixel", "default")

# Пакет шрифта лежит рядом с проектом: assets/fonts.
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PIXEL_FAMILY = "Handjet"
_DEFAULT_FAMILY = "Segoe UI"

# Пиксельный шрифт мельче обычного, поэтому ему нужен больший кегль.
_FONT_SIZES: Dict[str, Dict[str, int]] = {
    "pixel": {"base": 16, "small": 13, "title": 21, "nav": 17},
    "default": {"base": 13, "small": 11, "title": 15, "nav": 13},
}

PALETTES: Dict[str, Dict[str, str]] = {
    "dark": {
        "bg_base": "#0d1015",
        "bg_sidebar": "#12161d",
        "bg_panel": "#161b23",
        "bg_panel_hover": "#1e2530",
        "bg_inset": "#1a212a",
        "bg_input": "#10151b",
        "border": "#242c37",
        "border_soft": "#1b222b",
        "accent": "#4d8dff",
        "accent_soft": "#2f5fb8",
        "accent_hover": "#6ba3ff",
        "text_primary": "#e6ebf2",
        "text_secondary": "#92a0b2",
        # Не светлее этого: у мелких подписей должен оставаться читаемый контраст.
        "text_placeholder": "#7d8b9d",
        "on": "#35d0a5",
        "warn": "#e9a23b",
        "danger": "#ff5f5f",
        "select_bg": "#22334f",
        "shadow": "rgba(0, 0, 0, 90)",
    },
    "light": {
        "bg_base": "#eef1f6",
        "bg_sidebar": "#ffffff",
        "bg_panel": "#ffffff",
        "bg_panel_hover": "#e8edf5",
        "bg_inset": "#f1f4f9",
        "bg_input": "#ffffff",
        "border": "#d8dfe9",
        "border_soft": "#e6ebf2",
        "accent": "#2f6fe0",
        "accent_soft": "#7ba6ee",
        "accent_hover": "#1f57c4",
        "text_primary": "#16202b",
        "text_secondary": "#5a6878",
        # Не светлее этого: на белом фоне подсказки иначе почти не видны.
        "text_placeholder": "#6b7887",
        "on": "#12a97e",
        "warn": "#c67c17",
        "danger": "#d9463f",
        "select_bg": "#d6e3fb",
        "shadow": "rgba(30, 45, 70, 40)",
    },
}

_fonts_registered = False
_loaded_families: list[str] = []


def register_bundled_fonts() -> list[str]:
    """Подключить шрифты из assets/fonts. Возвращает список семейств.

    Вызывать один раз после создания QApplication.
    """
    global _fonts_registered, _loaded_families
    if _fonts_registered:
        return list(_loaded_families)
    _fonts_registered = True
    for path in sorted(_FONTS_DIR.glob("*.ttf")):
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            _LOGGER.warning("Не удалось подключить шрифт %s", path)
            continue
        _loaded_families.extend(QFontDatabase.applicationFontFamilies(font_id))
    if not _loaded_families:
        _LOGGER.warning("В %s нет пригодных шрифтов", _FONTS_DIR)
    return list(_loaded_families)


def pixel_font_available() -> bool:
    """Установлен ли пиксельный шрифт (он нужен интерфейсу по умолчанию)."""
    register_bundled_fonts()
    return any(family.lower() == _PIXEL_FAMILY.lower() for family in _loaded_families)


def resolve_family(kind: str) -> str:
    """Семейство шрифта для выбранного вида, с откатом на системное."""
    if kind == "pixel" and pixel_font_available():
        return _PIXEL_FAMILY
    return _DEFAULT_FAMILY


def fonts_sizes(kind: str) -> Dict[str, int]:
    """Кегли для выбранного вида шрифта."""
    return dict(_FONT_SIZES.get(kind, _FONT_SIZES["default"]))


def font_for(kind: str, weight: QFont.Weight | None = None) -> QFont:
    """Собрать шрифт приложения.

    Для пиксельного шрифта сглаживание выключается — иначе края букв
    размываются и весь смысл пиксельного рисунка теряется.
    """
    _kind = kind if kind in FONT_KINDS else "default"
    font = QFont(resolve_family(_kind), fonts_sizes(_kind)["base"])
    if weight is not None:
        font.setWeight(weight)
    if _kind == "pixel":
        # Без сглаживания края букв остаются квадратными, как в пиксельной игре.
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return font


def palette(theme: str) -> Dict[str, str]:
    """Палитра по имени темы (с откатом на тёмную)."""
    return PALETTES.get(theme, PALETTES["dark"])


def qss(theme: str = "dark", font_kind: str = "pixel") -> str:
    """Собрать таблицу стилей для темы и вида шрифта.

    font-family здесь намеренно не задаётся: семейство ставит QApplication
    через font_for(), иначе Qt-стили перебивали бы настройку сглаживания.
    """
    c = palette(theme)
    size = fonts_sizes(font_kind)
    spacing = "1px" if font_kind == "pixel" else "0px"
    return f"""
    QMainWindow, QWidget#central {{
        background-color: {c["bg_base"]};
    }}
    QWidget {{
        color: {c["text_primary"]};
        letter-spacing: {spacing};
    }}
    QLabel {{
        background: transparent;
        color: {c["text_primary"]};
        letter-spacing: {spacing};
    }}
    QLabel[role="secondary"] {{ color: {c["text_secondary"]}; }}
    QLabel[role="hint"] {{ color: {c["text_placeholder"]}; font-size: {size["small"]}px; }}
    QLabel[role="title"] {{ font-size: {size["title"]}px; color: {c["text_primary"]}; }}
    QLabel[role="section"] {{
        font-size: {size["small"]}px;
        color: {c["text_placeholder"]};
        letter-spacing: 2px;
    }}
    QLabel[role="stat"] {{ font-size: {size["title"]}px; color: {c["accent"]}; }}

    /* ---------- боковое меню ---------- */
    QFrame#sidebar {{
        background-color: {c["bg_sidebar"]};
        border-right: 1px solid {c["border_soft"]};
    }}
    QLabel#sidebarCaption {{
        color: {c["text_placeholder"]};
        font-size: {size["small"]}px;
        letter-spacing: 2px;
        padding: 0px 4px;
    }}
    QPushButton#navItem {{
        color: {c["text_secondary"]};
        background-color: transparent;
        border: none;
        border-left: 3px solid transparent;
        border-radius: 0px;
        padding: 9px 10px;
        text-align: left;
        font-size: {size["nav"]}px;
        letter-spacing: {spacing};
    }}
    QPushButton#navItem:hover {{
        color: {c["text_primary"]};
        background-color: {c["bg_panel_hover"]};
    }}
    QPushButton#navItem:checked {{
        color: {c["text_primary"]};
        background-color: {c["bg_panel_hover"]};
        border-left: 3px solid {c["accent"]};
    }}

    /* ---------- обычные кнопки ---------- */
    QPushButton {{
        background-color: {c["bg_inset"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        padding: 8px 16px;
        font-size: {size["base"]}px;
        letter-spacing: {spacing};
    }}
    QPushButton:hover {{
        background-color: {c["bg_panel_hover"]};
        border-color: {c["accent_soft"]};
    }}
    QPushButton:pressed {{ background-color: {c["accent_soft"]}; }}
    QPushButton:disabled {{
        color: {c["text_placeholder"]};
        background-color: {c["bg_input"]};
    }}
    QPushButton[role="primary"] {{
        background-color: {c["accent"]};
        border-color: {c["accent"]};
        color: #ffffff;
    }}
    QPushButton[role="primary"]:hover {{
        background-color: {c["accent_hover"]};
        border-color: {c["accent_hover"]};
    }}

    /* ---------- иконочные кнопки в шапке ---------- */
    QPushButton#headerButton {{
        background-color: {c["bg_panel"]};
        color: {c["text_secondary"]};
        border: 1px solid {c["border"]};
        border-radius: 8px;
        padding: 6px 10px;
        font-size: {size["base"]}px;
        letter-spacing: {spacing};
    }}
    QPushButton#headerButton:hover {{
        color: {c["text_primary"]};
        background-color: {c["bg_panel_hover"]};
        border-color: {c["accent_soft"]};
    }}

    /* ---------- шапка ---------- */
    QWidget#header {{ background-color: {c["bg_base"]}; }}

    /* ---------- карточки ---------- */
    QFrame#card {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 12px;
    }}
    QFrame#divider {{
        background-color: {c["border_soft"]};
        border: none;
        max-height: 1px;
    }}

    /* ---------- ввод ---------- */
    QLineEdit, QComboBox, QTextEdit, QSpinBox {{
        background-color: {c["bg_input"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        padding: 7px 9px;
        font-size: {size["base"]}px;
        letter-spacing: {spacing};
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
        border-color: {c["accent"]};
    }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        selection-background-color: {c["select_bg"]};
        selection-color: {c["text_primary"]};
        padding: 4px;
        outline: none;
    }}

    /* ---------- списки и таблицы ---------- */
    QListWidget, QListView, QTableWidget, QTreeWidget {{
        background-color: {c["bg_input"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 10px;
        selection-background-color: {c["select_bg"]};
        selection-color: {c["text_primary"]};
        gridline-color: {c["border_soft"]};
        font-size: {size["base"]}px;
        letter-spacing: {spacing};
    }}

    /* ---------- статус-бар ---------- */
    QStatusBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_secondary"]};
        border-top: 1px solid {c["border_soft"]};
        font-size: {size["small"]}px;
        letter-spacing: {spacing};
    }}

    /* ---------- меню ---------- */
    QMenuBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_primary"]};
        border-bottom: 1px solid {c["border_soft"]};
        font-size: {size["base"]}px;
    }}
    QMenuBar::item:selected {{ background-color: {c["bg_panel_hover"]}; }}
    QMenu {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        border-radius: 8px;
        color: {c["text_primary"]};
        padding: 6px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
    QMenu::item:selected {{
        background-color: {c["accent"]};
        color: #ffffff;
    }}

    /* ---------- прогресс и скролл ---------- */
    QProgressBar {{
        background-color: {c["bg_input"]};
        border: 1px solid {c["border"]};
        border-radius: 6px;
        text-align: center;
        color: {c["text_primary"]};
        font-size: {size["small"]}px;
    }}
    QProgressBar::chunk {{ background-color: {c["accent"]}; border-radius: 5px; }}
    QScrollBar:vertical {{
        background-color: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {c["border"]};
        border-radius: 5px;
        min-height: 26px;
    }}
    QScrollBar::handle:vertical:hover {{ background-color: {c["accent_soft"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollBar:horizontal {{ background-color: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background-color: {c["border"]};
        border-radius: 5px;
        min-width: 26px;
    }}
    """


def apply_theme(app: QApplication, theme: str = "dark", font_kind: str = "pixel") -> None:
    """Применить тему и шрифт ко всему приложению."""
    app.setStyleSheet(qss(theme, font_kind))
    app.setFont(font_for(font_kind))


# ---------- фабрики типовых виджетов ----------


def heading(text: str, parent: QWidget | None = None) -> QLabel:
    """Крупный заголовок страницы."""
    label = QLabel(text, parent)
    label.setProperty("role", "title")
    label.setWordWrap(True)
    return label


def subheading(text: str, parent: QWidget | None = None) -> QLabel:
    """Подзаголовок."""
    label = QLabel(text, parent)
    label.setProperty("role", "secondary")
    label.setWordWrap(True)
    return label


def body(text: str, parent: QWidget | None = None) -> QLabel:
    """Обычный текст."""
    label = QLabel(text, parent)
    label.setWordWrap(True)
    return label


def hint(text: str, parent: QWidget | None = None) -> QLabel:
    """Мелкая серая подсказка."""
    label = QLabel(text, parent)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    return label


def section(text: str, parent: QWidget | None = None) -> QLabel:
    """Разреженная подпись раздела."""
    label = QLabel(text, parent)
    label.setProperty("role", "section")
    return label


def stat(text: str, parent: QWidget | None = None) -> QLabel:
    """Крупная цифра-показатель."""
    label = QLabel(text, parent)
    label.setProperty("role", "stat")
    return label


def button(text: str, parent: QWidget | None = None, primary: bool = False) -> QPushButton:
    """Кнопка; primary — акцентная."""
    widget = QPushButton(text, parent)
    if primary:
        widget.setProperty("role", "primary")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setMinimumHeight(34)
    return widget


def spacer(height: int = 10) -> QWidget:
    """Вертикальный отступ фиксированной высоты."""
    widget = QWidget()
    widget.setFixedHeight(height)
    return widget


def card(parent: QWidget | None = None) -> QFrame:
    """Панель-карточка."""
    frame = QFrame(parent)
    frame.setObjectName("card")
    return frame


def divider(parent: QWidget | None = None) -> QFrame:
    """Тонкая горизонтальная линия."""
    frame = QFrame(parent)
    frame.setObjectName("divider")
    frame.setFixedHeight(1)
    return frame
