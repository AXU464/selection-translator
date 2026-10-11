"""Application wiring: threads, the event loop, translation jobs and history."""

from __future__ import annotations

import logging
import os
import pathlib
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import webview

from . import autostart, clipboard, config as config_mod, events
from .language import LOCAL_MODEL_LABEL, SAME_LANGUAGE_MESSAGE, matches_target_language
from .selection import CaptureResult, SelectionCapture
from .server import WebServer
from .translator import Translator
from .tray import Tray
from .ui import UiController

log = logging.getLogger(__name__)

DIRECTION_LABELS = {"zh": "中文", "en": "English"}
HISTORY_LIMIT = 40
STREAM_INTERVAL = 0.09


class Application:
    def __init__(self, cfg, root: pathlib.Path):
        self.cfg = cfg
        self.root = root
        self.queue: queue.Queue[events.Event] = events.make_queue()
        self.translator = Translator(cfg)
        self.server = WebServer(root / "web")
        self.icon_path = root / "assets" / "icon.ico"
        self.ui = UiController(cfg, self._emit, root / "web", root / ".webview", self.icon_path)
        self.capture = SelectionCapture(
            cfg, self._emit,
            popup_state=self.ui.popup_state,
            own_point=self.ui.owns_point,
        )
        self.tray = Tray(cfg, self._emit, self.icon_path)
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="translate")

        self.history: deque[dict] = deque(maxlen=HISTORY_LIMIT)
        self._history_seq = 0
        self._job_seq = 0
        self._current_job: dict | None = None
        self._cancel: threading.Event | None = None
        self._pending: str | None = None
        self._pending_truncated = False
        self._pending_selection: CaptureResult | None = None
        self._popup_deadline: float | None = None
        self._last_stream_push = 0.0
        self._running = True
        self._quitting = False

    # ------------------------------------------------------------- lifecycle
    def run(self) -> None:
        try:
            autostart.ensure_default(self.cfg)
        except OSError:
            log.exception("could not initialize autostart preference")
        base_url = self.server.start()
        self.ui.create_windows(base_url)
        if not self.capture.start():
            log.error("selection detection is unavailable (hooks failed)")
            self.tray.notify("无法安装全局钩子，划词检测不可用。可尝试以管理员身份运行。", "划词翻译")
        self.capture.set_enabled(self.enabled_state())
        self.tray.set_enabled(self.enabled_state())
        self.tray.start()

        try:
            webview.start(
                self._bootstrap,
                gui="edgechromium",
                debug=bool(self.cfg.get("ui.debug_webview", False)),
                private_mode=False,
                storage_path=str(self.root / ".webview"),
                icon=str(self.icon_path) if self.icon_path.exists() else None,
            )
        finally:
            self.shutdown()

    def _bootstrap(self) -> None:
        """Runs on a pywebview worker thread once the GUI loop is alive."""
        log.info("GUI loop started")
        self._wait_for_pages()
        self.ui.after_start()
        self._push_history()
        self._maybe_onboard()
        self._loop()

    def _wait_for_pages(self, timeout: float = 15.0) -> None:
        for window in (self.ui.popup, self.ui.result):
            if window is None:
                continue
            try:
                window.events.loaded.wait(timeout)
            except Exception:
                log.debug("waiting for page load failed", exc_info=True)
        log.debug("UI pages ready")

    def _maybe_onboard(self) -> None:
        state = config_mod.load_state()
        if state.get("onboarded"):
            return
        config_mod.save_state({"onboarded": True})
        self.ui.show_result(activate=False)

    def shutdown(self) -> None:
        log.info("shutting down")
        if self._cancel is not None:
            self._cancel.set()
        self._running = False
        try:
            self.capture.stop()
        except Exception:
            log.debug("stopping capture failed", exc_info=True)
        try:
            self.tray.stop()
        except Exception:
            log.debug("stopping tray failed", exc_info=True)
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            log.debug("stopping executor failed", exc_info=True)
        try:
            self.server.stop()
        except Exception:
            log.debug("stopping server failed", exc_info=True)

    def finalize(self) -> None:
        """Called by main() after the GUI loop returned."""
        try:
            self.ui._save_geometry()  # noqa: SLF001 - deliberate
        except Exception:
            log.debug("saving geometry failed", exc_info=True)

    # ---------------------------------------------------------------- events
    def _emit(self, kind: str, payload: dict | None = None) -> None:
        try:
            self.queue.put_nowait(events.Event(kind, payload or {}))
        except Exception:  # pragma: no cover - the queue is unbounded
            log.exception("could not queue event %s", kind)

    def enabled_state(self) -> bool:
        return bool(self.cfg.get("behavior.detect_selection", True))

    def _loop(self) -> None:
        last_heartbeat = 0.0
        while self._running:
            try:
                event = self.queue.get(timeout=0.2)
            except queue.Empty:
                self._check_deadline()
                last_heartbeat = self._heartbeat(last_heartbeat)
                continue
            try:
                self._dispatch(event)
            except Exception:
                log.exception("handling %s failed", event.kind)
            self._check_deadline()
            last_heartbeat = self._heartbeat(last_heartbeat)
        log.info("event loop finished")

    def _heartbeat(self, last: float) -> float:
        """Periodic health check: keeps the hooks alive and leaves a trace."""
        now = time.monotonic()
        if now - last < 60:
            return last
        self.capture.watchdog()
        log.debug("heartbeat: capture=%s events_queued=%s history=%s",
                  self.capture.stats(), self.queue.qsize(), len(self.history))
        return now

    def _check_deadline(self) -> None:
        if self._popup_deadline is None:
            return
        if time.monotonic() >= self._popup_deadline:
            self._popup_deadline = None
            log.debug("popup dismissed (auto)")
            self.ui.hide_popup()

    def _dispatch(self, event: events.Event) -> None:
        kind, data = event.kind, event.data

        if kind == events.CAPTURED:
            result = data.get("result")
            if result is None:
                return
            request = result.request
            if request.reason == "result-closed":
                if (not self._is_current(request.restore_job_id)
                        or self.ui.result_visible() or self.ui.popup_visible()
                        or not self.capture.enabled):
                    return
            if request.mode == "translate":
                self.start_translation(result.text, request.direction or "zh",
                                       origin=request.reason, truncated=result.truncated,
                                       selection=result)
            else:
                self._pending = result.text
                self._pending_truncated = result.truncated
                self._pending_selection = result
                self.ui.show_popup(request.point or (0, 0), preview=result.text)
                seconds = float(self.cfg.get("behavior.auto_dismiss_seconds", 9) or 0)
                self._popup_deadline = time.monotonic() + seconds if seconds > 0 else None

        elif kind == events.POPUP_ACTION:
            direction = str(data.get("direction") or "zh")
            text, truncated = self._pending, self._pending_truncated
            self._hide_popup()
            if text:
                self.start_translation(text, direction, origin="popup", truncated=truncated,
                                       selection=self._pending_selection)

        elif kind == events.DISMISS:
            if data.get("reason"):
                log.debug("popup dismissed (%s)", data.get("reason"))
            self._hide_popup()

        elif kind == events.CAPTURE_FAILED:
            if data.get("reason") == "no-selection":
                self.tray.notify("没有检测到选中文本。请先选中文字，再按快捷键。", "划词翻译")

        elif kind == events.JOB_STREAM:
            if self._is_current(data.get("job_id")):
                now = time.monotonic()
                if now - self._last_stream_push >= STREAM_INTERVAL:
                    self._last_stream_push = now
                    self.ui.push_stream(str(data.get("text") or ""))

        elif kind == events.JOB_DONE:
            if self._is_current(data.get("job_id")):
                self._finish_job(data)

        elif kind == events.JOB_FAILED:
            if self._is_current(data.get("job_id")):
                message = str(data.get("message") or "翻译失败")
                detail = str(data.get("detail") or "")
                log.warning("translation failed; details shown only in the result card")
                self.ui.set_result_error(message, detail)

        elif kind == events.TOGGLE_ENABLED:
            self._set_enabled(not self.enabled_state())

        elif kind == events.RELOAD_CONFIG:
            self._reload_config()

        elif kind == events.OPEN_SETTINGS:
            self.ui.show_settings()

        elif kind == events.SETTINGS_CHANGED:
            if any(key.startswith("behavior.hotkey_") for key in data.get("keys", [])):
                self.capture.reapply_hotkeys()
            self.capture.set_enabled(self.enabled_state())
            self.tray.set_enabled(self.enabled_state())
            if not self.enabled_state():
                self._hide_popup()

        elif kind == events.OPEN_RESULT:
            self.ui.show_result(activate=True)

        elif kind == events.CLIPBOARD_TRANSLATE:
            text = clipboard.text_for_translation()
            if not text:
                self.tray.notify("剪贴板里没有可翻译的文本。", "划词翻译")
                return
            self.start_translation(text, str(data.get("direction") or "zh"), origin="clipboard")

        elif kind == events.RESULT_ACTION:
            self._result_action(str(data.get("action") or ""))

        elif kind == events.RESULT_CLOSED:
            selection = (self._current_job or {}).get("selection")
            if selection is not None and not self._quitting and not self.ui.popup_visible():
                self.capture.restore_popup(selection, self._current_job["id"])

        elif kind == events.UI_READY:
            self.ui.push_font_size()
            self.ui.push_hotkeys()
            self._push_history()

        elif kind == events.QUIT:
            self._quit()

    # ------------------------------------------------------------- actions
    def _hide_popup(self) -> None:
        self._popup_deadline = None
        self.ui.hide_popup()

    def _set_enabled(self, enabled: bool) -> None:
        self.cfg.set("behavior.detect_selection", bool(enabled))
        self.cfg.save()
        self.capture.set_enabled(enabled)
        self.tray.set_enabled(enabled)
        if not enabled:
            self._hide_popup()
        self.tray.notify("划词翻译已启用" if enabled else "划词翻译已暂停", "划词翻译")

    def _reload_config(self) -> None:
        self.cfg.reload()
        self.capture.reapply_hotkeys()
        self.capture.set_enabled(self.enabled_state())
        self.tray.set_enabled(self.enabled_state())
        self.tray.refresh()
        self.ui.push_font_size()
        self.ui.push_hotkeys()
        self.ui.apply_preferences(["ui.fit_to_content"])
        log.info("configuration applied")

    def _result_action(self, action: str) -> None:
        if not action:
            return
        if action == "retry":
            if self._current_job:
                self.start_translation(self._current_job["original"],
                                       self._current_job["direction"], origin="retry",
                                       selection=self._current_job.get("selection"))
        elif action == "swap":
            if self._current_job:
                other = "en" if self._current_job["direction"] == "zh" else "zh"
                self.start_translation(self._current_job["original"], other, origin="swap",
                                       selection=self._current_job.get("selection"))
        elif action == "config":
            self.ui.show_settings()
        elif action == "logs":
            self._open_path(config_mod.LOG_DIR)
        elif action.startswith("history:"):
            self._select_history(action.split(":", 1)[1])
        elif action.startswith("copy:"):
            if not clipboard.write_text(action[5:]):
                log.warning("could not write to the clipboard")
        elif action.startswith("jserror:"):
            log.warning("UI javascript error reported (content omitted)")
        elif action == "hidden":
            pass

    @staticmethod
    def _open_path(path: pathlib.Path) -> None:
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception:
            log.exception("could not open %s", path)

    def _quit(self) -> None:
        log.info("quit requested")
        self.finalize()
        self._running = False
        self._quitting = True
        try:
            self.ui.destroy_windows()
        except Exception:
            log.debug("destroying windows failed", exc_info=True)

    # -------------------------------------------------------- translations
    def _is_current(self, job_id) -> bool:
        return bool(self._current_job) and self._current_job.get("id") == job_id

    def start_translation(self, text: str, direction: str, origin: str = "popup",
                          truncated: bool = False, selection: CaptureResult | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        direction = "en" if direction == "en" else "zh"
        same_language = matches_target_language(text, direction)

        if self._cancel is not None:
            self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel

        self._job_seq += 1
        self._history_seq += 1
        job_id, entry_id = self._job_seq, self._history_seq
        self._current_job = {
            "id": job_id,
            "entry": entry_id,
            "original": text,
            "direction": direction,
            "truncated": truncated,
            "origin": origin,
            "selection": selection,
        }
        self._last_stream_push = 0.0

        self.ui.set_result_title(f"译文 · {DIRECTION_LABELS[direction]}")
        model = LOCAL_MODEL_LABEL if same_language else self.translator.model
        self.ui.set_result_loading(direction, text, entry_id, model)
        if same_language:
            # Replace the current job first, so late events from a previous
            # translation cannot overwrite this local notice. Keep the original
            # text available for the source view and the opposite direction.
            self.ui.set_result_final(SAME_LANGUAGE_MESSAGE, {"model": LOCAL_MODEL_LABEL})
            self.ui.show_result(activate=True, scroll_top=True)
            self._push_history()  # A notice is not a completed translation.
            log.info("translation skipped locally: source matches target=%s (origin=%s)",
                     direction, origin)
            return
        self.ui.show_result(activate=True, scroll_top=True)
        log.info("translating %s chars into %s (origin=%s)", len(text), direction, origin)
        self.executor.submit(self._run_job, job_id, text, direction, cancel)

    def _run_job(self, job_id: int, text: str, direction: str, cancel: threading.Event) -> None:
        def on_delta(accumulated: str) -> None:
            self._emit(events.JOB_STREAM, {"job_id": job_id, "text": accumulated})

        try:
            result = self.translator.translate(text, direction, on_delta=on_delta, cancel=cancel)
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            if not cancel.is_set():
                self._emit(events.JOB_FAILED, {
                    "job_id": job_id,
                    "message": getattr(exc, "message", str(exc)),
                    "detail": getattr(exc, "detail", ""),
                })
            else:
                log.debug("job %s cancelled", job_id)
            return

        if cancel.is_set():
            log.debug("job %s superseded, result dropped", job_id)
            return

        self._emit(events.JOB_DONE, {
            "job_id": job_id,
            "text": result.text,
            "elapsed": result.elapsed,
            "model": result.model or self.translator.model,
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
        })

    def _finish_job(self, data: dict) -> None:
        text = str(data.get("text") or "")
        job = self._current_job or {}
        meta = {
            "model": data.get("model") or self.translator.model,
            "elapsed": data.get("elapsed") or 0.0,
            "prompt_tokens": data.get("prompt_tokens") or 0,
            "completion_tokens": data.get("completion_tokens") or 0,
        }
        self.ui.set_result_final(text, meta)

        entry = {
            "id": job.get("entry"),
            "direction": job.get("direction", "zh"),
            "original": job.get("original", ""),
            "translation": text,
            "truncated": job.get("truncated", False),
            "meta": meta,
            "ts": time.time(),
        }
        self.history.appendleft(entry)
        self._push_history()

    def _push_history(self) -> None:
        items = [{"id": entry["id"], "label": self._history_label(entry)} for entry in self.history]
        current = self._current_job.get("entry") if self._current_job else None
        self.ui.push_history(items, current)

    @staticmethod
    def _history_label(entry: dict) -> str:
        preview = " ".join((entry.get("original") or "").split())
        if len(preview) > 22:
            preview = preview[:22] + "…"
        tag = "中" if entry.get("direction") == "zh" else "EN"
        return f"{tag} · {preview}" if preview else f"{tag} · (空)"

    def _select_history(self, raw_id: str) -> None:
        try:
            entry_id = int(raw_id)
        except (TypeError, ValueError):
            return
        entry = next((item for item in self.history if item.get("id") == entry_id), None)
        if entry is None:
            return
        if self._cancel is not None:
            self._cancel.set()  # the user is looking at history, stop streaming into it
        self._current_job = {
            "id": None,
            "entry": entry["id"],
            "original": entry["original"],
            "direction": entry["direction"],
            "truncated": entry.get("truncated", False),
            "origin": "history",
        }
        self.ui.set_result_title(f"译文 · {DIRECTION_LABELS[entry['direction']]}")
        self.ui.set_result_loading(entry["direction"], entry["original"], entry["id"],
                                   (entry.get("meta") or {}).get("model", self.translator.model))
        self.ui.set_result_final(entry["translation"], entry.get("meta") or {})
        self.ui.show_result(activate=True)
        self._push_history()
