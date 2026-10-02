"""Состояние приложения: воркспейс, выделение, текущее фото, Undo/Redo (T-0027/T-0028).

Все изменения идут через AppState, чтобы (1) их можно было отменить и (2) панели узнали
о них по сигналам:
  * `edit_stacks` — правки параметров (снимки стеков затронутых фото; перетаскивание
    одного ползунка сливается в один шаг отмены по `merge_key`); `layer` — общий стек (None)
    или слой метки 0…6 (T-0061); `active_layer` — что сейчас правит панель «Настройки фото»;
  * `mutate` — изменения состава (сцены, фото, референсы) — снимок всего воркспейса;
  * `push_mask_edit` — мазок кистью / удаление метки: хранится только изменённый прямоугольник
    маски до/после (T-0059).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..models.stack import AdjustmentStack
from ..models.workspace import Workspace
from ..services import workspace_io
from .i18n import N_, tr

_MERGEABLE = 1001  # id() сливаемых команд; что именно сливать — решает merge_key


class _StacksCommand(QUndoCommand):
    def __init__(self, state: "AppState", text: str, before: dict[str, str], after: dict[str, str], merge_key: str | None):
        super().__init__(text)
        self.state, self.before, self.after, self.merge_key = state, before, after, merge_key
        self._first = True

    def id(self) -> int:  # noqa: A003 — API Qt
        return _MERGEABLE if self.merge_key else -1

    def mergeWith(self, other: QUndoCommand) -> bool:  # noqa: N802
        if not isinstance(other, _StacksCommand) or other.merge_key != self.merge_key or set(other.after) != set(self.after):
            return False
        self.after = other.after
        return True

    def redo(self) -> None:
        if self._first:  # изменение уже применено вызывающим
            self._first = False
            return
        self.state._restore_stacks(self.after)

    def undo(self) -> None:
        self.state._restore_stacks(self.before)


class _SnapshotCommand(QUndoCommand):
    def __init__(self, state: "AppState", text: str, before: str, after: str):
        super().__init__(text)
        self.state, self.before, self.after = state, before, after
        self._first = True

    def redo(self) -> None:
        if self._first:
            self._first = False
            return
        self.state._restore_snapshot(self.after)

    def undo(self) -> None:
        self.state._restore_snapshot(self.before)


class _MaskCommand(QUndoCommand):
    """Правка маски фото: прямоугольник (x0, y0, x1, y1) до/после; None — маски не было/нет."""

    def __init__(self, state: "AppState", text: str, pid: str, box, before, after, shape):
        super().__init__(text)
        self.state, self.pid, self.box, self.before, self.after, self.shape = state, pid, box, before, after, shape
        self._first = True

    def _put(self, patch) -> None:
        ws = self.state.ws
        mask = ws.mask(self.pid)
        if mask is None:
            mask = np.zeros(self.shape, np.uint8)
            ws.masks[self.pid] = mask
        x0, y0, x1, y1 = self.box
        mask[y0:y1, x0:x1] = patch
        if not mask.any():
            ws.masks.pop(self.pid, None)
        ws.touch_mask(self.pid)
        self.state.masks_changed.emit(self.pid)

    def redo(self) -> None:
        if self._first:
            self._first = False
            return
        self._put(self.after)

    def undo(self) -> None:
        self._put(self.before)


class AppState(QObject):
    workspace_changed = Signal()          # состав/структура — панели перестраиваются
    stacks_changed = Signal(list)         # правки параметров этих фото
    selection_changed = Signal()
    current_changed = Signal(str)         # текущее фото ('' — нет)
    path_changed = Signal()
    masks_changed = Signal(str)           # маска фото изменилась (T-0058)
    layer_changed = Signal(object)        # панель правок переключена: None — общее, 0…6 — слой метки

    def __init__(self) -> None:
        super().__init__()
        self.ws = Workspace(name=tr("без имени"))
        self.path: Path | None = None
        self.selection: list[str] = []
        self.current: str = ""
        self.undo = QUndoStack(self)
        self.undo.setUndoLimit(200)
        self._session = 0  # номер «сеанса» перетаскивания: правки одного сеанса сливаются
        self.active_layer: int | None = None   # слой, который правит панель (T-0061)

    # --- файл --------------------------------------------------------------------
    def new(self) -> None:
        self.ws, self.path = Workspace(name=tr("без имени")), None
        self._reset()

    def open(self, path: str | Path) -> None:
        self.ws = workspace_io.load(path)
        self.path = workspace_io.ws_path(path).resolve()
        self._reset()

    def save(self, path: str | Path | None = None) -> Path:
        target = workspace_io.ws_path(path or self.path)
        if not path and self.path is None:
            raise ValueError(tr("нет пути для сохранения"))
        self.path = workspace_io.save(self.ws, target)
        if self.ws.name in ("", N_("без имени"), tr("без имени")):   # «без имени» на любом языке
            self.ws.name = self.path.stem
        self.undo.setClean()
        self.path_changed.emit()
        return self.path

    def _reset(self) -> None:
        self.undo.clear()
        self.set_active_layer(None)
        self.selection, self.current = [], ""
        self.path_changed.emit()
        self.workspace_changed.emit()
        self.selection_changed.emit()
        self.current_changed.emit("")

    def mark_dirty(self, text: str) -> None:
        """Шаг без изменений — отметка «есть несохранённое» (напр. после восстановления)."""
        self.undo.push(QUndoCommand(text))

    @property
    def dirty(self) -> bool:
        return not self.undo.isClean()

    # --- выделение ----------------------------------------------------------------
    def select(self, ids: Iterable[str], current: str | None = None) -> None:
        ids = [i for i in dict.fromkeys(ids) if self._exists(i)]
        if current is None:  # текущее не указано: оставить прежнее, если оно ещё выделено
            current = self.current if self.current in ids else (ids[0] if ids else "")
        cur = current
        changed_sel = ids != self.selection
        self.selection = ids
        if changed_sel:
            self.selection_changed.emit()
        if cur != self.current:
            self.current = cur
            self.current_changed.emit(cur)

    def _exists(self, pid: str) -> bool:
        try:
            self.ws.locate(pid)
            return True
        except KeyError:
            return False

    def targets(self) -> list[str]:
        """Фото для правки: выделение, иначе текущее."""
        return self.selection or ([self.current] if self.current else [])

    # --- правки параметров -------------------------------------------------------------
    def set_active_layer(self, layer: int | None) -> None:
        if layer != self.active_layer:
            self.active_layer = layer
            self.layer_changed.emit(layer)

    def target_stack(self, pid: str, layer: int | None = None, create: bool = False) -> AdjustmentStack:
        """Стек фото: общий (layer None) или слой метки; нет слоя — пустой (create — завести)."""
        ph = self.ws.locate(pid)[1]
        if layer is None:
            return ph.stack
        if layer not in ph.layers:
            if not create:
                return AdjustmentStack()
            ph.layers[layer] = AdjustmentStack()
        return ph.layers[layer]

    def edit_stacks(self, ids: Iterable[str], fn: Callable[[AdjustmentStack, str], None],
                    text: str, merge_key: str | None = None, layer: int | None = None) -> None:
        """Применить fn(стек, id) к стекам фото `ids` одним шагом отмены.

        layer None — общий стек фото, 0…6 — слой метки (заводится при первой правке, T-0061)."""
        ids = [i for i in ids if self._exists(i)]
        if not ids:
            return
        key_of = (lambda i: i) if layer is None else (lambda i: f"{i}#{layer}")
        before = {key_of(i): self.target_stack(i, layer).model_dump_json() for i in ids}
        for i in ids:
            fn(self.target_stack(i, layer, create=True), i)
        after = {key_of(i): self.target_stack(i, layer).model_dump_json() for i in ids}
        if layer is not None:   # пустые слои не храним
            for i in ids:
                ph = self.ws.locate(i)[1]
                if layer in ph.layers and ph.layers[layer].is_identity():
                    del ph.layers[layer]
        if before == after:
            return
        key = f"{merge_key}#{self._session}" if merge_key else None
        self.undo.push(_StacksCommand(self, text, before, after, key))
        self.stacks_changed.emit(ids)

    # --- маски (T-0058/T-0059) ----------------------------------------------------------
    def push_mask_edit(self, text: str, pid: str, box, before: np.ndarray) -> None:
        """Мазок уже нанесён на маску `pid`; `before` — прямоугольник `box` до мазка."""
        mask = self.ws.mask(pid)
        x0, y0, x1, y1 = box
        after = mask[y0:y1, x0:x1].copy()
        if np.array_equal(before, after):
            return
        self.ws.touch_mask(pid)
        if not mask.any():
            self.ws.masks.pop(pid, None)
        self.undo.push(_MaskCommand(self, text, pid, box, before, after, mask.shape))
        self.masks_changed.emit(pid)

    def remove_mask_slot(self, pid: str, slot: int) -> None:
        mask = self.ws.mask(pid)
        if mask is None or not mask[..., slot].any():
            return
        box = (0, 0, mask.shape[1], mask.shape[0])
        before = mask.copy()
        mask[..., slot] = 0
        self.push_mask_edit(tr("Удалить метку"), pid, box, before)

    def end_merge(self) -> None:
        """Отпустили ползунок: следующая правка — уже новый шаг отмены."""
        self._session += 1

    def _restore_stacks(self, data: dict[str, str]) -> None:
        ids = []
        for key, js in data.items():
            pid, _, layer = key.partition("#")
            if not self._exists(pid):
                continue
            ph = self.ws.locate(pid)[1]
            st = AdjustmentStack.model_validate_json(js)
            if not layer:
                ph.stack = st
            elif st.is_identity():
                ph.layers.pop(int(layer), None)
            else:
                ph.layers[int(layer)] = st
            ids.append(pid)
        self.stacks_changed.emit(ids)

    # --- изменения состава ---------------------------------------------------------------
    def mutate(self, text: str, fn: Callable[[Workspace], object]) -> object:
        before = self.ws.model_dump_json()
        result = fn(self.ws)
        after = self.ws.model_dump_json()
        if before != after:
            self.undo.push(_SnapshotCommand(self, text, before, after))
            self._after_structure()
        return result

    def _restore_snapshot(self, data: str) -> None:
        root, old = self.ws.root, self.ws
        self.ws = Workspace.model_validate_json(data)
        self.ws.set_root(root)
        self.ws.adopt_masks(old)   # маски живут вне JSON — переносятся (у их правок свой Undo)
        self._after_structure()
        self.stacks_changed.emit([ph.id for ph in self.ws.all_photos()])

    def _after_structure(self) -> None:
        self.selection = [i for i in self.selection if self._exists(i)]
        if self.current and not self._exists(self.current):
            self.current = self.selection[0] if self.selection else ""
            self.current_changed.emit(self.current)
        self.workspace_changed.emit()
        self.selection_changed.emit()
