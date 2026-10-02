import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from colormatcher.cli.main import app
from colormatcher.core import imageio
from colormatcher.core.pipeline import render_direct
from colormatcher.models import AdjustmentStack
from colormatcher.services import export as export_svc
from colormatcher.services import importer, matching, workspace_io
from colormatcher.services.naming import Naming

from conftest import make_scene

runner = CliRunner()


@pytest.fixture
def shoot(tmp_path):
    """Съёмка по сценам: shoot/day/{ref,a,b}.png, shoot/night/{c,a}.png (одинаковое имя a.png!)."""
    scene = make_scene()
    root = tmp_path / "shoot"
    files = {
        "day/ref.png": scene,
        "day/a.png": render_direct(scene, AdjustmentStack().update({"cb.midtones.yb": -25})),
        "day/b.png": render_direct(scene, AdjustmentStack().update({"hs.saturation": -30})),
        "night/c.png": render_direct(scene, AdjustmentStack().update({"bc.brightness": -40})),
        "night/a.png": render_direct(scene, AdjustmentStack().update({"cb.shadows.cr": 20})),
    }
    for rel, img in files.items():
        imageio.save_image(root / rel, img)
    return root


def make_ws(tmp_path, shoot):
    ws = workspace_io.new_workspace(tmp_path / "proj" / "w.cmws")
    importer.add_folder(ws, shoot)
    return ws


