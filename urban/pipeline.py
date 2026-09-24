"""Clip a population raster by the urban mask, compute density-percentile cores,
and render the map layers the dashboard shows.

Per city and source the outputs land in urban_cities/<id>/<source>/:
  metric.tif             population per pixel in UTM (git-ignored, regenerable)
  summary.json           extent, total population, Core 70/80/90/95 %
  density_overlay.png    heatmap (WGS84 RGBA)      ┐
  density_meta.json      {"bounds": [S, W, N, E]}  │ map layers
  cores.geojson          one polygon per threshold ┘
and at city level urban_cities/<id>/boundary_processed.geojson (cleaned OSM mask).
"""
import datetime
import json
import math
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pyproj
import rasterio
from rasterio.crs import CRS
from rasterio.features import shapes as rio_shapes
from rasterio.mask import mask as rasterio_mask
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely import contains_xy
from shapely.geometry import shape, mapping, MultiPolygon
from shapely.ops import unary_union, transform as shp_transform

from . import boundary, sources
from .config import (CITIES_DIR, THRESHOLDS, SOURCES, RADIUS_M, HRSL_GRID_M,
                     DEFAULT_MASK)

warnings.filterwarnings("ignore")
WGS84 = CRS.from_epsg(4326)
NODATA = -9999.0


def auto_utm(lat, lon):
    zone = min(60, int((lon + 180) / 6) + 1)
    return CRS.from_epsg(32600 + zone if lat >= 0 else 32700 + zone)


def city_dir(city_id, source=None):
    d = CITIES_DIR / city_id
    return d / source if source else d


# ── Raster → metric grid ─────────────────────────────────────────────────────
def clip_raster(tif_path, polygon, out_path):
    with rasterio.open(tif_path) as src:
        img, transform = rasterio_mask(src, [polygon], crop=True, nodata=0.0)
        meta = src.meta.copy()
        meta.update(driver="GTiff", height=img.shape[1], width=img.shape[2],
                    transform=transform, nodata=0.0)
    with rasterio.open(out_path, "w", **meta) as dst:
        dst.write(img)


def warp_to_utm(in_path, out_path, utm):
    with rasterio.open(in_path) as src:
        t, w, h = calculate_default_transform(src.crs, utm, src.width, src.height, *src.bounds)
        meta = src.meta.copy()
        meta.update(crs=utm, transform=t, width=w, height=h, dtype="float32", nodata=0.0)
        with rasterio.open(out_path, "w", **meta) as dst:
            reproject(rasterio.band(src, 1), rasterio.band(dst, 1),
                      src_transform=src.transform, src_crs=src.crs,
                      dst_transform=t, dst_crs=utm, resampling=Resampling.bilinear)


def metric_from_raster(tif_path, polygon, utm, out_metric):
    """Clip a WGS84 population raster to the mask and warp it to UTM (≈ native pixel size)."""
    with tempfile.TemporaryDirectory() as td:
        clipped = Path(td) / "clipped.tif"
        clip_raster(tif_path, polygon, clipped)
        warp_to_utm(clipped, out_metric, utm)


def metric_from_hrsl(csv_path, polygon, utm, out_metric, grid_m=HRSL_GRID_M):
    """Sum HRSL population points inside the mask onto a regular UTM grid (mass-conserving).

    HRSL GeoTIFF/CSV stores each ~250 m neighbourhood's people in ONE ~30 m cell, so it
    must be aggregated before a per-pixel density method makes sense.
    """
    lon, lat, pop = sources.hrsl_points(csv_path)
    minx, miny, maxx, maxy = polygon.bounds
    m = (lon >= minx) & (lon <= maxx) & (lat >= miny) & (lat <= maxy)
    if not m.any():
        return False
    inside = contains_xy(polygon, lon[m], lat[m])
    cx, cy, cp = lon[m][inside], lat[m][inside], pop[m][inside]
    if len(cp) == 0:
        return False
    fwd = pyproj.Transformer.from_crs("EPSG:4326", utm, always_xy=True)
    ux, uy = fwd.transform(cx, cy)
    left, bottom, right, top = ux.min(), uy.min(), ux.max(), uy.max()
    w = int(math.ceil((right - left) / grid_m)) + 1
    h = int(math.ceil((top - bottom) / grid_m)) + 1
    col = np.clip(((ux - left) / grid_m).astype(int), 0, w - 1)
    row = np.clip(((top - uy) / grid_m).astype(int), 0, h - 1)
    grid = np.zeros((h, w), dtype=np.float64)
    np.add.at(grid, (row, col), cp)
    meta = dict(driver="GTiff", height=h, width=w, count=1, dtype="float32", crs=utm,
                transform=from_origin(left, top, grid_m, grid_m), nodata=0.0)
    with rasterio.open(out_metric, "w", **meta) as dst:
        dst.write(grid.astype("float32"), 1)
    return True


