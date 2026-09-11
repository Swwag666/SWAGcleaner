"""Тема SWAGcleaner — тёмная, аккуратная, без картинок.

Всё через QSS: цвета, отступы, шрифты, состояния кнопок.
Лёгкая светлая тема дотула позже, когда нужен контраст.
"""
from __future__ import annotations

from typing import Dict
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QWidget,
)

# Константы темы — лёгкий dark-стиль, акцентное синее-серо-белое.
COLORS = {
    "bg_base": "#14161a",
    "bg_panel": "#1b1e24",
    "bg_panel_hover": "#22262e",
    "bg_inset": "#262b33",
    "bg_input": "#1a1d22",
    "border": "#333a43",
    "border_focus": "#5a8cff",
    "text_primary": "#e7e9ec",
    "text_secondary": "#9aa3ad",
    "text_placeholder": "#5f6772",
    "accent": "#5a8cff",
    "accent_soft": "#3a6ed8",
    "accent_hover": "#7aa4ff",
    "on": "#38d9a9",
    "warn": "#f0a04a",
    "danger": "#ff5a5a",
    "select_bg": "#2a3340",
}

FONT_BASE = "Segoe UI, Roboto, sans-serif"
SIZE_BASE = 13
SIZE_SMALL = 11
SIZE_TITLE = 15


def _font(size: int, bold: bool = False, italic: bool = False) -> QFont:
    f = QFont(FONT_BASE)
    f.setPointSize(size)
    f.setBold(bold)
    f.setItalic(italic)
    from PySide6.QtGui import QFont as _QFont
    f.setWeight(_QFont.Weight.Normal if not bold else _QFont.Weight.Bold)
    return f


