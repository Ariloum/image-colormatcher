"""Команды анализа и авто-баланса: analyze, compare, match (этап 2). Логика — services.matching."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core import imageio
from ..core.analysis import apply_offset, combine, profile
from ..core.matcher import MODE_PARAMS, metrics
from ..models import AdjustmentStack, Preset
from ..models.profile import BANDS, ColorProfile, MatchSettings, ProfileOffset
from ..services.matching import load_small, match_job, run_parallel
from ..services.naming import Naming
from .common import parse_limits

console = Console(stderr=True)


def _emit(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _collect(images: list[Path], directory: Optional[Path], recursive: bool) -> list[Path]:
    files = list(images)
    if directory:
        it = directory.rglob("*") if recursive else directory.glob("*")
        files += sorted(p for p in it if p.is_file() and imageio.is_image(p))
    seen, out = set(), []
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _stack_from(preset: Optional[Path]) -> AdjustmentStack | None:
    return Preset.load(preset).apply_to(AdjustmentStack()) if preset else None


def _profile_row(name: str, p: ColorProfile) -> dict:
    return {"file": name, **p.model_dump()}


# --- analyze -------------------------------------------------------------------

def analyze(
    images: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Фото для анализа."),
    preset: Optional[Path] = typer.Option(None, "--preset", "-p", exists=True, help="Снять профиль ПОСЛЕ этого пресета."),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый вывод."),
) -> None:
    """«Пипетка»: цветовой профиль каждого фото (каст по тонам, тон, насыщенность)."""
    stack = _stack_from(preset)
    rows = [_profile_row(str(f), profile(load_small(f), stack)) for f in images]
    if as_json:
        _emit(rows)
        return
    table = Table(title="Цветовые профили (Lab)")
    for col in ("файл", "тени a/b", "середины a/b", "света a/b", "L p05/50/95", "насыщ. p50/p90", "нейтр.", "доверие"):
        table.add_column(col)
    for r in rows:
        b = r["bands"]
        ab = [f"{b[n]['a']:+.1f}/{b[n]['b']:+.1f}" if b[n]["mass"] > 0.01 else "—" for n in BANDS]
        table.add_row(Path(r["file"]).name[:34], *ab, f"{r['L_p05']:.0f}/{r['L_p50']:.0f}/{r['L_p95']:.0f}",
                      f"{100 * r['S_p50']:.0f}/{100 * r['S_p90']:.0f}", f"{100 * r['neutral_share']:.0f}%",
                      f"{r['confidence']:.2f}")
    Console().print(table)


# --- общий набор референсов ---------------------------------------------------------

def _ref_profile(refs: list[Path], weights: Optional[str], mode: str, preset: Optional[Path],
                 off: ProfileOffset) -> ColorProfile:
    ws = [float(w) for w in weights.split(",")] if weights else [1.0] * len(refs)
    if len(ws) != len(refs):
        raise typer.BadParameter(f"--ref-weight: {len(ws)} весов на {len(refs)} референсов")
    stack = _stack_from(preset)
    return apply_offset(combine([profile(load_small(r), stack) for r in refs], ws, mode), off)


def compare(
    refs: list[Path] = typer.Option(..., "--ref", "-r", exists=True, dir_okay=False, help="Референс (можно много раз)."),
    images: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Фото для сравнения."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Насколько фото расходятся с референсами: оттенок нейтралей, тон, насыщенность."""
    ref = _ref_profile(refs, None, "mean", None, ProfileOffset())
    rows = [{"file": str(f), **metrics(profile(load_small(f)), ref)} for f in images]
    if as_json:
        _emit(rows)
        return
    table = Table(title="Расхождение с референсом")
    for col in ("файл", "оттенок ΔE", "ΔL медиана", "Δ размах", "Δ насыщ."):
        table.add_column(col)
    for r in rows:
        table.add_row(Path(r["file"]).name[:40], f"{r['cast_dE']:.2f}", f"{r['dL50']:+.1f}", f"{r['dRange']:+.1f}", f"{r['dS50']:+.1f}")
    Console().print(table)


# --- match ---------------------------------------------------------------------------

