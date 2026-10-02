"""Воркспейс: сцены, фото, референсы, настройки матчинга и экспорта (T-0018, T-0050).

Референсы — список фото-эталонов (обычно по одному на локацию); в каждый момент активен
ОДИН (`active_ref`), без усреднения. Авто-баланс применяет активный референс и запоминает
его в фото (`PhotoItem.ref_id`) — так «весь проект» потом подгоняется каждый кадр к своей
локации. Сцены — просто группы кадров (подпапки импорта), к референсам не привязаны.

Маски-метки (T-0058) — не в JSON, а файлами рядом: `<имя>.masks/<id фото>.npz`; в памяти —
`Workspace.masks` (id → веса uint8 H×W×9, см. models/masks.py). Схема JSON от них не зависит.

Файл `*.cmws` — JSON. Пути к фото хранятся ОТНОСИТЕЛЬНО папки файла воркспейса (если
это возможно — тот же диск), поэтому папку с воркспейсом и фото можно переносить.
Описание — docs/ENTITIES.md.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, Field, PrivateAttr

from ..services.naming import Naming
from .profile import AnchorPair, MatchSettings, ProfileOffset
from .stack import AdjustmentStack

SCHEMA_VERSION = 2  # v2: список референсов + активный (T-0050); миграция — services/workspace_io
WS_SUFFIX = ".cmws"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class RefItem(BaseModel):
    """Референс: фото воркспейса + ручное смещение цели («хочу темнее/теплее», T-0017)."""

    photo_id: str
    offset: ProfileOffset = Field(default_factory=ProfileOffset)


class PhotoItem(BaseModel):
    id: str = Field(default_factory=new_id)
    path: str                                   # относительно папки воркспейса (posix) или абсолютный
    stack: AdjustmentStack = Field(default_factory=AdjustmentStack)
    anchors: list[AnchorPair] = Field(default_factory=list)
    regions: list[Any] = Field(default_factory=list)   # зарезервировано: слои-регионы (T-0032)
    # слои меток (T-0061/T-0062): номер метки 0…6 -> свой стек правок поверх общего `stack`;
    # 9 — слой «Фон» (всё, что не закрашено метками, T-0082)
    layers: dict[int, AdjustmentStack] = Field(default_factory=dict)
    ref_id: str | None = None                   # референс, по которому фото подбиралось (T-0050)
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    def active_layers(self) -> dict[int, AdjustmentStack]:
        """Слои меток, которые что-то меняют (пустые стеки не рендерятся)."""
        return {slot: st for slot, st in sorted(self.layers.items()) if not st.is_identity()}


class Scene(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    photos: list[PhotoItem] = Field(default_factory=list)


class ExportSettings(BaseModel):
    out_dir: str | None = None                  # None — рядом с исходниками
    naming: Naming = Naming.version
    suffix: str = "_cm"
    format: Literal["keep", "jpg", "png", "tif"] = "keep"
    quality: int = 95
    bit_depth: Literal["keep", "8", "16"] = "keep"
    keep_structure: bool = True                 # подпапки-сцены в папке вывода


class Workspace(BaseModel):
    schema_version: int = SCHEMA_VERSION
    name: str = "workspace"
    scenes: list[Scene] = Field(default_factory=list)
    refs: list[RefItem] = Field(default_factory=list)   # все референсы проекта (T-0050)
    active_ref: str | None = None                          # активный — применяется авто-балансом
    settings: MatchSettings = Field(default_factory=MatchSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)

    _root: Path = PrivateAttr(default_factory=Path.cwd)
    _masks: dict = PrivateAttr(default_factory=dict)        # id фото -> np.ndarray (T-0058)
    _mask_ver: dict = PrivateAttr(default_factory=dict)     # id фото -> номер правки маски
    _mask_saved: dict = PrivateAttr(default_factory=dict)   # "папка|id" -> сохранённый номер

    # --- пути ------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    def set_root(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def abs_path(self, photo: PhotoItem) -> Path:
        p = Path(photo.path)
        return p if p.is_absolute() else (self._root / p).resolve()

    def rel_path(self, path: str | Path) -> str:
        """Путь для хранения: относительный к корню, если возможно (тот же диск)."""
        path = Path(path).resolve()
        try:
            return Path(os.path.relpath(path, self._root)).as_posix()
        except ValueError:  # другой диск на Windows
            return path.as_posix()

    def rebase(self, new_root: str | Path) -> None:
        """Сменить корень, сохранив фактические файлы (пути пересчитываются)."""
        absolute = {ph.id: self.abs_path(ph) for ph in self.all_photos()}
        self.set_root(new_root)
        for ph in self.all_photos():
            ph.path = self.rel_path(absolute[ph.id])

    # --- поиск -------------------------------------------------------------------
    def all_photos(self) -> Iterator[PhotoItem]:
        for sc in self.scenes:
            yield from sc.photos

    def scene(self, name_or_id: str, create: bool = False) -> Scene:
        for sc in self.scenes:
            if sc.id == name_or_id or sc.name == name_or_id:
                return sc
        if not create:
            raise KeyError(f"нет сцены {name_or_id!r}")
        sc = Scene(name=name_or_id)
        self.scenes.append(sc)
        return sc

    def locate(self, photo_id: str) -> tuple[Scene, PhotoItem]:
        for sc in self.scenes:
            for ph in sc.photos:
                if ph.id == photo_id:
                    return sc, ph
        raise KeyError(f"нет фото {photo_id!r}")

    def find(self, key: str) -> PhotoItem:
        """Фото по id, пути (абсолютному/относительному) или однозначному имени файла."""
        matches = []
        key_path = Path(key)
        key_abs = key_path.resolve() if key_path.exists() else None
        for ph in self.all_photos():
            if ph.id == key or ph.path == key_path.as_posix() or (key_abs and self.abs_path(ph) == key_abs):
                return ph
            if ph.name == key_path.name:
                matches.append(ph)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"нет фото {key!r}")
        raise KeyError(f"{key!r} неоднозначно: {len(matches)} фото с таким именем — укажите id или путь")

    def contains_file(self, path: str | Path) -> bool:
        target = Path(path).resolve()
        return any(self.abs_path(ph) == target for ph in self.all_photos())

    # --- состав --------------------------------------------------------------------
    def add_photo(self, path: str | Path, scene: str) -> PhotoItem | None:
        """Добавить файл в сцену (создаётся при необходимости). Дубликаты пропускаются."""
        if self.contains_file(path):
            return None
        ph = PhotoItem(path=self.rel_path(path))
        self.scene(scene, create=True).photos.append(ph)
        return ph

    def remove_photos(self, ids: set[str]) -> int:
        n = 0
        for sc in self.scenes:
            before = len(sc.photos)
            sc.photos = [p for p in sc.photos if p.id not in ids]
            n += before - len(sc.photos)
        self.refs = [r for r in self.refs if r.photo_id not in ids]
        self._fix_active()
        for ph in self.all_photos():
            if ph.ref_id in ids:
                ph.ref_id = None
        return n

    def remove_scene(self, name_or_id: str) -> int:
        sc = self.scene(name_or_id)
        n = self.remove_photos({p.id for p in sc.photos})
        self.scenes = [s for s in self.scenes if s.id != sc.id]
        return n

    # --- референсы (T-0050) ----------------------------------------------------------
    def ref_ids(self) -> set[str]:
        return {r.photo_id for r in self.refs}

    def ref_item(self, photo_id: str) -> RefItem | None:
        return next((r for r in self.refs if r.photo_id == photo_id), None)

    def add_ref(self, photo_id: str, activate: bool = True) -> None:
        if self.ref_item(photo_id) is None:
            self.refs.append(RefItem(photo_id=photo_id))
        if activate or self.active_ref is None:
            self.active_ref = photo_id

    def remove_ref(self, photo_id: str) -> None:
        self.refs = [r for r in self.refs if r.photo_id != photo_id]
        self._fix_active()

    def set_active(self, photo_id: str | None) -> None:
        if photo_id is not None and self.ref_item(photo_id) is None:
            raise KeyError(f"{photo_id!r} — не референс")
        self.active_ref = photo_id

    def _fix_active(self) -> None:
        if self.active_ref not in self.ref_ids():
            self.active_ref = self.refs[0].photo_id if self.refs else None

    def ref_for(self, photo: PhotoItem) -> str | None:
        """Референс фото: тот, по которому оно подбиралось, иначе активный."""
        return photo.ref_id if photo.ref_id in self.ref_ids() else self.active_ref

    # --- маски-метки (T-0058) -----------------------------------------------------------
    @property
    def masks(self) -> dict:
        """Маски фото: id -> веса (H, W, 9) uint8. Меняя массив на месте, вызовите touch_mask."""
        return self._masks

    def mask(self, photo_id: str):
        return self._masks.get(photo_id)

    def set_mask(self, photo_id: str, mask) -> None:
        if mask is None:
            self._masks.pop(photo_id, None)
        else:
            self._masks[photo_id] = mask
        self.touch_mask(photo_id)

    def touch_mask(self, photo_id: str) -> None:
        self._mask_ver[photo_id] = self._mask_ver.get(photo_id, 0) + 1

    def adopt_masks(self, other: "Workspace") -> None:
        """Перенести маски из другого экземпляра (снимок Undo заменяет объект воркспейса)."""
        self._masks, self._mask_ver, self._mask_saved = other._masks, other._mask_ver, other._mask_saved

    def missing(self) -> list[PhotoItem]:
        return [ph for ph in self.all_photos() if not self.abs_path(ph).exists()]
