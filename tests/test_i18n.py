"""Локализация интерфейса (T-0083): полнота en.json, нет необёрнутых русских строк, загрузка языков."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import i18n_check  # noqa: E402

from colormatcher.gui import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def _source_language():
    yield
    i18n.load(i18n.SOURCE)   # остальные тесты — на исходном языке


def test_every_ui_string_has_english_translation():
    msgids, loose = i18n_check.scan()
    msgids |= i18n_check.core_labels()
    en = json.loads((i18n_check.LANG / "en.json").read_text(encoding="utf-8"))["strings"]
    missing = sorted(m for m in msgids if i18n_check.CYR.search(m) and not en.get(m))
    assert not missing, missing[:20]
    assert not loose, loose[:20]


def test_placeholders_match():
    en = json.loads((i18n_check.LANG / "en.json").read_text(encoding="utf-8"))["strings"]
    import string

    names = lambda s: {f for _, f, _, _ in string.Formatter().parse(s) if f}  # noqa: E731
    bad = [k for k, v in en.items() if names(k) != names(v)]
    assert not bad, bad[:10]


def test_load_languages_and_fallback(tmp_path, monkeypatch):
    langs = i18n.available()
    assert langs.get("en") == "English" and langs.get("ru") == "Русский"
    assert i18n.load("en") == "en" and i18n.tr("Настройки") == "Settings"
    assert i18n.tr("По умолчанию: {keys}", keys="B") == "Default: B"
    assert i18n.tr("строка без перевода") == "строка без перевода"
    assert i18n.load("xx") == i18n.SOURCE and i18n.tr("Настройки") == "Настройки"
    # свой язык в папке пользователя
    monkeypatch.setenv("COLORMATCHER_HOME", str(tmp_path))
    (tmp_path / "lang").mkdir()
    (tmp_path / "lang" / "de.json").write_text(json.dumps(
        {"meta": {"code": "de", "name": "Deutsch"}, "strings": {"Настройки": "Einstellungen"}}), encoding="utf-8")
    assert i18n.available()["de"] == "Deutsch"
    assert i18n.load("de") == "de" and i18n.tr("Настройки") == "Einstellungen"


def test_plural_follows_language():
    from colormatcher.gui.qtutil import plural

    i18n.load(i18n.SOURCE)
    assert plural(21, "папка", "папки", "папок") == "21 папка"
    i18n.load("en")
    assert plural(1, "папка", "папки", "папок") == "1 folder"
    assert plural(21, "папка", "папки", "папок") == "21 folders"


def test_window_builds_in_english(qapp, tmp_path):
    from colormatcher.gui.main_window import MainWindow
    from colormatcher.gui.settings import AppSettings

    i18n.load("en")
    w = MainWindow(AppSettings(language="en"))
    try:
        menus = [a.text() for a in w.menuBar().actions()]
        assert "File" in menus and not any(i18n_check.CYR.search(t) for t in menus)
        assert w.a_open.text().startswith("Open")
    finally:
        w.state.undo.setClean()
        w.close()


def test_switch_language_live_keeps_state(qapp, tmp_path):
    """T-0086: смена языка без перезапуска — окно пересобирается, состояние то же."""
    from test_gui import pump

    from colormatcher.core import imageio
    from colormatcher.gui.main_window import MainWindow
    from colormatcher.gui.settings import AppSettings
    from colormatcher.services import importer, workspace_io
    from conftest import make_scene

    for name in ("a.png", "b.png"):
        imageio.save_image(tmp_path / "shoot" / name, make_scene())
    ws = workspace_io.new_workspace(tmp_path / "w.cmws")
    importer.add_folder(ws, tmp_path / "shoot")
    path = workspace_io.save(ws, tmp_path / "w.cmws")

    i18n.load(i18n.SOURCE)
    w = MainWindow(AppSettings(language="ru"))
    w.show()
    w.open_path(str(path), confirm=False)
    ids = [p.id for p in w.state.ws.all_photos()]
    w.state.select(ids, ids[1])
    w.state.edit_stacks([ids[1]], lambda st, _p: st.set("hs.hue", 9), "hue")
    w.set_view_mode("masks")
    state, undo_count = w.state, w.state.undo.count()
    assert w.menuBar().actions()[0].text() == "Файл"

    new = w.switch_language("en")
    pump(qapp, 0.2)
    try:
        assert new is not w and new.state is state and new.state.path == path.resolve()
        assert new.state.current == ids[1] and new.state.selection == ids and new.state.undo.count() == undo_count
        assert new.view_mode == "masks" and new.menuBar().actions()[0].text() == "File"
        assert new.settings.language == "en" and i18n.current() == "en"
        assert new.adjust.target_label.text() == "Group: 2 photos"      # панели уже на английском
        new.a_undo.trigger()                                   # история Undo та же
        assert new.state.ws.locate(ids[1])[1].stack.get("hs.hue") == 0
        state.current_changed.emit(ids[0])                     # старое окно больше не подписано
        pump(qapp, 0.1)
    finally:
        new.state.undo.setClean()
        new.close()
