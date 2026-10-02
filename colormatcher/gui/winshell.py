"""Интеграция с панелью задач Windows (T-0041, T-0052).

Окно принадлежит процессу pythonw.exe. Чтобы в панели задач была наша иконка, процессу
задаётся свой AppUserModelID. Но при закреплении («Закрепить на панели задач») Windows
берёт команду повторного запуска, имя и иконку из СВОЙСТВ ОКНА — без них закреплённый
значок пустой. Здесь они записываются в окно через IPropertyStore:
  System.AppUserModel.ID                         — тот же, что у процесса;
  System.AppUserModel.RelaunchCommand            — ColorMatcher.exe (или pythonw -m colormatcher.gui);
  System.AppUserModel.RelaunchDisplayNameResource — «Image ColorMatcher»;
  System.AppUserModel.RelaunchIconResource       — moon.ico.
Всё через ctypes (без pywin32). Вне Windows функции ничего не делают.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_ulong, c_ushort, c_void_p, c_wchar_p
from pathlib import Path

APP_ID = "colormatcher.image-colormatcher"
APP_NAME = "Image ColorMatcher"
ROOT = Path(__file__).resolve().parents[2]
ICON_ICO = Path(__file__).with_name("icons") / "moon.ico"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        g = cls()
        ctypes.oledll.ole32.CLSIDFromString(c_wchar_p(s), byref(g))
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(ctypes.Structure):
    # vt + 3 резервных WORD + объединение (указатель на строку) — 24 байта на x64
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("pwszVal", c_void_p), ("pad", c_void_p)]


VT_LPWSTR = 31
_FMTID_AUMID = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
PID_ID, PID_RELAUNCH_COMMAND, PID_RELAUNCH_ICON, PID_RELAUNCH_NAME = 5, 2, 3, 4

# vtable IPropertyStore: 0 QueryInterface, 1 AddRef, 2 Release, 3 GetCount, 4 GetAt,
# 5 GetValue, 6 SetValue, 7 Commit
_SetValue = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
_GetValue = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
_Commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p)
_Release = ctypes.WINFUNCTYPE(c_ulong, c_void_p)


def set_process_app_id() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def relaunch_command() -> str:
    exe = ROOT / "ColorMatcher.exe"
    if exe.exists():
        return f'"{exe}"'
    pyw = ROOT / "python-embed" / "pythonw.exe"
    return f'"{pyw}" -m colormatcher.gui'


def _store(hwnd: int) -> c_void_p:
    store = c_void_p()
    iid = GUID.parse(_IID_IPROPERTYSTORE)
    ctypes.oledll.shell32.SHGetPropertyStoreForWindow(c_void_p(hwnd), byref(iid), byref(store))
    return store


def _vtbl(store: c_void_p, index: int, proto):
    vtbl = ctypes.cast(store, POINTER(POINTER(c_void_p))).contents
    return proto(vtbl[index])


def _key(pid: int) -> PROPERTYKEY:
    return PROPERTYKEY(GUID.parse(_FMTID_AUMID), pid)


def set_window_relaunch(hwnd: int) -> bool:
    """Записать в окно свойства для закрепления на панели задач. True — успешно."""
    if sys.platform != "win32" or not hwnd:
        return False
    values = {
        PID_ID: APP_ID,
        PID_RELAUNCH_COMMAND: relaunch_command(),
        PID_RELAUNCH_NAME: APP_NAME,
        PID_RELAUNCH_ICON: f"{ICON_ICO},0",
    }
    store = _store(hwnd)
    try:
        set_value = _vtbl(store, 6, _SetValue)
        keep = []  # строки должны жить до вызова SetValue (он копирует значение)
        for pid, text in values.items():
            buf = ctypes.create_unicode_buffer(text)
            keep.append(buf)
            pv = PROPVARIANT(vt=VT_LPWSTR, pwszVal=ctypes.cast(buf, c_void_p))
            set_value(store, byref(_key(pid)), byref(pv))
        _vtbl(store, 7, _Commit)(store)
        return True
    finally:
        _vtbl(store, 2, _Release)(store)


def get_window_property(hwnd: int, pid: int) -> str | None:
    """Прочитать строковое свойство окна (для проверки)."""
    if sys.platform != "win32":
        return None
    store = _store(hwnd)
    try:
        pv = PROPVARIANT()
        _vtbl(store, 5, _GetValue)(store, byref(_key(pid)), byref(pv))
        if pv.vt != VT_LPWSTR or not pv.pwszVal:
            return None
        text = ctypes.wstring_at(pv.pwszVal)
        ctypes.oledll.ole32.PropVariantClear(byref(pv))
        return text
    finally:
        _vtbl(store, 2, _Release)(store)
