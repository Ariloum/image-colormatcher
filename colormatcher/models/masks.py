"""Маски-метки (T-0058, T-0064): палитры, мягкая кисть, площади.

Маска фото — массив весов uint8 (H, W, SLOTS): на каждую метку свой канал 0…255, сумма по
каналам в каждом пикселе ≤ 255 (фон = 255 − сумма). Метки адресуются СЛОТОМ, а не цветом:
пресет палитры меняет только отображение. Слоты 0…6 — цвета палитры проекта, 7 — служебная
«не учитывать» (вне статистики авто-баланса), 8 — «не трогать» (коррекция = 0).
Описание — docs/MASKS-DESIGN.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

COLOR_SLOTS = 7
IGNORE, KEEP = 7, 8
SLOTS = 9
SLOT_NAMES = {IGNORE: "не учитывать", KEEP: "не трогать"}
BG_LAYER = 9   # слой «Фон» в PhotoItem.layers: всё, что не закрашено ни одной меткой (T-0082)

# пресеты палитр: 7 спокойных различимых оттенков + служебные (T-0064)
PALETTES: dict[str, tuple[str, list[str]]] = {
    "calm": ("Спокойная", ["#7FB08A", "#6F9BC8", "#9D8CC7", "#C98A6B", "#C9B06B", "#5FA8A6", "#C48399"]),
    "pastel": ("Пастель", ["#A8D5B5", "#A3C4E6", "#C6B8E6", "#E6B8A0", "#E6D8A0", "#9ED6D3", "#E6B3C6"]),
    "deep": ("Глубокая", ["#4E7D5B", "#3F6690", "#6A5A96", "#97583A", "#967D3A", "#2F7472", "#8F4F68"]),
    "distinct": ("Различимая", ["#D9A441", "#6CB4DE", "#3A9E80", "#D9CF6A", "#3C7FB8", "#C9723C", "#C08AAE"]),
}
SPECIAL_COLORS = {IGNORE: "#7A7A7A", KEEP: "#E4E4E4"}
DEFAULT_PALETTE = "calm"


def palette_colors(preset: str) -> list[str]:
    """Цвета всех слотов (0…8) для пресета."""
    colors = PALETTES.get(preset, PALETTES[DEFAULT_PALETTE])[1]
    return colors + [SPECIAL_COLORS[IGNORE], SPECIAL_COLORS[KEEP]]


MASK_SIDE = 1024      # длинная сторона сетки маски (прокси-разрешение; к кадру масштабируется)
TINY_AREA = 0.002     # метка меньше 0.2% кадра — вероятно, случайный мазок (предупреждение)


def empty(h: int, w: int) -> np.ndarray:
    return np.zeros((h, w, SLOTS), np.uint8)


def shape_for(h: int, w: int) -> tuple[int, int]:
    """Размер сетки маски для кадра h×w (пропорции кадра, длинная сторона MASK_SIDE)."""
    k = MASK_SIDE / max(h, w)
    return max(1, round(h * k)), max(1, round(w * k))


CORE_MIN, CORE_MAX = 0.106, 0.95      # радиус плотного ядра кисти при распределении 0 и 100%
_CORE_LEVEL = 1.0 - 0.8 ** 0.5         # (1 − c^p)² = 0.8  <=>  c^p = 1 − √0.8


@dataclass
class Brush:
    diameter: float = 120.0   # px маски
    hardness: float = 0.0     # жёсткое ядро 0…1 (в панели скрыто, всегда 0 — T-0075; на будущее)
    opacity: float = 1.0      # сила мазка 0…1 (в панели скрыто, всегда 1 — T-0075; на будущее)
    spread: float = 0.2       # распределение 0…1: 0 — мягкое пятно, 1 — почти сплошной диск (T-0075)


def brush_profile(d: np.ndarray, spread: float = 0.2, hardness: float = 0.0) -> np.ndarray:
    """Сила кисти 0…1 по относительному расстоянию от центра d (0 — центр, 1 — край).

    (1 − d^p)² — профиль ВСЕГДА занимает весь диаметр (T-0075), меняется распределение. Радиус
    плотного ядра (сила ≥ 80%) растёт с `spread` ЛИНЕЙНО — шаги шкалы видны одинаково (T-0078):
    0 → 0.106 радиуса (конус (1 − d)², ~10% диаметра, дальше плавный видимый спад, T-0076),
    0.25 → 0.32, 0.5 → 0.53, 0.75 → 0.74, 1 → 0.95 (почти сплошной диск). Гладко гаснет в 0 на краю.
    `hardness` > 0 — ядро полной силы, профиль сжимается в кольцо вокруг него.
    """
    d = np.asarray(d, np.float32)
    inner = min(max(hardness, 0.0), 0.999)
    if inner > 0:
        d = np.clip((d - inner) / (1.0 - inner), 0.0, None)
    core = CORE_MIN + (CORE_MAX - CORE_MIN) * min(max(spread, 0.0), 1.0)
    p = max(1.0, float(np.log(_CORE_LEVEL) / np.log(core)))
    return np.clip(1.0 - np.minimum(d, 1.0) ** p, 0.0, 1.0) ** 2


def _capsule(p0: tuple[float, float], p1: tuple[float, float], brush: Brush, box: tuple[int, int, int, int]) -> np.ndarray:
    """Покрытие 0…1 «капсулы» — отрезка p0→p1 толщиной в диаметр кисти с мягким краем.

    Считается расстояние каждого пикселя до ОТРЕЗКА (а не до отдельных штампов), поэтому мазок —
    сплошной «червячок» без бусин (T-0071). Поперечное сечение — профиль кисти `brush_profile`.
    """
    x0, y0, x1, y1 = box
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32) + 0.5   # центры пикселей (T-0077)
    ax, ay = p0
    dx, dy = p1[0] - ax, p1[1] - ay
    len2 = dx * dx + dy * dy
    if len2 > 1e-9:
        t = np.clip(((xx - ax) * dx + (yy - ay) * dy) / len2, 0.0, 1.0)
        px, py = ax + t * dx, ay + t * dy
    else:
        px, py = ax, ay
    r = max(brush.diameter / 2, 0.5)
    d = np.sqrt((xx - px) ** 2 + (yy - py) ** 2) / r
    return brush_profile(d, brush.spread, brush.hardness)


def _composite(before: np.ndarray, a: np.ndarray, slot: int, erase: bool) -> np.ndarray:
    """Маска до мазка + покрытие мазка `a` (0…1) -> новая маска (uint8).

    Рисование: вес метки растёт к 255 как «нормальный» мазок PS, остальные метки в этом месте
    пропорционально уступают (сумма ≤ 255). Ластик: уменьшает веса всех меток.
    """
    region = before.astype(np.float32)
    if erase:
        region = np.rint(region * (1.0 - a)[..., None])
    else:
        cur = region[..., slot]
        new = np.rint(cur + (255.0 - cur) * a)
        others = region.sum(-1) - cur
        room = 255.0 - new
        scale = np.where(others > room, room / np.maximum(others, 1e-6), 1.0)
        region = np.floor(region * scale[..., None] + 1e-4)   # вниз — сумма не превысит 255
        region[..., slot] = new
    return np.clip(region, 0, 255).astype(np.uint8)


class Stroke:
    """Один мазок (от нажатия до отпускания кнопки).

    Покрытие мазка копится МАКСИМУМОМ (как «непрозрачность» кисти PS): перекрытия внутри мазка
    не накапливаются — нажим 13% даёт ровно 13% по всему следу. Смешивание с маской — от снимка
    на начало мазка (`before`), он же нужен для Undo.
    """

    def __init__(self, mask: np.ndarray, slot: int, brush: Brush, erase: bool = False):
        self.mask, self.slot, self.brush, self.erase = mask, slot, brush, erase
        self.before = mask.copy()
        self.acc = np.zeros(mask.shape[:2], np.float32)
        self.box: tuple[int, int, int, int] | None = None   # общий изменённый прямоугольник

    def add(self, p0: tuple[float, float], p1: tuple[float, float]) -> tuple[int, int, int, int] | None:
        """Добавить отрезок траектории. Возвращает изменённый прямоугольник (x0, y0, x1, y1)."""
        h, w = self.mask.shape[:2]
        r = self.brush.diameter / 2
        x0, y0 = max(0, int(np.floor(min(p0[0], p1[0]) - r))), max(0, int(np.floor(min(p0[1], p1[1]) - r)))
        x1, y1 = min(w, int(np.ceil(max(p0[0], p1[0]) + r)) + 1), min(h, int(np.ceil(max(p0[1], p1[1]) + r)) + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        box = (x0, y0, x1, y1)
        acc = self.acc[y0:y1, x0:x1]
        np.maximum(acc, _capsule(p0, p1, self.brush, box) * self.brush.opacity, out=acc)
        self.mask[y0:y1, x0:x1] = _composite(self.before[y0:y1, x0:x1], acc, self.slot, self.erase)
        b = self.box
        self.box = box if b is None else (min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1))
        return box


def stamp(mask: np.ndarray, slot: int, x: float, y: float, brush: Brush, erase: bool = False) -> tuple[int, int, int, int] | None:
    """Отдельный отпечаток кисти в точке (x, y) — мазок из одного клика."""
    return Stroke(mask, slot, brush, erase).add((x, y), (x, y))


def stroke(mask: np.ndarray, slot: int, p0: tuple[float, float], p1: tuple[float, float], brush: Brush,
           erase: bool = False) -> tuple[int, int, int, int] | None:
    """Прямой мазок от p0 до p1 (сплошная капсула)."""
    return Stroke(mask, slot, brush, erase).add(p0, p1)


def coverage(mask: np.ndarray | None) -> dict[int, float]:
    """Площадь каждой метки в долях кадра (вес учитывается): {слот: доля}, только ненулевые."""
    if mask is None:
        return {}
    total = mask.shape[0] * mask.shape[1] * 255.0
    sums = mask.reshape(-1, mask.shape[2]).sum(0, dtype=np.float64) / total
    return {s: float(v) for s, v in enumerate(sums) if v > 0}


def remove_slot(mask: np.ndarray, slot: int) -> None:
    mask[..., slot] = 0


def resized(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    """Маска под другой размер кадра (мягкое масштабирование весов)."""
    if mask.shape[:2] == (h, w):
        return mask
    import cv2

    chans = [cv2.resize(mask[..., s], (w, h), interpolation=cv2.INTER_LINEAR) for s in range(mask.shape[2])]
    return np.stack(chans, -1)
