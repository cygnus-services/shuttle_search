# Shuttle Scout

Automated shuttle mountain-bike trail scouting for Montana. Give it a search
polygon and a stack of rasters (elevation, roads, hydrography, optional land
cover); it finds road-accessible start/end pairs with big vertical drop,
sanity-checks them with example descent routes, ranks them, and writes a
QGIS-ready multi-band GeoTIFF plus a ranked CSV/GeoJSON.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Get data (full instructions + verified links: docs/DATA_SOURCING.md)
.venv/bin/python scripts/fetch_usfs_roads.py --bbox -113.30 45.95 -112.80 46.30 \
    --out data/raw/usfs_roads_anaconda.geojson
.venv/bin/python scripts/prepare_data.py \
    --polygon examples/anaconda_search_area.geojson \
    --dem data/raw/USGS_13_n47w113.tif \
    --osm-roads data/raw/montana_osm/gis_osm_roads_free_1.shp \
    --usfs-roads data/raw/usfs_roads_anaconda.geojson \
    --nhd-gdb data/raw/NHD_H_17010201_HU8_GDB.zip \
    --out data/prepared/anaconda

# Run the scout
.venv/bin/python -m shuttle_scout \
    --polygon examples/anaconda_search_area.geojson \
    --dem data/prepared/anaconda/dem.tif \
    --roads data/prepared/anaconda/roads.tif \
    --hydro data/prepared/anaconda/hydro.tif \
    --out out/anaconda
