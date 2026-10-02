"""Просмотрщик: масштаб колесом, панорамирование, «вписать»/100% двойным кликом,
правый клик — вернуть исходный вид (вписать, по центру) (T-0057),
шторка «до/после» (перетаскивается мышью), подписи режимов.
Режим рисования масок (T-0059): ЛКМ — кисть, Alt+ЛКМ — ластик, средняя кнопка — панорама,
контур кисти под курсором; маска — отдельный полупрозрачный слой поверх кадра.

Сам кадр собирает контроллер (главное окно) и отдаёт готовый QImage; просмотрщик
только показывает и сообщает о перемещении шторки (`split_moved`).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class Viewer(QGraphicsView):
    split_moved = Signal(float)   # доля ширины 0..1
    zoom_changed = Signal(float)
    rect_picked = Signal(QRectF)  # режим пипетки: обведённый прямоугольник (координаты кадра)
    stroke_started = Signal(QPointF, bool)   # режим масок: точка кадра, ластик (Alt)
    stroke_moved = Signal(QPointF)
    stroke_ended = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        # масштабированная под экран картинка кешируется: перерисовки (шторка, рамки, подписи) не
        # пересчитывают сглаживание всего кадра (T-0044)
        self._item.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self._scene.addItem(self._item)
        self._mask_item = QGraphicsPixmapItem()   # оверлей масок (T-0059), масштабируется на кадр
        self._mask_item.setTransformationMode(Qt.SmoothTransformation)
        self._mask_item.setZValue(1)
        self._scene.addItem(self._mask_item)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        # полосы прокрутки не нужны: вокруг кадра — поля сцены, панорама — перетаскиванием (T-0067)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self._fit = True
        self.split: float | None = None      # доля; None — шторки нет
        self.labels: tuple[str, str] | None = None
        self.message = ""
        self._dragging_split = False
        self.accent = QColor("#5fd4b4")
        self.overlays: list[tuple[QRectF, QColor, str]] = []   # рамки в координатах кадра
        self.badge = ""   # постоянная плашка в углу (напр. прогресс авто-баланса)
        self.pick_mode = False
        self._pick_start: QPointF | None = None
        self._pick_rect: QRectF | None = None
        self.paint_mode = False
        self.brush_px = 0.0                   # диаметр кисти в пикселях кадра (для контура)
        self.brush_erase = False              # ластик выбран в панели
        self._cursor: QPointF | None = None   # позиция курсора (вьюпорт) для контура кисти
        self._stroking = False
        self._pan_from: QPointF | None = None
        self.viewport().setMouseTracking(True)

    def set_paint_mode(self, on: bool) -> None:
        self.paint_mode = on
        self._stroking = False
        self.setDragMode(QGraphicsView.NoDrag if on or self.pick_mode else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on or self.pick_mode else Qt.OpenHandCursor)
        self.viewport().update()

    def set_mask_image(self, img: QImage | None) -> None:
        """Оверлей маски (RGBA любого размера) — растягивается на весь кадр."""
        if img is None:
            self._mask_item.setPixmap(QPixmap())
            return
        pm = QPixmap.fromImage(img)
        self._mask_item.setPixmap(pm)
        frame = self._item.pixmap()
        if not frame.isNull() and pm.width() and pm.height():
            self._mask_item.setTransform(QTransform.fromScale(frame.width() / pm.width(), frame.height() / pm.height()))

    def frame_size(self) -> tuple[int, int]:
        pm = self._item.pixmap()
        return pm.width(), pm.height()

    def set_pick_mode(self, on: bool) -> None:
        self.pick_mode = on
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)

    # --- содержимое -----------------------------------------------------------------
    def set_image(self, img: QImage | None) -> None:
        had = not self._item.pixmap().isNull()
        old = self._item.pixmap().size()
        self._item.setPixmap(QPixmap.fromImage(img) if img is not None else QPixmap())
        # поля вокруг кадра: точка под курсором может оказаться в любом месте окна — зум
        # колесом держит её на месте при любом масштабе (T-0067)
        r = QRectF(self._item.pixmap().rect())
        m = max(r.width(), r.height()) * 4
        self._scene.setSceneRect(r.adjusted(-m, -m, m, m))
        mpm = self._mask_item.pixmap()
        if not mpm.isNull() and img is not None and mpm.width() and mpm.height():
            self._mask_item.setTransform(QTransform.fromScale(img.width() / mpm.width(), img.height() / mpm.height()))
        if self._fit or not had or old != self._item.pixmap().size():
            self.fit()
        self.viewport().update()

    def set_background(self, brush: QBrush) -> None:
        self.setBackgroundBrush(brush)

    def set_message(self, text: str) -> None:
        self.message = text
        self.viewport().update()

    # --- масштаб --------------------------------------------------------------------
    def fit(self) -> None:
        if self._item.pixmap().isNull():
            return
        self._fit = True
        self.fitInView(self._item, Qt.KeepAspectRatio)
        self.zoom_changed.emit(self.transform().m11())

    def zoom_100(self) -> None:
        self._fit = False
        self.resetTransform()
        self.centerOn(self._item)
        self.zoom_changed.emit(1.0)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self._fit:
            self.fit()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if self._item.pixmap().isNull():
            return
        # с Alt Windows/Qt отдают прокрутку по горизонтали — учитываем обе оси (T-0072)
        delta = e.angleDelta().y() or e.angleDelta().x()
        if not delta:
            return
        f = 1.15 if delta > 0 else 1 / 1.15
        s = self.transform().m11() * f
        if not 0.02 < s < 32:
            return
        # зум под курсором: точка кадра под мышью остаётся под мышью (T-0067)
        pos = e.position()
        before = self.mapToScene(pos.toPoint())
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.scale(f, f)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        shift = self.mapFromScene(before) - pos.toPoint()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + shift.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + shift.y())
        self._fit = False
        self.zoom_changed.emit(s)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.paint_mode:   # быстрые клики кистью — это мазки, а не «вписать/100%»
            self.mousePressEvent(e)
            return
        if self._fit:
            self.zoom_100()
        else:
            self.fit()

    # --- шторка ---------------------------------------------------------------------
    def _split_x(self) -> float | None:
        if self.split is None or self._item.pixmap().isNull():
            return None
        x = self._item.pixmap().width() * self.split
        return self.mapFromScene(QPointF(x, 0)).x()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.RightButton:   # правый клик — исходный размер и положение (вписать) (T-0057)
            self.fit()
            return
        if self.paint_mode and e.button() == Qt.MiddleButton:
            self._pan_from = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if self.paint_mode and e.button() == Qt.LeftButton:
            if not self._item.pixmap().isNull():
                self._stroking = True
                erase = self.brush_erase or bool(e.modifiers() & Qt.AltModifier)
                self.stroke_started.emit(self.mapToScene(e.position().toPoint()), erase)
            return
        if self.pick_mode and e.button() == Qt.LeftButton:
            self._pick_start = self.mapToScene(e.position().toPoint())
            self._pick_rect = QRectF(self._pick_start, self._pick_start)
            return
        sx = self._split_x()
        if sx is not None and abs(e.position().x() - sx) < 8 and e.button() == Qt.LeftButton:
            self._dragging_split = True
            self.setDragMode(QGraphicsView.NoDrag)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.paint_mode:
            self._cursor = e.position()
            if self._pan_from is not None:
                d = e.position() - self._pan_from
                self._pan_from = e.position()
                self._fit = False
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            elif self._stroking:
                self.stroke_moved.emit(self.mapToScene(e.position().toPoint()))
            self.viewport().update()
            return
        if self.pick_mode and self._pick_start is not None:
            self._pick_rect = QRectF(self._pick_start, self.mapToScene(e.position().toPoint())).normalized()
            self.viewport().update()
            return
        sx = self._split_x()
        if self._dragging_split:
            pt = self.mapToScene(e.position().toPoint())
            w = max(1, self._item.pixmap().width())
            self.split = min(1.0, max(0.0, pt.x() / w))
            self.split_moved.emit(self.split)
            return
        if sx is not None:
            self.viewport().setCursor(Qt.SplitHCursor if abs(e.position().x() - sx) < 8 else Qt.OpenHandCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self.paint_mode:
            if e.button() == Qt.MiddleButton:
                self._pan_from = None
                self.viewport().setCursor(Qt.CrossCursor)
            elif e.button() == Qt.LeftButton and self._stroking:
                self._stroking = False
                self.stroke_ended.emit()
            return
        if self.pick_mode and self._pick_start is not None:
            rect = self._pick_rect
            self._pick_start, self._pick_rect = None, None
            self.viewport().update()
            if rect is not None and rect.width() > 3 and rect.height() > 3:
                self.rect_picked.emit(rect.intersected(QRectF(self._item.pixmap().rect())))
            return
        if self._dragging_split:
            self._dragging_split = False
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._cursor = None
        self.viewport().update()
        super().leaveEvent(e)

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:  # noqa: N802
        p.save()
        p.resetTransform()
        vw, vh = self.viewport().width(), self.viewport().height()
        if self.paint_mode and self._cursor is not None and self.brush_px > 0:
            r = self.brush_px * self.transform().m11() / 2
            p.setBrush(Qt.NoBrush)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(0, 0, 0, 170), 3))
            p.drawEllipse(self._cursor, r, r)
            p.setPen(QPen(QColor(255, 255, 255, 220), 1, Qt.DashLine if self.brush_erase else Qt.SolidLine))
            p.drawEllipse(self._cursor, r, r)
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        sx = self._split_x()
        if sx is not None:
            p.setPen(QPen(self.accent, 2))
            p.drawLine(int(sx), 0, int(sx), vh)
            p.setBrush(self.accent)
            p.drawEllipse(QPointF(sx, vh / 2), 7, 7)
        picking = [(self._pick_rect, self.accent, "")] if self._pick_rect is not None else []
        for rect, color, text in self.overlays + picking:
            vr = QRectF(self.mapFromScene(rect.topLeft()), self.mapFromScene(rect.bottomRight()))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 160), 4))
            p.drawRect(vr)
            p.setPen(QPen(color, 2))
            p.drawRect(vr)
            if text:
                self._tag(p, vr.left(), vr.top() - 26, text)
        if self.labels and not self._item.pixmap().isNull():
            left, right = self.labels
            if left:
                self._tag(p, 10, 10, left)
            if right:
                self._tag(p, vw - p.fontMetrics().horizontalAdvance(right) - 26, 10, right)
        if self.badge:
            self._tag(p, 10, vh - p.fontMetrics().height() - 18, self.badge)
        if self.message:
            fm = p.fontMetrics()
            self._tag(p, (vw - fm.horizontalAdvance(self.message)) / 2 - 8, vh / 2 - 12, self.message)
        p.restore()

    @staticmethod
    def _tag(p: QPainter, x: float, y: float, text: str) -> None:
        fm = p.fontMetrics()
        r = QRectF(x, y, fm.horizontalAdvance(text) + 16, fm.height() + 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor("#ffffff"))
        p.drawText(r, Qt.AlignCenter, text)
