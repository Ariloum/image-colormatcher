"""Мелкие помощники Qt: numpy ↔ QImage, иконки-символы, фоны просмотра."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPixmap

from ..core.colorspace import linear_to_srgb


def to_qimage(rgb: np.ndarray) -> QImage:
    """float32 RGB [0,1] (H, W, 3) -> QImage (копия, владеет данными)."""
    # convertScaleAbs: умножение, округление и насыщение до uint8 за один быстрый проход
    arr = np.ascontiguousarray(cv2.convertScaleAbs(np.ascontiguousarray(rgb, dtype=np.float32), alpha=255.0))
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def from_qimage(img: QImage) -> np.ndarray:
    """QImage -> float32 RGB [0,1]."""
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    return buf[:, : w * 3].reshape(h, w, 3).astype(np.float32) / 255.0


def lab_to_qcolor(L: float, a: float, b: float) -> QColor:
    """Lab D65 -> QColor (для плашек профиля)."""
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    eps = 216 / 24389

    def inv(f: float) -> float:
        return f ** 3 if f ** 3 > eps else (116 * f - 16) / (24389 / 27)

    xyz = np.array([inv(fx) * 0.95047, inv(fy), inv(fz) * 1.08883])
    m = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560],
                  [0.0556434, -0.2040259, 1.0572252]])
    rgb = linear_to_srgb(np.clip(m @ xyz, 0, 1))
    return QColor.fromRgbF(*[float(c) for c in rgb])


def checker_brush(size: int = 12, a: str = "#8a8a8a", b: str = "#a8a8a8") -> QBrush:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor(a))
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, QColor(b))
    p.fillRect(size, size, size, size, QColor(b))
    p.end()
    return QBrush(pm)


VIEWER_BG = {"gray18": "#767676", "black": "#000000", "white": "#ffffff"}


def viewer_brush(kind: str, theme_viewer: str) -> QBrush:
    if kind == "checker":
        return checker_brush()
    return QBrush(QColor(VIEWER_BG.get(kind, theme_viewer)))


ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter


def plural(n: int, one: str, few: str, many: str) -> str:
    """Число со словом: формы — на исходном (русском) языке, переводятся через tr().

    Русский: 1 папка, 2 папки, 5 папок; другие языки — «одна / много» (формы few и many
    в переводе обычно совпадают: folders)."""
    from .i18n import SOURCE, current, tr

    if current() == SOURCE:
        n10, n100 = n % 10, n % 100
        word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    else:
        word = tr(one) if n == 1 else tr(many)
    return f"{n} {word}"


def swatch_icon(color: str, size: int = 16, pattern: str = "") -> QIcon:
    """Кружок цвета метки; pattern: "hatch" — «не учитывать», "cross" — «не трогать»."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(0, 0, 0, 120))
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(1, 1, size - 2, size - 2)
    if pattern:
        p.setPen(QColor(0, 0, 0, 170))
        if pattern == "hatch":
            for k in range(-size, size, 4):
                p.drawLine(k, size, k + size, 0)
        else:
            p.drawLine(4, 4, size - 4, size - 4)
            p.drawLine(size - 4, 4, 4, size - 4)
    p.end()
    return QIcon(pm)


def tool_icon(kind: str, ink: QColor, size: int = 20) -> QIcon:
    """Иконки инструментов масок, нарисованные кодом: "brush" — кисть, "eraser" — ластик."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QPen, QPolygonF

    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 20
    if kind == "brush":
        p.setPen(QPen(ink, 2.2 * k, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(17 * k, 3 * k), QPointF(9 * k, 11 * k))           # ручка
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([QPointF(9.5 * k, 9 * k), QPointF(11.5 * k, 11 * k), QPointF(7 * k, 17 * k),
                                 QPointF(2.5 * k, 17.5 * k), QPointF(3 * k, 13 * k)]))   # ворс
    else:
        p.translate(size / 2, size / 2)
        p.rotate(-40)
        p.setPen(QPen(ink, 1.4 * k))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(-8 * k, -4.5 * k, 16 * k, 9 * k), 2 * k, 2 * k)
        p.setBrush(ink)
        p.drawRoundedRect(QRectF(-8 * k, -4.5 * k, 6.5 * k, 9 * k), 2 * k, 2 * k)
    p.end()
    return QIcon(pm)


def palette_icon(colors: list[str], cell: int = 12) -> QIcon:
    """Полоска цветов палитры (для списка пресетов, T-0064)."""
    pm = QPixmap(cell * len(colors), cell)
    p = QPainter(pm)
    for i, c in enumerate(colors):
        p.fillRect(i * cell, 0, cell, cell, QColor(c))
    p.end()
    return QIcon(pm)


def mask_rgba(mask: np.ndarray, colors: list[str], alpha: float, only: int | None = None,
              x0: int = 0, y0: int = 0) -> np.ndarray:
    """Маска (H, W, 9) uint8 -> RGBA uint8 для оверлея: цвет метки, прозрачность = вес·alpha.

    Служебные метки со штриховкой: «не учитывать» — косые полосы, «не трогать» — клетка.
    x0, y0 — смещение куска `mask` в полной маске (штриховка не «съезжает» при частичном обновлении)."""
    from ..models import masks as M

    h, w, n = mask.shape
    m = mask.astype(np.float32) / 255.0
    if only is not None:
        sel = np.zeros(n, np.float32)
        sel[only] = 1
        m *= sel
    yy, xx = np.ogrid[y0:y0 + h, x0:x0 + w]
    m[..., M.IGNORE] *= (((xx + yy) // 5) % 2 == 0) * 0.7 + 0.3
    m[..., M.KEEP] *= ((((xx // 7) + (yy // 7)) % 2) == 0) * 0.5 + 0.5
    rgb = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colors], np.float32)
    weight = m.sum(-1)
    tint = (m @ rgb) / np.maximum(weight, 1e-6)[..., None]
    out = np.empty((h, w, 4), np.uint8)
    out[..., :3] = np.clip(tint, 0, 255)
    out[..., 3] = np.clip(weight * alpha * 255, 0, 255)
    return out


def rgba_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


def scrolled(widget):
    """Обернуть панель в вертикальную прокрутку без рамки: её высота больше не распирает окно (T-0079)."""
    from PySide6.QtWidgets import QFrame, QScrollArea

    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    # по ширине не обрезать: минимум — содержимое + полоса прокрутки
    area.setMinimumWidth(widget.minimumSizeHint().width() + area.verticalScrollBar().sizeHint().width())
    return area
