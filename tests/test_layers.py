"""Слои меток по мягким маскам (М2: T-0061, T-0062, T-0065): композит, экспорт, хранение, GUI."""

import numpy as np
import pytest

from colormatcher.core import imageio
from colormatcher.core.pipeline import render_direct, render_masked
from colormatcher.models import AdjustmentStack
from colormatcher.models import masks as M
from colormatcher.services import export as export_svc
from colormatcher.services import workspace_io
from conftest import make_scene
from test_gui import pump, win, ws_file  # noqa: F401 — фикстуры окна


def _mask_halves(h, w, slot=0, weight=255):
    """Метка `slot` на левой половине кадра (вес weight), правая — фон."""
    m = M.empty(h, w)
    m[:, : w // 2, slot] = weight
    return m


def test_render_masked_layer_only_under_mask():
    img = make_scene()
    g_stack = AdjustmentStack().update({"cb.midtones.cr": 30})
    layer = AdjustmentStack().update({"bc.brightness": -60})
    mask = _mask_halves(*img.shape[:2])
    out = render_masked(img, g_stack, {0: layer}, mask, lut_size=33)
    g = render_direct(img, g_stack)
    lg = render_direct(g, layer)
    w2 = img.shape[1] // 2
    assert np.abs(out[:, w2 + 2:] - g[:, w2 + 2:]).max() < 0.02        # фон — только общие правки
    assert np.abs(out[:, : w2 - 2] - lg[:, : w2 - 2]).max() < 0.02      # под маской — слой поверх общих
    assert out[:, : w2 - 2].mean() < g[:, : w2 - 2].mean() - 0.05


def test_render_masked_soft_weights_and_keep():
    img = make_scene()
    g_stack = AdjustmentStack().update({"hs.saturation": -80})
    layer = AdjustmentStack().update({"bc.brightness": 50})
    h, w = img.shape[:2]
    mask = M.empty(h, w)
    mask[:, :60, 0] = 128                       # полвеса — середина между общими правками и слоем
    mask[:, 120:, M.KEEP] = 255                 # «не трогать» — исходник
    out = render_masked(img, g_stack, {0: layer}, mask, lut_size=33)
    g = render_direct(img, g_stack)
    lg = render_direct(g, layer)
    half = g + (lg - g) * (128 / 255)
    assert np.abs(out[:, :58] - half[:, :58]).max() < 0.02
    assert np.abs(out[:, 122:] - img[:, 122:]).max() < 1e-5
    assert np.abs(out[:, 62:118] - g[:, 62:118]).max() < 0.02
    # без маски и без слоёв — то же, что обычный рендер
    assert np.abs(render_masked(img, g_stack, {}, None, lut_size=33) - g).max() < 0.02


def test_render_masked_layers_are_parallel():
    """Порядок меток не влияет: слои смешиваются параллельно от общего кадра."""
    img = make_scene()
    h, w = img.shape[:2]
    mask = M.empty(h, w)
    mask[..., 0], mask[..., 1] = 100, 100
    a = AdjustmentStack().update({"bc.brightness": 40})
    b = AdjustmentStack().update({"cb.midtones.yb": 40})
    o1 = render_masked(img, AdjustmentStack(), {0: a, 1: b}, mask, lut_size=33)
    o2 = render_masked(img, AdjustmentStack(), {1: b, 0: a}, mask, lut_size=33)
    assert np.allclose(o1, o2, atol=1e-6)


def test_mask_scaled_to_any_frame_size():
    img = make_scene(240, 360)
    mask = _mask_halves(60, 90)                  # маска в 4 раза меньше кадра
    out = render_masked(img, AdjustmentStack(), {0: AdjustmentStack().update({"bc.brightness": -60})}, mask, lut_size=33)
    assert out[:, :170].mean() < img[:, :170].mean() - 0.05 and np.abs(out[:, 190:] - img[:, 190:]).max() < 1e-5


def test_layers_saved_and_exported(tmp_path, ws_file):  # noqa: F811
    ws = workspace_io.load(ws_file)
    ph = next(ws.all_photos())
    src = imageio.load_image(ws.abs_path(ph)).rgb
    ph.layers[0] = AdjustmentStack().update({"bc.brightness": -70})
    ws.set_mask(ph.id, _mask_halves(40, 60))
    workspace_io.save(ws, ws_file)
    back = workspace_io.load(ws_file)
    bph = back.locate(ph.id)[1]
    assert bph.layers[0].get("bc.brightness") == -70 and bph.active_layers()
    res = export_svc.export(back, {ph.id}, settings=back.export.model_copy(update={"out_dir": str(tmp_path / "out")}),
                            workers=1)
    assert "error" not in res[0], res
    out = imageio.load_image(res[0]["dst"]).rgb
    w = out.shape[1]
    assert out[:, : w // 2 - 4].mean() < src[:, : w // 2 - 4].mean() - 0.05          # слой применён под маской
    assert abs(out[:, w // 2 + 4:].mean() - src[:, w // 2 + 4:].mean()) < 0.01       # фон не тронут


def test_window_layer_switch_edit_undo(qapp, win):  # noqa: F811
    w = win
    pid = w.state.current
    ph = w.state.ws.locate(pid)[1]
    assert pump(qapp, 5, until=lambda: w.cache.proxy(pid, w.state.ws.abs_path(ph)) is not None)
    adj = w.adjust
    assert not adj.layer_row.isVisible()                     # масок нет — переключателя нет
    w.state.ws.set_mask(pid, _mask_halves(40, 60, slot=2))
    w.state.masks_changed.emit(pid)
    pump(qapp, 0.1)
    assert adj.layer_row.isVisible() and adj.layer_btns[2].isVisible() and not adj.layer_btns[0].isVisible()
    stack_before = ph.stack.model_dump_json()
    adj.layer_btns[2].click()
    assert w.state.active_layer == 2 and adj.layer_hint.isVisible()
    adj._on_change("bc.brightness", -50)
    adj._on_commit("bc.brightness")
    ph = w.state.ws.locate(pid)[1]
    assert ph.layers[2].get("bc.brightness") == -50 and ph.stack.model_dump_json() == stack_before
    assert adj._row_for("bc.brightness").value() == -50
    pump(qapp, 0.2)
    key = w._photo_key(ph)
    assert "2=" in key and key in w._frames                    # кадр отрендерен со слоем
    w.state.undo.undo()
    ph = w.state.ws.locate(pid)[1]
    assert 2 not in ph.layers                                  # пустой слой не хранится
    w.state.undo.redo()
    assert w.state.ws.locate(pid)[1].layers[2].get("bc.brightness") == -50
    adj.layer_group.button(100).click()                        # «Общее»
    assert w.state.active_layer is None and adj._row_for("bc.brightness").value() == 0
    # фото без этой метки — панель сама возвращается к общим правкам
    adj.layer_btns[2].click()
    other = [p.id for p in w.state.ws.all_photos() if p.id != pid][0]
    w.state.select([other], other)
    pump(qapp, 0.1)
    assert w.state.active_layer is None and not adj.layer_row.isVisible()


def test_ref_thumbnails_show_masks_in_masks_mode(qapp, win):  # noqa: F811
    w = win
    w.set_view_mode("masks")
    assert w.ref_strip.list.mask_overlay is not None
    w.set_view_mode("single")
    assert w.ref_strip.list.mask_overlay is None


def test_thumb_variants_do_not_ping_pong(qapp, ws_file):  # noqa: F811
    """T-0080: одно фото в двух лентах (с оверлеем масок и без) — оба варианта в кеше, пересчётов нет."""
    from colormatcher.gui.imagecache import ImageCache

    ws = workspace_io.load(ws_file)
    ph = next(ws.all_photos())
    path = ws.abs_path(ph)
    mask = _mask_halves(40, 60)
    cache = ImageCache(400)
    starts = []
    orig = cache.pool.start
    cache.pool.start = lambda task: (starts.append(task), orig(task))[1]
    overlay = (tuple(M.palette_colors("calm")), 0.55)

    def both():
        return (cache.thumb(ph.id, path, ph.stack, {}, None),
                cache.thumb(ph.id, path, ph.stack, {}, mask, 1, overlay))

    assert pump(qapp, 5, until=lambda: cache.base(ph.id, path) is not None)
    both()
    assert pump(qapp, 5, until=lambda: all(x is not None for x in both()))
    n = len(starts)
    for _ in range(20):                      # ленты перерисовываются много раз
        plain, ov = both()
        pump(qapp, 0.01)
    assert len(starts) == n                  # ничего не пересчитывается
    assert plain is not None and ov is not None and plain != ov


def test_background_layer_only_outside_labels():
    """T-0082: слой «Фон» — только там, где нет ни одной метки; без маски — весь кадр."""
    img = make_scene()
    h, w = img.shape[:2]
    mask = M.empty(h, w)
    mask[:, :60, 0] = 255                        # метка — левая треть
    mask[:, 120:, M.KEEP] = 255                  # «не трогать» — правая треть
    bg = AdjustmentStack().update({"bc.brightness": -60})
    out = render_masked(img, AdjustmentStack(), {M.BG_LAYER: bg}, mask, lut_size=33)
    darker = render_direct(img, bg)
    assert np.abs(out[:, :58] - img[:, :58]).max() < 1e-5                   # под меткой — без фона
    assert np.abs(out[:, 62:118] - darker[:, 62:118]).max() < 0.02          # фон
    assert np.abs(out[:, 122:] - img[:, 122:]).max() < 1e-5                 # «не трогать»
    whole = render_masked(img, AdjustmentStack(), {M.BG_LAYER: bg}, None, lut_size=33)
    assert np.abs(whole - darker).max() < 0.02


def test_window_background_button_and_auto_balance_keeps_it(qapp, win):  # noqa: F811
    w = win
    pid = w.state.current
    w.state.ws.set_mask(pid, _mask_halves(40, 60, slot=1))
    w.state.masks_changed.emit(pid)
    pump(qapp, 0.1)
    adj = w.adjust
    assert adj.bg_btn.isVisible()
    adj.bg_btn.click()
    assert w.state.active_layer == M.BG_LAYER and "Фон" in adj.layer_hint.text()
    adj._on_change("hs.saturation", -40)
    adj._on_commit("hs.saturation")
    ph = w.state.ws.locate(pid)[1]
    assert ph.layers[M.BG_LAYER].get("hs.saturation") == -40 and ph.stack.get("hs.saturation") == 0
    assert adj.bg_btn.text() == "Фон •"
    w.state.undo.undo()
    assert M.BG_LAYER not in w.state.ws.locate(pid)[1].layers
    w.state.undo.redo()
    # авто-баланс заполняет общее и метки, «Фон» — только ручной
    from colormatcher.services import matching

    ws = w.state.ws.model_copy(deep=True)
    ws.set_root(w.state.ws.root)
    ref = [p.id for p in ws.all_photos() if p.id != pid][0]
    ws.add_ref(ref)
    matching.match_workspace(ws, photo_ids={pid}, workers=1)
    assert ws.locate(pid)[1].layers[M.BG_LAYER].get("hs.saturation") == -40