def match_cmd(
    refs: list[Path] = typer.Option([], "--ref", "-r", exists=True, dir_okay=False, help="Референс (можно много раз)."),
    ref_weight: Optional[str] = typer.Option(None, "--ref-weight", help="Веса референсов через запятую."),
    ref_mode: str = typer.Option("mean", "--ref-mode", help="Усреднение референсов: mean | median."),
    ref_preset: Optional[Path] = typer.Option(None, "--ref-preset", exists=True, help="Пресет, применяемый к референсам до анализа."),
    to_group_mean: bool = typer.Option(False, "--to-group-mean", help="Без референса: свести к медиане группы."),
    images: list[Path] = typer.Argument(None, help="Целевые фото."),
    directory: Optional[Path] = typer.Option(None, "--dir", "-d", exists=True, file_okay=False, help="Папка с целевыми фото."),
    recursive: bool = typer.Option(False, "--recursive", "-R", help="С подпапками."),
    mode: str = typer.Option("balance+sat", "--mode", "-m", help="balance | balance+sat | tone+balance+sat | full."),
    touch: Optional[str] = typer.Option(None, "--touch", help="Явный список ползунков через запятую (перекрывает --mode)."),
    limits: list[str] = typer.Option([], "--limit", help="Предел ползунка: bc.brightness=20, cb=40 (группа)."),
    sparsity: float = typer.Option(0.08, "--sparsity", min=0, help="Чем больше — тем меньше задействованных ползунков."),
    regularize: float = typer.Option(0.15, "--regularize", min=0, help="Штраф за величину ползунков."),
    strength: float = typer.Option(100, "--strength", min=0, max=100, help="Сила результата (opacity), %."),
    base_preset: Optional[Path] = typer.Option(None, "--base-preset", exists=True, help="Стартовые правки целевых фото."),
    offset_l: float = typer.Option(0.0, "--offset-L", help="Цель светлее (+) / темнее (−), единицы L*."),
    offset_a: float = typer.Option(0.0, "--offset-a", help="Цель пурпурнее (+) / зеленее (−)."),
    offset_b: float = typer.Option(0.0, "--offset-b", help="Цель теплее (+) / холоднее (−)."),
    offset_chroma: float = typer.Option(1.0, "--offset-chroma", min=0, help="Множитель насыщенности цели."),
    anchors_file: Optional[Path] = typer.Option(None, "--anchors", exists=True, help="JSON со списком пар пипеток (AnchorPair)."),
    presets_dir: Optional[Path] = typer.Option(None, "--out-presets", file_okay=False, help="Сохранить пресет для каждого фото."),
    render_out: Optional[Path] = typer.Option(None, "--out", "-o", file_okay=False, help="Сразу отрендерить в папку."),
    naming: Naming = typer.Option(Naming.version, "--naming", help="overwrite | suffix | version."),
    suffix: str = typer.Option("_cm", "--suffix"),
    quality: int = typer.Option(95, "--quality", min=1, max=100),
    jobs: int = typer.Option(0, "--jobs", "-j", min=0, help="Процессов (0 — по числу ядер, до 8)."),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый отчёт."),
) -> None:
    """Авто-баланс: подобрать ползунки для каждого фото под профиль референсов."""
    targets = _collect(images or [], directory, recursive)
    ref_paths = {r.resolve() for r in refs}
    targets = [t for t in targets if t.resolve() not in ref_paths]
    if not targets:
        raise typer.BadParameter("нет целевых фото (аргументы или --dir)")
    if mode not in MODE_PARAMS:
        raise typer.BadParameter(f"--mode: одно из {', '.join(MODE_PARAMS)}")
    if not refs and not to_group_mean:
        raise typer.BadParameter("нужен --ref или --to-group-mean")

    settings = MatchSettings(mode=mode, sparsity=sparsity, regularize=regularize, strength=strength,
                             touch=[k.strip() for k in touch.split(",")] if touch else None)
    settings.limits.update(parse_limits(limits))
    off = ProfileOffset(L=offset_l, a=offset_a, b=offset_b, chroma=offset_chroma)
    if to_group_mean:
        ref = apply_offset(combine([profile(load_small(t)) for t in targets], mode="median"), off)
    else:
        ref = _ref_profile(refs, ref_weight, ref_mode, ref_preset, off)

    pairs = json.loads(anchors_file.read_text(encoding="utf-8")) if anchors_file else []
    base = _stack_from(base_preset)
    common = {
        "settings": settings.model_dump(), "ref": ref.model_dump(),
        "base": base.model_dump() if base else None,
        "presets_dir": str(presets_dir) if presets_dir else None,
        "render_dir": str(render_out) if render_out else None,
        "naming": naming.value, "suffix": suffix, "quality": quality,
    }
    job_list = []
    for t in targets:
        mine = [p for p in pairs if Path(p["target"]["image"]).name == t.name]
        job_list.append({**common, "file": str(t), "anchors": mine})

    workers = jobs or min(8, os.cpu_count() or 1)
    if not as_json:
        console.print(f"референсов: {len(refs) or 'медиана группы'}, фото: {len(targets)}, режим: {mode}, процессов: {min(workers, len(targets))}")
    t0 = time.perf_counter()
    results = run_parallel(match_job, job_list, workers)
    failed = sum(1 for r in results if "error" in r)

    if as_json:
        _emit({"reference": ref.model_dump(), "files": results, "seconds": round(time.perf_counter() - t0, 2)})
    else:
        table = Table(title=f"Авто-баланс ({time.perf_counter() - t0:.1f} с)")
        for col in ("файл", "оттенок ΔE", "Δ насыщ.", "ползунки", "с"):
            table.add_column(col)
        for r in results:
            if "error" in r:
                table.add_row(Path(r["file"]).name[:34], "[red]ошибка[/]", "", r["error"][:60], "")
                continue
            sliders = ", ".join(f"{k}={v:+g}" for k, v in r["params"].items() if v)
            table.add_row(Path(r["file"]).name[:34],
                          f"{r['before']['cast_dE']:.1f} → {r['after']['cast_dE']:.1f}",
                          f"{r['before']['dS50']:+.1f} → {r['after']['dS50']:+.1f}",
                          sliders or "—", f"{r['seconds']:.1f}")
        Console().print(table)
    if failed:
        raise typer.Exit(2 if failed < len(results) else 1)
