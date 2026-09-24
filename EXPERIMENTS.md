> **Research log.** This file is the experiment history behind the method (kept verbatim).
> It predates the current repository layout: script names such as `run_compare.py`,
> `popcentre_city.py`, `audit_masks.py` refer to one-off research scripts that are not
> part of this repo, and `urban_cities/<id>/summary.json` is now
> `urban_cities/<id>/<source>/summary.json`. The production method it describes is what
> `add_city.py` / `run_urban.py` implement.

# Urban Area Pipeline — Experiments Log

**Baseline method:** OSM landuse union (25km radius) + WorldPop Constrained 2020 clip + density percentile cores  
**Primary metric:** Core 90% area (km²) vs functional trip-zone area (REAL dict)  
**Baseline MAPE:** 30.8% (39 cities)

---

## Key Definitions

- **Core 90%** — area of pixels with density ≥ 10th percentile (i.e. top 90% by density). Currently best metric at MAPE 30.8%.
- **Real area** — functional zone based on actual trip/mobility data (not administrative boundaries). Source: proprietary dataset.
- **REAL dict** — `export_comparison.py`, `run_urban.py`, `run_admin.py` all share these values.
- **WorldPop Constrained 2020** — 100m settlement raster (population per pixel), clipped to building footprints.
- **OSM URBAN_TAGS** — `landuse: [residential, commercial, industrial, retail, mixed, construction, garages, brownfield, allotments]`

---

## Baseline Pipeline (`run_urban.py`)

1. Query OSM features within 25km radius around city center using `ox.features_from_point()`
2. Union all landuse polygons + 0.003° buffer (fill-gap ~300m)
3. Clip WorldPop raster by union polygon
4. Reproject to UTM
5. Compute density (ppl/km²) per pixel
6. For each threshold t ∈ [70, 80, 90, 95]: keep pixels with density ≥ (100-t)th percentile → area + population

**Note:** OSM query uses square bbox (not circle) around city center.

---

## Experiment 1 — Abbottabad Fix (City-specific OSM filtering)

**Hypothesis:** Abbottabad boundary captures Muzaffarabad (a separate city ~60km away) because OSM landuse polygons are fragmented across the region.  
**Fix applied:** Connected components filter — keep only the component containing the city center.  
**Result:** Improved Abbottabad significantly but was initially applied city-specific. When generalized to all cities, it performed worse overall.  
**Decision:** Reverted. No uniform approach.

---

## Experiment 2 — OSM Tag Filter (remove `allotments` + `brownfield`)

**Hypothesis:** allotments (farmland) and brownfield (derelict land) inflate the urban polygon in peri-urban areas.  
**Method:** Removed these two tags from URBAN_TAGS, re-ran pipeline.  
**Result:** Worse overall MAPE than baseline.  
**Decision:** Reverted. Tags restored to original.

---

## Experiment 3 — GHS-BUILT-S Replacement

**Hypothesis:** Replace OSM landuse mask with EU JRC GHS-BUILT-S R2023A satellite-derived built-up surface raster (100m, WGS84 3 arc-second tiles).  
**Implementation:**
- Downloaded 19 tiles covering all 39 cities
- Tile naming formula (corrected): `row = ceil((89.10 - lat) / 10)`, `col = floor((lon + 180.01) / 10) + 1`
- `run_ghs.py` created — substitutes GHS mask for OSM polygon
- Bug fixed: original formula `int((90-lat)//10)+1` was off-by-one for cities near tile boundaries  
**Result:** Worse MAPE than baseline.  
**Decision:** `run_ghs.py` deleted. Approach abandoned.  
**Files:** GHS tiles remain in `downloads/ghs_built/` (not used by production pipeline).

---

## Experiment 4 — Absolute Density Threshold for Core

**Hypothesis:** Replace percentile-based core with absolute density threshold (e.g., ≥300 ppl/km²). This could be more consistent cross-city than percentiles.  
**Result:** Not formally tested as separate experiment; analyzed as part of dynamic bbox work.  
**Decision:** Not pursued further.

---

## Experiment 5 — Connected Components / Connectivity Filter

**Hypothesis:** After computing density mask, keep only the connected component that contains the city center. This would exclude Nowshera/Charsadda from Peshawar, etc.  
**Implementation:** `scipy.ndimage.label` on binary density mask → find component containing city center → keep only that.  
**Result:** Worse MAPE overall (hurts well-mapped cities more than it helps outliers).  
**Decision:** Reverted.

---

## Experiment 6 — Dynamic BBox via Density Ring Falloff

**Hypothesis:** Instead of fixed 25km radius for OSM query, find city-specific radius where WorldPop density "falls off". Use cumulative density rings (1km steps) from city center.

