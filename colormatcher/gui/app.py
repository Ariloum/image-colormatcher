"""Точка входа GUI: настройки → масштаб → тема → главное окно."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import winshell
from .settings import AppSettings

ICON = Path(__file__).with_name("icons") / "moon.png"


def startup_workspace(argv: list[str], settings: AppSettings) -> Path | None:
    """Что открыть при запуске: файл из аргументов, иначе последний воркспейс (T-0054)."""
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files and files[0].lower().endswith(".cmws"):
        return Path(files[0])
    return settings.last_workspace() if settings.reopen_last else None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    settings = AppSettings.load()
    # масштаб интерфейса — до создания QApplication (T-0024)
    if abs(settings.ui_scale - 1.0) > 1e-3 and "QT_SCALE_FACTOR" not in os.environ:
        os.environ["QT_SCALE_FACTOR"] = f"{settings.ui_scale:g}"
    winshell.set_process_app_id()  # своя «личность» в панели задач, а не python (T-0041)
    from . import i18n

    i18n.load(settings.language)   # язык — до создания окон (T-0083)
    app = QApplication(argv)
    app.setApplicationName("Image ColorMatcher")
    from .. import __version__

    app.setApplicationVersion(__version__)
    app.setOrganizationName("colormatcher")
    app.setWindowIcon(QIcon(str(ICON)))

    from .main_window import MainWindow  # после QApplication

    win = MainWindow(settings)
    if app.platformName() == "windows":
        try:  # команда/имя/иконка для «Закрепить на панели задач» — до показа окна (T-0052)
            winshell.set_window_relaunch(int(win.winId()))
        except OSError:
            pass  # не критично: без этого просто не будет иконки у закреплённой кнопки
    win.show()
    app._main_window = win   # при смене языка окно заменяется новым (T-0086)
    target = startup_workspace(argv, settings)
    if target is not None:
        win.open_path(str(target), confirm=False)
    return app.exec()
