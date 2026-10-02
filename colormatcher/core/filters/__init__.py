"""Фильтры. Импорт модулей регистрирует их в `FilterRegistry`.

Порядок по умолчанию в стеке — `DEFAULT_ORDER` (тон → цвет, см. docs/ALGORITHM.md §1).
"""

from .base import Filter, FilterRegistry, ParamSpec, register
from .brightness_contrast import BrightnessContrast
from .color_balance import ColorBalance
from .hue_saturation import HueSaturation

DEFAULT_ORDER = (BrightnessContrast.type, ColorBalance.type, HueSaturation.type)

__all__ = [
    "Filter",
    "FilterRegistry",
    "ParamSpec",
    "register",
    "BrightnessContrast",
    "ColorBalance",
    "HueSaturation",
    "DEFAULT_ORDER",
]
