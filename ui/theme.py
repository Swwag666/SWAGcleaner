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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget

_LOGGER = logging.getLogger("swag.ui.theme")

THEMES = ("dark", "light")
FONT_KINDS = ("pixel", "default")

# Пакет шрифта лежит рядом с проектом: assets/fonts.
_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PIXEL_FAMILY = "Handjet"
_DEFAULT_FAMILY = "Segoe UI"

# Размеры шрифтов. У пиксельного они в ПИКСЕЛЯХ, у обычного — в пунктах:
# пиксельному шрифту дробный размер противопоказан. 16pt ≈ 21.33px, и тогда
# штрихи попадают на половину пикселя — половина буквы получается размытой.
# При целом размере в пикселях размытых штрихов нет вообще.
_FONT_SIZES: Dict[str, Dict[str, int]] = {
    "pixel": {"base": 20, "small": 17, "title": 26, "nav": 20},
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
        # Затемнение под диалогом подтверждения (#AARRGGBB).
        "scrim": "#b30a0d12",
    },
    "light": {
        # Чуть приглушённая светлая тема: чистый белый на весь экран режет глаз.
        "bg_base": "#e5e9f0",
        "bg_sidebar": "#f6f8fb",
        "bg_panel": "#f6f8fb",
        "bg_panel_hover": "#dfe6f0",
        "bg_inset": "#eaeef5",
        "bg_input": "#fbfcfe",
        "border": "#cbd5e2",
        "border_soft": "#dbe3ee",
        "accent": "#2f6fe0",
        "accent_soft": "#7ba6ee",
        "accent_hover": "#1f57c4",
        "text_primary": "#16202b",
        "text_secondary": "#556374",
        # Не светлее этого: на светлом фоне подсказки иначе почти не видны.
        "text_placeholder": "#63707e",
        "on": "#12a97e",
        "warn": "#c67c17",
        "danger": "#d9463f",
        "select_bg": "#cfdffa",
        "shadow": "rgba(30, 45, 70, 40)",
        "scrim": "#59101a2a",
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


def font_for(kind: str, theme: str = "dark", weight: QFont.Weight | None = None) -> QFont:
    """Собрать шрифт приложения.

    Для пиксельного шрифта: целый размер в пикселях, выключенное сглаживание
    и, на тёмной теме, более жирный вес. Последнее — оптическая компенсация:
    светлые штрихи на тёмном фоне кажутся тоньше, чем такие же тёмные на
    светлом, поэтому на тёмной теме тот же текст выглядит «потрёпанным».
    """
    _kind = kind if kind in FONT_KINDS else "default"
    font = QFont(resolve_family(_kind))
    if _kind == "pixel":
        font.setPixelSize(fonts_sizes(_kind)["base"])
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        if theme not in ("light",):
            font.setWeight(QFont.Weight.Bold)
    else:
        font.setPointSize(fonts_sizes(_kind)["base"])
    if weight is not None:
        font.setWeight(weight)
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
    # У пиксельного шрифта размер задаём в пикселях, у обычного — в пунктах.
    unit = "px" if font_kind == "pixel" else "pt"
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
    QLabel[role="hint"] {{ color: {c["text_placeholder"]}; font-size: {size["small"]}{unit}; }}
    QLabel[role="title"] {{ font-size: {size["title"]}{unit}; color: {c["text_primary"]}; }}
    QLabel[role="section"] {{
        font-size: {size["small"]}{unit};
        color: {c["text_placeholder"]};
        letter-spacing: 2px;
    }}
    QLabel[role="stat"] {{ font-size: {size["title"]}{unit}; color: {c["accent"]}; }}

    /* ---------- боковое меню ---------- */
    QFrame#sidebar {{
        background-color: {c["bg_sidebar"]};
        border-right: 1px solid {c["border_soft"]};
    }}
    QLabel#sidebarCaption {{
        color: {c["text_placeholder"]};
        font-size: {size["small"]}{unit};
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
        font-size: {size["nav"]}{unit};
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
        font-size: {size["base"]}{unit};
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
        font-size: {size["base"]}{unit};
        letter-spacing: {spacing};
    }}
    QPushButton#headerButton:hover {{
        color: {c["text_primary"]};
        background-color: {c["bg_panel_hover"]};
        border-color: {c["accent_soft"]};
    }}

    /* ---------- шапка ---------- */
    QWidget#header {{ background-color: {c["bg_base"]}; }}
    /* Полоска под шапкой: выезжает при смене раздела, как заставка сцены.
       Во время работы она тускнеет, а по ней бежит акцентный сегмент —
       отдельный спиннер не нужен. */
    QFrame#accentBar {{
        background-color: {c["accent"]};
        border: none;
    }}
    QFrame#accentBar[mode="busy"] {{ background-color: {c["border_soft"]}; }}

    /* ---------- показатели результатов ---------- */
    QFrame#statTile {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 10px;
    }}
    QLabel#statTileCaption {{ color: {c["text_placeholder"]}; }}

    /* ---------- подтверждение ---------- */
    QDialog#confirmDialog {{ background: transparent; }}
    QFrame#dialogPanel {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        border-radius: 14px;
    }}
    QFrame#dialogItem {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 9px;
    }}
    QLabel#riskBadge {{ font-size: {size["small"]}{unit}; letter-spacing: 1px; }}
    QLabel#riskBadge[risk="low"] {{ color: {c["on"]}; }}
    QLabel#riskBadge[risk="medium"] {{ color: {c["warn"]}; }}
    QLabel#riskBadge[risk="high"] {{ color: {c["danger"]}; }}

    /* ---------- флажки ---------- */
    QCheckBox {{ color: {c["text_primary"]}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c["border"]};
        border-radius: 4px;
        background-color: {c["bg_input"]};
    }}
    QCheckBox::indicator:hover {{ border-color: {c["accent_soft"]}; }}
    QCheckBox::indicator:checked {{
        background-color: {c["accent"]};
        border-color: {c["accent"]};
    }}

    /* ---------- карточки категорий (экран чистки) ---------- */
    QScrollArea#categoryScroll {{ background: transparent; border: none; }}
    QScrollArea#categoryScroll > QWidget > QWidget {{ background: transparent; }}
    QFrame#categoryCard {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 10px;
    }}
    QFrame#categoryCard:hover {{ border-color: {c["accent_soft"]}; }}
    QFrame#categoryCard[checked="true"] {{ border-color: {c["accent_soft"]}; }}
    QLabel#laneBadge {{ font-size: {size["small"]}{unit}; letter-spacing: 1px; }}
    QLabel#laneBadge[lane="trash"] {{ color: {c["on"]}; }}
    QLabel#laneBadge[lane="direct"] {{ color: {c["warn"]}; }}

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

    /* ---------- персонаж и панель реплики ---------- */
    QFrame#speechBox {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border_soft"]};
        /* Акцентная кромка слева — как у реплики в визуальной новелле. */
        border-left: 3px solid {c["accent"]};
        border-radius: 10px;
    }}
    QLabel#speechName {{
        color: {c["accent"]};
        font-size: {size["small"]}{unit};
        letter-spacing: 2px;
    }}
    QLabel#speechText {{ color: {c["text_primary"]}; }}
    QLabel#speechCaret {{ color: {c["accent"]}; font-size: {size["small"]}{unit}; }}

    /* ---------- ввод ---------- */
    QLineEdit, QComboBox, QTextEdit, QSpinBox {{
        background-color: {c["bg_input"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        padding: 7px 9px;
        font-size: {size["base"]}{unit};
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
        font-size: {size["base"]}{unit};
        letter-spacing: {spacing};
    }}

    /* ---------- статус-бар ---------- */
    QStatusBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_secondary"]};
        border-top: 1px solid {c["border_soft"]};
        font-size: {size["small"]}{unit};
        letter-spacing: {spacing};
    }}

    /* ---------- меню ---------- */
    QMenuBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_primary"]};
        border-bottom: 1px solid {c["border_soft"]};
        font-size: {size["base"]}{unit};
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
        font-size: {size["small"]}{unit};
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
    app.setFont(font_for(font_kind, theme))


