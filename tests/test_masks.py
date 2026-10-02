"""Маски-метки (T-0058…T-0060, T-0064): кисть, веса, хранение, Undo, режим «Маски» в GUI."""

import numpy as np
import pytest
from PySide6.QtCore import QPointF

from colormatcher.models import masks as M
from colormatcher.services import workspace_io
from test_gui import pump, win, ws_file  # noqa: F401 — фикстуры окна


def test_palettes_have_seven_calm_colors():
    for key, (title, colors) in M.PALETTES.items():
        assert title and len(colors) == M.COLOR_SLOTS and len(set(colors)) == M.COLOR_SLOTS
        assert len(M.palette_colors(key)) == M.SLOTS
    assert M.palette_colors("нет такого") == M.palette_colors(M.DEFAULT_PALETTE)


def test_brush_soft_edge_and_sum_limit():
    mask = M.empty(200, 300)
    M.stamp(mask, 0, 150, 100, M.Brush(diameter=100, hardness=0.0))
    center, edge, outside = mask[100, 150, 0], mask[100, 195, 0], mask[100, 205, 0]
    assert center == 255 and 0 < edge < 128 and outside == 0
    # перекрашивание другой меткой: сумма весов ≤ 255, старая метка уступает место
    M.stamp(mask, 3, 150, 100, M.Brush(diameter=60, hardness=1.0, opacity=0.5))
    s = mask.astype(int).sum(-1)
    assert s.max() <= 255
    assert mask[100, 150, 3] in (127, 128) and mask[100, 150, 0] in (127, 128)


def test_stroke_eraser_and_coverage():
    mask = M.empty(100, 100)
    box = M.stroke(mask, 1, (10, 50), (90, 50), M.Brush(diameter=20, hardness=1.0))
    assert box is not None and box[0] <= 10 and box[2] >= 99   # p0 ставит предыдущий сегмент
    cov = M.coverage(mask)
    assert set(cov) == {1} and 0.15 < cov[1] < 0.25          # полоса ~20% кадра
    M.stroke(mask, 1, (0, 50), (100, 50), M.Brush(diameter=40, hardness=1.0), erase=True)
    assert M.coverage(mask) == {}
    assert M.stamp(mask, 0, -500, -500, M.Brush(diameter=10)) is None   # за кадром — ничего


def test_shape_for_keeps_aspect():
    assert M.shape_for(1000, 1500) == (683, 1024)
    assert M.shape_for(1600, 900) == (1024, 576)


def test_masks_saved_loaded_and_cleaned(tmp_path, ws_file):  # noqa: F811
    ws = workspace_io.load(ws_file)
    pids = [p.id for p in ws.all_photos()]
    m = M.empty(64, 96)
    M.stamp(m, 2, 40, 30, M.Brush(diameter=30))
    ws.set_mask(pids[0], m)
    workspace_io.save(ws, ws_file)
    folder = workspace_io.masks_dir(ws_file)
    assert folder.name == "w.masks" and (folder / f"{pids[0]}.npz").exists()
    back = workspace_io.load(ws_file)
    assert np.array_equal(back.mask(pids[0]), m) and back.mask(pids[1]) is None
    # упаковка переносит маски; удалённое фото — файл маски убирается при сохранении
    packed = workspace_io.load(workspace_io.pack(back, tmp_path / "packed"))
    assert np.array_equal(packed.mask(pids[0]), m)
    back.remove_photos({pids[0]})
    workspace_io.save(back, ws_file)
    assert not folder.exists()


def test_state_mask_undo_and_snapshot_keeps_masks(qapp, ws_file):  # noqa: F811
    from colormatcher.gui.state import AppState

    st = AppState()
    st.open(ws_file)
    pid = next(st.ws.all_photos()).id
    mask = M.empty(50, 50)
    st.ws.masks[pid] = mask
    before = mask.copy()
    box = M.stroke(mask, 0, (10, 10), (40, 40), M.Brush(diameter=10))
    st.push_mask_edit("мазок", pid, box, before[box[1]:box[3], box[0]:box[2]])
    assert st.dirty and st.ws.mask(pid).any()
    st.mutate("ref", lambda ws: ws.add_ref(pid))          # снимок воркспейса …
    st.undo.undo()
    assert st.ws.mask(pid) is not None                    # … маску не теряет
    st.undo.undo()
    assert st.ws.mask(pid) is None                        # мазок отменён -> маски нет
    st.undo.redo()
    assert st.ws.mask(pid)[..., 0].any()
    st.remove_mask_slot(pid, 0)
    assert st.ws.mask(pid) is None
    st.undo.undo()
    assert st.ws.mask(pid)[..., 0].any()


