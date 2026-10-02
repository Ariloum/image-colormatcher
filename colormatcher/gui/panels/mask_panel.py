"""Панель масок (T-0060): палитра меток, кисть/ластик, параметры кисти, оверлей,
список масок текущего фото с площадью на кадре и референсе и удалением.

Показывается над «Настройками фото» только в режиме «Маски». Сама панель маски не меняет —
сообщает выбор сигналами; удаление метки идёт через AppState (шаг отмены).
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ...models import masks as M
from ..i18n import tr
from ..qtutil import rgba_qimage, swatch_icon, tool_icon
from ..settings import AppSettings
from ..state import AppState
from ..widgets.slider import GradientSlider


ROW_H = 24   # высота строки списка масок


def slot_name(slot: int) -> str:
    if slot in M.SLOT_NAMES:
        return tr(M.SLOT_NAMES[slot])
    return tr("Метка {n}", n=slot + 1)


def slot_icon(slot: int, colors: list[str], size: int = 16):
    pattern = {M.IGNORE: "hatch", M.KEEP: "cross"}.get(slot, "")
    return swatch_icon(colors[slot], size, pattern)


class _Slider(QWidget):
    """Ползунок с подписью значения на общем GradientSlider (T-0073): клик — значение в точку,
    Ctrl/Shift — шаг 10/20, колесо, правый/двойной клик — сброс к `default`.
    Позиция — целое (px или %); `scale` — множитель позиция -> значение."""

    changed = Signal(float)

    def __init__(self, lo: int, hi: int, value: float, fmt: str, theme, scale: float = 1.0, default: float = 0.0):
        super().__init__()
        self.scale, self.fmt, self.default = scale, fmt, default
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = GradientSlider(None, None, theme)
        self.slider.setRange(lo, hi)
        self.slider.moved_to.connect(lambda pos: self.slider.setValue(min(hi, max(lo, pos))))
        self.slider.reset_requested.connect(lambda: self.set_value(self.default))
        self.label = QLabel()
        self.label.setMinimumWidth(48)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._on)
        self.set_value(value)

    def value(self) -> float:
        return self.slider.value() * self.scale

    def set_value(self, v: float) -> None:
        self.slider.setValue(round(v / self.scale))
        self._show()

    def _show(self) -> None:
        self.label.setText(self.fmt.format(self.slider.value()))

    def _on(self, _v: int) -> None:
        self._show()
        self.changed.emit(self.value())


class BrushPreview(QWidget):
    """Превью кисти: отпечаток с текущим распределением и кривая профиля (T-0075)."""

    SIDE = 76

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.brush = M.Brush()
        self.setFixedSize(self.SIDE, self.SIDE)
        self.setToolTip(tr("Профиль кисти: сила от центра к краю"))

    def set_brush(self, brush: M.Brush) -> None:
        self.brush = brush
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        n, b = self.SIDE, self.brush
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        c = (n - 1) / 2
        d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / (c - 2)
        cov = M.brush_profile(d, b.spread, b.hardness) * b.opacity
        ink = QColor(self.theme["text"])
        img = np.zeros((n, n, 4), np.uint8)
        img[..., 0], img[..., 1], img[..., 2] = ink.red(), ink.green(), ink.blue()
        img[..., 3] = np.clip(cov * 255, 0, 255).astype(np.uint8)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(self.theme["surface"]))
        p.drawImage(0, 0, rgba_qimage(img))
        # поперечный профиль: высота = сила
        xs = np.linspace(-1, 1, n)
        ys = M.brush_profile(np.abs(xs), b.spread, b.hardness) * b.opacity
        pts = [QPointF(i, n - 3 - y * (n - 8)) for i, y in enumerate(ys)]
        p.setPen(QPen(QColor(self.theme["accent"]), 1.6))
        p.drawPolyline(pts)
        p.setPen(QPen(QColor(self.theme["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()


class MaskPanel(QWidget):
    slot_changed = Signal(int)       # выбрана метка
    brush_changed = Signal()         # диаметр/жёсткость/непрозрачность/ластик
    overlay_changed = Signal()       # непрозрачность оверлея / «только активная»
    status = Signal(str)

    def __init__(self, state: AppState, settings: AppSettings, theme):
        super().__init__()
        self.setObjectName("Panel")
        self.theme = theme
        self.state, self.settings = state, settings
        self.colors = M.palette_colors(settings.mask_palette)
        self.slot = 0
        self.erase = False
        self.ref_id: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 4)

        # один ряд: кисть, ластик | 7 цветов | «не учитывать», «не трогать» — только иконки (T-0068, T-0069)
        box = QGroupBox(tr("Маски"))
        form = QFormLayout(box)
        row = QHBoxLayout()
        row.setSpacing(2)
        self.b_brush = self._icon_button(tr("Кисть  (Q)"))
        self.b_erase = self._icon_button(tr("Ластик  (E) — стирает все метки под кистью; Alt+ЛКМ — временно"))
        self.b_brush.setChecked(True)
        tg = QButtonGroup(self)
        for b in (self.b_brush, self.b_erase):
            tg.addButton(b)
            row.addWidget(b)
        self.b_brush.clicked.connect(lambda: self.set_erase(False))
        self.b_erase.clicked.connect(lambda: self.set_erase(True))
        row.addSpacing(8)
        self.group = QButtonGroup(self)
        self.buttons: dict[int, QToolButton] = {}
        for slot in range(M.SLOTS):
            if slot == M.COLOR_SLOTS:
                row.addSpacing(8)
            b = self._icon_button(slot_name(slot) + {
                M.IGNORE: tr(" — вне статистики авто-баланса (блики, логотипы, источники света)"),
                M.KEEP: tr(" — коррекция здесь = 0 (фирменные цвета, кожа)")}.get(slot, ""))
            self.group.addButton(b, slot)
            self.buttons[slot] = b
            row.addWidget(b)
        row.addStretch(1)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self._on_slot)
        form.addRow(row)
        s = settings
        self.diameter = _Slider(4, 600, s.brush_diameter, "{} px", theme, default=120)
        # жёсткость и нажим для масок не нужны (T-0075): всегда 0 и 100%, в модели остаются на будущее
        self.spread = _Slider(0, 100, s.brush_spread, "{} %", theme, 0.01, default=0.2)
        self.alpha = _Slider(0, 100, s.mask_alpha, "{} %", theme, 0.01, default=0.5)
        self.spread.setToolTip(tr("Распределение силы по кисти — всегда на весь диаметр:\n"
                                  "0% — мягкое пятно, к краю гаснет до едва видимого;\n"
                                  "100% — почти сплошной диск до самого края.\n"
                                  "Shift+[ / Shift+] — меньше/больше; правый клик — сброс"))
        self.diameter.setToolTip(tr("[ и ] — меньше/больше; размер — в пикселях сетки маски (длинная сторона 1024)"))
        sliders = QFormLayout()
        sliders.setContentsMargins(0, 0, 0, 0)
        sliders.addRow(tr("Диаметр"), self.diameter)
        sliders.addRow(tr("Распределение"), self.spread)
        sliders.addRow(tr("Оверлей"), self.alpha)
        self.preview = BrushPreview(theme)
        brush_row = QHBoxLayout()
        brush_row.addLayout(sliders, 1)
        brush_row.addWidget(self.preview, 0, Qt.AlignTop)
        form.addRow(brush_row)
        self.only = QCheckBox(tr("Показывать только выбранную метку"))
        form.addRow(self.only)
        hint = QLabel(tr("ЛКМ — рисовать, Alt+ЛКМ — стереть, средняя кнопка — двигать, колесо — масштаб под курсором, "
                         "[ ] — размер, Shift+[ ] — распределение, X — реф с масками"))
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        form.addRow(hint)
        for w in (self.diameter, self.spread):
            w.changed.connect(self._brush_edited)
        self.preview.set_brush(self.brush())
        self.alpha.changed.connect(self._alpha_edited)
        self.only.toggled.connect(lambda _on: self.overlay_changed.emit())
        lay.addWidget(box)

        lst = QGroupBox(tr("Маски фото"))
        ll = QVBoxLayout(lst)
        self.list = QListWidget()
        self.list.setMinimumHeight(ROW_H * 4 + 6)
        self.list.setMaximumHeight(ROW_H * 6 + 6)
        self.list.setSelectionMode(QListWidget.NoSelection)
        ll.addWidget(self.list)
        self.empty = QLabel(tr("Масок нет — баланс классический. Выберите цвет и рисуйте по кадру."))
        self.empty.setObjectName("Dim")
        self.empty.setWordWrap(True)
        ll.addWidget(self.empty)
        lay.addWidget(lst)

        state.masks_changed.connect(lambda _pid: self.refresh())
        state.current_changed.connect(lambda _pid: self.refresh())
        state.workspace_changed.connect(self.refresh)
        self._apply_colors()

    # --- палитра / кисть ---------------------------------------------------------------
    def apply_settings(self) -> None:
        self.colors = M.palette_colors(self.settings.mask_palette)
        if abs(self.alpha.value() - self.settings.mask_alpha) > 0.005:
            self.alpha.set_value(self.settings.mask_alpha)
        self._apply_colors()
        self.preview.update()
        self.refresh()

    @staticmethod
    def _icon_button(tip: str) -> QToolButton:
        b = QToolButton()
        b.setCheckable(True)
        b.setAutoRaise(True)
        b.setIconSize(QSize(20, 20))
        b.setFixedSize(30, 30)                               # компактно: ряд из 11 кнопок влезает в панель
        b.setStyleSheet("QToolButton { padding: 0px; }")
        b.setToolTip(tip)
        return b

    def _apply_colors(self) -> None:
        for slot, b in self.buttons.items():
            b.setIcon(slot_icon(slot, self.colors, 20))
        ink = self.palette().color(QPalette.ButtonText)
        self.b_brush.setIcon(tool_icon("brush", ink))
        self.b_erase.setIcon(tool_icon("eraser", ink))

    def brush(self) -> M.Brush:
        return M.Brush(self.diameter.value(), 0.0, 1.0, self.spread.value())

    def _on_slot(self, slot: int) -> None:
        self.slot = slot
        self.set_erase(False)
        self.slot_changed.emit(slot)
        if self.only.isChecked():
            self.overlay_changed.emit()

    def select_slot(self, slot: int) -> None:
        self.buttons[slot].setChecked(True)
        self._on_slot(slot)

    def set_erase(self, on: bool) -> None:
        self.erase = on
        (self.b_erase if on else self.b_brush).setChecked(True)
        self.brush_changed.emit()

    def resize_brush(self, factor: float) -> None:
        self.diameter.set_value(min(600, max(4, round(self.diameter.value() * factor))))

    def change_spread(self, delta: float) -> None:
        self.spread.set_value(min(1.0, max(0.0, self.spread.value() + delta)))

    def _brush_edited(self, _v: float) -> None:
        s = self.settings
        s.brush_diameter, s.brush_spread = self.diameter.value(), self.spread.value()
        self.preview.set_brush(self.brush())
        self.brush_changed.emit()

    def _alpha_edited(self, v: float) -> None:
        self.settings.mask_alpha = v
        self.overlay_changed.emit()

    def only_slot(self) -> int | None:
        return self.slot if self.only.isChecked() else None

    # --- список масок ---------------------------------------------------------------------
    def set_ref(self, ref_id: str | None) -> None:
        self.ref_id = ref_id
        self.refresh()

    def refresh(self) -> None:
        ws, pid = self.state.ws, self.state.current
        self.list.clear()
        if not pid:
            self.empty.setVisible(True)
            return
        frame = M.coverage(ws.mask(pid))
        ref_id = self.ref_id if self.ref_id and self.ref_id != pid else None
        ref = M.coverage(ws.mask(ref_id)) if ref_id else {}
        slots = sorted(set(frame) | set(ref))
        self.empty.setVisible(not slots)
        for slot in slots:
            self._add_row(slot, frame.get(slot), ref.get(slot), bool(ref_id))

    def _add_row(self, slot: int, on_frame: float | None, on_ref: float | None, has_ref: bool) -> None:
        item = QListWidgetItem(self.list)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 0, 2, 0)
        h.setSpacing(4)
        icon = QLabel()
        icon.setPixmap(slot_icon(slot, self.colors, 16).pixmap(16, 16))
        h.addWidget(icon)

        def pct(v: float | None) -> str:
            return "—" if v is None else ("<0.1%" if v < 0.001 else f"{v * 100:.1f}%")

        text = tr("{name}   кадр {area}", name=slot_name(slot), area=pct(on_frame))
        if has_ref:
            text += tr(" · реф {area}", area=pct(on_ref))
        tips = []
        if on_frame is not None and on_frame < M.TINY_AREA:
            text = "⚠ " + text
            tips.append(tr("Очень маленькая область на кадре — возможно, случайный мазок"))
        if on_frame is None:
            tips.append(tr("Метка есть только на референсе — для этого кадра не используется"))
        elif has_ref and on_ref is None and slot < M.COLOR_SLOTS:
            tips.append(tr("На референсе этой метки нет — будет взята та же область референса"))
        label = QLabel(text)
        label.setToolTip("\n".join(tips))
        if on_frame is None:
            label.setObjectName("Dim")
        h.addWidget(label, 1)
        if on_frame is not None:
            pick = QToolButton(text="✎")
            pick.setAutoRaise(True)
            pick.setToolTip(tr("Рисовать этой меткой"))
            pick.clicked.connect(lambda _c=False, s=slot: self.select_slot(s))
            rm = QToolButton(text="✕")
            rm.setAutoRaise(True)
            rm.setToolTip(tr("Удалить метку с этого фото (Ctrl+Z — вернуть)"))
            rm.clicked.connect(lambda _c=False, s=slot: self._remove(s))
            for b in (pick, rm):
                b.setFixedSize(22, 20)
                b.setStyleSheet("padding: 0px; margin: 0px;")
                h.addWidget(b)
        item.setSizeHint(QSize(row.sizeHint().width(), ROW_H))
        self.list.setItemWidget(item, row)

    def _remove(self, slot: int) -> None:
        pid = self.state.current
        if pid:
            self.state.remove_mask_slot(pid, slot)
            self.status.emit(tr("Метка «{name}» удалена с фото", name=slot_name(slot)))
