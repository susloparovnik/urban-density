# Urban Density

Estimates the **built-up core of a city** — its area, population and density — from open data,
and compares it with a reference ("real") area where one exists.

Method in one line: **OSM landuse mask (25 km around the centre) × population raster →
per-pixel density → keep the densest pixels holding 70 / 80 / 90 / 95 % of the population.**

Ships with **412 cities** in 46 countries that have a reference area, computed with WorldPop.
Any city can be added, with a choice of three population sources:

| source | data | resolution | how it is obtained |
|---|---|---|---|
| `worldpop` | WorldPop Constrained 2020, UN-adjusted | 100 m | one GeoTIFF per country, auto-download |
| `ghs` | GHS-POP R2023A, epoch 2025 (JRC) | 3″ ≈ 93 m | global 10°×10° tiles, auto-download |
| `hrsl` | Meta / CIESIN High Resolution Settlement Layer, 2020 | points → 250 m grid | one CSV per country from HDX, auto-download |

The dashboard has a **Population source** switch; every source computed for a city is one click away.

---

## 1. Just look at the results

```bash
git clone <this repo> && cd urban-density
python3 launch.py            # serves the folder over http://localhost and opens the dashboard
```

No dependencies are needed for viewing. On macOS you can also double-click **`Urban Density.app`**
(native window, needs `pip install pywebview`; falls back to the browser) or **`Open Dashboard.command`**.

> Do not open `urban_dashboard.html` directly from Finder/Explorer: browsers block `fetch()` on
> `file://` pages, so the city list and map layers would not load. The launcher serves it over HTTP.

**Dashboard**
- top bar: **Source** switch (WorldPop / GHS-POP / HRSL, with the number of cities each has), **＋ Add city**, running-jobs indicator, **?** help
- left: sortable table — real area, core area, population, Δ vs real for the selected core; footer = mean / median / mean |Δ| over the visible rows; badges show which *other* sources a city has
- right: map with heatmap, OSM mask, real boundary and the four core outlines; basemap switch; the city card has the numbers, source chips and actions (compute another source, recompute, copy link, delete)
- the URL keeps the state (`#source=ghs&city=cairo&core=95`), so views can be shared

---

## 2. Add a city

### From the dashboard

Install the pipeline dependencies once (Python 3.9+) and start the launcher from that Python:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python launch.py
```

Then **＋ Add city** in the top bar:

1. **Location** — type the name and *Search* (OSM Nominatim), or *Pick on map* and click the
   centre, or type the coordinates. The blue circle shows the mask radius.
2. **Population source** — tick one or more of WorldPop / GHS-POP / HRSL.
3. **Options** (optional) — mask radius, mask mode, a reference area or a GeoJSON polygon for Δ,
   *recompute* to overwrite existing results.
4. **Compute** — jobs run one at a time in the background; the panel streams the pipeline log,
   a toast tells you when a city is ready and *Show* jumps to it.

In a city's card: dashed chips (**+ GHS-POP 2025**) compute a missing source, **↻ Recompute**
redoes the current one, **🔗 Link** copies a URL with the current source / city / core, **Delete**
removes the city with its results and cached mask.

The launcher exposes this as a tiny JSON API on localhost (`/api/status`, `/api/geocode`,
`/api/jobs`, `DELETE /api/cities/<id>`, see `urban/server.py`); jobs run `add_city.py` with the
same interpreter, so the UI and the CLI always agree. If the dashboard is served by something
else (a static server, GitHub Pages) the viewing part works and the add-city controls are hidden.

### From a terminal

```bash
# a new city with GHS-POP (country is resolved automatically)
python add_city.py --name "Bukhara" --lat 39.7747 --lon 64.4286 --source ghs

# the same city with another source — name and coordinates are already known
python add_city.py --id bukhara --source hrsl
python add_city.py --id bukhara --source worldpop

# an existing city with a second source
python add_city.py --id cairo --source ghs

