import json
from pathlib import Path

import numpy as np
from typer.testing import CliRunner

from colormatcher.cli.main import app
from colormatcher.core import imageio

runner = CliRunner()


def test_filters_list_json():
    res = runner.invoke(app, ["filters", "list", "--json"])
    assert res.exit_code == 0
    groups = {f["group"] for f in json.loads(res.stdout)}
    assert groups == {"bc", "cb", "hs"}


def test_preset_save_show_apply(tmp_path, gradient_img):
    src = tmp_path / "in.png"
    imageio.save_image(src, gradient_img)
    preset = tmp_path / "p.cmpreset.json"

    res = runner.invoke(app, ["preset", "save", str(preset), "-s", "hs.saturation=-100", "-s", "cb.preserve_luminosity=false"])
    assert res.exit_code == 0, res.output
    shown = json.loads(runner.invoke(app, ["preset", "show", str(preset), "--json"]).stdout)
    assert shown["params"] == {"cb.preserve_luminosity": False, "hs.saturation": -100.0}

    out = tmp_path / "out"
    res = runner.invoke(app, ["apply", str(src), "-p", str(preset), "-o", str(out), "--json"])
    assert res.exit_code == 0, res.output
    dst = json.loads(res.stdout)["files"][0]["dst"]
    assert dst.endswith("in_v001.png")
    img = imageio.load_image(dst).rgb
    assert np.abs(img[..., 0] - img[..., 1]).max() < 2 / 255  # обесцвечено


def test_apply_refuses_overwriting_source(tmp_path, gradient_img):
    src = tmp_path / "in.png"
    imageio.save_image(src, gradient_img)
    res = runner.invoke(app, ["apply", str(src), "-s", "bc.brightness=10", "-o", str(tmp_path), "--naming", "overwrite"])
    assert res.exit_code == 1


def _scene_files(tmp_path):
    from conftest import make_scene
    from colormatcher.core.pipeline import render_direct
    from colormatcher.models import AdjustmentStack

    scene = make_scene()
    ref = tmp_path / "ref.png"
    imageio.save_image(ref, scene)
    targets = []
    for i, d in enumerate(({"cb.midtones.yb": -25}, {"hs.saturation": -35})):
        p = tmp_path / "shots" / f"shot{i}.png"
        imageio.save_image(p, render_direct(scene, AdjustmentStack().update(d)))
        targets.append(p)
    return ref, targets


def test_analyze_and_compare_json(tmp_path):
    ref, targets = _scene_files(tmp_path)
    res = runner.invoke(app, ["analyze", str(ref), "--json"])
    assert res.exit_code == 0, res.output
    prof = json.loads(res.stdout)[0]
    assert set(prof["bands"]) == {"shadows", "midtones", "highlights"} and prof["confidence"] > 0.5
    res = runner.invoke(app, ["compare", "-r", str(ref), *map(str, targets), "--json"])
    rows = json.loads(res.stdout)
    assert rows[0]["cast_dE"] > 3 and abs(rows[1]["dS50"]) > 5


def test_match_writes_presets_and_renders(tmp_path):
    ref, targets = _scene_files(tmp_path)
    out = tmp_path / "out"
    res = runner.invoke(app, ["match", "-r", str(ref), "-d", str(tmp_path / "shots"), "--out-presets", str(out / "p"),
                              "-o", str(out / "img"), "--jobs", "2", "--json"])
    assert res.exit_code == 0, res.output
    files = json.loads(res.stdout)["files"]
    assert len(files) == 2 and all("error" not in f for f in files)
    assert files[0]["after"]["cast_dE"] < files[0]["before"]["cast_dE"] / 2
    assert all(Path(f["preset"]).exists() and Path(f["output"]).exists() for f in files)


def test_match_needs_reference(tmp_path):
    _, targets = _scene_files(tmp_path)
    assert runner.invoke(app, ["match", str(targets[0])]).exit_code != 0
    res = runner.invoke(app, ["match", "--to-group-mean", *map(str, targets), "--jobs", "1", "--json"])
    assert res.exit_code == 0, res.output


def test_bad_key_rejected(tmp_path, gradient_img):
    src = tmp_path / "in.png"
    imageio.save_image(src, gradient_img)
    res = runner.invoke(app, ["apply", str(src), "-s", "cb.wrong=1", "-o", str(tmp_path / "o")])
    assert res.exit_code != 0