**Method:**
1. Read WorldPop raster in 30km window around city center
2. Compute mean density per 1km ring
3. Find radius where density drops below threshold (absolute or relative)
4. Clip cached OSM polygon to that radius → recompute cores

**Absolute thresholds tested:** 100, 200, 300, 500, 800, 1000, 1500, 2000 ppl/km²  
**Result:** Most cities never drop below 2000 ppl/km² within 30km. Threshold = 2000 gives MAE = 235% vs ideal radius (poor).

**Relative thresholds tested:** 5%, 10%, 20% of peak ring density  
- 5%:  MAPE = 31.1% (worse)
- 10%: MAPE = 30.6% (marginally better — saves Marrakesh and Bulawayo)
- 20%: MAPE = 32.8% (worse — helps Alexandria and Lagos, but destroys Tangier, Damietta, Cairo)

**Key insight from ring profiles:**
- Peshawar: flat ~7000-9000 ppl/km² all the way to 30km (entire Peshawar valley is continuously dense). No falloff signal.
- Cairo: drops naturally, but reducing radius makes underestimate worse (was already -49%).
- Port Said: clear drop at 4km — already well-estimated at 25km (0% error), reducing hurts.

**Decision:** 10% threshold is marginal improvement (+0.2%). Not worth implementing given complexity.

---

## Experiment 7 — Dynamic BBox via Citypopulation.de Population

**Hypothesis:** Use external population data as anchor — find WorldPop radius that captures known agglomeration population → use as OSM query radius.

**Data source:** Scraped `citypopulation.de` via CloakBrowser Playwright.  
**Scripts:** `scrape_citypop*.py`, `scrape_agglom*.py` in pipeline root.  
**Data files:** `citypop_final.json` (city proper populations, 39 cities), `agglom_data.json` (agglomeration populations ≥1M, 24 cities).

### Sub-experiment 7a — City proper population (municipality)

**Method:** Find WorldPop radius capturing `citypopulation.de` municipality population. Min=5km, max=25km.  
**Result:** MAPE = 43.3% (much worse)  
**Why:** Pakistani and Egyptian city populations are very dense — small radius captures the census population but functional trip area is larger. Damietta (5km), Sialkot (5km), Cairo (9km) get radii too small.

### Sub-experiment 7b — Agglomeration population (>1M cities)

**Method:** Use agglomeration populations (citypopulation.de world agglomerations page) where available; fall back to city proper for smaller cities.  
Key agglomeration values: Delhi=36.9M, Cairo=23.2M, Lagos=21.9M, Karachi=21.8M, Lahore=15M, Lima=12.2M, Peshawar=2.175M.  
Min=5km, max=25km.  
**Result:** MAPE = 36.6% (worse)

### Sub-experiment 7c — Agglomeration with min=12km cap

**Method:** Same as 7b but min radius = 12km to prevent over-reduction of smaller cities.  
**Result:** MAPE = 32.4% (still worse than baseline)  
**Who improves:** Peshawar (381→200), Lagos (915→684), Delhi (878→758), Marrakesh (178→131 — near-perfect!)  
**Who degrades:** Sialkot (130→67), Damietta (78→26), Blida (151→35)

**Why the approach fails:** `citypopulation.de` population (even agglomeration) does not reliably correlate with functional trip-zone area. These are fundamentally different metrics.

---

## Experiment 8 — Population-based extent (DEGURBA-like urban centre)

**Date:** 2026-09-17  
**Hypothesis:** In cities with sparse OSM landuse mapping the landuse union misses most of the
population, so build the extent from the population raster instead of from OSM.

**Method (`popcentre_city.py`):** clip WorldPop to a 25 km circle → warp to UTM (~100 m) →
block-sum to a 1 km grid → keep cells ≥ 1500 ppl/km² (DEGURBA urban-centre threshold) → fill
holes → keep the 4-connected component containing the city centre → polygonize. Cores are then
the usual density percentiles on the 100 m raster clipped to that extent.

**Validation:** 137 cities with trip-zone reference (up to 5 per country, seed 7).

| Extent | Core 90% MAPE | Bias |
|---|---|---|
| OSM landuse (production) | **52.0%** | +2.2% |
| Population-based urban centre | 53.7% | −16.7% |

**Result:** not better on average, and systematically low → **production method unchanged.**

**Who improves:** cities whose OSM landuse is over-mapped — Sousse (+168% → +0.9%),
Belaya Tserkov (+249% → −27.5%), Chiang Mai (+43% → −40%), Odessa (+109% → −45%).  
**Who degrades:** cities whose OSM landuse is already sparse — Gweru (−49% → −89%),
Masvingo (−34% → −57%), Da Nang (−34% → −54%).

