#!/usr/bin/env python3
"""Launch the Urban Density dashboard.

Serves the project over http://localhost (the dashboard loads its data with fetch(),
which browsers block on file:// pages) and opens it in the default browser. The same
server exposes the small API the dashboard uses to add cities from the UI.

Usage:
    python3 launch.py             # serve + open the dashboard
    python3 launch.py --no-open   # serve only
    python3 launch.py --port N    # force a port
    python3 launch.py --verbose   # log every request
"""
import sys
import threading
import webbrowser

from urban.server import make_server, find_free_port, RUNNER

DASHBOARD = "urban_dashboard.html"


def main():
    argv = sys.argv[1:]
    preferred = None
    if "--port" in argv:
        try:
            preferred = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            sys.exit("--port requires a number, e.g. --port 8765")
    port = find_free_port(preferred)
    url = f"http://localhost:{port}/{DASHBOARD}"
    httpd = make_server(port, verbose="--verbose" in argv)

    print("=" * 56)
    print("  Urban Density dashboard")
    print(f"  Open:    {url}")
    print("  Press Ctrl+C to stop.")
    print("=" * 56)
    if "--no-open" not in argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        RUNNER.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