# with a reference area / polygon to compare against
python add_city.py --name "Osh" --lat 40.53 --lon 72.80 --source worldpop --real-area 48.5
python add_city.py --id osh --source worldpop --real-polygon osh.geojson --force
```

What happens (a few seconds for a cached city; a new city takes 1–10 min, most of it waiting for
Overpass and for the WorldPop server, which is slow):

1. the city is registered in `cities_all.json` (country / ISO3 via Nominatim unless `--country/--iso3` are given)
2. the OSM landuse union within `--radius-km` (default 25) is fetched from Overpass and **cached in
   `urban_boundaries/<id>.geojson`** — every source of a city uses the same mask, so they are comparable
3. the population data is downloaded into `downloads/` (git-ignored) if not there yet
4. cores are computed and the map layers rendered into `urban_cities/<id>/<source>/`
5. reload the dashboard

Useful options: `--force` (recompute), `--refresh-boundary` (re-query OSM), `--radius-km 40`
(large agglomerations), `--mask osm+real` (union the reference polygon into the mask, see §5).

**Batch mode** for cities already in the list:

```bash
python run_urban.py --source ghs                 # every city that lacks GHS-POP
python run_urban.py --source hrsl cairo giza     # given ids
python run_urban.py --force --source worldpop    # recompute everything with WorldPop
```

**Reference polygons in bulk**: put them in `real_polygons_for_tool.xlsx` (sheet `for_tool`,
columns C = country, D = city, E = area km², F = GeoJSON geometry) and run
`python prepare_real_data.py` — it writes `real_boundary.geojson` / `real_meta.json` per city and
registers the cities; then compute them with `run_urban.py`.

Reuse rasters you already have: `export URBAN_DENSITY_DOWNLOADS=/path/to/downloads`
(expects `worldpop/<iso3>_constrained.tif`, `ghs_pop/GHS_POP_…_R<r>_C<c>.tif`, `hrsl/<iso3>_general_2020.csv`;
files directly in that folder are found too).

---

## 3. Method

1. **Mask.** Query OSM (Overpass, `urban/overpass.py`) for `landuse = residential | commercial | industrial | retail | mixed |
   construction | garages | brownfield | allotments` within 25 km of the centre; union the polygons;
   buffer by 0.003° (~300 m) to close gaps. This is the *urban extent*.
2. **Population grid.** Clip the raster to the mask and warp it to the local UTM zone
   (WorldPop / GHS-POP, bilinear, ≈ native pixel). HRSL is a *point lattice* — each ~250 m
   neighbourhood's people sit in one ~30 m cell — so its points are **summed onto a 250 m UTM grid**
   first (mass-conserving); feeding the raw lattice into a per-pixel method collapses the cores.
3. **Cores.** Density per pixel = population / pixel area. For t ∈ {70, 80, 90, 95}: keep pixels
   whose density is ≥ the (100 − t)-th percentile of populated pixels → area, population, density.
   *Core 90 %* is the headline metric.
4. **Δ vs real** = (core area − real area) / real area. The reference area is a functional zone
   from trip data (see `EXPERIMENTS.md`), not an administrative boundary.

Everything a run needs to be reproduced (source, mask, radius, grid size, date) is written into
`urban_cities/<id>/<source>/summary.json`.

### How the sources compare

Measured on the reference cities (details and all other experiments in `EXPERIMENTS.md`):

- **WorldPop** is the validated baseline: median |Δ| ≈ 35 % at Core 90 % on a 50-city A/B, bias +35 %.
- **GHS-POP** spreads population more smoothly → systematically **larger cores** (bias ≈ +130 % on the
  same 50 cities). Use it for global consistency and the 2025 epoch, not for the tightest match to trip zones.
- **HRSL** gives the sharpest settlement footprint, but its **country totals run 1.4–2.2× high in some
  countries** (Argentina, Angola, Vietnam; Sri Lanka is fine) — the tool prints the country total when it
  loads the CSV; if it is far from the UN figure, read HRSL cores as *shape*, not head-count.
  City-level totals inside the mask are usually close to WorldPop even where the country total is off.
- Country rasters (WorldPop, HRSL) stop at the border: a conurbation that crosses one (Kinshasa /
  Brazzaville) is only covered by GHS-POP.

---

## 4. Repository layout

```
urban-density/
├── urban_dashboard.html      # the dashboard (reads cities_all.json + urban_cities/ over HTTP)
├── launch.py                 # serve (files + add-city API) + open in browser ┐
├── app_window.py             # the same in a native window                     │ launchers
├── Urban Density.app         # macOS bundle around app_window.py (self-locating)
├── Open Dashboard.command    # macOS double-click → launch.py ┘
├── add_city.py               # add / recompute one city with one source
├── run_urban.py              # batch over cities_all.json
├── gen_map_assets.py         # re-render map layers from metric.tif
├── prepare_real_data.py      # import reference polygons from the xlsx
├── urban/                    # shared code: config, registry, boundary, sources, pipeline, server (API)
├── cities_all.json           # city registry: name, country, centre, real area, per-source numbers
├── real_polygons_for_tool.xlsx
├── urban_boundaries/<id>.geojson          # cached OSM landuse mask (simplified to 5e-5°)
├── urban_cities/<id>/
│   ├── real_boundary.geojson, real_meta.json   # reference polygon (if any)
│   ├── boundary_processed.geojson              # cleaned mask for display
│   └── <source>/                               # worldpop | ghs | hrsl
│       ├── summary.json                        # numbers + provenance
│       ├── metric.tif                          # population grid in UTM (git-ignored)
│       ├── density_overlay.png, density_meta.json, cores.geojson   # map layers
├── downloads/                # raw population data, git-ignored, auto-populated
└── EXPERIMENTS.md            # research log: what was tried and why the method is what it is
```

`cities_all.json` (schema in `urban/registry.py`):

```json
{"id": "cairo", "name": "Cairo", "country": "Egypt", "iso3": "EGY", "lat": 30.06, "lon": 31.25,
 "real_area": 986.1, "has_real_polygon": true, "bounds": [S, W, N, E],
 "sources": {"worldpop": {"urb_area": 929.8, "total_pop": 13949054,
                          "area70": 395.1, "pop70": 13662336, "...": "...", "area95": 536.3,
                          "mask": "osm", "computed_at": "2026-05-19", "grid_m": 92.3, "bounds": [S, W, N, E]}}}
