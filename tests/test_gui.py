"""GUI: темы, состояние/Undo, главное окно (офскрин)."""

import time

import pytest

from colormatcher.core import imageio
from colormatcher.core.pipeline import render_direct
from colormatcher.models import AdjustmentStack, Preset
from colormatcher.services import importer, workspace_io

from conftest import make_scene


def pump(app, seconds=0.0, until=None):
    t0 = time.time()
    while True:
        app.processEvents()
        if until is not None and until():
            return True
        if time.time() - t0 >= seconds:
            return until is None
        time.sleep(0.01)


@pytest.fixture
def ws_file(tmp_path):
    scene = make_scene()
    for rel, d in {"day/ref.png": {}, "day/a.png": {"cb.midtones.yb": -25}, "day/b.png": {"hs.saturation": -30},
                   "night/c.png": {"bc.brightness": -40}}.items():
        imageio.save_image(tmp_path / "shoot" / rel, render_direct(scene, AdjustmentStack().update(d)))
    ws = workspace_io.new_workspace(tmp_path / "w.cmws")
    importer.add_folder(ws, tmp_path / "shoot")
    return workspace_io.save(ws, tmp_path / "w.cmws")


@pytest.fixture
def win(qapp, ws_file):
    from colormatcher.gui.main_window import MainWindow
    from colormatcher.gui.settings import AppSettings

    w = MainWindow(AppSettings())
    w.resize(1400, 900)
    w.show()
    w.open_path(str(ws_file), confirm=False)
    yield w
    w.state.undo.setClean()
    w.close()


def test_all_themes_build_stylesheets(qapp):
    from colormatcher.gui import theme
    from colormatcher.gui.settings import AppSettings

    themes = theme.available()
    assert len(themes) >= 16 and sum(t.dark for t in themes.values()) >= 12
    for key in themes:
        qss = theme.stylesheet(themes[key], AppSettings(theme=key, font_size=12))
        assert "{" in qss and "#" in qss and "{bg}" not in qss


def test_state_undo_merges_drag(qapp, ws_file):
    from colormatcher.gui.state import AppState

    st = AppState()
    st.open(ws_file)
    pid = next(st.ws.all_photos()).id
    for v in (5, 10, 15):  # одно перетаскивание
        st.edit_stacks([pid], lambda s, _p, v=v: s.set("cb.midtones.cr", v), "cr", merge_key="cb.midtones.cr")
    st.end_merge()
    st.edit_stacks([pid], lambda s, _p: s.set("cb.midtones.cr", 40), "cr", merge_key="cb.midtones.cr")
    assert st.undo.count() == 2 and st.dirty
    st.undo.undo()
    assert st.ws.locate(pid)[1].stack.get("cb.midtones.cr") == 15
    st.undo.undo()
    assert st.ws.locate(pid)[1].stack.get("cb.midtones.cr") == 0
    st.undo.redo()
    assert st.ws.locate(pid)[1].stack.get("cb.midtones.cr") == 15


def test_state_structure_undo(qapp, ws_file):
    from colormatcher.gui.state import AppState

    st = AppState()
    st.open(ws_file)
    ids = [p.id for p in st.ws.all_photos()]
    st.select(ids[:2])
    st.mutate("remove", lambda ws: ws.remove_photos(set(ids[:2])))
    assert sum(1 for _ in st.ws.all_photos()) == 2 and st.selection == []
    st.undo.undo()
    assert sum(1 for _ in st.ws.all_photos()) == 4


def test_window_edit_group_relative_and_undo(qapp, win):
    ids = [p.id for p in win.state.ws.scene("shoot/day").photos]
    win.state.ws.locate(ids[1])[1].stack.set("hs.saturation", 10)
    win.state.select(ids, ids[0])
    pump(qapp, 0.1)
    panel = win.adjust
    assert panel.target_label._full.startswith("Группа")
    assert panel.rows["hs.saturation"].slider.mixed
    panel.relative.setChecked(True)
    row = panel.rows["hs.saturation"]
    row.changed.emit(0.0)   # старт перетаскивания от «—» (значение по умолчанию 0)
    row.changed.emit(5.0)
    row.committed.emit()
    vals = [win.state.ws.locate(i)[1].stack.get("hs.saturation") for i in ids]
    assert vals == [5.0, 15.0, 5.0]
    panel.relative.setChecked(False)
    row.changed.emit(-20.0)
    row.committed.emit()
    assert {win.state.ws.locate(i)[1].stack.get("hs.saturation") for i in ids} == {-20.0}
    win.state.undo.undo()
    assert [win.state.ws.locate(i)[1].stack.get("hs.saturation") for i in ids] == [5.0, 15.0, 5.0]


