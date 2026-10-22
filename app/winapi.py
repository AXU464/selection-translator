"""Low-level Win32 plumbing: DPI/geometry helpers, window probing, synthetic
keyboard input and global low-level input hooks.

Everything here is deliberately small and fast because parts of it run inside
low-level hook callbacks, where Windows removes the hook if the callback is slow.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui
import win32process

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

# ---------------------------------------------------------------- structures


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.SendMessageTimeoutW.restype = LRESULT
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR),
]
# These return 64-bit handles: without an explicit restype ctypes would truncate
# them to 32 bits and SetWindowsHookExW would fail with ERROR_MOD_NOT_FOUND.
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetDeviceCaps.restype = ctypes.c_int
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL

# ---------------------------------------------------------------- constants

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LLMHF_INJECTED = 0x00000001
LLKHF_INJECTED = 0x00000010

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_ESCAPE = 0x1B
VK_C = 0x43

SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002

MONITOR_DEFAULTTONEAREST = 0x00000002
MONITOR_DEFAULTTOPRIMARY = 0x00000001
HTCLIENT = 1
GA_ROOT = 2

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

# Every event we synthesise carries this marker in dwExtraInfo, so our own hooks
# can recognise (and ignore) our injected Ctrl+C while still honouring input that
# other software injects (key remappers, Remote Desktop, accessibility tools).
INJECT_SIGNATURE = 0x7A11CE

MODIFIER_VKS = {"ctrl": VK_CONTROL, "alt": VK_MENU, "shift": VK_SHIFT, "win": VK_LWIN}

_NAMED_VKS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": VK_ESCAPE,
    "escape": VK_ESCAPE, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}

# ---------------------------------------------------------------- DPI / geometry

_dpi_aware = False


def enable_dpi_awareness() -> None:
    """pywebview also does this, but we need it before any geometry maths."""
    global _dpi_aware
    if _dpi_aware:
        return
    try:
        user32.SetProcessDPIAware()
    except Exception:
        log.debug("SetProcessDPIAware failed", exc_info=True)
    _dpi_aware = True


def system_dpi() -> int:
    """DPI of the primary monitor, matching pywebview's logical/physical model."""
    try:
        dpi = int(user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        hdc = user32.GetDC(0)
        try:
            dpi = int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


def system_scale() -> float:
    return system_dpi() / 96.0


def cursor_pos() -> tuple[int, int]:
    x, y = win32api.GetCursorPos()
    return int(x), int(y)


def to_logical(point: tuple[int, int]) -> tuple[int, int]:
    scale = system_scale()
    if scale == 1.0:
        return int(point[0]), int(point[1])
    return int(round(point[0] / scale)), int(round(point[1] / scale))


def to_physical(point: tuple[int, int]) -> tuple[int, int]:
    scale = system_scale()
    if scale == 1.0:
        return int(point[0]), int(point[1])
    return int(round(point[0] * scale)), int(round(point[1] * scale))


def startup_work_area_logical() -> tuple[int, int, int, int]:
    """Choose the initial monitor even while the input desktop is unavailable."""
    # Windows can launch Startup shortcuts before the input desktop becomes
    # accessible during sign-in or a lock-screen transition. GetCursorPos then
    # raises ERROR_ACCESS_DENIED. Cursor placement is optional at startup, so
    # retry briefly and use the primary monitor instead of terminating the app.
    # Keep these waits out of cursor_pos(), which also runs in input hooks.
    for attempt in range(3):
        try:
            point = cursor_pos()
        except win32api.error as error:
            if attempt < 2:
                time.sleep(0.1)
                continue
            log.warning('Startup cursor unavailable after 3 attempts (Win32 error %s); '
                        'using primary monitor', error.winerror)
            return work_area_logical()
        if attempt:
            log.info('Startup cursor access recovered on attempt %s', attempt + 1)
        return work_area_logical(point)


def work_area_logical(point: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """Monitor work area, excluding the taskbar. None selects the primary monitor."""
    location = POINT(int(point[0]), int(point[1])) if point is not None else POINT(0, 0)
    policy = MONITOR_DEFAULTTONEAREST if point is not None else MONITOR_DEFAULTTOPRIMARY
    mon = user32.MonitorFromPoint(location, policy)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        rect = info.rcWork
    else:  # pragma: no cover - fallback for exotic setups
        rect = wintypes.RECT(0, 0, win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))
    left, top = to_logical((rect.left, rect.top))
    right, bottom = to_logical((rect.right, rect.bottom))
    return left, top, right, bottom


# ---------------------------------------------------------------- window probing


def window_from_point(point: tuple[int, int]) -> int:
    try:
        return int(win32gui.WindowFromPoint((int(point[0]), int(point[1]))))
    except Exception:
        return 0


def root_window(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        root = win32gui.GetAncestor(hwnd, GA_ROOT)
        return int(root or hwnd)
    except Exception:
        return hwnd


def window_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    try:
        _thread, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid)
    except Exception:
        return 0


def window_class(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd) or ""
    except Exception:
        return ""


def window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def foreground_window() -> int:
    try:
        return int(win32gui.GetForegroundWindow())
    except Exception:
        return 0


def is_window(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def set_foreground_window(hwnd: int) -> bool:
    """Bring a window back to the foreground (used to undo popup activation)."""
    if not hwnd:
        return False
    try:
        return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
    except Exception:
        log.debug("SetForegroundWindow failed", exc_info=True)
        return False


def show_window(hwnd: int, show: bool) -> bool:
    """Show/hide a window without activating it."""
    if not hwnd:
        return False
    try:
        return bool(user32.ShowWindow(wintypes.HWND(hwnd), 5 if show else 0))  # SW_SHOW / SW_HIDE
    except Exception:
        return False


def send_message_timeout(hwnd: int, msg: int, wparam: int, lparam: int,
                         timeout_ms: int = 60) -> int | None:
    """SendMessageTimeout wrapper that never blocks forever."""
    if not hwnd:
        return None
    out = ULONG_PTR(0)
    ok = user32.SendMessageTimeoutW(
        wintypes.HWND(hwnd), msg, wparam, lparam,
        SMTO_BLOCK | SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(out),
    )
    return out.value if ok else None


def is_client_area(point: tuple[int, int]) -> bool:
    """True when a mouse-down at `point` lands in the client area of a window.

    Filters out drags that started on a title bar, border, scroll bar or menu,
    which are never text selections.  On any doubt we answer True so that real
    selections are not lost.
    """
    hwnd = window_from_point(point)
    if not hwnd:
        return True
    if window_class(hwnd).lower() == "scrollbar":
        return False
    root = root_window(hwnd)
    x, y = int(point[0]) & 0xFFFF, int(point[1]) & 0xFFFF
    lparam = win32api.MAKELONG(x, y)
    hit = send_message_timeout(root, win32con.WM_NCHITTEST, 0, lparam)
    if hit is None:
        return True
    return hit == HTCLIENT


# ---------------------------------------------------------------- input injection

_inject_lock = threading.RLock()
_inject_counter = {"n": 0}


def is_injecting() -> bool:
    return _inject_counter["n"] > 0


@contextlib.contextmanager
def injection_guard():
    with _inject_lock:
        _inject_counter["n"] += 1
    try:
        yield
    finally:
        with _inject_lock:
            _inject_counter["n"] -= 1


def async_key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _key_input(vk: int, up: bool) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, 0)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(
        wVk=vk,
        wScan=scan,
        dwFlags=KEYEVENTF_KEYUP if up else 0,
        time=0,
        dwExtraInfo=INJECT_SIGNATURE,
    )
    return inp


def _send_inputs(inputs: list[INPUT]) -> bool:
    if not inputs:
        return False
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %s of %s events (err=%s)", sent, len(inputs), ctypes.get_last_error())
        return False
    return True


def send_ctrl_c() -> bool:
    """Synthesise Ctrl+C, deliberately clearing any held modifiers first so a
    Shift-drag selection does not turn into Shift+Ctrl+C."""
    events: list[INPUT] = []
    for vk in (VK_SHIFT, VK_MENU, VK_RWIN):
        if async_key_down(vk):
            events.append(_key_input(vk, up=True))
    events.append(_key_input(VK_CONTROL, up=True))
    events.append(_key_input(VK_CONTROL, up=False))
    events.append(_key_input(VK_C, up=False))
    events.append(_key_input(VK_C, up=True))
    events.append(_key_input(VK_CONTROL, up=True))
    with injection_guard():
        ok = _send_inputs(events)
    return ok


def parse_hotkey(spec: str) -> tuple[int, frozenset[str]] | None:
    """'ctrl+alt+c' -> (0x43, {'ctrl','alt'})."""
    if not spec or not isinstance(spec, str):
        return None
    mods: set[str] = set()
    vk: int | None = None
    for raw in spec.lower().replace(" ", "").split("+"):
        if not raw:
            continue
        if raw in MODIFIER_VKS and raw != "win":
            mods.add(raw)
        elif raw == "win":
            mods.add("win")
        elif raw in _NAMED_VKS:
            vk = _NAMED_VKS[raw]
        elif len(raw) == 1 and (raw.isalnum() or raw in "`-=[]\\;',./"):
            vk = ord(raw.upper())
        else:
            return None
    if vk is None:
        return None
    return vk, frozenset(mods)


# ---------------------------------------------------------------- input hooks


class InputHooks:
    """Owns a thread with WH_MOUSE_LL / WH_KEYBOARD_LL hooks and a message loop.

    `callback(kind, data)` is invoked from the hook thread and must return fast
    (it is expected to only push onto a queue).  `hotkeys` maps a name to a spec
    such as 'ctrl+alt+e'; matching presses are swallowed so they do not leak into
    the focused application.
    """

    def __init__(self, callback, hotkeys: dict[str, str] | None = None):
        self._callback = callback
        self._hotkeys: dict[str, tuple[int, frozenset[str]]] = {}
        for name, spec in (hotkeys or {}).items():
            parsed = parse_hotkey(spec)
            if parsed:
                self._hotkeys[name] = parsed
            else:
                log.warning("ignoring invalid hotkey %r = %r", name, spec)

        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._mouse_proc = None  # keep references alive for the lifetime of the hook
        self._key_proc = None
        self._mouse_hook = None
        self._key_hook = None
        self._click_time = 0.0
        self._click_point = (0, 0)
        self._button_down = False
        self._down_point = (0, 0)
        self._max_drag = 0.0
        self._is_double = False
        self._down_keys: set[int] = set()
        self._paused = threading.Event()
        self._slow_calls = 0
        self._mouse_events = 0
        self._key_events = 0
        self._dbl_click_s = max(0.25, user32.GetDoubleClickTime() / 1000.0)
        self._dbl_click_px = (
            int(win32api.GetSystemMetrics(36)),  # SM_CXDOUBLECLK
            int(win32api.GetSystemMetrics(37)),  # SM_CYDOUBLECLK
        )

    # -- lifecycle ------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="input-hooks", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            log.error("input hooks did not start in time")
            return False
        ok = bool(self._mouse_hook and self._key_hook)
        if not ok:
            log.error("input hooks failed to install (mouse=%s key=%s)", self._mouse_hook, self._key_hook)
        return ok

    def stop(self) -> None:
        if self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                log.debug("could not post WM_QUIT to hook thread", exc_info=True)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def pause(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def healthy(self) -> bool:
        """Thread alive, hooks installed, no dangerously slow callbacks."""
        return bool(self._thread and self._thread.is_alive()
                    and self._mouse_hook and self._key_hook
                    and self._slow_calls < 5)

    def stats(self) -> dict:
        return {"alive": bool(self._thread and self._thread.is_alive()),
                "hooks": bool(self._mouse_hook and self._key_hook),
                "slow_calls": self._slow_calls,
                "mouse_events": self._mouse_events,
                "key_events": self._key_events,
                "paused": self._paused.is_set()}

    def restart(self) -> bool:
        """Re-install the hooks (recovery when Windows dropped them)."""
        log.warning("restarting input hooks")
        self.stop()
        self._thread = threading.Thread(target=self._run, name="input-hooks", daemon=True)
        self._ready.clear()
        self._thread.start()
        return self._ready.wait(5.0) and bool(self._mouse_hook and self._key_hook)

    # -- hook thread ----------------------------------------------------
    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        try:
            self._mouse_proc = HOOKPROC(self._mouse_cb)
            self._key_proc = HOOKPROC(self._key_cb)
            self._mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
            if not self._mouse_hook:
                log.error("SetWindowsHookExW(WH_MOUSE_LL) failed: error %s",
                          ctypes.get_last_error())
            self._key_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_proc, hmod, 0)
            if not self._key_hook:
                log.error("SetWindowsHookExW(WH_KEYBOARD_LL) failed: error %s",
                          ctypes.get_last_error())
        except Exception:
            log.exception("installing hooks failed")
        finally:
            self._ready.set()

        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        for hook in (self._mouse_hook, self._key_hook):
            if hook:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
        self._mouse_hook = self._key_hook = None
        log.info("input hooks removed")

    def _emit(self, kind: str, data: dict | None = None) -> None:
        """Run the callback and keep an eye on how long it takes.

        Windows removes a low-level hook if its callback stays busy for too long
        (LowLevelHooksTimeout, 300 ms by default and it does not tell us), so a
        slow callback is logged loudly instead of failing silently.
        """
        started = time.perf_counter()
        try:
            self._callback(kind, data or {})
        except Exception:
            log.exception("hook callback failed")
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._slow_calls += 1 if elapsed_ms > 30 else 0
        if elapsed_ms > 60:
            log.warning("hook callback %s took %.0f ms (slow callbacks risk losing the hook)",
                        kind, elapsed_ms)

    # -- callbacks ------------------------------------------------------
    def _mouse_cb(self, ncode, wparam, lparam):
        try:
            if ncode == HC_ACTION and not self._paused.is_set():
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                point = (int(info.pt.x), int(info.pt.y))
                msg = int(wparam)
                # Note: injected mouse input is accepted on purpose.  We never
                # synthesise mouse events ourselves, and Windows flags input from
                # Remote Desktop / pen / touch promotion as injected — filtering it
                # would silently break selection there.
                injected = bool(info.flags & LLMHF_INJECTED)

                if msg == WM_MOUSEMOVE:
                    if self._button_down:
                        dx = point[0] - self._down_point[0]
                        dy = point[1] - self._down_point[1]
                        dist = (dx * dx + dy * dy) ** 0.5
                        if dist > self._max_drag:
                            self._max_drag = dist
                else:
                    self._mouse_events += 1
                    if msg == WM_LBUTTONDOWN:
                        now = time.time()
                        same_spot = (
                            abs(point[0] - self._click_point[0]) <= self._dbl_click_px[0]
                            and abs(point[1] - self._click_point[1]) <= self._dbl_click_px[1]
                        )
                        self._is_double = bool(
                            same_spot and (now - self._click_time) <= self._dbl_click_s
                        )
                        self._click_time = now
                        self._click_point = point
                        self._button_down = True
                        self._down_point = point
                        self._max_drag = 0.0
                        self._emit("mouse_down", {"pt": point, "double": self._is_double,
                                                  "button": "left"})
                    elif msg == WM_LBUTTONUP:
                        drag = self._max_drag
                        was_down = self._button_down
                        self._button_down = False
                        self._max_drag = 0.0
                        if was_down:
                            self._emit("mouse_up", {"pt": point, "start": self._down_point,
                                                    "drag": drag, "double": self._is_double,
                                                    "button": "left"})
                    elif msg in (WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                        self._emit("mouse_down", {"pt": point, "double": False,
                                                  "button": "right" if msg == WM_RBUTTONDOWN else "middle"})
                    elif msg in (WM_RBUTTONUP, WM_MBUTTONUP):
                        self._button_down = False
                        self._emit("other_click", {"pt": point})
                    elif msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                        self._emit("wheel", {"pt": point})
        except Exception:
            log.exception("mouse hook failed")
        return user32.CallNextHookEx(self._mouse_hook, ncode, wparam, lparam)

    def _key_cb(self, ncode, wparam, lparam):
        try:
            if ncode == HC_ACTION and not self._paused.is_set():
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = int(info.vkCode)
                msg = int(wparam)
                foreign_injected = bool(info.flags & LLKHF_INJECTED) \
                    and int(info.dwExtraInfo) != INJECT_SIGNATURE

                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    fresh = vk not in self._down_keys
                    self._down_keys.add(vk)
                    self._key_events += 1
                    if fresh and not is_injecting():
                        name = self._match_hotkey(vk)
                        if name:
                            self._emit("hotkey", {"name": name, "injected": foreign_injected})
                            return 1  # swallow the hotkey
                        if vk == VK_ESCAPE:
                            self._emit("escape", {"injected": foreign_injected})
                elif msg in (WM_KEYUP, WM_SYSKEYUP):
                    self._down_keys.discard(vk)
        except Exception:
            log.exception("keyboard hook failed")
        return user32.CallNextHookEx(self._key_hook, ncode, wparam, lparam)

    def _match_hotkey(self, vk: int) -> str | None:
        if not self._hotkeys:
            return None
        mods = set()
        for name, mod_vk in MODIFIER_VKS.items():
            if name == "win":
                if async_key_down(VK_LWIN) or async_key_down(VK_RWIN):
                    mods.add("win")
            elif async_key_down(mod_vk):
                mods.add(name)
        for name, (hotkey_vk, required) in self._hotkeys.items():
            if vk == hotkey_vk and required <= mods:
                return name
        return None