**Where it is used:** as a per-city alternative layer only, injected as a separate
`<id>_pop` / "<Name> (pop-based)" dashboard entry (same pattern as the `_hrsl` entries), never
replacing the OSM-landuse numbers. Currently: `libreville_pop`, `colombo_pop`.

### 8a — Colombo vs UN WUP 2025 (2026-09-18)

UN WUP2025-F21-DEGURBA-Cities_Pop puts the Colombo agglomeration near 5M; the tool said 1.32M.
Rebuilding the UN's own definition with no radius cap reproduces their number — GHS-POP 2025
(the source WUP is built on) gives 1178 km² / 4.81M, WorldPop 2020 gives 1282 km² / 4.75M, both
reaching ~59 km from the centre.

Where the gap comes from (WorldPop counts on the GHS cluster outline, so the source does not
confound it):

| | area | population |
|---|---|---|
| DEGURBA cluster | 1178 km² | 4 544 382 |
| ├─ within 25 km of centre | 799 km² | 3 504 198 |
| └─ beyond 25 km — cut by `RADIUS_M` | 380 km² | −1 040 184 |
| OSM landuse (tool's mask) | 288 km² | 1 323 130 |
| └─ of which inside the cluster | 234 km² | 1 258 289 |

So the 25 km cap costs 23% of the gap; the sparse landuse mask costs 69% — it holds 20% of the
cluster's area and 28% of its people. Raising the radius alone would not fix it. Shipped as
`colombo_pop` ("Colombo (pop-based, 60 km)"): 1284.4 km² / 4 741 099, Core 90% 673.6 km².
`popcentre_city.py` takes an optional `radius_km` argument for this.

Side note: WorldPop Constrained 2020 totals 18.4M for Sri Lanka against ~21.9M (UN 2020) — the
raster itself runs 16% low, which is why the same outline gives 4.54M (WorldPop) vs 4.81M (GHS-POP).

**Caveat on "correct":** the tool's reference is a functional trip zone, not a UN agglomeration.
These are different quantities, and against trip zones the population-based mask scored slightly
worse (above). Colombo has no trip reference, so UN is the only yardstick available there.

### 8b — Mask audit across all 644 cities (2026-09-18)

`audit_masks.py` measures, per city: population inside the OSM-landuse mask vs inside the DEGURBA
cluster built around the same centre (45 km probe), plus how far that cluster reaches. Output:
`_mask_audit.csv` (all cities), `_sparse_mask_cities.csv` (the sparse-mask shortlist).

Coverage — mask population / cluster population, clusters ≥ 100k, n=565 — is **median 103%**
(the landuse mask usually holds as many people as the density cluster, often more, since it also
covers sub-threshold fringe). The problem is the tail: **p25 = 69%, p10 = 37%.**

Two distinct failure modes, and they need different fixes:

| | count | what it is | fix |
|---|---|---|---|
| Sparse landuse | **56** | cluster is self-contained (reach ≤ 30 km) yet the mask holds < 60% of its people — OSM landuse is barely mapped there | `popcentre_city.py` at default radius (the Libreville case) |
| Cluster wider than 25 km | **70** | cluster still growing at the 45 km probe edge | `popcentre_city.py` with a bigger `radius_km` (the Colombo case) |

Worst sparse-landuse cities: Tasikmalaya 14%, Bloemfontein 14%, Tanga 14%, Eldoret 15%,
Mbarara 19%, Gorontalo 19%, Praia 20%, Zaria 21%, Porto Velho 23%, Tarakan 25%, Bauchi 26%,
Nakuru 26%, Mahajanga 28%, Abbottabad 29% (already a known outlier), Campo Grande 30%.

**Caution reading the second group:** DEGURBA clusters merge neighbouring cities, so for a
satellite the "cluster" is the whole conurbation — Banha's 21M is the Nile Delta plus Cairo,
Cuernavaca's 12.9M is Mexico City. Those rows show reach, not a per-city undercount. Genuine
large-agglomeration cases in that group (Jakarta, Tokyo, Dhaka, Kolkata, Seoul, Bangkok) are cut
by the 25 km radius the same way Colombo was.

**Note:** the 52.0% MAPE above is on the current 412-city reference set; the 30.8% headline
below is the original 39-city set and is not comparable.

---

## Summary of Results

| Approach | Core 90% MAPE | vs Baseline |
|----------|-------------|-------------|
| Baseline (OSM 25km + WorldPop) | **30.8%** | — |
| Tag filter (-allotments, -brownfield) | worse | ✗ |
| GHS-BUILT replacement | worse | ✗ |
| Connectivity filter | worse | ✗ |
| Relative falloff 5% | 31.1% | -0.3% ✗ |
| Relative falloff 10% | 30.6% | +0.2% ≈ |
| Relative falloff 20% | 32.8% | -2.0% ✗ |
| City population radius (min=5) | 43.3% | -12.5% ✗ |
| Agglom population radius (min=5) | 36.6% | -5.8% ✗ |
| Agglom population radius (min=12) | 32.4% | -1.6% ✗ |
| Population-based extent (1 km, ≥1500 ppl/km²) | 53.7% vs 52.0%¹ | ✗ |

¹ Measured on the current 412-city reference set (137-city sample), not on the 39-city set the other rows use — compare the two numbers in that row with each other, not with the rows above.

---

## Root Cause Analysis of Main Outliers

### Systematic overestimates (method captures more than trip data)

| City | Real | Model | Error | Root cause |
|------|------|-------|-------|------------|
| Peshawar | 130 | 381 | +193% | Entire Peshawar valley (Nowshera, Charsadda) is continuously dense — no natural boundary between cities in WorldPop or OSM |
| Alexandria | 125 | 220 | +76% | Nile Delta has continuous dense settlement, OSM polygon extends throughout |
| Lagos | 550 | 915 | +66% | Continuous Lagos megacity extends well beyond administrative Lagos LGA |
| Agadir | 97 | 154 | +59% | OSM captures surrounding resort/hotel developments as residential |
| Delhi | 589 | 878 | +49% | Delhi NCR is a continuous conurbation (Faridabad, Ghaziabad, Gurgaon all connected) |

### Systematic underestimates (method captures less than trip data)

| City | Real | Model | Error | Root cause |
|------|------|-------|-------|------------|
| Abbottabad | 161 | 24 | -85% | Sparse OSM landuse coverage in mountainous KPK region; WorldPop underestimates valley settlements |
| Hurghada | 103 | 49 | -53% | Resort/tourist city morphology — long thin strip along Red Sea coast, OSM landuse doesn't capture resort areas well |
| Cairo | 996 | 508 | -49% | Trip-zone is Greater Cairo (Cairo + Giza + Shubra El-Kheima ~20M people, 1000km²+); our 25km radius only captures part |
| Astana | 196 | 99 | -49% | New planned city — large undeveloped land parcels between built areas, OSM landuse gaps |
| Rabat | 288 | 162 | -44% | Rabat-Salé-Kénitra metropolitan area extends far; 25km doesn't fully cover |
| Libreville | n/a | n/a | — | No trip reference, but OSM landuse holds only 191k of the 754k WorldPop counts within 25 km — landuse in Gabon is barely mapped. Population-based extent gives 635k / Core 90% 128.9 km² vs 69.7 km² (Experiment 8); shipped as the `libreville_pop` layer |

---

## Current Production State

**Active pipeline:** `run_urban.py` — unchanged from original (25km radius, all tags, no connectivity filter)  
**Map assets:** `gen_map_assets.py` — generates density PNG overlays and cores GeoJSON  
**Dashboard:** `urban_dashboard.html` — two-tab: comparison table + Leaflet map  
**Comparison export:** `export_comparison.py` — generates `city_comparison.xlsx`

**Core 90% is the best single metric** — lowest MAPE across all methods tested.

---

## Unused Files (can be deleted)

- `scrape_citypop*.py`, `scrape_agglom*.py` — scrapers (data saved to JSON)
- `test_dynamic_radius.py`, `test_agglom_radius.py`, `test_pop_radius.py` — experiment scripts
- `analyze_threshold.py`, `analyze_profiles.py` — analysis scripts
- `check_current_mape.py` — quick MAPE check
- `citypop_pakistan.txt`, `citypop_*.txt` — raw scrape output
- `pak_cities_full.html` — raw HTML
- `world_agglom.txt` — raw agglomeration text

---

## Ideas Not Yet Tried

1. **DBSCAN clustering** — cluster WorldPop pixels from city center, keep the main cluster only. Different from connectivity filter (density-based, not grid-based).
2. **Increase max radius to 35km** — might fix Cairo, Rabat (currently underestimated), at cost of worsening Peshawar.
3. **Hybrid: use agglomeration radius for overestimates only** — would require knowing current estimate relative to baseline, not available at runtime.
4. **Different percentile for different city sizes** — large cities at Core 85%, small at Core 95%.
5. **WorldPop general (unconstrained)** — might better capture sparse peri-urban areas like Abbottabad, Hurghada.
