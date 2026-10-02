"""Авто-баланс по маскам (М3, T-0063): слой метки, исключения из статистики, сценарий воркспейса."""

import numpy as np

from colormatcher.core import imageio
from colormatcher.core.analysis import sample_image
from colormatcher.core.colorspace import srgb_to_lab
from colormatcher.core.pipeline import render_direct, render_masked
from colormatcher.core.region_match import match_region, region_sample, region_stats
from colormatcher.models import AdjustmentStack
from colormatcher.models.profile import MatchSettings
from colormatcher.models import masks as M
from colormatcher.services import matching, workspace_io
from conftest import make_scene


def _lab_mean(px):
    return srgb_to_lab(np.asarray(px, np.float32).reshape(-1, 3)).mean(0)


def test_match_region_restores_area_colour():
    ref = make_scene(160, 240)
    area = np.zeros(ref.shape[:2], np.float32)
    area[:50] = 1.0                                              # «небо»
    shifted = render_direct(ref, AdjustmentStack().update({"bc.brightness": -25, "cb.midtones.yb": 30,
                                                           "cb.highlights.cr": -20}))
    rpx, rw = region_sample(ref, area)
    tpx, tw = region_sample(shifted, area)
    res = match_region(tpx, tw, region_stats(rpx, rw), MatchSettings())
    assert res.after["dL"] < res.before["dL"] / 3 and res.after["dAB"] < res.before["dAB"] / 3
    fixed = render_direct(tpx, res.stack)
    assert np.linalg.norm(_lab_mean(fixed) - _lab_mean(rpx)) < 2.5
    assert res.params and all(float(v).is_integer() for v in res.params.values())   # целые, как в PS


def test_sample_excludes_ignored_areas():
    img = make_scene(120, 180)
    ex = np.zeros(img.shape[:2], np.float32)
    ex[:, :90] = 1.0
    s_all = sample_image(img, 4000)
    s_ex = sample_image(img, 4000, exclude=ex)
    h, w = s_ex.shape
    left = np.zeros((h, w), bool)
    left[:, : w // 2 - 1] = True
    assert s_all.w_valid.reshape(h, w)[left].sum() > 0
    assert s_ex.w_valid.reshape(h, w)[left].sum() == 0 and s_ex.w_band.reshape(h, w, 3)[left].sum() == 0


def test_workspace_match_fills_layers_for_marked_area(tmp_path):
    ref_img = make_scene(200, 300, seed=3)
    glob = AdjustmentStack().update({"cb.midtones.cr": 20, "cb.shadows.yb": -15})
    region = AdjustmentStack().update({"bc.brightness": -30, "cb.midtones.yb": 35})
    tgt = render_direct(ref_img, glob)
    sky = slice(0, 60)
    tgt[sky] = render_direct(tgt[sky], region)                 # «небо» кадра испорчено отдельно
    imageio.save_image(tmp_path / "shoot" / "ref.png", ref_img)
    imageio.save_image(tmp_path / "shoot" / "shot.png", tgt)
    ws = workspace_io.new_workspace(tmp_path / "w.cmws")
    r = ws.add_photo(tmp_path / "shoot" / "ref.png", "s")
    t = ws.add_photo(tmp_path / "shoot" / "shot.png", "s")
    ws.add_ref(r.id)
    mask = M.empty(*M.shape_for(200, 300))
    mh = mask.shape[0] * 60 // 200
    mask[: mh - 2, :, 0] = 255                                 # метка только на кадре -> та же область на рефе
    ws.set_mask(t.id, mask)
    res = matching.match_workspace(ws, workers=1)
    rt = next(x for x in res if x.get("id") == t.id)
    assert "error" not in rt, rt
    assert "0" in {str(k) for k in rt["layers"]} and rt["regions"][0]["same_geometry"]
    ph = ws.locate(t.id)[1]
    assert 0 in ph.layers and not ph.layers[0].is_identity()
    out = render_masked(tgt, ph.stack, ph.active_layers(), mask, lut_size=33)
    only_global = render_masked(tgt, ph.stack, {}, None, lut_size=33)
    top = slice(5, 50)
    err_layer = np.linalg.norm(_lab_mean(out[top]) - _lab_mean(ref_img[top]))
    err_global = np.linalg.norm(_lab_mean(only_global[top]) - _lab_mean(ref_img[top]))
    assert err_layer < err_global / 2 and err_layer < 4.0
    # без масок — классический баланс, слоёв нет
    ws2 = workspace_io.new_workspace(tmp_path / "w2.cmws")
    r2 = ws2.add_photo(tmp_path / "shoot" / "ref.png", "s")
    t2 = ws2.add_photo(tmp_path / "shoot" / "shot.png", "s")
    ws2.add_ref(r2.id)
    ws2.set_mask(t2.id, mask)
    ws2.settings.use_masks = False
    res2 = matching.match_workspace(ws2, workers=1)
    assert "layers" not in next(x for x in res2 if x.get("id") == t2.id)
    assert not ws2.locate(t2.id)[1].layers
