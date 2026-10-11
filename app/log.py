"""Logging setup: rotating file log plus optional console output."""

from __future__ import annotations

import logging
import logging.handlers
import pathlib
import sys
import threading

from . import config as config_mod

_configured = False


def setup(cfg) -> pathlib.Path:
    """Configure the root logger once; returns the log file path."""
    global _configured
    log_dir = config_mod.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"

    if not _configured:
        level_name = str(cfg.get("logging.level", "INFO")).upper()
        level = getattr(logging, level_name, logging.INFO)
        max_bytes = int(cfg.get("logging.max_mb", 2) * 1024 * 1024)
        backups = int(cfg.get("logging.backups", 3))

        fmt = logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        root = logging.getLogger()
        root.setLevel(level)
        for handler in list(root.handlers):
            root.removeHandler(handler)

        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)

        if sys.stderr is not None and sys.stderr.isatty():
            console = logging.StreamHandler(sys.stderr)
            console.setFormatter(fmt)
            root.addHandler(console)

        logging.getLogger("webview").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        _configured = True

        _install_excepthooks()
        logging.getLogger(__name__).info("logging to %s", log_path)

    return log_path


def _install_excepthooks() -> None:
    def handle(exc_type, exc_value, exc_tb):
        logging.getLogger("uncaught").critical(
            "unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = handle

    def thread_hook(args):
        logging.getLogger("uncaught").critical(
            "unhandled exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook
