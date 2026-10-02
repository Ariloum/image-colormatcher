"""Панель пресетов: библиотека (`%APPDATA%/colormatcher/presets`), сохранить с текущего фото,
применить к выделенным (по группам параметров), удалить (T-0011 в GUI)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ...models import Preset
from ...models.stack import AdjustmentStack
from ..i18n import tr
from ..qtutil import plural
from ..settings import app_home
from ..state import AppState


def groups_titles() -> list[tuple[str, str]]:
    """Группы параметров пресета: ключ -> название (переводится при вызове, не при импорте)."""
    return [("bc", tr("Яркость/контраст")), ("cb", "Color Balance"), ("hs", tr("Тон/насыщенность")), ("opacity", "Opacity")]


def presets_dir() -> Path:
    d = app_home() / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


class PresetsPanel(QWidget):
    status = Signal(str)

    def __init__(self, state: AppState):
        super().__init__()
        self.setObjectName("Panel")
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _it: self.apply())
        lay.addWidget(self.list, 1)
        lay.addWidget(QLabel(tr("Какие группы параметров:")))
        self.group_checks: dict[str, QCheckBox] = {}
        for key, title in groups_titles():
            c = QCheckBox(title)
            c.setChecked(True)
            self.group_checks[key] = c
            lay.addWidget(c)
        row = QHBoxLayout()
        self.btn_apply = QPushButton(tr("Применить к выделенным"))
        self.btn_apply.setObjectName("Accent")
        self.btn_save = QPushButton(tr("Сохранить с текущего…"))
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_save)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.btn_del = QPushButton(tr("Удалить"))
        self.btn_open = QPushButton(tr("Папка пресетов"))
        row2.addWidget(self.btn_del)
        row2.addWidget(self.btn_open)
        lay.addLayout(row2)
        hint = QLabel(tr("Ctrl+Shift+C / Ctrl+Shift+V — копировать/вставить правки между фото без сохранения."))
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.btn_apply.clicked.connect(self.apply)
        self.btn_save.clicked.connect(self.save_from_current)
        self.btn_del.clicked.connect(self.delete)
        self.btn_open.clicked.connect(lambda: os.startfile(presets_dir()))  # noqa: S606 — Windows
        self.reload()

    def groups(self) -> list[str]:
        return [k for k, c in self.group_checks.items() if c.isChecked()]

    def reload(self) -> None:
        self.list.clear()
        for f in sorted(presets_dir().glob("*.cmpreset.json")):
            try:
                p = Preset.load(f)
            except Exception:  # noqa: BLE001
                continue
            it = QListWidgetItem(p.name or f.stem)
            it.setData(Qt.UserRole, str(f))
            changed = {k: v for k, v in p.stack.flat().items() if AdjustmentStack().flat().get(k) != v}
            it.setToolTip("\n".join(f"{k} = {v}" for k, v in changed.items()) or tr("без изменений"))
            self.list.addItem(it)

    def apply(self) -> None:
        it = self.list.currentItem()
        ids = self.state.targets()
        if it is None or not ids:
            self.status.emit(tr("Выберите пресет и фото"))
            return
        preset = Preset.load(it.data(Qt.UserRole))
        apply_preset(self.state, preset, ids, self.groups())
        self.status.emit(tr("Пресет «{name}» → {photos}", name=preset.name, photos=plural(len(ids), "фото", "фото", "фото")))

    def save_from_current(self) -> None:
        if not self.state.current:
            self.status.emit(tr("Нет текущего фото"))
            return
        _, ph = self.state.ws.locate(self.state.current)
        name, ok = QInputDialog.getText(self, tr("Сохранить пресет"), tr("Имя пресета:"), text=Path(ph.name).stem)
        if not ok or not name.strip():
            return
        safe = "".join(c if c.isalnum() or c in " _-." else "_" for c in name.strip())
        Preset.from_stack(ph.stack, name=name.strip(), groups=self.groups(), source=ph.path).save(presets_dir() / safe)
        self.reload()
        self.status.emit(tr("Пресет «{name}» сохранён", name=name.strip()))

    def delete(self) -> None:
        it = self.list.currentItem()
        if it and QMessageBox.question(self, tr("Удалить пресет"), tr("Удалить «{name}»?", name=it.text())) == QMessageBox.Yes:
            Path(it.data(Qt.UserRole)).unlink(missing_ok=True)
            self.reload()


def apply_preset(state: AppState, preset: Preset, ids: list[str], groups: list[str] | None = None) -> None:
    def fn(st: AdjustmentStack, _pid: str) -> None:
        new = preset.apply_to(st, groups)
        st.filters, st.opacity = new.filters, new.opacity

    state.edit_stacks(ids, fn, tr("Пресет «{name}»", name=preset.name or tr("из буфера")))
