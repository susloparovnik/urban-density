"""Urban mask: OSM landuse union around the city centre (cached) and the optional
reference polygon ("real area") that can be unioned into it."""
import datetime
import json

from shapely.geometry import shape, mapping, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

from .config import BOUNDARY_DIR, CITIES_DIR, RADIUS_M, BUFFER_DEG, URBAN_TAGS

# Components of the raw mask farther than this from the centre are dropped from the
# *displayed* boundary only (degrees, ≈ 25 km). The clipping mask is not affected.
DISPLAY_RADIUS_DEG = 0.25


def cache_path(city_id):
    return BOUNDARY_DIR / f"{city_id}.geojson"


def osm_polygon(city_id, lat, lon, radius_m=RADIUS_M, refresh=False):
    """OSM landuse union (+ gap-filling buffer) within `radius_m` of the centre.

    Cached in urban_boundaries/<id>.geojson so re-runs (and other population
    sources) use the very same mask.
    """
    cache = cache_path(city_id)
    if cache.exists() and not refresh:
        return shape(json.loads(cache.read_text())["geometry"])

    from . import overpass
    print(f"  Fetching OSM landuse around ({lat:.4f}, {lon:.4f}), r={radius_m // 1000} km ...")
    try:
        polys = overpass.landuse_polygons(lat, lon, radius_m, URBAN_TAGS["landuse"])
    except overpass.OverpassError as e:
        print(f"  ERROR: {e}")
        return None
    if not polys:
        print("  No landuse polygons found in OSM here.")
        return None
    extent = unary_union(polys).buffer(BUFFER_DEG)
    BOUNDARY_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps({
        "geometry": mapping(extent),
        "radius_m": radius_m,
        "fetched": datetime.date.today().isoformat(),
        "source": "OSM landuse via Overpass",
    }))
    n = len(extent.geoms) if extent.geom_type == "MultiPolygon" else 1
    print(f"  {len(polys)} landuse polygons → {n} component(s)")
    return extent


def _rings_to_polygons(geom, center, max_dist_deg):
    """Reference polygons come as Polygon/MultiPolygon or as (Multi)LineString rings."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])
    polys = []
    if gtype in ("Polygon", "MultiPolygon"):
        g = shape(geom)
        if not g.is_valid:
            g = g.buffer(0)
        return [g] if not g.is_empty else []
    rings = [coords] if gtype == "LineString" else coords if gtype == "MultiLineString" else []
    for ring in rings:
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring)
            if not p.is_valid:
                p = p.buffer(0)
            if p.area > 0 and (center is None or p.centroid.distance(center) <= max_dist_deg):
                polys.append(p)
        except Exception:  # noqa: BLE001 — skip malformed rings
            pass
    return polys


def real_polygon(city_id, lat=None, lon=None, max_dist_deg=0.5):
    """Reference ("real area") polygon from urban_cities/<id>/real_boundary.geojson, or None."""
    p = CITIES_DIR / city_id / "real_boundary.geojson"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    geom = d.get("geometry", d)
    center = Point(lon, lat) if lat is not None and lon is not None else None
    polys = _rings_to_polygons(geom, center, max_dist_deg)
    return unary_union(polys) if polys else None


def mask_polygon(city_id, lat, lon, mask="osm", radius_m=RADIUS_M, refresh=False):
    """Return (polygon, mask_used). mask: 'osm' or 'osm+real'."""
    osm = osm_polygon(city_id, lat, lon, radius_m=radius_m, refresh=refresh)
    if mask == "osm+real":
        real = real_polygon(city_id, lat, lon)
        if osm is not None and real is not None:
            return osm.union(real), "osm+real"
        if real is not None:
            return real, "real"
    return osm, "osm"


def processed_boundary(city_id, lat, lon):
    """Cleaned version of the OSM mask for display: drop far/tiny fragments, close gaps."""
    cache = cache_path(city_id)
    if not cache.exists():
        return None
    raw = json.loads(cache.read_text())
    geom = shape(raw["geometry"])
    center = Point(lon, lat)

    if geom.geom_type == "MultiPolygon":
        geoms = list(geom.geoms)
        total = sum(g.area for g in geoms)
        geoms = [g for g in geoms if g.area >= total * 0.005]
        geoms = [g for g in geoms if g.centroid.distance(center) <= DISPLAY_RADIUS_DEG]
        if not geoms:
            geoms = [max(shape(raw["geometry"]).geoms, key=lambda g: g.area)]
        geom = unary_union(geoms)

    geom = geom.buffer(0.005).buffer(-0.004).simplify(0.001)
    if geom.geom_type == "MultiPolygon":
        geoms = list(geom.geoms)
        max_a = max(g.area for g in geoms)
        geoms = [g for g in geoms if g.area >= max_a * 0.01]
        geom = MultiPolygon(geoms) if len(geoms) > 1 else geoms[0]
    return {"type": "Feature", "geometry": mapping(geom), "properties": {}}


def write_processed_boundary(city_id, lat, lon):
    feat = processed_boundary(city_id, lat, lon)
    if feat is None:
        return None
    out = CITIES_DIR / city_id / "boundary_processed.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(feat))
    return out


def write_real_polygon(city_id, geometry, name=None, country=None, real_area=None):
    """Store a reference polygon (GeoJSON geometry dict) for a city; returns [S, W, N, E]."""
    d = CITIES_DIR / city_id
    d.mkdir(parents=True, exist_ok=True)
    feature = {"type": "Feature",
               "properties": {"city": name, "country": country, "real_area": real_area},
               "geometry": geometry}
    (d / "real_boundary.geojson").write_text(json.dumps(feature))
    polys = _rings_to_polygons(geometry, None, 0)
    bounds = None
    if polys:
        minx, miny, maxx, maxy = unary_union(polys).bounds
        bounds = [round(miny, 6), round(minx, 6), round(maxy, 6), round(maxx, 6)]
    (d / "real_meta.json").write_text(json.dumps({"real_area": real_area, "bounds": bounds}))
    return bounds
