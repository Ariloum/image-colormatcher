import numpy as np
import pytest

from colormatcher.core.analysis import (
    apply_offset, band_weights, combine, measure, profile, region_pixels, robust_lab, sample_image,
)
from colormatcher.core.colorspace import srgb_to_lab, srgb_to_lab_fast
from colormatcher.core.pipeline import render_direct
from colormatcher.models import AdjustmentStack
from colormatcher.models.profile import ProfileOffset


def gray_ramp(h=64, w=256):
    x = np.linspace(0.02, 0.97, w, dtype=np.float32)[None, :, None]
    return np.broadcast_to(x, (h, w, 3)).copy()


def test_fast_lab_matches_exact(random_img):
    assert np.abs(srgb_to_lab_fast(random_img) - srgb_to_lab(random_img)).max() < 0.05


def test_band_weights_cover_range():
    w = band_weights(np.array([0, 50, 100], np.float32))
    assert w[0, 0] == 1 and w[1, 1] == 1 and w[2, 2] == 1
    assert w[0, 2] == 0 and w[2, 0] == 0


def test_neutral_image_has_no_cast():
    p = profile(gray_ramp())
    for band in p.bands.values():
        assert abs(band.a) < 0.3 and abs(band.b) < 0.3 and band.mass > 0.05
    assert p.neutral_share > 0.9 and p.confidence > 0.8


def test_blue_cast_detected_per_band():
    img = render_direct(gray_ramp(), AdjustmentStack().update({"cb.highlights.yb": 40}))
    p = profile(img)
    assert p.bands["highlights"].b < -3                    # света посинели
    assert abs(p.bands["shadows"].b) < abs(p.bands["highlights"].b)


def test_colored_objects_do_not_fake_a_cast(scene):
    # насыщенные объекты сцены — не нейтрали; каст считается по серым плашкам и небу
    p = profile(scene)
    assert abs(p.bands["midtones"].a) < 3


def test_tile_voting_resists_big_tinted_area():
    img = gray_ramp(128, 256)
    img[:, :64] *= np.array([1.0, 1.0, 0.9], np.float32)  # четверть кадра слегка желтее (ещё «нейтраль»)
    p = profile(img)
    assert abs(p.bands["midtones"].b) < 2.0


def test_clipped_pixels_ignored():
    img = gray_ramp()
    img[:, -40:] = (1.0, 0.99, 0.2)  # пересвеченный жёлтый блик
    s = sample_image(img)
    assert s.w_band[s.rgb.max(axis=1) >= 0.985].sum() == 0


def test_measure_follows_correction(scene):
    s = sample_image(scene)
    warm = render_direct(s.rgb, AdjustmentStack().update({"cb.midtones.yb": -30}))
    assert measure(s, warm).bands["midtones"].b > measure(s).bands["midtones"].b + 3


def test_profile_after_own_stack(scene):
    plain = profile(scene)
    darker = profile(scene, AdjustmentStack().update({"bc.brightness": -60}))
    assert darker.L_p50 < plain.L_p50 - 5


def test_combine_and_offset(scene):
    p1 = profile(scene)
    p2 = profile(render_direct(scene, AdjustmentStack().update({"bc.brightness": 50})))
    mean = combine([p1, p2], [1, 1])
    assert p1.L_p50 < mean.L_p50 < p2.L_p50
    assert combine([p1, p2], [1, 0]).L_p50 == pytest.approx(p1.L_p50)
    off = apply_offset(p1, ProfileOffset(L=5, b=3, chroma=1.2))
    assert off.L_p50 == pytest.approx(p1.L_p50 + 5)
    assert off.bands["midtones"].b == pytest.approx(p1.bands["midtones"].b + 3)
    assert off.S_p50 == pytest.approx(min(0.99, p1.S_p50 * 1.2))


def test_region_pixels(scene):
    px = region_pixels(scene, (8 / 180, 70 / 120, 20 / 180, 40 / 120))  # первая серая плашка 0.08
    lab = robust_lab(px)
    assert abs(lab[1]) < 1.5 and abs(lab[2]) < 1.5 and lab[0] < 15
