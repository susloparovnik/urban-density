#!/bin/bash
# Double-click this file in Finder to launch the Urban Density dashboard.
# It serves the project over a local HTTP server (so boundaries & heatmap
# load) and opens it in your browser. Close the Terminal window to stop.
cd "$(dirname "$0")" || exit 1
# Prefer the bundled virtualenv python if present, else system python3.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi
exec "$PY" launch.py
