"""Команды воркспейса `colormatch ws …` (этап 3: T-0018…T-0021, T-0011, T-0013)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..core.imageio import is_image
from ..models import AdjustmentStack, Preset
from ..models.workspace import Workspace
from ..services import export as export_svc
from ..services import importer, matching, workspace_io
from ..services.naming import Naming
from .common import parse_limits, parse_sets

ws_app = typer.Typer(help="Воркспейсы: сцены, референсы, правки, авто-баланс, экспорт, упаковка.", no_args_is_help=True)
console = Console(stderr=True)
WS = typer.Argument(..., help="Файл воркспейса (*.cmws).")


def _emit(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _open(path: Path) -> Workspace:
    try:
        return workspace_io.load(path)
    except FileNotFoundError:
        raise typer.BadParameter(f"нет воркспейса {workspace_io.ws_path(path)} (создайте: colormatch ws new)") from None


def _photos(ws: Workspace, keys: list[str]) -> list:
    try:
        return [ws.find(k) for k in keys]
    except KeyError as exc:
        raise typer.BadParameter(str(exc).strip("'\"")) from None


def _select_ids(ws: Workspace, photos: list[str], scenes: list[str], all_: bool) -> set[str]:
    ids = {p.id for p in _photos(ws, photos)}
    for name in scenes:
        try:
            ids |= {p.id for p in ws.scene(name).photos}
        except KeyError as exc:
            raise typer.BadParameter(str(exc).strip("'\"")) from None
    if all_:
        ids |= {p.id for p in ws.all_photos()}
    if not ids:
        raise typer.BadParameter("не выбрано ни одного фото (--photo / --scene / --all)")
    return ids


# --- создание и состав -----------------------------------------------------------------

@ws_app.command("new")
def ws_new(path: Path = WS, name: Optional[str] = typer.Option(None, "--name"),
           force: bool = typer.Option(False, "--force", help="Перезаписать существующий.")) -> None:
    """Создать пустой воркспейс."""
    target = workspace_io.ws_path(path)
    if target.exists() and not force:
        raise typer.BadParameter(f"{target} уже есть (--force — перезаписать)")
    console.print(f"[green]✓[/] {workspace_io.save(workspace_io.new_workspace(target, name), target)}")


@ws_app.command("add")
def ws_add(
    path: Path = WS,
    images: list[Path] = typer.Argument(None, help="Файлы фото."),
    directory: list[Path] = typer.Option([], "--dir", "-d", exists=True, file_okay=False, help="Папка (подпапки = сцены)."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", "-R"),
    no_scenes: bool = typer.Option(False, "--no-scenes", help="Не делить папку на сцены по подпапкам."),
    scene: Optional[str] = typer.Option(None, "--scene", "-s", help="Сцена для файлов (или для всей папки)."),
) -> None:
    """Добавить фото: файлы и/или папки. Повторно добавленные файлы пропускаются."""
    ws = _open(path)
    added = []
    for d in directory:
        added += importer.add_folder(ws, d, recursive=recursive, scenes=not no_scenes, scene=scene)
    files = [f for f in (images or []) if f.is_file() and is_image(f)]
    bad = [f for f in (images or []) if f not in files]
    if bad:
        console.print(f"[yellow]пропущены (не файлы изображений): {', '.join(map(str, bad))}[/]")
    if files:
        added += importer.add_files(ws, files, scene or "photos")
    workspace_io.save(ws, workspace_io.ws_path(path))
    console.print(f"[green]✓[/] добавлено {len(added)} фото; всего {sum(1 for _ in ws.all_photos())} в {len(ws.scenes)} сценах")


@ws_app.command("remove")
def ws_remove(
    path: Path = WS,
    photos: list[str] = typer.Option([], "--photo", "-p", help="id, путь или имя файла."),
    scenes: list[str] = typer.Option([], "--scene", "-s", help="Удалить сцену целиком."),
) -> None:
    """Убрать фото или сцены из воркспейса (файлы на диске не трогаются)."""
    ws = _open(path)
    n = ws.remove_photos({p.id for p in _photos(ws, photos)})
    for name in scenes:
        n += ws.remove_scene(name)
    workspace_io.save(ws, workspace_io.ws_path(path))
    console.print(f"[green]✓[/] убрано {n} фото")


@ws_app.command("show")
def ws_show(path: Path = WS, as_json: bool = typer.Option(False, "--json")) -> None:
    """Состав воркспейса: сцены, фото, референсы, правки."""
    ws = _open(path)
    if as_json:
        _emit(json.loads(ws.model_dump_json()) | {"root": str(ws.root), "missing": [p.id for p in ws.missing()]})
        return
    refs = ws.ref_ids()
    missing = {p.id for p in ws.missing()}
    default = AdjustmentStack().flat()
    table = Table(title=f"«{ws.name}» — {ws.root}")
    for col in ("сцена", "id", "файл", "реф", "правки"):
        table.add_column(col)
    for sc in ws.scenes:
        for i, ph in enumerate(sc.photos):
            changed = {k: v for k, v in ph.stack.flat().items() if default.get(k) != v}
            mark = ("●" if ph.id == ws.active_ref else "★") if ph.id in refs else (f"→{ph.ref_id}" if ph.ref_id else "")
            name = f"[red]{ph.name} (нет файла)[/]" if ph.id in missing else ph.name
            table.add_row(sc.name if i == 0 else "", ph.id, name, mark,
                          ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in changed.items()) or "—")
    Console().print(table)
    names = {ph.id: ph.name for ph in ws.all_photos()}
    lines = [f"{'●' if r.photo_id == ws.active_ref else '○'} {r.photo_id} {names.get(r.photo_id, '?')}" for r in ws.refs]
    console.print("референсы (● активный):\n  " + ("\n  ".join(lines) if lines else "—"))


# --- референсы -------------------------------------------------------------------------

@ws_app.command("ref")
def ws_ref(
    path: Path = WS,
    add: list[str] = typer.Option([], "--add", help="Добавить референс (id/путь/имя; файл не из воркспейса будет добавлен в сцену «refs»)."),
    remove: list[str] = typer.Option([], "--remove", help="Убрать из референсов."),
    clear: bool = typer.Option(False, "--clear", help="Убрать все референсы."),
    active: Optional[str] = typer.Option(None, "--active", help="Сделать активным (применяется авто-балансом)."),
    offset_l: Optional[float] = typer.Option(None, "--offset-L", help="Цель светлее (+) / темнее (−)."),
    offset_a: Optional[float] = typer.Option(None, "--offset-a", help="Цель пурпурнее (+) / зеленее (−)."),
    offset_b: Optional[float] = typer.Option(None, "--offset-b", help="Цель теплее (+) / холоднее (−)."),
    offset_chroma: Optional[float] = typer.Option(None, "--offset-chroma", help="Множитель насыщенности цели."),
) -> None:
    """Референсы проекта: список, активный (без усреднения, T-0050), смещение цели активного."""
    ws = _open(path)

    def resolve(k: str) -> str:
        try:
            return ws.find(k).id
        except KeyError:
            if Path(k).is_file() and is_image(k):
                return ws.add_photo(k, "refs").id
            raise typer.BadParameter(f"нет фото {k!r}") from None

    if clear:
        ws.refs, ws.active_ref = [], None
    for k in add:
        ws.add_ref(resolve(k), activate=False)
    for k in remove:
        ws.remove_ref(resolve(k))
    if active:
        pid = resolve(active)
        ws.add_ref(pid, activate=True)
    offsets = (("L", offset_l), ("a", offset_a), ("b", offset_b), ("chroma", offset_chroma))
    if any(v is not None for _, v in offsets):
        item = ws.ref_item(ws.active_ref) if ws.active_ref else None
        if item is None:
            raise typer.BadParameter("смещение задаётся активному референсу — сначала --active")
        for field, val in offsets:
            if val is not None:
                setattr(item.offset, field, val)
    workspace_io.save(ws, workspace_io.ws_path(path))
    items = ", ".join(("●" if r.photo_id == ws.active_ref else "") + r.photo_id for r in ws.refs) or "—"
    console.print(f"[green]✓[/] референсы: {items} (● активный)")


# --- ручные правки и пресеты ---------------------------------------------------------------

@ws_app.command("set")
def ws_set(
    path: Path = WS,
    photos: list[str] = typer.Option([], "--photo", "-p", help="Фото (id/путь/имя), можно много."),
    scenes: list[str] = typer.Option([], "--scene", "-s", help="Все фото сцены."),
    all_: bool = typer.Option(False, "--all", help="Все фото воркспейса."),
    sets: list[str] = typer.Option([], "--set", help="Параметр: cb.midtones.cr=10, opacity=80."),
    relative: bool = typer.Option(False, "--relative", help="Прибавить к текущим значениям (групповая правка)."),
    preset: Optional[Path] = typer.Option(None, "--preset", exists=True, help="Вставить пресет."),
    groups: Optional[str] = typer.Option(None, "--groups", help="Группы пресета: bc,cb,hs,opacity."),
    reset: bool = typer.Option(False, "--reset", help="Сбросить все правки."),
) -> None:
    """Ручная правка одного фото или группы: значения, относительные сдвиги, вставка пресета."""
    ws = _open(path)
    ids = _select_ids(ws, photos, scenes, all_)
    values = parse_sets(sets)
    pre = Preset.load(preset) if preset else None
    grp = [g.strip() for g in groups.split(",")] if groups else None
    for pid in ids:
        _, ph = ws.locate(pid)
        if reset:
            ph.stack = AdjustmentStack()
        if pre:
            ph.stack = pre.apply_to(ph.stack, grp)
        for k, v in values.items():
            if relative and not isinstance(v, bool):
                v = float(ph.stack.get(k)) + v
            ph.stack.set(k, v)
    workspace_io.save(ws, workspace_io.ws_path(path))
    console.print(f"[green]✓[/] изменено фото: {len(ids)}")


@ws_app.command("preset")
def ws_preset(
    path: Path = WS,
    photo: str = typer.Argument(..., help="С какого фото снять пресет (id/путь/имя)."),
    out: Path = typer.Option(..., "--out", "-o", help="Файл пресета."),
    groups: Optional[str] = typer.Option(None, "--groups", help="Какие группы сохранить: bc,cb,hs,opacity."),
    name: str = typer.Option("", "--name"),
) -> None:
    """Сохранить пресет с фото воркспейса."""
    ws = _open(path)
    ph = _photos(ws, [photo])[0]
    grp = [g.strip() for g in groups.split(",")] if groups else None
    saved = Preset.from_stack(ph.stack, name=name or ph.name, groups=grp, source=ph.path).save(out)
    console.print(f"[green]✓[/] {saved}")


# --- авто-баланс --------------------------------------------------------------------------------

@ws_app.command("match")
def ws_match(
    path: Path = WS,
    scenes: list[str] = typer.Option([], "--scene", "-s", help="Только эти сцены."),
    photos: list[str] = typer.Option([], "--photo", "-p", help="Только эти фото."),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="balance | balance+sat | tone+balance+sat | full."),
    touch: Optional[str] = typer.Option(None, "--touch", help="Явный список ползунков через запятую."),
    limits: list[str] = typer.Option([], "--limit", help="Предел ползунка: bc.brightness=20, cb=40."),
    sparsity: Optional[float] = typer.Option(None, "--sparsity", min=0),
    strength: Optional[float] = typer.Option(None, "--strength", min=0, max=100),
    ref: Optional[str] = typer.Option(None, "--ref", "-r", help="Референс для всех выбранных фото (иначе — запомненный у фото или активный)."),
    to_group_mean: bool = typer.Option(False, "--to-group-mean", help="Фото без референса — к медиане своей сцены."),
    remember: bool = typer.Option(False, "--remember", help="Сохранить эти настройки в воркспейсе."),
    jobs: int = typer.Option(0, "--jobs", "-j", min=0),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Авто-баланс: каждое фото — к своему референсу (запомненному или активному; --ref — явно). Правки сохраняются в воркспейс."""
    ws = _open(path)
    settings = ws.settings.model_copy(deep=True)
    if mode:
        settings.mode = mode
    if touch:
        settings.touch = [k.strip() for k in touch.split(",")]
    if sparsity is not None:
        settings.sparsity = sparsity
    if strength is not None:
        settings.strength = strength
    settings.limits.update(parse_limits(limits))
    ids = {p.id for p in _photos(ws, photos)} or None
    t0 = time.perf_counter()
    ref_id = None
    if ref:
        ref_id = _photos(ws, [ref])[0].id
        if ref_id not in ws.ref_ids():
            raise typer.BadParameter(f"{ref!r} — не референс (colormatch ws ref --add)")
    results = matching.match_workspace(ws, scenes or None, ids, settings, to_group_mean, jobs, ref_id=ref_id)
    if remember:
        ws.settings = settings
    workspace_io.save(ws, workspace_io.ws_path(path))
    failed = sum(1 for r in results if "error" in r)
    if as_json:
        _emit({"files": results, "seconds": round(time.perf_counter() - t0, 2)})
    else:
        table = Table(title=f"Авто-баланс воркспейса ({time.perf_counter() - t0:.1f} с)")
        for col in ("фото", "оттенок ΔE", "Δ насыщ.", "ползунки"):
            table.add_column(col)
        for r in results:
            name = Path(r["file"]).name[:34]
            if "error" in r:
                table.add_row(name, "[red]—[/]", "", r["error"][:70])
                continue
            sliders = ", ".join(f"{k}={v:+g}" for k, v in r["params"].items() if v)
            table.add_row(name, f"{r['before']['cast_dE']:.1f} → {r['after']['cast_dE']:.1f}",
                          f"{r['before']['dS50']:+.1f} → {r['after']['dS50']:+.1f}", sliders or "—")
        Console().print(table)
    if not results:
        console.print("[yellow]нечего подбирать: нет фото вне наборов референсов[/]")
    if failed:
        raise typer.Exit(2 if failed < len(results) else 1)


