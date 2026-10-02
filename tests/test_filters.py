"""Фильтры. Эталонные числа — из снимков Photoshop 25.6 (docs/CALIBRATION.md)."""

import numpy as np
import pytest

from colormatcher.core.filters import BrightnessContrast, ColorBalance, FilterRegistry, HueSaturation
from colormatcher.core.filters.color_balance import channel_levels

GRAY = np.array([0.127, 0.254, 0.381, 0.508, 0.635, 0.762, 0.889], np.float32)


def grays(values=GRAY):
    return np.repeat(np.asarray(values, np.float32)[:, None], 3, axis=1)


@pytest.mark.parametrize("flt", [BrightnessContrast, ColorBalance, HueSaturation])
def test_defaults_are_identity(flt, random_img):
    assert flt.is_identity(flt.defaults())
    assert np.abs(flt.apply(random_img, flt.defaults()) - random_img).max() < 1e-5


def test_registry_keys():
    assert FilterRegistry.resolve_key("cb.shadows.cr")[0] is ColorBalance
    assert FilterRegistry.resolve_key("bc.contrast")[0] is BrightnessContrast
    with pytest.raises(KeyError):
        FilterRegistry.resolve_key("cb.nope")
    with pytest.raises(KeyError):
        FilterRegistry.resolve_key("zz.hue")


def test_params_are_clamped():
    p = ColorBalance.normalize({"shadows.cr": 500, "midtones.yb": -900, "junk": 1})
    assert p["shadows.cr"] == 100 and p["midtones.yb"] == -100 and "junk" not in p


# --- Color Balance: «уровни» по каналам ------------------------------------------

def _cb(**kw):
    p = {k.replace("__", "."): v for k, v in kw.items()}
    return ColorBalance.normalize(p)


def test_cb_is_per_channel(random_img):
    params = _cb(shadows__cr=40, midtones__mg=-30, highlights__yb=25)
    out = ColorBalance.apply(random_img, params)
    swapped = random_img.copy()
    swapped[..., 1:] = swapped[::-1, ::-1, 1:]  # G, B перемешаны — R на выходе не должен измениться
    assert np.allclose(ColorBalance.apply(swapped, params)[..., 0], out[..., 0], atol=1e-6)


def test_cb_shadows_pl_on_is_black_point_of_other_channels():
    out = ColorBalance.apply(grays(), _cb(shadows__cr=100))
    assert np.allclose(out[:, 0], GRAY, atol=1e-6)                 # R не тронут
    expected = np.clip((GRAY - 100 / 255) / (1 - 100 / 255), 0, 1)  # PS: 0, 0, 0, 0.191, 0.399…
    assert np.allclose(out[:, 1], expected, atol=2e-3) and np.allclose(out[:, 2], expected, atol=2e-3)


def test_cb_highlights_pl_on_is_white_point_of_own_channel():
    out = ColorBalance.apply(grays(), _cb(highlights__cr=50))
    assert np.allclose(out[:, 0], np.clip(GRAY / (1 - 50 / 255), 0, 1), atol=2e-3)  # PS: 0.158, 0.316…
    assert np.allclose(out[:, 1:], grays()[:, 1:], atol=1e-6)


def test_cb_midtones_gamma():
    b, w, g = channel_levels(_cb(midtones__yb=100))
    assert np.allclose(g, [2 ** 0.5, 2 ** 0.5, 2 ** -0.5])
    b, w, g = channel_levels(_cb(midtones__yb=100, preserve_luminosity=False))
    assert np.allclose(g, [1, 1, 0.5])


def test_cb_pl_off_matches_ps_samples():
    out = ColorBalance.apply(grays(), _cb(shadows__cr=100, preserve_luminosity=False))
    ps_r = [0.231, 0.378, 0.504, 0.619, 0.725, 0.825, 0.920]
    assert np.allclose(out[:, 0], ps_r, atol=4e-3)
    assert np.allclose(out[:, 1:], grays()[:, 1:], atol=1e-6)


def test_cb_sliders_combine():
    b, w, g = channel_levels(_cb(shadows__cr=60, highlights__yb=-40, midtones__mg=30))
    assert np.allclose(b, [0, 60 / 255, 60 / 255])
    assert np.allclose(w, [1 - 40 / 255, 1 - 40 / 255, 1])


# --- Brightness/Contrast ------------------------------------------------------------

