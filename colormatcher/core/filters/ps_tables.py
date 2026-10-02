"""Табличные части моделей, совместимых с Photoshop (T-0034).

Таблицы — `ps_calibration.json`, их пишет `tools/fit_calibration.py` по снимкам HALD из PS.
Законы, которые они дополняют, — docs/CALIBRATION.md. Все кривые здесь одномерные
(Photoshop применяет эти фильтры поканально) и строятся плотной таблицей один раз
на значение ползунка (кеш).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

_PATH = Path(__file__).with_name("ps_calibration.json")
_DENSE = np.linspace(0.0, 1.0, 4097)
_SAMPLES = np.linspace(0.0, 1.0, 64)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_PATH.read_text(encoding="utf-8"))


def apply_curve(x: np.ndarray, curve: np.ndarray) -> np.ndarray:
    """Применить плотную 1D-кривую (значения на _DENSE) к массиву [0,1].

    Линейная интерполяция по сетке 4097 точек через индексы — в разы быстрее np.interp.
    """
    n = curve.shape[0] - 1
    c32 = curve.astype(np.float32)
    pos = np.clip(np.asarray(x, dtype=np.float32), 0.0, 1.0) * n
    i = np.minimum(pos.astype(np.int32), n - 1)
    f = pos - i
    lo = c32[i]
    return lo + (c32[i + 1] - lo) * f


def _monotone(y: np.ndarray) -> np.ndarray:
    y = np.clip(y, 0.0, 1.0)
    y = np.maximum.accumulate(y)
    y[0], y[-1] = 0.0, 1.0
    return y


# --- Brightness (современный режим) ---------------------------------------------

def _brightness_positive(b: float) -> np.ndarray:
    d = _data()["brightness"]
    nodes = [0.0] + [float(n) for n in d["nodes"]]
    p_nodes = [d["p"][0]] + d["p"]
    zero = [0.0] * len(_SAMPLES)
    res_nodes = [zero] + d["residual"]

    k = np.exp(d["a"] * b)
    p = float(np.interp(b, nodes, p_nodes))
    hi = int(np.clip(np.searchsorted(nodes, b), 1, len(nodes) - 1))
    t = (b - nodes[hi - 1]) / (nodes[hi] - nodes[hi - 1])
    residual = (1 - t) * np.asarray(res_nodes[hi - 1]) + t * np.asarray(res_nodes[hi])

    x = _DENSE
    y = k * x / np.power(1.0 + (k ** p - 1.0) * np.power(x, p), 1.0 / p)
    return _monotone(y + np.interp(x, _SAMPLES, residual))


@lru_cache(maxsize=256)
def brightness_curve(b: float) -> np.ndarray:
    """Кривая яркости PS. Отрицательная — обратная к положительной (проверено по PS)."""
    if b == 0:
        return _DENSE.copy()
    pos = _brightness_positive(abs(b))
    return pos if b > 0 else _monotone(np.interp(_DENSE, pos, _DENSE))


# --- Contrast (современный режим): y = x + (c/100)·D(x) — линеен по c ------------

@lru_cache(maxsize=256)
def contrast_curve(c: float) -> np.ndarray:
    if c == 0:
        return _DENSE.copy()
    d = np.interp(_DENSE, _SAMPLES, _data()["contrast"]["D"])
    return _monotone(_DENSE + (c / 100.0) * d)


# --- Гамма с «носком» у чёрного (Color Balance) -----------------------------------

@lru_cache(maxsize=1)
def _toe_tables() -> tuple[float, np.ndarray, np.ndarray]:
    t = _data()["gamma_toe"]
    ratios = {float(k): np.asarray(v) for k, v in t["ratio"].items()}
    g707 = min(ratios, key=lambda g: abs(g - 2 ** -0.5))
    g500 = min(ratios, key=lambda g: abs(g - 0.5))
    return float(t["x_max"]), ratios[g707], ratios[g500]


def gamma_with_toe(x: np.ndarray, gamma: float) -> np.ndarray:
    """x^γ; при γ < 1 PS смягчает крутизну у нуля — поправка по таблице.

    u = log2(1/γ): до 0.25 поправки нет, 0.25→0.5 — к таблице γ=2^-0.5,
    0.5→1 — к таблице γ=0.5, дальше — таблица γ=0.5.
    """
    y = np.power(x, gamma)
    u = np.log2(1.0 / gamma) if gamma > 0 else 1.0
    if u <= 0.25:
        return y
    x_max, t707, t500 = _toe_tables()
    if u <= 0.5:
        w = (u - 0.25) / 0.25
        ratio = (1 - w) + w * t707
    else:
        w = min(1.0, (u - 0.5) / 0.5)
        ratio = (1 - w) * t707 + w * t500
    xs = np.linspace(0.0, x_max, len(ratio))
    r = np.interp(x, xs, ratio, right=1.0)
    return (y * r).astype(np.float32)