# --- экспорт и упаковка ---------------------------------------------------------------------------

@ws_app.command("export")
def ws_export(
    path: Path = WS,
    out: Optional[Path] = typer.Option(None, "--out", "-o", file_okay=False, help="Папка вывода (по умолчанию — из воркспейса или рядом с исходниками)."),
    naming: Optional[Naming] = typer.Option(None, "--naming", help="overwrite | suffix | version."),
    suffix: Optional[str] = typer.Option(None, "--suffix"),
    fmt: Optional[str] = typer.Option(None, "--format", help="keep | jpg | png | tif."),
    quality: Optional[int] = typer.Option(None, "--quality", min=1, max=100),
    bit_depth: Optional[str] = typer.Option(None, "--bit-depth", help="keep | 8 | 16."),
    flat: bool = typer.Option(False, "--flat", help="Без подпапок-сцен."),
    scenes: list[str] = typer.Option([], "--scene", "-s"),
    photos: list[str] = typer.Option([], "--photo", "-p"),
    force: bool = typer.Option(False, "--force", help="Разрешить перезапись исходных файлов."),
    jobs: int = typer.Option(0, "--jobs", "-j", min=0),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Экспорт с правками: те же имена (overwrite), с суффиксом или новой версией (_v001…)."""
    ws = _open(path)
    s = ws.export.model_copy(deep=True)
    if out:
        s.out_dir = str(out.resolve())
    for field, val in (("naming", naming), ("suffix", suffix), ("format", fmt), ("quality", quality), ("bit_depth", bit_depth)):
        if val is not None:
            setattr(s, field, val)
    s.keep_structure = s.keep_structure and not flat
    s = type(s).model_validate(s.model_dump())  # проверка format/bit_depth
    ids = {p.id for p in _photos(ws, photos)} or None
    t0 = time.perf_counter()
    results = export_svc.export(ws, ids, scenes or None, s, force, jobs)
    failed = sum(1 for r in results if "error" in r)
    if as_json:
        _emit({"files": results, "seconds": round(time.perf_counter() - t0, 2)})
    else:
        for r in results:
            if "error" in r:
                console.print(f"[red]✗[/] {Path(r['src']).name}: {r['error']}")
            else:
                console.print(f"[green]✓[/] {Path(r['src']).name} → {r['dst']}")
        console.print(f"готово: {len(results) - failed}, ошибок: {failed}, {time.perf_counter() - t0:.1f} с")
    if failed:
        raise typer.Exit(2 if failed < len(results) else 1)


@ws_app.command("pack")
def ws_pack(
    path: Path = WS,
    out: Path = typer.Option(..., "--out", "-o", file_okay=False, help="Папка упаковки."),
    name: Optional[str] = typer.Option(None, "--name", help="Имя воркспейса в упаковке."),
    with_renders: bool = typer.Option(False, "--with-renders", help="Положить рядом и отрендеренные версии (renders/)."),
    jobs: int = typer.Option(0, "--jobs", "-j", min=0),
) -> None:
    """Сохранить воркспейс со всеми фото в отдельную папку (переносимо)."""
    ws = _open(path)
    try:
        packed_path = workspace_io.pack(ws, out, name)
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"{exc} — сначала colormatch ws check --relink") from None
    console.print(f"[green]✓[/] {packed_path}")
    if with_renders:
        packed = workspace_io.load(packed_path)
        s = packed.export.model_copy(update={"out_dir": str(Path(out).resolve() / "renders"), "naming": Naming.overwrite})
        failed = [r for r in export_svc.export(packed, settings=s, workers=jobs) if "error" in r]
        console.print(f"рендеры: {out / 'renders'}" + (f", ошибок: {len(failed)}" if failed else ""))


@ws_app.command("check")
def ws_check(
    path: Path = WS,
    relink: Optional[Path] = typer.Option(None, "--relink", exists=True, file_okay=False, help="Искать пропавшие файлы по имени в этой папке."),
) -> None:
    """Проверить, что все файлы на месте; --relink — найти пропавшие."""
    ws = _open(path)
    if relink:
        fixed = workspace_io.relink(ws, relink)
        if fixed:
            workspace_io.save(ws, workspace_io.ws_path(path))
        console.print(f"найдено и перепривязано: {len(fixed)}")
    missing = ws.missing()
    for ph in missing:
        console.print(f"[red]нет файла[/] {ph.id}: {ph.path}")
    console.print(f"всего фото: {sum(1 for _ in ws.all_photos())}, пропало: {len(missing)}")
    if missing:
        raise typer.Exit(1)
