"""Иконка приложения — красная луна из `icon.jpg` (T-0041, T-0051).

Исходник — маленький JPEG: луна на тёмной плашке, не по центру. Скрипт:
  1. находит диск луны (порог яркости → крупнейшая область → окружность методом наименьших
     квадратов по контуру);
  2. вырезает его по окружности со сглаженным краем (прозрачный фон, без тёмной каймы);
  3. ставит строго по центру квадрата и сохраняет moon.png (512) и moon.ico (16…256).

Запуск: python-embed\\python.exe tools\\make_icon.py [исходник.jpg]
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "colormatcher" / "gui" / "icons"
SRC = ROOT / "icon.jpg"
UP = 8          # работаем в увеличенном масштабе — сглаженный край и точный центр
MARGIN = 0.03   # поле вокруг диска, доля стороны


def find_disc(rgb: np.ndarray) -> tuple[float, float, float]:
    """Центр и радиус диска (в пикселях исходника)."""
    gray = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (3, 3), 0)
    thr = max(20.0, float(np.percentile(gray, 50)) * 0.5 + 12)   # фон тёмный, луна заметно светлее
    mask = (gray > thr).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask = (labels == big).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))   # тёмные «моря» внутри
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    pts = max(contours, key=cv2.contourArea)[:, 0, :].astype(np.float64)
    # окружность по контуру: x² + y² + Dx + Ey + F = 0 (метод Каса)
    A = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))])
    b = -(pts[:, 0] ** 2 + pts[:, 1] ** 2)
    D, E, F = np.linalg.lstsq(A, b, rcond=None)[0]
    cx, cy = -D / 2, -E / 2
    r = float(np.sqrt(cx ** 2 + cy ** 2 - F))
    return float(cx), float(cy), r


def cut_out(rgb: np.ndarray, cx: float, cy: float, r: float, size: int = 1024) -> np.ndarray:
    """Диск -> квадрат size×size RGBA по центру, прозрачный фон, сглаженный край."""
    big = cv2.resize(rgb, None, fx=UP, fy=UP, interpolation=cv2.INTER_LANCZOS4)
    cxu, cyu, ru = (cx + 0.5) * UP - 0.5, (cy + 0.5) * UP - 0.5, r * UP
    ru_in = ru - 1.6 * UP            # чуть внутрь: край JPEG на тёмном фоне — тёмная кайма
    half = int(np.ceil(ru_in)) + 2
    x0, y0 = int(round(cxu)) - half, int(round(cyu)) - half
    pad = np.pad(big, ((max(0, -y0), max(0, y0 + 2 * half - big.shape[0])),
                       (max(0, -x0), max(0, x0 + 2 * half - big.shape[1])), (0, 0)), mode="edge")
    oy, ox = max(0, -y0), max(0, -x0)
    crop = pad[y0 + oy:y0 + oy + 2 * half, x0 + ox:x0 + ox + 2 * half]
    yy, xx = np.mgrid[0:2 * half, 0:2 * half].astype(np.float32)
    dist = np.sqrt((xx - (cxu - x0)) ** 2 + (yy - (cyu - y0)) ** 2)
    alpha = np.clip(ru_in - dist + 0.5 * UP, 0, UP) / UP          # край ~1 исходный пиксель
    rgba = np.dstack([crop.astype(np.float32) / 255.0, alpha])
    inner = int(round(size * (1 - 2 * MARGIN)))
    rgba = cv2.resize(rgba, (inner, inner), interpolation=cv2.INTER_AREA)
    out = np.zeros((size, size, 4), np.float32)
    o = (size - inner) // 2
    out[o:o + inner, o:o + inner] = rgba
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    rgb = np.asarray(Image.open(src).convert("RGB"))
    cx, cy, r = find_disc(rgb)
    print(f"диск: центр ({cx:.1f}, {cy:.1f}), радиус {r:.1f} px в {rgb.shape[1]}×{rgb.shape[0]}")
    img = Image.fromarray(cut_out(rgb, cx, cy, r), "RGBA")
    OUT.mkdir(parents=True, exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(OUT / "moon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.resize((256, 256), Image.LANCZOS).save(OUT / "moon.ico", sizes=sizes)
    print(f"→ {OUT / 'moon.png'}, {OUT / 'moon.ico'}")


if __name__ == "__main__":
    main()
