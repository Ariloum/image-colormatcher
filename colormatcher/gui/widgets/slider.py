"""Строка параметра: подпись + ползунок с градиентной подложкой + числовое поле (UI-DESIGN §6).

* клик по линии — значение в точку клика; перетаскивание — плавно (T-0046);
* Ctrl+клик — шаг 10 в сторону клика, Shift+клик — шаг 20; при перетаскивании с Ctrl /
  Shift значение прилипает к шагу 10 / 20; колесо — ±шаг×чувствительность, Ctrl ±10, Shift ±20;
* двойной клик или правый клик (по линии или подписи) — сброс к значению по умолчанию
  (0; у opacity — 100%) (T-0056);
* несимметричный диапазон (контраст −50…+100): ноль посередине, половины шкалы — каждая
  на свою сторону (T-0049);
* `set_mixed()` — у выделенных фото разные значения: «—»;
* сигналы: `changed(value)` — во время движения, `committed()` — отпустили/ввели.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QSizePolicy, QSlider, QWidget

from ...core.filters.base import ParamSpec
from ..i18n import tr

POS = 1000   # позиция ползунка: −POS…+POS (или 0…POS для диапазонов без нуля внутри)
_R = 7       # радиус ручки и отступ линии от краёв


class GradientSlider(QSlider):
    """Ползунок по «позиции»; перевод позиция ↔ значение задаёт владелец (ParamRow)."""

    reset_requested = Signal()
    moved_to = Signal(int)   # новая позиция от мыши/колеса

    def __init__(self, left: str | None, right: str | None, theme):
        super().__init__(Qt.Horizontal)
        self.left, self.right, self.theme = left, right, theme
        self.sensitivity = 1.0                         # шагов значения на щелчок колеса (T-0035)
        self.mixed = False
        self.to_value: Callable[[int], float] = float
        self.to_pos: Callable[[float], int] = lambda v: int(round(v))
        self.step_value = 1.0
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # --- геометрия ---------------------------------------------------------------------
    def _groove(self) -> QRectF:
        return QRectF(_R, self.height() / 2 - 3, self.width() - 2 * _R, 6)

    def _pos_at(self, x: float) -> int:
        g = self._groove()
        t = min(1.0, max(0.0, (x - g.left()) / max(1.0, g.width())))
        return int(round(self.minimum() + t * (self.maximum() - self.minimum())))

    def _x_of(self, pos: int) -> float:
        g = self._groove()
        return g.left() + g.width() * (pos - self.minimum()) / max(1, self.maximum() - self.minimum())

    @staticmethod
    def _snap(mods) -> float | None:
        if mods & Qt.ShiftModifier:
            return 20.0
        if mods & Qt.ControlModifier:
            return 10.0
        return None

    # --- мышь и колесо ------------------------------------------------------------------
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.RightButton:   # правый клик — сброс параметра (T-0056)
            self.reset_requested.emit()
            return
        if e.button() != Qt.LeftButton:
            return
        self.setSliderDown(True)       # sliderPressed
        target = self._pos_at(e.position().x())
        step = self._snap(e.modifiers())
        if step is None:
            self.moved_to.emit(target)                  # в точку клика
        else:                                           # шаг 10/20 в сторону клика
            v = self.to_value(self.value())
            tv = self.to_value(target)
            if abs(tv - v) > 1e-9:
                self.moved_to.emit(self.to_pos(v + (step if tv > v else -step)))

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if not self.isSliderDown():
            return
        pos = self._pos_at(e.position().x())
        step = self._snap(e.modifiers())
        if step is not None:  # прилипание к шагу 10/20 при перетаскивании
            pos = self.to_pos(round(self.to_value(pos) / step) * step)
        self.moved_to.emit(pos)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self.isSliderDown():
            self.setSliderDown(False)  # sliderReleased -> фиксация шага отмены

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.reset_requested.emit()

    def wheelEvent(self, e) -> None:  # noqa: N802
        step = self._snap(e.modifiers()) or self.step_value * self.sensitivity
        v = self.to_value(self.value()) + (step if e.angleDelta().y() > 0 else -step)
        self.moved_to.emit(self.to_pos(v))
        e.accept()

    # --- отрисовка --------------------------------------------------------------------
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h = self.height()
        groove = self._groove()
        path = QPainterPath()
        path.addRoundedRect(groove, 3, 3)
        if self.left and self.right:
            g = QLinearGradient(groove.left(), 0, groove.right(), 0)
            g.setColorAt(0, QColor(self.left))
            g.setColorAt(1, QColor(self.right))
            p.fillPath(path, g)
        else:
            p.fillPath(path, QColor(self.theme["border"]))
        if self.minimum() < 0 < self.maximum():   # нулевая отметка
            x0 = self._x_of(0)
            p.setPen(QPen(QColor(self.theme["text"]), 1))
            p.drawLine(int(x0), int(groove.top()) - 3, int(x0), int(groove.bottom()) + 3)
        if not self.mixed:
            cx = self._x_of(self.value())
            p.setPen(QPen(QColor(self.theme["accent"]), 2))
            p.setBrush(QColor(self.theme["text"]))
            p.drawEllipse(QRectF(cx - _R + 1, h / 2 - _R + 1, 2 * _R - 2, 2 * _R - 2))
        p.end()


class ParamRow(QWidget):
    changed = Signal(float)
    committed = Signal()

    def __init__(self, spec: ParamSpec, theme, label_pt: float = 9.0, label: str | None = None):
        super().__init__()
        self.spec = spec
        self._block = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        self.label = QLabel(label if label is not None else tr(spec.label))
        f = self.label.font()
        f.setPointSizeF(label_pt)
        self.label.setFont(f)
        grad = spec.gradient or (None, None)
        self.slider = GradientSlider(grad[0], grad[1], theme)
        # позиция ↔ значение: при нуле внутри диапазона каждая половина шкалы — на свою сторону
        lo, hi = float(spec.min), float(spec.max)
        if lo < 0 < hi:
            self.slider.setRange(-POS, POS)
            self._to_value = lambda pos: (pos / POS) * (hi if pos >= 0 else -lo)
            self._to_pos = lambda v: int(round(POS * (v / hi if v >= 0 else v / -lo)))
        else:
            self.slider.setRange(0, POS)
            self._to_value = lambda pos: lo + (hi - lo) * pos / POS
            self._to_pos = lambda v: int(round(POS * (v - lo) / (hi - lo)))
        self.slider.to_value = lambda pos: self._quant(self._to_value(pos))
        self.slider.to_pos = lambda v: self._to_pos(min(hi, max(lo, v)))
        self.slider.step_value = spec.step
        self.spin = QDoubleSpinBox()
        self.spin.setRange(spec.min, spec.max)
        self.spin.setDecimals(1 if spec.step < 1 else 0)
        self.spin.setSingleStep(spec.step)
        self.spin.setFixedWidth(64)
        self.spin.setKeyboardTracking(False)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setSpecialValueText("")
        lay.addWidget(self.label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.set_value(float(spec.default))

        self.slider.moved_to.connect(self._from_slider)
        self.slider.sliderReleased.connect(self.committed)
        self.slider.reset_requested.connect(self.reset)
        self.spin.valueChanged.connect(self._from_spin)
        self.spin.editingFinished.connect(self.committed)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.RightButton:   # правый клик по подписи строки — тоже сброс
            self.reset()
            return
        super().mousePressEvent(e)

    def _quant(self, v: float) -> float:
        step = self.spec.step
        return round(round(v / step) * step, 6)

    def set_label_width(self, w: int) -> None:
        """Одинаковая ширина подписей в панели -> одинаковая длина линий (T-0048)."""
        self.label.setFixedWidth(w)

    def value(self) -> float:
        return self.spin.value()

    def set_value(self, v: float) -> None:
        self._block = True
        self.slider.mixed = False
        self.spin.setSpecialValueText("")
        self.slider.setValue(self._to_pos(v))
        self.spin.setValue(v)
        self.slider.update()
        self._block = False

    def set_mixed(self) -> None:
        """Разные значения у выделенных фото."""
        self._block = True
        self.slider.mixed = True
        self.spin.setSpecialValueText("—")
        self.spin.setValue(self.spin.minimum())
        self.slider.update()
        self._block = False

    def reset(self) -> None:
        self.set_value(float(self.spec.default))
        self.changed.emit(float(self.spec.default))
        self.committed.emit()

    def _from_slider(self, pos: int) -> None:
        if self._block:
            return
        v = min(self.spec.max, max(self.spec.min, self._quant(self._to_value(pos))))
        if not self.slider.mixed and abs(v - self.spin.value()) < 1e-9:
            return
        self._block = True
        self.spin.setSpecialValueText("")
        self.slider.mixed = False
        self.slider.setValue(self._to_pos(v))
        self.spin.setValue(v)
        self.slider.update()
        self._block = False
        self.changed.emit(v)

    def _from_spin(self, v: float) -> None:
        if self._block:
            return
        self._block = True
        self.slider.mixed = False
        self.slider.setValue(self._to_pos(v))
        self.slider.update()
        self._block = False
        self.changed.emit(v)
