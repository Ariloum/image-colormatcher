"""Конвейер рендера: AdjustmentStack -> 3D LUT -> изображение, смешение по opacity и маске.

Главная операция — «стек + маска»: `render(img, stack, mask=m)`. Глобальная
коррекция — частный случай маски ≡ 1. На этом же примитиве позже встанут регионы
(T-0032): `render_layers` последовательно накладывает слои-регионы с их масками.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from ..models.stack import AdjustmentStack
from . import lut as lutlib
from .filters import FilterRegistry


def stack_function(stack: AdjustmentStack) -> lutlib.ColorFn | None:
    """Композиция включённых фильтров стека (без opacity). None — если ничего не меняет."""
    active = [f for f in stack.filters if not f.is_identity()]
    if not active:
        return None
    for f in active:
        if not FilterRegistry.get(f.type).pointwise:
            raise NotImplementedError(f"фильтр {f.type} не поточечный — нужна отдельная ступень конвейера")

    def fn(rgb: np.ndarray) -> np.ndarray:
        for f in active:
            rgb = FilterRegistry.get(f.type).apply(rgb, f.params)
        return rgb

    return fn


def bake_stack(stack: AdjustmentStack, size: int = lutlib.PREVIEW_SIZE) -> np.ndarray | None:
    fn = stack_function(stack)
    return None if fn is None else lutlib.bake(fn, size)


def _blend(img: np.ndarray, adj: np.ndarray, opacity: float, mask: np.ndarray | None) -> np.ndarray:
    strength: float | np.ndarray = opacity / 100.0
    if mask is not None:
        strength = np.asarray(mask, dtype=np.float32)[..., None] * strength
    if isinstance(strength, float) and strength == 1.0:
        return adj
    return (img + (adj - img) * strength).astype(np.float32)


def render(
    img: np.ndarray,
    stack: AdjustmentStack,
    *,
    mask: np.ndarray | None = None,
    lut: np.ndarray | None = None,
    lut_size: int = lutlib.PREVIEW_SIZE,
) -> np.ndarray:
    """Рендер через LUT. `lut` можно передать заранее запечённый (живое превью)."""
    img = np.asarray(img, dtype=np.float32)
    if stack.is_identity():
        return img.copy()
    if lut is None:
        lut = bake_stack(stack, lut_size)
        if lut is None:
            return img.copy()
    return _blend(img, lutlib.apply(img, lut), stack.opacity, mask)


def render_direct(img: np.ndarray, stack: AdjustmentStack, *, mask: np.ndarray | None = None) -> np.ndarray:
    """Эталонный рендер без LUT (медленнее; для тестов и сверки точности)."""
    img = np.asarray(img, dtype=np.float32)
    fn = stack_function(stack)
    if fn is None or stack.opacity == 0:
        return img.copy()
    return _blend(img, np.clip(fn(img), 0.0, 1.0), stack.opacity, mask)


def render_layers(
    img: np.ndarray,
    stack: AdjustmentStack,
    layers: Iterable[tuple[AdjustmentStack, np.ndarray]] = (),
    *,
    lut_size: int = lutlib.PREVIEW_SIZE,
) -> np.ndarray:
    """Глобальный стек, затем слои-регионы (стек, мягкая маска [0,1] HxW) по очереди."""
    out = render(img, stack, lut_size=lut_size)
    for layer_stack, mask in layers:
        out = render(out, layer_stack, mask=mask, lut_size=lut_size)
    return out


KEEP_SLOT = 8   # «не трогать» (models/masks.KEEP): там — исходник без коррекции
BG_LAYER = 9    # слой «Фон» (models/masks.BG_LAYER): вес = 1 − сумма всех меток (T-0082)


def mask_weights(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    """Маска меток uint8 (h', w', 9) -> веса float32 (h, w, 9) в размер кадра (мягкое масштабирование)."""
    import cv2

    if mask.shape[:2] != (h, w):
        interp = cv2.INTER_AREA if mask.shape[0] > h else cv2.INTER_LINEAR
        chans = [cv2.resize(np.ascontiguousarray(mask[..., k]), (w, h), interpolation=interp) for k in range(mask.shape[2])]
        mask = np.stack(chans, -1)
    return mask.astype(np.float32) / 255.0


def render_masked(
    img: np.ndarray,
    stack: AdjustmentStack,
    layers: dict[int, AdjustmentStack] | None = None,
    mask: np.ndarray | None = None,
    *,
    lut_for=None,
    lut_size: int = lutlib.PREVIEW_SIZE,
    base: np.ndarray | None = None,
) -> np.ndarray:
    """Общий стек + слои меток по мягким маскам (T-0062).

    G = общий стек(img); для метки i со своим слоем Lᵢ и слоя «Фон» L_bg (T-0082):
        out = G + Σ wᵢ·(Lᵢ(G) − G) + w_bg·(L_bg(G) − G) + w_keep·(img − G),   w_bg = 1 − Σ всех меток
    — слои складываются ПАРАЛЛЕЛЬНО (порядок меток не важен, на мягких стыках — плавная смесь),
    «не учитывать» — только общая коррекция, «не трогать» — исходник. Нет маски — фон весь кадр.
    `mask` — веса uint8 (H', W', 9) любого размера; `lut_for(stack)` — кеш LUT (превью);
    `base` — уже посчитанный G (превью его кеширует).
    """
    img = np.asarray(img, dtype=np.float32)

    def run(src: np.ndarray, st: AdjustmentStack) -> np.ndarray:
        if st.is_identity():
            return src
        lut = lut_for(st) if lut_for else bake_stack(st, lut_size)
        return render(src, st, lut=lut) if lut is not None else src

    g = base if base is not None else run(img, stack)
    bg = (layers or {}).get(BG_LAYER)
    bg = None if bg is None or bg.is_identity() else bg
    if mask is None:
        if bg is not None:   # масок нет — весь кадр фон
            return np.array(run(g, bg), dtype=np.float32, copy=True)
        return g if g is not img else img.copy()
    active = {k: st for k, st in (layers or {}).items()
              if k < mask.shape[2] and not st.is_identity() and mask[..., k].any()}
    if bg is not None:
        active[BG_LAYER] = bg
    keep = mask.shape[2] > KEEP_SLOT and mask[..., KEEP_SLOT].any()
    if not active and not (keep and g is not img):
        return g if g is not img else img.copy()
    h, w = img.shape[:2]
    wts = mask_weights(mask, h, w)
    out = g.copy()
    for k, st in active.items():
        wk = np.clip(1.0 - wts.sum(-1, keepdims=True), 0.0, 1.0) if k == BG_LAYER else wts[..., k:k + 1]
        out += wk * (run(g, st) - g)
    if keep and g is not img:
        out += wts[..., KEEP_SLOT:KEEP_SLOT + 1] * (img - g)
    return np.clip(out, 0.0, 1.0, out=out)
