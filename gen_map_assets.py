#!/usr/bin/env python3
"""Re-render the dashboard map layers (heatmap PNG, core polygons, cleaned boundary)
from the metric.tif files that a previous compute left in urban_cities/<id>/<source>/.

Usage
  python gen_map_assets.py                    # every city / source that has a metric.tif
  python gen_map_assets.py --source ghs cairo
Only needed after changing the rendering code — add_city.py / run_urban.py already do this.
"""
import argparse
import json

from urban import pipeline, registry
from urban.config import SOURCES


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--source", choices=list(SOURCES), help="default: all sources")
    a = ap.parse_args()

    reg = registry.load()
    n = 0
    for city in reg["cities"]:
        if a.ids and city["id"] not in a.ids:
            continue
        for src in ([a.source] if a.source else list(SOURCES)):
            if not (pipeline.city_dir(city["id"], src) / "metric.tif").exists():
                continue
            bounds = pipeline.make_assets(city["id"], src, city["lat"], city["lon"])
            res = city.setdefault("sources", {}).setdefault(src, {})
            res["bounds"] = bounds
            n += 1
            print(f"  OK {city['id']}/{src}")
    registry.save(reg)
    print(f"{n} layer sets rendered")


if __name__ == "__main__":
    main()