```

---

## 5. Notes on the shipped numbers

- All 412 shipped cities were computed with **WorldPop**; GHS-POP / HRSL are present for the few
  cities used to test the other sources — run `run_urban.py --source ghs|hrsl` to fill them in.
- `mask` in `summary.json` records the extent used: `osm` for 360 cities; for 52 cities the original
  run unioned the reference polygon into the mask (`osm+real`). `add_city.py` defaults to `osm`,
  which does not let the model "see" the reference; pass `--mask osm+real` to reproduce the other mode.
- The cached OSM masks were fetched in May–June 2026 and are simplified to 5e-5° (~5 m) for the repo —
  irrelevant at 100 m pixels. `--refresh-boundary` re-queries OSM; expect small changes as the map evolves.
- Overpass has usage limits: adding many cities in one go may throttle; the fetcher rotates over three mirrors.

---

## 6. Troubleshooting

| symptom | cause / fix |
|---|---|
| empty dashboard, banner "Opened as a file" | open via `python3 launch.py`, not by double-clicking the HTML |
| `cities_all.json could not be loaded` | the server was started in another folder — run the launcher from the repo root |
| `all Overpass mirrors failed`, HTTP 504 / 429 lines in the job log | overpass-api.de is overloaded or one of its two backends (lambert / gall) is broken. The fetcher tries each backend with a 4-minute cap and retries a busy one twice, then gives up (~15 min worst case); the mask is cached once fetched. Wait a few minutes and re-run. Jobs are killed after 45 min anyway. |
| `WorldPop Constrained 2020 has no raster for XXX` | that country is not covered — use `--source ghs` |
| `No HRSL CSV found on HDX for XXX` | Meta has not published HRSL for that country — use `--source ghs` or `worldpop` |
| HRSL run is slow / uses lots of RAM | the country CSV is loaded whole (Vietnam ≈ 34 M points, 1.6 GB); it is cached within one `run_urban.py` batch |
| dark basemap shows "API KEY REQUIRED" | you are on an old copy of the dashboard: CARTO basemaps need a key since 2026; the current one uses Esri's dark canvas |
