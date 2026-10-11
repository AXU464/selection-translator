"""WebView2 selection bar, reading card and preferences window."""

from __future__ import annotations

import json
import ctypes
import logging
import pathlib
import threading
import uuid
from datetime import datetime, timezone
from ctypes import wintypes

import webview

from . import config as config_mod
from . import events, winapi
from .document import prepare_document
from . import preferences
from .translator import ConnectionProbe, Translator

log = logging.getLogger(__name__)

POPUP_W, POPUP_H = 380, 50
# The result card is a quarter of the work area, on both axes.
RESULT_SIZE_DIVISOR = 4
RESULT_MIN = (300, 160)
RESULT_FIT_MIN_H = 190
RESIZE_HIT_TESTS = {"w": 10, "e": 11, "n": 12, "nw": 13,
                    "ne": 14, "s": 15, "sw": 16, "se": 17, "move": 2}

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0
GA_ROOT = 2


class _PopupApi:
    """Exposed to popup.html as window.pywebview.api."""

    def __init__(self, emit):
        self._emit = emit

    def action(self, direction):
        self._emit(events.POPUP_ACTION, {"direction": str(direction or "zh")})
        return True

    def dismiss(self):
        self._emit(events.DISMISS, {"reason": "popup"})
        return True


class _ResultApi:
    """Exposed to result.html as window.pywebview.api."""

    def __init__(self, ui: "UiController"):
        self._ui = ui

    def action(self, action):
        self._ui.emit(events.RESULT_ACTION, {"action": str(action or "")})
        return True

    def ready(self):
        self._ui.emit(events.UI_READY, {"which": "result"})
        return True

    def begin_drag(self, edge):
        return self._ui.begin_result_drag(str(edge))

    def window_control(self, action):
        return self._ui.control_result_window(str(action))

    def fit(self, value):
        """The page reports how tall its content is (CSS px == logical px)."""
        try:
            needed = int(str(value).strip())
        except (TypeError, ValueError):
            return False
        self._ui.fit_result_height(needed)
        return True


