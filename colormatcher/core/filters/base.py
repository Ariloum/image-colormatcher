"""Базовые классы фильтров и реестр.

Фильтр описывает свои параметры схемой (`ParamSpec`) — по ней GUI строит панель,
CLI — ключи `--set group.key=value`, а модели валидируют значения. Новый фильтр =
один файл с классом-наследником `Filter` и декоратором `@register`.

Имена параметров: короткий ключ внутри фильтра (`shadows.cr`) и полный ключ с
префиксом группы фильтра (`cb.shadows.cr`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np


@dataclass(frozen=True)
class ParamSpec:
    key: str                      # короткий ключ внутри фильтра
    label: str                    # подпись для UI
    min: float = -100.0
    max: float = 100.0
    default: float | bool = 0.0
    step: float = 1.0
    kind: str = "float"           # "float" | "bool"
    section: str = ""             # подгруппа в UI (напр. "shadows")
    gradient: tuple[str, str] | None = None  # цвета подложки ползунка (слева, справа)

    def clamp(self, value: Any) -> float | bool:
        if self.kind == "bool":
            return bool(value)
        return float(min(self.max, max(self.min, float(value))))


class Filter:
    """Поточечный фильтр: RGB (..., 3) float32 [0,1] -> RGB того же вида."""

    type: ClassVar[str]
    group: ClassVar[str]
    label: ClassVar[str]
    params: ClassVar[tuple[ParamSpec, ...]]
    pointwise: ClassVar[bool] = True  # можно ли запечь в 3D LUT

    @classmethod
    def spec(cls, key: str) -> ParamSpec:
        for p in cls.params:
            if p.key == key:
                return p
        raise KeyError(f"{cls.type}: нет параметра {key!r}")

    @classmethod
    def defaults(cls) -> dict[str, float | bool]:
        return {p.key: p.clamp(p.default) for p in cls.params}

    @classmethod
    def normalize(cls, params: dict[str, Any]) -> dict[str, float | bool]:
        """Дополняет дефолтами, зажимает в диапазоны, отбрасывает чужие ключи."""
        out = cls.defaults()
        for k, v in params.items():
            if k in out:
                out[k] = cls.spec(k).clamp(v)
        return out

    @classmethod
    def is_identity(cls, params: dict[str, Any]) -> bool:
        """Истина, если параметры не меняют картинку (фильтр можно пропустить)."""
        p = cls.normalize(params)
        return all(p[s.key] == s.default for s in cls.params if s.kind != "bool")

    @classmethod
    def apply(cls, rgb: np.ndarray, params: dict[str, Any]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError


class FilterRegistry:
    _by_type: dict[str, type[Filter]] = {}
    _by_group: dict[str, type[Filter]] = {}

    @classmethod
    def register(cls, flt: type[Filter]) -> type[Filter]:
        if flt.type in cls._by_type or flt.group in cls._by_group:
            raise ValueError(f"фильтр {flt.type}/{flt.group} уже зарегистрирован")
        cls._by_type[flt.type] = flt
        cls._by_group[flt.group] = flt
        return flt

    @classmethod
    def get(cls, type_: str) -> type[Filter]:
        try:
            return cls._by_type[type_]
        except KeyError:
            raise KeyError(f"неизвестный фильтр {type_!r}") from None

    @classmethod
    def by_group(cls, group: str) -> type[Filter]:
        try:
            return cls._by_group[group]
        except KeyError:
            raise KeyError(f"неизвестная группа параметров {group!r}") from None

    @classmethod
    def all(cls) -> list[type[Filter]]:
        return list(cls._by_type.values())

    @classmethod
    def resolve_key(cls, full_key: str) -> tuple[type[Filter], str]:
        """`cb.shadows.cr` -> (ColorBalance, "shadows.cr")."""
        group, _, key = full_key.partition(".")
        flt = cls.by_group(group)
        flt.spec(key)  # проверка существования
        return flt, key


register = FilterRegistry.register