def _qss() -> str:
    return f"""
    /* Рамки и линии */
    QMainWindow {{
        background-color: {COLORS["bg_base"]};
        color: {COLORS["text_primary"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    QWidget {{
        background-color: transparent;
        color: {COLORS["text_primary"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    QTabWidget::pane {{
        border: 1px solid {COLORS["border"]};
        background-color: {COLORS["bg_panel"]};
        border-radius: 6px 6px 0 0;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {COLORS["text_secondary"]};
        padding: 8px 14px;
        border: none;
        border-bottom: 2px solid transparent;
        font-size: {SIZE_BASE}px;
    }}
    QTabBar::tab:selected {{
        color: {COLORS["text_primary"]};
        border-bottom-color: {COLORS["accent"]};
        background-color: {COLORS["bg_panel_hover"]};
    }}
    QTabBar::tab:hover:!selected {{
        color: {COLORS["text_primary"]};
        background-color: {COLORS["bg_panel_hover"]};
    }}
    /* Группы и панели */
    QGroupBox {{
        border: 1px solid {COLORS["border"]};
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 14px;
        background-color: {COLORS["bg_panel"]};
        color: {COLORS["text_primary"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {COLORS["text_secondary"]};
        font-size: {SIZE_BASE}px;
        font-weight: 600;
    }}
    /* Кнопки */
    QPushButton {{
        background-color: {COLORS["bg_inset"]};
        color: {COLORS["text_primary"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 5px;
        padding: 7px 14px;
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {COLORS["bg_panel_hover"]};
        border-color: {COLORS["accent_soft"]};
    }}
    QPushButton:pressed {{
        background-color: {COLORS["accent_soft"]};
        border-color: {COLORS["accent"]};
    }}
    QPushButton:disabled {{
        color: {COLORS["text_placeholder"]};
        background-color: {COLORS["bg_input"]};
        border-color: {COLORS["border"]};
    }}
    QPushButton[role="primary"] {{
        background-color: {COLORS["accent"]};
        border-color: {COLORS["accent"]};
        color: #ffffff;
    }}
    QPushButton[role="primary"]:hover {{
        background-color: {COLORS["accent_hover"]};
        border-color: {COLORS["accent_hover"]};
    }}
    /* Ввод */
    QLineEdit, QComboBox, QTextEdit {{
        background-color: {COLORS["bg_input"]};
        color: {COLORS["text_primary"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 5px;
        padding: 6px 8px;
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
        border-color: {COLORS["border_focus"]};
    }}
    QComboBox::drop-down {{
        border: none;
        padding-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLORS["bg_panel"]};
        border: 1px solid {COLORS["border"]};
        selection-background-color: {COLORS["accent"]};
        selection-color: #ffffff;
        padding: 4px;
    }}
    /* Метки */
    QLabel {{
        color: {COLORS["text_primary"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    QLabel[role="secondary"] {{
        color: {COLORS["text_secondary"]};
    }}
    QLabel[role="hint"] {{
        color: {COLORS["text_placeholder"]};
        font-size: {SIZE_SMALL}px;
    }}
    /* Статус-бар */
    QStatusBar {{
        background-color: {COLORS["bg_base"]};
        color: {COLORS["text_secondary"]};
        border-top: 1px solid {COLORS["border"]};
        font-size: {SIZE_SMALL}px;
    }}
    /* Список */
    QListWidget, QListView, QTableWidget {{
        background-color: {COLORS["bg_input"]};
        color: {COLORS["text_primary"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 5px;
        selection-background-color: {COLORS["select_bg"]};
        selection-color: {COLORS["text_primary"]};
        gridline-color: {COLORS["border"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
    }}
    /* Главный слой */
    QSplitter::handle {{
        background-color: {COLORS["border"]};
        border-radius: 2px;
    }}
    QSplitter::handle:horizontal {{
        width: 2px;
    }}
    /* Меню */
    QMenuBar {{
        background-color: {COLORS["bg_panel"]};
        color: {COLORS["text_primary"]};
        border-bottom: 1px solid {COLORS["border"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
        padding: 2px 4px;
    }}
    QMenuBar::item:selected {{
        background-color: {COLORS["bg_inset"]};
    }}
    QMenu {{
        background-color: {COLORS["bg_panel"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 6px;
        color: {COLORS["text_primary"]};
        font-family: "{FONT_BASE}";
        font-size: {SIZE_BASE}px;
        padding: 4px;
    }}
    QMenu::item:selected {{
        background-color: {COLORS["accent"]};
        color: #ffffff;
    }}
    /* Прогресс */
    QProgressBar {{
        background-color: {COLORS["bg_input"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 4px;
        text-align: center;
        color: {COLORS["text_primary"]};
        font-size: {SIZE_SMALL}px;
    }}
    QProgressBar::chunk {{
        background-color: {COLORS["accent"]};
        border-radius: 4px;
    }}
    /* Скролл */
    QScrollBar:vertical {{
        background-color: {COLORS["bg_input"]};
        width: 10px;
        border-radius: 5px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background-color: {COLORS["border"]};
        border-radius: 5px;
        min-height: 20px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {COLORS["bg_inset"]};
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0;
    }}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Применить тёмную тему к приложению."""
    app.setStyleSheet(_qss())
    app.setFont(_font(SIZE_BASE))


def apply_light_theme(app: QApplication) -> None:
    """Пока заглушка — потом светлая тема."""
    app.setStyleSheet("")
    app.setFont(_font(SIZE_BASE))


# Фабрики виджетов с типовыми настройками.
def panel(parent: QWidget | None = None) -> QWidget:
    """Базовая панель."""
    w = QWidget(parent)
    w.setContentsMargins(0, 0, 0, 0)
    return w


def heading(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(_font(SIZE_TITLE, bold=True))
    label.setProperty("role", "secondary")
    label.setAttribute(Qt.WA_TranslucentBackground)
    return label


def subheading(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setFont(_font(SIZE_BASE, bold=True))
    label.setProperty("role", "secondary")
    return label


def body(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "secondary")
    label.setWordWrap(True)
    label.setAttribute(Qt.WA_TranslucentBackground)
    return label


def hint(text: str, parent: QWidget | None = None) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("role", "hint")
    label.setWordWrap(True)
    return label


def button(text: str, parent: QWidget | None = None, primary: bool = False) -> QPushButton:
    b = QPushButton(text, parent)
    if primary:
        b.setProperty("role", "primary")
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(32)
    return b


def spacer() -> QWidget:
    sp = QWidget()
    sp.setMinimumSize(QSize(0, 8))
    sp.setMaximumSize(QSize(0, 8))
    return sp
