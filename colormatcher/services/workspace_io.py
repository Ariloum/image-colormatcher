"""Сохранение / загрузка / упаковка воркспейса (T-0018, T-0019).

* save: атомарная запись (временный файл → замена), прошлая версия — в `*.cmws.bak`;
  пути фото пересчитываются относительно папки нового файла.
* load: миграции схемы по `schema_version`.
* pack: воркспейс + копии всех фото в одну папку (структура сцен сохраняется),
  по желанию — и отрендеренные версии.
* relink: поиск пропавших файлов по имени в указанной папке.
* маски-метки (T-0058): `<имя>.masks/<id фото>.npz` рядом с файлом воркспейса; пишутся
  только изменённые, файлы убранных фото / пустых масок удаляются.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

import numpy as np

from ..models.workspace import SCHEMA_VERSION, WS_SUFFIX, Workspace

def _v1_to_v2(data: dict) -> dict:
    """v1: общий набор + наборы сцен с усреднением -> v2: список референсов + активный (T-0050)."""
    refs, seen = [], set()
    sets = [data.pop("global_refs", None)] + [sc.pop("refs", None) for sc in data.get("scenes", [])]
    for refset in sets:
        if not refset:
            continue
        offset = refset.get("offset") or {}
        for item in refset.get("items", []):
            pid = item.get("photo_id")
            if pid and pid not in seen:
                seen.add(pid)
                refs.append({"photo_id": pid, "offset": offset})
    data["refs"] = refs
    data["active_ref"] = refs[0]["photo_id"] if refs else None
    return data


# миграции: версия -> функция(dict) -> dict следующей версии
MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _v1_to_v2}


def ws_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.suffix.lower() == WS_SUFFIX else path.with_name(path.name + WS_SUFFIX)


def new_workspace(path: str | Path, name: str | None = None) -> Workspace:
    path = ws_path(path)
    ws = Workspace(name=name or path.stem)
    ws.set_root(path.parent.resolve())
    return ws


def masks_dir(path: str | Path) -> Path:
    """Папка масок воркспейса: `shoot.cmws` -> `shoot.masks`."""
    path = ws_path(path)
    return path.with_name(path.name[: -len(WS_SUFFIX)] + ".masks")


def save_masks(ws: Workspace, folder: Path) -> None:
    present = {ph.id for ph in ws.all_photos()}
    keep = set()
    for pid, mask in ws.masks.items():
        if pid not in present or mask is None or not mask.any():
            continue
        keep.add(pid)
        key, ver = f"{folder}|{pid}", ws._mask_ver.get(pid, 0)
        target = folder / f"{pid}.npz"
        if ws._mask_saved.get(key) == ver and target.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{pid}.tmp.npz"
        np.savez_compressed(tmp, weights=mask)
        os.replace(tmp, target)
        ws._mask_saved[key] = ver
    if folder.is_dir():
        for f in folder.glob("*.npz"):
            if f.stem not in keep:
                f.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()


def load_masks(ws: Workspace, folder: Path) -> None:
    if not folder.is_dir():
        return
    present = {ph.id for ph in ws.all_photos()}
    for f in folder.glob("*.npz"):
        if f.stem in present:
            with np.load(f) as data:
                ws.masks[f.stem] = data["weights"]
            ws._mask_saved[f"{folder}|{f.stem}"] = ws._mask_ver.get(f.stem, 0)


def write_json(ws: Workspace, path: Path, indent: int = 2) -> None:
    """Записать JSON воркспейса и маски как есть (без пересчёта путей) — для автосохранения."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(ws.model_dump_json(indent=indent), encoding="utf-8")
    os.replace(tmp, path)
    save_masks(ws, masks_dir(path))


def save(ws: Workspace, path: str | Path) -> Path:
    path = ws_path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent != ws.root:
        ws.rebase(path.parent)
    save_masks(ws, masks_dir(path))
    data = ws.model_dump_json(indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)
    return path


def load(path: str | Path) -> Workspace:
    path = ws_path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("schema_version", 1))
    if version > SCHEMA_VERSION:
        raise ValueError(f"{path.name}: схема v{version} новее поддерживаемой v{SCHEMA_VERSION} — обновите программу")
    while version < SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    ws = Workspace.model_validate(data)
    ws.set_root(path.parent)
    load_masks(ws, masks_dir(path))
    return ws


def _unique(dst: Path, taken: set[Path]) -> Path:
    stem, ext, n = dst.stem, dst.suffix, 1
    while dst in taken or dst.exists():
        dst = dst.with_name(f"{stem}_{n}{ext}")
        n += 1
    taken.add(dst)
    return dst


def pack(ws: Workspace, out_dir: str | Path, name: str | None = None) -> Path:
    """Скопировать все фото в `out_dir/photos/<сцена>/` и сохранить туда воркспейс.

    Исходный воркспейс не меняется — пакуется копия. Пропавшие файлы — ошибка
    (проверьте `missing()` / relink перед упаковкой).
    """
    out_dir = Path(out_dir).resolve()
    missing = ws.missing()
    if missing:
        raise FileNotFoundError(f"нет файлов: {', '.join(p.path for p in missing[:5])}"
                                + (" …" if len(missing) > 5 else ""))
    packed = ws.model_copy(deep=True)
    packed.set_root(ws.root)
    packed._mask_saved = {}   # в новой папке маски пишутся заново
    targets: dict[str, Path] = {}
    taken: set[Path] = set()
    for sc in packed.scenes:
        folder = out_dir / "photos" / _safe(sc.name)
        for ph in sc.photos:
            targets[ph.id] = _unique(folder / ph.name, taken)
    for ph in packed.all_photos():
        src, dst = packed.abs_path(ph), targets[ph.id]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        ph.path = str(dst)
    packed.set_root(out_dir)
    for ph in packed.all_photos():
        ph.path = packed.rel_path(ph.path)
    return save(packed, out_dir / f"{name or ws.name}{WS_SUFFIX}")


def _safe(name: str) -> str:
    """Имя сцены -> путь папки (слэши сохраняются как вложенность, запрещённые символы — «_»)."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    bad = '<>:"|?*'
    return "/".join("".join("_" if c in bad else c for c in p) for p in parts) or "scene"


def relink(ws: Workspace, search_dir: str | Path, recursive: bool = True) -> dict[str, str]:
    """Найти пропавшие файлы по имени в `search_dir`. Возвращает {id: новый путь}."""
    missing = ws.missing()
    if not missing:
        return {}
    root = Path(search_dir).resolve()
    index: dict[str, list[Path]] = {}
    for p in (root.rglob("*") if recursive else root.glob("*")):
        if p.is_file():
            index.setdefault(p.name.lower(), []).append(p)
    fixed = {}
    for ph in missing:
        found = index.get(ph.name.lower(), [])
        if len(found) == 1:
            ph.path = ws.rel_path(found[0])
            fixed[ph.id] = ph.path
    return fixed
