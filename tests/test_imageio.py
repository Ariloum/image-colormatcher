import numpy as np
from PIL import Image

from colormatcher.core import imageio


def test_jpeg_roundtrip_cyrillic_path_keeps_exif_icc(tmp_path, gradient_img):
    src = tmp_path / "сцена 1" / "фото.jpg"
    exif = Image.Exif()
    exif[0x010F] = "TestCam"  # Make
    icc = b"fake-icc-profile"
    imageio.save_image(src, gradient_img, exif=exif.tobytes(), icc=icc, quality=98)
    img = imageio.load_image(src)
    assert img.rgb.shape == gradient_img.shape and img.bit_depth == 8
    assert np.abs(img.rgb - gradient_img).mean() < 0.01
    assert img.icc == icc
    assert Image.open(src).getexif()[0x010F] == "TestCam"
    out = tmp_path / "копия.jpg"
    imageio.save_image(out, img.rgb, exif=img.exif, icc=img.icc)
    assert Image.open(out).getexif()[0x010F] == "TestCam"


def test_png16_roundtrip(tmp_path, gradient_img):
    path = tmp_path / "деталь.png"
    imageio.save_image(path, gradient_img, bit_depth=16)
    img = imageio.load_image(path)
    assert img.bit_depth == 16
    assert np.abs(img.rgb - gradient_img).max() < 1e-4


def test_alpha_preserved(tmp_path, gradient_img):
    alpha = np.linspace(0, 1, gradient_img.shape[1], dtype=np.float32)[None, :].repeat(gradient_img.shape[0], 0)
    path = tmp_path / "a.png"
    imageio.save_image(path, gradient_img, alpha=alpha)
    img = imageio.load_image(path)
    assert img.alpha is not None and np.abs(img.alpha - alpha).max() < 1 / 255 + 1e-6


def test_exif_orientation_applied(tmp_path):
    arr = np.zeros((20, 40, 3), np.uint8)
    arr[:, :5] = 255  # белая полоса слева
    exif = Image.Exif()
    exif[0x0112] = 6  # повернуть на 90° по часовой
    path = tmp_path / "rot.jpg"
    Image.fromarray(arr).save(path, exif=exif.tobytes(), quality=100)
    img = imageio.load_image(path)
    assert img.size == (20, 40)                       # стороны поменялись
    assert img.rgb[:5].mean() > 0.9                    # полоса ушла наверх
    assert Image.open(path).getexif()[0x0112] == 6     # исходник не тронут
    out = tmp_path / "out.jpg"
    imageio.save_image(out, img.rgb, exif=img.exif)
    assert Image.open(out).getexif().get(0x0112, 1) == 1  # ориентация сброшена


def test_proxy(gradient_img):
    p = imageio.make_proxy(gradient_img, 32)
    assert max(p.shape[:2]) == 32 and p.dtype == np.float32
    assert imageio.make_proxy(gradient_img, 4096) is gradient_img
