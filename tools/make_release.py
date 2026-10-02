"""Сборка релиза Image ColorMatcher (T-0087).

    python-embed\\python.exe tools\\make_release.py [--out ПАПКА] [--no-zip] [--check]

1. **Исходники** — в папку релиза (`--out`, по умолчанию `..\\image-colormatcher-release`) копируется только
   публичная часть проекта (код, тесты, инструменты, документация, README/CHANGELOG/LICENSE). Личные и рабочие
   файлы (тикеты, заметки, воркспейсы, демо-фото, python-embed, служебные скрипты tests/_out) не попадают;
   в документах имена заменяются нейтральными словами. Папка релиза — отдельный git-репозиторий (его `.git`
   и `dist/` не трогаются).
2. **Сборка для Windows** — `dist/ColorMatcher-<версия>-win64/`: ColorMatcher.exe, bat-файлы, код программы
   и урезанный встроенный Python (без pip/pytest, из Qt — только Core/Gui/Widgets), затем zip-архив.
   Распаковал — запустил ColorMatcher.exe: установка не нужна.
3. `--check` — архив распаковывается во временную папку и проверяется: `--version`, фильтр из CLI,
   окно GUI в режиме offscreen.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from colormatcher import __version__  # noqa: E402

# --- публичная часть исходников ------------------------------------------------------------------
SOURCE_ITEMS = [
    "colormatcher", "tools", "tests", "docs", "calibration/README.md", "calibration/list.csv",
    "README.md", "CHANGELOG.md", "LICENSE", "requirements.txt", "pytest.ini",
    "colormatch.bat", "colormatch-gui.bat",
]
SOURCE_SKIP = [
    "__pycache__", ".pytest_cache", "tests/_out", "docs/QUESTIONS.md", "docs/analysis",
]
SKIP_SUFFIXES = {".pyc", ".pyo"}
# личное в документах -> нейтрально (только в копии релиза) и маркеры утечек — из ЛОКАЛЬНОГО файла
# tools/release_private.json (в репозиторий и релиз не попадает): {"sanitize": [[regex, замена]…], "markers": [regex…]}
_PRIVATE = ROOT / "tools" / "release_private.json"
_priv = __import__("json").loads(_PRIVATE.read_text(encoding="utf-8")) if _PRIVATE.exists() else {}
SANITIZE = [(re.compile(rx), repl) for rx, repl in _priv.get("sanitize", [])]
PRIVATE_MARKERS = re.compile("|".join(_priv.get("markers", [])) or r"(?!x)x")
SOURCE_SKIP_EXTRA = ["tools/release_private.json"]

RELEASE_GITIGNORE = """python-embed/
dist/
__pycache__/
*.pyc
.pytest_cache/
tests/_out/
workspaces/
*.autosave.cmws
*.autosave.masks/
ColorMatcher.exe
"""

# --- сборка для Windows ---------------------------------------------------------------------------
APP_FILES = ["ColorMatcher.exe", "colormatch.bat", "colormatch-gui.bat", "README.md", "CHANGELOG.md", "LICENSE"]
SITE_SKIP = re.compile(r"^(pip|_pytest|pytest|pluggy|iniconfig|pyflakes|py\.py)([-_.].*)?$", re.I)
PYSIDE_KEEP_FILES = re.compile(
    r"^(__init__\.py|_config\.py|_git_pyside_version\.py|py\.typed|Qt(Core|Gui|Widgets)\.pyd|"
    r"Qt6(Core|Gui|Widgets|Svg)\.dll|pyside6\.abi3\.dll|msvcp140.*\.dll|vcruntime140.*\.dll|concrt140\.dll)$", re.I)
PYSIDE_KEEP_DIRS = {"support", "__pycache__"}
PYSIDE_PLUGINS = {"platforms", "imageformats", "styles", "iconengines", "generic", "platforminputcontexts"}


def _skip(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    return any(rel == s or rel.startswith(s + "/") or f"/{s}/" in f"/{rel}/" for s in SOURCE_SKIP + SOURCE_SKIP_EXTRA)


def _copy_tree(src: Path, dst: Path, root: Path) -> int:
    n = 0
    for p in sorted(src.rglob("*")) if src.is_dir() else [src]:
        rel = p.relative_to(root).as_posix()
        if _skip(rel) or p.suffix in SKIP_SUFFIXES or p.is_dir():
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".md":
            text = p.read_text(encoding="utf-8")
            for rx, repl in SANITIZE:
                text = rx.sub(repl, text)
            target.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(p, target)
        n += 1
    return n


def make_source(out: Path) -> None:
    """Публичные исходники -> out (кроме .git и dist/, которые сохраняются)."""
    out.mkdir(parents=True, exist_ok=True)
    for item in out.iterdir():
        if item.name in (".git", "dist"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    n = 0
    for item in SOURCE_ITEMS:
        src = ROOT / item
        if not src.exists():
            print(f"  нет {item} — пропущено")
            continue
        n += _copy_tree(src, out, ROOT)
    (out / ".gitignore").write_text(RELEASE_GITIGNORE, encoding="utf-8")
    leaks = []
    for p in out.rglob("*"):
        if p.is_file() and ".git" not in p.parts and "dist" not in p.relative_to(out).parts[:1] \
                and p.suffix in (".md", ".py", ".json", ".bat", ".cs", ".jsx", ".txt", ".ini", ".csv"):
            if PRIVATE_MARKERS.search(p.read_text(encoding="utf-8", errors="ignore")):
                leaks.append(p.relative_to(out).as_posix())
    if leaks:
        raise SystemExit(f"в релиз попали личные данные: {leaks}")
    print(f"исходники: {n} файлов -> {out}")


def _copy_embed(src: Path, dst: Path) -> None:
    site_src = src / "Lib" / "site-packages"
    for p in src.iterdir():   # корень встроенного Python (dll, pyd, zip стандартной библиотеки)
        if p.is_file():
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst / p.name)
    for p in site_src.iterdir():
        target = dst / "Lib" / "site-packages" / p.name
        if SITE_SKIP.match(p.name):
            continue
        if p.name == "PySide6":
            for q in p.iterdir():
                if q.is_file() and PYSIDE_KEEP_FILES.match(q.name):
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(q, target / q.name)
                elif q.is_dir() and q.name in PYSIDE_KEEP_DIRS:
                    shutil.copytree(q, target / q.name)
                elif q.is_dir() and q.name == "plugins":
                    for plug in q.iterdir():
                        if plug.name in PYSIDE_PLUGINS:
                            shutil.copytree(plug, target / "plugins" / plug.name)
        elif p.is_dir():
            shutil.copytree(p, target, ignore=shutil.ignore_patterns("tests", "testing") if p.name == "numpy" else None)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def make_build(out: Path, make_zip: bool = True) -> Path:
    name = f"ColorMatcher-{__version__}-win64"
    build = out / "dist" / name
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    for f in APP_FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, build / f)
    shutil.copytree(ROOT / "colormatcher", build / "colormatcher",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _copy_embed(ROOT / "python-embed", build / "python-embed")
    size = sum(p.stat().st_size for p in build.rglob("*") if p.is_file())
    print(f"сборка: {build} ({size / 2**20:.0f} МБ)")
    if not make_zip:
        return build
    archive = out / "dist" / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(build.rglob("*")):
            if p.is_file():
                z.write(p, Path(name) / p.relative_to(build))
    print(f"архив: {archive} ({archive.stat().st_size / 2**20:.0f} МБ)")
    return archive


SMOKE_GUI = r"""
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["COLORMATCHER_HOME"] = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([])
from colormatcher.gui import i18n
i18n.load("en")
from colormatcher.gui.main_window import MainWindow
from colormatcher.gui.settings import AppSettings
w = MainWindow(AppSettings()); w.show()
t0 = time.time()
while time.time() - t0 < 1.0:
    app.processEvents()
