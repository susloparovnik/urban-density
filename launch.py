#!/usr/bin/env python3
"""Launch the Urban Density dashboard over a local HTTP server.

The dashboard loads its map layers (heatmap PNGs, boundary/core GeoJSON)
via fetch(), which browsers block under the file:// protocol. Opening the
.html directly therefore shows a blank map with no boundaries or heatmap.

This launcher serves the project directory over http://localhost and opens
the dashboard in the default browser, so the layers always load. It picks a
free port automatically and keeps running until you press Ctrl+C.

Usage:
    python3 launch.py            # serve + open dashboard
    python3 launch.py --no-open  # serve only, don't open a browser
    python3 launch.py --port N   # force a specific port
"""
import contextlib
import functools
import http.server
import os
import socket
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = "urban_dashboard.html"


def find_free_port(preferred=None):
    """Return an available TCP port, trying `preferred` first."""
    if preferred:
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                print(f"Port {preferred} is busy — picking a free one instead.")
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    argv = sys.argv[1:]
    open_browser = "--no-open" not in argv
    preferred = None
    if "--port" in argv:
        try:
            preferred = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            sys.exit("--port requires a number, e.g. --port 8765")

    if not os.path.exists(os.path.join(ROOT, DASHBOARD)):
        sys.exit(f"Cannot find {DASHBOARD} in {ROOT}")

    port = find_free_port(preferred)
    url = f"http://localhost:{port}/{DASHBOARD}"

    class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
        """Serve without caching — a re-run of the pipeline changes the dashboard
        and its layers on disk, and a cached copy would show stale numbers."""

        def send_head(self):
            for h in ("If-Modified-Since", "If-None-Match"):
                if h in self.headers:
                    del self.headers[h]
            return super().send_head()

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, max-age=0")
            super().end_headers()

    # Serve from the project directory regardless of where launch.py is called.
    handler = functools.partial(NoCacheHandler, directory=ROOT)

    class QuietServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

    httpd = QuietServer(("127.0.0.1", port), handler)

    print("=" * 56)
    print("  Urban Density dashboard")
    print(f"  Serving: {ROOT}")
    print(f"  Open:    {url}")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)

    if open_browser:
        # Open after the server is actually accepting connections.
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
