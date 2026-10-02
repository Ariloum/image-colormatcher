"""Пакетный авто-баланс: воркер одного фото, параллельный запуск, матчинг воркспейса.

Воркер получает словарь (сериализуемый — годится для процессов) и возвращает словарь
с результатом; этим пользуются CLI (`match`, `ws match`) и GUI.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from ..core import imageio, lut as lutlib
from ..core.analysis import apply_offset, combine, profile, sample_image
from ..core.matcher import make_anchor, match
from ..core.pipeline import bake_stack, render, render_masked
from ..core.region_match import MIN_SHARE, match_region, region_sample, region_stats
from ..models import masks as M
from ..models import AdjustmentStack, Preset
from ..models.profile import AnchorPair, ColorProfile, MatchSettings
from ..models.workspace import PhotoItem, Workspace
from .naming import Naming, output_path

ANALYSIS_SIDE = 1024  # до какого размера уменьшать кадр перед анализом (выборка всё равно ~12k px)


def load_small(path: str | Path) -> np.ndarray:
    return imageio.make_proxy(imageio.load_image(path).rgb, ANALYSIS_SIDE)


Progress = Callable[[int, int], None]  # (сделано, всего)


def run_parallel(fn: Callable[[dict], dict], jobs: list[dict], workers: int = 0,
                 progress: Progress | None = None) -> list[dict]:
    """Выполнить задания (параллельно в процессах); порядок результатов = порядок заданий.
    `progress(сделано, всего)` вызывается после каждого завершённого задания."""
    workers = workers or min(8, os.cpu_count() or 1)
    total = len(jobs)
    if progress:
        progress(0, total)
    if workers <= 1 or total <= 1:
        out = []
        for j in jobs:
            out.append(fn(j))
            if progress:
                progress(len(out), total)
        return out
    results: list[dict | None] = [None] * total
    with ProcessPoolExecutor(max_workers=min(workers, total)) as pool:
        futures = {pool.submit(fn, j): i for i, j in enumerate(jobs)}
        for done, fut in enumerate(as_completed(futures), 1):
            results[futures[fut]] = fut.result()
            if progress:
                progress(done, total)
    return results  # type: ignore[return-value]


def match_job(job: dict) -> dict:
    """Подбор для одного фото (+ по желанию пресет и рендер). Ключи job:
    file, ref, settings, base, anchors, presets_dir, render_dir, naming, suffix, quality."""
    t0 = time.perf_counter()
    src = Path(job["file"])
    try:
        settings = MatchSettings.model_validate(job["settings"])
        ref = ColorProfile.model_validate(job["ref"])
        base = AdjustmentStack.model_validate(job["base"]) if job.get("base") else None
        full = imageio.load_image(src)
        small = imageio.make_proxy(full.rgb, ANALYSIS_SIDE)
        anchors = []
        for pair in job.get("anchors", []):
            pair = AnchorPair.model_validate(pair)
            anchors.append(make_anchor(small, pair.target.rect, load_small(pair.ref.image), pair.ref.rect,
                                       weight=pair.weight, label=pair.label))
        mask = job.get("mask")
        if mask is not None:
            mask = M.resized(mask, *small.shape[:2])
        res = match(small, ref, settings, base_stack=base, anchors=anchors,
                    sample=sample_image(small, settings.sample_pixels, exclude=_excluded(mask)))
        out: dict = {
            "file": str(src), "params": res.params, "stack": res.stack.model_dump(),
            "before": res.metrics_before, "after": res.metrics_after,
            "anchors_before": res.anchors_before, "anchors_after": res.anchors_after,
            "iterations": res.iterations,
        }
        if mask is not None and job.get("ref_file"):
            out["layers"], out["regions"] = _match_layers(small, res.stack, mask, job, settings)
        if job.get("id"):
            out["id"] = job["id"]
        if job.get("presets_dir"):
            path = Path(job["presets_dir"]) / f"{src.stem}.cmpreset.json"
            Preset.from_stack(res.stack, name=f"match: {src.name}", source=str(src)).save(path)
            out["preset"] = str(path)
        if job.get("render_dir"):
            dst = output_path(src, Path(job["render_dir"]), Naming(job["naming"]), suffix=job["suffix"])
            lut = bake_stack(res.stack, lutlib.EXPORT_SIZE)
            img = render(full.rgb, res.stack, lut=lut) if lut is not None else full.rgb
            imageio.save_image(dst, img, quality=job["quality"], bit_depth=full.bit_depth,
                               alpha=full.alpha, exif=full.exif, icc=full.icc)
            out["output"] = str(dst)
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out
    except Exception as exc:  # noqa: BLE001 — отчёт по каждому файлу
        return {"file": str(src), "id": job.get("id"), "error": f"{type(exc).__name__}: {exc}"}


def _excluded(mask: np.ndarray | None) -> np.ndarray | None:
    """Карта «вне статистики»: «не учитывать» + «не трогать» (0…1)."""
    if mask is None or not (mask[..., M.IGNORE].any() or mask[..., M.KEEP].any()):
        return None
    return np.clip((mask[..., M.IGNORE].astype(np.float32) + mask[..., M.KEEP]) / 255.0, 0, 1)


def _match_layers(small: np.ndarray, stack: AdjustmentStack, mask: np.ndarray, job: dict,
                  settings: MatchSettings) -> tuple[dict, dict]:
    """Слои меток кадра: цвет каждой области — к той же области референса (T-0063).

    Область референса — его маска той же метки; если на референсе метки нет — та же геометрия,
    что на кадре. Референс берётся как он выглядит (со своими правками и слоями)."""
    lut = bake_stack(stack, lutlib.PREVIEW_SIZE)
    g = render(small, stack, lut=lut) if lut is not None else small
    ref_small = load_small(job["ref_file"])
    ref_stack = AdjustmentStack.model_validate(job["ref_stack"])
    ref_layers = {int(k): AdjustmentStack.model_validate(v) for k, v in job.get("ref_layers", {}).items()}
    ref_mask = job.get("ref_mask")
    ref_img = render_masked(ref_small, ref_stack, ref_layers, ref_mask)
    rh, rw = ref_img.shape[:2]
    ref_mask_s = M.resized(ref_mask, rh, rw) if ref_mask is not None else None
    layers, report = {}, {}
    total = mask.shape[0] * mask.shape[1]
    for slot in range(M.COLOR_SLOTS):
        wt = mask[..., slot].astype(np.float32) / 255.0
        if wt.sum() / total < MIN_SHARE:
            continue
        rw_slot = ref_mask_s[..., slot].astype(np.float32) / 255.0 if ref_mask_s is not None else None
        same_geometry = rw_slot is None or rw_slot.sum() / (rh * rw) < MIN_SHARE
        if same_geometry:   # метки нет на референсе — та же область, что на кадре
            rw_slot = M.resized(mask, rh, rw)[..., slot].astype(np.float32) / 255.0
        rpx, rwt = region_sample(ref_img, rw_slot)
        tpx, twt = region_sample(g, wt)
        if len(rpx) < 50 or len(tpx) < 50:
            continue
        base = AdjustmentStack.model_validate(job["layers_base"][str(slot)]) if str(slot) in job.get("layers_base", {}) else None
        r = match_region(tpx, twt, region_stats(rpx, rwt), settings, base)
        layers[slot] = r.stack.model_dump()
        report[slot] = {"params": r.params, "before": r.before, "after": r.after, "same_geometry": same_geometry}
    return layers, report


def ref_profile_from_files(refs: list[Path], weights: Iterable[float] | None = None, mode: str = "mean",
                           stack: AdjustmentStack | None = None) -> ColorProfile:
    return combine([profile(load_small(r), stack) for r in refs], weights, mode)


# --- воркспейс (T-0050: один активный референс, у фото — свой запомненный) ---------------------

def ref_profile(ws: Workspace, ref_id: str, use_masks: bool | None = None) -> ColorProfile:
    """Профиль-цель референса: с его собственными правками и смещением цели."""
    _, ph = ws.locate(ref_id)
    item = ws.ref_item(ref_id)
    small = load_small(ws.abs_path(ph))
    use_masks = ws.settings.use_masks if use_masks is None else use_masks
    mask = ws.mask(ref_id) if use_masks else None
    if mask is None:
        prof = profile(small, ph.stack)
    else:   # маски референса: его слои меток видны в цели, «не учитывать»/«не трогать» — вне статистики
        mask = M.resized(mask, *small.shape[:2])
        prof = profile(render_masked(small, ph.stack, ph.active_layers(), mask), None, exclude=_excluded(mask))
    return apply_offset(prof, item.offset) if item else prof


def _anchor_jobs(ws: Workspace, ph: PhotoItem, ref_id: str | None) -> list[dict]:
    """Якоря фото, относящиеся к используемому референсу (пути — абсолютные для воркера)."""
    out = []
    for pair in ph.anchors:
        try:
            ref_ph = ws.find(pair.ref.image)
        except KeyError:
            continue
        if ref_id is not None and ref_ph.id != ref_id:
            continue  # якорь к другой локации
        d = pair.model_dump()
        d["ref"]["image"] = str(ws.abs_path(ref_ph))
        out.append(d)
    return out


def match_workspace(ws: Workspace, scenes: list[str] | None = None, photo_ids: set[str] | None = None,
                    settings: MatchSettings | None = None, to_group_mean: bool = False,
                    workers: int = 0, ref_id: str | None = None,
                    progress: Progress | None = None) -> list[dict]:
    """Авто-баланс фото воркспейса (референсы не трогаются).

    Референс каждого фото: `ref_id` (если задан — например, активный для выделенных),
    иначе запомненный у фото, иначе активный. После подбора фото запоминает референс.
    Без референсов: `to_group_mean` — к медиане своей сцены, иначе ошибка по фото.
    Решаемые ползунки подбираются, остальные правки фото сохраняются.
    """
    settings = settings or ws.settings
    refs = ws.ref_ids()
    if ref_id is not None and ref_id not in refs:
        raise KeyError(f"{ref_id!r} — не референс")
    profiles: dict[str, ColorProfile] = {}
    jobs, skipped = [], []
    for sc in ws.scenes:
        if scenes and sc.name not in scenes and sc.id not in scenes:
            continue
        group_mean: ColorProfile | None = None
        for ph in sc.photos:
            if ph.id in refs or (photo_ids and ph.id not in photo_ids):
                continue
            rid = ref_id or ws.ref_for(ph)
            if rid is not None:
                if rid not in profiles:
                    profiles[rid] = ref_profile(ws, rid, settings.use_masks)
                target = profiles[rid]
            elif to_group_mean:
                if group_mean is None:
                    group_mean = combine([profile(load_small(ws.abs_path(p))) for p in sc.photos], mode="median")
                target = group_mean
            else:
                skipped.append({"id": ph.id, "file": str(ws.abs_path(ph)), "error": "нет референса — выберите активный"})
                continue
            job = {"id": ph.id, "file": str(ws.abs_path(ph)), "ref": target.model_dump(), "ref_id": rid,
                   "settings": settings.model_dump(), "base": ph.stack.model_dump(),
                   "anchors": _anchor_jobs(ws, ph, rid)}
            mask = ws.mask(ph.id) if settings.use_masks else None
            if mask is not None:   # маски кадра (T-0063): исключения + слои меток к тем же меткам референса
                job["mask"] = mask
                job["layers_base"] = {str(k): st.model_dump() for k, st in ph.layers.items()}
                if rid is not None:
                    _, rph = ws.locate(rid)
                    job.update(ref_file=str(ws.abs_path(rph)), ref_stack=rph.stack.model_dump(),
                               ref_layers={str(k): st.model_dump() for k, st in rph.active_layers().items()},
                               ref_mask=ws.mask(rid))
            jobs.append(job)
    results = run_parallel(match_job, jobs, workers, progress)
    for job, r in zip(jobs, results):
        r["ref_id"] = job["ref_id"]
        if "stack" in r:
            ph = ws.locate(r["id"])[1]
            ph.stack = AdjustmentStack.model_validate(r["stack"])
            ph.ref_id = job["ref_id"]
            for k, st in r.get("layers", {}).items():
                ph.layers[int(k)] = AdjustmentStack.model_validate(st)
    return results + skipped
