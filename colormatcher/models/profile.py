"""Модели анализа и авто-баланса: ColorProfile («пипетка»), смещение, якоря, настройки матчинга.

Описание — docs/ENTITIES.md, алгоритм — docs/ALGORITHM.md §2–§4.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BANDS = ("shadows", "midtones", "highlights")


class BandStats(BaseModel):
    """Средний цвет почти нейтральных пикселей тонового диапазона в Lab."""

    L: float = 0.0
    a: float = 0.0
    b: float = 0.0
    mass: float = 0.0  # доля веса нейтральных пикселей этого диапазона (0..1)


class ColorProfile(BaseModel):
    bands: dict[str, BandStats] = Field(default_factory=lambda: {b: BandStats() for b in BANDS})
    L_p05: float = 0.0
    L_p50: float = 0.0
    L_p95: float = 0.0
    C_p50: float = 0.0          # хрома (насыщенность) Lab: медиана
    C_p90: float = 0.0          # и 90-й перцентиль
    S_p50: float = 0.0          # насыщенность Lab C/√(C²+L²) (не зависит от «ключа» сцены): медиана
    S_p90: float = 0.0          # и 90-й перцентиль
    neutral_share: float = 0.0  # доля почти нейтральных пикселей
    confidence: float = 0.0     # надёжность оценки каста 0..1


class ProfileOffset(BaseModel):
    """Ручное смещение целевого профиля набора референсов («хочу темнее / теплее»)."""

    L: float = 0.0        # светлота, единицы L*
    a: float = 0.0        # + к пурпурному / − к зелёному
    b: float = 0.0        # + к жёлтому (теплее) / − к синему
    chroma: float = 1.0   # множитель насыщенности


class Region(BaseModel):
    """Прямоугольная область в относительных координатах [0,1] (x, y, ширина, высота)."""

    image: str
    rect: tuple[float, float, float, float]


class AnchorPair(BaseModel):
    """Пара пипеток: один и тот же объект на референсе и на целевом фото."""

    label: str = ""
    ref: Region
    target: Region
    weight: float = 1.0


MatchMode = Literal["balance", "balance+sat", "tone+balance+sat", "full"]


class MatchSettings(BaseModel):
    mode: MatchMode = "balance+sat"    # тон (яркость/контраст) — только по запросу: сцены разного «ключа»
    touch: list[str] | None = None      # явный список параметров (перекрывает mode)
    regularize: float = 0.15            # штраф за величину ползунков (не «перекрутить»)
    band_weights: tuple[float, float, float] = (1.0, 1.2, 1.0)
    tone_weight: float = 0.7
    sat_weight: float = 1.0
    anchor_weight: float = 3.0
    sample_pixels: int = 12000          # размер выборки пикселей для решателя
    max_iter: int = 25
    sparsity: float = 0.08              # L1-штраф: «цена» пункта ползунка; 0 — выключить
    sparse_rounds: int = 2              # перевзвешивания ≈ L1: меньше задействованных ползунков
    strength: float = 100.0             # opacity результата
    reset: bool = True                  # обнулить решаемые ползунки перед подбором
    limits: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_LIMITS))  # |ползунок| ≤ предела
    use_masks: bool = True              # маски: слой на каждую метку + «не учитывать»/«не трогать» вне статистики (T-0063)


DEFAULT_LIMITS = {"bc.brightness": 40, "bc.contrast": 30, "hs.saturation": 35, "hs.hue": 20, "cb": 50}