def test_window_cb_range_switch(qapp, win):
    pid = next(win.state.ws.all_photos()).id
    win.state.select([pid], pid)
    panel = win.adjust
    panel.range_buttons["shadows"].setChecked(True)
    panel.rows["cb.*.yb"].changed.emit(30.0)
    panel.rows["cb.*.yb"].committed.emit()
    st = win.state.ws.locate(pid)[1].stack
    assert st.get("cb.shadows.yb") == 30 and st.get("cb.midtones.yb") == 0
    assert "•" in panel.range_buttons["shadows"].text()


def test_window_preview_modes_render(qapp, win):
    ws = win.state.ws
    ref = ws.find("ref.png")
    win.state.select([ref.id], ref.id)
    win.make_ref()
    a = next(p for p in ws.scene("shoot/day").photos if p.name == "a.png")
    win.state.select([a.id], a.id)
    win.state.edit_stacks([a.id], lambda s, _p: s.set("cb.midtones.yb", 25), "yb")
    for mode in ("single", "split", "side"):
        win.set_view_mode(mode)
        assert pump(qapp, 5, until=lambda: not win.viewer._item.pixmap().isNull() and win.viewer.message == "")
        win._render_preview()
    assert win.viewer.labels and win.viewer.labels[1] == "текущее"
    pix = win.viewer._item.pixmap()
    assert pix.width() > pix.height() * 2  # референс + текущее рядом


def test_window_refs_and_edits_clipboard(qapp, win):
    ws = win.state.ws
    ids = [p.id for p in ws.scene("shoot/day").photos]
    win.state.select([ids[0]], ids[0])
    win.make_ref()
    assert [r.photo_id for r in win.state.ws.refs] == [ids[0]] and win.state.ws.active_ref == ids[0]
    win.state.edit_stacks([ids[0]], lambda s, _p: s.set("cb.highlights.mg", -12), "mg")
    win.copy_edits()
    win.state.select(ids[1:], ids[1])
    win.paste_edits()
    assert all(ws.locate(i)[1].stack.get("cb.highlights.mg") == -12 for i in ids[1:])
    win.state.select([ids[0]], ids[0])
    win.unref()
    assert ids[0] not in win.state.ws.ref_ids()


def test_window_remove_and_import(qapp, win, tmp_path):
    ws = win.state.ws
    c = ws.find("c.png")
    win.state.select([c.id], c.id)
    win.remove_selected()
    assert not any(p.name == "c.png" for p in win.state.ws.all_photos())
    extra = tmp_path / "extra.png"
    imageio.save_image(extra, make_scene(40, 60))
    win._import([extra])
    assert any(p.name == "extra.png" for p in win.state.ws.all_photos())
    win.state.undo.undo()
    assert not any(p.name == "extra.png" for p in win.state.ws.all_photos())


def test_window_presets_panel_apply(qapp, win):
    from colormatcher.gui.panels.presets_panel import presets_dir

    Preset.from_stack(AdjustmentStack().update({"bc.contrast": 22, "cb.midtones.cr": 5}), name="t",
                      groups=["bc", "cb"]).save(presets_dir() / "t")
    panel = win.presets_panel
    panel.reload()
    panel.list.setCurrentRow(0)
    panel.group_checks["cb"].setChecked(False)
    pid = next(win.state.ws.all_photos()).id
    win.state.select([pid], pid)
    panel.apply()
    st = win.state.ws.locate(pid)[1].stack
    assert st.get("bc.contrast") == 22 and st.get("cb.midtones.cr") == 0


def test_window_theme_switch_and_save(qapp, win, tmp_path):
    win.set_theme("paper")
    assert win.theme.theme.key == "paper" and not win.theme.dark
    win.state.edit_stacks([next(win.state.ws.all_photos()).id], lambda s, _p: s.set("hs.hue", 7), "hue")
    assert win.state.dirty
    assert win.save()
    assert not win.state.dirty
    back = workspace_io.load(win.state.path)
    assert any(p.stack.get("hs.hue") == 7 for p in back.all_photos())


