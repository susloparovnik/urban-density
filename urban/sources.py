"""Population data acquisition for the three supported sources.

  worldpop  WorldPop Constrained 2020 UN-adj, 100 m   — one GeoTIFF per country
  ghs       GHS-POP R2023A epoch 2025, 3 arc-seconds  — global 10°×10° tiles
  hrsl      Meta/CIESIN HRSL general 2020             — one CSV point lattice per country (HDX)

Everything is downloaded on demand into DOWNLOADS (see config.py) and reused afterwards.
"""
import io
import json
import math
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from .config import DOWNLOADS

UA = {"User-Agent": "urban-density-pipeline/2.0 (+https://github.com)"}

# ── Country codes ────────────────────────────────────────────────────────────
ISO2_TO_ISO3 = {
    'AF': 'AFG', 'AX': 'ALA', 'AL': 'ALB', 'DZ': 'DZA', 'AS': 'ASM', 'AD': 'AND', 'AO': 'AGO',
    'AI': 'AIA', 'AQ': 'ATA', 'AG': 'ATG', 'AR': 'ARG', 'AM': 'ARM', 'AW': 'ABW', 'AU': 'AUS',
    'AT': 'AUT', 'AZ': 'AZE', 'BS': 'BHS', 'BH': 'BHR', 'BD': 'BGD', 'BB': 'BRB', 'BY': 'BLR',
    'BE': 'BEL', 'BZ': 'BLZ', 'BJ': 'BEN', 'BM': 'BMU', 'BT': 'BTN', 'BO': 'BOL', 'BQ': 'BES',
    'BA': 'BIH', 'BW': 'BWA', 'BV': 'BVT', 'BR': 'BRA', 'IO': 'IOT', 'BN': 'BRN', 'BG': 'BGR',
    'BF': 'BFA', 'BI': 'BDI', 'CV': 'CPV', 'KH': 'KHM', 'CM': 'CMR', 'CA': 'CAN', 'KY': 'CYM',
    'CF': 'CAF', 'TD': 'TCD', 'CL': 'CHL', 'CN': 'CHN', 'CX': 'CXR', 'CC': 'CCK', 'CO': 'COL',
    'KM': 'COM', 'CG': 'COG', 'CD': 'COD', 'CK': 'COK', 'CR': 'CRI', 'CI': 'CIV', 'HR': 'HRV',
    'CU': 'CUB', 'CW': 'CUW', 'CY': 'CYP', 'CZ': 'CZE', 'DK': 'DNK', 'DJ': 'DJI', 'DM': 'DMA',
    'DO': 'DOM', 'EC': 'ECU', 'EG': 'EGY', 'SV': 'SLV', 'GQ': 'GNQ', 'ER': 'ERI', 'EE': 'EST',
    'SZ': 'SWZ', 'ET': 'ETH', 'FK': 'FLK', 'FO': 'FRO', 'FJ': 'FJI', 'FI': 'FIN', 'FR': 'FRA',
    'GF': 'GUF', 'PF': 'PYF', 'TF': 'ATF', 'GA': 'GAB', 'GM': 'GMB', 'GE': 'GEO', 'DE': 'DEU',
    'GH': 'GHA', 'GI': 'GIB', 'GR': 'GRC', 'GL': 'GRL', 'GD': 'GRD', 'GP': 'GLP', 'GU': 'GUM',
    'GT': 'GTM', 'GG': 'GGY', 'GN': 'GIN', 'GW': 'GNB', 'GY': 'GUY', 'HT': 'HTI', 'HM': 'HMD',
    'VA': 'VAT', 'HN': 'HND', 'HK': 'HKG', 'HU': 'HUN', 'IS': 'ISL', 'IN': 'IND', 'ID': 'IDN',
    'IR': 'IRN', 'IQ': 'IRQ', 'IE': 'IRL', 'IM': 'IMN', 'IL': 'ISR', 'IT': 'ITA', 'JM': 'JAM',
    'JP': 'JPN', 'JE': 'JEY', 'JO': 'JOR', 'KZ': 'KAZ', 'KE': 'KEN', 'KI': 'KIR', 'KP': 'PRK',
    'KR': 'KOR', 'KW': 'KWT', 'KG': 'KGZ', 'LA': 'LAO', 'LV': 'LVA', 'LB': 'LBN', 'LS': 'LSO',
    'LR': 'LBR', 'LY': 'LBY', 'LI': 'LIE', 'LT': 'LTU', 'LU': 'LUX', 'MO': 'MAC', 'MG': 'MDG',
    'MW': 'MWI', 'MY': 'MYS', 'MV': 'MDV', 'ML': 'MLI', 'MT': 'MLT', 'MH': 'MHL', 'MQ': 'MTQ',
    'MR': 'MRT', 'MU': 'MUS', 'YT': 'MYT', 'MX': 'MEX', 'FM': 'FSM', 'MD': 'MDA', 'MC': 'MCO',
    'MN': 'MNG', 'ME': 'MNE', 'MS': 'MSR', 'MA': 'MAR', 'MZ': 'MOZ', 'MM': 'MMR', 'NA': 'NAM',
    'NR': 'NRU', 'NP': 'NPL', 'NL': 'NLD', 'NC': 'NCL', 'NZ': 'NZL', 'NI': 'NIC', 'NE': 'NER',
    'NG': 'NGA', 'NU': 'NIU', 'NF': 'NFK', 'MK': 'MKD', 'MP': 'MNP', 'NO': 'NOR', 'OM': 'OMN',
    'PK': 'PAK', 'PW': 'PLW', 'PS': 'PSE', 'PA': 'PAN', 'PG': 'PNG', 'PY': 'PRY', 'PE': 'PER',
    'PH': 'PHL', 'PN': 'PCN', 'PL': 'POL', 'PT': 'PRT', 'PR': 'PRI', 'QA': 'QAT', 'RE': 'REU',
    'RO': 'ROU', 'RU': 'RUS', 'RW': 'RWA', 'BL': 'BLM', 'SH': 'SHN', 'KN': 'KNA', 'LC': 'LCA',
    'MF': 'MAF', 'PM': 'SPM', 'VC': 'VCT', 'WS': 'WSM', 'SM': 'SMR', 'ST': 'STP', 'SA': 'SAU',
    'SN': 'SEN', 'RS': 'SRB', 'SC': 'SYC', 'SL': 'SLE', 'SG': 'SGP', 'SX': 'SXM', 'SK': 'SVK',
    'SI': 'SVN', 'SB': 'SLB', 'SO': 'SOM', 'ZA': 'ZAF', 'GS': 'SGS', 'SS': 'SSD', 'ES': 'ESP',
    'LK': 'LKA', 'SD': 'SDN', 'SR': 'SUR', 'SJ': 'SJM', 'SE': 'SWE', 'CH': 'CHE', 'SY': 'SYR',
    'TW': 'TWN', 'TJ': 'TJK', 'TZ': 'TZA', 'TH': 'THA', 'TL': 'TLS', 'TG': 'TGO', 'TK': 'TKL',
    'TO': 'TON', 'TT': 'TTO', 'TN': 'TUN', 'TR': 'TUR', 'TM': 'TKM', 'TC': 'TCA', 'TV': 'TUV',
    'UG': 'UGA', 'UA': 'UKR', 'AE': 'ARE', 'GB': 'GBR', 'US': 'USA', 'UM': 'UMI', 'UY': 'URY',
    'UZ': 'UZB', 'VU': 'VUT', 'VE': 'VEN', 'VN': 'VNM', 'VG': 'VGB', 'VI': 'VIR', 'WF': 'WLF',
    'EH': 'ESH', 'YE': 'YEM', 'ZM': 'ZMB', 'ZW': 'ZWE', 'XK': 'XKX',
}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout)


