"""Локализация интерфейса (T-0083).

Строки интерфейса пишутся в коде на исходном языке (русском) и оборачиваются в `tr()`:

    tr("Открыть…")                     -> "Open…" при английском языке
    tr("Открыт {name}", name=path)     -> "Opened {name}" с подстановкой

Языки — JSON-файлы в `gui/lang/` (встроенные) и `%APPDATA%/colormatcher/lang/` (свои):

    {"meta": {"code": "en", "name": "English"}, "strings": {"Открыть…": "Open…", ...}}

Нет перевода — показывается исходная строка. Чтобы добавить язык, скопируйте `en.json` в
`<код>.json`, поменяйте `meta` и переведите значения (ключи не трогать). Язык выбирается в
настройках и применяется при запуске программы. Описание — docs/I18N.md.
"""

from __future__ import annotations

import json
from pathlib import Path

LANG_DIR = Path(__file__).with_name("lang")
DEFAULT = "en"
SOURCE = "ru"   # язык строк в коде

_strings: dict[str, str] = {}
_code = SOURCE


def _dirs() -> list[Path]:
    from .settings import app_home

    return [LANG_DIR, app_home() / "lang"]


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("strings"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def available() -> dict[str, str]:
    """Языки: код -> название (свои файлы перекрывают встроенные с тем же кодом)."""
    out: dict[str, str] = {}
    for d in _dirs():
        for p in sorted(d.glob("*.json")):
            data = _read(p)
            if data is not None:
                meta = data.get("meta", {})
                out[meta.get("code", p.stem)] = meta.get("name", p.stem)
    return out


def load(code: str) -> str:
    """Включить язык; возвращает код включённого (нет файла — исходный язык)."""
    global _strings, _code
    strings: dict[str, str] = {}
    found = code == SOURCE
    for d in _dirs():
        for p in d.glob("*.json"):
            data = _read(p)
            if data is not None and data.get("meta", {}).get("code", p.stem) == code:
                strings.update({k: v for k, v in data["strings"].items() if isinstance(v, str) and v})
                found = True
    _strings, _code = (strings, code) if found else ({}, SOURCE)
    return _code


def current() -> str:
    return _code


def N_(text: str) -> str:
    """Пометка «строка для перевода» без перевода (константы, которые переводятся при показе через tr())."""
    return text


def tr(text: str, **kw) -> str:
    """Перевод строки интерфейса; `kw` — подстановки {имя} (после перевода)."""
    s = _strings.get(text, text)
    return s.format(**kw) if kw else s