class _SettingsApi:
    def __init__(self, ui):
        self._ui = ui

    def read(self):
        return preferences.snapshot(self._ui.cfg)

    def test_connection(self, values):
        if not self._ui._settings_open:
            return {"ok": False, "error": "请先打开设置。"}
        return Translator.connection_probe(self._ui.cfg, values, self._ui.connection_probe)

    def history_list(self, keyword='', tag='', page=0):
        store = self._ui.history_store
        if store is None:
            return {'items': [], 'total': 0, 'tags': [], 'error': ''}
        rows = store.query(keyword, tag)
        start = max(0, int(page)) * 30
        return {'items': [{'id': row['id'], 'original': row['original'][:100],
                          'translation': row['translation'][:100], 'direction': row['direction'],
                          'ts': row['ts'], 'tags': row.get('tags', [])} for row in rows[start:start+30]],
                'total': len(rows), 'tags': sorted({tag for row in store.all() for tag in row.get('tags', [])}),
                'error': store.error}

    def history_detail(self, entry_id):
        store = self._ui.history_store
        return next((row for row in store.all() if row['id'] == int(entry_id)), None) if store else None

    def history_tags(self, entry_id, text):
        try:
            ok = self._ui.history_store.set_tags(entry_id, text)
        except (ValueError, TypeError, AttributeError) as exc:
            return {'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else '历史暂不可用。'}
        self._ui.emit(events.HISTORY_CHANGED)
        return {'ok': ok, 'error': '' if ok else self._ui.history_store.error}

    def history_open(self, entry_id):
        self._ui.emit(events.RESULT_ACTION, {'action': 'history:' + str(int(entry_id))})

    def history_clear(self):
        if not self._ui._settings_open or self._ui.history_store is None:
            return {'ok': False, 'error': '请先打开历史页面。'}
        if not self._ui.settings.create_confirmation_dialog('清空翻译历史',
                '清空全部历史（含标签及损坏行）？此操作无法撤销。\n独立的翻译缓存和已导出文件会保留。'):
            return {'ok': False, 'cancelled': True}
        ok = self._ui.history_store.clear()
        if ok:
            self._ui.emit(events.HISTORY_CHANGED)
        return {'ok': ok, 'error': '' if ok else self._ui.history_store.error}

    def history_export(self, kind, keyword='', tag=''):
        store = self._ui.history_store
        if store is None:
            return {'ok': False, 'error': '历史暂不可用。'}
        try:
            content = store.export(kind, keyword, tag)
            directory = store.path.parent / 'exports'
            directory.mkdir(parents=True, exist_ok=True)
            name = 'history-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
            path = directory / (name + ('.md' if kind == 'markdown' else '.txt'))
            with path.open('x', encoding='utf-8', newline='') as output:
                output.write(content)
            return {'ok': True, 'path': str(path)}
        except (OSError, ValueError):
            return {'ok': False, 'error': '导出失败，请检查本地数据目录的写入权限。'}

    def history_export_folder(self):
        import os
        store = self._ui.history_store
        if store and (store.path.parent / 'exports').is_dir():
            os.startfile(str(store.path.parent / 'exports'))

    def preview(self, values):
        with self._ui._settings_lock:
            if not self._ui._settings_open:
                return False
            if not isinstance(values, dict) or set(values) - {"ui.theme", "ui.font_size"}:
                return False
            try:
                values = preferences.validate(values, self._ui.cfg)
            except ValueError:
                return False
            self._ui.apply_appearance(values)
            return True

    def save(self, values):
        with self._ui._settings_lock:
            if not self._ui._settings_open:
                return {"ok": False, "error": "请重新打开设置。"}
            try:
                cleaned = preferences.validate(values, self._ui.cfg)
                self._ui.cfg.update_values(cleaned)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            except OSError:
                return {"ok": False, "error": "保存失败，请检查配置文件的写入权限后重试。"}
            self._ui.apply_preferences(list(cleaned))
            self._ui.emit(events.SETTINGS_CHANGED, {"keys": list(cleaned)})
            return {"ok": True, **preferences.snapshot(self._ui.cfg)}

    def close(self):
        return self._ui.close_settings()


class UiController:
    def __init__(self, cfg, emit, web_dir: pathlib.Path, storage_dir: pathlib.Path,
                 icon_path: pathlib.Path | None = None):
        self.cfg = cfg
        self._emit = emit
        self.web_dir = web_dir
        self.storage_dir = storage_dir
        self.icon_path = icon_path
        self.server = None
        self.popup: webview.Window | None = None
        self.result: webview.Window | None = None
        self.settings: webview.Window | None = None
        self._settings_open = False
        self._settings_lock = threading.RLock()
        self.connection_probe = ConnectionProbe()
        self.history_store = None
        self._popup_visible = False
        self._popup_bounds: tuple[int, int, int, int] | None = None
        self._popup_hwnd = 0
        self._result_hwnd = 0
        self._result_open = False
        self._result_baseline_h = 0     # height to use for long content / geometry
        self._fit_target: int | None = None
        self._manual_size = False
        self._geometry_lock = threading.Lock()
        self._assume_closing = False   # True while we close windows on purpose
        self._lock = threading.Lock()

    def emit(self, kind: str, payload: dict | None = None) -> None:
        """Forward an event to the application queue (used by the JS bridge)."""
        self._emit(kind, payload or {})

    # ------------------------------------------------------------ lifecycle
    def create_windows(self, base_url: str) -> None:
        state = config_mod.load_state()
        left, top, right, bottom = winapi.work_area_logical(winapi.cursor_pos())
        work_w, work_h = max(320, right - left), max(240, bottom - top)
        default_size = (work_w // RESULT_SIZE_DIVISOR, work_h // RESULT_SIZE_DIVISOR)

        saved = state.get("result_size")
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            width, height = int(saved[0]), int(saved[1])
        else:
            width, height = default_size
        width = max(RESULT_MIN[0], min(width, work_w))
        height = max(RESULT_MIN[1], min(height, work_h))
        log.info("result card size %sx%s (work area %sx%s)", width, height, work_w, work_h)

        pos = state.get("result_pos")
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            pos = (int(left + max(0, (work_w - width) / 2)),
                   int(top + max(0, (work_h - height) / 2)))

        # Parked far off-screen; show_popup() moves it into view before showing.
        self.popup = webview.create_window(
            "划词翻译",
            url=f"{base_url}/popup.html",
            width=POPUP_W,
            height=POPUP_H,
            x=-POPUP_W - 200,
            y=-POPUP_H - 200,
            frameless=True,
            on_top=True,
            focus=False,
            hidden=True,
            resizable=False,
            transparent=True,
            shadow=False,
            easy_drag=False,
            text_select=False,
            # pywebview defaults min_size to (200, 100), which would stretch the
            # 50 px bar; pin it to the real size.
            min_size=(POPUP_W, POPUP_H),
            js_api=_PopupApi(self._emit),
        )
        self.result = webview.create_window(
            "译文",
            url=f"{base_url}/result.html",
            width=width,
            height=height,
            x=int(pos[0]),
            y=int(pos[1]),
            hidden=True,
            resizable=True,
            frameless=True,
            easy_drag=False,
            shadow=True,
            background_color="#fafbf8",
            min_size=RESULT_MIN,
            on_top=bool(self.cfg.get("ui.result_on_top", False)),
            text_select=True,
            zoomable=True,
            js_api=_ResultApi(self),
        )
        self._result_baseline_h = int(height)
        self.result.events.resized += self._on_result_resized
        # Both windows must be able to *cancel* their close: pywebview exits the
        # whole application as soon as one of them is actually closed.
        self.result.events.closing += self._on_result_closing
        self.popup.events.closing += self._on_popup_closing
        self.settings = webview.create_window(
            "设置 · 划词翻译", url=f"{base_url}/settings.html",
            width=min(820, work_w), height=min(730, work_h), min_size=(620, 480),
            hidden=True, resizable=True, background_color="#f4f3ed",
            text_select=True, js_api=_SettingsApi(self),
        )
        self.settings.events.closing += self._on_settings_closing
        for window in (self.popup, self.result, self.settings):
            window.events.loaded += lambda: self.apply_appearance()

    def after_start(self) -> None:
        """Runs once the GUI loop is alive; native handles now exist."""
        self._apply_popup_styles()
        self._apply_result_styles()
        self.push_font_size()
        self.push_hotkeys()
        self._push_result("enableAutoFit", [bool(self.cfg.get("ui.fit_to_content", False))])
        self.apply_appearance()

    def destroy_windows(self) -> None:
        self._assume_closing = True
        for window in (self.popup, self.result, self.settings):
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    log.debug("destroying window failed", exc_info=True)

    def show_settings(self, page='appearance') -> None:
        with self._settings_lock:
            if self.settings is None:
                return
            if not self._settings_open:
                self._settings_open = True
                self.apply_appearance()
                self._eval(self.settings, "window.Settings && window.Settings.load(" + json.dumps(page) + ")")
            elif page != 'appearance':
                self._eval(self.settings, "window.Settings && window.Settings.openPage(" + json.dumps(page) + ")")
            self.hide_popup()
            self.settings.show()
            self.settings.restore()
            self._raise_window(self._safe_hwnd(self.settings))

    def refresh_history(self):
        if self._settings_open:
            self._eval(self.settings, 'window.History && window.History.refresh()')

    def close_settings(self) -> bool:
        with self._settings_lock:
            self._settings_open = False
            self.apply_appearance()  # cancel any unsaved appearance preview
            self._eval(self.settings, "window.Settings && window.Settings.clearSecrets()")
            if self.settings is not None:
                self.settings.hide()
            return True

    def _on_settings_closing(self) -> bool:
        if self._assume_closing:
            return True
        # WinForms raises Closing on its UI thread. Evaluating JavaScript there
        # blocks WebView2's completion callback; restore previews off-thread.
        threading.Thread(target=self.close_settings, name="close-settings", daemon=True).start()
        return False

    def apply_appearance(self, preview: dict | None = None) -> None:
        values = preview or {}
        theme = values.get("ui.theme", self.cfg.get("ui.theme", "system"))
        if not isinstance(theme, str) or theme not in preferences.THEMES:
            theme = "system"
        for window in (self.popup, self.result, self.settings):
            self._eval(window, "window.applyTheme && window.applyTheme(" + json.dumps(theme) + ")")
        self._push_result("fontSize", [values.get("ui.font_size", self.cfg.get("ui.font_size", 14))])

    def apply_preferences(self, keys=()) -> None:
        self.apply_appearance()
        self.push_hotkeys()
        if self.result is not None:
            self.result.on_top = bool(self.cfg.get("ui.result_on_top", False))
            self._apply_result_styles()
        if "ui.fit_to_content" in keys:
            self._manual_size = False
        self._push_result("enableAutoFit", [bool(self.cfg.get("ui.fit_to_content", False))])

    def _on_popup_closing(self) -> bool:
        if self._assume_closing:
            return True
        log.debug("popup close cancelled")
        return False

    def _on_result_closing(self) -> bool:
        """Closing the result window hides it; the app keeps running in the tray."""
        if self._assume_closing:
            return True
        self._save_geometry()
        was_open = self._result_open
        try:
            self.result.hide()
        except Exception:
            log.debug("hiding result window failed", exc_info=True)
            return False
        self._result_open = False
        if was_open:
            self.emit(events.RESULT_CLOSED)
        return False  # cancel the close

    # ------------------------------------------------------------ helpers
    def _safe_hwnd(self, window) -> int:
        try:
            return int(window.native.Handle.ToInt32())
        except Exception:
            return 0

    def _refresh_handles(self) -> None:
        """Re-read the native handles.

        Setting Form.ShowInTaskbar makes WinForms recreate the window, so cached
        handles can go stale — always refresh before acting on them.
        """
        popup = self._safe_hwnd(self.popup)
        if popup and popup != self._popup_hwnd:
            if self._popup_hwnd:
                log.debug("popup handle changed %s -> %s", self._popup_hwnd, popup)
            self._popup_hwnd = popup
        result = self._safe_hwnd(self.result)
        if result and result != self._result_hwnd:
            if self._result_hwnd:
                log.debug("result handle changed %s -> %s", self._result_hwnd, result)
            self._result_hwnd = result

    def _popup_handle(self) -> int:
        popup = self._safe_hwnd(self.popup)
        if popup and popup != self._popup_hwnd:
            self._popup_hwnd = popup
        return self._popup_hwnd

    def _apply_popup_styles(self) -> None:
        # WinForms puts a taskbar button on every top-level form by default
        # (ShowInTaskbar = true + WS_EX_APPWINDOW) and that flag beats
        # WS_EX_TOOLWINDOW.  Ask WinForms for a taskbar-less form first (it gives
        # the form a hidden owner window), then enforce our own flags.
        self._set_winforms_taskbar(self.popup, False)
        self._refresh_handles()
        hwnd = self._popup_handle()
        if not hwnd:
            log.warning("popup handle unavailable; it may show up in the taskbar")
            return
        self._enforce_no_taskbar(hwnd, no_activate=True)
        # It is parked off-screen, but make sure it starts hidden regardless.
        winapi.show_window(hwnd, False)
        self._popup_visible = False

    def _apply_result_styles(self) -> None:
        """Keep native shadows/rounded corners around the custom card chrome."""
        show_taskbar = bool(self.cfg.get("ui.result_in_taskbar", False))
        self._set_winforms_taskbar(self.result, show_taskbar)
        self._refresh_handles()
        if not bool(self.cfg.get("ui.result_in_taskbar", False)):
            self._enforce_no_taskbar(self._result_hwnd)
        elif self._result_hwnd:
            style = winapi.user32.GetWindowLongW(self._result_hwnd, GWL_EXSTYLE)
            winapi.user32.SetWindowLongW(self._result_hwnd, GWL_EXSTYLE,
                                        (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
        try:
            # Windows 11 rounds the native surface; older Windows ignores this.
            preference = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(self._result_hwnd), 33, ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
            log.debug("native card corners unavailable", exc_info=True)

    @staticmethod
    def _enforce_no_taskbar(hwnd: int, no_activate: bool = False) -> None:
        """Keep a window out of the taskbar: WS_EX_TOOLWINDOW on, WS_EX_APPWINDOW off."""
        if not hwnd:
            return
        try:
            style = winapi.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            wanted = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            if no_activate:
                wanted |= WS_EX_NOACTIVATE
            if wanted != style:
                winapi.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, wanted)
                winapi.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            log.debug("could not enforce taskbar style", exc_info=True)

    @staticmethod
    def _set_winforms_taskbar(window, show: bool) -> None:
        """Set Form.ShowInTaskbar on the GUI thread (pythonnet), so WinForms stops
        re-applying the taskbar styles itself."""
        if window is None:
            return
        try:
            from System import Action  # type: ignore

            form = window.native
            form.Invoke(Action(lambda: setattr(form, "ShowInTaskbar", bool(show))))
        except Exception:
            log.debug("Form.ShowInTaskbar could not be set", exc_info=True)

    # -- popup style introspection (used for assertions/logging) ---------
    def _popup_exstyle(self) -> int:
        hwnd = self._popup_handle()
        if not hwnd:
            return 0
        try:
            return int(winapi.user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        except Exception:
            return 0

    def _popup_style_ok(self) -> bool:
        """True when the popup can neither appear in the taskbar nor take focus."""
        ex = self._popup_exstyle()
        return bool(ex & WS_EX_TOOLWINDOW) and bool(ex & WS_EX_NOACTIVATE) \
            and not (ex & WS_EX_APPWINDOW)

    def _push_popup(self, func: str, args: list) -> None:
        self._eval(self.popup, f"window.__app && window.__app.{func} && window.__app.{func}.apply(null, {json.dumps(args)})")

    def _push_result(self, func: str, args: list) -> None:
        self._eval(self.result, f"window.__app && window.__app.{func} && window.__app.{func}.apply(null, {json.dumps(args)})")

    @staticmethod
    def _eval(window, js: str) -> bool:
        if window is None:
            return False
        try:
            window.evaluate_js(js)
            return True
        except Exception:
            log.debug("evaluate_js failed (page not ready?)", exc_info=True)
            return False

    # ------------------------------------------------------------ popup api
    def popup_state(self) -> tuple[bool, tuple[int, int, int, int] | None]:
        """Called from the hook thread — must stay cheap."""
        return self._popup_visible, self._popup_bounds

    def popup_visible(self) -> bool:
        return self._popup_visible

    def window_rects(self) -> list[tuple[int, int, int, int]]:
        """Physical screen rects of our visible windows (debug/diagnostics)."""
        rects = []
        for hwnd in (self._popup_hwnd, self._result_hwnd):
            if not hwnd:
                continue
            try:
                import win32gui

                if not win32gui.IsWindowVisible(hwnd):
                    continue
                rects.append(tuple(int(value) for value in win32gui.GetWindowRect(hwnd)))
            except Exception:
                continue
        return rects

    def owns_point(self, point: tuple[int, int]) -> bool:
        """True when the given physical screen point is covered by our own UI.

        Uses the window actually under the point (not cached rectangles), so a
        hidden window never suppresses a real selection somewhere else.
        """
        try:
            import win32gui

            self._refresh_handles()
            if self._popup_hwnd == 0 and self._result_hwnd == 0:
                return False
            hwnd = win32gui.WindowFromPoint((int(point[0]), int(point[1])))
            if not hwnd:
                return False
            for own in (self._popup_hwnd, self._result_hwnd, self._safe_hwnd(self.settings)):
                if not own:
                    continue
                if hwnd == own or win32gui.IsChild(own, hwnd):
                    return True
                # WebView2 renders in child windows; climb to the root as well.
                if win32gui.GetAncestor(hwnd, GA_ROOT) == own:
                    return True
        except Exception:
            log.debug("own window lookup failed", exc_info=True)
        return False

    def show_popup(self, point: tuple[int, int], preview: str = "") -> None:
        if self.popup is None:
            return
        logical = winapi.to_logical(point)
        offset = self.cfg.get("ui.popup_offset", [12, 18]) or [12, 18]
        off_x, off_y = int(offset[0]), int(offset[1])
        left, top = logical[0] + off_x, logical[1] + off_y
        work = winapi.work_area_logical(point)
        wl, wt, wr, wb = work

        if left + POPUP_W > wr:
            left = logical[0] - POPUP_W - off_x
        if top + POPUP_H > wb:
            top = logical[1] - POPUP_H - off_y
        left = max(wl, min(left, wr - POPUP_W))
        top = max(wt, min(top, wb - POPUP_H))

        self._push_popup("setPreview", [preview])
        previous = winapi.foreground_window()
        try:
            self.popup.move(int(left), int(top))
            self.popup.show()
        except Exception:
            log.exception("showing popup failed")
            return
        # Showing can restore WinForms' own styles; re-assert ours afterwards
        # (also refreshes the handle in case it was recreated).
        self._refresh_handles()
        self._enforce_no_taskbar(self._popup_handle(), no_activate=True)
        if not self._popup_style_ok():
            log.warning("popup window styles could not be fully applied (ex=0x%08x)",
                        self._popup_exstyle())
        # pywebview's show() calls Form.Activate(), which can pull the focus away
        # from whatever the user is reading. Give it back straight away; the popup
        # still receives mouse clicks without being the foreground window.
        if previous and previous != self._popup_hwnd and winapi.is_window(previous):
            if winapi.foreground_window() != previous:
                winapi.set_foreground_window(previous)
        with self._lock:
            self._popup_visible = True
            self._popup_bounds = (left, top, left + POPUP_W, top + POPUP_H)
        log.info("popup shown at cursor=%s logical=(%s,%s) work=%s bounds=%s focus=%s",
                 point, left, top, work, self._popup_bounds,
                 winapi.window_title(winapi.foreground_window())[:40])

    def hide_popup(self) -> None:
        with self._lock:
            if not self._popup_visible:
                return
            self._popup_visible = False
            self._popup_bounds = None
        try:
            if self.popup is not None:
                self.popup.hide()
        except Exception:
            log.debug("hiding popup failed", exc_info=True)

    # ------------------------------------------------------------ result api
    def result_visible(self) -> bool:
        return self._result_open

    def begin_result_drag(self, edge: str) -> bool:
        """Let Windows own the move/resize loop, including capture and DPI."""
        if self.result is None or edge not in RESIZE_HIT_TESTS:
            return False
        try:
            import win32api
            import win32con
            import win32gui
            from System import Action  # type: ignore

            def drag() -> None:
                # A fast click may already have ended while the bridge was busy.
                if not win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000:
                    return
                hwnd = self._refresh_result_handle()
                if edge != "move":
                    self._manual_size = True
                    self._fit_target = None
                x, y = winapi.cursor_pos()
                win32gui.ReleaseCapture()
                win32gui.SendMessage(hwnd, win32con.WM_NCLBUTTONDOWN,
                                     RESIZE_HIT_TESTS[edge],
                                     (x & 0xffff) | ((y & 0xffff) << 16))

            self.result.native.Invoke(Action(drag))
            self._on_result_resized()
            self._save_geometry()
            return True
        except Exception:
            log.exception("moving/resizing the result card failed")
            return False

    def control_result_window(self, action: str) -> bool:
        if self.result is None:
            return False
        if action == "close":
            self._on_result_closing()
            return True
        if action not in ("grow", "shrink", "default"):
            return False
        with self._geometry_lock:
            try:
                width, height = int(self.result.width), int(self.result.height)
                x, y = int(self.result.x), int(self.result.y)
                center = winapi.to_physical((x + width // 2, y + height // 2))
                left, top, right, bottom = winapi.work_area_logical(center)
                if action == "default":
                    target_w = (right - left) // RESULT_SIZE_DIVISOR
                    target_h = (bottom - top) // RESULT_SIZE_DIVISOR
                else:
                    factor = 1.2 if action == "grow" else 1 / 1.2
                    target_w, target_h = round(width * factor), round(height * factor)
                target_w = min(right - left, max(RESULT_MIN[0], target_w))
                target_h = min(bottom - top, max(RESULT_MIN[1], target_h))
                x = max(left, min(x, right - target_w))
                y = max(top, min(y, bottom - target_h))
                self._manual_size = True
                self._fit_target = None
                self.result.resize(target_w, target_h)
                self.result.move(x, y)
                self._result_baseline_h = target_h
                self._save_geometry()
                return True
            except Exception:
                log.exception("card size control failed")
                return False

    def set_result_loading(self, direction: str, original: str, entry_id, model: str) -> None:
        # A new translation may be longer than the last one: give it the full
        # height again before the content arrives.
        self._restore_result_height()
        self._push_result("reset", [{
            "direction": direction,
            "original": original,
            "id": entry_id,
            "model": model,
            "sourceInfo": prepare_document(original).ui_info(),
        }])

    def push_stream(self, text: str) -> None:
        self._push_result("stream", [text])

    def set_result_final(self, text: str, meta: dict) -> None:
        self._push_result("final", [text, meta])

    def set_result_error(self, message: str, detail: str = "") -> None:
        self._push_result("error", [message, detail])

    def push_history(self, items: list, current_id) -> None:
        self._push_result("history", [items, current_id])

    def push_hotkeys(self) -> None:
        self._push_result("hotkeys", [
            str(self.cfg.get("behavior.hotkey_chinese", "")),
            str(self.cfg.get("behavior.hotkey_english", "")),
        ])

    def push_font_size(self) -> None:
        self._push_result("fontSize", [self.cfg.get("ui.font_size", 15)])

    def show_result(self, activate: bool = True, scroll_top: bool = False) -> None:
        if self.result is None:
            return
        try:
            self.result.show()
            if activate:
                self.result.restore()
        except Exception:
            log.exception("showing result window failed")
        # Windows refuses SetForegroundWindow for a background process (e.g. when
        # the translation came from a hotkey), which would leave the card hidden
        # behind the current window. Raise it to the top without taking focus.
        self._raise_window(self._refresh_result_handle())
        self._result_open = True
        if scroll_top:
            self._push_result("focusContent", [])

    def _refresh_result_handle(self) -> int:
        result = self._safe_hwnd(self.result)
        if result:
            self._result_hwnd = result
        return self._result_hwnd

    @staticmethod
    def _raise_window(hwnd: int) -> None:
        if not hwnd:
            return
        try:
            winapi.user32.SetWindowPos(
                wintypes.HWND(hwnd), HWND_TOP, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            log.debug("raising a window failed", exc_info=True)

    def set_result_title(self, text: str) -> None:
        try:
            if self.result is not None:
                self.result.set_title(text)
        except Exception:
            log.debug("set_title failed", exc_info=True)

    def _save_geometry(self) -> None:
        if self.result is None:
            return
        try:
            height = int(self._result_baseline_h or self.result.height)
            config_mod.save_state({
                "result_size": [int(self.result.width), height],
                "result_pos": [int(self.result.x), int(self.result.y)],
            })
        except Exception:
            log.debug("saving window geometry failed", exc_info=True)

    # ---------------------------------------------------- window auto-height
    def _on_result_resized(self) -> None:
        """Remember a size the user chose; ignore the resizes we do ourselves."""
        if self.result is None:
            return
        try:
            height = int(self.result.height)
        except Exception:
            return
        if self._fit_target is not None and abs(height - self._fit_target) <= 2:
            return
        self._fit_target = None
        self._result_baseline_h = max(RESULT_MIN[1], height)

    def _restore_result_height(self) -> None:
        if self.result is None or self._result_baseline_h <= 0:
            return
        try:
            current = int(self.result.height)
            if current >= self._result_baseline_h:
                return
            self._fit_target = int(self._result_baseline_h)
            self.result.resize(int(self.result.width), int(self._result_baseline_h))
        except Exception:
            log.debug("restoring the result window height failed", exc_info=True)

    def fit_result_height(self, needed: int) -> None:
        """Shrink the result window to its content (only when
        ui.fit_to_content is enabled — the default keeps a fixed card)."""
        if self.result is None or self._manual_size or not bool(self.cfg.get("ui.fit_to_content", False)):
            return
        if self._result_baseline_h <= 0:
            return
        try:
            width = int(self.result.width)
            current = int(self.result.height)
        except Exception:
            return
        target = max(RESULT_FIT_MIN_H, min(int(needed), int(self._result_baseline_h)))
        if abs(target - current) < 24:
            return
        self._fit_target = target
        try:
            self.result.resize(width, target)
            log.debug("result window fitted: %s -> %s", current, target)
        except Exception:
            log.debug("fitting the result window failed", exc_info=True)
