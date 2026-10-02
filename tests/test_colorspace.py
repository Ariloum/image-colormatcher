import numpy as np

from colormatcher.core.colorspace import hsl_to_rgb, linear_to_srgb, rgb_to_hsl, srgb_to_lab, srgb_to_linear


def test_hsl_roundtrip(random_img):
    back = hsl_to_rgb(*rgb_to_hsl(random_img))
    assert np.abs(back - random_img).max() < 1e-5


def test_linear_roundtrip(random_img):
    assert np.abs(linear_to_srgb(srgb_to_linear(random_img)) - random_img).max() < 1e-5


def test_lab_reference_points():
    lab = srgb_to_lab(np.array([[1, 1, 1], [0, 0, 0], [0.5, 0.5, 0.5], [1, 0, 0]], dtype=np.float32))
    assert np.allclose(lab[0], [100, 0, 0], atol=0.05)
    assert np.allclose(lab[1], [0, 0, 0], atol=0.05)
    assert abs(lab[2, 1]) < 0.05 and abs(lab[2, 2]) < 0.05
    assert np.allclose(lab[3], [53.24, 80.09, 67.20], atol=0.1)  # sRGB red, D65
