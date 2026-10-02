import pytest

from colormatcher.core.filters import DEFAULT_ORDER
from colormatcher.models import AdjustmentStack, Preset


def test_stack_default_order_and_set_get():
    s = AdjustmentStack()
    assert [f.type for f in s.filters] == list(DEFAULT_ORDER)
    s.set("cb.midtones.cr", 250)
    assert s.get("cb.midtones.cr") == 100
    s.set("opacity", -5)
    assert s.opacity == 0
    with pytest.raises(KeyError):
        s.set("cb.bad", 1)


def test_missing_filter_is_inserted_in_order():
    s = AdjustmentStack(filters=[])
    s.set("hs.hue", 10)
    s.set("bc.contrast", 5)
    assert [f.type for f in s.filters] == ["brightness_contrast", "hue_saturation"]


def test_json_roundtrip_normalizes():
    s = AdjustmentStack().update({"bc.brightness": 12, "cb.preserve_luminosity": False})
    back = AdjustmentStack.model_validate_json(s.model_dump_json())
    assert back == s and back.get("cb.preserve_luminosity") is False


def test_copy_groups():
    a = AdjustmentStack().update({"cb.shadows.cr": 30, "hs.saturation": -20, "opacity": 70})
    b = AdjustmentStack().update({"cb.shadows.cr": -5, "hs.saturation": 10})
    c = b.copy_groups_from(a, ["cb", "opacity"])
    assert c.get("cb.shadows.cr") == 30 and c.opacity == 70
    assert c.get("hs.saturation") == 10          # группа hs не тронута
    assert b.get("cb.shadows.cr") == -5          # исходник не изменён


def test_preset_save_load_and_partial_apply(tmp_path):
    src = AdjustmentStack().update({"bc.contrast": 20, "cb.highlights.yb": 15})
    preset = Preset.from_stack(src, name="тёплый вечер", groups=["cb"])
    path = preset.save(tmp_path / "тёплый")
    assert path.name == "тёплый.cmpreset.json"
    loaded = Preset.load(path)
    assert loaded == preset
    target = loaded.apply_to(AdjustmentStack())
    assert target.get("cb.highlights.yb") == 15 and target.get("bc.contrast") == 0


def test_preset_clipboard_roundtrip():
    p = Preset.from_stack(AdjustmentStack().update({"hs.hue": -30}))
    assert Preset.from_clipboard_text(p.to_clipboard_text()) == p
