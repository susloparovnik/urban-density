"""Minimal Overpass client for the landuse mask — no osmnx.

Why not osmnx: its HTTP layer retries a 429/504 forever and, when a backend accepts
the connection but never answers, its request can hang for hours. Here every attempt
has a wall-clock cap, mirrors are tried in order, and the JSON (`out geom`) is turned
into polygons directly with shapely.
"""
import json
import math
import time
import urllib.parse
import urllib.request

from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge, polygonize, unary_union

UA = {"User-Agent": "urban-density-pipeline/2.0", "Accept-Encoding": "identity"}

# Public endpoints with full-planet data. overpass-api.de round-robins between the two
# backends below and one of them is regularly broken (accepts TCP, never answers), so
# they are listed by their own names. overpass.openstreetmap.fr is whitelist-only and
# overpass.osm.ch is Switzerland-only, so they are not listed.
MIRRORS = [
    "https://lambert.openstreetmap.de/api/interpreter",
    "https://gall.openstreetmap.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
QUERY_TIMEOUT_S = 180      # server-side [timeout:]
ATTEMPT_CAP_S = 240        # wall-clock cap per HTTP attempt (connect + stream)
RETRIES_PER_MIRROR = 2     # on 429 / 504 (server busy)
BUSY_PAUSE_S = 30


class OverpassError(RuntimeError):
    pass


def bbox_around(lat, lon, dist_m):
    """(south, west, north, east) of a square of half-side `dist_m` around a point."""
    dlat = dist_m / 111_320.0
    dlon = dist_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def landuse_query(bbox, values, timeout_s=QUERY_TIMEOUT_S):
    s, w, n, e = bbox
    regex = "^(" + "|".join(values) + ")$"
    return (f'[out:json][timeout:{timeout_s}];'
            f'(nwr["landuse"~"{regex}"]({s:.6f},{w:.6f},{n:.6f},{e:.6f}););out geom;')


def _post(url, query, cap_s, log):
    """POST the query and stream the answer under a wall-clock cap. Returns (status, body)."""
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=data, headers=UA)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            chunks = []
            while True:
                if time.time() - t0 > cap_s:
                    raise OverpassError(f"no complete answer within {cap_s} s")
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
            return r.status, b"".join(chunks)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:2000]


def fetch(query, mirrors=None, log=print):
    """Run a query against the mirrors in order; returns the parsed JSON."""
    last = None
    for url in (mirrors or MIRRORS):
        host = urllib.parse.urlsplit(url).hostname
        for attempt in range(1, RETRIES_PER_MIRROR + 2):
            try:
                status, body = _post(url, query, ATTEMPT_CAP_S, log)
            except Exception as e:  # noqa: BLE001 — timeout, refused, DNS, cap exceeded
                last = f"{host}: {type(e).__name__}: {str(e)[:80]}"
                log(f"  {last} — next mirror")
                break
            if status == 200:
                try:
                    d = json.loads(body)
                except ValueError:
                    last = f"{host}: not JSON"
                    log(f"  {last} — next mirror")
                    break
                if "remark" in d and "elements" not in d:
                    last = f"{host}: {d['remark'][:120]}"
                    log(f"  {last}")
                    break
                return d
            if status in (429, 504) and attempt <= RETRIES_PER_MIRROR:
                log(f"  {host}: HTTP {status} (busy), retrying in {BUSY_PAUSE_S} s ({attempt}/{RETRIES_PER_MIRROR})")
                time.sleep(BUSY_PAUSE_S)
                continue
            last = f"{host}: HTTP {status}"
            log(f"  {last} — next mirror")
            break
    raise OverpassError(f"all Overpass mirrors failed ({last})")


def _ring(coords):
    pts = [(c["lon"], c["lat"]) for c in coords if c]
    return pts if len(pts) >= 2 else None


def polygons_from_elements(elements):
    """Closed landuse ways → polygons; multipolygon relations → outer minus inner."""
    polys = []
    for el in elements:
        t = el.get("type")
        if t == "way":
            pts = _ring(el.get("geometry") or [])
            if pts and len(pts) >= 4 and pts[0] == pts[-1]:
                p = Polygon(pts)
                if not p.is_valid:
                    p = p.buffer(0)
                if not p.is_empty:
                    polys.append(p)
        elif t == "relation":
            outer, inner = [], []
            for m in el.get("members") or []:
                if m.get("type") != "way":
                    continue
                pts = _ring(m.get("geometry") or [])
                if pts:
                    (inner if m.get("role") == "inner" else outer).append(LineString(pts))
            if not outer:
                continue
            shell = unary_union(list(polygonize(linemerge(outer)))) if outer else None
            if shell is None or shell.is_empty:
                continue
            if inner:
                holes = unary_union(list(polygonize(linemerge(inner))))
                if not holes.is_empty:
                    shell = shell.difference(holes)
            if not shell.is_valid:
                shell = shell.buffer(0)
            if not shell.is_empty:
                polys.append(shell)
    return polys


def landuse_polygons(lat, lon, dist_m, values, log=print):
    """All landuse polygons with the given values within `dist_m` of the point."""
    d = fetch(landuse_query(bbox_around(lat, lon, dist_m), values), log=log)
    return polygons_from_elements(d.get("elements", []))
