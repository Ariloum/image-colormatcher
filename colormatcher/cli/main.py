"""Команды CLI `colormatch`: filters, apply, preset (этап 1); analyze, compare, match (этап 2 — match_cmds)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core import imageio, lut as lutlib
from ..core.filters import FilterRegistry
from ..core.pipeline import bake_stack, render
from ..models import AdjustmentStack, Preset
from ..services.naming import Naming, output_path
from . import match_cmds, ws_cmds
from .common import parse_sets as _parse_sets

app = typer.Typer(help="Image ColorMatcher — авто-баланс цветов серии фото по референсам.", no_args_is_help=True)
filters_app = typer.Typer(help="Реестр фильтров.", no_args_is_help=True)
preset_app = typer.Typer(help="Пресеты (*.cmpreset.json).", no_args_is_help=True)
app.add_typer(filters_app, name="filters")
app.add_typer(preset_app, name="preset")
app.command("analyze")(match_cmds.analyze)
app.command("compare")(match_cmds.compare)
app.command("match")(match_cmds.match_cmd)
app.add_typer(ws_cmds.ws_app, name="ws")

console = Console(stderr=True)


def _version(value: bool) -> None:
    if value:
        from .. import __version__

        typer.echo(f"Image ColorMatcher {__version__}")
        raise typer.Exit()


@app.callback()
def _root(version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True,
                                       help="Версия программы.")) -> None:
    """Image ColorMatcher — авто-баланс цветов серии фото по референсам."""

def _build_stack(preset: Optional[Path], sets: list[str], opacity: Optional[float]) -> AdjustmentStack:
    stack = Preset.load(preset).apply_to(AdjustmentStack()) if preset else AdjustmentStack()
    stack.update(_parse_sets(sets))
    if opacity is not None:
        stack.opacity = opacity
    return stack


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# --- filters -----------------------------------------------------------------

@filters_app.command("list")
def filters_list(as_json: bool = typer.Option(False, "--json", help="Машиночитаемый вывод.")) -> None:
    """Фильтры и их параметры с диапазонами."""
    data = [
        {
            "type": f.type,
            "group": f.group,
            "label": f.label,
            "params": [
                {"key": f"{f.group}.{p.key}", "label": p.label, "kind": p.kind,
                 "min": p.min, "max": p.max, "default": p.clamp(p.default)}
                for p in f.params
            ],
        }
        for f in FilterRegistry.all()
    ]
    if as_json:
        _emit(data, True)
        return
    table = Table(title="Фильтры")
    for col in ("ключ", "подпись", "диапазон", "по умолч."):
        table.add_column(col)
    for f in data:
        table.add_section()
        for p in f["params"]:
            if p["kind"] == "bool":
                rng, default = "bool", str(p["default"])
            else:
                rng, default = f"{p['min']:g} … {p['max']:g}", f"{p['default']:g}"
            table.add_row(p["key"], p["label"], rng, default)
    table.add_section()
    table.add_row("opacity", "Сила коррекции", "0 … 100", "100")
    Console().print(table)


# --- apply -------------------------------------------------------------------

@app.command()
def apply(
    images: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Файлы изображений."),
    preset: Optional[Path] = typer.Option(None, "--preset", "-p", exists=True, help="Пресет *.cmpreset.json."),
    sets: list[str] = typer.Option([], "--set", "-s", help="Параметр: cb.shadows.cr=10 (можно много раз)."),
    opacity: Optional[float] = typer.Option(None, "--opacity", min=0, max=100, help="Сила коррекции, %."),
    out: Path = typer.Option(..., "--out", "-o", file_okay=False, help="Папка вывода."),
    naming: Naming = typer.Option(Naming.version, "--naming", help="overwrite | suffix | version."),
    suffix: str = typer.Option("_cm", "--suffix", help="Суффикс для --naming suffix."),
    quality: int = typer.Option(95, "--quality", min=1, max=100, help="Качество JPEG/WebP."),
    lut_size: int = typer.Option(lutlib.EXPORT_SIZE, "--lut-size", min=2, max=129, help="Размер LUT при рендере."),
    force: bool = typer.Option(False, "--force", help="Разрешить перезапись исходных файлов."),
    as_json: bool = typer.Option(False, "--json", help="Машиночитаемый отчёт."),
) -> None:
    """Применить параметры (пресет и/или --set) к фото и сохранить результат."""
    stack = _build_stack(preset, sets, opacity)
    lut = None if stack.is_identity() else bake_stack(stack, lut_size)
    report, failed = [], 0
    for src in images:
        dst = output_path(src, out, naming, suffix=suffix)
        if dst.resolve() == src.resolve() and not force:
            console.print(f"[red]пропуск[/] {src}: перезапись исходника требует --force")
            failed += 1
            continue
        t0 = time.perf_counter()
        try:
            img = imageio.load_image(src)
            res = render(img.rgb, stack, lut=lut) if lut is not None else img.rgb
            imageio.save_image(dst, res, quality=quality, bit_depth=img.bit_depth,
                               alpha=img.alpha, exif=img.exif, icc=img.icc)
        except Exception as exc:  # noqa: BLE001 — отчитываемся по каждому файлу
            console.print(f"[red]ошибка[/] {src}: {exc}")
            report.append({"src": str(src), "error": str(exc)})
            failed += 1
            continue
        dt = time.perf_counter() - t0
        report.append({"src": str(src), "dst": str(dst), "seconds": round(dt, 3)})
        if not as_json:
            console.print(f"[green]✓[/] {src.name} → {dst}  ({dt:.2f} с)")
    _emit({"stack": stack.flat(), "files": report}, as_json)
    if failed:
        raise typer.Exit(2 if failed < len(images) else 1)


# --- preset ------------------------------------------------------------------

@preset_app.command("save")
def preset_save(
    out: Path = typer.Argument(..., help="Куда сохранить пресет."),
    sets: list[str] = typer.Option([], "--set", "-s", help="Параметр: hs.saturation=-10."),
    opacity: Optional[float] = typer.Option(None, "--opacity", min=0, max=100),
    name: str = typer.Option("", "--name", help="Имя пресета."),
    groups: Optional[str] = typer.Option(None, "--groups", help="Группы через запятую (bc,cb,hs,opacity)."),
) -> None:
    """Собрать пресет из параметров."""
    stack = _build_stack(None, sets, opacity)
    grp = [g.strip() for g in groups.split(",")] if groups else None
    path = Preset.from_stack(stack, name=name or out.stem, groups=grp).save(out)
    console.print(f"[green]✓[/] {path}")


@preset_app.command("show")
def preset_show(
    path: Path = typer.Argument(..., exists=True, dir_okay=False),
    as_json: bool = typer.Option(False, "--json"),
    changed_only: bool = typer.Option(True, "--changed-only/--all", help="Только отличные от умолчаний."),
) -> None:
    """Показать параметры пресета."""
    preset = Preset.load(path)
    flat = preset.stack.flat()
    if changed_only:
        default = AdjustmentStack().flat()
        flat = {k: v for k, v in flat.items() if default.get(k) != v}
    if as_json:
        _emit({"name": preset.name, "groups": preset.groups, "params": flat}, True)
        return
    table = Table(title=f"Пресет «{preset.name}» — группы: {', '.join(preset.groups)}")
    table.add_column("ключ")
    table.add_column("значение")
    for k, v in flat.items():
        table.add_row(k, f"{v:g}" if isinstance(v, float) else str(v))
    Console().print(table)
