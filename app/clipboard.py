"""Clipboard helpers: sequence tracking, text reading, snapshot/restore.

We use the clipboard only to learn what the user selected (Ctrl+C is the one
mechanism that works in every Windows application).  Whatever was on the
clipboard before is restored afterwards, when that is possible.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import win32clipboard as wc
import win32con

from .winapi import user32

log = logging.getLogger(__name__)

CF_HTML = wc.RegisterClipboardFormat("HTML Format")
CF_RTF = wc.RegisterClipboardFormat("Rich Text Format")
CF_PNG = wc.RegisterClipboardFormat("PNG")

# Formats worth preserving across our capture.
_SIMPLE_FORMATS = (win32con.CF_UNICODETEXT, win32con.CF_TEXT, win32con.CF_DIB, CF_HTML, CF_RTF)
_MAX_BACKUP_BYTES = 32 * 1024 * 1024


def sequence_number() -> int:
    try:
        return int(user32.GetClipboardSequenceNumber())
    except Exception:
        return 0


def _open(retries: int = 10, delay: float = 0.02) -> bool:
    for attempt in range(retries):
        try:
            wc.OpenClipboard(None)
            return True
        except Exception:
            if attempt == retries - 1:
                log.debug("clipboard is locked by another process")
                return False
            time.sleep(delay)
    return False


def _available(formats: list[int], fmt: int) -> bool:
    return fmt in formats


def list_formats() -> list[int]:
    if not _open():
        return []
    try:
        formats: list[int] = []
        current = 0
        while True:
            current = wc.EnumClipboardFormats(current)
            if not current:
                break
            formats.append(int(current))
        return formats
    except Exception:
        log.debug("enumerating clipboard formats failed", exc_info=True)
        return []
    finally:
        wc.CloseClipboard()


def read_text() -> str | None:
    """Return the clipboard's plain text, or None."""
    if not _open():
        return None
    try:
        if not wc.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        value = wc.GetClipboardData(win32con.CF_UNICODETEXT)
        if isinstance(value, bytes):
            value = value.decode("utf-16-le", "replace")
        return str(value)
    except Exception:
        log.debug("reading clipboard text failed", exc_info=True)
        return None
    finally:
        wc.CloseClipboard()


def has_files() -> bool:
    """True when the clipboard currently holds copied files (CF_HDROP)."""
    if not _open():
        return False
    try:
        return bool(wc.IsClipboardFormatAvailable(win32con.CF_HDROP))
    except Exception:
        return False
    finally:
        wc.CloseClipboard()


@dataclass
class Snapshot:
    """Best-effort copy of the clipboard contents."""

    items: list[tuple[int, object]] = field(default_factory=list)
    restorable: bool = True
    was_empty: bool = True
    had_files: bool = False

    @property
    def usable(self) -> bool:
        return self.restorable and (bool(self.items) or self.was_empty)


def snapshot() -> Snapshot | None:
    """Copy the clipboard before we overwrite it.  None when it cannot be read."""
    if not _open():
        return None
    try:
        formats: list[int] = []
        current = 0
        while True:
            current = wc.EnumClipboardFormats(current)
            if not current:
                break
            formats.append(int(current))
    except Exception:
        log.debug("clipboard enumeration failed", exc_info=True)
        wc.CloseClipboard()
        return None

    snap = Snapshot(was_empty=not formats, had_files=_available(formats, win32con.CF_HDROP))
    try:
        if snap.had_files:
            try:
                snap.items.append((win32con.CF_HDROP, wc.GetClipboardData(win32con.CF_HDROP)))
            except Exception:
                log.debug("could not back up file list", exc_info=True)
        for fmt in (win32con.CF_UNICODETEXT, CF_HTML, CF_RTF, win32con.CF_TEXT, win32con.CF_DIB):
            if not _available(formats, fmt):
                continue
            try:
                value = wc.GetClipboardData(fmt)
            except Exception:
                log.debug("could not back up clipboard format %s", fmt, exc_info=True)
                continue
            if isinstance(value, (bytes, bytearray)) and len(value) > _MAX_BACKUP_BYTES:
                log.info("clipboard format %s too large to back up (%s bytes)", fmt, len(value))
                continue
            snap.items.append((fmt, value))
    finally:
        wc.CloseClipboard()

    # If the clipboard held nothing we can put back later, leave it alone rather
    # than emptying it for good.
    if not snap.was_empty and not snap.items:
        snap.restorable = False
        log.info("clipboard contents are not restorable, they will be left untouched")
    return snap


def restore(snap: Snapshot | None) -> bool:
    """Put a snapshot back.  Returns True on success."""
    if snap is None:
        return False
    if not snap.restorable:
        return False
    if not _open():
        log.debug("clipboard busy, cannot restore backup")
        return False
    try:
        wc.EmptyClipboard()
        restored = 0
        for fmt, value in snap.items:
            try:
                wc.SetClipboardData(fmt, value)
                restored += 1
            except Exception:
                log.debug("restoring clipboard format %s failed", fmt, exc_info=True)
        if snap.was_empty and not snap.items:
            return True
        if not restored:
            log.warning("clipboard backup could not be restored")
            return False
        return True
    except Exception:
        log.debug("clipboard restore failed", exc_info=True)
        return False
    finally:
        wc.CloseClipboard()


def wait_for_change(previous: int, timeout: float = 0.9, interval: float = 0.012) -> int | None:
    """Wait until the clipboard sequence number changes; returns the new value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = sequence_number()
        if current != previous:
            return current
        time.sleep(interval)
    return None


def write_text(text: str) -> bool:
    """Put plain text on the clipboard (used by the result window's copy button)."""
    if text is None:
        return False
    if not _open():
        return False
    try:
        wc.EmptyClipboard()
        wc.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
        return True
    except Exception:
        log.debug("writing clipboard text failed", exc_info=True)
        return False
    finally:
        wc.CloseClipboard()


def text_for_translation() -> str | None:
    """Plain text currently on the clipboard (used by the tray menu)."""
    text = read_text()
    if text and text.strip():
        return text
    return None
