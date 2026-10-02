import numpy as np

from colormatcher.core import lut as lutlib
from colormatcher.core.pipeline import bake_stack, render, render_direct, render_layers
from colormatcher.models import AdjustmentStack

BUSY = {
    "bc.brightness": 30, "bc.contrast": 25,
    "cb.shadows.cr": 20, "cb.midtones.mg": -35, "cb.highlights.yb": 40,
    "hs.hue": 15, "hs.saturation": 30, "hs.lightness": -5,
}


def test_identity_lut_is_exact(random_img):
    assert np.abs(lutlib.apply(random_img, lutlib.identity_lut(17)) - random_img).max() < 1e-6


def test_lut_matches_direct_render(gradient_img):
    stack = AdjustmentStack().update(BUSY)
    direct = render_direct(gradient_img, stack)
    for size, tol in ((33, 0.02), (65, 0.01)):
        err = np.abs(render(gradient_img, stack, lut_size=size) - direct)
        assert err.mean() < tol / 4 and np.percentile(err, 99) < tol


def test_numba_matches_numpy(random_img):
    lut = bake_stack(AdjustmentStack().update(BUSY), 17)
    assert np.abs(lutlib.apply(random_img, lut) - lutlib.apply_numpy(random_img, lut)).max() < 1e-5


def test_identity_stack_skips_work(random_img):
    stack = AdjustmentStack()
    assert stack.is_identity() and bake_stack(stack) is None
    assert np.array_equal(render(random_img, stack), random_img)


def test_opacity_blends(gradient_img):
    full = render_direct(gradient_img, AdjustmentStack().update(BUSY))
    half = render_direct(gradient_img, AdjustmentStack().update({**BUSY, "opacity": 50}))
    assert np.allclose(half, (gradient_img + full) / 2, atol=1e-5)
    zero = render(gradient_img, AdjustmentStack().update({**BUSY, "opacity": 0}))
    assert np.array_equal(zero, gradient_img)


def test_mask_and_layers(gradient_img):
    stack = AdjustmentStack().update({"hs.lightness": 100})
    mask = np.zeros(gradient_img.shape[:2], np.float32)
    mask[:, :10] = 1.0
    out = render_layers(gradient_img, AdjustmentStack(), [(stack, mask)])
    assert np.allclose(out[:, :10], 1.0, atol=1e-3)
    assert np.array_equal(out[:, 10:], gradient_img[:, 10:])


def test_disabled_filter_ignored(random_img):
    stack = AdjustmentStack().update({"hs.saturation": -100})
    stack.filter("hue_saturation").enabled = False
    assert stack.is_identity()
