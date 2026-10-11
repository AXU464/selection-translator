"""Validated, secret-safe settings exposed to the local preferences window."""
from __future__ import annotations

import json
import math
from urllib.parse import urlsplit

from .config import ROOT
from .winapi import MODIFIER_VKS, parse_hotkey

THEMES = {"system", *json.loads((ROOT / "web" / "themes.json").read_text(encoding="utf-8"))}
NUMBERS = {
    "ui.font_size": (11, 32, int), "api.temperature": (0, 2, float),
    "api.max_tokens": (256, 65536, int), "api.timeout_seconds": (10, 300, int),
    "api.retries": (0, 3, int), "behavior.max_chars": (100, 100000, int),
    "behavior.auto_dismiss_seconds": (0, 120, int),
}
LABELS = {"ui.font_size": "阅读字号", "api.temperature": "生成温度", "api.max_tokens": "最大输出 tokens",
          "api.timeout_seconds": "超时时间", "api.retries": "重试次数", "behavior.max_chars": "选区字符上限",
          "behavior.auto_dismiss_seconds": "按钮自动收起时间"}
BOOLEANS = {
    "ui.result_on_top", "ui.fit_to_content", "ui.result_in_taskbar", "api.stream", "api.thinking",
    "behavior.detect_selection", "behavior.double_click_select", "behavior.restore_clipboard",
    "behavior.hotkey_enabled",
}
STRINGS = {"ui.theme", "api.model", "api.base_url", "api.extra_instructions",
           "behavior.hotkey_chinese", "behavior.hotkey_english"}
KEYS = set(NUMBERS) | BOOLEANS | STRINGS


def connection_values(values, cfg):
    if not isinstance(values, dict) or set(values) - {"api.api_key", "api.base_url"}:
        raise ValueError("连接参数无效，请重新打开设置。")
    key = values.get("api.api_key", "")
    base = values.get("api.base_url", cfg.get("api.base_url", ""))
    if not isinstance(key, str) or not isinstance(base, str):
        raise ValueError("请填写有效的 API Key 与地址。")
    key = key.strip() or str(cfg.get("api.api_key", "")).strip()
    if not key:
        raise ValueError("请先填写 API Key。")
    if any(c.isspace() for c in key):
        raise ValueError("API Key 不能包含空格或换行。")
    try:
        url = urlsplit(base.strip())
        valid = url.scheme in {"http", "https"} and url.hostname and not (url.username or url.password or url.query or url.fragment)
        url.port  # validate malformed ports before requesting anything
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请填写完整的 HTTP/HTTPS API 地址，不包含用户名、密码或查询参数。")
    return {"api.api_key": key, "api.base_url": base.strip().rstrip("/")}


def snapshot(cfg) -> dict:
    return {"values": {key: cfg.get(key) for key in sorted(KEYS)}, "hasApiKey": bool(cfg.get("api.api_key", ""))}


def validate(changes: dict, cfg) -> dict:
    if not isinstance(changes, dict) or set(changes) - (KEYS | {"api.api_key"}):
        raise ValueError("设置内容无效，请重新打开设置。")
    cleaned = {}
    for key, value in changes.items():
        if key in BOOLEANS:
            if not isinstance(value, bool):
                raise ValueError("开关选项必须为开启或关闭。")
        elif key in NUMBERS:
            low, high, kind = NUMBERS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("请填写有效的数字。")
            if not low <= value <= high or (kind is int and value != int(value)):
                raise ValueError(f"{LABELS[key]}的范围是 {low}–{high}" + ("（整数）。" if kind is int else "。"))
            value = kind(value)
        else:
            if not isinstance(value, str):
                raise ValueError("请填写有效的文本。")
            value = value.strip()
            if len(value) > (8000 if key == "api.extra_instructions" else 2048):
                raise ValueError("输入内容过长。")
            if key == "api.api_key" and not value:
                continue  # empty password field always preserves the stored key
            if key == "ui.theme" and value not in THEMES:
                raise ValueError("请选择列表中的主题。")
            if key == "api.model" and (not value or any(c.isspace() for c in value)):
                raise ValueError("模型名称不能为空或包含空格。")
            if key == "api.base_url":
                url = urlsplit(value)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError("API 地址应为完整的 HTTP/HTTPS 地址，不包含用户名、密码或查询参数。")
                value = value.rstrip("/")
            if key == "api.api_key" and any(c.isspace() for c in value):
                raise ValueError("API Key 不能包含空格或换行。")
        cleaned[key] = value
    hotkeys = []
    for key in ("behavior.hotkey_chinese", "behavior.hotkey_english"):
        value = cleaned.get(key, cfg.get(key, ""))
        parts = str(value).lower().replace(" ", "").split("+")
        parsed = parse_hotkey(value)
        if not parsed or not parsed[1] or sum(part not in MODIFIER_VKS for part in parts) != 1:
            raise ValueError("快捷键需要修饰键和一个按键，例如 ctrl+alt+c。")
        hotkeys.append(parsed)
        if key in cleaned:
            cleaned[key] = "+".join(parts)
    if hotkeys[0][0] == hotkeys[1][0] and (hotkeys[0][1] <= hotkeys[1][1] or hotkeys[1][1] <= hotkeys[0][1]):
        raise ValueError("两种翻译方向的快捷键不能相同或相互包含。")
    return cleaned
