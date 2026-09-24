"""Paths, constants and the population-source catalogue."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Raw population data lives here (git-ignored). Point URBAN_DENSITY_DOWNLOADS at an
# existing folder to reuse rasters you already have.
DOWNLOADS = Path(os.environ.get("URBAN_DENSITY_DOWNLOADS", BASE / "downloads")).expanduser()

CITIES_DIR    = BASE / "urban_cities"       # per-city results and map layers
BOUNDARY_DIR  = BASE / "urban_boundaries"   # cached OSM landuse masks
REGISTRY_PATH = BASE / "cities_all.json"    # city list + numbers the dashboard shows

# ── Method constants (do not change if you want numbers comparable to the shipped set)
RADIUS_M   = 25_000     # OSM landuse query radius around the city centre
BUFFER_DEG = 0.003      # ~300 m gap-filling buffer around the landuse union
THRESHOLDS = [70, 80, 90, 95]
URBAN_TAGS = {"landuse": ["residential", "commercial", "industrial", "retail", "mixed",
                          "construction", "garages", "brownfield", "allotments"]}
HRSL_GRID_M = 250       # HRSL points are summed onto this UTM grid (the data's own sampling)

MASKS = ("osm", "osm+real")   # osm+real = landuse union ∪ reference polygon (when present)
DEFAULT_MASK = "osm"

SOURCES = {
    "worldpop": {
        "label": "WorldPop 2020",
        "title": "WorldPop Constrained 2020 (UN-adjusted), 100 m",
        "url": "https://www.worldpop.org/geodata/listing?id=78",
        "note": "Per-country GeoTIFF, auto-downloaded. Validated baseline of this tool.",
    },
    "ghs": {
        "label": "GHS-POP 2025",
        "title": "GHS-POP R2023A, epoch 2025, 3 arc-seconds (~93 m)",
        "url": "https://human-settlement.emergency.copernicus.eu/download.php?ds=pop",
        "note": "Global 10°×10° tiles, auto-downloaded. Smoother than WorldPop → larger cores.",
    },
    "hrsl": {
        "label": "HRSL 2020",
        "title": "Meta / CIESIN High Resolution Settlement Layer, general population 2020, summed to 250 m",
        "url": "https://data.humdata.org/organization/meta",
        "note": "Per-country CSV point lattice from HDX (hundreds of MB), auto-downloaded. "
                "Country totals run high in some countries — read cores as shape, not head-count.",
    },
}
DEFAULT_SOURCE = "worldpop"
