"""Taskbar integration without recreating the WebView2 host window."""

from __future__ import annotations

import atexit
import ctypes
import logging
from pathlib import Path

import pythoncom
import win32con
import win32gui
from win32com.shell import shell

from . import winapi

log = logging.getLogger(__name__)
APP_ID = "SelectionTranslator.Desktop"
_icons: dict[tuple[str, int], int] = {}


def set_app_identity() -> None:
    try:
        set_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_id.argtypes = [ctypes.c_wchar_p]
        set_id.restype = ctypes.c_long
        result = set_id(APP_ID)
        if result < 0:
            raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: {result:#x}")
    except Exception:
        log.warning("could not set taskbar application identity", exc_info=True)


def _set_style(hwnd: int, index: int, value: int) -> None:
    # Style changes synchronously message the GUI thread. ctypes releases the GIL
    # while waiting, so Python window callbacks on that thread can still run.
    ctypes.set_last_error(0)
    previous = winapi.user32.SetWindowLongW(hwnd, index, value)
    error = ctypes.get_last_error()
    if not previous and error:
        raise ctypes.WinError(error)


def configure_window(hwnd: int, icon_path: Path | None) -> None:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    # Frameless WinForms omits these, but the shell needs native restore/icon support.
    wanted = style | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX
    if wanted != style:
        _set_style(hwnd, win32con.GWL_STYLE, wanted)
    if icon_path and icon_path.is_file():
        for kind, size in ((win32con.ICON_SMALL, 16), (win32con.ICON_BIG, 32)):
            key = (str(icon_path), size)
            if key not in _icons:
                _icons[key] = win32gui.LoadImage(0, str(icon_path), win32con.IMAGE_ICON,
                                               size, size, win32con.LR_LOADFROMFILE)
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, kind, _icons[key])


def set_visible(hwnd: int, visible: bool) -> None:
    """Change shell registration and styles, never Form.ShowInTaskbar."""
    pythoncom.CoInitialize()
    taskbar = None
    try:
        taskbar = pythoncom.CoCreateInstance(shell.CLSID_TaskbarList, None,
                                            pythoncom.CLSCTX_INPROC_SERVER,
                                            shell.IID_ITaskbarList)
        taskbar.HrInit()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if visible:
            wanted = (style | win32con.WS_EX_APPWINDOW) & ~win32con.WS_EX_TOOLWINDOW
        else:
            wanted = (style | win32con.WS_EX_TOOLWINDOW) & ~win32con.WS_EX_APPWINDOW
        if wanted != style:
            _set_style(hwnd, win32con.GWL_EXSTYLE, wanted)
        if visible:
            taskbar.AddTab(hwnd)
        else:
            taskbar.DeleteTab(hwnd)
    finally:
        taskbar = None
        pythoncom.CoUninitialize()


@atexit.register
def _release_icons() -> None:
    for icon in _icons.values():
        win32gui.DestroyIcon(icon)
    _icons.clear()
