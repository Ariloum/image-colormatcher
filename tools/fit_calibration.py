"""Подгонка таблиц совместимости с Photoshop по снимкам HALD (T-0034).

Читает calibration/ps/*.png (снятые tools/ps_calibrate.jsx) и пишет
colormatcher/core/filters/ps_calibration.json — табличные части моделей фильтров.
Анализ и найденные законы — docs/CALIBRATION.md.

  * contrast.D        — профиль современного контраста: y = x + (c/100)·D(x)   (из c+100);
  * brightness        — положительная яркость: аналитическая кривая
                        y = kx / (1 + (k^p − 1)·x^p)^(1/p), k = e^(a·b), p(b) по узлам,
                        плюс остаток R_b(x) в узлах (b = 25, 50, 100, 150);
                        отрицательная яркость = обратная функция положительной;
  * gamma_toe         — «носок» гаммы < 1 у чёрного (Color Balance): отношение
                        PS / x^γ в первых отсчётах для γ = 2^-0.5 и 2^-1.

Запуск: python-embed\\python.exe tools\\fit_calibration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from colormatcher.core import imageio  # noqa: E402

PS = ROOT / "calibration" / "ps"
OUT = ROOT / "colormatcher" / "core" / "filters" / "ps_calibration.json"
N = 64
XS = np.arange(N) / (N - 1)
TOE_SAMPLES = 13  # отсчёты 0..12/63 (x < ~0.19)


def channel_curve(name: str, ch: int) -> np.ndarray:
    """Кривая канала ch по HALD (все фильтры PS здесь поканальные — проверено)."""
    a = imageio.load_image(PS / name).rgb.reshape(N, N, N, 3)  # индексы [b, g, r]
    return [a[0, 0, :, 0], a[0, :, 0, 1], a[:, 0, 0, 2]][ch].astype(np.float64)


def brightness_model(x: np.ndarray, k: float, p: float) -> np.ndarray:
    return k * x / np.power(1.0 + (k ** p - 1.0) * np.power(x, p), 1.0 / p)


def fit_brightness() -> dict:
    nodes = [25, 50, 100, 150]
    curves = {b: channel_curve(f"bc__b{b:+d}__c0.png", 0) for b in nodes}
    # общий a: наклон в тенях k = e^(a·b)
    a_vals = [np.log(curves[b][4] / XS[4]) / b for b in nodes]
    a = float(np.mean(a_vals))
    ps, residuals = [], []
    for b in nodes:
        k = np.exp(a * b)
        grid = np.linspace(0.5, 10, 381)
        errs = [np.abs(brightness_model(XS, k, p) - curves[b]).max() for p in grid]
        p = float(grid[int(np.argmin(errs))])
        ps.append(p)
        residuals.append((curves[b] - brightness_model(XS, k, p)).round(6).tolist())
    return {"a": round(a, 6), "nodes": nodes, "p": ps, "residual": residuals}


def fit_contrast() -> dict:
    d = channel_curve("bc__b0__c+100.png", 0) - XS
    return {"D": d.round(6).tolist()}


def fit_gamma_toe() -> dict:
    tables = {}
    for gamma, name in ((2 ** -0.5, "cb__shadows.cr+100__pl-off.png"), (0.5, "cb__midtones.cr+100__pl-off.png")):
        c = channel_curve(name, 0)[:TOE_SAMPLES]
        pure = np.power(XS[:TOE_SAMPLES], gamma)
        ratio = np.ones(TOE_SAMPLES)
        ratio[1:] = c[1:] / pure[1:]
        ratio[0] = ratio[1]
        ratio = np.minimum(ratio, 1.0)
        tables[f"{gamma:.6f}"] = ratio.round(6).tolist()
    return {"x_max": XS[TOE_SAMPLES - 1], "samples": TOE_SAMPLES, "ratio": tables}


def main() -> None:
    data = {
        "source": "Photoshop 25.6, HALD 64³, tools/ps_calibrate.jsx",
        "brightness": fit_brightness(),
        "contrast": fit_contrast(),
        "gamma_toe": fit_gamma_toe(),
    }
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"→ {OUT.relative_to(ROOT)}")
    print(f"brightness: a={data['brightness']['a']}, p={data['brightness']['p']}")


if __name__ == "__main__":
    main()