assert w.menuBar().actions()[0].text() == "File"
print("gui ok", w.windowTitle())
w.close()
"""


def check(archive: Path) -> None:
    """Распаковать архив во временную папку и запустить программу оттуда."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
        app = next(tmp.iterdir())
        py = app / "python-embed" / "python.exe"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        run = lambda *a: subprocess.run([str(py), *a], cwd=app, env=env, capture_output=True, text=True,  # noqa: E731
                                        encoding="utf-8", errors="replace", timeout=300)
        r = run("-m", "colormatcher.cli", "--version")
        assert r.returncode == 0 and __version__ in r.stdout, r.stderr
        print(" ", r.stdout.strip())
        img = tmp / "probe.png"
        r = run("-c", f"import numpy as np; from colormatcher.core import imageio; "
                      f"imageio.save_image(r'{img}', np.random.default_rng(1).random((64, 96, 3)).astype('float32'))")
        assert r.returncode == 0, r.stderr
        r = run("-m", "colormatcher.cli", "apply", str(img), "-o", str(tmp / "out"), "--set", "cb.midtones.cr=20")
        assert r.returncode == 0 and any((tmp / "out").glob("*")), r.stdout + r.stderr
        print("  cli apply ok")
        r = run("-c", SMOKE_GUI, str(tmp / "home"))
        assert r.returncode == 0 and "gui ok" in r.stdout, r.stdout + r.stderr
        print(" ", r.stdout.strip().splitlines()[-1])
    print("проверка архива: ok")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "image-colormatcher-release")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--no-build", action="store_true", help="только исходники")
    ap.add_argument("--check", action="store_true", help="проверить архив после сборки")
    a = ap.parse_args()
    print(f"Image ColorMatcher {__version__}")
    make_source(a.out)
    if not a.no_build:
        archive = make_build(a.out, not a.no_zip)
        if a.check and archive.suffix == ".zip":
            check(archive)


if __name__ == "__main__":
    main()