def _download(url, dest, timeout=600):
    """Stream a URL to `dest` (via a .part file); prints progress."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with _get(url, timeout=timeout) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                sys.stdout.write(f"\r    {done / 1e6:7.1f} / {total / 1e6:.1f} MB")
            else:
                sys.stdout.write(f"\r    {done / 1e6:7.1f} MB")
            sys.stdout.flush()
    sys.stdout.write("\n")
    part.rename(dest)
    return dest


def country_lookup(lat, lon):
    """(country name, ISO3) via OSM Nominatim reverse geocoding."""
    url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&zoom=3&accept-language=en"
    with _get(url, timeout=30) as r:
        d = json.loads(r.read().decode())
    addr = d.get("address", {})
    iso2 = (addr.get("country_code") or "").upper()
    iso3 = ISO2_TO_ISO3.get(iso2)
    if not iso3:
        raise SystemExit(f"Could not resolve the country for ({lat}, {lon}) — pass --iso3 explicitly.")
    return addr.get("country", iso3), iso3


def _first_existing(paths):
    for p in paths:
        if p.exists():
            return p
    return None


# ── WorldPop ─────────────────────────────────────────────────────────────────
WP_URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/"
          "{variant}/{ISO}/{iso}_ppp_2020_UNadj_constrained.tif")


def worldpop_tif(iso3):
    """Path to the country's WorldPop Constrained 2020 GeoTIFF (downloaded if missing)."""
    iso = iso3.lower()
    found = _first_existing([DOWNLOADS / "worldpop" / f"{iso}_constrained.tif",
                             DOWNLOADS / f"{iso}_constrained.tif"])
    if found:
        return found
    dest = DOWNLOADS / "worldpop" / f"{iso}_constrained.tif"
    for variant in ("BSGM", "maxar_v1"):
        url = WP_URL.format(variant=variant, ISO=iso3.upper(), iso=iso)
        try:
            with _get(url, timeout=30) as r:
                if r.status != 200:
                    continue
        except Exception:  # noqa: BLE001 — 404 on one variant is expected
            continue
        print(f"  Downloading WorldPop {iso3.upper()} ({variant}) ...")
        return _download(url, dest)
    raise SystemExit(f"WorldPop Constrained 2020 has no raster for {iso3.upper()}.")


