"""Configuration and state handling.

`config.json` holds user settings (API key, model, behaviour switches) and is
created with sensible defaults on first run.  `state.json` holds ephemeral
things like window geometry so the result window re-opens where you left it.
"""

from __future__ import annotations

import copy
import json
import logging
import pathlib
import threading

log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
LOG_DIR = ROOT / "logs"

DEFAULTS: dict = {
    "api": {
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-flash",
        "temperature": 0.2,
        "max_tokens": 16000,
        # False sends {"thinking": {"type": "disabled"}} which is noticeably
        # faster and just as accurate for translation work.
        "thinking": False,
        "stream": True,
        "timeout_seconds": 120,
        "retries": 1,
        "extra_instructions": "",
    },
    "behavior": {
        "detect_selection": True,
        "double_click_select": True,
        "drag_threshold_px": 5,
        "min_chars": 2,
        "max_chars": 12000,
        "capture_delay_ms": 70,
        "restore_clipboard": True,
        "auto_dismiss_seconds": 9,
        "hotkey_chinese": "ctrl+alt+c",
        "hotkey_english": "ctrl+alt+e",
        "hotkey_enabled": True,
    },
    "ui": {
        "theme": "system",
        "font_size": 14,
        "popup_offset": [12, 18],
        "result_on_top": False,
        # false = fixed card (a quarter of the work area) that scrolls; true =
        # shrink the window to fit short translations.
        "fit_to_content": False,
        # false keeps the result window out of the taskbar / Alt+Tab (silent by
        # default); reach it again from the tray menu.
        "result_in_taskbar": False,
        "debug_webview": False,
    },
    "logging": {
        "level": "INFO",
        "max_mb": 2,
        "backups": 3,
    },
}


def _merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into a copy of `base`."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_json(path: pathlib.Path) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log.exception("could not parse %s, using defaults", path.name)
        return {}


class Config:
    """Thread-safe accessor around the config dict."""

    def __init__(self, path: pathlib.Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data = _merge(DEFAULTS, _read_json(path))
        self.save()

    # -- access ---------------------------------------------------------
    def get(self, dotted: str, default=None):
        """Fetch a value by dotted path, e.g. cfg.get('behavior.min_chars')."""
        node = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def as_dict(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update_values(self, values: dict) -> None:
        """Commit validated edits atomically; keep memory unchanged on I/O failure."""
        with self._lock:
            data = copy.deepcopy(self._data)
            for dotted, value in values.items():
                parts = dotted.split(".")
                node = data
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._data = data

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        with self._lock:
            data = copy.deepcopy(self._data)
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                tmp.replace(self.path)
            except Exception:
                log.exception("could not save config")

    def reload(self) -> None:
        """Re-read the file from disk, keeping defaults for missing keys."""
        data = _merge(DEFAULTS, _read_json(self.path))
        with self._lock:
            self._data = data
        log.info("configuration reloaded")

    @property
    def api_key(self) -> str:
        return (self.get("api.api_key") or "").strip()


def load_state() -> dict:
    return _read_json(STATE_PATH)


def save_state(patch: dict) -> None:
    state = _read_json(STATE_PATH)
    state.update(patch or {})
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        log.exception("could not save state")
