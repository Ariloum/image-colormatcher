"""Модели данных (pydantic v2). Описание — docs/ENTITIES.md."""

from .preset import Preset
from .stack import AdjustmentStack, FilterState

__all__ = ["AdjustmentStack", "FilterState", "Preset"]