# ── GHS-POP ──────────────────────────────────────────────────────────────────
GHS_TILE = "GHS_POP_E2025_GLOBE_R2023A_4326_3ss_V1_0_{rc}"
GHS_URL = ("https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/"
           "GHS_POP_E2025_GLOBE_R2023A_4326_3ss/V1-0/tiles/" + GHS_TILE + ".zip")


def ghs_tile_rc(lat, lon):
    return f"R{math.ceil((89.10 - lat) / 10)}_C{math.floor((lon + 180.01) / 10) + 1}"


def ghs_tile(rc):
    name = GHS_TILE.format(rc=rc) + ".tif"
    found = _first_existing([DOWNLOADS / "ghs_pop" / name])
    if found:
        return found
    print(f"  Downloading GHS-POP tile {rc} ...")
    with _get(GHS_URL.format(rc=rc), timeout=600) as r:
        buf = io.BytesIO(r.read())
    with zipfile.ZipFile(buf) as z:
        member = next((m for m in z.namelist() if m.endswith(".tif")), None)
        if member is None:
            raise SystemExit(f"GHS tile {rc}: no .tif inside the archive")
        (DOWNLOADS / "ghs_pop").mkdir(parents=True, exist_ok=True)
        with z.open(member) as src, open(DOWNLOADS / "ghs_pop" / name, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return DOWNLOADS / "ghs_pop" / name


def ghs_window(bounds, out_path, margin=0.05):
    """Mosaic the GHS tiles covering `bounds` (W, S, E, N) into one WGS84 GeoTIFF."""
    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.merge import merge as rio_merge

    w, s, e, n = bounds
    rcs = sorted({ghs_tile_rc(la, lo) for la in (s - margin, n + margin) for lo in (w - margin, e + margin)})
    datasets = [rasterio.open(ghs_tile(rc)) for rc in rcs]
    try:
        mosaic, transform = rio_merge(datasets, bounds=(w - margin, s - margin, e + margin, n + margin))
    finally:
        for d in datasets:
            d.close()
    arr = mosaic[0].astype("float32")
    arr[arr < 0] = 0.0
    meta = {"driver": "GTiff", "height": arr.shape[0], "width": arr.shape[1], "count": 1,
            "dtype": "float32", "crs": CRS.from_epsg(4326), "transform": transform, "nodata": 0.0}
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(arr, 1)
    return out_path


# ── HRSL (Meta / CIESIN, via HDX) ────────────────────────────────────────────
HDX_SHOW = "https://data.humdata.org/api/3/action/package_show?id=highresolutionpopulationdensitymaps-{iso}"
HDX_SEARCH = ("https://data.humdata.org/api/3/action/package_search?"
              "q=title:%22High%20Resolution%20Population%20Density%22&fq=groups:{iso}&rows=5")


def _hdx_resource(iso3):
    iso = iso3.lower()
    pkgs = []
    try:
        with _get(HDX_SHOW.format(iso=iso), timeout=60) as r:
            d = json.loads(r.read().decode())
        if d.get("success"):
            pkgs.append(d["result"])
    except Exception:  # noqa: BLE001 — fall through to search
        pass
    if not pkgs:
        with _get(HDX_SEARCH.format(iso=iso), timeout=60) as r:
            d = json.loads(r.read().decode())
        pkgs = d.get("result", {}).get("results", [])
    for pkg in pkgs:
        res = pkg.get("resources", [])
        for pattern in (f"{iso}_general_2020_csv", "general_2020_csv", f"population_{iso}", "population_"):
            for r in res:
                name = (r.get("name") or "").lower()
                if pattern in name and "csv" in name and name.endswith(".zip"):
                    return r
    raise SystemExit(f"No HRSL CSV found on HDX for {iso3.upper()} — check "
                     f"https://data.humdata.org/organization/meta?q={iso3.upper()}")


def hrsl_csv(iso3):
    """Path to the country's HRSL general-population CSV (downloaded & unzipped if missing)."""
    iso = iso3.lower()
    found = _first_existing([DOWNLOADS / "hrsl" / f"{iso}_general_2020.csv",
                             DOWNLOADS / f"{iso}_general_2020.csv"])
    if found:
        return found
    res = _hdx_resource(iso3)
    size = int(res.get("size") or 0)
    print(f"  Downloading HRSL {iso3.upper()} from HDX: {res['name']} ({size / 1e6:.0f} MB zipped) ...")
    zpath = _download(res["url"], DOWNLOADS / "hrsl" / res["name"])
    with zipfile.ZipFile(zpath) as z:
        member = next((m for m in z.namelist() if m.lower().endswith(".csv")), None)
        if member is None:
            raise SystemExit(f"{zpath}: no CSV inside")
        dest = DOWNLOADS / "hrsl" / f"{iso}_general_2020.csv"
        print(f"  Unzipping → {dest.name}")
        with z.open(member) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    zpath.unlink()
    return dest


_HRSL_CACHE = {}


def hrsl_points(csv_path):
    """(lon, lat, pop) float64 arrays for the whole country; cached per process."""
    key = str(csv_path)
    if key in _HRSL_CACHE:
        return _HRSL_CACHE[key]
    import pandas as pd
    header = pd.read_csv(csv_path, nrows=0).columns.tolist()
    low = [h.lower() for h in header]
    lon_col = next((h for h, l in zip(header, low) if l.startswith("lon")), header[0])
    lat_col = next((h for h, l in zip(header, low) if l.startswith("lat")), header[1])
    pop_col = next(h for h in header if h not in (lon_col, lat_col))
    print(f"  Loading {Path(csv_path).name} (columns {lon_col}, {lat_col}, {pop_col}) ...")
    df = pd.read_csv(csv_path, usecols=[lon_col, lat_col, pop_col], dtype="float64")
    arrs = (df[lon_col].to_numpy(), df[lat_col].to_numpy(), df[pop_col].to_numpy())
    print(f"  {len(df):,} points, country total {arrs[2].sum():,.0f}")
    _HRSL_CACHE.clear()
    _HRSL_CACHE[key] = arrs
    return arrs
