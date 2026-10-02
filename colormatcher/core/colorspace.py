"""Цветовые пространства: sRGB <-> linear, sRGB -> CIE Lab (D65), RGB <-> HSL.

Все функции векторные: принимают массивы формы (..., 3) float32 в [0, 1].
"""

from __future__ import annotations

import numpy as np

# sRGB (D65) -> XYZ
_RGB2XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float32,
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.asarray(c, dtype=np.float32), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055).astype(np.float32)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] -> Lab (L 0..100, a/b примерно -128..127)."""
    xyz = srgb_to_linear(rgb) @ _RGB2XYZ.T / _WHITE_D65
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


_LIN_LUT = srgb_to_linear(np.linspace(0.0, 1.0, 4097, dtype=np.float32))


def srgb_to_lab_fast(rgb: np.ndarray) -> np.ndarray:
    """То же, что srgb_to_lab, но линеаризация — по таблице 4097 точек (для анализа/решателя).

    Погрешность ≤ 0.01 ΔE — ниже порога различимости, а скорость в разы выше.
    """
    idx = np.rint(np.clip(rgb, 0.0, 1.0) * 4096).astype(np.int32)
    xyz = _LIN_LUT[idx] @ _RGB2XYZ.T / _WHITE_D65
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1
    ).astype(np.float32)


def delta_e76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.asarray(lab1) - np.asarray(lab2), axis=-1)


def rgb_to_hsl(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB -> (h, s, l), все компоненты в [0, 1] (h — доля круга)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    l = (mx + mn) * 0.5
    d = mx - mn
    denom = np.where(l > 0.5, 2.0 - mx - mn, mx + mn)
    s = np.where(d > 1e-7, d / np.maximum(denom, 1e-7), 0.0)
    dd = np.maximum(d, 1e-7)
    h = np.where(
        mx == r,
        ((g - b) / dd) % 6.0,
        np.where(mx == g, (b - r) / dd + 2.0, (r - g) / dd + 4.0),
    )
    h = np.where(d > 1e-7, h / 6.0, 0.0)
    return h.astype(np.float32), s.astype(np.float32), l.astype(np.float32)


def hsl_to_rgb(h: np.ndarray, s: np.ndarray, l: np.ndarray) -> np.ndarray:
    """(h, s, l) в [0, 1] -> RGB [0, 1]."""
    q = np.where(l < 0.5, l * (1.0 + s), l + s - l * s)
    p = 2.0 * l - q

    def channel(t: np.ndarray) -> np.ndarray:
        t = t % 1.0
        return np.where(
            t < 1 / 6,
            p + (q - p) * 6.0 * t,
            np.where(t < 0.5, q, np.where(t < 2 / 3, p + (q - p) * (2 / 3 - t) * 6.0, p)),
        )

    rgb = np.stack([channel(h + 1 / 3), channel(h), channel(h - 1 / 3)], axis=-1)
    return rgb.astype(np.float32)


def hsl_lightness(rgb: np.ndarray) -> np.ndarray:
    """HSL-светлота (max+min)/2 — ею пользуются Color Balance и preserve luminosity."""
    return ((rgb.max(axis=-1) + rgb.min(axis=-1)) * 0.5).astype(np.float32)
