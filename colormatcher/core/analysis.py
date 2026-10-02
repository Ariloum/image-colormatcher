"""«Пипетка»: цветовой профиль изображения (T-0012) и набор референсов (T-0013).

Устройство (docs/ALGORITHM.md §2):
  1. Выборка — уменьшенная копия (все её пиксели).
  2. Почти нейтральные пиксели: хрома, отсчитанная ОТ каста кадра (серые под синим
     светом синеваты — они всё равно «серые»), вес exp(−(C'/C0)²). Пересветы и провалы
     исключаются.
  3. Тоновые диапазоны — мягкие маски по L* (тени / середины / света).
  4. Голосование плиток: кадр делится на сетку, каждая плитка даёт свой каст; плитки,
     далёкие от медианы, получают меньший вес — большой цветной объект не перетянет.
  5. Тон — перцентили L*, насыщенность — перцентили хромы Lab.

Выборка (какие пиксели и с какими весами) фиксируется по исходному кадру —
`PixelSample`. Решатель меняет только цвета этих пикселей (`measure`), поэтому
целевая функция гладкая.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from ..models.profile import BANDS, BandStats, ColorProfile, ProfileOffset
from ..models.stack import AdjustmentStack
from .colorspace import srgb_to_lab_fast as srgb_to_lab
from .imageio import make_proxy
from .pipeline import render_direct

NEUTRAL_C0 = 7.0      # «ширина» нейтральности в единицах хромы Lab (от каста диапазона)
CHROMA_ABS = 30.0     # мягкий потолок абсолютной хромы: сильнее каст редко бывает, это уже цвет объекта
CLIP_HI = 0.985       # канал выше — пересвет
L_MIN = 3.0           # L* ниже — провал
GRID = 8              # сетка голосования плиток
_A, _B = 0.25, 0.333  # форма тоновых масок (как у GIMP)


def band_weights(L: np.ndarray) -> np.ndarray:
    """Мягкие маски теней/середин/светов по L* (0..100) -> (n, 3)."""
    v = np.clip(L / 100.0, 0.0, 1.0)
    sh = np.clip((v - _B) / -_A + 0.5, 0, 1)
    mid = np.clip((v - _B) / _A + 0.5, 0, 1) * np.clip((v + _B - 1) / -_A + 0.5, 0, 1)
    hi = np.clip((v + _B - 1) / _A + 0.5, 0, 1)
    return np.stack([sh, mid, hi], axis=-1).astype(np.float32)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cw = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cw, cw[-1] / 2)])


@dataclass
class PixelSample:
    """Зафиксированная выборка пикселей кадра и их веса."""

    rgb: np.ndarray        # (n, 3) исходные цвета выборки
    w_band: np.ndarray     # (n, 3) вес пикселя в тенях/серединах/светах (нейтральность × тон × плитка)
    w_valid: np.ndarray    # (n,) 1 для непересвеченных — для тона и насыщенности
    neutral_share: float
    confidence: float
    shape: tuple[int, int] # (h, w) выборки — для якорей по координатам


SAMPLE_PIXELS = 12000


def sample_image(rgb: np.ndarray, pixels: int = SAMPLE_PIXELS, grid: int = GRID,
                 exclude: np.ndarray | None = None) -> PixelSample:
    """Выборка: кадр, уменьшенный примерно до `pixels` пикселей (все они участвуют).

    `exclude` — карта 0…1 (любого размера) областей вне статистики: метки «не учитывать» и
    «не трогать» (T-0063); вклад пикселя в пипетку, тон и насыщенность × (1 − exclude)."""
    rgb = np.asarray(rgb, dtype=np.float32)
    h0, w0 = rgb.shape[:2]
    side = int(max(h0, w0) * min(1.0, np.sqrt(pixels / (h0 * w0))))
    proxy = make_proxy(rgb, max(side, 16))
    h, w = proxy.shape[:2]
    px = proxy.reshape(-1, 3)
    lab = srgb_to_lab(px)
    L, ab = lab[:, 0], lab[:, 1:]

    valid = (px.max(axis=1) < CLIP_HI) & (L > L_MIN)
    keep = np.ones(h * w, np.float32)
    if exclude is not None:
        import cv2

        ex = cv2.resize(np.asarray(exclude, np.float32), (w, h), interpolation=cv2.INTER_AREA).reshape(-1)
        keep = np.clip(1.0 - ex, 0.0, 1.0)
        valid &= keep > 0.5
    w_valid = valid.astype(np.float32)

    # нейтральность — относительно каста СВОЕГО тонового диапазона (при раздельном
    # тонировании тени и света «уезжают» в разные стороны): начальный центр — медиана
    # 10% наименее хроматичных пикселей диапазона, затем две итерации уточнения.
    # Мягкий потолок абсолютной хромы не даёт большому ровному цветному фону (небо,
    # стена) выдать себя за «нейтраль».
    bw = band_weights(L)
    chroma = np.linalg.norm(ab, axis=1)
    prior = np.exp(-(chroma / CHROMA_ABS) ** 2) * w_valid * keep
    cast_b = np.zeros((3, 2), np.float32)
    if valid.any():
        fallback = np.median(ab[valid & (chroma <= np.percentile(chroma[valid], 10))], axis=0)
        for b in range(3):
            core = valid & (bw[:, b] > 0.5)
            if core.sum() >= 20:
                low = core & (chroma <= np.percentile(chroma[core], 10))
                cast_b[b] = np.median(ab[low], axis=0)
            else:
                cast_b[b] = fallback
    for _ in range(2):
        rel = np.linalg.norm(ab[:, None, :] - cast_b[None], axis=2)          # (n, 3)
        wn_b = np.exp(-(rel / NEUTRAL_C0) ** 2) * prior[:, None]
        for b in range(3):
            wb = bw[:, b] * wn_b[:, b]
            if wb.sum() > 1e-6:
                cast_b[b] = (ab * wb[:, None]).sum(0) / wb.sum()
    bw_sum = np.maximum(bw.sum(1), 1e-6)
    w_n = (bw * wn_b).sum(1) / bw_sum                                       # нейтральность пикселя
    local_cast = (bw @ cast_b) / bw_sum[:, None]                            # каст его тонов

    # голосование плиток: отклонение каста плитки (относительно кастов тонов) от медианы плиток
    ty = np.minimum(np.arange(h) * grid // h, grid - 1)
    tx = np.minimum(np.arange(w) * grid // w, grid - 1)
    tile = (ty[:, None] * grid + tx[None, :]).reshape(-1)
    dev_ab = ab - local_cast
    mass = np.bincount(tile, w_n, minlength=grid * grid)
    tile_ab = np.stack([np.bincount(tile, w_n * dev_ab[:, k], minlength=grid * grid) for k in (0, 1)], 1)
    ok = mass > max(1e-6, 0.002 * w_n.sum())
    tile_w = np.ones(grid * grid, np.float32)
    agreement = 1.0
    if ok.sum() >= 3:
        tile_ab[ok] /= mass[ok, None]
        med = np.array([_weighted_median(tile_ab[ok, k], mass[ok]) for k in (0, 1)])
        dev = np.linalg.norm(tile_ab - med, axis=1)
        mad = _weighted_median(dev[ok], mass[ok])
        tile_w = 1.0 / (1.0 + (dev / (2.5 * mad + 1.0)) ** 2)
        agreement = float(np.exp(-mad / 10.0))

    w_band = bw * wn_b * tile_w[tile][:, None]
    neutral_share = float(w_n.sum() / max(1.0, w_valid.sum()))
    confidence = float(np.clip(neutral_share / 0.05, 0, 1) * agreement)
    return PixelSample(px, w_band.astype(np.float32), w_valid, neutral_share, confidence, (h, w))


def measure(sample: PixelSample, rgb: np.ndarray | None = None) -> ColorProfile:
    """Профиль выборки; `rgb` — те же пиксели после коррекции (по умолчанию — исходные)."""
    px = sample.rgb if rgb is None else rgb
    lab = srgb_to_lab(px)
    total = sample.w_band.sum()
    bands = {}
    for i, name in enumerate(BANDS):
        w = sample.w_band[:, i]
        m = float(w.sum())
        if m > 1e-6:
            mean = (lab * w[:, None]).sum(0) / m
            bands[name] = BandStats(L=float(mean[0]), a=float(mean[1]), b=float(mean[2]), mass=m / max(total, 1e-6))
        else:
            bands[name] = BandStats()
    valid = sample.w_valid > 0
    Lv = lab[valid, 0] if valid.any() else lab[:, 0]
    Cv = np.linalg.norm(lab[valid, 1:] if valid.any() else lab[:, 1:], axis=1)
    p05, p50, p95 = np.percentile(Lv, [5, 50, 95])
    c50, c90 = np.percentile(Cv, [50, 90])
    lit = Lv > 12.0  # в глубоких тенях насыщенность шумит
    Sv = Cv[lit] / np.sqrt(Cv[lit] ** 2 + Lv[lit] ** 2) if lit.any() else np.zeros(1)
    s50, s90 = np.percentile(Sv, [50, 90])
    return ColorProfile(
        bands=bands, L_p05=float(p05), L_p50=float(p50), L_p95=float(p95),
        C_p50=float(c50), C_p90=float(c90), S_p50=float(s50), S_p90=float(s90),
        neutral_share=sample.neutral_share, confidence=sample.confidence,
    )


def profile(rgb: np.ndarray, stack: AdjustmentStack | None = None, pixels: int = SAMPLE_PIXELS,
            exclude: np.ndarray | None = None) -> ColorProfile:
    """Профиль изображения; `stack` — его собственная коррекция (снимается ПОСЛЕ неё);
    `exclude` — области вне статистики (см. sample_image)."""
    sample = sample_image(rgb, pixels, exclude=exclude)
    if stack is None or stack.is_identity():
        return measure(sample)
    return measure(sample, render_direct(sample.rgb, stack))


def combine(profiles: Sequence[ColorProfile], weights: Iterable[float] | None = None,
            mode: str = "mean") -> ColorProfile:
    """Усреднение профилей референсов (T-0013): mean — взвешенное, median — медиана."""
    ws = np.asarray(list(weights) if weights is not None else [1.0] * len(profiles), dtype=np.float64)
    if not profiles:
        raise ValueError("нет профилей для усреднения")

    def agg(values: list[float], extra: np.ndarray | None = None) -> float:
        v = np.asarray(values, dtype=np.float64)
        w = ws * (extra if extra is not None else 1.0)
        if w.sum() <= 0:
            w = np.ones_like(v)
        return _weighted_median(v, w) if mode == "median" else float((v * w).sum() / w.sum())

    bands = {}
    for name in BANDS:
        mass = np.asarray([p.bands[name].mass * max(p.confidence, 0.05) for p in profiles])
        bands[name] = BandStats(
            L=agg([p.bands[name].L for p in profiles], mass),
            a=agg([p.bands[name].a for p in profiles], mass),
            b=agg([p.bands[name].b for p in profiles], mass),
            mass=float(np.mean([p.bands[name].mass for p in profiles])),
        )
    fields = ("L_p05", "L_p50", "L_p95", "C_p50", "C_p90", "S_p50", "S_p90", "neutral_share", "confidence")
    return ColorProfile(bands=bands, **{f: agg([getattr(p, f) for p in profiles]) for f in fields})


def apply_offset(prof: ColorProfile, off: ProfileOffset) -> ColorProfile:
    """Ручное смещение целевого профиля (T-0017)."""
    out = prof.model_copy(deep=True)
    for band in out.bands.values():
        band.L += off.L
        band.a += off.a
        band.b += off.b
    out.L_p05 += off.L
    out.L_p50 += off.L
    out.L_p95 += off.L
    out.C_p50 *= off.chroma
    out.C_p90 *= off.chroma
    out.S_p50 = min(0.99, out.S_p50 * off.chroma)
    out.S_p90 = min(0.99, out.S_p90 * off.chroma)
    return out


def region_pixels(rgb: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    """Пиксели прямоугольника (относительные координаты) -> (n, 3)."""
    h, w = rgb.shape[:2]
    x, y, rw, rh = rect
    x0, y0 = int(np.clip(x * w, 0, w - 1)), int(np.clip(y * h, 0, h - 1))
    x1, y1 = int(np.clip(np.ceil((x + rw) * w), x0 + 1, w)), int(np.clip(np.ceil((y + rh) * h), y0 + 1, h))
    return np.asarray(rgb[y0:y1, x0:x1], dtype=np.float32).reshape(-1, 3)


def robust_lab(px: np.ndarray) -> np.ndarray:
    """Цвет области: медиана Lab (устойчива к бликам и шуму)."""
    return np.median(srgb_to_lab(px), axis=0)
