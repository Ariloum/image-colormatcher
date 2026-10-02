"""3D LUT: запекание поточечной функции цвета и трилинейное применение.

LUT хранится как массив (N, N, N, 3) float32, индексы [r, g, b].
Применение идёт порциями, чтобы полноразмерные кадры не раздували память.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

ColorFn = Callable[[np.ndarray], np.ndarray]

PREVIEW_SIZE = 33
EXPORT_SIZE = 65
_CHUNK = 1 << 20  # пикселей за проход


def identity_lut(size: int) -> np.ndarray:
    g = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    return np.stack([r, gg, b], axis=-1)


def bake(fn: ColorFn, size: int = PREVIEW_SIZE) -> np.ndarray:
    grid = identity_lut(size).reshape(-1, 3)
    out = np.clip(fn(grid), 0.0, 1.0).astype(np.float32)
    return out.reshape(size, size, size, 3)


try:  # быстрый путь: параллельное ядро numba (~в 50–100 раз быстрее NumPy-варианта)
    import numba

    @numba.njit(parallel=True, fastmath=True, cache=True)
    def _apply_nb(src: np.ndarray, lut: np.ndarray, dst: np.ndarray) -> None:  # pragma: no cover - JIT
        n = lut.shape[0]
        top = n - 1
        for i in numba.prange(src.shape[0]):
            r = min(max(src[i, 0], 0.0), 1.0) * top
            g = min(max(src[i, 1], 0.0), 1.0) * top
            b = min(max(src[i, 2], 0.0), 1.0) * top
            ir, ig, ib = min(int(r), n - 2), min(int(g), n - 2), min(int(b), n - 2)
            fr, fg, fb = r - ir, g - ig, b - ib
            for c in range(3):
                c000 = lut[ir, ig, ib, c]
                c001 = lut[ir, ig, ib + 1, c]
                c010 = lut[ir, ig + 1, ib, c]
                c011 = lut[ir, ig + 1, ib + 1, c]
                c100 = lut[ir + 1, ig, ib, c]
                c101 = lut[ir + 1, ig, ib + 1, c]
                c110 = lut[ir + 1, ig + 1, ib, c]
                c111 = lut[ir + 1, ig + 1, ib + 1, c]
                c00 = c000 + (c001 - c000) * fb
                c01 = c010 + (c011 - c010) * fb
                c10 = c100 + (c101 - c100) * fb
                c11 = c110 + (c111 - c110) * fb
                c0 = c00 + (c01 - c00) * fg
                c1 = c10 + (c11 - c10) * fg
                dst[i, c] = c0 + (c1 - c0) * fr

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover
    HAVE_NUMBA = False


def apply(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Применить LUT к изображению (..., 3) float32 [0,1] трилинейно."""
    if HAVE_NUMBA:
        src = np.ascontiguousarray(img, dtype=np.float32).reshape(-1, 3)
        dst = np.empty_like(src)
        _apply_nb(src, np.ascontiguousarray(lut, dtype=np.float32), dst)
        return dst.reshape(np.shape(img))
    return apply_numpy(img, lut)


def apply_numpy(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Запасной путь без numba (медленнее)."""
    n = lut.shape[0]
    flat_lut = lut.reshape(-1, 3)
    src = np.asarray(img, dtype=np.float32).reshape(-1, 3)
    dst = np.empty_like(src)
    s_r, s_g = n * n, n

    for start in range(0, src.shape[0], _CHUNK):
        px = np.clip(src[start:start + _CHUNK], 0.0, 1.0) * (n - 1)
        i0 = np.minimum(px.astype(np.int32), n - 2)
        f = px - i0
        base = i0[:, 0] * s_r + i0[:, 1] * s_g + i0[:, 2]
        fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]

        def lerp_b(idx: np.ndarray) -> np.ndarray:
            lo = flat_lut[idx]
            return lo + (flat_lut[idx + 1] - lo) * fb

        c00 = lerp_b(base)
        c01 = lerp_b(base + s_g)
        c10 = lerp_b(base + s_r)
        c11 = lerp_b(base + s_r + s_g)
        c0 = c00 + (c01 - c00) * fg
        c1 = c10 + (c11 - c10) * fg
        dst[start:start + _CHUNK] = c0 + (c1 - c0) * fr

    return dst.reshape(np.shape(img))
