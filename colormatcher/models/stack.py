"""AdjustmentStack — упорядоченный стек фильтров фото + общая сила (opacity).

Параметры адресуются полными ключами `группа.ключ` (`bc.brightness`,
`cb.shadows.cr`, `hs.saturation`); сила — ключом `opacity`.
"""

from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel, Field, model_validator

from ..core.filters import DEFAULT_ORDER, FilterRegistry

OPACITY_KEY = "opacity"


class FilterState(BaseModel):
    type: str
    enabled: bool = True
    params: dict[str, float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize(self) -> "FilterState":
        self.params = FilterRegistry.get(self.type).normalize(self.params)
        return self

    @property
    def group(self) -> str:
        return FilterRegistry.get(self.type).group

    def is_identity(self) -> bool:
        return not self.enabled or FilterRegistry.get(self.type).is_identity(self.params)


def _default_filters() -> list[FilterState]:
    return [FilterState(type=t) for t in DEFAULT_ORDER]


class AdjustmentStack(BaseModel):
    filters: list[FilterState] = Field(default_factory=_default_filters)
    opacity: float = Field(100.0, ge=0.0, le=100.0)

    # --- доступ к фильтрам -------------------------------------------------
    def filter(self, type_: str) -> FilterState | None:
        return next((f for f in self.filters if f.type == type_), None)

    def ensure(self, type_: str) -> FilterState:
        """Фильтр типа `type_`; если его нет — вставить на место по DEFAULT_ORDER."""
        found = self.filter(type_)
        if found:
            return found
        state = FilterState(type=type_)
        rank = DEFAULT_ORDER.index(type_) if type_ in DEFAULT_ORDER else len(DEFAULT_ORDER)
        pos = sum(1 for f in self.filters if f.type in DEFAULT_ORDER and DEFAULT_ORDER.index(f.type) < rank)
        self.filters.insert(pos, state)
        return state

    # --- параметры по полным ключам -----------------------------------------
    def get(self, full_key: str) -> float | bool:
        if full_key == OPACITY_KEY:
            return self.opacity
        flt, key = FilterRegistry.resolve_key(full_key)
        state = self.filter(flt.type)
        return state.params[key] if state else flt.spec(key).default

    def set(self, full_key: str, value: Any) -> None:
        if full_key == OPACITY_KEY:
            self.opacity = float(min(100.0, max(0.0, float(value))))
            return
        flt, key = FilterRegistry.resolve_key(full_key)
        self.ensure(flt.type).params[key] = flt.spec(key).clamp(value)

    def update(self, values: dict[str, Any]) -> "AdjustmentStack":
        for k, v in values.items():
            self.set(k, v)
        return self

    def flat(self) -> dict[str, float | bool]:
        """Все параметры плоским словарём полных ключей (для CLI/отчётов)."""
        out: dict[str, float | bool] = {}
        for f in self.filters:
            out.update({f"{f.group}.{k}": v for k, v in f.params.items()})
        out[OPACITY_KEY] = self.opacity
        return out

    # --- группы (для частичной вставки пресетов) -----------------------------
    def groups(self) -> list[str]:
        return [f.group for f in self.filters] + [OPACITY_KEY]

    def copy_groups_from(self, other: "AdjustmentStack", groups: Iterable[str]) -> "AdjustmentStack":
        """Новый стек: текущий, где группы `groups` взяты из `other`."""
        result = self.model_copy(deep=True)
        for g in groups:
            if g == OPACITY_KEY:
                result.opacity = other.opacity
                continue
            flt = FilterRegistry.by_group(g)
            src = other.filter(flt.type)
            dst = result.ensure(flt.type)
            if src is None:
                dst.params = flt.defaults()
                dst.enabled = True
            else:
                dst.params = dict(src.params)
                dst.enabled = src.enabled
        return result

    def is_identity(self) -> bool:
        return self.opacity == 0 or all(f.is_identity() for f in self.filters)
