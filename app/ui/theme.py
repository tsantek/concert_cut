"""concert_cut visual theme — late-night studio console, not default Qt chrome."""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

# Palette
BG = "#12141a"
BG_ELEVATED = "#1a1e28"
BG_INPUT = "#0e1016"
BG_HOVER = "#242936"
BORDER = "#2c3344"
BORDER_FOCUS = "#3d9b8f"
TEXT = "#e8e4dc"
TEXT_MUTED = "#8b8790"
ACCENT = "#3d9b8f"
ACCENT_HOVER = "#4cb3a5"
ACCENT_DIM = "#2a6b63"
DANGER = "#c45c5c"
WAVE_BG = "#0c0e14"


def pick_ui_font() -> QFont:
    """Prefer a distinctive installed face over the system UI default."""
    preferred = [
        "Avenir Next",
        "Avenir",
        "Futura",
        "Gill Sans",
        "Bahnschrift",
        "Segoe UI Variable",
        "Segoe UI",
    ]
    available = set(QFontDatabase.families())
    for name in preferred:
        if name in available:
            font = QFont(name, 13)
            font.setStyleStrategy(QFont.PreferAntialias)
            return font
    font = QFont()
    font.setPointSize(13)
    return font


def app_stylesheet() -> str:
    return f"""
    * {{
        font-size: 13px;
    }}

    QMainWindow, QDialog {{
        background-color: {BG};
        color: {TEXT};
    }}

    QWidget {{
        background-color: {BG};
        color: {TEXT};
        selection-background-color: {ACCENT_DIM};
        selection-color: {TEXT};
    }}

    QLabel {{
        background: transparent;
        color: {TEXT};
    }}

    QLabel#BrandTitle {{
        font-size: 42px;
        font-weight: 600;
        letter-spacing: 1px;
        color: {TEXT};
    }}

    QLabel#BrandSub {{
        font-size: 14px;
        color: {TEXT_MUTED};
        padding-bottom: 8px;
    }}

    QLabel#SectionHint, QLabel#MutedLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
    }}

    QLabel#ProjectTitle {{
        font-size: 18px;
        font-weight: 600;
        color: {TEXT};
    }}

    QFrame#HeroCard, QFrame#Panel {{
        background-color: {BG_ELEVATED};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
        background-color: {BG_INPUT};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 9px 12px;
        selection-background-color: {ACCENT_DIM};
    }}

    QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border: 1px solid {BORDER_FOCUS};
    }}

    QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled {{
        color: {TEXT_MUTED};
        background-color: {BG};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 28px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {BG_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER};
        selection-background-color: {ACCENT_DIM};
    }}

    QPushButton {{
        background-color: {BG_HOVER};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 9px 16px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        background-color: #2e3545;
        border-color: #3a4358;
    }}

    QPushButton:pressed {{
        background-color: #1c212c;
    }}

    QPushButton:disabled {{
        color: {TEXT_MUTED};
        background-color: {BG};
        border-color: {BORDER};
    }}

    QPushButton#PrimaryButton {{
        background-color: {ACCENT};
        color: #0a1211;
        border: 1px solid {ACCENT};
        font-weight: 600;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {ACCENT_HOVER};
        border-color: {ACCENT_HOVER};
    }}

    QPushButton#PrimaryButton:pressed {{
        background-color: {ACCENT_DIM};
    }}

    QPushButton#GhostButton {{
        background-color: transparent;
        border: 1px solid {BORDER};
    }}

    QPushButton#ModeTab, QPushButton#ModeTabActive {{
        border-radius: 6px;
        padding: 7px 16px;
        font-weight: 600;
        min-width: 118px;
    }}

    QPushButton#ModeTab {{
        background-color: transparent;
        border: 1px solid {BORDER};
        color: {TEXT_MUTED};
    }}

    QPushButton#ModeTab:hover {{
        color: {TEXT};
        border-color: #3a4358;
    }}

    QPushButton#ModeTabActive {{
        background-color: {ACCENT_DIM};
        border: 1px solid {ACCENT};
        color: {TEXT};
    }}

    QPushButton#ModeTabActive:disabled {{
        background-color: {ACCENT_DIM};
        border: 1px solid {ACCENT};
        color: {TEXT};
    }}

    QCheckBox {{
        spacing: 8px;
        color: {TEXT};
        background: transparent;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {BORDER};
        background: {BG_INPUT};
    }}

    QCheckBox::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}

    QCheckBox#PlaylistCheck::indicator {{
        width: 22px;
        height: 22px;
        border-radius: 6px;
        border: 2px solid {BORDER};
        background: {BG_INPUT};
    }}

    QCheckBox#PlaylistCheck::indicator:hover {{
        border-color: {ACCENT};
    }}

    QCheckBox#PlaylistCheck::indicator:checked {{
        background: {ACCENT};
        border-color: {ACCENT};
    }}

    QWidget#CheckCell {{
        background: transparent;
    }}

    QProgressBar {{
        background-color: {BG_INPUT};
        border: 1px solid {BORDER};
        border-radius: 6px;
        text-align: center;
        color: {TEXT};
        height: 18px;
    }}

    QProgressBar::chunk {{
        background-color: {ACCENT};
        border-radius: 5px;
    }}

    QScrollBar:horizontal {{
        background: {BG_INPUT};
        height: 10px;
        margin: 0;
        border-radius: 5px;
        border: none;
    }}

    QScrollBar::handle:horizontal {{
        background: {BORDER};
        min-width: 40px;
        border-radius: 5px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: {ACCENT_DIM};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    QScrollBar:vertical {{
        background: {BG_INPUT};
        width: 10px;
        border-radius: 5px;
    }}

    QScrollBar::handle:vertical {{
        background: {BORDER};
        min-height: 32px;
        border-radius: 5px;
    }}

    QTableWidget {{
        background-color: {BG_ELEVATED};
        alternate-background-color: #171b24;
        color: {TEXT};
        gridline-color: {BORDER};
        border: 1px solid {BORDER};
        border-radius: 10px;
        outline: none;
    }}

    QTableWidget::item {{
        padding: 6px;
        border: none;
    }}

    QTableWidget::item:selected {{
        background-color: {ACCENT_DIM};
        color: {TEXT};
    }}

    QHeaderView::section {{
        background-color: {BG};
        color: {TEXT_MUTED};
        border: none;
        border-bottom: 1px solid {BORDER};
        border-right: 1px solid {BORDER};
        padding: 8px 10px;
        font-weight: 600;
        font-size: 11px;
    }}

    QTextEdit {{
        font-family: "Avenir Next", "Avenir", "Segoe UI", sans-serif;
    }}

    QDialogButtonBox QPushButton {{
        min-width: 88px;
    }}
    """


def apply_theme(app: QApplication) -> None:
    app.setFont(pick_ui_font())
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
