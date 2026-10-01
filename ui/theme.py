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
from typing import Dict, Tuple

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
    QTimer,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

_LOGGER = logging.getLogger("swag.ui.theme")

THEMES = ("dark", "light", "mono", "num")
FONT_KINDS = ("pixel", "default")

# Акцентные схемы: синий (классика), фиолет (киберпанк), изумруд (хакер).
# У каждой — свои оттенки для тёмной и светлой темы, чтобы контраст
# текста на кнопках и подписей не просел ни в одной комбинации.
ACCENTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "blue": {
        "dark": {"accent": "#4d8dff", "accent_soft": "#2f5fb8",
                 "accent_hover": "#6ba3ff"},
        "light": {"accent": "#2563eb", "accent_soft": "#8fb2f2",
                  "accent_hover": "#1d4fd7"},
    },
    "violet": {
        "dark": {"accent": "#9d7bff", "accent_soft": "#6a4fd6",
                 "accent_hover": "#b89cff"},
        "light": {"accent": "#7c3aed", "accent_soft": "#c4b5fd",
                  "accent_hover": "#6d28d9"},
    },
    "emerald": {
        "dark": {"accent": "#2bd49a", "accent_soft": "#1b9a74",
                 "accent_hover": "#4fe3b0"},
        "light": {"accent": "#059669", "accent_soft": "#6ee7b7",
                  "accent_hover": "#047857"},
    },
}
ACCENT_IDS: Tuple[str, ...] = tuple(ACCENTS)

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
        "button_text": "#ffffff",
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
        # Светлая тема с этажами: серый стол, белые карточки, чёткие кромки.
        # Раньше панель и фон отличались на пару тонов - интерфейс выглядел
        # размытой бумагой, теперь слои читаются с первого взгляда.
        "bg_base": "#dce3ed",
        "bg_sidebar": "#f1f5fa",
        "bg_panel": "#ffffff",
        "bg_panel_hover": "#e9eff8",
        "bg_inset": "#eef2f8",
        "bg_input": "#ffffff",
        "border": "#b6c3d6",
        "border_soft": "#cfd9e6",
        "accent": "#2563eb",
        "accent_soft": "#8fb2f2",
        "accent_hover": "#1d4fd7",
        "button_text": "#ffffff",
        "text_primary": "#0f1826",
        "text_secondary": "#43536a",
        # Не светлее этого: на светлом фоне подсказки иначе почти не видны.
        "text_placeholder": "#5a697c",
        "on": "#0f9d76",
        "warn": "#b26a0d",
        "danger": "#d33c34",
        "select_bg": "#d6e3fb",
        "shadow": "rgba(30, 45, 70, 55)",
        "scrim": "#59101a2a",
    },
    "mono": {
        # Монохром: чистые градации серого, ни одного цветного пикселя.
        # За образец взят референс «чёрно-белая картинка»: чёрный стол,
        # тёмно-серые панели, а акцент - светлый серый, почти белый.
        "bg_base": "#0a0a0a",
        "bg_sidebar": "#141414",
        "bg_panel": "#1b1b1b",
        "bg_panel_hover": "#232323",
        "bg_inset": "#101010",
        "bg_input": "#161616",
        "border": "#2c2c2c",
        "border_soft": "#242424",
        "accent": "#c9c9c9",
        "accent_soft": "#5a5a5a",
        "accent_hover": "#ffffff",
        # Светлый акцент => тёмный текст на кнопке, иначе белый сольётся.
        "button_text": "#0a0a0a",
        "text_primary": "#e6e6e6",
        "text_secondary": "#999999",
        "text_placeholder": "#5a5a5a",
        "on": "#c9c9c9",
        "warn": "#9a9a9a",
        "danger": "#ffffff",
        "select_bg": "#272727",
        "shadow": "rgba(0, 0, 0, 90)",
        "scrim": "#b3000000",
    },
    "num": {
        # «Числовая» тема: премиальный цифровой HUD. Глубокий тёмно-синий
        # фон и кованое золото — приборная панель, а не лимонный неон.
        # Золото здесь тёплое и приглушённое (антик), не кислотно-жёлтое.
        "bg_base": "#0e1420",
        "bg_sidebar": "#101722",
        "bg_panel": "#16202b",
        "bg_panel_hover": "#1e2a37",
        "bg_inset": "#131c26",
        "bg_input": "#0f1620",
        "border": "#263340",
        "border_soft": "#1c2732",
        "accent": "#d4a037",
        "accent_soft": "#6e5719",
        "accent_hover": "#e6bb56",
        "button_text": "#0a0a0a",
        "text_primary": "#e9eef4",
        "text_secondary": "#9aa8b6",
        "text_placeholder": "#6d7c8a",
        "on": "#35d0a5",
        "warn": "#d98a2b",
        "danger": "#ff5f5f",
        "select_bg": "#23324a",
        "shadow": "rgba(0, 0, 0, 90)",
        "scrim": "#b3000000",
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
    """Собрать базовый шрифт приложения.

    Для пиксельного шрифта: целый размер в пикселях, выключенное сглаживание,
    полное хинтинг-предпочтение и разрядка в 1px ( раньше разрядку задавал QSS
    на каждый селектор — смена темы перечитывала шрифт из стилей и рассылала
    FontChange по всем виджетам, окно замирало на секунды ). Результат
    НЕ зависит от темы: смена темы не должна менять шрифт приложения.
    Оптическая компенсация «светлое на тёмном кажется тоньше» уехала в QSS
    ( глобальный font-weight ), который дёшев: не меняет метрики шрифта.
    Параметр theme оставлен для совместимости подписи и игнорируется.
    """
    _kind = kind if kind in FONT_KINDS else "default"
    font = QFont(resolve_family(_kind))
    if _kind == "pixel":
        font.setPixelSize(fonts_sizes(_kind)["base"])
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
    else:
        font.setPointSize(fonts_sizes(_kind)["base"])
    if weight is not None:
        font.setWeight(weight)
    return font


# Активный вид шрифта: фабрики виджетов спрашивают его при создании, чтобы
# ставить рольной шрифт сразу. Обновляется в apply_theme.
_active_kind: str = "pixel"

# Рольные шрифты. Размер шрифта в QSS — главный тормоз смены темы: каждый
# font-size в стилях Qt разрешает шрифт заново и рассылает FontChange, из-за
# чего все QLabel с переносом пересчитывают высоту. Здесь размеры задаются
# кодом, и смена темы их не трогает вовсе.
_ROLE_RULES: Dict[str, Tuple[str, float | None]] = {
    "title": ("title", None),
    "hint": ("small", None),
    "section": ("small", 2.0),
    "stat": ("title", None),
}
_LABEL_NAME_RULES: Dict[str, Tuple[str, float | None]] = {
    "sidebarCaption": ("small", 2.0),
    "sidebarStatus": ("small", None),
    "speechName": ("small", 2.0),
    "speechCaret": ("small", None),
    "riskBadge": ("small", 1.0),
    "laneBadge": ("small", 1.0),
    "accordionCount": ("small", None),
    "heroStatValue": ("title", None),
    "heroStatCaption": ("small", None),
}


def role_font(kind: str, size_key: str, spacing: float | None = None) -> QFont:
    """Шрифт по роли: базовый + размер + разрядка.

    size_key — «base», «small», «title», «nav» или «emph» (акцентная цифра
    hero-карточки: заметно крупнее title). spacing задаётся в пикселях.
    """
    k = kind if kind in FONT_KINDS else "default"
    sizes = fonts_sizes(k)
    if size_key == "emph":
        size = sizes["title"] + (8 if k == "pixel" else 5)
    else:
        size = sizes.get(size_key, sizes["base"])
    font = font_for(k)
    if k == "pixel":
        font.setPixelSize(size)
    else:
        font.setPointSize(size)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def widget_font(widget: QWidget, kind: str = "pixel") -> QFont:
    """Шрифт для виджета по его роли/objectName/типу. Базовый, если правил нет."""
    k = kind if kind in FONT_KINDS else "default"
    name = widget.objectName() or ""
    role = widget.property("role")
    if isinstance(widget, QLabel):
        rule = _LABEL_NAME_RULES.get(name)
        if rule is None and role:
            rule = _ROLE_RULES.get(str(role))
        if rule is not None and name != "heroStatValue":
            return role_font(k, rule[0], rule[1])
    if isinstance(widget, QPushButton) and name == "navItem":
        return role_font(k, "nav")
    if isinstance(widget, (QStatusBar, QProgressBar)):
        return role_font(k, "small")
    return font_for(k)


def apply_role_font(widget: QWidget, kind: str | None = None) -> None:
    """Поставить виджету его рольной шрифт ( для фабрик и конструкторов )."""
    k = kind or _active_kind
    font = widget_font(widget, k)
    if widget.font() != font:
        widget.setFont(font)


def apply_widget_fonts(app: QApplication, kind: str) -> None:
    """Обновить шрифты всех виджетов под выбранный вид шрифта.

    Дорогой проход — запускается только при смене вида ( pixel/default ),
    а не при смене темы: тема шрифты не меняет.
    """
    for widget in app.allWidgets():
        apply_role_font(widget, kind)


def palette(theme: str, accent: str = "blue") -> Dict[str, str]:
    """Палитра темы с подставленным акцентом.

    Базовые цвета берутся из PALETTES, а accent/accent_soft/accent_hover -
    из ACCENTS по выбранной схеме (синий/фиолет/изумруд). Монохромная тема
    несёт собственный серый акцент и не зависит от цветной схемы.
    """
    colors = dict(PALETTES.get(theme, PALETTES["dark"]))
    if theme in ("mono", "num"):
        return colors
    accent_colors = ACCENTS.get(accent, ACCENTS["blue"]).get(
        theme, ACCENTS["blue"]["dark"])
    colors.update(accent_colors)
    return colors


def qss(theme: str = "dark", font_kind: str = "pixel",
        accent: str = "blue") -> str:
    """Собрать таблицу стилей для темы, вида шрифта и акцента.

    font-family и font-size здесь намеренно не задаются: семейство и рольные
    размеры ставит код (font_for/role_font), иначе Qt-стили перебивали бы
    настройку сглаживания, а каждый font-size в стилях при смене темы
    перечитывал шрифт и рассылал FontChange по всем виджетам — окно
    замирало. Исключение — heroStatValue[emphasis]: его размер динамический,
    дешевле оставить один font-size в стилях, чем ловить смену свойства.
    """
    c = palette(theme, accent)
    size = fonts_sizes(font_kind)
    # Оптическая компенсация веса: светлые штрихи на тёмном фоне кажутся
    # тоньше, поэтому пиксельный шрифт на тёмных темах утолщается через QSS.
    # font-weight в стилях дёшев: метрики шрифта не меняются, дорогой
    # font-resolve не запускается.
    bold_rule = "font-weight: bold;" if (
        font_kind == "pixel" and theme != "light") else ""
    # Акцентная «героическая» цифра: заметно крупнее title, но не слон.
    emph = size["title"] + (8 if font_kind == "pixel" else 5)
    # У пиксельного шрифта размер задаём в пикселях, у обычного — в пунктах.
    unit = "px" if font_kind == "pixel" else "pt"
    # Верхняя кромка карточек чуть светлее нижней: панель «ловит свет» и
    # выглядит объёмной, а не плоской плашкой.
    border_light = QColor(c["border"]).lighter(122).name()
    return f"""
    QMainWindow, QWidget#central {{
        background-color: {c["bg_base"]};
    }}
    QWidget {{
        color: {c["text_primary"]};
        {bold_rule}
    }}
    QLabel {{
        background: transparent;
        color: {c["text_primary"]};
    }}
    QLabel[role="secondary"] {{ color: {c["text_secondary"]}; }}
    QLabel[role="hint"] {{ color: {c["text_placeholder"]}; }}
    QLabel[role="title"] {{ color: {c["text_primary"]}; }}
    QLabel[role="section"] {{ color: {c["text_placeholder"]}; }}
    QLabel[role="stat"] {{ color: {c["accent"]}; }}

    /* ---------- боковое меню ---------- */
    QFrame#sidebar {{
        background-color: {c["bg_sidebar"]};
        border-right: 1px solid {c["border_soft"]};
    }}
    QLabel#sidebarCaption {{
        color: {c["text_placeholder"]};
        padding: 0px 4px;
    }}
    QFrame#sidebarBrand {{ background: transparent; border: none; }}
    QLabel#sidebarStatus {{
        color: {c["text_placeholder"]};
    }}
    QLabel#statusDot {{ border-radius: 10px; background-color: {c["border"]}; }}
    QLabel#statusDot[kind="admin"] {{ background-color: {c["on"]}; }}
    QLabel#statusDot[kind="user"] {{ background-color: {c["warn"]}; }}
    QPushButton#navItem {{
        color: {c["text_secondary"]};
        background-color: transparent;
        border: none;
        border-left: 3px solid transparent;
        border-radius: 0px;
        padding: 9px 10px;
        text-align: left;
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
    }}
    QPushButton:hover {{
        background-color: {c["bg_panel_hover"]};
        border-color: {c["accent_soft"]};
    }}
    /* Пиксельная кнопка продавливается, как клавиша: текст на 1px вниз. */
    QPushButton:pressed {{
        background-color: {c["accent_soft"]};
        padding-top: 9px;
        padding-bottom: 7px;
    }}
    QPushButton:focus {{ border-color: {c["accent"]}; }}
    QPushButton:disabled {{
        color: {c["text_placeholder"]};
        background-color: {c["bg_input"]};
    }}
    QPushButton[role="primary"] {{
        background-color: {c["accent"]};
        border-color: {c["accent"]};
        color: {c["button_text"]};
    }}
    QPushButton[role="primary"]:hover {{
        background-color: {c["accent_hover"]};
        border-color: {c["accent_hover"]};
    }}
    QPushButton[role="primary"]:pressed {{
        padding-top: 9px;
        padding-bottom: 7px;
    }}

    /* ---------- иконочные кнопки в шапке ---------- */
    QPushButton#headerButton {{
        background-color: {c["bg_panel"]};
        color: {c["text_secondary"]};
        border: 1px solid {c["border"]};
        border-radius: 10px;
        padding: 6px 10px;
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
        border-top-color: {border_light};
        border-radius: 10px;
    }}
    QLabel#statTileCaption {{ color: {c["text_placeholder"]}; }}

    /* ---------- подтверждение ---------- */
    QDialog#confirmDialog {{ background: transparent; }}
    QFrame#dialogPanel {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        border-top-color: {border_light};
        border-radius: 12px;
    }}
    QFrame#dialogItem {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 10px;
    }}
    QLabel#riskBadge {{
        padding: 2px 6px;
        border-radius: 5px;
        background-color: {c["bg_input"]};
    }}
    QLabel#riskBadge[risk="low"] {{ color: {c["on"]}; }}
    QLabel#riskBadge[risk="medium"] {{ color: {c["warn"]}; }}
    QLabel#riskBadge[risk="high"] {{ color: {c["danger"]}; }}

    /* ---------- флажки ---------- */
    QCheckBox {{ color: {c["text_primary"]}; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c["border"]};
        border-radius: 5px;
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
        border-top-color: {border_light};
        border-left: 3px solid {c["border_soft"]};
        border-radius: 10px;
    }}
    QFrame#categoryCard:hover {{
        border-color: {c["accent_soft"]};
        background-color: {c["bg_panel_hover"]};
    }}
    QFrame#categoryCard[checked="true"] {{
        border-color: {c["accent"]};
        background-color: {c["select_bg"]};
    }}
    /* Полоса риска по левому краю карточки. */
    QFrame#categoryCard[risk="low"] {{ border-left: 3px solid {c["on"]}; }}
    QFrame#categoryCard[risk="medium"] {{ border-left: 3px solid {c["warn"]}; }}
    QFrame#categoryCard[risk="high"] {{ border-left: 3px solid {c["danger"]}; }}
    QLabel#laneBadge {{
        padding: 2px 6px;
        border-radius: 5px;
        background-color: {c["bg_input"]};
    }}
    QLabel#laneBadge[lane="trash"] {{ color: {c["on"]}; }}
    QLabel#laneBadge[lane="direct"] {{ color: {c["warn"]}; }}

    /* ---------- карточки ---------- */
    QFrame#card {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 10px;
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
        border-top-color: {border_light};
        /* Акцентная кромка слева — как у реплики в визуальной новелле. */
        border-left: 3px solid {c["accent"]};
        border-radius: 10px;
    }}
    QLabel#speechName {{
        color: {c["accent"]};
    }}
    QLabel#speechText {{ color: {c["text_primary"]}; }}
    QLabel#speechCaret {{ color: {c["accent"]}; }}

    /* ---------- ввод ---------- */
    QLineEdit, QComboBox, QTextEdit, QSpinBox {{
        background-color: {c["bg_input"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        padding: 7px 9px;
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
    }}

    /* ---------- статус-бар ---------- */
    QStatusBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_secondary"]};
        border-top: 1px solid {c["border_soft"]};
    }}

    /* ---------- меню ---------- */
    QMenuBar {{
        background-color: {c["bg_sidebar"]};
        color: {c["text_primary"]};
        border-bottom: 1px solid {c["border_soft"]};
    }}
    QMenuBar::item:selected {{ background-color: {c["bg_panel_hover"]}; }}
    QMenu {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        border-radius: 10px;
        color: {c["text_primary"]};
        padding: 6px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 8px; }}
    QMenu::item:selected {{
        background-color: {c["accent"]};
        color: {c["button_text"]};
    }}

    /* ---------- прогресс и скролл ---------- */
    QProgressBar {{
        background-color: {c["bg_input"]};
        border: 1px solid {c["border"]};
        border-radius: 7px;
        text-align: center;
        color: {c["text_primary"]};
    }}
    QProgressBar::chunk {{ background-color: {c["accent"]}; border-radius: 1px; }}
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
        border-radius: 8px;
        min-width: 26px;
    }}

    /* ---------- аккордеон ---------- */
    QPushButton#accordionHeader {{
        background-color: transparent;
        border: none;
        border-left: 3px solid {c["border_soft"]};
        border-radius: 0px;
        padding: 9px 8px;
        text-align: left;
        color: {c["text_secondary"]};
    }}
    QPushButton#accordionHeader:hover {{
        color: {c["text_primary"]};
        background-color: {c["bg_panel_hover"]};
    }}
    QPushButton#accordionHeader[open="true"] {{
        color: {c["text_primary"]};
        border-left: 3px solid {c["accent"]};
        background-color: {c["bg_inset"]};
    }}
    QLabel#accordionCount {{
        color: {c["text_placeholder"]};
    }}
    /* Тултипы - тоже карточки в теме, а не системная жёлтая тряпка. */
    QToolTip {{
        background-color: {c["bg_panel"]};
        color: {c["text_primary"]};
        border: 1px solid {c["border"]};
        border-radius: 10px;
        padding: 6px 9px;
    }}

    /* ---------- hero-карточка главного экрана ---------- */
    QFrame#heroCard {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-left: 3px solid {c["accent"]};
        border-radius: 10px;
    }}
    QFrame#heroStat {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 10px;
    }}
    QLabel#heroStatValue {{
        color: {c["accent"]};
    }}
    /* Единственный font-size в стилях: акцентная цифра меняет размер
       динамически (свойство emphasis), ловить его смену кодом дороже,
       чем оставить одно правило. FontChange уходит максимум четырём
       лейблам hero-карточки — это копейки. */
    QLabel#heroStatValue[emphasis="true"] {{
        color: {c["on"]};
        font-size: {emph}{unit};
    }}
    QLabel#heroStatValue[tone="good"] {{ color: {c["on"]}; }}
    QLabel#heroStatValue[tone="warn"] {{ color: {c["warn"]}; }}
    QLabel#heroStatValue[tone="danger"] {{ color: {c["danger"]}; }}
    QLabel#heroStatCaption {{
        color: {c["text_placeholder"]};
    }}
    QPushButton#quickTile {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 10px;
        padding: 12px 14px;
        text-align: left;
    }}
    QPushButton#quickTile:hover {{
        background-color: {c["bg_panel_hover"]};
        border-color: {c["accent_soft"]};
    }}
    QPushButton#quickTile:pressed {{
        background-color: {c["accent_soft"]};
        padding-top: 13px;
        padding-bottom: 11px;
    }}

    /* ---------- карточка конфига ---------- */
    QFrame#configCard {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 10px;
    }}
    QFrame#configCard:hover {{ border-color: {c["accent_soft"]}; }}
    QFrame#configCard[selected="true"] {{
        border: 1px solid {c["accent"]};
        background-color: {c["select_bg"]};
    }}

    /* ---------- чип превью и тост ---------- */
    QFrame#previewChip {{
        background-color: {c["bg_inset"]};
        border: 1px solid {c["border_soft"]};
        border-top-color: {border_light};
        border-radius: 5px;
    }}
    QFrame#storageBar {{
        background-color: {c["bg_input"]};
        border: 1px solid {c["border_soft"]};
        border-radius: 5px;
    }}
    QFrame#storageRow {{ background: transparent; border: none; }}
    QFrame#toast {{
        background-color: {c["bg_panel"]};
        border: 1px solid {c["border"]};
        border-left: 3px solid {c["accent"]};
        border-radius: 10px;
    }}
    QFrame#toast[kind="ok"] {{ border-left: 3px solid {c["on"]}; }}
    QFrame#toast[kind="warn"] {{ border-left: 3px solid {c["warn"]}; }}
    QFrame#toast[kind="error"] {{ border-left: 3px solid {c["danger"]}; }}
    QFrame#toastLife {{ border: none; }}
    QFrame#toastLife[kind="ok"] {{ background-color: {c["on"]}; }}
    QFrame#toastLife[kind="warn"] {{ background-color: {c["warn"]}; }}
    QFrame#toastLife[kind="error"] {{ background-color: {c["danger"]}; }}
    QFrame#toastLife[kind="info"] {{ background-color: {c["accent"]}; }}
    """


_QSS_CACHE: Dict[Tuple[str, str, str], str] = {}


def apply_theme(app: QApplication, theme: str = "dark", font_kind: str = "pixel",
                accent: str = "blue") -> None:
    """Применить тему, шрифт и акцент ко всему приложению.

    Строка стилей собирается дорого, а полировка ею всех виджетов ещё
    дороже: держим кэш на тройку (тема, шрифт, акцент) и не трогаем
    приложение вовсе, если эта тройка уже применена.

    Шрифты при смене темы не трогаем вовсе: рольные размеры живут в
    QFont виджетов, таблица стилей теперь меняет только цвета. Проход
    setFont по всем виджетам нужен один раз — при смене вида шрифта.
    """
    global _active_kind
    key = (theme, font_kind, accent)
    sheet = _QSS_CACHE.get(key)
    if sheet is None:
        sheet = qss(theme, font_kind, accent)
        _QSS_CACHE[key] = sheet
    if app.styleSheet() != sheet:
        # Фильтр кликов снимаем на время пере-полировки: смена стилей
        # прогоняет через него десятки тысяч событий, и каждый вызов
        # Python-кода стоит денег. Синхронная операция — клик между
        # снятием и возвратом не успевает произойти.
        from ui import sounds as _sounds
        quiet = _sounds.suspend(app)
        try:
            app.setStyleSheet(sheet)
        finally:
            if quiet:
                _sounds.resume(app)
    # Шрифт приложения держим равным базовому: новые виджеты-сироты
    # (без рольного setFont) наследуют его при создании.
    font = font_for(font_kind)
    if app.font() != font:
        app.setFont(font)
    kind_changed = _active_kind != font_kind
    if kind_changed:
        _active_kind = font_kind
        # Рольные шрифты: каждому виджету — его размер по роли/objectName.
        # Раньше семейство и стратегия вбивались вручную из-за QSS
        # font-size, который сливал размер из стилей с семейством виджета
        # на момент полировки; теперь размеры тоже идут из QFont, и QSS
        # шрифтов не задаёт (кроме heroStatValue[emphasis]).
        apply_widget_fonts(app, font_kind)


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
    apply_role_font(label)
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
    apply_role_font(label)
    # Текст прижат к верху: после правки высоты через heightForWidth
    # центрирование уводило строки под нижний край видимой области.
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return label


def fix_wrap_labels(host: QWidget) -> None:
    """Починить переносимые подписи внутри скролл-хоста.

    QLabel с wordWrap отдаёт ширину минимумом в всю строку целиком, а
    высоту - через heightForWidth после раскладки. Без правки хост
    требует ширину самой длинной строки (горизонтальное обрезание), а
    подписи получают высоту одной строки и перекрывают соседей.
    """
    for label in host.findChildren(QLabel):
        if not label.wordWrap():
            continue
        label.setMinimumWidth(0)
        # Минимум сбрасываем каждый раз: иначе высота, посчитанная на
        # узкой ширине скрытой страницы, залипает навсегда.
        label.setMinimumHeight(0)
        if label.width() > 0:
            h = label.heightForWidth(label.width())
            if h > 0:
                label.setMinimumHeight(h)


def section(text: str, parent: QWidget | None = None) -> QLabel:
    """Разреженная подпись раздела."""
    label = QLabel(text, parent)
    label.setProperty("role", "section")
    apply_role_font(label)
    return label


def stat(text: str, parent: QWidget | None = None) -> QLabel:
    """Крупная цифра-показатель."""
    label = QLabel(text, parent)
    label.setProperty("role", "stat")
    apply_role_font(label)
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


def risk_badge(level: str, parent: QWidget | None = None) -> QLabel:
    """Бейдж риска твика: цвет берётся из QSS по свойству risk."""
    label = QLabel(parent)
    label.setObjectName("riskBadge")
    label.setProperty("risk", level if level in ("low", "medium", "high")
                      else "low")
    apply_role_font(label)
    return label


def chip(text: str, parent: QWidget | None = None) -> QFrame:
    """Маленький чип превью (категория и объём прошлого скана)."""
    frame = QFrame(parent)
    frame.setObjectName("previewChip")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(10, 5, 10, 5)
    label = hint(text, frame)
    layout.addWidget(label)
    return frame


class Accordion(QFrame):
    """Сворачиваемая секция: шапка с шевроном и счётчиком, тело по клику.

    Тело не удаляется, а прячется: виджеты внутри живут своей жизнью
    (сигналы, состояния), аккордеон только управляет видимостью.
    """

    toggled = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None,
                 open: bool = False) -> None:  # noqa: A002 - имя из UI-словаря
        super().__init__(parent)
        self._open = open
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(8)
        self._chevron = QLabel("v" if open else ">", self)
        self._chevron.setProperty("role", "hint")
        self._chevron.setFixedWidth(12)
        apply_role_font(self._chevron)
        head_row.addWidget(self._chevron)
        self._header = QPushButton(title, self)
        self._header.setObjectName("accordionHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setMinimumHeight(34)
        self._header.setProperty("open", open)
        self._header.clicked.connect(self.toggle)
        head_row.addWidget(self._header, 1)
        self._count = QLabel("", self)
        self._count.setObjectName("accordionCount")
        apply_role_font(self._count)
        head_row.addWidget(self._count)
        outer.addLayout(head_row)

        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(14, 6, 4, 10)
        self._body_layout.setSpacing(6)
        # Прячем тело через maximumHeight, а не setVisible: скрытый
        # виджет выпадает из min-size расчёта родительского layout, и
        # скролл сжимал аккордеоны в кашу. Так минимумы честные.
        self._body.setMinimumHeight(0)
        self._body.setMaximumHeight(16777215 if open else 0)
        outer.addWidget(self._body)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_title(self, title: str) -> None:
        self._header.setText(title)

    def set_count(self, count: int) -> None:
        self._count.setText(str(count) if count else "")

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open: bool) -> None:  # noqa: A002
        if self._open == open:
            return
        self._open = open
        self._body.setMaximumHeight(16777215 if open else 0)
        self._chevron.setText("v" if open else ">")
        self._header.setProperty("open", open)
        style = self._header.style()
        style.unpolish(self._header)
        style.polish(self._header)
        self.toggled.emit(open)

    def toggle(self) -> None:
        self.set_open(not self._open)


