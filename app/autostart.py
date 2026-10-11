"""Start-with-Windows support via a shortcut in the user's Startup folder."""

from __future__ import annotations

import logging
import pathlib
import sys

log = logging.getLogger(__name__)

SHORTCUT_NAME = "划词翻译.lnk"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def startup_dir() -> pathlib.Path:
    return pathlib.Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> pathlib.Path:
    return startup_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    try:
        return shortcut_path().exists()
    except Exception:
        return False


def _target() -> tuple[str, str]:
    """Return (executable, arguments) used for the shortcut."""
    pythonw = pathlib.Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    return exe, f'"{ROOT / "main.py"}"'


def set_enabled(enabled: bool) -> bool:
    path = shortcut_path()
    try:
        if not enabled:
            if path.exists():
                from win32com.shell import shell, shellcon
                resolved = path.resolve(strict=True)
                result, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, str(resolved), None,
                    shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT, None, None))
                if result or aborted or path.exists():
                    raise OSError("Could not recycle the startup shortcut")
            return True

        exe, args = _target()
        import pythoncom  # type: ignore
        from win32com.client import Dispatch  # type: ignore

        pythoncom.CoInitialize()
        shell = shortcut = None
        try:
            shell = Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(path))
            shortcut.TargetPath = exe
            shortcut.Arguments = args
            shortcut.WorkingDirectory = str(ROOT)
            shortcut.Description = "划词翻译 (DeepSeek)"
            shortcut.Save()
        finally:
            shortcut = shell = None
            pythoncom.CoUninitialize()
        return True
    except Exception:
        log.exception("could not update autostart shortcut")
        return False


def ensure_default(cfg) -> None:
    """Enable once; never override a later manual opt-out or deleted shortcut."""
    if cfg.get("behavior.autostart_initialized", False):
        return
    if is_enabled() or set_enabled(True):
        cfg.update_values({"behavior.autostart_initialized": True})