# ── Cores ────────────────────────────────────────────────────────────────────
def cores_from_metric(metric_path):
    with rasterio.open(metric_path) as src:
        data = src.read(1).astype(np.float64)
        pixel_area_m2 = abs(src.transform.a * src.transform.e)
    data[data <= 0] = np.nan
    if np.isnan(data).all():
        return None
    dens = data / (pixel_area_m2 / 1e6)
    cores = {}
    for t in THRESHOLDS:
        thr = np.nanpercentile(dens, 100 - t)
        sel = dens >= thr
        pop = float(np.nansum(data[sel]))
        area = float(sel.sum()) * pixel_area_m2 / 1e6
        cores[t] = {"area_km2": round(area, 1), "population": int(pop),
                    "density_km2": round(pop / area if area else 0, 1), "threshold_pct": t}
    return {"total_population": int(np.nansum(data)), "pixel_m": round(pixel_area_m2 ** 0.5, 1),
            "cores": cores}


# ── Main entry point ─────────────────────────────────────────────────────────
def compute(city, source, force=False, mask=DEFAULT_MASK, radius_m=None, refresh_boundary=False):
    """Run the pipeline for one registry entry and one source. Returns the summary dict.

    `city` needs id, name, lat, lon and (for worldpop/hrsl) iso3.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; choose from {list(SOURCES)}")
    cid, lat, lon = city["id"], float(city["lat"]), float(city["lon"])
    radius_m = int(radius_m or RADIUS_M)
    out = city_dir(cid, source)
    summary_path = out / "summary.json"
    if summary_path.exists() and not force:
        print(f"  {cid}/{source}: already computed (use --force to redo)")
        return json.loads(summary_path.read_text())
    out.mkdir(parents=True, exist_ok=True)

    polygon, mask_used = boundary.mask_polygon(cid, lat, lon, mask=mask, radius_m=radius_m,
                                               refresh=refresh_boundary)
    if polygon is None:
        print(f"  {cid}: no urban mask (no OSM landuse and no reference polygon)")
        return None
    utm = auto_utm(lat, lon)
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", utm, always_xy=True).transform
    extent_km2 = round(shp_transform(to_utm, polygon).area / 1e6, 1)

    metric = out / "metric.tif"
    if source == "worldpop":
        tif = sources.worldpop_tif(city["iso3"])
        metric_from_raster(tif, polygon, utm, metric)
    elif source == "ghs":
        with tempfile.TemporaryDirectory() as td:
            win = sources.ghs_window(polygon.bounds, Path(td) / "ghs_window.tif")
            metric_from_raster(win, polygon, utm, metric)
    elif source == "hrsl":
        csv = sources.hrsl_csv(city["iso3"])
        if not metric_from_hrsl(csv, polygon, utm, metric):
            print(f"  {cid}: no HRSL points inside the mask")
            return None

    res = cores_from_metric(metric)
    if res is None:
        print(f"  {cid}/{source}: no populated pixels inside the mask")
        return None

    summary = {
        "city": city.get("name", cid), "id": cid, "source": source,
        "source_title": SOURCES[source]["title"],
        "mask": mask_used, "radius_m": radius_m, "utm_epsg": utm.to_epsg(),
        "grid_m": res["pixel_m"],
        "urban_extent_km2": extent_km2,
        "total_population": res["total_population"],
        **{f"core_{t}pct": res["cores"][t] for t in THRESHOLDS},
        "computed_at": datetime.date.today().isoformat(),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    bounds = make_assets(cid, source, lat, lon)
    summary["bounds"] = bounds
    return summary


# ── Map assets ───────────────────────────────────────────────────────────────
_MAGMA = None


def colorize(d_arr, sigma=3):
    """density (ppl/km²) → RGBA (magma colormap, log scale, gaussian glow)."""
    global _MAGMA
    if _MAGMA is None:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import colormaps
        _MAGMA = colormaps["magma"]
    from scipy.ndimage import gaussian_filter
    raw = np.maximum(np.where(np.isnan(d_arr), 0.0, d_arr), 0.0)
    smoothed = gaussian_filter(raw, sigma=sigma)
    log_arr = np.log1p(smoothed)
    valid = smoothed > 15
    vmax = float(np.percentile(log_arr[valid], 99)) if valid.any() else (float(log_arr.max()) or 1.0)
    normalized = np.clip(log_arr / max(vmax, 1e-6), 0.0, 1.0)
    normalized[~valid] = 0.0
    rgba = _MAGMA(normalized)
    alpha = normalized ** 0.65
    alpha[~valid] = 0.0
    rgba[:, :, 3] = alpha
    return (rgba * 255).astype(np.uint8)


def make_assets(city_id, source, lat=None, lon=None):
    """Render density_overlay.png, density_meta.json, cores.geojson from metric.tif.
    Returns the overlay bounds [S, W, N, E]."""
    from PIL import Image

    out = city_dir(city_id, source)
    metric_path = out / "metric.tif"
    if not metric_path.exists():
        raise FileNotFoundError(f"{metric_path} — compute the city first")

    with rasterio.open(metric_path) as src:
        data = src.read(1).astype(np.float64)
        pixel_area_m2 = abs(src.transform.a * src.transform.e)
        src_crs, src_transform = src.crs, src.transform
        src_w, src_h, src_bounds = src.width, src.height, src.bounds

    dens_utm = data.copy()
    dens_utm[dens_utm <= 0] = np.nan
    dens_utm /= (pixel_area_m2 / 1e6)

    # density → WGS84 for the image overlay
    dst_t, dst_w, dst_h = calculate_default_transform(src_crs, WGS84, src_w, src_h, *src_bounds)
    dens_src = np.where(np.isnan(dens_utm), NODATA, dens_utm).astype(np.float32)
    dens_wgs = np.full((dst_h, dst_w), NODATA, np.float32)
    reproject(source=dens_src, destination=dens_wgs,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_t, dst_crs=WGS84, resampling=Resampling.bilinear,
              src_nodata=NODATA, dst_nodata=NODATA)
    dens_wgs = dens_wgs.astype(np.float64)
    dens_wgs[dens_wgs == NODATA] = np.nan
    west, north = dst_t.c, dst_t.f
    bounds = [north + dst_h * dst_t.e, west, north, west + dst_w * dst_t.a]  # S, W, N, E

    Image.fromarray(colorize(dens_wgs), "RGBA").save(str(out / "density_overlay.png"))
    (out / "density_meta.json").write_text(json.dumps({"bounds": bounds}))

    # core polygons: vectorise in UTM, clean, reproject
    to_wgs = pyproj.Transformer.from_crs(src_crs, WGS84, always_xy=True).transform
    pixel_m = pixel_area_m2 ** 0.5
    features = []
    for t in THRESHOLDS:
        thr = np.nanpercentile(dens_utm, 100 - t)
        mask = (dens_utm >= thr).astype(np.uint8)
        polys = [shape(g) for g, v in rio_shapes(mask, transform=src_transform) if v == 1]
        polys = [p for p in polys if p.area >= pixel_m ** 2 * 25]
        if not polys:
            continue
        merged = unary_union(polys).buffer(pixel_m * 4).buffer(-pixel_m * 3)
        if merged.geom_type == "MultiPolygon":
            geoms = list(merged.geoms)
            max_a = max(g.area for g in geoms)
            geoms = [g for g in geoms if g.area >= max_a * 0.02]
            merged = MultiPolygon(geoms) if len(geoms) > 1 else geoms[0]
        merged = merged.simplify(pixel_m * 2)
        features.append({"type": "Feature", "properties": {"threshold": t},
                         "geometry": mapping(shp_transform(to_wgs, merged))})
    (out / "cores.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))

    if lat is not None and lon is not None:
        boundary.write_processed_boundary(city_id, lat, lon)
    return bounds