def test_window_auto_balance_async(qapp, win):
    ws = win.state.ws
    ref = ws.find("ref.png")
    win.state.select([ref.id], ref.id)
    win.make_ref()
    targets = [p.id for p in ws.scene("shoot/day").photos if p.id != ref.id]
    win.state.select(targets, targets[0])
    steps = win.state.undo.count()
    win.match_panel.start("selection")
    assert pump(qapp, 60, until=lambda: win.match_panel._task is None)
    assert win.match_panel.table.rowCount() == 2
    assert all(not win.state.ws.locate(i)[1].stack.is_identity() for i in targets)
    assert win.state.undo.count() == steps + 1          # весь подбор — один шаг отмены
    win.state.undo.undo()
    assert all(win.state.ws.locate(i)[1].stack.is_identity() for i in targets)


def test_window_anchor_pick_creates_pair(qapp, win, monkeypatch):
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QInputDialog

    ws = win.state.ws
    ref = ws.find("ref.png")
    win.state.select([ref.id], ref.id)
    win.make_ref()
    a = next(p for p in ws.scene("shoot/day").photos if p.name == "a.png")
    win.state.select([a.id], a.id)
    win.a_anchor.setChecked(True)
    win.toggle_anchor_mode()
    assert win.view_mode == "side" and win.viewer.pick_mode
    assert pump(qapp, 5, until=lambda: win._side_layout is not None or (win._render_preview() or False))
    lay = win._side_layout
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("серая плашка", True)))
    win._on_rect_picked(QRectF(10, 10, 40, 30))                                   # на референсе
    win._on_rect_picked(QRectF(lay["ref_w"] + lay["gap"] + 10, 10, 40, 30))       # на текущем
    anchors = win.state.ws.locate(a.id)[1].anchors
    assert len(anchors) == 1 and anchors[0].label == "серая плашка"
    assert anchors[0].ref.image == win.state.ws.locate(ref.id)[1].path
    assert all(0 <= v <= 1 for v in anchors[0].target.rect)
    win._render_preview()
    assert len(win.viewer.overlays) == 2                                           # рамки на обеих половинах
    win.clear_anchors()
    assert not win.state.ws.locate(a.id)[1].anchors


def test_window_autosave_and_restore(qapp, win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    pid = next(win.state.ws.all_photos()).id
    win.state.edit_stacks([pid], lambda s, _p: s.set("hs.hue", 11), "hue")
    win._autosave()
    auto = win._autosave_path()
    assert auto.exists()
    path = str(win.state.path)
    win.state.undo.setClean()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win.open_path(path, confirm=False)
    assert win.state.dirty and str(win.state.path) == path
    assert win.state.ws.locate(pid)[1].stack.get("hs.hue") == 11
    win.save()
    assert not auto.exists()


def test_slider_wheel_sensitivity(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from colormatcher.core.filters import HueSaturation
    from colormatcher.gui import theme
    from colormatcher.gui.widgets.slider import ParamRow

    row = ParamRow(HueSaturation.spec("saturation"), theme.ThemeHolder(theme.get("moonlight")))
    row.slider.sensitivity = 2.0

    def wheel(mods):
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, mods,
                         Qt.NoScrollPhase, False)
        row.slider.wheelEvent(ev)

    wheel(Qt.NoModifier)
    assert row.value() == 2
    wheel(Qt.ShiftModifier)
    assert row.value() == 22


def test_top_strip_fixed_and_compact(qapp, win):
    from PySide6.QtWidgets import QDockWidget

    assert win.dock_top.features() == QDockWidget.NoDockWidgetFeatures      # не открепить/закрыть/сдвинуть
    assert win.film is win.top.film
    one = win.top.list.width()
    ws = win.state.ws
    ids = [p.id for p in ws.scene("shoot/day").photos][:2]
    win.state.select(ids, ids[0])
    win.make_ref()
    pump(qapp, 0.1)
    assert win.top.list.count() == 2 and win.top.list.width() > one        # ширина — по числу референсов
    assert win.top.list.width() < win.top.film.width()


def test_peek_keys_before_and_reference(qapp, win, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    ws = win.state.ws
    ref = ws.find("ref.png")
    win.state.select([ref.id], ref.id)
    win.make_ref()
    a = next(p for p in ws.scene("shoot/day").photos if p.name == "a.png")
    win.state.select([a.id], a.id)
    win.state.edit_stacks([a.id], lambda s, _p: s.set("hs.saturation", -80), "sat")
    assert pump(qapp, 5, until=lambda: win.cache.proxy(a.id, ws.abs_path(a)) is not None
                and win.cache.proxy(ref.id, ws.abs_path(ref)) is not None)
    win._render_preview()
    edited = win.viewer._item.pixmap().toImage()
    monkeypatch.setattr(win, "isActiveWindow", lambda: True)
    key = lambda t, k: win.eventFilter(win, QKeyEvent(t, k, Qt.NoModifier))  # noqa: E731
    assert key(QEvent.KeyPress, Qt.Key_Z)
    assert win.peek == "before" and win.viewer.labels[0].startswith("ДО")
    assert win.viewer._item.pixmap().toImage() != edited
    assert key(QEvent.KeyRelease, Qt.Key_Z) and win.peek is None
    assert win.viewer._item.pixmap().toImage() == edited
    assert key(QEvent.KeyPress, Qt.Key_X)
    assert win.viewer.labels[0].startswith("РЕФЕРЕНС")
    key(QEvent.KeyRelease, Qt.Key_X)
    assert win.peek is None


def test_settings_migration_v1(tmp_path, monkeypatch):
    import json

    from colormatcher.gui.settings import AppSettings

    monkeypatch.setenv("COLORMATCHER_HOME", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"theme": "nord", "viewer_bg": "gray18", "window_state": "abc"}),
                                            encoding="utf-8")
    s = AppSettings.load()
    assert s.theme == "nord" and s.viewer_bg == "theme" and s.window_state == "" and s.version == 2
    assert AppSettings().viewer_bg == "theme"


