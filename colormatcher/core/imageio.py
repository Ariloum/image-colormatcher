"""Загрузка/сохранение изображений.

* Пиксели — float32 RGB [0,1] формы (H, W, 3); альфа хранится отдельно.
* 8-битные форматы читает Pillow (с поворотом по EXIF), 16-битные PNG/TIFF — OpenCV.
* Пути с кириллицей: OpenCV читает/пишет через np.fromfile/tofile (cv2.imread их не умеет).
* При сохранении переносятся EXIF (ориентация сброшена в 1 — пиксели уже повёрнуты) и ICC.
  В 16-битный вывод (через OpenCV) метаданные пока не пишутся.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
_EXIF_ORIENTATION = 0x0112
_HIGH_DEPTH_MODES = {"I;16", "I;16B", "I;16L", "I", "F"}


@dataclass
class LoadedImage:
    rgb: np.ndarray                     # (H, W, 3) float32 [0,1]
    path: Path | None = None
    bit_depth: int = 8
    alpha: np.ndarray | None = None     # (H, W) float32 [0,1]
    exif: bytes | None = None
    icc: bytes | None = None
    format: str = ""
    info: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.rgb.shape[:2]
        return w, h


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXT


def _exif_bytes(pil: Image.Image) -> bytes | None:
    exif = pil.getexif()
    if not exif:
        return None
    exif[_EXIF_ORIENTATION] = 1
    return exif.tobytes()


def _read_cv(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    arr = cv2.imdecode(data, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if arr is None:
        raise ValueError(f"не удалось прочитать {path}")
    return arr


def _apply_orientation(arr: np.ndarray, orientation: int) -> np.ndarray:
    ops = {
        2: lambda a: a[:, ::-1],
        3: lambda a: a[::-1, ::-1],
        4: lambda a: a[::-1],
        5: lambda a: np.swapaxes(a, 0, 1),
        6: lambda a: np.swapaxes(a, 0, 1)[:, ::-1],
        7: lambda a: np.swapaxes(a, 0, 1)[::-1, ::-1],
        8: lambda a: np.swapaxes(a, 0, 1)[::-1],
    }
    return np.ascontiguousarray(ops[orientation](arr)) if orientation in ops else arr


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    with Image.open(path) as pil:
        fmt = pil.format or ""
        icc = pil.info.get("icc_profile")
        orientation = pil.getexif().get(_EXIF_ORIENTATION, 1)
        high_depth = pil.mode in _HIGH_DEPTH_MODES or _is_16bit(pil, fmt, path)

        if not high_depth:
            view = ImageOps.exif_transpose(pil)  # до _exif_bytes: тот сбрасывает ориентацию
            alpha = None
            if view.mode in ("RGBA", "LA", "PA") or (view.mode == "P" and "transparency" in view.info):
                rgba = view.convert("RGBA")
                alpha = np.asarray(rgba, dtype=np.float32)[..., 3] / 255.0
                rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32) / 255.0
            else:
                rgb = np.asarray(view.convert("RGB"), dtype=np.float32) / 255.0
            return LoadedImage(rgb, path, 8, alpha, _exif_bytes(pil), icc, fmt)
        exif = _exif_bytes(pil)

    arr = _read_cv(path)
    scale = 65535.0 if arr.dtype == np.uint16 else (255.0 if arr.dtype == np.uint8 else 1.0)
    arr = arr.astype(np.float32) / scale
    alpha = None
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=-1)
    elif arr.shape[2] == 4:
        alpha = arr[..., 3]
        arr = arr[..., :3]
    rgb = np.ascontiguousarray(arr[..., ::-1])  # BGR -> RGB
    rgb = _apply_orientation(rgb, orientation)
    if alpha is not None:
        alpha = _apply_orientation(alpha, orientation)
    return LoadedImage(np.clip(rgb, 0, 1), path, 16, alpha, exif, icc, fmt)


def _is_16bit(pil: Image.Image, fmt: str, path: Path) -> bool:
    """Pillow открывает 16-битный RGB PNG/TIFF как 8-битный — смотрим глубину в заголовке."""
    if fmt == "PNG":
        with open(path, "rb") as fh:
            header = fh.read(25)
        return len(header) == 25 and header[24] == 16  # IHDR: байт глубины
    if fmt == "TIFF":
        bits = pil.tag_v2.get(258, (8,))  # BitsPerSample
        bits = bits if isinstance(bits, tuple) else (bits,)
        return max(bits) > 8
    return False


def save_image(
    path: str | Path,
    rgb: np.ndarray,
    *,
    quality: int = 95,
    bit_depth: int = 8,
    alpha: np.ndarray | None = None,
    exif: bytes | None = None,
    icc: bytes | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    rgb = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    has_alpha = alpha is not None and ext in {".png", ".tif", ".tiff", ".webp"}

    if bit_depth == 16 and ext in {".png", ".tif", ".tiff"}:
        arr = np.round(rgb[..., ::-1] * 65535).astype(np.uint16)
        if has_alpha:
            a = np.round(np.clip(alpha, 0, 1) * 65535).astype(np.uint16)
            arr = np.dstack([arr, a])
        ok, buf = cv2.imencode(ext, arr)
        if not ok:
            raise ValueError(f"не удалось закодировать {path}")
        buf.tofile(str(path))
        return path

    arr8 = np.round(rgb * 255).astype(np.uint8)
    if has_alpha:
        a8 = np.round(np.clip(alpha, 0, 1) * 255).astype(np.uint8)
        pil = Image.fromarray(np.dstack([arr8, a8]), "RGBA")
    else:
        pil = Image.fromarray(arr8, "RGB")

    kwargs: dict = {}
    if exif:
        kwargs["exif"] = exif
    if icc:
        kwargs["icc_profile"] = icc
    if ext in {".jpg", ".jpeg"}:
        kwargs.update(quality=quality, subsampling=0 if quality >= 90 else 2, optimize=True)
    elif ext == ".webp":
        kwargs.update(quality=quality)
    pil.save(path, **kwargs)
    return path


def make_proxy(rgb: np.ndarray, max_side: int) -> np.ndarray:
    """Уменьшенная копия для превью/анализа (INTER_AREA); меньше max_side — без изменений."""
    h, w = rgb.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1.0:
        return rgb
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
