"""cities_all.json — the list of cities and their per-source numbers.

Schema (version 2):
{
  "version": 2,
  "sources": {"worldpop": {"label": ..., "title": ...}, ...},
  "cities": [
    {"id": "cairo", "name": "Cairo", "country": "Egypt", "iso3": "EGY",
     "lat": 30.06, "lon": 31.25,
     "real_area": 996.2, "has_real_polygon": true, "bounds": [S, W, N, E],
     "sources": {
        "worldpop": {"urb_area": 929.8, "total_pop": 13949054,
                     "area70": ..., "pop70": ..., ..., "area95": ..., "pop95": ...,
                     "bounds": [S, W, N, E], "mask": "osm", "computed_at": "2026-05-19"}
     }}
  ]
}
"""
import json
import re
import unicodedata

from .config import REGISTRY_PATH, SOURCES, THRESHOLDS

VERSION = 2

_CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'қ': 'k', 'ң': 'n', 'ұ': 'u', 'ғ': 'g', 'ә': 'a', 'і': 'i',
}


def slugify(text):
    """URL-safe lowercase id from any name (Cyrillic is transliterated)."""
    t = ''.join(_CYR.get(c, c) for c in text.lower())
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn' and ord(c) < 128)
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')


def empty():
    return {"version": VERSION, "sources": {}, "cities": []}


def load(path=REGISTRY_PATH):
    if not path.exists():
        return empty()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        raise SystemExit(f"{path} is in the old flat format; this code expects version {VERSION}.")
    return data


def save(reg, path=REGISTRY_PATH):
    reg["version"] = VERSION
    reg["sources"] = {k: {"label": v["label"], "title": v["title"]} for k, v in SOURCES.items()}
    reg["cities"].sort(key=lambda c: c["name"].lower())
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def find(reg, city_id):
    for c in reg["cities"]:
        if c["id"] == city_id:
            return c
    return None


def unique_id(reg, base):
    cid, n = base, 2
    while find(reg, cid):
        cid = f"{base}_{n}"
        n += 1
    return cid


def upsert(reg, entry):
    """Merge `entry` into the registry by id; existing per-source results are kept."""
    cur = find(reg, entry["id"])
    if cur is None:
        cur = {"id": entry["id"], "sources": {}}
        reg["cities"].append(cur)
    for k, v in entry.items():
        if k == "sources":
            cur.setdefault("sources", {}).update(v)
        elif v is not None or k not in cur:
            cur[k] = v
    cur.setdefault("sources", {})
    return cur


def result_from_summary(summary, bounds=None):
    """Flatten a summary.json into the fields the dashboard table uses."""
    r = {
        "urb_area": summary.get("urban_extent_km2"),
        "total_pop": summary.get("total_population"),
    }
    for t in THRESHOLDS:
        core = summary.get(f"core_{t}pct") or {}
        r[f"area{t}"] = core.get("area_km2")
        r[f"pop{t}"] = core.get("population")
    for k in ("mask", "computed_at", "radius_m", "grid_m"):
        if summary.get(k) is not None:
            r[k] = summary[k]
    if bounds:
        r["bounds"] = bounds
    return r


def set_result(reg, city_id, source, result):
    city = find(reg, city_id)
    if city is None:
        raise KeyError(city_id)
    city.setdefault("sources", {})[source] = result
    if not city.get("bounds") and result.get("bounds"):
        city["bounds"] = result["bounds"]
    return city
