#!/usr/bin/env python3
"""Run the Urban Density dashboard in its own native window (WKWebView).

The dashboard loads its map layers (heatmap PNGs, boundary/core GeoJSON) via
fetch(), which browsers block under file://. So we serve the project over a
local HTTP server (background thread) and point a native webview window at it.

This gives a normal app window — no browser chrome, its own Dock entry. Closing
the window stops the server and quits.

Falls back to opening the system browser if pywebview is unavailable.

Usage:
    python3 app_window.py
"""
import contextlib
import http.server
import os
import socket
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = "urban_dashboard.html"
TITLE = "Urban Density"


def find_free_port():
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):  # silence per-request logging
        pass

    def send_head(self):
        # Ignore conditional requests: after a pipeline re-run the dashboard and
        # its layers change on disk, and a cached 304 would show stale numbers.
        for h in ("If-Modified-Since", "If-None-Match"):
            if h in self.headers:
                del self.headers[h]
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()


def start_server(port):
    """Serve ROOT over HTTP on a daemon thread; return the server."""
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    httpd = Server(("127.0.0.1", port), QuietHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    if not os.path.exists(os.path.join(ROOT, DASHBOARD)):
        sys.exit(f"Cannot find {DASHBOARD} in {ROOT}")

    port = find_free_port()
    url = f"http://localhost:{port}/{DASHBOARD}"
    start_server(port)

    try:
        import webview  # pywebview
    except ImportError:
        # Fallback: open in the default browser and keep the server alive.
        import webbrowser
        print(f"pywebview not installed — opening in browser: {url}")
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    webview.create_window(
        TITLE,
        url,
        width=1500,
        height=950,
        min_size=(1000, 650),
    )
    # Blocks on the main thread until the window is closed; then we exit
    # and the daemon server thread dies with the process.
    webview.start()


if __name__ == "__main__":
    main()
