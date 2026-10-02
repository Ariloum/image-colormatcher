"""Кеш изображений GUI: фоновая загрузка, прокси для превью, миниатюры с правками.

* `proxy(id)` — float32 RGB ~1600px для живого превью (LRU, ограничен по памяти);
* `thumb(id, stack, layers, mask, …)` — QImage-миниатюра с применённым стеком и слоями меток,
  по желанию — с оверлеем масок (T-0062, T-0065); кеш по хешу всего этого;
* всё тяжёлое — в QThreadPool; готовность — сигнал `ready(id)`.
Пока данных нет, методы возвращают None, а по готовности придёт сигнал.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from ..core import imageio
from ..core.pipeline import bake_stack, render, render_masked
from ..models.stack import AdjustmentStack
from .qtutil import to_qimage

THUMB_SIDE = 320  # базовая миниатюра (рисуется в ленте с масштабом)
THUMB_VARIANTS = 4  # вариантов миниатюры на фото в кеше (с оверлеем масок, без, прошлые правки)


class _Emitter(QObject):
    loaded = Signal(str, object, object, str)   # id, proxy, thumb_base, error
    thumbed = Signal(str, object, object)       # id, (ключ, вариант), QImage


class _LoadTask(QRunnable):
    def __init__(self, pid: str, path: Path, side: int, emitter: _Emitter):
        super().__init__()
        self.pid, self.path, self.side, self.emitter = pid, path, side, emitter

    def run(self) -> None:
        try:
            img = imageio.load_image(self.path).rgb
            proxy = imageio.make_proxy(img, self.side).copy()
            base = imageio.make_proxy(proxy, THUMB_SIDE).copy()
            self.emitter.loaded.emit(self.pid, proxy, base, "")
        except Exception as exc:  # noqa: BLE001
            self.emitter.loaded.emit(self.pid, None, None, f"{type(exc).__name__}: {exc}")


class _ThumbTask(QRunnable):
    def __init__(self, pid: str, key: str, base: np.ndarray, stack_json: str, emitter: _Emitter,
                 layers_json: dict | None = None, mask: np.ndarray | None = None, overlay=None):
        super().__init__()
        self.pid, self.key, self.base, self.stack_json, self.emitter = pid, key, base, stack_json, emitter
        self.layers_json, self.mask, self.overlay = layers_json or {}, mask, overlay

    def run(self) -> None:
        stack = AdjustmentStack.model_validate_json(self.stack_json)
        if self.mask is None and not self.layers_json:
            lut = None if stack.is_identity() else bake_stack(stack, 17)
            out = render(self.base, stack, lut=lut) if lut is not None else self.base
        else:
            layers = {int(k): AdjustmentStack.model_validate_json(v) for k, v in self.layers_json.items()}
            out = render_masked(self.base, stack, layers, self.mask, lut_size=17)
            if self.overlay is not None and self.mask is not None:   # маски поверх миниатюры (лента референсов в режиме «Маски»)
                import cv2

                from .qtutil import mask_rgba

                colors, alpha = self.overlay
                h, w = out.shape[:2]
                rgba = cv2.resize(mask_rgba(self.mask, colors, alpha), (w, h), interpolation=cv2.INTER_AREA)
                a = rgba[..., 3:4].astype(np.float32) / 255.0
                out = out * (1 - a) + rgba[..., :3].astype(np.float32) / 255.0 * a
        self.emitter.thumbed.emit(self.pid, self.key, to_qimage(out))


def stack_hash(stack: AdjustmentStack) -> str:
    return hashlib.md5(stack.model_dump_json().encode()).hexdigest()[:12]


class ImageCache(QObject):
    ready = Signal(str)          # у фото появились прокси/базовая миниатюра
    thumb_ready = Signal(str)    # готова миниатюра с правками
    failed = Signal(str, str)    # id, ошибка

    def __init__(self, preview_side: int = 1600, max_proxies: int = 12, parent: QObject | None = None):
        super().__init__(parent)
        self.preview_side = preview_side
        self.max_proxies = max_proxies
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, QThreadPool.globalInstance().maxThreadCount() - 1))
        self._proxies: OrderedDict[str, np.ndarray] = OrderedDict()
        self._bases: dict[str, np.ndarray] = {}
        # несколько вариантов миниатюры на фото (с оверлеем масок / без, T-0080): {id: {ключ: (вариант, QImage)}}
        self._thumbs: dict[str, OrderedDict[str, tuple[str, QImage]]] = {}
        self._pending: set[str] = set()
        self._thumb_pending: set[tuple[str, str]] = set()
        self._paths: dict[str, Path] = {}
        self.errors: dict[str, str] = {}
        self._em = _Emitter()
        self._em.loaded.connect(self._on_loaded)
        self._em.thumbed.connect(self._on_thumbed)

    # --- загрузка -------------------------------------------------------------------
    def request(self, pid: str, path: Path) -> None:
        self._paths[pid] = path
        if pid in self._pending or (pid in self._bases and pid in self._proxies) or pid in self.errors:
            return
        self._pending.add(pid)
        self.pool.start(_LoadTask(pid, path, self.preview_side, self._em))

    def _on_loaded(self, pid: str, proxy, base, error: str) -> None:
        self._pending.discard(pid)
        if error:
            self.errors[pid] = error
            self.failed.emit(pid, error)
            return
        self._proxies[pid] = proxy
        self._proxies.move_to_end(pid)
        while len(self._proxies) > self.max_proxies:
            self._proxies.popitem(last=False)
        self._bases[pid] = base
        self.ready.emit(pid)

    def proxy(self, pid: str, path: Path) -> np.ndarray | None:
        if pid in self._proxies:
            self._proxies.move_to_end(pid)
            return self._proxies[pid]
        self.request(pid, path)
        return None

    def base(self, pid: str, path: Path) -> np.ndarray | None:
        if pid in self._bases:
            return self._bases[pid]
        self.request(pid, path)
        return None

    # --- миниатюры с правками ------------------------------------------------------------
    def thumb(self, pid: str, path: Path, stack: AdjustmentStack, layers: dict | None = None,
              mask: np.ndarray | None = None, mask_ver: int = 0, overlay=None) -> QImage | None:
        """Миниатюра с правками. `mask` передаётся, только если она влияет (слои / «не трогать» / оверлей)."""
        layers_json = {k: st.model_dump_json() for k, st in (layers or {}).items()}
        key = stack_hash(stack)
        if mask is not None or layers_json:
            extra = f"{sorted(layers_json.items())}|{mask_ver if mask is not None else -1}|{overlay}"
            key += hashlib.md5(extra.encode()).hexdigest()[:8]
        variant = "overlay" if overlay is not None and mask is not None else "plain"
        cached = self._thumbs.get(pid, {})
        if key in cached:
            cached.move_to_end(key)
            return cached[key][1]
        base = self.base(pid, path)
        if base is not None and (pid, key) not in self._thumb_pending:
            self._thumb_pending.add((pid, key))
            self.pool.start(_ThumbTask(pid, (key, variant), base, stack.model_dump_json(), self._em, layers_json,
                                       None if mask is None else mask.copy(), overlay))
        # пока считается — последняя миниатюра ТОГО ЖЕ варианта (иначе ленты с оверлеем и без мигают)
        same = [img for v, img in cached.values() if v == variant]
        return same[-1] if same else None

    def _on_thumbed(self, pid: str, key_variant, img: QImage) -> None:
        key, variant = key_variant
        self._thumb_pending.discard((pid, key))
        variants = self._thumbs.setdefault(pid, OrderedDict())
        variants[key] = (variant, img)
        variants.move_to_end(key)
        while len(variants) > THUMB_VARIANTS:
            variants.popitem(last=False)
        self.thumb_ready.emit(pid)

    def forget(self, pid: str) -> None:
        for d in (self._proxies, self._bases, self._thumbs, self.errors):
            d.pop(pid, None)

    def clear(self) -> None:
        self._proxies.clear()
        self._bases.clear()
        self._thumbs.clear()
        self.errors.clear()
