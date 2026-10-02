"""Brightness/Contrast — совместимый с Photoshop.

По снимкам HALD из PS (docs/CALIBRATION.md): фильтр поканальный и НЕ адаптивный
(не зависит от содержимого кадра); контраст применяется после яркости.

Современный режим:
  * яркость b ∈ [-150, 150]: в тенях — умножение на k = e^(0.0063·b), к светам кривая
    плавно выходит на 1; отрицательная яркость — обратная функция положительной.
    Форма — аналитика + остаток по узлам из ps_calibration.json;
  * контраст c ∈ [-50, 100]: y = x + (c/100)·D(x), профиль D снят с PS (линеен по c).
Legacy: яркость — сдвиг на b/255; контраст — растяжение вокруг 127/255 с клиппингом
(наклон 1 + c/100 при c < 0, 1 / (1 − c/100) при c ≥ 0).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .base import Filter, ParamSpec, register
from .ps_tables import apply_curve, brightness_curve, contrast_curve

_LEGACY_PIVOT = 127.0 / 255.0


@register
class BrightnessContrast(Filter):
    type = "brightness_contrast"
    group = "bc"
    label = "Brightness/Contrast"
    params = (
        ParamSpec("brightness", "Яркость", -150, 150, 0, gradient=("#202020", "#f0f0f0")),
        ParamSpec("contrast", "Контраст", -50, 100, 0, gradient=("#808080", "#000000")),
        ParamSpec("legacy", "Legacy (с клиппингом)", kind="bool", default=False),
    )

    @classmethod
    def apply(cls, rgb: np.ndarray, params: dict[str, Any]) -> np.ndarray:
        p = cls.normalize(params)
        x = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
        b, c = float(p["brightness"]), float(p["contrast"])

        if p["legacy"]:
            x = x + b / 255.0
            slope = 1.0 + c / 100.0 if c < 0 else 1.0 / max(1.0 - c / 100.0, 1e-3)
            x = (x - _LEGACY_PIVOT) * slope + _LEGACY_PIVOT
            return np.clip(x, 0.0, 1.0).astype(np.float32)

        if b:
            x = apply_curve(x, brightness_curve(b))
        if c:
            x = apply_curve(x, contrast_curve(c))
        return x.astype(np.float32)
