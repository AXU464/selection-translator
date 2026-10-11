"""System tray icon and its menu."""

from __future__ import annotations

import logging
import pathlib
import threading
import time

from PIL import Image, ImageDraw, ImageFont

try:  # pystray is the only optional dependency of the app
    import pystray
    from pystray import Menu, MenuItem
except Exception:  # pragma: no cover
    pystray = None

from . import autostart, config as config_mod, events

log = logging.getLogger(__name__)

ICON_SIZE = 64
_CJK_FONTS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def draw_icon(text: str = "译", size: int = ICON_SIZE) -> Image.Image:
    """Draw the app icon (a rounded gradient square with a glyph)."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # vertical gradient background
    top, bottom = (79, 140, 247), (109, 91, 240)
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)) + (255,)
        draw.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.24), fill=255)
    image.putalpha(mask)

    font = None
    for path in _CJK_FONTS:
        try:
            font = ImageFont.truetype(path, int(size * 0.58), index=0)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=int(size * 0.58))
        except Exception:
            font = ImageFont.load_default()

    draw = ImageDraw.Draw(image)
    try:
        draw.text((size / 2, size / 2 - size * 0.02), text, font=font, fill=(255, 255, 255, 255), anchor="mm")
    except Exception:
        draw.text((size * 0.22, size * 0.2), text, font=font, fill=(255, 255, 255, 255))
    return image


def load_icon_image(icon_path: pathlib.Path | None) -> Image.Image:
    if icon_path and icon_path.exists():
        try:
            image = Image.open(icon_path)
            image.load()
            return image.convert("RGBA")
        except Exception:
            log.debug("could not load %s, drawing icon instead", icon_path, exc_info=True)
    return draw_icon()


class Tray:
    def __init__(self, cfg, emit, icon_path: pathlib.Path | None = None):
        self.cfg = cfg
        self._emit = emit
        self.icon_path = icon_path
        self._icon = None
        self._thread: threading.Thread | None = None
        self._enabled = True

    # -- lifecycle ------------------------------------------------------
    @property
    def available(self) -> bool:
        return pystray is not None

    def start(self) -> None:
        if pystray is None:
            log.warning("pystray unavailable, running without a tray icon")
            return
        image = load_icon_image(self.icon_path)
        menu = Menu(
            MenuItem("划词翻译", self._on_toggle, checked=lambda item: self._enabled, default=False),
            Menu.SEPARATOR,
            MenuItem("翻译剪贴板 → 中文", lambda: self._emit(events.CLIPBOARD_TRANSLATE, {"direction": "zh"})),
            MenuItem("翻译剪贴板 → English", lambda: self._emit(events.CLIPBOARD_TRANSLATE, {"direction": "en"})),
            Menu.SEPARATOR,
            MenuItem("显示结果窗口", lambda: self._emit(events.OPEN_RESULT, {}), default=True),
            MenuItem("设置与主题", self._open_config),
            MenuItem("打开日志目录", self._open_logs),
            MenuItem("开机自启", self._on_toggle_autostart, checked=lambda item: autostart.is_enabled()),
            Menu.SEPARATOR,
            MenuItem("退出", lambda: self._emit(events.QUIT, {})),
        )
        self._icon = pystray.Icon("selection-translator", image, "划词翻译 · DeepSeek", menu)
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        threading.Thread(target=self._log_visibility, name="tray-check", daemon=True).start()

    def _log_visibility(self) -> None:
        """Report once whether the notification-area icon actually appeared."""
        for _ in range(20):
            time.sleep(0.25)
            if self._icon is not None and getattr(self._icon, "visible", False):
                log.info("tray icon is visible")
                return
        log.warning("tray icon did not become visible (Windows may be hiding it in the overflow area)")

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception:
            log.exception("tray icon failed")

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                log.debug("stopping tray icon failed", exc_info=True)

    def notify(self, message: str, title: str = "划词翻译") -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            log.debug("tray notification failed", exc_info=True)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.refresh()

    def refresh(self) -> None:
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                log.debug("tray menu refresh failed", exc_info=True)

    # -- menu actions ---------------------------------------------------
    def _on_toggle(self) -> None:
        self._emit(events.TOGGLE_ENABLED, {})

    def _on_toggle_autostart(self) -> None:
        target = not autostart.is_enabled()
        ok = autostart.set_enabled(target)
        if ok:
            self.cfg.update_values({"behavior.autostart_initialized": True})
        else:
            self.notify("无法更新开机自启，请检查 Windows 启动文件夹的访问权限。", "划词翻译")
        log.info("autostart %s -> %s", target, ok)
        self.refresh()

    def _open_config(self) -> None:
        self._emit(events.OPEN_SETTINGS, {})

    @staticmethod
    def _open_logs() -> None:
        _open_path(config_mod.LOG_DIR)


def _open_path(path: pathlib.Path) -> None:
    try:
        import os
        os.startfile(str(path))  # noqa: S606 - intended shell open
    except Exception:
        log.exception("could not open %s", path)
