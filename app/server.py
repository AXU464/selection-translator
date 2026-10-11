"""Local static file server for the UI pages.

WebView2 gives pages served over http://127.0.0.1 full access to local assets
(KateX fonts, marked, CSS) without file:// restrictions, and binding to the
loopback interface only keeps it private (no firewall prompt either).
"""

from __future__ import annotations

import functools
import logging
import pathlib
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)


class _Handler(SimpleHTTPRequestHandler):
    server_version = "SelectionTranslator/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - quiet by design
        log.debug("http %s", fmt % args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        super().end_headers()


class WebServer:
    def __init__(self, directory: pathlib.Path):
        self.directory = directory
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> str:
        handler = functools.partial(_Handler, directory=str(self.directory))
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="web-server", daemon=True)
        self._thread.start()
        log.info("UI server at %s", self.base_url)
        return self.base_url

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                log.debug("stopping UI server failed", exc_info=True)

    def url(self, page: str) -> str:
        return f"{self.base_url}/{page.lstrip('/')}"
