"""Hue/Saturation (Master) — совместимый с Photoshop.

По снимкам HALD из PS (docs/CALIBRATION.md), порядок: светлота, затем тон и насыщенность в HSL.
* lightness [-100..100]: < 0 — rgb·(1+v) (к чёрному), > 0 — rgb + (1−rgb)·v (к белому);
* hue [-180..180] — поворот тона HSL;
* saturation [-100..100]: < 0 — S·(1+v) (−100 = серое), > 0 — S / (1 − v) с обрезкой до 1
  (+100 доводит любой не серый цвет до полной насыщенности); HSL-светлота сохраняется.
Диапазоны по цветам (Reds/Yellows/…) и Colorize — позже.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..colorspace import hsl_to_rgb, rgb_to_hsl
from .base import Filter, ParamSpec, register


@register
class HueSaturation(Filter):
    type = "hue_saturation"
    group = "hs"
    label = "Hue/Saturation"
    params = (
        ParamSpec("hue", "Тон", -180, 180, 0, gradient=("#00b7c7", "#e0443a")),
        ParamSpec("saturation", "Насыщенность", -100, 100, 0, gradient=("#808080", "#e0443a")),
        ParamSpec("lightness", "Светлота", -100, 100, 0, gradient=("#000000", "#ffffff")),
    )

    @classmethod
    def apply(cls, rgb: np.ndarray, params: dict[str, Any]) -> np.ndarray:
        p = cls.normalize(params)
        rgb = np.asarray(rgb, dtype=np.float32)
        hue, sat, light = p["hue"] / 360.0, p["saturation"] / 100.0, p["lightness"] / 100.0

        if light < 0:
            rgb = rgb * (1.0 + light)
        elif light > 0:
            rgb = rgb + (1.0 - rgb) * light

        if hue or sat:
            h, s, l = rgb_to_hsl(rgb)
            h = (h + hue) % 1.0
            if sat < 0:
                s = s * (1.0 + sat)
            elif sat >= 1.0:
                s = np.where(s > 0, 1.0, 0.0)
            elif sat > 0:
                s = s / (1.0 - sat)
            rgb = hsl_to_rgb(h, np.clip(s, 0.0, 1.0), l)
        return np.clip(rgb, 0.0, 1.0).astype(np.float32)