def test_window_masks_mode_paint_undo_and_panel(qapp, win):  # noqa: F811
    w = win
    pid = w.state.current
    assert pump(qapp, 5, until=lambda: w.cache.proxy(pid, w.state.ws.abs_path(w.state.ws.locate(pid)[1])) is not None)
    w.set_view_mode("masks")
    pump(qapp, 0.2)
    assert w.mask_panel.isVisible() and w.viewer.paint_mode
    assert w.a_brush.shortcut().toString() == "Q" and w.a_match_sel.shortcut().toString() == "B"   # T-0081
    fw, fh = w.viewer.frame_size()
    assert fw > 0 and w.viewer.brush_px > 0
    w.mask_panel.select_slot(4)
    w.viewer.stroke_started.emit(QPointF(fw * 0.3, fh * 0.5), False)
    w.viewer.stroke_moved.emit(QPointF(fw * 0.7, fh * 0.5))
    w.viewer.stroke_ended.emit()
    pump(qapp, 0.1)
    cov = M.coverage(w.state.ws.mask(pid))
    assert set(cov) == {4} and cov[4] > 0.02
    assert w.mask_panel.list.count() == 1 and w._mask_ov is not None
    w.state.undo.undo()
    pump(qapp, 0.1)
    assert w.state.ws.mask(pid) is None and w.mask_panel.list.count() == 0 and w._mask_ov is None
    w.state.undo.redo()
    pump(qapp, 0.1)
    assert w.mask_panel.list.count() == 1
    # палитра из настроек (T-0064) и выход из режима
    w.settings.mask_palette = "deep"
    w.apply_settings()
    assert w.mask_panel.colors[:7] == M.PALETTES["deep"][1]
    w.set_view_mode("single")
    pump(qapp, 0.1)
    assert not w.mask_panel.isVisible() and not w.viewer.paint_mode and w._mask_ov is None
    assert w.a_match_sel.shortcut().toString() == "B"


def test_window_save_and_autosave_write_masks(qapp, win, monkeypatch):  # noqa: F811
    w = win
    pid = w.state.current
    m = M.empty(40, 60)
    M.stamp(m, 1, 30, 20, M.Brush(diameter=20))
    w.state.ws.set_mask(pid, m)
    w.state.mark_dirty("маска")
    w._autosave()
    auto = w._autosave_path()
    assert (workspace_io.masks_dir(auto) / f"{pid}.npz").exists()
    assert w.save()
    assert (workspace_io.masks_dir(w.state.path) / f"{pid}.npz").exists()
    assert not workspace_io.masks_dir(auto).exists()


def test_stroke_is_continuous_worm_without_beads():
    """T-0071: мазок — сплошная капсула; перекрытия внутри мазка не копятся (максимум)."""
    mask = M.empty(100, 300)
    st = M.Stroke(mask, 6, M.Brush(diameter=30, hardness=0.8, opacity=0.13))
    pts = [(20 + 7 * i, 50) for i in range(38)]      # частые мелкие отрезки, как от мыши
    for p0, p1 in zip(pts, pts[1:]):
        st.add(p0, p1)
    axis = mask[50, 25:275, 6].astype(int)
    assert axis.min() == axis.max() == 33               # 13% ровно по всей длине, без «бус»
    across = mask[35:66, 150, 6].astype(int)
    assert across[15] == 33 and mask[34, 150, 6] == 0 and 0 < across[1] < 33   # мягкий край поперёк (жёсткость 80% — край ~3 px)
    assert st.box[0] <= 5 and st.box[2] >= 280


def test_eraser_stroke_uses_max_too():
    mask = M.empty(60, 60)
    M.stamp(mask, 0, 30, 30, M.Brush(diameter=60, hardness=1.0))
    st = M.Stroke(mask, 0, M.Brush(diameter=20, hardness=1.0, opacity=0.5), erase=True)
    for _ in range(5):
        st.add((20, 30), (40, 30))                     # водим по одному месту — стирается на 50%, не больше
    assert mask[30, 30, 0] in (127, 128)


def test_viewer_wheel_zoom_keeps_point_under_cursor_and_alt(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QImage, QWheelEvent

    from colormatcher.gui.widgets.viewer import Viewer

    v = Viewer()
    v.resize(800, 600)
    v.show()
    img = QImage(400, 300, QImage.Format_RGB888)
    img.fill(0x335577)
    v.set_image(img)
    pump(qapp, 0.1)

    def wheel(pos, dy=0, dx=0, mods=Qt.NoModifier):
        ev = QWheelEvent(QPointF(pos), QPointF(v.viewport().mapToGlobal(pos)), QPoint(), QPoint(dx, dy),
                         Qt.NoButton, mods, Qt.NoScrollPhase, False)
        v.wheelEvent(ev)

    cursor = QPoint(200, 150)                        # не центр окна
    target = v.mapToScene(cursor)
    for _ in range(6):
        wheel(cursor, dy=120)
    after = v.mapToScene(cursor)
    assert abs(after.x() - target.x()) < 2 and abs(after.y() - target.y()) < 2
    z = v.transform().m11()
    wheel(cursor, dx=120, mods=Qt.AltModifier)       # Alt: Qt отдаёт горизонтальную прокрутку
    assert v.transform().m11() > z
    wheel(cursor, dx=-120, mods=Qt.AltModifier)
    assert abs(v.transform().m11() - z) < 1e-6


def test_bracket_keys_any_layout(qapp, win):  # noqa: F811
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    w = win
    w.activateWindow()
    w.set_view_mode("masks")
    monkey_active = w.isActiveWindow
    w.isActiveWindow = lambda: True
    try:
        d0, h0 = w.mask_panel.diameter.value(), w.mask_panel.spread.value()
        # русская раскладка: символ «ъ», но физическая клавиша ] (VK_OEM_6)
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key(0x42A), Qt.NoModifier, 0, 0xDD, 0, "ъ")
        assert w.eventFilter(w, ev)
        assert w.mask_panel.diameter.value() > d0
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_BraceLeft, Qt.ShiftModifier, 0, 0xDB, 0, "{")
        assert w.eventFilter(w, ev)
        assert w.mask_panel.spread.value() < h0          # Shift+[ — распределение
    finally:
        w.isActiveWindow = monkey_active
    w.set_view_mode("single")
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_BracketRight, Qt.NoModifier, 0, 0xDD, 0, "]")
    assert not w.eventFilter(w, ev)                  # вне режима масок клавиша не перехватывается


