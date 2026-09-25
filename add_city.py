#!/usr/bin/env python3
"""Add a city — or a new population source for an existing one — and compute its cores.

Examples
  # a new city, GHS-POP 2025
  python add_city.py --name "Luanda" --lat -8.93 --lon 13.33 --source ghs

  # the same city with a second source (name / coordinates come from cities_all.json)
  python add_city.py --id luanda --source hrsl

  # WorldPop, with a reference ("real") area to compare against
  python add_city.py --name "Bukhara" --lat 39.77 --lon 64.43 --source worldpop --real-area 55.2

  # attach a reference polygon (GeoJSON Feature / geometry) and recompute
  python add_city.py --id bukhara --source worldpop --real-polygon bukhara.geojson --force

What happens
  1. the city is registered in cities_all.json (country / ISO3 resolved via Nominatim if omitted)
  2. the OSM landuse mask within --radius-km is fetched from Overpass (cached in urban_boundaries/)
  3. the population data for --source is downloaded into downloads/ if missing
  4. density-percentile cores are computed → urban_cities/<id>/<source>/summary.json + map layers
  5. the dashboard picks it up on the next reload (python launch.py)
"""
import argparse
import json
import sys
from pathlib import Path

import pyproj
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from urban import boundary, pipeline, registry, sources
from urban.config import SOURCES, MASKS, DEFAULT_MASK, RADIUS_M, THRESHOLDS


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, choices=list(SOURCES), help="population source")
    ap.add_argument("--id", help="city id (existing, or the id to give a new city)")
    ap.add_argument("--name", help="display name (new city)")
    ap.add_argument("--lat", type=float, help="city centre latitude (new city)")
    ap.add_argument("--lon", type=float, help="city centre longitude (new city)")
    ap.add_argument("--country", help="country name (default: Nominatim lookup)")
    ap.add_argument("--iso3", help="ISO-3166 alpha-3 code (default: Nominatim lookup)")
    ap.add_argument("--radius-km", type=float, default=RADIUS_M / 1000,
                    help=f"OSM landuse query radius (default {RADIUS_M // 1000})")
    ap.add_argument("--mask", choices=MASKS, default=DEFAULT_MASK,
                    help="osm = landuse only (default); osm+real = union with the reference polygon")
    ap.add_argument("--real-area", type=float, help="reference area in km² (shown as 'Real Area')")
    ap.add_argument("--real-polygon", help="GeoJSON file with the reference polygon")
    ap.add_argument("--force", action="store_true", help="recompute even if results exist")
    ap.add_argument("--refresh-boundary", action="store_true", help="re-fetch the OSM mask from Overpass")
    return ap.parse_args()


def load_geometry(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("type") == "FeatureCollection":
        feats = d.get("features") or []
        if len(feats) != 1:
            sys.exit("--real-polygon: the FeatureCollection must contain exactly one feature")
        d = feats[0]
    if d.get("type") == "Feature":
        d = d["geometry"]
    if d.get("type") not in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        sys.exit(f"--real-polygon: unsupported geometry type {d.get('type')}")
    return d


def main():
    a = parse_args()
    reg = registry.load()
    city = registry.find(reg, a.id) if a.id else None

    if city is None:
        missing = [k for k in ("name", "lat", "lon") if getattr(a, k) is None]
        if missing:
            sys.exit(f"New city: --{' --'.join(missing)} required (or pass --id of an existing city)")
        cid = a.id or registry.unique_id(reg, registry.slugify(a.name))
        city = {"id": cid, "name": a.name, "lat": a.lat, "lon": a.lon, "sources": {}}
        print(f"New city '{a.name}' → id '{cid}'")
    else:
        print(f"Existing city '{city['name']}' ({city['id']})")
        for k in ("name", "lat", "lon"):
            if getattr(a, k) is not None:
                city[k] = getattr(a, k)

    if a.country:
        city["country"] = a.country
    if a.iso3:
        city["iso3"] = a.iso3.upper()
    if not city.get("iso3") or not city.get("country"):
        country, iso3 = sources.country_lookup(city["lat"], city["lon"])
        city.setdefault("country", country)
        city.setdefault("iso3", iso3)
        print(f"  Country: {city['country']} ({city['iso3']})")

    if a.real_polygon:
        geom = load_geometry(a.real_polygon)
        real_area = a.real_area
        if real_area is None:
            utm = pipeline.auto_utm(city["lat"], city["lon"])
            fwd = pyproj.Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform
            real_area = round(shp_transform(fwd, boundary.real_polygon.__globals__["unary_union"](
                boundary._rings_to_polygons(geom, None, 0))).area / 1e6, 2)
        bounds = boundary.write_real_polygon(city["id"], geom, city.get("name"), city.get("country"), real_area)
        city.update(real_area=real_area, has_real_polygon=True, bounds=bounds)
        print(f"  Reference polygon stored, real area {real_area} km²")
    elif a.real_area is not None:
        city["real_area"] = a.real_area
        city.setdefault("has_real_polygon", False)
        meta = pipeline.city_dir(city["id"]) / "real_meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"real_area": a.real_area, "bounds": city.get("bounds")}))

    was_new = registry.find(reg, city["id"]) is None
    city = registry.upsert(reg, city)
    registry.save(reg)

    print(f"\nComputing {city['name']} with {SOURCES[a.source]['label']} ...")
    try:
        summary = pipeline.compute(city, a.source, force=a.force, mask=a.mask,
                                   radius_m=int(a.radius_km * 1000), refresh_boundary=a.refresh_boundary)
    except BaseException:
        summary = None
        raise
    finally:
        if summary is None and was_new and not city.get("sources"):
            # nothing computed for a city that did not exist before: do not leave a stub behind
            reg["cities"] = [c for c in reg["cities"] if c["id"] != city["id"]]
            registry.save(reg)
            d = pipeline.city_dir(city["id"])
            if d.exists() and not any(d.rglob("summary.json")) and not (d / "real_boundary.geojson").exists():
                import shutil
                shutil.rmtree(d, ignore_errors=True)
    if summary is None:
        sys.exit(f"No result for {city['name']} — see the messages above.")

    bounds = summary.get("bounds")
    if not bounds:
        meta = pipeline.city_dir(city["id"], a.source) / "density_meta.json"
        bounds = json.loads(meta.read_text())["bounds"] if meta.exists() else None
    registry.set_result(reg, city["id"], a.source, registry.result_from_summary(summary, bounds))
    registry.save(reg)

    real = city.get("real_area")
    print(f"\n{city['name']} — {SOURCES[a.source]['label']} (mask: {summary['mask']}, "
          f"{summary['radius_m'] // 1000} km, {summary['grid_m']} m grid)")
    print(f"  Urban extent : {summary['urban_extent_km2']:,} km²")
    print(f"  Population   : {summary['total_population']:,}")
    for t in THRESHOLDS:
        c = summary[f"core_{t}pct"]
        delta = f"   Δ {100 * (c['area_km2'] - real) / real:+.0f}% vs real {real:,.0f} km²" if real else ""
        print(f"  Core {t}%     : {c['area_km2']:8,.1f} km²  {c['population']:>12,} ppl  "
              f"{c['density_km2']:>8,.0f} /km²{delta}")
    print(f"\nSaved to urban_cities/{city['id']}/{a.source}/ — open the dashboard: python launch.py")


if __name__ == "__main__":
    main()
