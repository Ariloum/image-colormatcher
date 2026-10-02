"""Авто-баланс: подбор ползунков стека так, чтобы профиль фото совпал с референсным (T-0014).

Решаем через сам конвейер (docs/ALGORITHM.md §3): параметры — обычные ползунки
(Brightness/Contrast, 9 × Color Balance, Hue/Sat), поэтому результат можно докрутить
руками и перенести в Photoshop. Минимизация — Левенберг–Марквардт с численным
якобианом на фиксированной выборке пикселей (`analysis.PixelSample`).

Невязки (единицы Lab):
  * каст по тонам: (a, b) нейтралей в тенях/серединах/светах — вес α_band × доверие;
  * тон (если решаем яркость/контраст): L* p05 / p50 / p95;
  * насыщенность (если решаем saturation): хрома p50 / p90;
  * якоря (пары пипеток): Lab области цели против области референса;
  * регуляризация: ползунок/10 × √μ — не «перекручивать».
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..models.profile import BANDS, ColorProfile, MatchSettings
from ..models.stack import AdjustmentStack
from .analysis import PixelSample, measure, region_pixels, robust_lab, sample_image
from .filters import FilterRegistry
from .filters.color_balance import AXES, RANGES
from .pipeline import render_direct

CB9 = [f"cb.{r}.{a}" for r in RANGES for a, _, _ in AXES]
TONE = ["bc.brightness", "bc.contrast"]
MODE_PARAMS: dict[str, list[str]] = {
    "balance": CB9,
    "balance+sat": CB9 + ["hs.saturation"],
    "tone+balance+sat": TONE + CB9 + ["hs.saturation"],
    "full": TONE + CB9 + ["hs.saturation", "hs.hue"],
}
_STEP = 2.0  # шаг численной производной, единиц ползунка


@dataclass
class Anchor:
    """Якорь для решателя: пиксели области цели и Lab той же области на референсе."""

    target_px: np.ndarray
    ref_lab: np.ndarray
    weight: float = 1.0
    label: str = ""


@dataclass
class MatchResult:
    stack: AdjustmentStack
    params: dict[str, float]
    before: ColorProfile
    after: ColorProfile
    target: ColorProfile
    metrics_before: dict[str, float]
    metrics_after: dict[str, float]
    iterations: int
    seconds: float
    anchors_before: list[float] = field(default_factory=list)
    anchors_after: list[float] = field(default_factory=list)


def params_for(settings: MatchSettings) -> list[str]:
    keys = settings.touch or MODE_PARAMS[settings.mode]
    for k in keys:
        FilterRegistry.resolve_key(k)  # проверка ключей
    return list(keys)


def metrics(p: ColorProfile, ref: ColorProfile) -> dict[str, float]:
    """Понятные человеку расхождения: оттенок нейтралей, тон, насыщенность."""
    w = np.array([min(p.bands[b].mass, ref.bands[b].mass) for b in BANDS]) + 1e-9
    cast = np.array([np.hypot(p.bands[b].a - ref.bands[b].a, p.bands[b].b - ref.bands[b].b) for b in BANDS])
    return {
        "cast_dE": float((cast * w).sum() / w.sum()),
        "dL50": p.L_p50 - ref.L_p50,
        "dRange": (p.L_p95 - p.L_p05) - (ref.L_p95 - ref.L_p05),
        "dS50": 100 * (p.S_p50 - ref.S_p50),
    }


def _residuals(p: ColorProfile, ref: ColorProfile, s: MatchSettings, keys: list[str]) -> list[float]:
    """Все критерии решаются совместно: поэтапная схема (тон → баланс → насыщенность)
    проверена и дала худшее восстановление (docs/QUESTIONS.md, 2026-09-29)."""
    r: list[float] = []
    conf = max(0.2, float(np.sqrt(max(p.confidence, 0) * max(ref.confidence, 0))))
    for name, alpha in zip(BANDS, s.band_weights):
        if p.bands[name].mass > 0.01 and ref.bands[name].mass > 0.01:
            # диапазону с малым числом нейтралей доверяем меньше
            support = min(1.0, p.bands[name].mass / 0.1) * min(1.0, ref.bands[name].mass / 0.1)
            k = np.sqrt(alpha * support) * conf
            r += [k * (p.bands[name].a - ref.bands[name].a), k * (p.bands[name].b - ref.bands[name].b)]
    if any(k in keys for k in TONE + ["hs.lightness"]):
        t = np.sqrt(s.tone_weight)
        r += [t * (p.L_p05 - ref.L_p05), t * (p.L_p50 - ref.L_p50), t * (p.L_p95 - ref.L_p95)]
    if "hs.saturation" in keys:
        c = np.sqrt(s.sat_weight)
        r += [c * 100 * (p.S_p50 - ref.S_p50), c * 100 * (p.S_p90 - ref.S_p90)]
    return r


def match(
    target_rgb: np.ndarray,
    ref: ColorProfile,
    settings: MatchSettings | None = None,
    base_stack: AdjustmentStack | None = None,
    anchors: Sequence[Anchor] = (),
    sample: PixelSample | None = None,
) -> MatchResult:
    """Подобрать ползунки для `target_rgb`, чтобы его профиль совпал с `ref`."""
    s = settings or MatchSettings()
    t0 = time.perf_counter()
    keys = params_for(s)
    sample = sample or sample_image(target_rgb, s.sample_pixels)

    base = (base_stack or AdjustmentStack()).model_copy(deep=True)
    base.opacity = 100.0
    if s.reset:
        for k in keys:
            base.set(k, 0)
    specs = [FilterRegistry.resolve_key(k) for k in keys]
    limit = np.array([s.limits.get(k, s.limits.get(k.split(".")[0], np.inf)) for k in keys])
    lo = np.maximum([f.spec(k).min for f, k in specs], -limit)
    hi = np.minimum([f.spec(k).max for f, k in specs], limit)
    x = np.clip([float(base.get(k)) for k in keys], lo, hi)
    reg_base = np.sqrt(s.regularize) / 10.0

    def stack_at(theta: np.ndarray) -> AdjustmentStack:
        st = base.model_copy(deep=True)
        for k, v in zip(keys, theta):
            st.set(k, float(v))
        return st

    def evaluate(theta: np.ndarray, reg: np.ndarray) -> np.ndarray:
        st = stack_at(theta)
        prof = measure(sample, render_direct(sample.rgb, st))
        r = _residuals(prof, ref, s, keys)
        for a in anchors:
            lab = robust_lab(render_direct(a.target_px, st))
            r += list(np.sqrt(s.anchor_weight * a.weight) * (lab - a.ref_lab))
        r += list(reg * theta)
        return np.asarray(r, dtype=np.float64)

    def solve(x: np.ndarray, reg: np.ndarray, max_iter: int) -> tuple[np.ndarray, int]:
        return lm_solve(evaluate, x, lo, hi, reg, max_iter)

    before = measure(sample, render_direct(sample.rgb, stack_at(x)))
    anchors_before = [float(np.linalg.norm(robust_lab(render_direct(a.target_px, stack_at(x))) - a.ref_lab)) for a in anchors]

    # 1) обычный L2-штраф; 2) перевзвешивание ≈ L1: мелкие ползунки уходят в ноль,
    #    решение собирается на немногих — его проще понять и докрутить руками
    reg = np.full(x.size, reg_base)
    x, it = solve(x, reg, s.max_iter)
    for _ in range(s.sparse_rounds if s.sparsity > 0 else 0):
        # IRLS: √(λ₁/(|θ|+ε))·θ даёт штраф ≈ λ₁·|θ| (λ₁ — «цена» пункта ползунка)
        reg = np.sqrt(reg_base ** 2 + s.sparsity / (np.abs(x) + 0.5))
        x, n = solve(x, reg, max(8, s.max_iter // 2))
        it += n
    x = np.round(x)  # целые — как в Photoshop (дробные значения там не ввести)
    final = stack_at(x)
    after = measure(sample, render_direct(sample.rgb, final))
    anchors_after = [float(np.linalg.norm(robust_lab(render_direct(a.target_px, final)) - a.ref_lab)) for a in anchors]
    final.opacity = s.strength
    return MatchResult(
        stack=final,
        params={k: float(v) for k, v in zip(keys, x)},
        before=before, after=after, target=ref,
        metrics_before=metrics(before, ref), metrics_after=metrics(after, ref),
        iterations=it, seconds=time.perf_counter() - t0,
        anchors_before=anchors_before, anchors_after=anchors_after,
    )


def lm_solve(evaluate, x: np.ndarray, lo: np.ndarray, hi: np.ndarray, reg: np.ndarray,
             max_iter: int) -> tuple[np.ndarray, int]:
    """Левенберг–Марквардт с границами (проекцией) и численным якобианом.

    `evaluate(theta, reg)` -> вектор невязок. Общий для кадра (`match`) и слоёв меток (region_match)."""
    r = evaluate(x, reg)
    cost, lam, it = float(r @ r), 1e-2, 0
    for it in range(1, max_iter + 1):
        J = np.empty((r.size, x.size))
        for i in range(x.size):
            step = _STEP if x[i] + _STEP <= hi[i] else -_STEP
            xp = x.copy()
            xp[i] += step
            J[:, i] = (evaluate(xp, reg) - r) / step
        A, g = J.T @ J, J.T @ r
        improved, rel = False, 0.0
        while lam < 1e7:
            dx = -np.linalg.solve(A + lam * (np.diag(np.diag(A)) + 1e-6 * np.eye(x.size)), g)
            xn = np.clip(x + dx, lo, hi)
            rn = evaluate(xn, reg)
            cn = float(rn @ rn)
            if cn < cost:
                rel = (cost - cn) / max(cost, 1e-12)
                x, r, cost, improved = xn, rn, cn, True
                lam = max(lam / 3.0, 1e-7)
                break
            lam *= 4.0
        if not improved or rel < 1e-4 or np.abs(dx).max() < 0.05:
            break
    return x, it


def make_anchor(target_rgb: np.ndarray, target_rect, ref_rgb: np.ndarray, ref_rect,
                ref_stack: AdjustmentStack | None = None, weight: float = 1.0, label: str = "") -> Anchor:
    """Якорь из пары прямоугольников (относительные координаты)."""
    ref_px = region_pixels(ref_rgb, ref_rect)
    if ref_stack is not None and not ref_stack.is_identity():
        ref_px = render_direct(ref_px, ref_stack)
    tgt = region_pixels(target_rgb, target_rect)
    if tgt.shape[0] > 4096:  # решателю хватит выборки
        tgt = tgt[np.linspace(0, tgt.shape[0] - 1, 4096).astype(int)]
    return Anchor(target_px=tgt, ref_lab=robust_lab(ref_px), weight=weight, label=label)

