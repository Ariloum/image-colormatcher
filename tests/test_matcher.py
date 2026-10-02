import numpy as np

from colormatcher.core.analysis import profile
from colormatcher.core.colorspace import delta_e76, srgb_to_lab
from colormatcher.core.matcher import MODE_PARAMS, make_anchor, match
from colormatcher.core.pipeline import render_direct
from colormatcher.models import AdjustmentStack
from colormatcher.models.profile import MatchSettings

WARM = {"cb.midtones.cr": 20, "cb.midtones.yb": -25, "cb.highlights.yb": -15}


def px_de(a, b):
    return float(delta_e76(srgb_to_lab(a), srgb_to_lab(b)).mean())


def test_match_removes_cast(scene):
    bad = render_direct(scene, AdjustmentStack().update(WARM))
    res = match(bad, profile(scene))
    assert res.metrics_before["cast_dE"] > 5
    assert res.metrics_after["cast_dE"] < 1.0
    assert px_de(render_direct(bad, res.stack), scene) < 0.5 * px_de(bad, scene)


def test_match_restores_saturation(scene):
    pale = render_direct(scene, AdjustmentStack().update({"hs.saturation": -40}))
    res = match(pale, profile(scene))
    assert res.params["hs.saturation"] > 15
    assert abs(res.metrics_after["dS50"]) < abs(res.metrics_before["dS50"]) / 3


def test_tone_mode_fixes_brightness(scene):
    dark = render_direct(scene, AdjustmentStack().update({"bc.brightness": -35}))
    res = match(dark, profile(scene), MatchSettings(mode="tone+balance+sat"))
    assert res.params["bc.brightness"] > 15
    assert abs(res.metrics_after["dL50"]) < abs(res.metrics_before["dL50"]) / 3


def test_only_touched_params_change(scene):
    bad = render_direct(scene, AdjustmentStack().update(WARM))
    res = match(bad, profile(scene), MatchSettings(touch=["cb.midtones.cr", "cb.midtones.yb"]))
    assert set(res.params) == {"cb.midtones.cr", "cb.midtones.yb"}
    assert res.stack.get("cb.highlights.yb") == 0 and res.stack.get("hs.saturation") == 0


def test_limits_respected(scene):
    bad = render_direct(scene, AdjustmentStack().update({"hs.saturation": -80}))
    s = MatchSettings()
    s.limits["hs.saturation"] = 10
    assert match(bad, profile(scene), s).params["hs.saturation"] <= 10


def test_strength_and_base_stack(scene):
    bad = render_direct(scene, AdjustmentStack().update(WARM))
    base = AdjustmentStack().update({"bc.contrast": 12})
    res = match(bad, profile(scene), MatchSettings(strength=60), base_stack=base)
    assert res.stack.opacity == 60 and res.stack.get("bc.contrast") == 12  # не решаемые — сохранены


def test_anchor_pulls_region_to_reference(scene):
    # якорь: серая плашка 0.65 на референсе и на цели (без пересвета — его «Уровнями» не вернуть)
    rect = (104 / 180, 70 / 120, 20 / 180, 40 / 120)
    bad = render_direct(scene, AdjustmentStack().update({"cb.midtones.cr": 35}))
    anchor = make_anchor(bad, rect, scene, rect, weight=2.0)
    res = match(bad, profile(scene), anchors=[anchor])
    assert res.anchors_after[0] < res.anchors_before[0] / 2


def test_modes_are_consistent():
    assert set(MODE_PARAMS["balance"]) < set(MODE_PARAMS["balance+sat"]) < set(MODE_PARAMS["full"])
    assert "bc.brightness" in MODE_PARAMS["tone+balance+sat"]


def test_result_is_sparse_and_rounded(scene):
    bad = render_direct(scene, AdjustmentStack().update(WARM))
    res = match(bad, profile(scene))
    vals = [v for v in res.params.values() if v]
    assert all(v == round(v) for v in vals)  # целые, как в Photoshop
    assert len(vals) < len(res.params)
    assert np.isfinite(res.seconds)
