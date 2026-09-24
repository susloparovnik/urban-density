#!/usr/bin/env python3
"""Batch-compute cores for cities already listed in cities_all.json.

Usage
  python run_urban.py                         # WorldPop for every city that lacks it
  python run_urban.py --source ghs            # GHS-POP for every city that lacks it
  python run_urban.py --source hrsl cairo giza
  python run_urban.py --force --source worldpop lagos   # recompute

Population data is downloaded into downloads/ on demand (see urban/sources.py).
Use add_city.py to register a city that is not in the list yet.
"""
import argparse

from urban import pipeline, registry
from urban.config import SOURCES, MASKS, DEFAULT_MASK, DEFAULT_SOURCE, RADIUS_M


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="city ids (default: all)")
    ap.add_argument("--source", default=DEFAULT_SOURCE, choices=list(SOURCES))
    ap.add_argument("--mask", choices=MASKS, default=DEFAULT_MASK)
    ap.add_argument("--radius-km", type=float, default=RADIUS_M / 1000)
    ap.add_argument("--force", action="store_true", help="recompute cities that already have this source")
    a = ap.parse_args()

    reg = registry.load()
    todo = [c for c in reg["cities"] if not a.ids or c["id"] in a.ids]
    unknown = set(a.ids) - {c["id"] for c in todo}
    if unknown:
        ap.exit(2, f"unknown ids: {sorted(unknown)}\n")
    if not a.force:
        todo = [c for c in todo if a.source not in c.get("sources", {})]
    print(f"{len(todo)} cities to compute with {SOURCES[a.source]['label']}")

    ok, failed = [], []
    for i, city in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] {city['name']} ({city['id']})")
        try:
            if not city.get("iso3") and a.source in ("worldpop", "hrsl"):
                from urban import sources
                country, iso3 = sources.country_lookup(city["lat"], city["lon"])
                city.setdefault("country", country)
                city["iso3"] = iso3
            s = pipeline.compute(city, a.source, force=a.force, mask=a.mask,
                                 radius_m=int(a.radius_km * 1000))
            if s is None:
                failed.append(city["id"])
                continue
            registry.set_result(reg, city["id"], a.source,
                                registry.result_from_summary(s, s.get("bounds")))
            registry.save(reg)   # persist progress city by city
            c90 = s["core_90pct"]
            print(f"  extent {s['urban_extent_km2']} km² | pop {s['total_population']:,} | "
                  f"core90 {c90['area_km2']} km²")
            ok.append(city["id"])
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001 — keep the batch going
            print(f"  ERROR: {e}")
            failed.append(city["id"])

    print(f"\nDONE: {len(ok)} ok, {len(failed)} failed")
    if failed:
        print("Failed:", failed)


if __name__ == "__main__":
    main()