def test_bc_brightness_matches_ps():
    up = BrightnessContrast.apply(grays(), {"brightness": 50})[:, 0]
    down = BrightnessContrast.apply(grays(), {"brightness": -50})[:, 0]
    assert np.allclose(up, [0.174, 0.348, 0.522, 0.678, 0.804, 0.901, 0.967], atol=4e-3)
    assert np.allclose(down, [0.093, 0.185, 0.278, 0.371, 0.471, 0.589, 0.743], atol=4e-3)


def test_bc_negative_brightness_is_inverse():
    x = np.linspace(0, 1, 101, dtype=np.float32)[:, None].repeat(3, 1)
    for b in (30, 75, 150):
        there = BrightnessContrast.apply(x, {"brightness": b})
        back = BrightnessContrast.apply(there, {"brightness": -b})
        assert np.abs(back - x).max() < 3e-3


def test_bc_monotonic_and_endpoints():
    x = np.linspace(0, 1, 256, dtype=np.float32)[:, None].repeat(3, 1)
    for b in range(-150, 151, 15):
        for c in (-50, 0, 40, 100):
            y = BrightnessContrast.apply(x, {"brightness": b, "contrast": c})[:, 0]
            assert np.all(np.diff(y) >= -1e-6) and y[0] == pytest.approx(0, abs=1e-6) and y[-1] == pytest.approx(1)


def test_bc_contrast_is_linear_in_value():
    x = grays()
    c50 = BrightnessContrast.apply(x, {"contrast": 50})
    c100 = BrightnessContrast.apply(x, {"contrast": 100})
    cm50 = BrightnessContrast.apply(x, {"contrast": -50})
    assert np.allclose(c50 - x, (c100 - x) / 2, atol=1e-4)
    assert np.allclose(cm50 - x, -(c50 - x), atol=1e-4)


def test_bc_legacy_matches_ps():
    x = grays()
    assert np.allclose(BrightnessContrast.apply(x, {"brightness": 100, "legacy": True})[:, 0],
                       np.clip(GRAY + 100 / 255, 0, 1), atol=1e-5)
    low = BrightnessContrast.apply(np.zeros((1, 3), np.float32), {"contrast": -50, "legacy": True})
    assert low[0, 0] == pytest.approx(0.249, abs=2e-3)


# --- Hue/Saturation -------------------------------------------------------------------

def test_hs_desaturate_to_gray(random_img):
    out = HueSaturation.apply(random_img, {"saturation": -100})
    assert np.abs(out[..., 0] - out[..., 1]).max() < 1e-5
    assert np.abs(out[..., 1] - out[..., 2]).max() < 1e-5


def test_hs_hue_full_turn(random_img):
    half = HueSaturation.apply(random_img, {"hue": 180})
    assert np.abs(HueSaturation.apply(half, {"hue": -180}) - random_img).max() < 1e-4


def test_hs_hue_rotates_red_to_green():
    red = np.array([[1, 0, 0]], np.float32)
    assert np.allclose(HueSaturation.apply(red, {"hue": 120}), [[0, 1, 0]], atol=1e-5)


def test_hs_saturation_matches_ps():
    px = np.array([[0.635, 0.317, 0.317], [0.317, 0.476, 0.794]], np.float32)
    out = HueSaturation.apply(px, {"saturation": 50})
    assert np.allclose(out, [[0.792, 0.160, 0.160], [0.111, 0.407, 1.0]], atol=4e-3)


def test_hs_saturation_100_is_full(random_img):
    out = HueSaturation.apply(random_img, {"saturation": 100})
    mx, mn = out.max(-1), out.min(-1)
    l = (mx + mn) / 2
    colored = (random_img.max(-1) - random_img.min(-1)) > 1e-3
    s = np.where(l > 0.5, (mx - mn) / (2 - mx - mn), (mx - mn) / np.maximum(mx + mn, 1e-6))
    assert np.allclose(s[colored], 1.0, atol=1e-3)


def test_hs_lightness_applied_before_saturation():
    px = np.array([[0.635, 0.317, 0.317]], np.float32)
    both = HueSaturation.apply(px, {"saturation": 50, "lightness": -30})
    manual = HueSaturation.apply(HueSaturation.apply(px, {"lightness": -30}), {"saturation": 50})
    assert np.allclose(both, manual, atol=1e-6)


def test_hs_lightness_extremes(random_img):
    assert np.allclose(HueSaturation.apply(random_img, {"lightness": 100}), 1.0)
    assert np.allclose(HueSaturation.apply(random_img, {"lightness": -100}), 0.0)
