#!/usr/bin/env python3
"""Run the Urban Density dashboard in its own native window (pywebview / WKWebView).

Starts the local server (static files + add-city API) on a background thread and
points a native window at it. Closing the window stops everything. Falls back to
the system browser if pywebview is not installed.

Usage:
    python3 app_window.py
"""
import threading

from urban.server import make_server, find_free_port, RUNNER

DASHBOARD = "urban_dashboard.html"
TITLE = "Urban Density"


def main():
    port = find_free_port()
    url = f"http://localhost:{port}/{DASHBOARD}"
    httpd = make_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        import webview  # pywebview
    except ImportError:
        import webbrowser
        print(f"pywebview not installed — opening in browser: {url}\nPress Ctrl+C to stop.")
        webbrowser.open(url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    webview.create_window(TITLE, url, width=1500, height=950, min_size=(1000, 650))
    try:
        webview.start()
    finally:
        RUNNER.shutdown()


if __name__ == "__main__":
    main()
