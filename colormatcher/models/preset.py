"""Preset — снимок параметров с любого фото; файл `*.cmpreset.json`.

`groups` — какие группы параметров несёт пресет (`bc`, `cb`, `hs`, `opacity`);
при вставке в другое фото меняются только они.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from .stack import AdjustmentStack

PRESET_SUFFIX = ".cmpreset.json"
SCHEMA_VERSION = 1


class Preset(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str = ""
    stack: AdjustmentStack = Field(default_factory=AdjustmentStack)
    groups: list[str] = Field(default_factory=list)
    source: str | None = None

    @classmethod
    def from_stack(
        cls,
        stack: AdjustmentStack,
        name: str = "",
        groups: Iterable[str] | None = None,
        source: str | None = None,
    ) -> "Preset":
        return cls(
            name=name,
            stack=stack.model_copy(deep=True),
            groups=list(groups) if groups is not None else stack.groups(),
            source=source,
        )

    def apply_to(self, stack: AdjustmentStack, groups: Iterable[str] | None = None) -> AdjustmentStack:
        """Вставить пресет в стек. `groups` сужает набор групп пресета."""
        use = [g for g in self.groups if groups is None or g in set(groups)]
        return stack.copy_groups_from(self.stack, use)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        if not path.name.endswith(PRESET_SUFFIX) and path.suffix.lower() != ".json":
            path = path.with_name(path.name + PRESET_SUFFIX)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Preset":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    # Буфер обмена (копипаст пресетов между фото): JSON-строка
    def to_clipboard_text(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_clipboard_text(cls, text: str) -> "Preset":
        return cls.model_validate_json(text)
