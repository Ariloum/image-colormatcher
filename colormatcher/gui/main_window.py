"""Главное окно: раскладка (UI-DESIGN §1), живое превью, меню и горячие клавиши,
drag&drop, буфер обмена, файлы воркспейса (T-0022, T-0025, T-0026, T-0027, T-0028);
режим «Маски» — рисование меток кистью (T-0059, T-0060)."""

from __future__ import annotations

import datetime as _dt
import shutil
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QByteArray, QEvent, QMimeData, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QProgressBar, QSplitter, QTabWidget, QToolBar,
    QToolButton, QVBoxLayout, QWidget,
)

from ..core import imageio
from ..core import lut as lutlib
from ..core.imageio import is_image
from ..core.pipeline import bake_stack, render, render_masked
from ..models import Preset
from ..models import masks as M
from ..core.analysis import sample_image
from ..models.profile import AnchorPair, Region
from ..services import export as export_svc
from ..services import importer, matching, workspace_io
from . import theme as themes
from .dialogs import ExportDialog, SettingsDialog
from .imagecache import ImageCache, stack_hash
from .panels.adjustments import AdjustmentsPanel
from .panels.mask_panel import MaskPanel
from .panels.match_panel import MatchPanel
from .panels.presets_panel import PresetsPanel, apply_preset
from .qtutil import from_qimage, mask_rgba, plural, rgba_qimage, scrolled, to_qimage, viewer_brush
from .. import __version__
from . import i18n
from .i18n import tr
from .settings import AppSettings, app_home
from .shortcuts import Registry
from .state import AppState
from .tasks import run_task
from .widgets.photolist import TopStrip, WorkspaceTree
from .widgets.viewer import Viewer

