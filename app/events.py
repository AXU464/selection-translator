"""Tiny event bus: threads communicate with the UI loop through one queue."""

from __future__ import annotations

import queue
from dataclasses import dataclass, field

# event kinds ---------------------------------------------------------------
CAPTURED = "captured"              # data: result (CaptureResult)
CAPTURE_FAILED = "capture_failed"  # data: reason
POPUP_ACTION = "popup_action"      # data: direction ('zh' | 'en')
DISMISS = "dismiss"                # hide the popup (escape / click elsewhere / scroll / timeout)
JOB_STREAM = "job_stream"          # data: job_id, text
JOB_DONE = "job_done"              # data: job_id, text, elapsed, model, prompt_tokens, completion_tokens
JOB_FAILED = "job_failed"          # data: job_id, message, detail
TOGGLE_ENABLED = "toggle_enabled"
RELOAD_CONFIG = "reload_config"
OPEN_RESULT = "open_result"
OPEN_SETTINGS = "open_settings"
SETTINGS_CHANGED = "settings_changed"
HISTORY_CHANGED = "history_changed"
CLIPBOARD_TRANSLATE = "clipboard_translate"   # data: direction
RESULT_ACTION = "result_action"    # data: action
RESULT_CLOSED = "result_closed"    # result card hidden by the user
UI_READY = "ui_ready"              # data: which
QUIT = "quit"


@dataclass
class Event:
    kind: str
    data: dict = field(default_factory=dict)


def make_queue() -> "queue.Queue[Event]":
    return queue.Queue()