def test_ctrl_r_toggles_reference(qapp, win):
    ws = win.state.ws
    pid = ws.find("ref.png").id
    win.state.select([pid], pid)
    win.a_ref.trigger()
    assert win.state.ws.active_ref == pid
    win.a_ref.trigger()                                # повторно — убрать (T-0043)
    assert not win.state.ws.refs
    assert win.a_match_sel.shortcut().toString() == "B"  # T-0047


def test_ref_strip_click_sets_active(qapp, win):
    ws = win.state.ws
    ids = [ws.find("ref.png").id, ws.find("c.png").id]
    win.state.select(ids, ids[0])
    win.a_ref.trigger()
    pump(qapp, 0.1)
    assert win.top.list.count() == 2 and win.state.ws.active_ref == ids[0]
    win.top.list.item(1).setSelected(True)             # клик по второму референсу
    assert win.state.ws.active_ref == ids[1]
    assert "активный" in win.top.scope.text()
    assert win.match_panel.ref_label.text().count("c.png") == 1


def test_slider_click_jump_steps_and_center_zero(qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from colormatcher.core.filters import BrightnessContrast, HueSaturation
    from colormatcher.gui import theme
    from colormatcher.gui.widgets.slider import ParamRow

    th = theme.ThemeHolder(theme.get("moonlight"))
    row = ParamRow(HueSaturation.spec("saturation"), th)
    row.resize(400, 30)
    row.show()
    s = row.slider
    x = lambda v: int(s._x_of(s.to_pos(v)))  # noqa: E731
    y = s.height() // 2
    QTest.mouseClick(s, Qt.LeftButton, Qt.NoModifier, QPoint(x(60), y))
    assert abs(row.value() - 60) <= 1                    # в точку клика (T-0046)
    QTest.mouseClick(s, Qt.LeftButton, Qt.ControlModifier, QPoint(x(-80), y))
    v1 = row.value()
    assert abs(v1 - 50) <= 1                             # Ctrl — шаг 10 в сторону клика
    QTest.mouseClick(s, Qt.LeftButton, Qt.ShiftModifier, QPoint(x(-80), y))
    assert row.value() == v1 - 20                        # Shift — шаг 20
    QTest.mouseClick(s, Qt.LeftButton, Qt.ControlModifier, QPoint(x(90), y))
    assert row.value() == v1 - 10                        # Ctrl — шаг 10 к точке клика

    c = ParamRow(BrightnessContrast.spec("contrast"), th)
    c.resize(400, 30)
    c.show()
    g = c.slider._groove()
    assert abs(c.slider._x_of(c.slider.to_pos(0)) - g.center().x()) < 1   # ноль посередине (T-0049)
    assert c.slider.to_value(c.slider.minimum()) == -50 and c.slider.to_value(c.slider.maximum()) == 100


def test_adjustment_sliders_equal_width(qapp, win):
    pid = next(win.state.ws.all_photos()).id
    win.state.select([pid], pid)
    win.resize(1500, 950)
    pump(qapp, 0.2)
    widths = {r.slider.width() for r in win.adjust.rows.values() if r.isVisible()}
    assert len(widths) == 1                              # T-0048


def test_match_progress_badge(qapp, win):
    win._on_match_progress(2, 5)
    assert "2 из 5" in win.viewer.badge and win.busy.isVisible()
    win._on_match_progress(0, 0)
    assert win.viewer.badge == "" and not win.busy.isVisible()


def test_startup_reopens_last_workspace(tmp_path):
    from colormatcher.gui.app import startup_workspace
    from colormatcher.gui.settings import AppSettings

    a, b, auto = tmp_path / "a.cmws", tmp_path / "b.cmws", tmp_path / "a.autosave.cmws"
    for f in (a, b, auto):
        f.write_text("{}", encoding="utf-8")
    s = AppSettings(recent=[str(tmp_path / "gone.cmws"), str(auto), str(b), str(a)])
    assert startup_workspace(["app"], s) == b                      # первый существующий, не автосохранение
    assert startup_workspace(["app", str(a)], s) == a              # файл из аргументов важнее
    s.reopen_last = False
    assert startup_workspace(["app"], s) is None                   # выключено в настройках
    assert startup_workspace(["app"], AppSettings()) is None       # недавних нет


def test_tree_root_shows_all_photos_in_film(qapp, win):
    from PySide6.QtCore import Qt

    tree, ws = win.tree, win.state.ws
    root = tree.topLevelItem(0)
    assert root.text(0).startswith("Все фото") and "(4)" in root.text(0)
    assert win.film_scope is None and len(win.film.ids()) == 4           # после открытия — все фото
    night = next(it for it in tree._scene_items() if it.text(0).startswith("shoot/night"))
    sel_before = list(win.state.selection)
    tree.itemClicked.emit(night, 0)                                      # клик по папке
    assert win.film.ids() == [p.id for p in ws.scene("shoot/night").photos]
    assert "КАДРЫ СЦЕНЫ" in win.top.scene_title.text()
    assert win.state.selection == sel_before                             # выделение не тронуто
    tree.itemClicked.emit(root, 0)                                       # клик по корню
    assert len(win.film.ids()) == 4 and win.top.scene_title.text().startswith("ВСЕ ФОТО")
    tree.itemClicked.emit(night, 0)
    day_photo = ws.scene("shoot/day").photos[0].id
    item = next(it for it in tree._photo_items() if it.data(0, Qt.UserRole) == day_photo)
    tree.itemClicked.emit(item, 0)                                       # клик по фото из другой папки
    assert win.film_scope == ws.scene("shoot/day").id and day_photo in win.film.ids()


def test_plural():
    from colormatcher.gui.qtutil import plural

    assert [plural(n, "папка", "папки", "папок") for n in (1, 2, 5, 11, 21, 22, 25)] == [
        "1 папка", "2 папки", "5 папок", "11 папок", "21 папка", "22 папки", "25 папок"]


def test_right_click_resets_slider_and_viewer(qapp, win):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    pid = next(win.state.ws.all_photos()).id
    win.state.select([pid], pid)
    row = win.adjust.rows["hs.saturation"]
    row.changed.emit(35.0)
    row.committed.emit()
    assert win.state.ws.locate(pid)[1].stack.get("hs.saturation") == 35
    s = row.slider
    QTest.mouseClick(s, Qt.RightButton, Qt.NoModifier, QPoint(s.width() // 4, s.height() // 2))
    assert win.state.ws.locate(pid)[1].stack.get("hs.saturation") == 0          # T-0056
    op = win.adjust.rows["opacity"]
    op.changed.emit(40.0)
    op.committed.emit()
    QTest.mouseClick(op.label, Qt.RightButton)                                  # по подписи — тоже
    assert win.state.ws.locate(pid)[1].stack.opacity == 100                    # opacity — к 100%
    win.state.undo.undo()
    assert win.state.ws.locate(pid)[1].stack.opacity == 40                     # сброс — один шаг отмены

    v = win.viewer
    assert pump(qapp, 5, until=lambda: not v._item.pixmap().isNull())
    fit_scale = v.transform().m11()
    v.zoom_100()
    v.scale(2, 2)
    v.horizontalScrollBar().setValue(v.horizontalScrollBar().maximum())
    assert v.transform().m11() != fit_scale and not v._fit
    QTest.mouseClick(v.viewport(), Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
    assert v._fit and abs(v.transform().m11() - fit_scale) < 1e-6              # T-0057
