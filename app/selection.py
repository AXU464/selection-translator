"""Turn mouse gestures (and hotkeys) into selected text.

Mouse gestures are detected by the global hooks in `winapi.InputHooks`; the
actual text is obtained the only way that works everywhere on Windows — a
synthetic Ctrl+C followed by a clipboard read.  Both the hooks and the capture
work run off the main thread, and the clipboard is put back afterwards.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from . import clipboard, events, winapi

log = logging.getLogger(__name__)


@dataclass
class CaptureRequest:
    mode: str = "popup"                 # 'popup' | 'translate'
    direction: str | None = None        # for mode == 'translate'
    reason: str = "drag"
    point: tuple[int, int] | None = None    # physical mouse-up point
    start: tuple[int, int] | None = None    # physical mouse-down point
    force: bool = False                 # hotkeys bypass the gesture filters
    expected_hwnd: int = 0              # only re-read this source window
    expected_text: str | None = None    # restore only an unchanged selection
    restore_job_id: int | None = None   # reject late restores after another job


@dataclass
class CaptureResult:
    request: CaptureRequest
    text: str = ""
    truncated: bool = False
    info: dict = field(default_factory=dict)


class SelectionCapture:
    """Hook thread + capture worker.

    `emit(kind, payload)` is called from these threads; it must be non-blocking
    (the app pushes the payload onto its event queue).
    """

    def __init__(self, cfg, emit, popup_state=None, own_point=None):
        self.cfg = cfg
        self._emit = emit
        self._popup_state = popup_state      # () -> (visible: bool, bounds: tuple | None)
        self._own_point = own_point          # (physical point) -> bool
        self._queue: queue.Queue[CaptureRequest | None] = queue.Queue(maxsize=8)
        self._worker: threading.Thread | None = None
        self._hooks: winapi.InputHooks | None = None
        self._last_enqueue = 0.0
        self._enabled = threading.Event()
        self._enabled.set()
        self._pending_notice = 0.0
        self.requests = 0                    # diagnostics
        self.successes = 0
        self._last_activity = time.monotonic()

    # -- lifecycle ------------------------------------------------------
    def hotkey_specs(self) -> dict[str, str]:
        if not bool(self.cfg.get("behavior.hotkey_enabled", True)):
            return {}
        return {
            "translate_zh": str(self.cfg.get("behavior.hotkey_chinese", "ctrl+alt+c")),
            "translate_en": str(self.cfg.get("behavior.hotkey_english", "ctrl+alt+e")),
        }

    def start(self) -> bool:
        self._worker = threading.Thread(target=self._worker_loop, name="capture", daemon=True)
        self._worker.start()
        self._hooks = winapi.InputHooks(self._on_hook, self.hotkey_specs())
        ok = self._hooks.start()
        self._hooks.pause(not self.enabled)
        return ok

    def stop(self) -> None:
        if self._hooks:
            self._hooks.stop()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def reapply_hotkeys(self) -> None:
        """Pick up hotkey changes from the config (restarts the hook thread)."""
        if not self._hooks:
            return
        self._hooks.stop()
        self._hooks = winapi.InputHooks(self._on_hook, self.hotkey_specs())
        self._hooks.start()
        self._hooks.pause(not self.enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()
        if self._hooks:
            self._hooks.pause(not enabled)
        log.info("selection detection %s", "enabled" if enabled else "paused")

    # -- trigger logic (hook thread) -------------------------------------
    def _on_hook(self, kind: str, data: dict) -> None:
        try:
            if kind == "mouse_up":
                if data.get("button") != "left":
                    return
                point = tuple(data.get("pt") or (0, 0))
                start = tuple(data.get("start") or point)
                drag = float(data.get("drag") or 0.0)
                threshold = float(self.cfg.get("behavior.drag_threshold_px", 5))
                is_double = bool(data.get("double"))
                if drag >= threshold:
                    self._enqueue(CaptureRequest("popup", reason="drag", point=point, start=start))
                elif is_double and bool(self.cfg.get("behavior.double_click_select", True)):
                    self._enqueue(CaptureRequest("popup", reason="double", point=point, start=start))
            elif kind == "mouse_down":
                self._maybe_dismiss(tuple(data.get("pt") or (0, 0)), data.get("button"))
            elif kind == "wheel":
                self._dismiss("wheel")
            elif kind == "other_click":
                self._dismiss("click")
            elif kind == "escape":
                self._dismiss("escape")
            elif kind == "hotkey":
                name = str(data.get("name") or "")
                direction = {"translate_zh": "zh", "translate_en": "en"}.get(name)
                if direction:
                    point = winapi.cursor_pos()
                    self._enqueue(CaptureRequest("translate", direction=direction,
                                                 reason="hotkey", point=point, start=point,
                                                 force=True))
        except Exception:
            log.exception("hook event handling failed")

    def _popup_geometry(self):
        if not self._popup_state:
            return False, None
        try:
            return self._popup_state()
        except Exception:
            log.debug("popup state lookup failed", exc_info=True)
            return False, None

    def _maybe_dismiss(self, point: tuple[int, int], button) -> None:
        visible, bounds = self._popup_geometry()
        if not visible:
            return
        if self._in_bounds(point, bounds):
            return
        self._dismiss("click-outside")

    @staticmethod
    def _in_bounds(point: tuple[int, int], bounds) -> bool:
        if not bounds:
            return False
        logical = winapi.to_logical(point)
        left, top, right, bottom = bounds
        return left <= logical[0] <= right and top <= logical[1] <= bottom

    def _dismiss(self, reason: str) -> None:
        visible, _bounds = self._popup_geometry()
        if visible:
            self._emit(events.DISMISS, {"reason": reason})

    # -- request queue ---------------------------------------------------
    def request_now(self, mode: str = "translate", direction: str | None = None,
                    reason: str = "manual") -> None:
        """Programmatic capture (hotkey, tray menu)."""
        point = winapi.cursor_pos()
        self._enqueue(CaptureRequest(mode, direction=direction, reason=reason,
                                     point=point, start=point, force=True))

    def restore_popup(self, selection: CaptureResult, job_id: int) -> None:
        """Re-check a retained selection after closing its translation card.

        Do not activate another app or trust cached text: the worker only copies
        from the original foreground window and requires the same selection.
        """
        if not self.enabled:
            return
        hwnd = int(selection.info.get("source_hwnd") or 0)
        if not hwnd or not winapi.is_window(hwnd) or selection.request.point is None:
            return
        self._enqueue(CaptureRequest(
            "popup", reason="result-closed", point=selection.request.point,
            start=selection.request.start, force=True, expected_hwnd=hwnd,
            expected_text=selection.text, restore_job_id=job_id,
        ))

    def _enqueue(self, request: CaptureRequest) -> None:
        if not self.enabled and not request.force:
            return
        now = time.monotonic()
        if request.reason != "result-closed" and now - self._last_enqueue < 0.2:
            log.debug("capture request dropped (debounce)")
            return
        self._last_enqueue = now
        self.requests += 1
        log.debug("capture requested (mode=%s reason=%s point=%s)",
                  request.mode, request.reason, request.point)
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            log.warning("capture queue full, dropping request")

    # -- diagnostics -----------------------------------------------------
    def stats(self) -> dict:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "queued": self._queue.qsize(),
            "worker_alive": bool(self._worker and self._worker.is_alive()),
            "idle_s": round(time.monotonic() - self._last_activity, 1),
            "hooks": self._hooks.stats() if self._hooks else {},
        }

    def watchdog(self) -> None:
        """Called periodically: re-install the hooks if Windows dropped them."""
        if not self._hooks or not self.enabled:
            return
        if not self._hooks.alive:
            log.error("input hook thread died; restarting it")
            self._hooks.restart()
        elif self._hooks.stats().get("slow_calls", 0) >= 5:
            log.error("too many slow hook callbacks, re-installing hooks")
            self._hooks.restart()
            self._hooks.pause(False)

    # -- capture worker --------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                self._last_activity = time.monotonic()
                self._handle(request)
            except Exception:
                log.exception("selection capture failed")
            finally:
                self._last_activity = time.monotonic()
                self._queue.task_done()

    def _handle(self, request: CaptureRequest) -> None:
        delay = max(0, int(self.cfg.get("behavior.capture_delay_ms", 70))) / 1000.0
        if delay:
            time.sleep(delay)

        if request.expected_hwnd and not self.enabled:
            return

        if not request.force and request.start:
            if self._is_own_window(request.start):
                log.debug("ignoring gesture inside our own window")
                return
            if not winapi.is_client_area(request.start):
                log.debug("ignoring gesture outside a client area")
                return

        text, info = self._grab_text(expected_hwnd=request.expected_hwnd)
        if request.expected_text is not None and text != request.expected_text:
            log.debug("not restoring popup: the original selection changed or disappeared")
            return
        if not text:
            reason = info.get("reason", "unknown")
            if request.mode == "translate" and request.force and reason == "no-selection":
                self._emit(events.CAPTURE_FAILED, {"reason": reason})
            log.debug("no text captured (%s)", reason)
            return

        result = CaptureResult(request=request, text=text,
                               truncated=bool(info.get("truncated")), info=info)
        self.successes += 1
        self._emit(events.CAPTURED, {"result": result})

    def _is_own_window(self, point: tuple[int, int]) -> bool:
        if not self._own_point:
            return False
        try:
            return bool(self._own_point(point))
        except Exception:
            log.debug("own window check failed", exc_info=True)
        return False

    def _grab_text(self, expected_hwnd: int = 0) -> tuple[str, dict]:
        cfg = self.cfg
        restore = bool(cfg.get("behavior.restore_clipboard", True))
        before = clipboard.sequence_number()
        snap = clipboard.snapshot() if restore else None

        foreground = winapi.foreground_window()
        if expected_hwnd and foreground != expected_hwnd:
            return "", {"reason": "source-changed"}
        log.debug("capture: injecting Ctrl+C into %r (%s)",
                  winapi.window_title(foreground)[:40], winapi.window_class(foreground))

        if not winapi.send_ctrl_c():
            return "", {"reason": "send-failed"}

        changed = clipboard.wait_for_change(before, timeout=1.0)
        text = ""
        info: dict = {"source_hwnd": foreground}
        if changed is not None:
            if clipboard.has_files():
                info["reason"] = "files"
            else:
                raw = clipboard.read_text()
                if raw:
                    text = raw.strip()
                    info["reason"] = "ok"

        if snap is not None and changed is not None:
            clipboard.restore(snap)

        if winapi.foreground_window() != foreground:
            return "", {"reason": "source-changed"}

        if not text:
            info.setdefault("reason", "no-selection")
            return "", info

        min_chars = int(cfg.get("behavior.min_chars", 2))
        if len(text) < max(1, min_chars):
            info["reason"] = "too-short"
            return "", info

        max_chars = int(cfg.get("behavior.max_chars", 12000))
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars]
            info["truncated"] = True
        info["chars"] = len(text)
        return text, info
