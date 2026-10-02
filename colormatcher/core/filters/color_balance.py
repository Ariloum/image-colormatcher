"""Color Balance — совместимый с Photoshop: тени/середины/света × cyan-red, magenta-green, yellow-blue.

По снимкам HALD из PS (docs/CALIBRATION.md) Color Balance — это «Уровни» по каналам:
у каждого канала точка чёрного B, точка белого W и гамма γ; выход канала зависит только
от его же входа. Ползунок v ∈ [-100, 100] на оси канала ch (cr → R, mg → G, yb → B),
a = |v| / 255:

  Preserve Luminosity ВКЛ:
    тени     v > 0: B других двух каналов += a;   v < 0: B своего += a
    света    v > 0: W своего −= a;                v < 0: W других двух −= a
    середины γ своего ×= 2^(−v/200), γ других двух ×= 2^(+v/200)
  Preserve Luminosity ВЫКЛ (только свой канал):
    тени     v < 0: B своего += a;   света v > 0: W своего −= a
    и всегда γ своего ×= 2^(−v/200) (для середин — дважды: 2^(−v/100))

Ползунки складываются: B и W суммируются, гаммы перемножаются.
Кривая канала: y = ((x − B) / (W − B))^γ с «носком» у чёрного при γ < 1.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .base import Filter, ParamSpec, register
from .ps_tables import gamma_with_toe

RANGES = ("shadows", "midtones", "highlights")
AXES = (
    ("cr", "Cyan ↔ Red", ("#00b7c7", "#e0443a")),
    ("mg", "Magenta ↔ Green", ("#c23fb5", "#3fb54a")),
    ("yb", "Yellow ↔ Blue", ("#e0c02f", "#3a62e0")),
)
_RANGE_LABELS = {"shadows": "Тени", "midtones": "Средние тона", "highlights": "Света"}


def channel_levels(p: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """9 ползунков + флаг -> (B, W, γ) по каналам R, G, B."""
    black, white, gamma = np.zeros(3), np.ones(3), np.ones(3)
    pl = bool(p["preserve_luminosity"])
    for rng in RANGES:
        for ch, (axis, _, _) in enumerate(AXES):
            v = float(p[f"{rng}.{axis}"])
            if not v:
                continue
            others = [c for c in range(3) if c != ch]
            a = abs(v) / 255.0
            own_gamma = 2.0 ** (-v / 200.0)
            if pl:
                if rng == "shadows":
                    black[others if v > 0 else ch] += a
                elif rng == "highlights":
                    white[ch if v > 0 else others] -= a
                else:
                    gamma[ch] *= own_gamma
                    gamma[others] /= own_gamma
            else:
                if rng == "shadows" and v < 0:
                    black[ch] += a
                elif rng == "highlights" and v > 0:
                    white[ch] -= a
                gamma[ch] *= own_gamma * (own_gamma if rng == "midtones" else 1.0)
    return black, white, gamma


@register
class ColorBalance(Filter):
    type = "color_balance"
    group = "cb"
    label = "Color Balance"
    params = tuple(
        ParamSpec(
            key=f"{rng}.{axis}",
            label=f"{_RANGE_LABELS[rng]}: {label}",
            section=rng,
            gradient=grad,
        )
        for rng in RANGES
        for axis, label, grad in AXES
    ) + (ParamSpec(key="preserve_luminosity", label="Сохранять яркость", kind="bool", default=True),)

    @classmethod
    def apply(cls, rgb: np.ndarray, params: dict[str, Any]) -> np.ndarray:
        black, white, gamma = channel_levels(cls.normalize(params))
        rgb = np.asarray(rgb, dtype=np.float32)
        out = np.empty_like(rgb)
        for ch in range(3):
            span = max(white[ch] - black[ch], 1e-4)
            x = np.clip((rgb[..., ch] - black[ch]) / span, 0.0, 1.0)
            out[..., ch] = gamma_with_toe(x, gamma[ch]) if gamma[ch] != 1.0 else x
        return out