CLIP_PREFIX = "colormatcher-preset:"
LAYOUT_VERSION = 2   # версия раскладки док-панелей: старые сохранённые раскладки игнорируются
VK_OEM_4, VK_OEM_6 = 0xDB, 0xDD   # Windows: физические клавиши [ и ] (не зависят от раскладки)
PEEK_DEFAULTS = {"view.peek_before": ("Z", "before"), "view.peek_ref": ("X", "ref")}   # удержание (T-0039, T-0085)
FILE_PATTERNS = "*.jpg *.jpeg *.png *.tif *.tiff *.webp *.bmp"


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, state: AppState | None = None, cache: ImageCache | None = None):
        """`state` / `cache` — от прежнего окна при смене языка на лету (T-0086)."""
        super().__init__()
        self.settings = settings
        self.theme = themes.ThemeHolder(themes.get(settings.theme))
        self.state = state or AppState()
        self.cache = cache or ImageCache(settings.preview_side)
        self.cache.setParent(self)
        self._closing_for_rebuild = False
        self.view_mode = "single"
        self.split = 0.5
        self._luts: OrderedDict[str, np.ndarray] = OrderedDict()
        self._frames: OrderedDict[str, object] = OrderedDict()  # готовые QImage «фото + правки» (T-0044)
        self._mask_cache: dict = {}   # маска текущего фото под размер прокси (T-0062)
        self._tasks: list = []
        self._side_layout: dict | None = None     # раскладка кадра «рядом с референсом»
        self._pending_ref_rect: tuple | None = None  # пипетка: обведено на референсе, ждём цель
        self.peek: str | None = None               # удержание клавиши: "before" | "ref" | None
        self.film_scope: str | None = None         # что в ленте: None — все фото, иначе id сцены (T-0055)
        self._stroke: dict | None = None           # идущий мазок кисти масок (T-0059)
        self._mask_ov: tuple[str, np.ndarray] | None = None   # (id фото, RGBA оверлея)
        self.setWindowTitle("Image ColorMatcher")
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)

        self._build_central()
        self._build_docks()
        self._build_actions()
        self._build_menus_and_toolbar()
        self.status_msg = QLabel("")
        self.status_counts = QLabel("")
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(120)
        self.busy.setVisible(False)
        self.statusBar().addWidget(self.status_msg, 1)
        self.statusBar().addPermanentWidget(self.busy)
        self.statusBar().addPermanentWidget(self.status_counts)

        self._render_timer = QTimer(self, singleShot=True, interval=0, timeout=self._render_preview)
        self._refs_timer = QTimer(self, singleShot=True, interval=300, timeout=self._update_ref_profile)
        s = self.state
        s.workspace_changed.connect(self._on_structure)
        s.current_changed.connect(self._on_current)
        s.selection_changed.connect(self._update_counts)
        s.selection_changed.connect(lambda: self._follow_current())
        s.stacks_changed.connect(self._on_stacks)
        s.path_changed.connect(self._update_title)
        s.undo.cleanChanged.connect(lambda _c: self._update_title())
        self.cache.ready.connect(self._on_cache_ready)
        self.cache.failed.connect(lambda pid, err: self.say(tr("Не удалось открыть: {error}", error=err)))
        self.match_panel.status.connect(self.say)
        self.match_panel.progress_changed.connect(self._on_match_progress)
        self.presets_panel.status.connect(self.say)
        self.viewer.split_moved.connect(self._on_split)
        self.viewer.rect_picked.connect(self._on_rect_picked)
        self.viewer.stroke_started.connect(self._stroke_start)
        self.viewer.stroke_moved.connect(self._stroke_move)
        self.viewer.stroke_ended.connect(self._stroke_end)
        s.masks_changed.connect(self._on_masks_changed)
        self.mask_panel.status.connect(self.say)
        self.mask_panel.brush_changed.connect(self._update_brush)
        self.mask_panel.overlay_changed.connect(self._refresh_mask_overlay)
        self._autosave_timer = QTimer(self, timeout=self._autosave)
        self._restart_autosave()
        QApplication.instance().installEventFilter(self)  # удержание Z / X — где бы ни был фокус

        self.apply_settings()
        if settings.geometry:
            self.restoreGeometry(QByteArray.fromBase64(settings.geometry.encode()))
        else:
            self.resize(1500, 920)
        self._fit_to_screen()
        if settings.window_state:
            self.restoreState(QByteArray.fromBase64(settings.window_state.encode()), LAYOUT_VERSION)
        self._on_structure()
        self._update_title()
        if state is not None:   # перенесённое состояние: панели показывают текущее фото и выделение
            self.state.selection_changed.emit()
            self.state.current_changed.emit(self.state.current)
            self.state.layer_changed.emit(self.state.active_layer)

    # --- смена языка на лету (T-0086) ------------------------------------------------------------
    def switch_language(self, code: str) -> "MainWindow":
        """Пересобрать окно на другом языке с тем же воркспейсом, Undo, выделением и видом."""
        i18n.load(code)
        self.settings.language = code
        self.settings.geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.window_state = bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode()
        self.settings.save()
        state, cache = self.state, self.cache
        # старое окно больше не слушает состояние и кеш: его виджеты скоро исчезнут
        for obj in (state, state.undo, cache):
            for name in ("workspace_changed", "stacks_changed", "selection_changed", "current_changed", "path_changed",
                         "masks_changed", "layer_changed", "cleanChanged", "indexChanged", "canUndoChanged",
                         "canRedoChanged", "undoTextChanged", "redoTextChanged", "ready", "thumb_ready", "failed"):
                sig = getattr(obj, name, None)
                if sig is not None:
                    try:
                        sig.disconnect()
                    except (RuntimeError, TypeError):
                        pass
        QApplication.instance().removeEventFilter(self)
        self._autosave_timer.stop()
        cache.setParent(None)
        mode, scope, split = self.view_mode, self.film_scope, self.split
        new = MainWindow(self.settings, state=state, cache=cache)
        new.split = split
        new.set_film_scope(scope)
        new.set_view_mode(mode)
        new.setWindowState(self.windowState())
        new.show()
        app = QApplication.instance()
        app._main_window = new          # держим ссылку на новое главное окно
        self._closing_for_rebuild = True
        self.close()
        self.deleteLater()
        new.say(tr("Язык интерфейса: {name}", name=i18n.available().get(code, code)))
        return new

    def _fit_to_screen(self) -> None:
        """Окно не больше доступной области экрана (без панели задач) (T-0079)."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None or self.isMaximized() or self.isFullScreen():
            return
        area = screen.availableGeometry()
        frame = self.frameGeometry()
        extra_w, extra_h = frame.width() - self.width(), frame.height() - self.height()
        w = min(self.width(), area.width() - extra_w)
        h = min(self.height(), area.height() - extra_h)
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        g = self.frameGeometry()
        x = min(max(g.left(), area.left()), area.right() - g.width() + 1)
        y = min(max(g.top(), area.top()), area.bottom() - g.height() + 1)
        if (x, y) != (g.left(), g.top()):
            self.move(x, y)

    # --- построение -------------------------------------------------------------------
    def _build_central(self) -> None:
        self.viewer = Viewer()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.viewer)
        self.setCentralWidget(central)

    def _dock(self, title: str, widget: QWidget, area, name: str) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self) -> None:
        s = self.settings
        # верх: референсы + кадры сцены одним блоком; всегда на месте (T-0037, T-0040)
        self.top = TopStrip(self.state, self.cache, self.theme, s.ref_thumb_size, s.thumb_size)
        self.ref_strip = self.top
        self.film = self.top.film
        self.top.list.itemDoubleClicked.connect(lambda it: self.state.select([it.data(Qt.UserRole)], it.data(Qt.UserRole)))
        self.dock_top = self._dock(tr("Референсы и кадры"), self.top, Qt.TopDockWidgetArea, "top")
        self.dock_top.setFeatures(QDockWidget.NoDockWidgetFeatures)   # не закрыть, не открепить, не сдвинуть
        self.dock_top.setTitleBarWidget(QWidget())                     # заголовки — внутри блока
        self.tree = WorkspaceTree(self.state, self.cache, self.theme)
        self.tree.scope_requested.connect(self.set_film_scope)
        self.tree.photo_clicked.connect(self._follow_current)
        self.dock_tree = self._dock(tr("Воркспейс"), self.tree, Qt.LeftDockWidgetArea, "tree")
        self.adjust = AdjustmentsPanel(self.state, self.theme, s)
        self.match_panel = MatchPanel(self.state)
        self.presets_panel = PresetsPanel(self.state)
        tabs = QTabWidget()
        tabs.addTab(self.adjust, tr("Правки"))   # у панели правок своя прокрутка
        # высокие панели — с прокруткой: окно не должно распирать по высоте (T-0079)
        tabs.addTab(scrolled(self.match_panel), tr("Авто-баланс"))
        tabs.addTab(scrolled(self.presets_panel), tr("Пресеты"))
        self.right_tabs = tabs
        # панель масок — над настройками фото, только в режиме «Маски» (T-0060); граница с вкладками
        # перетаскивается, у панели масок своя прокрутка (T-0079)
        self.mask_panel = MaskPanel(self.state, s, self.theme)
        self.mask_box = scrolled(self.mask_panel)
        self.mask_box.setMinimumHeight(90)
        self.mask_box.setVisible(False)
        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self.mask_box)
        right.addWidget(tabs)
        right.setStretchFactor(0, 0)
        right.setStretchFactor(1, 1)
        self.right_split = right
        self.dock_right = self._dock(tr("Настройки фото"), right, Qt.RightDockWidgetArea, "right")
        self.resizeDocks([self.dock_tree, self.dock_right], [240, 380], Qt.Horizontal)

    def _act(self, text: str, slot, shortcut=None, tip: str = "", checkable: bool = False) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.a_new = A(tr("Новый воркспейс"), self.new_workspace, QKeySequence.New)
        self.a_open = A(tr("Открыть…"), self.open_dialog, QKeySequence.Open)
        self.a_save = A(tr("Сохранить"), self.save, QKeySequence.Save)
        self.a_save_as = A(tr("Сохранить как…"), self.save_as, QKeySequence.SaveAs)
        self.a_pack = A(tr("Упаковать с фото в папку…"), self.pack)
        self.a_export = A(tr("Экспорт…"), self.export_dialog, "Ctrl+E", tr("Сохранить фото с правками"))
        self.a_quit = A(tr("Выход"), self.close, QKeySequence.Quit)
        self.a_undo = self.state.undo.createUndoAction(self, tr("Отменить"))
        self.a_undo.setShortcut(QKeySequence.Undo)
        self.a_redo = self.state.undo.createRedoAction(self, tr("Повторить"))
        self.a_redo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Shift+Z")])
        self.addActions([self.a_undo, self.a_redo])
        self.a_add_files = A(tr("Добавить фото…"), self.add_files_dialog, "Ctrl+I")
        self.a_add_folder = A(tr("Добавить папку (подпапки = сцены)…"), self.add_folder_dialog, "Ctrl+Shift+I")
        self.a_copy = A(tr("Копировать фото"), self.copy_photos, QKeySequence.Copy, tr("Выделенные фото с правками — в буфер как файлы"))
        self.a_paste = A(tr("Вставить фото"), self.paste, QKeySequence.Paste, tr("Файлы или картинку из буфера — в воркспейс"))
        self.a_copy_edits = A(tr("Копировать правки"), self.copy_edits, "Ctrl+Shift+C")
        self.a_paste_edits = A(tr("Вставить правки"), self.paste_edits, "Ctrl+Shift+V")
        self.a_reset = A(tr("Сбросить правки"), self.adjust._reset_all, "Ctrl+Backspace")
        self.a_remove = A(tr("Убрать из воркспейса"), self.remove_selected, QKeySequence.Delete)
        self.a_select_all = A(tr("Выделить все кадры ленты"), self.select_scene, QKeySequence.SelectAll)
        self.a_ref = A(tr("★ Референс: сделать / убрать"), self.toggle_ref, "Ctrl+R",
                       tr("Сделать выделенные референсами (текущее — активным); повторно — убрать"))
        self.a_unref = A(tr("Убрать из референсов"), self.unref)
        self.a_match_sel = A(tr("Авто-баланс выделенных (по активному референсу)"),
                             lambda: self.match_panel.start("selection"), "B")
        self.a_match_scene = A(tr("Авто-баланс сцены"), lambda: self.match_panel.start("scene"))
        self.a_match_all = A(tr("Авто-баланс всех"), lambda: self.match_panel.start("all"))
        self.a_prev = A(tr("Предыдущее фото"), lambda: self.step(-1), "Alt+Left")
        self.a_next = A(tr("Следующее фото"), lambda: self.step(1), "Alt+Right")
        self.a_fit = A(tr("Вписать"), self.viewer.fit, "F")
        self.a_100 = A("100%", self.viewer.zoom_100, "Ctrl+1")
        self.a_settings = A(tr("Настройки…"), self.settings_dialog, "Ctrl+,")
        self.a_anchor = A(tr("◎ Якорь: пара пипеток"), self.toggle_anchor_mode, "A",
                          tr("Обведите объект на референсе, затем тот же объект на текущем фото"), checkable=True)
        self.a_clear_anchors = A(tr("Удалить якоря текущего фото"), self.clear_anchors)
        group = QActionGroup(self)
        self.a_modes = {}
        for key, title, sc in (("single", tr("Одно фото"), "1"), ("masks", tr("Маски: рисование меток"), "M"),
                               ("split", tr("До / после"), "2"), ("side", tr("Рядом с референсом"), "3"),
                               ("neutrals", tr("Что видит пипетка"), "4")):
            a = A(title, lambda _c=False, k=key: self.set_view_mode(k), sc, checkable=True)
            group.addAction(a)
            self.a_modes[key] = a
        self.a_modes["single"].setChecked(True)
        # кисть масок: клавиши действуют только в режиме «Маски»; кисть — Q, B везде — авто-баланс (T-0081)
        mp = self.mask_panel
        self.a_brush = A(tr("Кисть"), lambda: mp.set_erase(False), "Q")
        self.a_eraser = A(tr("Ластик"), lambda: mp.set_erase(True), "E")
        # [ ] и Shift+[ ] ловятся по физической клавише в eventFilter — при любой раскладке (T-0070);
        # в меню клавиша только подписана
        self.a_brush_smaller = A(tr("Кисть меньше") + "\t[", lambda: mp.resize_brush(1 / 1.2))
        self.a_brush_bigger = A(tr("Кисть больше") + "\t]", lambda: mp.resize_brush(1.2))
        self.a_brush_softer = A(tr("Кисть мягче (распределение −10%)") + "\tShift+[", lambda: mp.change_spread(-0.1))
        self.a_brush_harder = A(tr("Кисть плотнее (распределение +10%)") + "\tShift+]", lambda: mp.change_spread(0.1))
        self.mask_actions = [self.a_brush, self.a_eraser, self.a_brush_smaller, self.a_brush_bigger,
                             self.a_brush_softer, self.a_brush_harder]
        for a in self.mask_actions:
            a.setEnabled(False)
        self.tree.addActions([self.a_ref, self.a_unref, self.a_match_sel, self.a_copy_edits,
                              self.a_paste_edits, self.a_reset, self.a_remove])
        self.film.setContextMenuPolicy(Qt.ActionsContextMenu)
        self.film.addActions([self.a_ref, self.a_unref, self.a_match_sel, self.a_copy,
                              self.a_copy_edits, self.a_paste_edits, self.a_reset, self.a_remove])
        self._register_shortcuts()

    def _register_shortcuts(self) -> None:
        """Все действия — в реестр горячих клавиш со стабильными id (T-0085)."""
        r = self.shortcuts = Registry()
        for bid, name in (
                ("file.new", "a_new"), ("file.open", "a_open"), ("file.save", "a_save"), ("file.save_as", "a_save_as"),
                ("file.pack", "a_pack"), ("file.export", "a_export"), ("file.quit", "a_quit"),
                ("edit.undo", "a_undo"), ("edit.redo", "a_redo"), ("edit.copy_photos", "a_copy"),
                ("edit.paste_photos", "a_paste"), ("edit.copy_edits", "a_copy_edits"),
                ("edit.paste_edits", "a_paste_edits"), ("edit.reset", "a_reset"),
                ("photos.add_files", "a_add_files"), ("photos.add_folder", "a_add_folder"),
                ("photos.remove", "a_remove"), ("photos.select_all", "a_select_all"),
                ("refs.toggle", "a_ref"), ("refs.remove", "a_unref"), ("refs.anchor", "a_anchor"),
                ("refs.clear_anchors", "a_clear_anchors"),
                ("match.selection", "a_match_sel"), ("match.scene", "a_match_scene"), ("match.all", "a_match_all"),
                ("nav.prev", "a_prev"), ("nav.next", "a_next"), ("view.fit", "a_fit"), ("view.100", "a_100"),
                ("masks.brush", "a_brush"), ("masks.eraser", "a_eraser"), ("app.settings", "a_settings")):
            r.add(bid, getattr(self, name))
        for key, a in self.a_modes.items():
            r.add(f"view.mode_{key}", a)
        for bid, (key, _mode) in PEEK_DEFAULTS.items():
            r.add(bid, None, tr("Удерживать: фото без правок") if bid == "view.peek_before"
                  else tr("Удерживать: референс"), [key])
        for bid, a, keys in (("masks.smaller", self.a_brush_smaller, "["), ("masks.bigger", self.a_brush_bigger, "]"),
                             ("masks.softer", self.a_brush_softer, "Shift+["), ("masks.denser", self.a_brush_harder, "Shift+]")):
            r.add(bid, a, defaults=[keys], editable=False)   # физические клавиши [ ] — для справки
        self.apply_shortcuts()

    def apply_shortcuts(self) -> None:
        self.shortcuts.apply(self.settings.shortcuts)
        self._peek_keys = {}
        for bid, (_key, mode) in PEEK_DEFAULTS.items():
            cur = self.shortcuts.items[bid].current
            seq = QKeySequence(cur[0]) if cur else QKeySequence()
            if not seq.isEmpty():
                self._peek_keys[seq[0].key()] = mode

    def _build_menus_and_toolbar(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(tr("Файл"))
        m.addActions([self.a_new, self.a_open])
        self.recent_menu = m.addMenu(tr("Недавние"))
        m.addSeparator()
        m.addActions([self.a_save, self.a_save_as, self.a_pack])
        m.addSeparator()
        m.addActions([self.a_add_files, self.a_add_folder, self.a_export])
        m.addSeparator()
        m.addAction(self.a_quit)
        m = mb.addMenu(tr("Правка"))
        m.addActions([self.a_undo, self.a_redo])
        m.addSeparator()
        m.addActions([self.a_copy, self.a_paste, self.a_copy_edits, self.a_paste_edits, self.a_reset])
        m.addSeparator()
        m.addActions([self.a_select_all, self.a_remove])
        m = mb.addMenu(tr("Референсы"))
        m.addActions([self.a_ref, self.a_unref])
        m.addSeparator()
        m.addActions([self.a_anchor, self.a_clear_anchors])
        m = mb.addMenu(tr("Авто-баланс"))
        m.addActions([self.a_match_sel, self.a_match_scene, self.a_match_all])
        m = mb.addMenu(tr("Вид"))
        m.addActions(list(self.a_modes.values()))
        mm = m.addMenu(tr("Кисть масок"))
        mm.addActions(self.mask_actions)
        m.addSeparator()
        m.addActions([self.a_fit, self.a_100, self.a_prev, self.a_next])
        m.addSeparator()
        for d in (self.dock_tree, self.dock_right):
            m.addAction(d.toggleViewAction())
        self.theme_menu = m.addMenu(tr("Тема"))
        m.addSeparator()
        m.addAction(self.a_settings)
        self._fill_theme_menu()

        short = {self.a_open: tr("Открыть"), self.a_save: tr("Сохранить"), self.a_add_folder: tr("+ Папка"),
                 self.a_add_files: tr("+ Фото"), self.a_ref: tr("★ Референс"), self.a_match_sel: tr("⚖ Авто-баланс"),
                 self.a_export: tr("Экспорт"), self.a_settings: "⚙",
                 self.a_modes["single"]: tr("Одно"), self.a_modes["masks"]: tr("Маски"),
                 self.a_modes["split"]: tr("До | после"), self.a_modes["side"]: tr("С референсом"),
                 self.a_modes["neutrals"]: tr("Пипетка"), self.a_anchor: tr("◎ Якорь")}
        for a, text in short.items():
            a.setIconText(text)
        tb = QToolBar(tr("Инструменты"))
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.setObjectName("toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addActions([self.a_open, self.a_save])
        tb.addSeparator()
        tb.addActions([self.a_add_folder, self.a_add_files])
        tb.addSeparator()
        tb.addActions([self.a_ref, self.a_match_sel])
        tb.addSeparator()
        tb.addActions(list(self.a_modes.values()))
        tb.addAction(self.a_anchor)
        tb.addSeparator()
        for mode, text, tip in (("before", tr("◐ До  (Z)"), tr("Держите — покажет фото без правок")),
                                ("ref", tr("★ Реф  (X)"), tr("Держите — покажет референс сцены"))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.pressed.connect(lambda m=mode: self.set_peek(m))
            b.released.connect(lambda: self.set_peek(None))
            tb.addWidget(b)
        tb.addSeparator()
        tb.addActions([self.a_export, self.a_settings])

    def _fill_theme_menu(self) -> None:
        self.theme_menu.clear()
        group = QActionGroup(self)
        for key, th in sorted(themes.available().items(), key=lambda kv: (not kv[1].dark, kv[1].name)):
            a = QAction(th.name, self, checkable=True)
            a.setChecked(key == self.settings.theme)
            a.triggered.connect(lambda _c=False, k=key: self.set_theme(k))
            group.addAction(a)
            self.theme_menu.addAction(a)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for p in self.settings.recent:
            self.recent_menu.addAction(p, lambda path=p: self.open_path(path))
        self.recent_menu.setEnabled(bool(self.settings.recent))

    # --- настройки/темы ---------------------------------------------------------------------
    def apply_settings(self) -> None:
        app = QApplication.instance()
        self.theme.set(themes.apply(app, self.settings))
        self.viewer.set_background(viewer_brush(self.settings.viewer_bg, self.theme["viewer"]))
        self.viewer.accent = themes.get(self.settings.theme).qcolor("accent")
        self.top.set_thumb_sizes(self.settings.ref_thumb_size, self.settings.thumb_size)
        for w in (self.film, self.ref_strip.list, self.tree, self.viewer.viewport(), self.adjust):
            w.update()
        self.adjust.apply_settings()
        self.adjust.refresh()
        if hasattr(self, "shortcuts"):
            self.apply_shortcuts()
        self.mask_panel.apply_settings()
        self._refresh_mask_overlay()
        self._sync_ref_overlay()
        if hasattr(self, "_autosave_timer"):
            self._restart_autosave()
        self._fill_theme_menu()
        self._fill_recent()

    def set_theme(self, key: str) -> None:
        self.settings.theme = key
        self.settings.save()
        self.apply_settings()

    def settings_dialog(self) -> None:
        dlg = SettingsDialog(self, self.settings, self.shortcuts)
        dlg.applied.connect(self.apply_settings)
        dlg.exec()
        if self.settings.language != i18n.current():   # язык сменили — пересобрать окно (T-0086)
            QTimer.singleShot(0, lambda: self.switch_language(self.settings.language))

    def say(self, text: str) -> None:
        self.status_msg.setText(text)

    # --- синхронизация панелей --------------------------------------------------------------
    def _current_scene(self):
        ws = self.state.ws
        if self.state.current:
            return ws.locate(self.state.current)[0]
        return ws.scenes[0] if ws.scenes else None

    def _on_structure(self) -> None:
        for ph in self.state.ws.all_photos():
            self.cache.request(ph.id, self.state.ws.abs_path(ph))
        self._sync_film()
        self._sync_refs()
        self._update_counts()
        self._schedule_render()

    def _follow_current(self, pid: str | None = None) -> None:
        """Фото вне показанной папки (выбрано в дереве) — лента переходит на его папку."""
        pid = pid or self.state.current
        if pid and self.film_scope is not None and pid not in self.film.ids():
            self.film_scope = self.state.ws.locate(pid)[0].id
            self.tree.set_scope(self.film_scope)
            self._sync_film()

    def _on_current(self, pid: str) -> None:
        self._follow_current()
        self._sync_film()
        self._sync_refs()
        self._schedule_render()

    def _on_stacks(self, ids: list) -> None:
        if self.state.current in ids or (self.view_mode == "side" and set(ids) & self.state.ws.ref_ids()):
            self._schedule_render()
        if set(ids) & self.state.ws.ref_ids():
            self._refs_timer.start()
        self.tree._refresh_marks()

    def _on_cache_ready(self, pid: str) -> None:
        if pid == self.state.current or (self.view_mode == "side" and pid in self.state.ws.ref_ids()):
            self._schedule_render()

    def set_film_scope(self, scope: str | None) -> None:
        """Что показывать в верхней ленте: None — все фото из всех подпапок, иначе сцену."""
        self.film_scope = scope
        self.tree.set_scope(scope)
        self._sync_film()

    def _sync_film(self) -> None:
        ws = self.state.ws
        scene = None
        if self.film_scope is not None:
            try:
                scene = ws.scene(self.film_scope)
            except KeyError:  # сцену убрали — показываем всё
                self.film_scope = None
                self.tree.set_scope(None)
        ids = [p.id for p in scene.photos] if scene else [p.id for p in ws.all_photos()]
        if ids != self.film.ids():
            self.film.set_ids(ids)
        if scene:
            self.top.set_title(tr("КАДРЫ СЦЕНЫ: {scene}  ({n})", scene=scene.name, n=len(ids)))
        else:
            self.top.set_title(tr("ВСЕ ФОТО  ({n})", n=len(ids))
                               + (f" · {plural(len(ws.scenes), 'папка', 'папки', 'папок')}" if len(ws.scenes) > 1 else ""))

    def _ref_for_current(self) -> str | None:
        """Референс текущего фото: запомненный у него (подбиралось по нему), иначе активный."""
        ws = self.state.ws
        if self.state.current:
            return ws.ref_for(ws.locate(self.state.current)[1])
        return ws.active_ref

    def _sync_refs(self) -> None:
        ws = self.state.ws
        ids = [r.photo_id for r in ws.refs]
        active = ws.locate(ws.active_ref)[1].name if ws.active_ref else ""
        fm = self.ref_strip.scope.fontMetrics()
        scope = (tr("● активный:") + "\n" + fm.elidedText(active, Qt.ElideMiddle, 136)) if active else ""
        self.ref_strip.scope.setToolTip(active)
        if ids != self.ref_strip.list.ids() or self.ref_strip.scope.text() != scope:
            self.ref_strip.set_refs(ids, scope)
            self._refs_timer.start()
        self.ref_strip.list._from_state()
        self.mask_panel.set_ref(self._ref_for_current())

    def _update_ref_profile(self) -> None:
        ws = self.state.ws
        if not ws.active_ref:
            self.ref_strip.swatches.set_profile(None)
            return
        snap = ws.model_copy(deep=True)
        snap.set_root(ws.root)
        rid = ws.active_ref
        self._tasks.append(run_task(lambda: matching.ref_profile(snap, rid),
                                    self.ref_strip.swatches.set_profile, lambda _e: self.ref_strip.swatches.set_profile(None)))

    def _on_match_progress(self, done: int, total: int) -> None:
        if total:
            self.busy.setRange(0, total)
            self.busy.setValue(done)
            self.busy.setFormat(tr("авто-баланс {done}/{total}", done=done, total=total))
            self.busy.setTextVisible(True)
            self.busy.setVisible(True)
            self.viewer.badge = tr("⚖ Авто-баланс: {done} из {total}", done=done, total=total)
        else:
            self.busy.setRange(0, 0)
            self.busy.setTextVisible(False)
            self.busy.setVisible(False)
            self.viewer.badge = ""
        self.viewer.viewport().update()

    def _update_counts(self) -> None:
        ws = self.state.ws
        n = sum(1 for _ in ws.all_photos())
        self.status_counts.setText(tr("{n} фото · {folders} · выделено {selected}", n=n,
                                      folders=plural(len(ws.scenes), 'папка', 'папки', 'папок'),
                                      selected=len(self.state.selection)))

    def _update_title(self) -> None:
        try:
            self.state.undo.isClean()
        except RuntimeError:  # завершение программы: стек отмены уже удалён
            return
        name = self.state.path.name if self.state.path else tr("без имени")
        self.setWindowTitle(f"{'● ' if self.state.dirty else ''}{name} — Image ColorMatcher {__version__}")

    # --- превью -------------------------------------------------------------------------------
    def set_view_mode(self, mode: str) -> None:
        if mode != "side" and self.a_anchor.isChecked():
            self.a_anchor.setChecked(False)
            self.toggle_anchor_mode()
        self.view_mode = mode
        self.a_modes[mode].setChecked(True)
        self.viewer.split = self.split if mode == "split" else None
        masks_on = mode == "masks"
        self._sync_ref_overlay(masks_on)
        was_on = self.mask_box.isVisible()
        self.mask_box.setVisible(masks_on)
        if masks_on and not was_on:   # панель масок — по содержимому, если хватает места
            total = sum(self.right_split.sizes())
            want = min(self.mask_panel.sizeHint().height() + 4, max(90, total - 260))
            self.right_split.setSizes([want, max(1, total - want)])
        self.viewer.set_paint_mode(masks_on)
        for a in self.mask_actions:
            a.setEnabled(masks_on)
        if masks_on:
            self.say(tr("Маски: выберите цвет справа и рисуйте. Q — кисть, E — ластик, [ ] — размер, "
                        "X — реф с масками, B — авто-баланс"))
        self._update_brush()
        self._schedule_render()

    def _on_split(self, frac: float) -> None:
        self.split = frac
        self._schedule_render()

    def _schedule_render(self) -> None:
        self._render_timer.start()

    def _lut_for(self, stack) -> np.ndarray | None:
        key = stack_hash(stack)
        lut = self._luts.get(key)
        if lut is None:
            lut = bake_stack(stack, lutlib.PREVIEW_SIZE)
            self._luts[key] = lut
            while len(self._luts) > 24:
                self._luts.popitem(last=False)
        self._luts.move_to_end(key)
        return lut

    def _rendered(self, pid: str, proxy: np.ndarray, stack) -> np.ndarray:
        if stack.is_identity():
            return proxy
        return render(proxy, stack, lut=self._lut_for(stack))

    def _photo_key(self, ph) -> str:
        """Ключ кеша кадра: общий стек + слои меток + номер правки маски."""
        key = f"{ph.id}:{stack_hash(ph.stack)}"
        layers = ph.active_layers()
        if layers:
            key += ":" + ",".join(f"{k}={stack_hash(st)}" for k, st in layers.items())
        if self.state.ws.mask(ph.id) is not None:
            key += f":m{self.state.ws._mask_ver.get(ph.id, 0)}"
        return key

    def _mask_at(self, pid: str, h: int, w: int) -> np.ndarray | None:
        """Маска фото, растянутая под размер прокси (кеш по номеру правки) — рендер слоёв без пересчёта."""
        mask = self.state.ws.mask(pid)
        if mask is None:
            return None
        key = (pid, self.state.ws._mask_ver.get(pid, 0), h, w)
        if self._mask_cache.get("key") != key:
            weights = M.resized(mask, h, w) if mask.shape[:2] != (h, w) else mask
            self._mask_cache = {"key": key, "mask": weights}
        return self._mask_cache["mask"]

    def _rendered_photo(self, ph, proxy: np.ndarray) -> np.ndarray:
        """Кадр с общими правками и слоями меток по мягким маскам (T-0062)."""
        base = self._rendered(ph.id, proxy, ph.stack)
        mask = self._mask_at(ph.id, *proxy.shape[:2])
        if mask is None and M.BG_LAYER not in ph.active_layers():
            return base
        return render_masked(proxy, ph.stack, ph.active_layers(), mask, lut_for=self._lut_for, base=base)

    def _render_preview(self) -> None:
        ws, pid = self.state.ws, self.state.current
        self.viewer.labels = None
        self.viewer.overlays = []
        self._set_mask_overlay(None)
        if not pid:
            self.viewer.set_image(None)
            self.viewer.set_message(tr("Добавьте фото: перетащите папку или файлы в окно, Ctrl+V, или «Файл → Добавить»")
                                    if not any(True for _ in ws.all_photos()) else tr("Выберите фото"))
            return
        _, ph = ws.locate(pid)
        path = ws.abs_path(ph)
        if pid in self.cache.errors:
            self.viewer.set_image(None)
            self.viewer.set_message(tr("Не удалось открыть {name}", name=ph.name))
            return
        proxy = self.cache.proxy(pid, path)
        if proxy is None:
            self.viewer.set_message(tr("Загрузка…"))
            return
        if self.peek == "before":
            self.viewer.labels = (tr("ДО — без правок"), "")
            self.viewer.set_message("")
            self.viewer.set_image(to_qimage(proxy))
            return
        if self.peek == "ref":
            ref = self._ref_frame()
            if ref is not None:
                self.viewer.set_message("")
                self.viewer.set_image(to_qimage(ref))
                if self.view_mode == "masks":   # X в режиме масок — реф с его масками (T-0065)
                    self._set_mask_overlay(self._ref_for_current())
                return
        if self.view_mode in ("single", "masks"):
            key = self._photo_key(ph)
            img = self._frames.get(key)
            if img is None:
                img = to_qimage(self._rendered_photo(ph, proxy))
                self._frames[key] = img
                while len(self._frames) > 24:
                    self._frames.popitem(last=False)
            self._frames.move_to_end(key)
            self.viewer.set_message("")
            self.viewer.set_image(img)
            if self.view_mode == "masks":
                self._set_mask_overlay(pid)
                self._update_brush()
            return
        after = self._rendered_photo(ph, proxy)
        if self.view_mode == "split":
            x = int(round(after.shape[1] * self.split))
            frame = np.concatenate([proxy[:, :x], after[:, x:]], axis=1)
            self.viewer.labels = (tr("до"), tr("после"))
        elif self.view_mode == "side":
            frame = self._side_by_side(after)
            self.viewer.overlays = self._anchor_overlays(ph)
        elif self.view_mode == "neutrals":
            frame = self._neutral_overlay(after)
            self.viewer.labels = (tr("что видит пипетка: яркое — учтено как нейтраль"), tr("текущее"))
        else:
            frame = after
        self.viewer.set_message("")
        self.viewer.set_image(to_qimage(frame))

    def _side_by_side(self, current: np.ndarray) -> np.ndarray:
        ws = self.state.ws
        rid = self._ref_for_current()
        self._side_layout = None
        if not rid or rid == self.state.current:
            self.viewer.labels = (tr("нет референса") if not rid else tr("это и есть референс"), tr("текущее"))
            return current
        _, rph = ws.locate(rid)
        rproxy = self.cache.proxy(rph.id, ws.abs_path(rph))
        if rproxy is None:
            return current
        ref = self._rendered_photo(rph, rproxy)
        h = current.shape[0]
        ref = cv2.resize(ref, (max(1, int(ref.shape[1] * h / ref.shape[0])), h), interpolation=cv2.INTER_AREA)
        gap = np.zeros((h, max(4, h // 100), 3), np.float32)
        self.viewer.labels = (tr("референс: {name}", name=rph.name), tr("текущее"))
        self._side_layout = {"ref_id": rph.id, "ref_w": ref.shape[1], "gap": gap.shape[1],
                             "cur_w": current.shape[1], "h": h}
        return np.concatenate([ref, gap, current], axis=1)

    def _ref_frame(self) -> np.ndarray | None:
        """Референс текущего фото (с его правками) для удержания X."""
        ws = self.state.ws
        rid = self._ref_for_current()
        if not rid:
            self.viewer.labels = (tr("нет референса — Ctrl+R"), "")
            return None
        _, rph = ws.locate(rid)
        proxy = self.cache.proxy(rph.id, ws.abs_path(rph))
        if proxy is None:
            self.viewer.set_message(tr("Загрузка референса…"))
            return None
        self.viewer.labels = (tr("РЕФЕРЕНС: {name}", name=rph.name), "")
        return self._rendered_photo(rph, proxy)

    # --- удержание клавиш: «до» / референс (T-0039) ------------------------------------------
    def set_peek(self, mode: str | None) -> None:
        if mode == self.peek:
            return
        self.peek = mode
        self._render_preview()  # сразу, без таймера — реакция на клавишу должна быть мгновенной

    def _bracket(self, ev) -> int:
        """-1 / +1 — нажата физическая клавиша [ / ] (на русской раскладке это «х» / «ъ»), иначе 0."""
        vk = ev.nativeVirtualKey()
        if vk == VK_OEM_4 or ev.key() in (Qt.Key_BracketLeft, Qt.Key_BraceLeft):
            return -1
        if vk == VK_OEM_6 or ev.key() in (Qt.Key_BracketRight, Qt.Key_BraceRight):
            return 1
        return 0

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        et = ev.type()
        if et == QEvent.KeyPress and self.view_mode == "masks" and self.isActiveWindow() and not self._typing():
            step = self._bracket(ev)
            if step:
                if ev.modifiers() & Qt.ShiftModifier:
                    self.mask_panel.change_spread(0.1 * step)             # Shift+[ ] — распределение
                else:
                    self.mask_panel.resize_brush(1.2 if step > 0 else 1 / 1.2)   # [ ] — размер
                return True
        if et in (QEvent.KeyPress, QEvent.KeyRelease) and ev.key() in self._peek_keys and not ev.isAutoRepeat():
            if not self._typing() and self.isActiveWindow() and ev.modifiers() in (Qt.NoModifier, Qt.KeypadModifier):
                self.set_peek(self._peek_keys[ev.key()] if et == QEvent.KeyPress else None)
                return True
        elif et == QEvent.WindowDeactivate and obj is self and self.peek:
            self.set_peek(None)  # отпустили клавишу вне окна
        return super().eventFilter(obj, ev)

    @staticmethod
    def _typing() -> bool:
        focus = QApplication.focusWidget()
        return isinstance(focus, (QLineEdit, QAbstractSpinBox, QPlainTextEdit)) or (
            isinstance(focus, QComboBox) and focus.isEditable())

    def _neutral_overlay(self, frame: np.ndarray) -> np.ndarray:
        """Подсветка пикселей, которые пипетка считает нейтральными (с учётом голосов плиток)."""
        s = sample_image(frame, pixels=min(160_000, frame.shape[0] * frame.shape[1]))  # крупнее — для показа
        w = s.w_band.sum(axis=1).reshape(s.shape)
        w = w / max(float(w.max()), 1e-6)
        w = cv2.resize(w.astype(np.float32), (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_LINEAR)[..., None]
        accent = np.array(QColor(self.theme["accent"]).getRgbF()[:3], np.float32)
        return np.clip(frame * (0.15 + 0.85 * w) + accent * 0.25 * w, 0, 1)

    # --- пипетка-якоря -------------------------------------------------------------------
    def toggle_anchor_mode(self) -> None:
        on = self.a_anchor.isChecked()
        if on and self.view_mode != "side":
            self.set_view_mode("side")
            self.a_anchor.setChecked(True)
        self._pending_ref_rect = None
        self.viewer.set_pick_mode(on)
        self.say(tr("Якорь: обведите объект на РЕФЕРЕНСЕ (слева), затем тот же объект на текущем фото (справа)")
                 if on else "")

    def _norm_rect(self, r: QRectF, x0: float, w: float, h: float) -> tuple[float, float, float, float]:
        return ((r.left() - x0) / w, r.top() / h, r.width() / w, r.height() / h)

    def _on_rect_picked(self, r: QRectF) -> None:
        lay = self._side_layout
        if not lay or not self.state.current:
            self.say(tr("Нужен режим «рядом с референсом» и референс у сцены"))
            return
        cx = r.center().x()
        if cx < lay["ref_w"]:
            self._pending_ref_rect = (lay["ref_id"], self._norm_rect(r, 0, lay["ref_w"], lay["h"]))
            self.say(tr("Теперь обведите тот же объект на текущем фото (справа)"))
            self._schedule_render()
            return
        if self._pending_ref_rect is None:
            self.say(tr("Сначала обведите объект на референсе (слева)"))
            return
        ref_id, ref_rect = self._pending_ref_rect
        x0 = lay["ref_w"] + lay["gap"]
        tgt_rect = self._norm_rect(r, x0, lay["cur_w"], lay["h"])
        label, ok = QInputDialog.getText(self, tr("Якорь"), tr("Что это за объект (кожа, белая стена, небо…)?"),
                                         text=tr("якорь"))
        self._pending_ref_rect = None
        if not ok:
            return
        pid = self.state.current

        def fn(ws):
            ph = ws.locate(pid)[1]
            ref_ph = ws.locate(ref_id)[1]
            ph.anchors.append(AnchorPair(label=label.strip() or tr("якорь"), weight=1.0,
                                         ref=Region(image=ref_ph.path, rect=ref_rect),
                                         target=Region(image=ph.path, rect=tgt_rect)))

        self.state.mutate(tr("Якорь «{label}»", label=label), fn)
        self.say(tr("Якорь «{label}» добавлен — учтётся при авто-балансе этого фото", label=label))
        self._schedule_render()

    def _anchor_overlays(self, ph) -> list:
        lay = self._side_layout
        if not lay:
            return []
        ref_ph = self.state.ws.locate(lay["ref_id"])[1]
        acc, dim = QColor(self.theme["accent"]), QColor(self.theme["danger"])
        out = []

        def box(rect, x0, w, color, text):
            x, y, rw, rh = rect
            out.append((QRectF(x0 + x * w, y * lay["h"], rw * w, rh * lay["h"]), color, text))

        for a in ph.anchors:
            if a.ref.image == ref_ph.path:
                box(a.ref.rect, 0, lay["ref_w"], acc, a.label)
            box(a.target.rect, lay["ref_w"] + lay["gap"], lay["cur_w"], acc, a.label)
        if self._pending_ref_rect and self._pending_ref_rect[0] == lay["ref_id"]:
            box(self._pending_ref_rect[1], 0, lay["ref_w"], dim, tr("…и где это на текущем?"))
        return out

    def clear_anchors(self) -> None:
        pid = self.state.current
        if not pid or not self.state.ws.locate(pid)[1].anchors:
            return
        self.state.mutate(tr("Удалить якоря"), lambda ws: setattr(ws.locate(pid)[1], "anchors", []))
        self._schedule_render()

    # --- маски: оверлей и кисть (T-0059) ----------------------------------------------------
    def _set_mask_overlay(self, pid: str | None) -> None:
        mask = self.state.ws.mask(pid) if pid else None
        if mask is None:
            self._mask_ov = None
            self.viewer.set_mask_image(None)
            return
        rgba = mask_rgba(mask, self.mask_panel.colors, self.settings.mask_alpha, self.mask_panel.only_slot())
        self._mask_ov = (pid, rgba)
        self.viewer.set_mask_image(rgba_qimage(rgba))

    def _refresh_mask_overlay(self) -> None:
        if self._mask_ov is not None:
            self._set_mask_overlay(self._mask_ov[0])
        elif self.view_mode == "masks":
            self._schedule_render()

    def _on_masks_changed(self, pid: str) -> None:
        if self._stroke is None and self.view_mode == "masks":
            shown = self._mask_ov[0] if self._mask_ov else (self.state.current if self.peek != "ref" else None)
            if pid == shown:
                self._set_mask_overlay(pid)
        # маска меняет и сам кадр, если у фото есть слои меток / «не трогать» (T-0062)
        if pid == self.state.current or pid == self._ref_for_current():
            self._schedule_render()
        for lst in (self.film, self.ref_strip.list):
            lst.viewport().update()

    def _sync_ref_overlay(self, on: bool | None = None) -> None:
        """Миниатюры референсов в режиме «Маски» — с масками (T-0065)."""
        on = self.view_mode == "masks" if on is None else on
        self.ref_strip.list.mask_overlay = (tuple(self.mask_panel.colors), 0.55) if on else None
        self.ref_strip.list.viewport().update()

    def _update_brush(self) -> None:
        fw, fh = self.viewer.frame_size()
        self.viewer.brush_px = self.mask_panel.diameter.value() * max(fw, fh) / M.MASK_SIDE if fw else 0.0
        self.viewer.brush_erase = self.mask_panel.erase
        self.viewer.viewport().update()

    def _mask_point(self, pt, mask: np.ndarray) -> tuple[float, float]:
        fw, fh = self.viewer.frame_size()
        return pt.x() * mask.shape[1] / max(1, fw), pt.y() * mask.shape[0] / max(1, fh)

    def _stroke_start(self, pt, erase: bool) -> None:
        pid = self.state.current
        fw, fh = self.viewer.frame_size()
        if not pid or self.peek or not fw:
            return
        ws = self.state.ws
        mask = ws.mask(pid)
        if mask is None:
            if erase:
                return
            mask = M.empty(*M.shape_for(fh, fw))
            ws.masks[pid] = mask
        start = self._mask_point(pt, mask)
        # сплошной мазок: покрытие копится максимумом от снимка на начало (T-0071)
        self._stroke = {"pid": pid, "last": start,
                        "stroke": M.Stroke(mask, self.mask_panel.slot, self.mask_panel.brush(), erase)}
        if self._mask_ov is None or self._mask_ov[0] != pid:
            self._mask_ov = (pid, mask_rgba(mask, self.mask_panel.colors, self.settings.mask_alpha,
                                            self.mask_panel.only_slot()))
        self._apply_stroke(start, start)

    def _stroke_move(self, pt) -> None:
        st = self._stroke
        if st is None:
            return
        p1 = self._mask_point(pt, st["stroke"].mask)
        self._apply_stroke(st["last"], p1)
        st["last"] = p1

    def _apply_stroke(self, p0, p1) -> None:
        stroke = self._stroke["stroke"]
        box = stroke.add(p0, p1)
        if box is None:
            return
        x0, y0, x1, y1 = box
        rgba = self._mask_ov[1]
        rgba[y0:y1, x0:x1] = mask_rgba(stroke.mask[y0:y1, x0:x1], self.mask_panel.colors, self.settings.mask_alpha,
                                       self.mask_panel.only_slot(), x0, y0)
        self.viewer.set_mask_image(rgba_qimage(rgba))

    def _stroke_end(self) -> None:
        st, self._stroke = self._stroke, None
        if st is None:
            return
        ws, pid, stroke = self.state.ws, st["pid"], st["stroke"]
        if stroke.box is None:
            if not stroke.mask.any():
                ws.masks.pop(pid, None)
            return
        x0, y0, x1, y1 = stroke.box
        self.state.push_mask_edit(tr("Ластик") if stroke.erase else tr("Мазок кистью"), pid, stroke.box,
                                  stroke.before[y0:y1, x0:x1].copy())

    # --- автосохранение ---------------------------------------------------------------------
    def _restart_autosave(self) -> None:
        m = self.settings.autosave_minutes
        if m > 0:
            self._autosave_timer.start(m * 60_000)
        else:
            self._autosave_timer.stop()

    def _autosave_path(self) -> Path | None:
        return self.state.path.with_name(self.state.path.stem + ".autosave.cmws") if self.state.path else None

    def _autosave(self) -> None:
        path = self._autosave_path()
        if path is None or not self.state.dirty:
            return
        try:
            workspace_io.write_json(self.state.ws, path, indent=1)   # + маски в *.autosave.masks
        except OSError as exc:
            self.say(tr("Автосохранение не удалось: {error}", error=exc))

    # --- выделение/навигация ---------------------------------------------------------------------
    def step(self, d: int) -> None:
        ids = self.film.ids()
        if not ids:
            return
        i = ids.index(self.state.current) if self.state.current in ids else -1
        nxt = ids[(i + d) % len(ids)]
        self.state.select([nxt], nxt)

    def select_scene(self) -> None:
        ids = self.film.ids()
        self.state.select(ids, self.state.current or (ids[0] if ids else ""))

    # --- состав воркспейса --------------------------------------------------------------------------
    def _import(self, paths: list[Path]) -> None:
        files = [p for p in paths if p.is_file() and is_image(p)]
        folders = [p for p in paths if p.is_dir()]
        sc = self._current_scene()
        target_scene = sc.name if sc else "photos"

        def fn(ws):
            added = []
            for d in folders:
                added += importer.add_folder(ws, d)
            if files:
                added += importer.add_files(ws, files, target_scene)
            return added

        added = self.state.mutate(tr("Добавить фото ({n})", n=len(paths)), fn) or []
        if added and not self.state.current:
            self.state.select([added[0].id], added[0].id)
        self.say(tr("Добавлено фото: {n}", n=len(added)) if added else tr("Нового ничего не добавлено"))

    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("Добавить фото"), "",
                                                tr("Изображения ({patterns})", patterns=FILE_PATTERNS))
        if files:
            self._import([Path(f) for f in files])

    def add_folder_dialog(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Папка со сценами"))
        if d:
            self._import([Path(d)])

    def remove_selected(self) -> None:
        ids = self.state.targets()
        if not ids:
            return
        if len(ids) > 1 and QMessageBox.question(self, tr("Убрать из воркспейса"),
                                                 tr("Убрать {n} фото из воркспейса?\nФайлы на диске не удаляются.",
                                                    n=len(ids))) != QMessageBox.Yes:
            return
        self.state.mutate(tr("Убрать фото ({n})", n=len(ids)), lambda ws: ws.remove_photos(set(ids)))

    def toggle_ref(self) -> None:
        """Ctrl+R (T-0043): если все выделенные — референсы, убрать их; иначе сделать
        референсами, а текущее — активным (T-0050)."""
        ids = self.state.targets()
        if not ids:
            return
        refs = self.state.ws.ref_ids()
        if all(i in refs for i in ids):
            self.unref()
            return
        current = self.state.current if self.state.current in ids else ids[0]

        def fn(ws):
            for pid in ids:
                ws.add_ref(pid, activate=False)
            ws.set_active(current)

        self.state.mutate(tr("Референсы: добавить"), fn)
        self.say(tr("Референсов: {n}; активный — {name}", n=len(self.state.ws.refs),
                    name=self.state.ws.locate(current)[1].name))

    def make_ref(self, scene: bool = False) -> None:  # совместимость: «сделать референсом»
        ids = self.state.targets()
        if ids and not all(i in self.state.ws.ref_ids() for i in ids):
            self.toggle_ref()

    def unref(self) -> None:
        ids = set(self.state.targets())

        def fn(ws):
            for pid in ids:
                ws.remove_ref(pid)

        self.state.mutate(tr("Референсы: убрать"), fn)
        self.say(tr("Убрано из референсов"))

    # --- буфер обмена ------------------------------------------------------------------------------
    def copy_edits(self) -> None:
        if not self.state.current:
            return
        _, ph = self.state.ws.locate(self.state.current)
        QGuiApplication.clipboard().setText(CLIP_PREFIX + Preset.from_stack(ph.stack, name=ph.name).to_clipboard_text())
        self.say(tr("Правки «{name}» скопированы", name=ph.name))

    def paste_edits(self) -> None:
        text = QGuiApplication.clipboard().text()
        if not text.startswith(CLIP_PREFIX):
            self.say(tr("В буфере нет правок (сначала Ctrl+Shift+C)"))
            return
        preset = Preset.from_clipboard_text(text[len(CLIP_PREFIX):])
        ids = self.state.targets()
        apply_preset(self.state, preset, ids, self.presets_panel.groups())
        self.say(tr("Правки вставлены в {n} фото", n=len(ids)))

    def copy_photos(self) -> None:
        ids = self.state.targets()
        if not ids:
            return
        ws = self.state.ws
        jobs = [(ws.abs_path(ws.locate(i)[1]), ws.locate(i)[1].stack.model_copy(deep=True)) for i in ids]
        out_dir = app_home() / "clipboard" / _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

        def work():
            paths = []
            for src, stack in jobs:
                img = imageio.load_image(src)
                lut = None if stack.is_identity() else bake_stack(stack, lutlib.EXPORT_SIZE)
                out = render(img.rgb, stack, lut=lut) if lut is not None else img.rgb
                dst = out_dir / (src.stem + (".png" if src.suffix.lower() in (".png", ".tif", ".tiff") else ".jpg"))
                imageio.save_image(dst, out, quality=95, exif=img.exif, icc=img.icc)
                paths.append((dst, out if len(jobs) == 1 else None))
            return paths

        def done(paths):
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(p)) for p, _ in paths])
            if len(paths) == 1 and paths[0][1] is not None:
                mime.setImageData(to_qimage(paths[0][1]))
            QGuiApplication.clipboard().setMimeData(mime)
            self._set_busy(False)
            self.say(tr("В буфере: {n} фото с правками", n=len(paths)))

        self._set_busy(True)
        self.say(tr("Готовлю фото для буфера…"))
        self._tasks.append(run_task(work, done, self._task_failed))

    def paste(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime.hasUrls():
            paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
            if paths:
                self._import(paths)
                return
        if mime.hasImage():
            img = from_qimage(QGuiApplication.clipboard().image())
            base = (self.state.ws.root if self.state.path else app_home()) / "_pasted"
            dst = base / f"pasted_{_dt.datetime.now():%Y%m%d_%H%M%S}.png"
            imageio.save_image(dst, img)
            self._import([dst])
            return
        text = QGuiApplication.clipboard().text()
        if text.startswith(CLIP_PREFIX):
            self.paste_edits()
            return
        self.say(tr("В буфере нет фото"))

    # --- drag & drop -------------------------------------------------------------------------------
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._import(paths)

    # --- файлы ---------------------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.state.dirty:
            return True
        r = QMessageBox.question(self, tr("Несохранённые изменения"), tr("Сохранить изменения воркспейса?"),
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def new_workspace(self) -> None:
        if self._confirm_discard():
            self.cache.clear()
            self.film_scope = None
            self.state.new()

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Открыть воркспейс"), "", tr("Воркспейс (*.cmws)"))
        if path:
            self.open_path(path, confirm=False)

    def open_path(self, path: str, confirm: bool = True) -> None:
        if confirm and not self._confirm_discard():
            return
        try:
            self.cache.clear()
            self.film_scope = None
            self.state.open(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("Не удалось открыть"), str(exc))
            return
        self.settings.push_recent(path)
        self.settings.save()
        self._fill_recent()
        auto = self._autosave_path()
        if auto and auto.exists() and auto.stat().st_mtime > self.state.path.stat().st_mtime:
            if QMessageBox.question(self, tr("Автосохранение"),
                                    tr("Есть более свежее автосохранение (возможно, после сбоя). Восстановить?")) == QMessageBox.Yes:
                real = self.state.path
                self.state.open(auto)
                self.state.path = real
                self.state.mark_dirty(tr("Восстановлено из автосохранения"))
                self._update_title()
        first = next(self.state.ws.all_photos(), None)
        if first:
            self.state.select([first.id], first.id)
        missing = self.state.ws.missing()
        name = Path(path).name
        self.say(tr("Открыт {name} — пропало файлов: {n}", name=name, n=len(missing)) if missing
                 else tr("Открыт {name}", name=name))

    def save(self) -> bool:
        if self.state.path is None:
            return self.save_as()
        self.state.save()
        self._drop_autosave()
        self.say(tr("Сохранено: {path}", path=self.state.path))
        return True

    def _drop_autosave(self) -> None:
        auto = self._autosave_path()
        if auto and auto.exists():
            auto.unlink()
        if auto:
            shutil.rmtree(workspace_io.masks_dir(auto), ignore_errors=True)

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить воркспейс"), str(self.state.path or "workspace.cmws"),
                                              tr("Воркспейс (*.cmws)"))
        if not path:
            return False
        self.state.save(path)
        self.settings.push_recent(self.state.path)
        self.settings.save()
        self._fill_recent()
        self.say(tr("Сохранено: {path}", path=self.state.path))
        return True

    def pack(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Папка для упаковки (воркспейс + все фото)"))
        if not d:
            return
        ws = self.state.ws.model_copy(deep=True)
        ws.set_root(self.state.ws.root)
        self._set_busy(True)
        self._tasks.append(run_task(lambda: workspace_io.pack(ws, d),
                                    lambda p: (self._set_busy(False), self.say(tr("Упаковано: {path}", path=p))),
                                    self._task_failed))

    def export_dialog(self) -> None:
        ws = self.state.ws
        sc = self._current_scene()
        dlg = ExportDialog(self, ws.export, len(self.state.targets()), len(sc.photos) if sc else 0,
                           sum(1 for _ in ws.all_photos()))
        if dlg.exec() != ExportDialog.Accepted:
            return
        scope, settings, force = dlg.result_settings()
        if settings != ws.export:
            self.state.mutate(tr("Настройки экспорта"), lambda w: setattr(w, "export", settings))
        ids = set(self.state.targets()) if scope == "selection" else None
        scenes = [sc.id] if scope == "scene" and sc else None
        snap = self.state.ws.model_copy(deep=True)
        snap.set_root(self.state.ws.root)
        self._set_busy(True)
        self.say(tr("Экспорт…"))

        def done(results):
            self._set_busy(False)
            failed = [r for r in results if "error" in r]
            ok = len(results) - len(failed)
            self.say(tr("Экспорт: {n} файлов, ошибок: {errors}", n=ok, errors=len(failed)) if failed
                     else tr("Экспорт: {n} файлов", n=ok))
            if failed:
                QMessageBox.warning(self, tr("Экспорт"), "\n".join(f"{Path(r['src']).name}: {r['error']}" for r in failed[:12]))

        self._tasks.append(run_task(lambda: export_svc.export(snap, ids, scenes, settings, force), done, self._task_failed))

    def _set_busy(self, on: bool) -> None:
        self.busy.setVisible(on)

    def _task_failed(self, err: str) -> None:
        self._set_busy(False)
        self.say(tr("Ошибка: {error}", error=err.splitlines()[0]))

    def closeEvent(self, e) -> None:  # noqa: N802
        if self._closing_for_rebuild:   # окно заменено новым (смена языка) — ничего не спрашивать
            e.accept()
            return
        if not self._confirm_discard():
            e.ignore()
            return
        self.settings.geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.window_state = bytes(self.saveState(LAYOUT_VERSION).toBase64()).decode()
        self.settings.save()
        e.accept()
