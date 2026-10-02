"""Импорт фото в воркспейс: папка с подпапками = сцены, отдельные файлы (T-0025, ядро)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..core.imageio import is_image
from ..models.workspace import PhotoItem, Workspace


def natural_key(p: Path) -> list:
    """«img2» раньше «img10»."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.as_posix())]


def scan_folder(folder: str | Path, recursive: bool = True) -> dict[str, list[Path]]:
    """Файлы изображений по сценам: имя сцены = путь подпапки относительно `folder`
    (файлы в самой папке — сцена с именем папки)."""
    folder = Path(folder).resolve()
    it = folder.rglob("*") if recursive else folder.glob("*")
    scenes: dict[str, list[Path]] = {}
    for p in sorted((p for p in it if p.is_file() and is_image(p)), key=natural_key):
        rel = p.parent.relative_to(folder)
        name = folder.name if rel == Path(".") else f"{folder.name}/{rel.as_posix()}"
        scenes.setdefault(name, []).append(p)
    return scenes


def add_folder(ws: Workspace, folder: str | Path, recursive: bool = True,
               scenes: bool = True, scene: str | None = None) -> list[PhotoItem]:
    """Добавить папку. scenes=True — подпапки становятся сценами; иначе всё в `scene`."""
    added = []
    for name, files in scan_folder(folder, recursive).items():
        target = scene or (name if scenes else Path(folder).resolve().name)
        added += [ph for f in files if (ph := ws.add_photo(f, target))]
    return added


def add_files(ws: Workspace, files: Iterable[str | Path], scene: str) -> list[PhotoItem]:
    return [ph for f in files if is_image(f) and (ph := ws.add_photo(f, scene))]