# ---------- фабрики типовых виджетов ----------


class ElideButton(QPushButton):
    """Кнопка, которая при нехватке ширины сжимает текст в эллипсис.

    Обычный QPushButton на узком окне (1024×768) просто обрезает длинную
    подпись: layout не даёт кнопке сузиться из-за minimumSizeHint по тексту,
    а сам текст рисуется куском. Здесь минимальная ширина нулевая — кнопка
    может сжаться, а текст подгоняется под фактическую ширину с «…» на конце.
    Полный текст всегда остаётся в tooltip и возвращается, когда места
    становится достаточно.
    """

    # Запас под паддинги QSS (8px 16px) и рамку, чтобы эллипсис не вплотную.
    _H_PADDING = 40

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self._elide()

    def fullText(self) -> str:
        """Подпись без эллипсиса — как её задали."""
        return self._full_text

    def sizeHint(self) -> QSize:  # type: ignore[override]
        """Размер всегда считается по ПОЛНОМУ тексту.

        Иначе эллипсис уменьшал бы sizeHint, разметка сжимала бы кнопку
        ещё сильнее, текст элидировался бы снова — спираль до нуля. Полный
        sizeHint говорит разметке «столько мне надо», а сжатие ниже него
        уже честно обрабатывается эллипсисом в _elide().
        """
        hint = super().sizeHint()
        if not self._full_text:
            return hint
        width = (self.fontMetrics().horizontalAdvance(self._full_text)
                 + self._H_PADDING + 8)
        return QSize(max(width, hint.width()), hint.height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        if not self._full_text:
            super().setText("")
            return
        metrics = self.fontMetrics()
        available = self.width() - self._H_PADDING
        if available <= 0 or metrics.horizontalAdvance(self._full_text) <= available:
            elided = self._full_text
        else:
            elided = metrics.elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, available)
        if elided != self.text():
            super().setText(elided)
        self.setToolTip(self._full_text)


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
    widget = ElideButton(text, parent)
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
