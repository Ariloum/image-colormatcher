"""Настройки по вкладкам (T-0084) и переназначение горячих клавиш (T-0085)."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence

from test_gui import pump, win, ws_file  # noqa: F401 — фикстуры окна


def test_settings_dialog_tabs(qapp, win):  # noqa: F811
    from colormatcher.gui.dialogs import SettingsDialog

    dlg = SettingsDialog(win, win.settings, win.shortcuts)
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert len(titles) == 4
    assert dlg.language.count() >= 2 and dlg.language.findData("en") >= 0 and dlg.language.findData("ru") >= 0
    assert "match.selection" in dlg.key_edits and "masks.smaller" not in dlg.key_edits   # [ ] — справочно
    dlg.close()


def test_rebind_shortcut_and_peek(qapp, win):  # noqa: F811
    from colormatcher.gui.dialogs import SettingsDialog

    w = win
    assert w.a_match_sel.shortcut().toString() == "B"
    dlg = SettingsDialog(w, w.settings, w.shortcuts)
    dlg.applied.connect(w.apply_settings)     # как в MainWindow.settings_dialog
    dlg.key_edits["match.selection"].setKeySequence(QKeySequence("Ctrl+B"))
    dlg.key_edits["view.peek_ref"].setKeySequence(QKeySequence("V"))
    dlg.key_edits["masks.eraser"].setKeySequence(QKeySequence())          # снять клавишу
    dlg._apply()
    assert w.settings.shortcuts == {"match.selection": "Ctrl+B", "view.peek_ref": "V", "masks.eraser": ""}
    assert w.a_match_sel.shortcut().toString() == "Ctrl+B" and w.a_eraser.shortcut().isEmpty()
    w.isActiveWindow = lambda: True
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_V, Qt.NoModifier)
    assert w.eventFilter(w, ev) and w.peek == "ref"
    w.eventFilter(w, QKeyEvent(QEvent.KeyRelease, Qt.Key_V, Qt.NoModifier))
    assert w.peek is None
    assert not w.eventFilter(w, QKeyEvent(QEvent.KeyPress, Qt.Key_X, Qt.NoModifier))   # X больше не «реф»
    # конфликт подсвечивается
    dlg.key_edits["file.export"].setKeySequence(QKeySequence("Ctrl+B"))
    dlg._check_keys()
    assert "⚠" in dlg.key_status.text()
    dlg._reset_all_keys()
    dlg._apply()
    assert w.settings.shortcuts == {} and w.a_match_sel.shortcut().toString() == "B"
    dlg.close()
