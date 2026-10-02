"""Списки фото: лента кадров, лента референсов, дерево сцен (UI-DESIGN §1–2).

Все три синхронизируют выделение с AppState; миниатюры берут из ImageCache (с правками).
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
    QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..imagecache import ImageCache
from ..qtutil import lab_to_qcolor
from ..state import AppState

PID = Qt.UserRole
KIND = Qt.UserRole + 1


class PhotoDelegate(QStyledItemDelegate):
    """Миниатюра (вписанная), подпись, бейджи: ★ референс, ✎ правки, рамка — нет файла."""

    def __init__(self, owner: "PhotoList"):
        super().__init__(owner)
        self.owner = owner

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        s = self.owner.thumb_size
        return QSize(int(s * 1.5) + 8, s + 26)

    def paint(self, p: QPainter, option, index) -> None:
        theme = self.owner.theme
        pid = index.data(PID)
        r = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.State_Selected)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme["selection"] if selected else theme["surface"]))
        p.drawRoundedRect(r, 6, 6)
        img_rect = QRect(r.left() + 4, r.top() + 4, r.width() - 8, r.height() - 26)
        img = self.owner.image_for(pid)
        if img is not None and not img.isNull():
            sz = img.size().scaled(img_rect.size(), Qt.KeepAspectRatio)
            target = QRect(0, 0, sz.width(), sz.height())
            target.moveCenter(img_rect.center())
            p.drawImage(target, img)
        else:
            p.setPen(QColor(theme["text_dim"]))
            p.drawText(img_rect, Qt.AlignCenter, "…")
        info = self.owner.info(pid)
        p.setPen(QColor(theme["text"] if selected else theme["text_dim"]))
        name_rect = QRect(r.left() + 4, r.bottom() - 20, r.width() - 8, 18)
        p.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(info.get("name", ""), Qt.ElideMiddle, name_rect.width()))
        f = QFont(p.font())
        f.setBold(True)
        p.setFont(f)
        x = img_rect.left() + 3
        badges = (("active", "●", QColor(theme["accent"]), QColor(theme["accent_text"])),
                  ("ref", "★", QColor(theme["accent"]), QColor(theme["accent_text"])),
                  ("edited", "✎", QColor(0, 0, 0, 170), QColor("#ffffff")))
        for flag, text, fill, fg in badges:
            if info.get(flag):
                badge = QRect(x, img_rect.top() + 3, 20, 20)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawEllipse(badge)
                p.setPen(fg)
                p.drawText(badge, Qt.AlignCenter, text)
                x += 23
        if info.get("missing"):
            p.setPen(QPen(QColor(theme["danger"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        elif selected:
            p.setPen(QPen(QColor(theme["accent"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        p.restore()


class PhotoList(QListWidget):
    """Горизонтальная лента миниатюр."""

    def __init__(self, state: AppState, cache: ImageCache, theme, thumb_size: int):
        super().__init__()
        self.state, self.cache, self.theme, self.thumb_size = state, cache, theme, thumb_size
        self._syncing = False
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setItemDelegate(PhotoDelegate(self))
        self.setSpacing(2)
        self.itemSelectionChanged.connect(self._to_state)
        self.state.selection_changed.connect(self._from_state)
        self.state.stacks_changed.connect(lambda ids: self.viewport().update())
        # смена активного референса и прочие изменения состава меняют значки ●/★ — перерисовать
        # сразу, а не при случайной следующей перерисовке (T-0053)
        self.state.workspace_changed.connect(lambda: self.viewport().update())
        cache.thumb_ready.connect(lambda _pid: self.viewport().update())
        cache.ready.connect(lambda _pid: self.viewport().update())
        self._fix_height()

    def _fix_height(self) -> None:
        self.setFixedHeight(self.thumb_size + 26 + 22)

    def set_thumb_size(self, s: int) -> None:
        self.thumb_size = s
        self._fix_height()
        self.doItemsLayout()

    def set_ids(self, ids: list[str]) -> None:
        self._syncing = True
        self.clear()
        for pid in ids:
            it = QListWidgetItem()
            it.setData(PID, pid)
            self.addItem(it)
        self._syncing = False
        self._from_state()

    def ids(self) -> list[str]:
        return [self.item(i).data(PID) for i in range(self.count())]

    def image_for(self, pid: str):
        try:
            _, ph = self.state.ws.locate(pid)
        except KeyError:
            return None
        ws = self.state.ws
        mask, layers = ws.mask(pid), ph.active_layers()
        overlay = getattr(self, "mask_overlay", None)   # (цвета, прозрачность) — показывать маски (T-0065)
        if mask is not None and not (layers or overlay or mask[..., 8].any()):
            mask = None                                   # маска ни на что не влияет — обычная миниатюра
        return self.cache.thumb(pid, ws.abs_path(ph), ph.stack, layers, mask, ws._mask_ver.get(pid, 0),
                                overlay if mask is not None else None)

    def info(self, pid: str) -> dict:
        try:
            _, ph = self.state.ws.locate(pid)
        except KeyError:
            return {}
        return {"name": ph.name, "ref": pid in self.state.ws.ref_ids(), "active": pid == self.state.ws.active_ref,
                "edited": not ph.stack.is_identity() or bool(ph.active_layers()), "missing": pid in self.cache.errors}

    def _to_state(self) -> None:
        if self._syncing:
            return
        rows = sorted(self.row(it) for it in self.selectedItems())       # визуальный порядок
        ids = [self.item(r).data(PID) for r in rows]
        cur_item = self.currentItem()
        cur = cur_item.data(PID) if cur_item is not None and cur_item.isSelected() else None
        # своё же выделение не возвращаем себе эхом: иначе сбивается якорь Shift и шаг стрелок (T-0044)
        self._syncing = True
        try:
            self.state.select(ids, cur)
        finally:
            self._syncing = False

    def _from_state(self) -> None:
        """Выделение пришло извне (другая панель) — применить одной операцией."""
        if self._syncing:
            return
        self._syncing = True
        try:
            sel = set(self.state.selection)
            selection = QItemSelection()
            current_row = -1
            for i in range(self.count()):
                pid = self.item(i).data(PID)
                if pid in sel:
                    idx = self.model().index(i, 0)
                    selection.select(idx, idx)
                if pid == self.state.current:
                    current_row = i
            model = self.selectionModel()
            model.select(selection, QItemSelectionModel.ClearAndSelect)
            if current_row >= 0:
                model.setCurrentIndex(self.model().index(current_row, 0), QItemSelectionModel.NoUpdate)
                self.scrollToItem(self.item(current_row))
        finally:
            self._syncing = False


class RefList(PhotoList):
    """Лента референсов (T-0050): клик — сделать референс активным (применяется авто-балансом);
    двойной клик — открыть референс для правки. Выделение ленты = активный референс."""

    def __init__(self, state: AppState, cache: ImageCache, theme, thumb_size: int):
        super().__init__(state, cache, theme, thumb_size)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        state.workspace_changed.connect(self._from_state)

    def info(self, pid: str) -> dict:
        d = super().info(pid)
        d["ref"] = False  # в ленте референсов звёздочка лишняя — все они референсы
        if d.get("active"):
            d["name"] = "● " + d["name"]
        return d

    def _to_state(self) -> None:
        if self._syncing:
            return
        items = self.selectedItems()
        if items:
            pid = items[0].data(PID)
            if pid != self.state.ws.active_ref:
                self.state.mutate(tr("Активный референс"), lambda ws: ws.set_active(pid))

    def _from_state(self) -> None:
        self._syncing = True
        try:
            for i in range(self.count()):
                it = self.item(i)
                it.setSelected(it.data(PID) == self.state.ws.active_ref)
        finally:
            self._syncing = False
        self.viewport().update()


class TopStrip(QWidget):
    """Верхняя лента (всегда видна, T-0037): слева — референсы (узкий блок по их числу,
    обычно 1–2) с плашками целевого профиля, справа — кадры текущей сцены."""

    MAX_REF_COLUMNS = 3  # шире — прокрутка

    def __init__(self, state: AppState, cache: ImageCache, theme, ref_thumb: int, film_thumb: int):
        super().__init__()
        self.setObjectName("Panel")
        self.state, self.theme = state, theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(8)

        head = QVBoxLayout()
        self.title = QLabel(tr("РЕФЕРЕНСЫ"))
        self.title.setObjectName("Accent")
        self.scope = QLabel("")
        self.scope.setObjectName("Dim")
        self.scope.setWordWrap(True)
        self.scope.setMaximumWidth(140)
        head.addWidget(self.title)
        head.addWidget(self.scope)
        head.addStretch(1)
        self.swatches = ProfileSwatches(theme)
        head.addWidget(self.swatches)
        lay.addLayout(head)

        self.list = RefList(state, cache, theme, ref_thumb)
        self.list.setToolTip(tr("Клик — сделать активным (по нему идёт авто-баланс). Двойной клик — открыть для правки."))
        lay.addWidget(self.list)
        self.empty = QLabel(tr("Нет референсов.\nВыделите фото →\n★ Референс (Ctrl+R)"))
        self.empty.setObjectName("Dim")
        self.empty.setFixedWidth(150)
        lay.addWidget(self.empty)

        sep = QFrame()
        sep.setObjectName("VSeparator")
        sep.setFrameShape(QFrame.VLine)
        lay.addWidget(sep)

        film_box = QVBoxLayout()
        film_box.setSpacing(2)
        self.scene_title = QLabel(tr("КАДРЫ СЦЕНЫ"))
        self.scene_title.setObjectName("Accent")
        film_box.addWidget(self.scene_title)
        self.film = PhotoList(state, cache, theme, film_thumb)
        film_box.addWidget(self.film)
        lay.addLayout(film_box, 1)
        self._fit_refs()

    def set_refs(self, ids: list[str], scope: str) -> None:
        self.scope.setText(scope)
        self.list.set_ids(ids)
        self.list.setVisible(bool(ids))
        self.empty.setVisible(not ids)
        self._fit_refs()

    def set_scene(self, name: str | None) -> None:
        self.scene_title.setText(tr("КАДРЫ СЦЕНЫ: {name}", name=name) if name else tr("КАДРЫ СЦЕНЫ"))

    def set_title(self, text: str) -> None:
        self.scene_title.setText(text)

    def set_thumb_sizes(self, ref_thumb: int, film_thumb: int) -> None:
        self.list.set_thumb_size(ref_thumb)
        self.film.set_thumb_size(film_thumb)
        self._fit_refs()

    def _fit_refs(self) -> None:
        """Ширина блока референсов — по числу миниатюр (не больше MAX_REF_COLUMNS)."""
        n = max(1, min(self.list.count(), self.MAX_REF_COLUMNS))
        item_w = int(self.list.thumb_size * 1.5) + 8 + 2 * self.list.spacing()
        self.list.setFixedWidth(n * item_w + 2 * self.list.frameWidth() + 6)


class ProfileSwatches(QWidget):
    """Три плашки (тени/середины/света нейтралей целевого профиля) + насыщенность."""

    def __init__(self, theme):
        super().__init__()
        self.theme = theme
        self.profile = None
        self.setFixedSize(132, 40)
        self.setToolTip(tr("Целевой профиль референсов: оттенок нейтралей в тенях, серединах и светах; насыщенность"))

    def set_profile(self, prof) -> None:
        self.profile = prof
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.profile is None:
            p.setPen(QColor(self.theme["text_dim"]))
            p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, tr("профиль: —"))
            return
        for i, name in enumerate(("shadows", "midtones", "highlights")):
            b = self.profile.bands[name]
            # светлоту плашки фиксируем — показываем именно оттенок
            color = lab_to_qcolor((30, 55, 80)[i], b.a * 2.5, b.b * 2.5)
            p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.setBrush(color)
            p.drawRoundedRect(QRect(i * 26, 2, 22, 22), 4, 4)
        p.setPen(QColor(self.theme["text_dim"]))
        p.drawText(QRect(80, 2, 52, 22), Qt.AlignLeft | Qt.AlignVCenter, f"S {100 * self.profile.S_p50:.0f}")
        p.drawText(QRect(0, 24, 132, 16), Qt.AlignLeft | Qt.AlignVCenter, tr("тени · сер. · света"))


class WorkspaceTree(QTreeWidget):
    """Дерево: «Все фото» → сцены (подпапки) → фото (T-0055).

    Клик по корню или сцене выбирает, что показывать в верхней ленте (`scope_requested`:
    None — все фото, иначе id сцены), и НЕ меняет выделение — чтобы случайно не править
    группу. Выделение фото — мультивыделение, как раньше.
    """

    scope_requested = Signal(object)
    photo_clicked = Signal(str)       # клик по фото — лента должна его показывать

    def __init__(self, state: AppState, cache: ImageCache, theme):
        super().__init__()
        self.state, self.cache, self.theme = state, cache, theme
        self._syncing = False
        self._scope: str | None = None
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.setIndentation(14)
        self.itemSelectionChanged.connect(self._to_state)
        self.itemClicked.connect(self._on_clicked)
        state.workspace_changed.connect(self.rebuild)
        state.selection_changed.connect(self._from_state)
        state.stacks_changed.connect(self._refresh_marks)
        cache.failed.connect(lambda *_: self._refresh_marks())

    # --- структура ------------------------------------------------------------------
    def _root(self) -> QTreeWidgetItem | None:
        return self.topLevelItem(0) if self.topLevelItemCount() else None

    def _scene_items(self) -> list[QTreeWidgetItem]:
        root = self._root()
        return [root.child(i) for i in range(root.childCount())] if root else []

    def _photo_items(self):
        for sc in self._scene_items():
            for j in range(sc.childCount()):
                yield sc.child(j)

    def rebuild(self) -> None:
        self._syncing = True
        collapsed = {it.data(0, PID) for it in self._scene_items() if not it.isExpanded()}
        self.clear()
        ws = self.state.ws
        total = sum(len(sc.photos) for sc in ws.scenes)
        root = QTreeWidgetItem([tr("Все фото  ({n})", n=total)])
        root.setData(0, KIND, "root")
        root.setToolTip(0, tr("Показать в ленте все фото из всех подпапок"))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        self.addTopLevelItem(root)
        for sc in ws.scenes:
            top = QTreeWidgetItem([f"{sc.name}  ({len(sc.photos)})"])
            top.setData(0, PID, sc.id)
            top.setData(0, KIND, "scene")
            top.setToolTip(0, tr("Показать в ленте кадры этой папки"))
            top.setFont(0, f)
            for ph in sc.photos:
                it = QTreeWidgetItem([ph.name])
                it.setData(0, PID, ph.id)
                it.setData(0, KIND, "photo")
                top.addChild(it)
            root.addChild(top)
            top.setExpanded(sc.id not in collapsed)
        root.setExpanded(True)
        self._syncing = False
        self._refresh_marks()
        self.set_scope(self._scope)
        self._from_state()

    def set_scope(self, scope: str | None) -> None:
        """Подсветить узел, который сейчас показан в ленте (корень или сцену)."""
        self._scope = scope
        root = self._root()
        if root is None:
            return
        accent, text = QColor(self.theme["accent"]), QColor(self.theme["text"])
        root.setForeground(0, accent if scope is None else text)
        for it in self._scene_items():
            it.setForeground(0, accent if it.data(0, PID) == scope else text)

    def _on_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        kind = item.data(0, KIND)
        if kind == "root":
            self.scope_requested.emit(None)
        elif kind == "scene":
            self.scope_requested.emit(item.data(0, PID))
        elif kind == "photo":
            self.photo_clicked.emit(item.data(0, PID))

    def _refresh_marks(self, _ids=None) -> None:
        refs = self.state.ws.ref_ids()
        for it in self._photo_items():
            pid = it.data(0, PID)
            try:
                _, ph = self.state.ws.locate(pid)
            except KeyError:
                continue
            ref_mark = ("● " if pid == self.state.ws.active_ref else "★ ") if pid in refs else ""
            mark = ref_mark + ("✎ " if not ph.stack.is_identity() else "")
            it.setText(0, mark + ph.name)
            it.setForeground(0, QColor(self.theme["danger"] if pid in self.cache.errors else self.theme["text"]))
            it.setToolTip(0, self.cache.errors.get(pid, ph.path))

    # --- выделение ------------------------------------------------------------------
    def _to_state(self) -> None:
        if self._syncing:
            return
        items = [it for it in self.selectedItems() if it.data(0, KIND) == "photo"]
        if not items:
            return  # клик по корню/сцене — это выбор ленты, а не выделение
        ids = [it.data(0, PID) for it in items]
        cur = self.currentItem()
        current = cur.data(0, PID) if cur is not None and cur.data(0, KIND) == "photo" and cur.isSelected() else None
        order = {ph.id: n for n, ph in enumerate(self.state.ws.all_photos())}
        ids = sorted(dict.fromkeys(ids), key=lambda i: order.get(i, 0))       # визуальный порядок
        self._syncing = True  # без эха собственного выделения (T-0044)
        try:
            self.state.select(ids, current)
        finally:
            self._syncing = False

    def _from_state(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            sel = set(self.state.selection)
            selection = QItemSelection()
            current_idx = None
            for it in self._photo_items():
                idx = self.indexFromItem(it)
                if it.data(0, PID) in sel:
                    selection.select(idx, idx)
                if it.data(0, PID) == self.state.current:
                    current_idx = idx
            self.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            if current_idx is not None:
                self.selectionModel().setCurrentIndex(current_idx, QItemSelectionModel.NoUpdate)
                self.scrollTo(current_idx)
        finally:
            self._syncing = False