def test_import_scenes_and_duplicates(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    assert [s.name for s in ws.scenes] == ["shoot/day", "shoot/night"]
    assert [p.name for p in ws.scene("shoot/day").photos] == ["a.png", "b.png", "ref.png"]
    assert importer.add_folder(ws, shoot) == []                       # повтор — пропуск
    assert importer.add_files(ws, [shoot / "day" / "a.png"], "x") == []


def test_save_load_roundtrip_relative_paths(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    ws.scene("shoot/day").photos[0].stack.set("cb.midtones.cr", 12)
    path = workspace_io.save(ws, tmp_path / "proj" / "w.cmws")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["scenes"][0]["photos"][0]["path"] == "../shoot/day/a.png"
    back = workspace_io.load(path)
    assert back.scene("shoot/day").photos[0].stack.get("cb.midtones.cr") == 12
    assert not back.missing()
    workspace_io.save(back, path)
    assert path.with_name("w.cmws.bak").exists()


def test_save_as_elsewhere_rebases_paths(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    other = workspace_io.save(ws, tmp_path / "deep" / "er" / "w2.cmws")
    assert not workspace_io.load(other).missing()


def test_future_schema_rejected(tmp_path):
    p = tmp_path / "x.cmws"
    p.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(ValueError):
        workspace_io.load(p)


def test_find_and_ambiguity(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    assert ws.find("ref.png").name == "ref.png"
    assert ws.find(str(shoot / "night" / "a.png")).path.endswith("night/a.png")
    with pytest.raises(KeyError):
        ws.find("a.png")  # два файла с таким именем


def test_remove_cleans_refs(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    ref = ws.find("ref.png")
    ws.add_ref(ref.id)
    b = ws.find("b.png")
    b.ref_id = ref.id
    assert ws.remove_photos({ref.id}) == 1
    assert not ws.refs and ws.active_ref is None and b.ref_id is None
    assert ws.remove_scene("shoot/night") == 2 and len(ws.scenes) == 1


def test_refs_single_active_and_ref_for(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    day, night = ws.find("ref.png"), ws.find("c.png")
    ws.add_ref(day.id)
    ws.add_ref(night.id, activate=False)
    assert ws.active_ref == day.id and ws.ref_ids() == {day.id, night.id}
    b = ws.find("b.png")
    assert ws.ref_for(b) == day.id                    # нового фото — активный
    b.ref_id = night.id
    assert ws.ref_for(b) == night.id                  # запомненный важнее активного
    ws.set_active(night.id)
    with pytest.raises(KeyError):
        ws.set_active(b.id)                           # не референс
    ws.remove_ref(night.id)
    assert ws.active_ref == day.id and ws.ref_for(b) == day.id


def test_migration_v1_refs(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    path = workspace_io.save(ws, tmp_path / "proj" / "old.cmws")
    data = json.loads(path.read_text(encoding="utf-8"))
    ref, c = ws.find("ref.png").id, ws.find("c.png").id
    data.pop("refs"), data.pop("active_ref")
    data["schema_version"] = 1
    data["global_refs"] = {"items": [{"photo_id": ref, "weight": 1.0}], "mode": "mean", "offset": {"b": 3}}
    data["scenes"][1]["refs"] = {"items": [{"photo_id": c, "weight": 2.0}], "mode": "mean", "offset": {}}
    path.write_text(json.dumps(data), encoding="utf-8")
    back = workspace_io.load(path)
    assert [r.photo_id for r in back.refs] == [ref, c] and back.active_ref == ref
    assert back.ref_item(ref).offset.b == 3 and back.schema_version == 2


def test_pack_copies_and_is_portable(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    packed = workspace_io.pack(ws, tmp_path / "pack")
    moved = tmp_path / "moved"
    shutil.copytree(tmp_path / "pack", moved)
    shutil.rmtree(tmp_path / "pack")
    back = workspace_io.load(moved / packed.name)
    assert not back.missing() and sum(1 for _ in back.all_photos()) == 5
    assert all(not Path(p.path).is_absolute() for p in back.all_photos())
    assert not workspace_io.load(workspace_io.save(ws, tmp_path / "proj" / "w.cmws")).missing()  # оригинал цел


def test_pack_refuses_missing_and_relink(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    workspace_io.save(ws, tmp_path / "proj" / "w.cmws")
    shutil.move(str(shoot), str(tmp_path / "relocated"))
    with pytest.raises(FileNotFoundError):
        workspace_io.pack(ws, tmp_path / "pack")
    fixed = workspace_io.relink(ws, tmp_path / "relocated")
    assert len(fixed) == 3  # однозначные имена; два a.png — неоднозначны, остаются пропавшими
    assert len(ws.missing()) == 2


def test_export_versions_and_collisions(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    s = ws.export.model_copy(update={"out_dir": str(tmp_path / "out"), "keep_structure": False})
    r1 = export_svc.export(ws, settings=s, workers=1)
    names = sorted(Path(r["dst"]).name for r in r1)
    assert names == ["a_v001.png", "a_v002.png", "b_v001.png", "c_v001.png", "ref_v001.png"]
    r2 = export_svc.export(ws, photo_ids={ws.find("b.png").id}, settings=s, workers=1)
    assert Path(r2[0]["dst"]).name == "b_v002.png"


def test_export_structure_format_and_overwrite_guard(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    ws.find("b.png").stack.set("hs.saturation", 40)
    s = ws.export.model_copy(update={"out_dir": str(tmp_path / "out"), "format": "jpg", "naming": Naming.overwrite})
    res = export_svc.export(ws, scenes=["shoot/day"], settings=s, workers=1)
    assert {Path(r["dst"]).relative_to(tmp_path / "out").as_posix() for r in res} == {
        "shoot/day/a.jpg", "shoot/day/b.jpg", "shoot/day/ref.jpg"}
    in_place = ws.export.model_copy(update={"out_dir": None, "naming": Naming.overwrite})
    guarded = export_svc.export(ws, photo_ids={ws.find("b.png").id}, settings=in_place, workers=1)
    assert "error" in guarded[0]


def test_match_workspace_locations(tmp_path, shoot):
    ws = make_ws(tmp_path, shoot)
    res = matching.match_workspace(ws, workers=1)
    assert res and all("нет референса" in r["error"] for r in res)          # референсов нет
    assert all("error" not in r for r in matching.match_workspace(ws, to_group_mean=True, workers=1))
    for ph in ws.all_photos():
        ph.stack, ph.ref_id = type(ph.stack)(), None

    day_ref, night_ref = ws.find("ref.png"), ws.find("c.png")
    ws.add_ref(day_ref.id)
    ws.add_ref(night_ref.id, activate=False)
    ws.find("b.png").stack.set("bc.contrast", 7)  # ручная правка вне решаемых — должна сохраниться
    day = {p.id for p in ws.scene("shoot/day").photos} - {day_ref.id}
    night_a = ws.find(str(shoot / "night" / "a.png"))
    # локация 1: выделенные дневные — по активному
    r1 = matching.match_workspace(ws, photo_ids=day, workers=1, ref_id=ws.active_ref)
    assert {r["ref_id"] for r in r1} == {day_ref.id}
    a = ws.find(str(shoot / "day" / "a.png"))
    by = {r["id"]: r for r in r1}
    assert by[a.id]["after"]["cast_dE"] < by[a.id]["before"]["cast_dE"] / 2
    assert a.ref_id == day_ref.id and ws.find("b.png").stack.get("bc.contrast") == 7
    # локация 2: переключили активный, подобрали ночной кадр
    ws.set_active(night_ref.id)
    r2 = matching.match_workspace(ws, photo_ids={night_a.id}, workers=1, ref_id=ws.active_ref)
    assert r2[0]["ref_id"] == night_ref.id and night_a.ref_id == night_ref.id
    # весь проект: каждый — к своему запомненному референсу; референсы не трогаются
    seen = []
    r3 = matching.match_workspace(ws, workers=1, progress=lambda d, n: seen.append((d, n)))
    refs_used = {r["id"]: r["ref_id"] for r in r3}
    assert refs_used[a.id] == day_ref.id and refs_used[night_a.id] == night_ref.id
    assert day_ref.id not in refs_used and night_ref.stack.is_identity()
    assert seen[0] == (0, 3) and seen[-1] == (3, 3)


def test_cli_ws_flow(tmp_path, shoot):
    w = str(tmp_path / "cli.cmws")
    ok = lambda *args: runner.invoke(app, ["ws", *args])  # noqa: E731
    assert ok("new", w).exit_code == 0
    assert ok("new", w).exit_code != 0                         # уже есть
    assert ok("add", w, "-d", str(shoot)).exit_code == 0
    assert ok("ref", w, "--add", "c.png", "--active", "ref.png").exit_code == 0
    assert ok("ref", w, "--offset-b", "2").exit_code == 0
    assert ok("match", w, "--ref", "b.png", "--jobs", "1").exit_code != 0   # не референс
    r = ok("match", w, "--scene", "shoot/day", "--ref", "ref.png", "--jobs", "1", "--json")
    assert r.exit_code == 0, r.output
    assert len(json.loads(r.stdout)["files"]) == 2
    assert ok("set", w, "-s", "shoot/day", "--set", "hs.saturation=5", "--relative").exit_code == 0
    preset = tmp_path / "p.cmpreset.json"
    assert ok("preset", w, "ref.png", "-o", str(preset), "--groups", "cb").exit_code == 0
    assert ok("set", w, "-p", "c.png", "--preset", str(preset)).exit_code == 0
    shown = json.loads(ok("show", w, "--json").stdout)
    assert len(shown["scenes"]) == 2 and not shown["missing"]
    r = ok("export", w, "-o", str(tmp_path / "exp"), "--jobs", "1", "--json")
    assert r.exit_code == 0 and len(json.loads(r.stdout)["files"]) == 5
    assert ok("pack", w, "-o", str(tmp_path / "pk")).exit_code == 0
    assert ok("check", str(tmp_path / "pk" / "cli.cmws")).exit_code == 0
    assert ok("remove", w, "-s", "shoot/night").exit_code == 0
