import numpy as np
import pytest


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def random_img(rng):
    """Случайная картинка 64×48 float32 [0,1]."""
    return rng.random((48, 64, 3), dtype=np.float32)


def make_scene(h: int = 120, w: int = 180, seed: int = 1) -> np.ndarray:
    """Синтетическая «фотосцена»: небо-градиент, серые плашки (нейтрали) разной яркости,
    насыщенные объекты и немного шума — как у реального кадра."""
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    img = (0.25 + 0.6 * (1 - y)) * np.array([0.3, 0.5, 0.98], np.float32) * (0.9 + 0.1 * x)  # небо явно синее
    img = np.broadcast_to(img, (h, w, 3)).copy()
    for i, v in enumerate((0.08, 0.2, 0.35, 0.5, 0.65, 0.8, 0.92)):   # серые плашки
        x0 = 8 + i * 24
        img[70:110, x0:x0 + 20] = v
    img[10:40, 20:60] = (0.8, 0.2, 0.15)    # красный объект
    img[15:45, 110:160] = (0.2, 0.6, 0.25)  # зелёный объект
    img[50:65, 60:120] = (0.9, 0.75, 0.2)   # жёлтый объект
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    return np.clip(img, 0, 1).astype(np.float32)


@pytest.fixture
def scene():
    return make_scene()


@pytest.fixture
def gradient_img():
    """Плавная картинка: по x — яркость, по y — оттенок; для проверок на «фото-подобных» данных."""
    h, w = 64, 128
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    r = np.clip(x * (0.6 + 0.4 * y), 0, 1)
    g = np.clip(x * (1.0 - 0.3 * y), 0, 1)
    b = np.clip(x * (0.5 + 0.5 * (1 - y)), 0, 1)
    return np.stack([r, g, b], axis=-1).astype(np.float32)


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    """QApplication для GUI-тестов (офскрин, отдельная папка настроек)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["COLORMATCHER_HOME"] = str(tmp_path_factory.mktemp("cm_home"))
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