```

Outputs in `out/anaconda/`:

* **QGIS layers, one file each so they toggle independently** — every one
  has a `.qml` style sidecar that QGIS applies automatically on load (the
  `.qml` files are styles, not data — load only the `.geojson`/`.tif`):
  * `start_points.geojson` (circles) / `end_points.geojson` (squares) —
    large markers labeled `#rank · elevation`, second line = feet to road
  * `routes.geojson` — example descent lines; each pair's primary route is
    solid, its alternate dashed (same color). Routes are confined to a
    corridor around the pair's straight line (`params.route_corridor_*`).
  * Each ranked pair gets its own color, sampled uniformly from the Turbo
    colormap, **hotter = better** (#1 red → last blue/purple), shared by
    its start marker, end marker, and routes — trace a pair by color.
    `color_legend.svg` is the standalone color bar with score/drop/drive.
  * Hover popups: enable *View > Show Map Tips* — hovering a point shows
    elevation, drop, grade, score, shuttle time; a route shows length and
    intermediate climb.
  * `roads_overlay.tif` — roads colored by TYPE: dark asphalt = highway,
    purple = paved local, dark brown = gravel, mid brown = dirt
    (high-clearance), tan = jeep/barely-a-road, magenta = too steep to
    drive. Types come from OSM `fclass` + USFS maintenance level/surface
    and drive the per-class shuttle speeds (`params.drive_speed_*`).
  * `hazards_overlay.tif` — translucent red (water/cliff) / orange (too steep)
  * Default is the top 10 pairs (`params.max_ranked_pairs`); routes per
    pair via `params.routes_per_pair` (set 1 to halve the line count).
* **`ranked_pairs.md`** — the ranked table as Markdown; open your IDE's
  Markdown preview (⇧⌘V in VS Code/Antigravity) for a clean read.

Cursor elevation readout: QGIS's status bar shows x/y only. Install the
**Value Tool** plugin, load `data/prepared/<area>/dem.tif`, and its panel
shows the elevation (meters) under the cursor live.
* **`ranked_pairs.csv`** — the ranked list with drop, grade, drive time,
  road distances, per-factor scores.
* **`shuttle_candidates.tif`** — everything in one 5-band uint16 GeoTIFF
  (band 1 starts, 2 ends, 3 routes — pixel value = rank, 1 best; 4 roads;
  5 hazards), for tools that want a single raster.

QGIS: select all the files above and drag them in together. When the NAD83
→ WGS84 transform dialog appears, the default (top) option is fine — the
choices differ by 1–4 m, far below the 10 m cell size (silence it under
*Settings > Options > CRS Handling*). Click any point/route with the
Identify tool to see the full scorecard.

## How it works

1. **Load & validate** ([data_loading.py](shuttle_scout/data_loading.py)) —
   everything is warped onto one grid (default EPSG:26912 @ 10 m); loud
   logging of coverage, elevation range, and unit sanity checks.
2. **Terrain analysis** ([terrain.py](shuttle_scout/terrain.py)) — slope,
   hazard mask (buffered water, cliffs, nodata, excluded land-cover
   classes), unrideable-steepness mask, distance-to-road surface.
3. **Candidates** ([candidates.py](shuttle_scout/candidates.py)) — start
   points are local elevation maxima and ends are local minima *among
   road-accessible, safe cells*; pairs are screened by the cheap hard
   constraints (drop ≥ 1000 ft configurable to 500, avg grade 6–100%,
   distance window).
4. **Routing** ([routing.py](shuttle_scout/routing.py)) — shuttle drive
   time via least-cost path along the rasterized road network (≤ 45 min
   gate), then 1–2 example descent routes per pair via a cost surface that
   prefers steady grade and forbids hazards. Pairs with no viable route
   are dropped.
5. **Scoring** ([scoring.py](shuttle_scout/scoring.py)) — weighted blend
   (drop efficiency 30%, road access 25%, low intermediate climb 15%,
   grade consistency 15%, drivability 15%), near-duplicate suppression,
   top-N ranked.
6. **Output** ([output.py](shuttle_scout/output.py)).

All thresholds/weights live in [config.py](shuttle_scout/config.py) and can
be overridden from JSON (`--config`, see
[examples/config_overrides.example.json](examples/config_overrides.example.json)).
Unit conventions: internal math in meters; user-facing config in
feet/miles/minutes, converted exactly once ([units.py](shuttle_scout/units.py)).

## Assumptions & caveats

* Drive time = road-network least-cost distance ÷ one average speed
  (default 25 mph). Treat the 45-min gate as ±30% until ground-truthed.
* Example routes are *sanity-check corridors* from an isotropic cost
  surface, not final trail alignments; intermediate climbing is measured
  after extraction and penalized in scoring rather than forbidden a priori.
* A mapped road ≠ a drivable road (gates, washouts, seasonal closures) —
  MVUM data and field checks are the fix; see docs/DATA_SOURCING.md.
* GeoJSON polygons are WGS84 (RFC 7946); DEMs assumed meters
  (`--dem-units feet` otherwise).

## Parameter profiles

Three families live in `profiles/`; pass one with `--config`. They differ in
hard gates, scoring ramps, and how densely candidates are sampled.

| profile | hunting for | drop | grade | shuttle | notes |
|---|---|---|---|---|---|
| *(default)* | big vertical | ≥ 1000 ft | 6–100% | ≤ 45 min | sparse peak/valley pool |
| `short_laps.json` | quick repeatable laps | 400–1000 ft | 6–100% | ≤ 20 min | dense pool, prefers ≤ 250 ft road access |
| `dh_friendly.json` | pure gravity descents | ≥ 1000 ft | **20–50%** | ≤ 45 min | climb > 400 ft rejected, > 120 ft heavily penalised |

DH needs the dense close-spaced pool it ships with: a 1000 ft drop at 20–50%
grade puts start and end only 0.4–1.0 mi apart, and a sparse peak-to-valley
pool never produces pairs that close together.

## Zone model (`--zones`)

A drop-off is not a GPS point — it is "anywhere along this stretch of road".
`--zones` clusters the surviving candidates into start/end **zones** and keeps
one representative trail per zone pair, which natively replaces the dedup
radius and the stacked `#3 · #8 · #10` labels of the point model. On Warm
Springs v2, 46 point-pairs collapse to 3 start zones, 6 end zones, and 10
connections. Zone mode writes zone layers *instead of* the point layers:

* `start_zones.geojson` / `end_zones.geojson` — Turbo blobs, ranked by colour
* `zone_routes.geojson` — one white trail per connection (dashed only when a
  genuinely divergent second line exists between the same two zones)

Knobs: `zone_radius_m` (clustering), `zone_buffer_m` (blob size),
`zone_route_separation_m` (what counts as a distinct alternative).

## What gets rejected, and why

Beyond drop/grade/drive-time, a pair must survive several shape gates. These
are the ones that actually do the pruning — check the "Route generation" log
line to see which is biting:

* **Pickup overshoot** (`max_pickup_overshoot_ft`, 300) — the descent may not
  run *past* a usable pickup road and force a hike back up. By far the most
  aggressive filter.
* **Road crossings / riding the road** (`max_route_road_crossings` = 1,
  `max_road_along_ft` = 200) — the finish may sit on a paved street, but the
  trail may not thread street crossings or ride along a road to get there.
  The first and last road runs are free (departing and arriving).
* **Degenerate shuttle** (`min_shuttle_drive_mi`, `min_route_to_access_ratio`)
  — start and end anchoring to the same road spot is not a shuttle.
* **Cliffs** (`cliff_slope_pct` 150) — extreme-cost, not forbidden: the router
  hunts a ride-around and the pair fails only if the route still crosses one.
  Steep is good — rideable up to `max_rideable_slope_pct` (100%, ~45°).

Caveat worth knowing: at 10 m DEM resolution slope cannot distinguish a 20 ft
vertical cliff from a sustained steep chute. Feed a 1 m lidar DEM if true
cliff detection matters.

## Candidate generation

Starts and ends come from two sources, both required to be road-accessible:

1. **Local extrema** — peaks for starts, valleys for ends.
2. **The road network itself** (`road_endpoint_spacing_m`, 500 m) — road-seeded
   starts exclude local minima, road-seeded ends exclude local maxima.

Source 2 matters more than it sounds: the most sought-after shape is
"road-accessed high point → ridge → any road below", and strict extrema can
never express it, because a ridge meeting a road mid-slope is neither a peak
nor a valley. Warm Springs v2 went from **0 to 10** ranked pairs when road
endpoints were added.

## Data provenance

What each analysis layer is actually built from (full sourcing detail and
verified URLs: [docs/DATA_SOURCING.md](docs/DATA_SOURCING.md)):

| Layer | Source of record | Provider / license | Vintage used here |
|---|---|---|---|
| Elevation (`dem.tif`) | USGS 3DEP 1/3 arc-second DEM, tiles `USGS_13_n47w113.tif` + `USGS_13_n47w114.tif` from the National Map staged-products S3 bucket | USGS, public domain | tile publish 2024-11 (fetched 2026-07-21) |
| Roads (`roads.tif`) | **Union of two sources:** (1) OpenStreetMap Montana extract (Geofabrik `montana-*-free.shp.zip`, layer `gis_osm_roads_free_1`, filtered to drivable `fclass` values incl. `track`); (2) USFS National Forest System Roads from the EDW `EDW_RoadBasic_01` ArcGIS REST service | OSM © OpenStreetMap contributors, ODbL; USFS EDW, public domain | OSM snapshot 2026-07-01; EDW queried live 2026-07-21 |
| Hydrography (`hydro.tif`) | USGS National Hydrography Dataset High-Res, HUC8 17010201 (Upper Clark Fork) FileGDB: `NHDFlowline` + `NHDArea` + `NHDWaterbody` | USGS, public domain | GDB build 2023-12 (fetched 2026-07-21) |
| Land cover (optional) | NLCD Annual Land Cover, MRLC viewer export | USGS/MRLC, public domain | user-supplied |

Provenance caveats that affect interpretation: OSM completeness for old
logging roads is uneven (that's why USFS EDW is merged in); the EDW road
layer includes closed/custodial roads (`OPER_MAINT_LEVEL` 1–2) that may not
be legally drivable — check MVUM before planning a shuttle; NHD is frozen
(superseded by 3DHP) but remains the authoritative high-res hydro layer.
If you regenerate `roads.tif`/`hydro.tif`, record the fetch date — both OSM
and EDW are living datasets.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

30 tests cover unit conversions, config validation, terrain math on
synthetic surfaces, hard-constraint gates, scoring behavior, and an
end-to-end run on a synthetic mountain scene.

## Extending

New scoring factor: add a field to `ScoringWeights`, compute a 0–1 part in
`scoring.score_pairs`, done — ranking and CSV pick it up automatically.
New data source (e.g. BLM roads): merge it in `scripts/prepare_data.py`;
the pipeline only sees rasters. New hard constraint: add to
`HardConstraints` and gate in `candidates.screen_pairs` (cheap) or
`routing` (expensive).
