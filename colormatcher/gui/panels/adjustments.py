"""Панель правок: ползунки всех фильтров из реестра + opacity (T-0022, T-0027).

* Одно фото или группа: у группы при расхождении значение показывается «—».
* Групповая правка: абсолютно (всем одно значение) или относительно (+Δ к значению каждого).
* Color Balance — как в Photoshop: переключатель тени/середины/света и три ползунка.
* Перетаскивание ползунка — один шаг отмены (слияние в AppState).
* Слои меток (T-0061): «Общее» + цвета меток выделенных фото; выбран цвет — ползунки правят слой
  этой метки (поверх общих правок, только под маской). Точка на кнопке — у слоя есть правки.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ...core.filters import DEFAULT_ORDER, FilterRegistry, ParamSpec
from ...core.filters.color_balance import AXES, RANGES
from ...models import masks as M
from ...models.stack import AdjustmentStack
from ..i18n import tr
from ..qtutil import plural, swatch_icon
from ..state import AppState
from ..widgets.slider import ParamRow

GENERAL = 100   # id кнопки «Общее» в группе слоёв (метки — 0…6)


class ElidedLabel(QLabel):
    """Метка, которая не растягивает панель: длинный текст обрезается многоточием."""

    def __init__(self, text: str = ""):
        super().__init__()
        self._full = text
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideMiddle, max(10, self.width())))


OPACITY = ParamSpec("opacity", "Opacity", 0, 100, 100, gradient=None)   # подпись — tr() при построении
_AXIS_LABELS = {"cr": "Cyan ↔ Red", "mg": "Magenta ↔ Green", "yb": "Yellow ↔ Blue"}


def _range_title(rng: str) -> str:
    """Название диапазона Color Balance (переводится при показе)."""
    return {"shadows": tr("Тени"), "midtones": tr("Середины"), "highlights": tr("Света")}[rng]


class AdjustmentsPanel(QScrollArea):
    def __init__(self, state: AppState, theme, settings):
        super().__init__()
        self.state, self.theme, self.settings = state, theme, settings
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Panel")
        self.setWidget(body)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(8, 8, 8, 8)
        self.lay.setSpacing(8)

        head = QHBoxLayout()
        self.target_label = ElidedLabel(tr("Нет выбранных фото"))
        self.target_label.setObjectName("Title")
        head.addWidget(self.target_label, 1)
        self.lay.addLayout(head)
        # переключатель слоя (T-0061): «Общее» + цвета меток, которые есть на выделенных фото
        self.layer_row = QWidget()
        lr = QHBoxLayout(self.layer_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(2)
        self.layer_group = QButtonGroup(self)
        self.layer_group.setExclusive(True)
        self.layer_btns: dict[int, QToolButton] = {}
        general = QToolButton(text=tr("Общее"), checkable=True, checked=True)
        general.setToolTip(tr("Правки всего фото (фон и все метки)"))
        self.layer_group.addButton(general, GENERAL)
        lr.addWidget(general)
        self.bg_btn = QToolButton(text=tr("Фон"), checkable=True)
        self.bg_btn.setToolTip(tr("Правки всего, что не закрашено ни одной меткой (поверх общих). Только вручную — "
                                  "авто-баланс этот слой не трогает"))
        self.layer_group.addButton(self.bg_btn, M.BG_LAYER)
        lr.addWidget(self.bg_btn)
        for slot in range(M.COLOR_SLOTS):
            b = QToolButton(checkable=True)
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(28, 28)
            b.setStyleSheet("QToolButton { padding: 0px; }")
            b.setToolTip(tr("Слой метки {n}: правки только под этой маской, поверх общих", n=slot + 1))
            self.layer_group.addButton(b, slot)
            self.layer_btns[slot] = b
            lr.addWidget(b)
        lr.addStretch(1)
        self.layer_group.idClicked.connect(lambda i: state.set_active_layer(None if i == GENERAL else i))
        self.layer_hint = QLabel()
        self.layer_hint.setObjectName("Dim")
        self.layer_hint.setWordWrap(True)
        self.lay.addWidget(self.layer_row)
        self.lay.addWidget(self.layer_hint)
        self._apply_layer_colors()
        self.relative = QCheckBox(tr("Группа: относительно (+Δ к каждому)"))
        self.relative.setChecked(settings.group_edit_relative)
        self.relative.setToolTip(tr("Выкл — всем выделенным одно значение; вкл — сдвиг прибавляется к значению каждого фото"))
        self.relative.toggled.connect(lambda v: setattr(settings, "group_edit_relative", v))
        self.lay.addWidget(self.relative)

        self.rows: dict[str, ParamRow] = {}        # полный ключ -> строка (для CB — текущий диапазон)
        self.checks: dict[str, QCheckBox] = {}     # bool-параметры
        self.groups: dict[str, QGroupBox] = {}     # тип фильтра -> группа (галочка = enabled)
        self._drag: dict[str, dict[str, float]] = {}  # ключ -> стартовые значения по фото (относительный режим)
        self._active: str | None = None
        self.cb_range = "midtones"

        for ftype in DEFAULT_ORDER:
            self._build_filter(FilterRegistry.get(ftype))
        op_box = QGroupBox(tr("Смешивание"))
        ol = QVBoxLayout(op_box)
        self._add_row(ol, OPACITY, "opacity", label=tr("Сила (opacity)"))
        self.lay.addWidget(op_box)

        self._align_labels()
        btns = QHBoxLayout()
        self.reset_btn = QPushButton(tr("Сбросить"))
        self.reset_btn.setToolTip(tr("Сбросить все правки выделенных фото"))
        self.reset_btn.clicked.connect(self._reset_all)
        btns.addWidget(self.reset_btn)
        btns.addStretch(1)
        self.lay.addLayout(btns)
        self.lay.addStretch(1)

        state.selection_changed.connect(self.refresh)
        state.current_changed.connect(lambda _pid: self.refresh())
        state.stacks_changed.connect(lambda _ids: self.refresh())
        state.workspace_changed.connect(self.refresh)
        state.masks_changed.connect(lambda _pid: self.refresh())
        state.layer_changed.connect(lambda _l: self.refresh())
        self.refresh()

    # --- построение ---------------------------------------------------------------------
    def _build_filter(self, flt) -> None:
        box = QGroupBox(tr(flt.label))
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(tr("Галочка — фильтр включён"))
        box.toggled.connect(lambda on, t=flt.type: self._set_enabled(t, on))
        self.groups[flt.type] = box
        lay = QVBoxLayout(box)
        lay.setSpacing(2)
        if flt.type == "color_balance":
            radios = QHBoxLayout()
            self.range_group = QButtonGroup(self)
            self.range_buttons: dict[str, QRadioButton] = {}
            for rng in RANGES:
                rb = QRadioButton(_range_title(rng))
                rb.setChecked(rng == self.cb_range)
                rb.toggled.connect(lambda on, r=rng: on and self._switch_range(r))
                self.range_group.addButton(rb)
                self.range_buttons[rng] = rb
                radios.addWidget(rb)
            lay.addLayout(radios)
            for axis, _, _ in AXES:
                spec = flt.spec(f"{self.cb_range}.{axis}")
                self._add_row(lay, spec, f"cb.{self.cb_range}.{axis}", label=tr(_AXIS_LABELS[axis]), slot=f"cb.*.{axis}")
        else:
            for spec in flt.params:
                if spec.kind != "bool":
                    self._add_row(lay, spec, f"{flt.group}.{spec.key}")
        for spec in flt.params:
            if spec.kind == "bool":
                chk = QCheckBox(tr(spec.label))
                chk.setTristate(False)
                key = f"{flt.group}.{spec.key}"
                chk.clicked.connect(lambda on, k=key: self._set_bool(k, on))
                self.checks[key] = chk
                lay.addWidget(chk)
        self.lay.addWidget(box)

    def _add_row(self, lay, spec: ParamSpec, key: str, label: str | None = None, slot: str | None = None) -> None:
        row = ParamRow(spec, self.theme, self.settings.slider_label_size, label if label is not None else tr(spec.label))
        row.slider.sensitivity = self.settings.wheel_step
        row.key = key
        row.changed.connect(lambda v, r=row: self._on_change(r.key, v))
        row.committed.connect(lambda r=row: self._on_commit(r.key))
        self.rows[slot or key] = row
        lay.addWidget(row)

    def _align_labels(self) -> None:
        """Все подписи — одной ширины, чтобы линии ползунков были одинаковой длины (T-0048)."""
        w = max(r.label.fontMetrics().horizontalAdvance(r.label.text()) for r in self.rows.values()) + 10
        for r in self.rows.values():
            r.set_label_width(w)

    def _switch_range(self, rng: str) -> None:
        self.cb_range = rng
        for axis, _, _ in AXES:
            self.rows[f"cb.*.{axis}"].key = f"cb.{rng}.{axis}"
        self.refresh()

    # --- правка ---------------------------------------------------------------------------
    def _targets(self) -> list[str]:
        return self.state.targets()

    def _stack(self, pid: str) -> AdjustmentStack:
        return self.state.target_stack(pid, self.state.active_layer)

    def _on_change(self, key: str, value: float) -> None:
        ids = self._targets()
        if not ids:
            return
        self._active = key
        if self.relative.isChecked() and len(ids) > 1:
            if key not in self._drag:  # начало перетаскивания: запомнить старт и значения каждого фото
                row = self._row_for(key)
                start = row.spec.default if row.slider.mixed else self._shown.get(key, row.spec.default)
                self._drag[key] = {"__start__": float(start), **{i: float(self._stack(i).get(key)) for i in ids}}
            base = self._drag[key]
            delta = value - base["__start__"]
            fn: Callable[[AdjustmentStack, str], None] = lambda st, pid: st.set(key, base[pid] + delta)  # noqa: E731
            text = f"{self._title(key)} {delta:+g}"
        else:
            fn = lambda st, _pid: st.set(key, value)  # noqa: E731
            text = f"{self._title(key)} = {value:g}"
        self.state.edit_stacks(ids, fn, text, merge_key=key, layer=self.state.active_layer)

    def _on_commit(self, key: str) -> None:
        self._drag.pop(key, None)
        self._active = None
        self.state.end_merge()
        self.refresh()

    def _set_bool(self, key: str, on: bool) -> None:
        self.state.edit_stacks(self._targets(), lambda st, _pid: st.set(key, on), f"{self._title(key)}: {tr('вкл') if on else tr('выкл')}",
                               layer=self.state.active_layer)

    def _set_enabled(self, ftype: str, on: bool) -> None:
        if getattr(self, "_refreshing", False):
            return

        def fn(st: AdjustmentStack, _pid: str) -> None:
            st.ensure(ftype).enabled = on

        self.state.edit_stacks(self._targets(), fn, f"{tr(FilterRegistry.get(ftype).label)}: {tr('вкл') if on else tr('выкл')}",
                               layer=self.state.active_layer)

    def _reset_all(self) -> None:
        def fn(st: AdjustmentStack, _pid: str) -> None:
            fresh = AdjustmentStack()
            st.filters, st.opacity = fresh.filters, fresh.opacity

        layer = self.state.active_layer
        text = (tr("Сброс правок") if layer is None else tr("Сброс слоя «Фон»") if layer == M.BG_LAYER
                else tr("Сброс слоя метки {n}", n=layer + 1))
        self.state.edit_stacks(self._targets(), fn, text, layer=layer)

    def _title(self, key: str) -> str:
        if key == "opacity":
            return "Opacity"
        flt, k = FilterRegistry.resolve_key(key)
        return f"{tr(flt.label)}: {tr(flt.spec(k).label)}"

    def _row_for(self, key: str) -> ParamRow:
        for row in self.rows.values():
            if row.key == key:
                return row
        raise KeyError(key)

    # --- отображение ------------------------------------------------------------------------
    def apply_settings(self) -> None:
        for row in self.rows.values():
            row.slider.sensitivity = self.settings.wheel_step
        self._align_labels()
        self._apply_layer_colors()
        self.relative.setChecked(self.settings.group_edit_relative)

    def _apply_layer_colors(self) -> None:
        colors = M.palette_colors(self.settings.mask_palette)
        for slot, b in self.layer_btns.items():
            b.setIcon(swatch_icon(colors[slot], 18))

    def _refresh_layers(self, ids: list[str]) -> None:
        """Кнопки слоёв: метки с маской на выделенных фото или с уже заданным слоем (T-0061)."""
        ws = self.state.ws
        slots: set[int] = set()
        for i in ids:
            ph = ws.locate(i)[1]
            mask = ws.mask(i)
            if mask is not None:
                slots |= {k for k in range(M.COLOR_SLOTS) if mask[..., k].any()}
                slots.add(M.BG_LAYER)                       # есть маска — есть и фон вне неё (T-0082)
            slots |= {k for k, st in ph.layers.items() if not st.is_identity()}
        layer = self.state.active_layer
        if layer is not None and layer not in slots:
            self.state.set_active_layer(None)   # у новых фото этой метки нет — к общим правкам
            layer = None
        for slot, b in self.layer_btns.items():
            b.setVisible(slot in slots)
            used = any(slot in ws.locate(i)[1].active_layers() for i in ids)
            b.setText("•" if used else "")
        self.bg_btn.setVisible(M.BG_LAYER in slots)
        bg_used = any(M.BG_LAYER in ws.locate(i)[1].active_layers() for i in ids)
        self.bg_btn.setText(tr("Фон") + (" •" if bg_used else ""))
        self.layer_row.setVisible(bool(slots))
        self.layer_group.button(GENERAL if layer is None else layer).setChecked(True)
        if layer is None:
            self.layer_hint.setVisible(False)
        elif layer == M.BG_LAYER:
            self.layer_hint.setText(tr("Фон: правки только там, где нет ни одной метки, поверх общих. "
                                       "«Общее» — вернуться к правкам всего фото."))
            self.layer_hint.setVisible(True)
        else:
            self.layer_hint.setText(tr("Слой метки {n}: правки только под этой маской, поверх общих. "
                                       "«Общее» — вернуться к правкам всего фото.", n=layer + 1))
            self.layer_hint.setVisible(True)

    def refresh(self) -> None:
        ids = self._targets()
        self._refreshing = True
        self._shown: dict[str, float] = {}
        if not ids:
            self.target_label.setText(tr("Нет выбранных фото"))
        elif len(ids) == 1:
            self.target_label.setText(self.state.ws.locate(ids[0])[1].name)
        else:
            self.target_label.setText(tr("Группа: {photos}", photos=plural(len(ids), "фото", "фото", "фото")))
        self.relative.setVisible(len(ids) > 1)
        enabled = bool(ids)
        for box in self.groups.values():
            box.setEnabled(enabled)
        self.reset_btn.setEnabled(enabled)
        self._refresh_layers(ids)
        stacks = [self._stack(i) for i in ids]
        for row in self.rows.values():
            row.setEnabled(enabled)
            if row.key == self._active or not stacks:
                continue
            vals = {float(st.get(row.key)) for st in stacks}
            if len(vals) == 1:
                v = vals.pop()
                row.set_value(v)
                self._shown[row.key] = v
            else:
                row.set_mixed()
        for key, chk in self.checks.items():
            vals = {bool(st.get(key)) for st in stacks} if stacks else {False}
            chk.setTristate(len(vals) > 1)
            chk.setCheckState(Qt.PartiallyChecked if len(vals) > 1 else (Qt.Checked if vals.pop() else Qt.Unchecked))
        for ftype, box in self.groups.items():
            flags = {(st.filter(ftype).enabled if st.filter(ftype) else True) for st in stacks} or {True}
            box.setChecked(all(flags))
        # подсказка: в каких диапазонах CB есть правки
        for rng, rb in self.range_buttons.items():
            used = any(st.get(f"cb.{rng}.{a}") for st in stacks for a, _, _ in AXES)
            rb.setText(_range_title(rng) + (" •" if used else ""))
        self._refreshing = False