class ShimmerProgress(QProgressBar):
    """Полоса прогресса с бегущим бликом: видно, что работа идёт."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        apply_role_font(self)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.03) % 1.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        super().paintEvent(_event)
        if self.maximum() <= self.minimum():
            return
        span = self.maximum() - self.minimum()
        frac = (self.value() - self.minimum()) / span
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        chunk = QRectF(3, 3, (self.width() - 6) * max(0.0, min(1.0, frac)),
                       self.height() - 6)
        if chunk.width() < 2:
            painter.end()
            return
        painter.setClipRect(chunk)
        band_w = max(24.0, self.width() * 0.18)
        x = -band_w + (self.width() + 2 * band_w) * self._phase
        grad = QLinearGradient(x, 0, x + band_w, 0)
        edge = QColor(255, 255, 255, 0)
        mid = QColor(255, 255, 255, 46)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, mid)
        grad.setColorAt(1.0, edge)
        painter.fillRect(chunk, QBrush(grad))
        painter.end()


class ToastHost(QWidget):
    """Оверлей тостов внизу справа: информашки без модальных окон.

    Родителю ставится eventFilter-ом, чтобы держать геометрию оверлея
    по размеру окна; мышь сквозь тосты проходит, клики не крадутся.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 24, 24)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignBottom
                                  | Qt.AlignmentFlag.AlignRight)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(0, 0, obj.width(), obj.height())
            self.raise_()
        return super().eventFilter(obj, event)

    def show_message(self, text: str, kind: str = "info") -> None:
        frame = QFrame(self)
        frame.setObjectName("toast")
        frame.setProperty("kind", kind)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(14, 10, 14, 10)
        label = body(text, frame)
        row.addWidget(label)
        outer.addLayout(row)
        # Полоска жизни тоста: тает справа налево, пока тост жив.
        life = QFrame(frame)
        life.setObjectName("toastLife")
        life.setProperty("kind", kind if kind != "info" else "info")
        life.setFixedHeight(3)
        life.setMinimumWidth(0)
        life.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outer.addWidget(life)
        frame.adjustSize()
        self._layout.insertWidget(self._layout.count() - 1, frame)
        effect = QGraphicsOpacityEffect(frame)
        frame.setGraphicsEffect(effect)
        fade_in = QPropertyAnimation(effect, b"opacity", frame)
        fade_in.setDuration(140)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        frame.setProperty("_fade_in", fade_in)
        life_anim = QPropertyAnimation(life, b"maximumWidth", frame)
        life_anim.setDuration(3400)
        life_anim.setStartValue(max(frame.width(), 24))
        life_anim.setEndValue(0)
        life_anim.setEasingCurve(QEasingCurve.Type.Linear)
        life_anim.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        frame.setProperty("_life", life_anim)
        QTimer.singleShot(3400, lambda: self._fade_out(frame, effect))
        while self._layout.count() > 5:
            old = self._layout.takeAt(0)
            if old is not None and old.widget() is not None:
                old.widget().deleteLater()

    def _fade_out(self, frame: QFrame, effect: QGraphicsOpacityEffect) -> None:
        fade = QPropertyAnimation(effect, b"opacity", frame)
        fade.setDuration(200)
        fade.setStartValue(effect.opacity())
        fade.setEndValue(0.0)
        fade.finished.connect(frame.deleteLater)
        fade.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)
        frame.setProperty("_fade_out", fade)


