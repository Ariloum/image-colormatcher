"""Авто-баланс по меткам масок (М3, T-0063): цвет области кадра — к той же области референса.

Внутри области (небо, песок, кожа) нейтралей нет, поэтому здесь сравнивается не «оттенок серого»,
а РАСПРЕДЕЛЕНИЕ цвета области (docs/MASKS-DESIGN.md, «Что важно не упустить», п. 1):

  * светлота: взвешенные перцентили L* 10 / 50 / 90;
  * оттенок: средние a*, b* отдельно в тёмной и светлой половине области (по её медиане L*) —
    так решатель различает тени и света (Color Balance по трём диапазонам);
  * насыщенность: взвешенная медиана хромы C*.

Решаются те же ползунки, что в общем авто-балансе (Brightness/Contrast, 9 × Color Balance,
Saturation) — это слой метки поверх общих правок; решатель — тот же Левенберг–Марквардт
(`matcher.lm_solve`) с L2 + L1-разреженностью и пределами из MatchSettings.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models.profile import MatchSettings
from ..models.stack import AdjustmentStack
from .colorspace import srgb_to_lab
from .filters import FilterRegistry
from .matcher import CB9, TONE, lm_solve
from .pipeline import render_direct

REGION_PARAMS = TONE + CB9 + ["hs.saturation"]
REGION_PIXELS = 5000        # выборка пикселей области для решателя
MIN_SHARE = 0.003           # метка меньше 0.3% кадра — не подбирается (случайный мазок)
STAT_NAMES = ["L10", "L50", "L90", "a_dark", "b_dark", "a_light", "b_light", "C50"]


@dataclass
class RegionResult:
    stack: AdjustmentStack
    params: dict[str, float]
    before: dict[str, float]       # расхождение с референсом до / после (единицы Lab)
    after: dict[str, float]


def _wquantile(v: np.ndarray, w: np.ndarray, q: float) -> float:
    order = np.argsort(v)
    cw = np.cumsum(w[order])
    return float(v[order][min(len(v) - 1, np.searchsorted(cw, q * cw[-1]))])


def region_sample(rgb: np.ndarray, weights: np.ndarray, n: int = REGION_PIXELS) -> tuple[np.ndarray, np.ndarray]:
    """Пиксели области с весами: (k, 3), (k,). `weights` — вес метки (h, w) 0…1 того же размера."""
    px = np.asarray(rgb, np.float32).reshape(-1, 3)
    w = np.asarray(weights, np.float32).reshape(-1)
    idx = np.flatnonzero(w > 0.05)                       # совсем прозрачный край маски не в счёт
    if idx.size > n:
        idx = idx[np.linspace(0, idx.size - 1, n).astype(int)]   # равномерно по области, детерминированно
    return px[idx], w[idx]


def region_stats(px: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Вектор статистик области (STAT_NAMES)."""
    lab = srgb_to_lab(px)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    med = _wquantile(L, w, 0.5)
    dark, light = L <= med, L > med
    if not light.any():
        light = dark

    def mean(v: np.ndarray, m: np.ndarray) -> float:
        wm = w * m
        return float((v * wm).sum() / max(wm.sum(), 1e-9))

    C = np.hypot(a, b)
    return np.array([_wquantile(L, w, 0.1), med, _wquantile(L, w, 0.9),
                     mean(a, dark), mean(b, dark), mean(a, light), mean(b, light), _wquantile(C, w, 0.5)])


def _deviation(stats: np.ndarray, ref: np.ndarray) -> dict[str, float]:
    d = stats - ref
    return {"dL": float(np.abs(d[:3]).mean()), "dAB": float(np.hypot(d[3:7:2], d[4:7:2]).mean()), "dC": float(d[7])}


def match_region(target_px: np.ndarray, target_w: np.ndarray, ref_stats: np.ndarray,
                 settings: MatchSettings | None = None, base_layer: AdjustmentStack | None = None) -> RegionResult:
    """Подобрать слой метки: `target_px` — пиксели области ПОСЛЕ общих правок кадра."""
    s = settings or MatchSettings()
    keys = REGION_PARAMS
    base = (base_layer or AdjustmentStack()).model_copy(deep=True)
    base.opacity = 100.0
    for k in keys:
        base.set(k, 0)
    specs = [FilterRegistry.resolve_key(k) for k in keys]
    limit = np.array([s.limits.get(k, s.limits.get(k.split(".")[0], np.inf)) for k in keys])
    lo = np.maximum([f.spec(k).min for f, k in specs], -limit)
    hi = np.minimum([f.spec(k).max for f, k in specs], limit)
    # веса невязок: тон и оттенок — наравне (у области, в отличие от кадра, тон — её суть), хрома — как в общем
    wts = np.sqrt(np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, s.sat_weight]))
    reg_base = np.sqrt(s.regularize) / 10.0

    def stack_at(theta: np.ndarray) -> AdjustmentStack:
        st = base.model_copy(deep=True)
        for k, v in zip(keys, theta):
            st.set(k, float(v))
        return st

    def stats_at(theta: np.ndarray) -> np.ndarray:
        return region_stats(render_direct(target_px, stack_at(theta)), target_w)

    def evaluate(theta: np.ndarray, reg: np.ndarray) -> np.ndarray:
        return np.concatenate([wts * (stats_at(theta) - ref_stats), reg * theta])

    x = np.zeros(len(keys))
    before = _deviation(stats_at(x), ref_stats)
    reg = np.full(x.size, reg_base)
    x, _ = lm_solve(evaluate, x, lo, hi, reg, s.max_iter)
    for _ in range(s.sparse_rounds if s.sparsity > 0 else 0):
        reg = np.sqrt(reg_base ** 2 + s.sparsity / (np.abs(x) + 0.5))
        x, _ = lm_solve(evaluate, x, lo, hi, reg, max(8, s.max_iter // 2))
    x = np.round(x)
    final = stack_at(x)
    after = _deviation(stats_at(x), ref_stats)
    final.opacity = s.strength
    return RegionResult(final, {k: float(v) for k, v in zip(keys, x) if v}, before, after)
