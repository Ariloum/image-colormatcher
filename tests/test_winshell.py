"""Свойства окна для закрепления на панели задач Windows (T-0052)."""

import ctypes
import sys

import pytest

from colormatcher.gui import winshell

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="только Windows")


@pytest.fixture
def hwnd():
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    h = user32.CreateWindowExW(0, "STATIC", "cm-test", 0, 0, 0, 10, 10, None, None, None, None)  # невидимое
    assert h
    yield h
    user32.DestroyWindow(ctypes.c_void_p(h))


def test_relaunch_properties_roundtrip(hwnd):
    assert winshell.set_window_relaunch(hwnd)
    assert winshell.get_window_property(hwnd, winshell.PID_ID) == winshell.APP_ID
    cmd = winshell.get_window_property(hwnd, winshell.PID_RELAUNCH_COMMAND)
    assert "ColorMatcher.exe" in cmd or "pythonw.exe" in cmd
    assert winshell.get_window_property(hwnd, winshell.PID_RELAUNCH_NAME) == winshell.APP_NAME
    icon = winshell.get_window_property(hwnd, winshell.PID_RELAUNCH_ICON)
    assert icon.endswith("moon.ico,0") and winshell.ICON_ICO.exists()
