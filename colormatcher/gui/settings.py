"""Настройки приложения: тема, масштаб, шрифты, миниатюры, фон просмотра, недавние воркспейсы.

Файл — `%APPDATA%/colormatcher/settings.json` (переменная COLORMATCHER_HOME переопределяет
папку — удобно для тестов и «портативного» режима).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def app_home() -> Path:
    env = os.environ.get("COLORMATCHER_HOME")
    base = Path(env) if env else Path(os.environ.get("APPDATA", Path.home())) / "colormatcher"
    base.mkdir(parents=True, exist_ok=True)
    return base


ViewerBg = Literal["theme", "gray18", "black", "white", "checker"]


class AppSettings(BaseModel):
    version: int = 2                      # версия файла настроек (миграции в load)
    theme: str = "moonlight"
    ui_scale: float = 1.0                 # 0.75–2.5; применяется при запуске (QT_SCALE_FACTOR)
    font_family: str = "Segoe UI"
    font_size: float = 10.0               # pt
    slider_label_size: float = 9.0        # pt, подписи ползунков
    thumb_size: int = 128                 # px, лента кадров
    ref_thumb_size: int = 110             # px, лента референсов
    viewer_bg: ViewerBg = "theme"      # фон просмотра — цвет темы (T-0038); серый 18% — в настройках
    preview_side: int = 1600              # px, размер прокси для живого превью
    group_edit_relative: bool = False     # групповая правка: абсолютно / относительно
    wheel_step: float = 1.0               # шагов ползунка на щелчок колеса (Shift ×10, Ctrl ×0.1)
    autosave_minutes: int = 2             # 0 — выключено
    reopen_last: bool = True              # при запуске открывать последний воркспейс (T-0054)
    mask_palette: str = "calm"            # пресет палитры масок (T-0064), см. models/masks.PALETTES
    mask_alpha: float = 0.5               # непрозрачность оверлея масок
    brush_diameter: float = 120.0         # кисть масок: диаметр, px маски (длинная сторона 1024)
    brush_hardness: float = 0.0           # в панели скрыто (T-0075)
    brush_opacity: float = 1.0            # в панели скрыто (T-0075)
    brush_spread: float = 0.2             # распределение кисти 0…1: мягкое пятно … сплошной диск (T-0075)
    language: str = "en"                  # язык интерфейса (T-0083), применяется при запуске
    shortcuts: dict[str, str] = Field(default_factory=dict)   # переназначения клавиш: id -> клавиши, «» — нет (T-0085)
    recent: list[str] = Field(default_factory=list)
    geometry: str = ""                    # base64 геометрии окна
    window_state: str = ""                # base64 раскладки док-панелей

    @classmethod
    def load(cls) -> "AppSettings":
        path = app_home() / "settings.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("version", 1) < 2:
                    # v1: серый фон просмотра был значением по умолчанию — теперь фон темы (T-0038)
                    if data.get("viewer_bg") == "gray18":
                        data["viewer_bg"] = "theme"
                    data["window_state"] = ""  # раскладка изменилась (T-0037)
                    data["version"] = 2
                return cls.model_validate(data)
            except Exception:  # noqa: BLE001 — битый файл настроек не должен мешать запуску
                path.replace(path.with_suffix(".broken.json"))
        return cls()

    def save(self) -> None:
        (app_home() / "settings.json").write_text(self.model_dump_json(indent=2), encoding="utf-8")

    def last_workspace(self) -> Path | None:
        """Последний воркспейс из недавних, который ещё существует (для открытия при запуске)."""
        for r in self.recent:
            p = Path(r)
            if p.suffix.lower() == ".cmws" and not p.name.endswith(".autosave.cmws") and p.is_file():
                return p
        return None

    def push_recent(self, path: str | Path, limit: int = 10) -> None:
        p = str(Path(path).resolve())
        self.recent = [p] + [r for r in self.recent if r != p][: limit - 1]