def test_brush_spread_fills_whole_diameter():
    """T-0075/T-0076: кисть всегда на весь диаметр; при 0% — плотное ядро ~10%, дальше плавный спад."""
    d = np.array([0.0, 0.5, 0.75, 0.9, 0.97, 1.0])
    soft, mid, solid = (M.brush_profile(d, v) for v in (0.0, 0.5, 1.0))
    for prof in (soft, mid, solid):
        assert prof[0] == 1.0 and prof[-1] == 0.0
        assert np.all(np.diff(prof) <= 1e-7)                     # монотонно гаснет к краю
        assert prof[3] > 0.005                                   # до края не обрывается
    assert soft[2] < mid[2] < solid[2] and soft[3] < mid[3] < solid[3]
    assert 0.2 < soft[1] < 0.3 and solid[3] > 0.9                # мягкая: видимый спад; сплошная: почти 100% у края
    xs = np.linspace(0, 1, 10001)
    cores = [xs[np.argmax(M.brush_profile(xs, v) < 0.8)] for v in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert 0.08 < cores[0] < 0.13                                # плотное ядро ~10% при минимуме
    assert cores == sorted(cores) and cores[-1] > 0.9
    steps = np.diff(cores)
    assert steps.max() - steps.min() < 0.02                      # шкала равномерная (T-0078)


def test_brush_centered_on_cursor():
    """T-0077: пятно симметрично относительно точки кисти (центры пикселей)."""
    mask = M.empty(21, 21)
    M.stamp(mask, 0, 10.0, 10.0, M.Brush(diameter=9, spread=1.0))   # точка — граница пикселей 9 и 10
    m = mask[..., 0].astype(int)
    assert m[10, 9] == m[10, 10] and m[9, 10] == m[10, 10]
    assert np.array_equal(m[:, :10], m[:, 10:20][:, ::-1])       # лево/право зеркальны вокруг x = 10
    mask = M.empty(21, 21)
    M.stamp(mask, 0, 10.5, 10.5, M.Brush(diameter=9, spread=1.0))   # центр пикселя 10
    m = mask[..., 0].astype(int)
    assert np.array_equal(m, m[::-1, ::-1]) and m[10, 10] == m.max()


def test_mask_panel_sliders_click_to_point(qapp, win):  # noqa: F811
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent

    w = win
    w.set_view_mode("masks")
    pump(qapp, 0.1)
    assert not hasattr(w.mask_panel, "hardness") and not hasattr(w.mask_panel, "opacity")  # скрыты (T-0075)
    sl = w.mask_panel.spread.slider
    g = sl._groove()
    x = g.left() + g.width() * 0.75
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, sl.height() / 2), QPointF(x, sl.height() / 2),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    sl.mousePressEvent(ev)
    sl.mouseReleaseEvent(ev)
    assert abs(w.mask_panel.spread.value() - 0.75) < 0.02
    b = w.mask_panel.brush()
    assert b.hardness == 0.0 and b.opacity == 1.0 and b.spread == pytest.approx(w.mask_panel.spread.value())
    assert w.settings.brush_spread == pytest.approx(b.spread) and w.mask_panel.preview.brush.spread == pytest.approx(b.spread)
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(x, 5), QPointF(x, 5), Qt.RightButton, Qt.RightButton, Qt.NoModifier)
    sl.mousePressEvent(ev)
    assert abs(w.mask_panel.spread.value() - 0.2) < 1e-9       # правый клик — сброс


def test_window_fits_small_screens(qapp, win):  # noqa: F811
    """T-0079: окно сжимается по высоте во всех режимах (раньше в «Масках» — не меньше ~1150 px)."""
    w = win
    for mode in ("single", "masks"):
        w.set_view_mode(mode)
        pump(qapp, 0.05)
        assert w.minimumSizeHint().height() < 620, mode
    assert w.mask_box.isVisible() and w.mask_panel.buttons[0].isVisible()
    w.set_view_mode("single")
