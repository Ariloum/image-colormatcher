"""Групповой экспорт: те же имена или новые с инкрементом версии (T-0020).

План экспорта строится заранее (`plan_export`): имена вычисляются с учётом уже
запланированных, поэтому два фото с одинаковым именем из разных сцен не перезапишут
друг друга. Перезапись исходников — только с force=True.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core import imageio, lut as lutlib
from ..core.pipeline import render_masked
from ..models.stack import AdjustmentStack
from ..models.workspace import ExportSettings, Workspace
from .matching import run_parallel
from .naming import Naming, next_version_path, output_path
from .workspace_io import _safe

_EXT = {"jpg": ".jpg", "png": ".png", "tif": ".tif"}


def plan_export(ws: Workspace, photo_ids: set[str] | None = None, scenes: list[str] | None = None,
                settings: ExportSettings | None = None, force: bool = False) -> list[dict]:
    s = settings or ws.export
    out_root = Path(s.out_dir) if s.out_dir else None
    if out_root and not out_root.is_absolute():
        out_root = (ws.root / out_root).resolve()
    planned: set[Path] = set()
    jobs = []
    for sc in ws.scenes:
        if scenes and sc.name not in scenes and sc.id not in scenes:
            continue
        for ph in sc.photos:
            if photo_ids and ph.id not in photo_ids:
                continue
            src = ws.abs_path(ph)
            folder = (out_root / _safe(sc.name) if s.keep_structure else out_root) if out_root else src.parent
            ext = _EXT.get(s.format, src.suffix.lower())
            dst = output_path(src, folder, s.naming, suffix=s.suffix, ext=ext)
            while dst in planned:  # то же имя уже в плане (версии считаются по диску)
                if s.naming is Naming.version:
                    dst = next_version_path(folder, dst.stem, ext)
                    if dst in planned:
                        dst = dst.with_name(f"{dst.stem}_{len(planned)}{ext}")
                else:
                    dst = dst.with_name(f"{dst.stem}_{len(planned)}{ext}")
            planned.add(dst)
            job = {"id": ph.id, "src": str(src), "dst": str(dst), "stack": ph.stack.model_dump(),
                   "quality": s.quality, "bit_depth": s.bit_depth}
            layers = ph.active_layers()
            mask = ws.mask(ph.id)
            if layers or (mask is not None and mask[..., 8].any()):   # слои меток, «Фон», «не трогать» (T-0062, T-0082)
                job["layers"] = {k: st.model_dump() for k, st in layers.items()}
                job["mask"] = mask
            if dst.resolve() == src.resolve() and not force:
                job["error"] = "перезапись исходника — нужен force"
            jobs.append(job)
    return jobs


def export_job(job: dict) -> dict:
    t0 = time.perf_counter()
    if "error" in job:
        return {"id": job["id"], "src": job["src"], "error": job["error"]}
    try:
        img = imageio.load_image(job["src"])
        stack = AdjustmentStack.model_validate(job["stack"])
        layers = {int(k): AdjustmentStack.model_validate(v) for k, v in job.get("layers", {}).items()}
        # маска прокси-разрешения мягко растягивается на полный кадр
        out = render_masked(img.rgb, stack, layers, job.get("mask"), lut_size=lutlib.EXPORT_SIZE)
        depth = img.bit_depth if job["bit_depth"] == "keep" else int(job["bit_depth"])
        if Path(job["dst"]).suffix.lower() in (".jpg", ".jpeg", ".webp", ".bmp"):
            depth = 8
        imageio.save_image(job["dst"], out, quality=job["quality"], bit_depth=depth,
                           alpha=img.alpha, exif=img.exif, icc=img.icc)
        return {"id": job["id"], "src": job["src"], "dst": job["dst"], "seconds": round(time.perf_counter() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"id": job["id"], "src": job["src"], "error": f"{type(exc).__name__}: {exc}"}


def export(ws: Workspace, photo_ids: set[str] | None = None, scenes: list[str] | None = None,
           settings: ExportSettings | None = None, force: bool = False, workers: int = 0) -> list[dict]:
    return run_parallel(export_job, plan_export(ws, photo_ids, scenes, settings, force), workers)
