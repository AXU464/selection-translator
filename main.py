"""Entry point: python main.py  (or launch run.vbs for a console-free start)."""

from __future__ import annotations

import ctypes
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MUTEX_NAME = "SelectionTranslator-3f6a1c9e-4c2b"
_mutex_handle = None


def _message_box(text: str, title: str = "划词翻译", flags: int = 0x10) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
    except Exception:
        pass


def _acquire_single_instance() -> bool:
    """Keep only one instance alive (a second one would double the hooks)."""
    global _mutex_handle
    try:
        import win32api
        import win32event
        import winerror

        _mutex_handle = win32event.CreateMutex(None, False, MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
    except Exception:
        return True  # never block startup because of this
    return True


def main() -> int:
    from app import winapi

    winapi.enable_dpi_awareness()

    if not _acquire_single_instance():
        _message_box(
            "划词翻译已经在运行了。\n\n请在系统托盘中找到「译」图标（可能需要点开隐藏的图标）。",
            "划词翻译",
            0x40,
        )
        return 0

    import logging

    try:
        from app import config as config_mod
        from app import log as log_mod

        cfg = config_mod.Config()
        log_mod.setup(cfg)
        log = logging.getLogger("main")
        log.info("starting 划词翻译 %s", _version())

        from app.app import Application

        application = Application(cfg, ROOT)
        application.run()
        application.finalize()
        log.info("bye")
    except Exception:
        import traceback

        detail = traceback.format_exc()
        try:
            logging.getLogger("main").critical("fatal error:\n%s", detail)
        except Exception:
            pass
        _message_box(
            "划词翻译启动失败，详情见 logs/app.log：\n\n" + detail[-1200:],
            "划词翻译 · 错误",
            0x10,
        )
        return 1
    return 0


def _version() -> str:
    try:
        from app import __version__

        return __version__
    except Exception:
        return "?"


if __name__ == "__main__":
    code = main()
    # Tray/GUI helpers keep non-daemon threads alive; exit deterministically.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)
