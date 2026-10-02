"""Темы/скины: JSON-палитра токенов + общий QSS-шаблон (T-0023).

Встроенные темы — `gui/themes/*.json`, пользовательские — `%APPDATA%/colormatcher/themes/*.json`
(перекрывают встроенные с тем же именем файла). Новая тема = один JSON:
{"name": "…", "dark": true, "bg": "#…", "panel": …, "surface", "raised", "border",
 "text", "text_dim", "accent", "accent_text", "hover", "selection", "danger", "viewer"}.
Ни один цвет интерфейса не задаётся мимо палитры.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .settings import AppSettings, app_home

BUILTIN = Path(__file__).with_name("themes")
TOKENS = ("bg", "panel", "surface", "raised", "border", "text", "text_dim", "accent",
          "accent_text", "hover", "selection", "danger", "viewer")


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    dark: bool
    colors: dict[str, str]

    def __getitem__(self, token: str) -> str:
        return self.colors[token]

    def qcolor(self, token: str) -> QColor:
        return QColor(self.colors[token])


class ThemeHolder:
    """Текущая тема для виджетов: цвета читаются при отрисовке, поэтому смена темы
    применяется на лету (`set` + перерисовка)."""

    def __init__(self, theme: Theme):
        self.theme = theme

    def set(self, theme: Theme) -> None:
        self.theme = theme

    def __getitem__(self, token: str) -> str:
        return self.theme.colors[token]

    @property
    def dark(self) -> bool:
        return self.theme.dark


def available() -> dict[str, Theme]:
    themes: dict[str, Theme] = {}
    for folder in (BUILTIN, app_home() / "themes"):
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                colors = {t: data[t] for t in TOKENS}
            except (KeyError, ValueError):
                continue  # неполная тема — пропускаем, не ломая запуск
            themes[f.stem] = Theme(f.stem, data.get("name", f.stem), bool(data.get("dark", True)), colors)
    return themes


def get(key: str) -> Theme:
    themes = available()
    return themes.get(key) or themes["moonlight"]


QSS = """
* {{ font-family: "{font}"; font-size: {fs}pt; color: {text}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QWidget#Panel, QDockWidget > QWidget {{ background: {panel}; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {bg}; color: {text_dim}; padding: {p}px {p2}px; text-transform: uppercase; }}
QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: {p}px; }}
QMenuBar {{ background: {bg}; }}
QMenuBar::item {{ padding: {p}px {p2}px; background: transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {selection}; }}
QMenu {{ background: {panel}; border: 1px solid {border}; padding: {p}px 0; }}
QMenu::item {{ padding: {p}px {p3}px; }}
QMenu::separator {{ height: 1px; background: {border}; margin: {p}px {p}px; }}
QToolBar {{ background: {bg}; border: none; spacing: {p}px; padding: {p}px; }}
QStatusBar {{ background: {bg}; color: {text_dim}; }}
QPushButton, QToolButton {{
    background: {raised}; border: 1px solid {border}; border-radius: {r}px; padding: {p}px {p2}px;
}}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {selection}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {text_dim}; }}
QPushButton#Accent {{ background: {accent}; color: {accent_text}; border-color: {accent}; font-weight: 600; }}
QPushButton#Accent:hover {{ background: {accent}; border-color: {text}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
    background: {surface}; border: 1px solid {border}; border-radius: {r}px; padding: {ph}px {p}px;
    selection-background-color: {selection};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {border}; selection-background-color: {selection}; }}
QAbstractItemView {{ background: {surface}; border: 1px solid {border}; outline: none;
    selection-background-color: {selection}; selection-color: {text}; alternate-background-color: {panel}; }}
QAbstractItemView::item:hover {{ background: {hover}; }}
QAbstractItemView::item:selected {{ background: {selection}; }}
QHeaderView::section {{ background: {panel}; color: {text_dim}; border: none; border-bottom: 1px solid {border}; padding: {ph}px {p}px; }}
QTabBar::tab {{ background: {bg}; color: {text_dim}; padding: {p}px {p2}px; border: 1px solid {border}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {panel}; color: {text}; border-top: 2px solid {accent}; }}
QTabWidget::pane {{ border: 1px solid {border}; background: {panel}; }}
QGroupBox {{ border: 1px solid {border}; border-radius: {r}px; margin-top: {g}px; padding-top: {p}px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: {p}px; padding: 0 {ph}px; color: {accent}; font-weight: 600; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: {ind}px; height: {ind}px; border: 1px solid {border}; background: {surface}; }}
QCheckBox::indicator {{ border-radius: {r}px; }}
QRadioButton::indicator {{ border-radius: {indr}px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QSlider::groove:horizontal {{ height: {gh}px; background: {border}; border-radius: {ghr}px; }}
QSlider::handle:horizontal {{ width: {hw}px; margin: -{hm}px 0; border-radius: {hr}px; background: {text}; border: 2px solid {accent}; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: {ghr}px; }}
QProgressBar {{ background: {surface}; border: 1px solid {border}; border-radius: {r}px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: {r}px; }}
QScrollBar:vertical {{ background: {bg}; width: {sb}px; margin: 0; }}
QScrollBar:horizontal {{ background: {bg}; height: {sb}px; margin: 0; }}
QScrollBar::handle {{ background: {raised}; border-radius: {sbr}px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {hover}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QSplitter::handle, QMainWindow::separator {{ background: {border}; width: 1px; height: 1px; }}
QLabel#Dim {{ color: {text_dim}; }}
QLabel#Title {{ font-size: {fs_title}pt; font-weight: 600; }}
QLabel#Accent {{ color: {accent}; }}
QLabel#Danger {{ color: {danger}; }}
QFrame#Separator {{ background: {border}; max-height: 1px; min-height: 1px; }}
QFrame#VSeparator {{ background: {border}; max-width: 1px; min-width: 1px; border: none; }}
"""


def stylesheet(theme: Theme, s: AppSettings) -> str:
    k = s.font_size / 10.0  # отступы растут вместе со шрифтом
    px = lambda v: max(1, round(v * k))  # noqa: E731
    return QSS.format(
        font=s.font_family, fs=s.font_size, fs_title=round(s.font_size * 1.25, 1),
        p=px(4), p2=px(10), p3=px(18), ph=px(2), r=px(4), g=px(14),
        ind=px(14), indr=px(7), gh=px(4), ghr=px(2), hw=px(14), hm=px(6), hr=px(7),
        sb=px(10), sbr=px(5), **theme.colors,
    )


def apply(app: QApplication, s: AppSettings) -> Theme:
    theme = get(s.theme)
    app.setStyle("Fusion")
    pal = QPalette()
    roles = {
        QPalette.Window: "bg", QPalette.WindowText: "text", QPalette.Base: "surface",
        QPalette.AlternateBase: "panel", QPalette.Text: "text", QPalette.Button: "raised",
        QPalette.ButtonText: "text", QPalette.Highlight: "selection", QPalette.HighlightedText: "text",
        QPalette.ToolTipBase: "raised", QPalette.ToolTipText: "text", QPalette.PlaceholderText: "text_dim",
        QPalette.Link: "accent", QPalette.BrightText: "danger",
    }
    for role, token in roles.items():
        pal.setColor(role, theme.qcolor(token))
    pal.setColor(QPalette.Disabled, QPalette.Text, theme.qcolor("text_dim"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, theme.qcolor("text_dim"))
    app.setPalette(pal)
    app.setFont(QFont(s.font_family, round(s.font_size)))
    app.setStyleSheet(stylesheet(theme, s))
    return theme
