"""Проверка локализации GUI (T-0083).

    python tools/i18n_check.py            — отчёт: строки без перевода в en.json, необёрнутые русские строки
    python tools/i18n_check.py --stub     — дописать недостающие строки в en.json с пустым переводом ("")

Строки интерфейса — аргументы tr("…") и plural(n, "…", "…", "…") в colormatcher/gui, плюс подписи из ядра,
которые GUI переводит при показе (фильтры, параметры, палитры, служебные метки, темы).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "colormatcher" / "gui"
LANG = GUI / "lang"
CYR = re.compile("[А-Яа-яЁё]")
# русские строки, которые переводить не нужно (названия языков, служебные значения)
ALLOWED = {"Русский"}


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def scan() -> tuple[set[str], list[str]]:
    """(msgid из tr/plural, необёрнутые русские строки «файл:строка: текст»)."""
    msgids: set[str] = set()
    loose: list[str] = []
    for p in sorted(GUI.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        wrapped: set[int] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                name = n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ""
                args = n.args[:1] if name in ("tr", "N_") else n.args[1:4] if name == "plural" else []
                for a in args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        msgids.add(a.value)
                        wrapped.add(id(a))
        docs = _docstrings(tree)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str) and CYR.search(n.value)
                    and id(n) not in wrapped and id(n) not in docs and n.value not in ALLOWED):
                loose.append(f"{p.relative_to(ROOT)}:{n.lineno}: {n.value[:70]!r}")
    return msgids, loose


def core_labels() -> set[str]:
    """Подписи из ядра и моделей, которые GUI показывает через tr(переменная)."""
    sys.path.insert(0, str(ROOT))
    from colormatcher.core.filters import FilterRegistry
    from colormatcher.core.filters.color_balance import AXES
    from colormatcher.gui import theme
    from colormatcher.models import masks

    out: set[str] = set()
    for flt in FilterRegistry.all():
        out.add(flt.label)
        for spec in flt.params:
            out.add(spec.label)
    out |= {label for _k, label, _c in AXES}
    out |= {title for title, _c in masks.PALETTES.values()}
    out |= set(masks.SLOT_NAMES.values())
    out |= {th.name for th in theme.available().values()}
    return out   # английские подписи тоже — их переводят на другие языки


def main() -> int:
    msgids, loose = scan()
    msgids |= core_labels()
    en_path = LANG / "en.json"
    en = json.loads(en_path.read_text(encoding="utf-8"))
    missing = sorted(m for m in msgids if CYR.search(m) and not en["strings"].get(m))   # английские — по желанию
    unused = sorted(k for k in en["strings"] if k not in msgids)
    print(f"строк интерфейса: {len(msgids)}; без перевода: {len(missing)}; лишних в en.json: {len(unused)}; "
          f"необёрнутых русских строк: {len(loose)}")
    for m in missing[:50]:
        print("  нет перевода:", repr(m))
    for u in unused[:20]:
        print("  лишняя:", repr(u))
    for s in loose[:50]:
        print("  не обёрнута:", s)
    if "--stub" in sys.argv:
        for m in missing:
            en["strings"].setdefault(m, "")
        en_path.write_text(json.dumps(en, ensure_ascii=False, indent=1, sort_keys=False), encoding="utf-8")
    return 1 if missing or loose else 0


if __name__ == "__main__":
    raise SystemExit(main())