class QuickTile(QPushButton):
    """Крупная плитка быстрого действия на главном экране."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("quickTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)


class HeroCard(QFrame):
    """Шапка главного экрана: состояние машины и быстрые действия."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("heroCard")
        # Layout страницы охотно сжимает всё сжимаемое: без жёстких
        # минимумов карточка схлопывается до полоски заголовка.
        self.setMinimumHeight(188)
        # Minimum: расти можно, сжиматься ниже sizeHint - нет, иначе
        # плитки быстрых действий уезают под обрез карточки.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        self._title = QLabel("", self)
        self._title.setProperty("role", "title")
        apply_role_font(self._title)
        outer.addWidget(self._title)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self._stat_values: Dict[str, QLabel] = {}
        for key in ("last", "junk", "freed", "score"):
            tile = QFrame(self)
            tile.setObjectName("heroStat")
            tile.setMinimumHeight(64)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(12, 10, 12, 10)
            tile_layout.setSpacing(2)
            value = QLabel("-", tile)
            value.setObjectName("heroStatValue")
            caption = QLabel("", tile)
            caption.setObjectName("heroStatCaption")
            apply_role_font(value)
            apply_role_font(caption)
            tile_layout.addWidget(value)
            tile_layout.addWidget(caption)
            stats_row.addWidget(tile, 1)
            self._stat_values[key] = value
            setattr(self, f"_cap_{key}", caption)
        outer.addLayout(stats_row)

        self._tiles_row = QHBoxLayout()
        self._tiles_row.setSpacing(10)
        self._tiles_row.addStretch(1)
        outer.addLayout(self._tiles_row)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_captions(self, last: str, junk: str, freed: str, score: str) -> None:
        self._cap_last.setText(last)
        self._cap_junk.setText(junk)
        self._cap_freed.setText(freed)
        self._cap_score.setText(score)

    def set_stats(self, last: str, junk: str, freed: str, score: str) -> None:
        labels = (
            ("last", last, None),
            ("junk", junk, "good"),
            ("freed", freed, None),
            ("score", score, "good"),
        )
        for key, text, tone in labels:
            value = self._stat_values[key]
            value.setProperty("emphasis", "true" if key in ("junk", "score") else "false")
            value.setProperty("tone", tone or "")
            style = value.style()
            style.unpolish(value)
            style.polish(value)
            changed = value.text() != text
            value.setText(text)
            if changed and key in ("junk", "score"):
                self._pulse(value)

    @staticmethod
    def _pulse(widget: QWidget) -> None:
        """Короткая вспышка прозрачности: цифра свежая, а не застыла."""
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(200)
        animation.setStartValue(0.4)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def add_tile(self, text: str) -> QuickTile:
        tile = QuickTile(text, self)
        self._tiles_row.insertWidget(self._tiles_row.count() - 1, tile)
        return tile


class ConfigCard(QFrame):
    """Карточка конфига системы: имя, состав, дата; выбор кликом."""

    clicked = Signal(str)

    def __init__(self, name: str, meta: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("configCard")
        self._name = name
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self._title = body(name, self)
        self._meta = hint(meta, self)
        layout.addWidget(self._title)
        layout.addWidget(self._meta)
        self.set_selected(False)

    def name(self) -> str:
        return self._name

    def set_meta(self, meta: str) -> None:
        self._meta.setText(meta)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self._name)
        super().mousePressEvent(event)
