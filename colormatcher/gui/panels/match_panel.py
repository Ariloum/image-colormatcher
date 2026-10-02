"""Панель авто-баланса: режим, сила, пределы; запуск для выделенных / сцены / всех (T-0014 в GUI).

Подбор идёт на копии воркспейса в фоне (процессы — services.matching); результат
применяется одним шагом отмены.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHeaderView, QLabel,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...models.stack import AdjustmentStack
from ...services import matching
from ..i18n import tr
from ..qtutil import plural
from ..state import AppState
from ..tasks import run_task


def modes() -> list[tuple[str, str]]:
    """Режимы подбора: ключ -> название (переводится при вызове, не при импорте)."""
    return [("balance+sat", tr("Баланс + насыщенность (рекомендуется)")), ("balance", tr("Только баланс (Color Balance)")),
            ("tone+balance+sat", tr("Тон + баланс + насыщенность")), ("full", tr("Всё, включая тон (hue)"))]


class MatchPanel(QWidget):
    status = Signal(str)
    progress_changed = Signal(int, int)   # сделано, всего (0, 0 — закончили) — для строки состояния (T-0045)

    def __init__(self, state: AppState):
        super().__init__()
        self.setObjectName("Panel")
        self.state = state
        self._task = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        box = QGroupBox(tr("Настройки подбора"))
        form = QFormLayout(box)
        self.mode = QComboBox()
        for key, title in modes():
            self.mode.addItem(title, key)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0, 100)
        self.strength.setSuffix(" %")
        self.sparsity = QDoubleSpinBox()
        self.sparsity.setRange(0, 1)
        self.sparsity.setSingleStep(0.02)
        self.sparsity.setToolTip(tr("Больше — меньше задействованных ползунков"))
        self.group_mean = QCheckBox(tr("Фото без референса — к медиане своей сцены"))
        form.addRow(tr("Режим"), self.mode)
        form.addRow(tr("Сила"), self.strength)
        form.addRow(tr("Разреженность"), self.sparsity)
        form.addRow(self.group_mean)
        self.use_masks = QCheckBox(tr("Учитывать маски: слой на каждую метку"))
        self.use_masks.setToolTip(tr("Цвет каждой метки кадра — к той же метке референса (нет её на референсе — та же область).\n"
                                     "«Не учитывать» и «не трогать» — вне статистики общего баланса."))
        form.addRow(self.use_masks)
        lay.addWidget(box)

        run = QGroupBox(tr("Запуск"))
        rl = QVBoxLayout(run)
        self.ref_label = QLabel()
        self.ref_label.setWordWrap(True)
        rl.addWidget(self.ref_label)
        self.btn_sel = QPushButton(tr("Выделенные — по активному референсу  (B)"))
        self.btn_sel.setObjectName("Accent")
        self.btn_scene = QPushButton(tr("Текущая сцена — каждому свой референс"))
        self.btn_all = QPushButton(tr("Весь проект — каждому свой референс"))
        self.btn_scene.setToolTip(tr("Каждое фото — к референсу, по которому оно подбиралось; новые — к активному"))
        self.btn_all.setToolTip(self.btn_scene.toolTip())
        for b in (self.btn_sel, self.btn_scene, self.btn_all):
            rl.addWidget(b)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFormat(tr("%v из %m"))
        self.progress.setVisible(False)
        rl.addWidget(self.progress)
        note = QLabel(tr("Референсы не меняются. Ручные правки ползунков, которые не подбираются, сохраняются. Ctrl+Z — отменить весь подбор."))
        note.setObjectName("Dim")
        note.setWordWrap(True)
        rl.addWidget(note)
        lay.addWidget(run)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("фото"), tr("оттенок ΔE"), tr("ползунки")])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)

        self.btn_sel.clicked.connect(lambda: self.start("selection"))
        self.btn_scene.clicked.connect(lambda: self.start("scene"))
        self.btn_all.clicked.connect(lambda: self.start("all"))
        state.workspace_changed.connect(self.load_settings)
        state.workspace_changed.connect(self._update_ref_label)
        self.load_settings()
        self._update_ref_label()

    def _update_ref_label(self) -> None:
        ws = self.state.ws
        if ws.active_ref:
            name = ws.locate(ws.active_ref)[1].name
            self.ref_label.setText(tr("Активный референс: <b>{name}</b> · всего референсов: {n}", name=name, n=len(ws.refs)))
        else:
            self.ref_label.setText(tr("<b>Нет активного референса</b> — выделите фото и нажмите Ctrl+R"))

    def load_settings(self) -> None:
        s = self.state.ws.settings
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.mode)))
        self.strength.setValue(s.strength)
        self.sparsity.setValue(s.sparsity)
        self.use_masks.setChecked(s.use_masks)

    def _settings(self):
        s = self.state.ws.settings.model_copy(deep=True)
        s.mode = self.mode.currentData()
        s.strength = self.strength.value()
        s.sparsity = self.sparsity.value()
        s.use_masks = self.use_masks.isChecked()
        s.touch = None
        return s

    def start(self, scope: str) -> None:
        if self._task is not None:
            return
        ws = self.state.ws
        ids, scenes, ref_id = None, None, None
        if scope == "selection":
            ids = set(self.state.targets())
            if not ids:
                self.status.emit(tr("Нет выделенных фото"))
                return
            ref_id = ws.active_ref  # выделенные — к активному референсу (T-0050)
            if ref_id is None and not self.group_mean.isChecked():
                self.status.emit(tr("Нет активного референса — выделите фото-эталон и нажмите Ctrl+R"))
                return
        elif scope == "scene":
            if not self.state.current:
                self.status.emit(tr("Нет текущего фото — не понятно, какая сцена"))
                return
            scenes = [ws.locate(self.state.current)[0].id]
        settings = self._settings()
        if settings != ws.settings:  # запомнить в воркспейсе (шаг отмены — чтобы работал «несохранено»)
            self.state.mutate(tr("Настройки авто-баланса"), lambda w: setattr(w, "settings", settings))
            ws = self.state.ws
        snapshot = ws.model_copy(deep=True)
        snapshot.set_root(ws.root)
        group_mean = self.group_mean.isChecked()
        self._busy(True)
        self.status.emit(tr("Авто-баланс: подготовка референсов…"))
        self._task = run_task(
            lambda progress: matching.match_workspace(snapshot, scenes, ids, settings, group_mean,
                                                      ref_id=ref_id, progress=progress),
            self._done, self._failed, on_progress=self._on_progress)

    def _on_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress_changed.emit(done, total)
        self.status.emit(tr("Авто-баланс: {done} из {total} фото…", done=done, total=total))

    def _busy(self, on: bool) -> None:
        self.progress.setVisible(on)
        if on:
            self.progress.setRange(0, 0)  # «бегущая» полоса, пока готовятся референсы
        else:
            self.progress_changed.emit(0, 0)
        for b in (self.btn_sel, self.btn_scene, self.btn_all):
            b.setEnabled(not on)

    def _done(self, results: list[dict]) -> None:
        self._task = None
        self._busy(False)
        ok = {r["id"]: r for r in results if "stack" in r and r.get("id")}
        if ok:
            def apply(ws):  # правки + запомненный референс — одним шагом отмены
                for pid, r in ok.items():
                    ph = ws.locate(pid)[1]
                    _replace(ph.stack, AdjustmentStack.model_validate(r["stack"]))
                    ph.ref_id = r.get("ref_id")
                    for k, st in r.get("layers", {}).items():   # слои меток (T-0063)
                        ph.layers[int(k)] = AdjustmentStack.model_validate(st)

            self.state.mutate(tr("Авто-баланс ({photos})", photos=plural(len(ok), "фото", "фото", "фото")), apply)
            self.state.stacks_changed.emit(list(ok))
        self._show(results)
        errors = [r for r in results if "error" in r]
        self.status.emit(tr("Авто-баланс: {photos}", photos=plural(len(ok), "фото", "фото", "фото"))
                         + (tr(", пропущено: {n}", n=len(errors)) if errors else ""))

    def _failed(self, err: str) -> None:
        self._task = None
        self._busy(False)
        self.status.emit(tr("Ошибка авто-баланса: {error}", error=err.splitlines()[0]))

    def _show(self, results: list[dict]) -> None:
        self.table.setRowCount(0)
        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(Path(r["file"]).name))
            if "error" in r:
                self.table.setItem(row, 1, QTableWidgetItem("—"))
                self.table.setItem(row, 2, QTableWidgetItem(r["error"]))
                continue
            self.table.setItem(row, 1, QTableWidgetItem(f"{r['before']['cast_dE']:.1f} → {r['after']['cast_dE']:.1f}"))
            sliders = ", ".join(f"{k.replace('cb.', '')}={v:+g}" for k, v in r["params"].items() if v)
            regions = r.get("regions", {})
            tip = sliders
            if regions:   # слои меток: что подобрано и насколько сошлось
                sliders = (sliders or "—") + tr("  · меток: {n}", n=len(regions))
                for slot, info in sorted(regions.items(), key=lambda kv: int(kv[0])):
                    ps = ", ".join(f"{k.replace('cb.', '')}={v:+g}" for k, v in info["params"].items()) or tr("без правок")
                    geo = tr(" (та же область на рефе)") if info.get("same_geometry") else ""
                    tip += "\n" + tr("Метка {n}{geo}: {params}; ΔL {l0:.1f}→{l1:.1f}, Δab {ab0:.1f}→{ab1:.1f}",
                                     n=int(slot) + 1, geo=geo, params=ps, l0=info['before']['dL'], l1=info['after']['dL'],
                                     ab0=info['before']['dAB'], ab1=info['after']['dAB'])
            item = QTableWidgetItem(sliders or "—")
            item.setToolTip(tip)
            self.table.setItem(row, 2, item)
        self.table.resizeColumnToContents(0)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, min(self.table.columnWidth(0), 180))


def _replace(dst: AdjustmentStack, src: AdjustmentStack) -> None:
    dst.filters, dst.opacity = src.filters, src.opacity
