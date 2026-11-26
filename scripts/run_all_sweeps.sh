#!/bin/bash
# Run every area x profile sweep on the current rule set.
# Assumes data/prepared/<area>/ exists (see scripts/prepare_data.py).
#   Areas:    anaconda, warm_springs_v2, philipsburg, echo_lake  (MT, 26912)
#             jamul                                             (CA, 26911)
#   Profiles: default (big vertical), short_laps, dh_friendly
set -u
PY=.venv/bin/python

run_sweep() {  # polygon rasters_dir outdir crs [profile]
    local polygon=$1 rasters=$2 outdir=$3 crs=$4 profile=${5:-}
    echo "--- $outdir ---"
    if [ -n "$profile" ]; then
        $PY -m shuttle_scout --polygon "$polygon" \
            --dem "data/prepared/$rasters/dem.tif" \
            --roads "data/prepared/$rasters/roads.tif" \
            --hydro "data/prepared/$rasters/hydro.tif" \
            --target-crs "$crs" --config "$profile" --out "$outdir" 2>&1 \
            | grep -E "Route generation|Ranked|No candidate"
    else
        $PY -m shuttle_scout --polygon "$polygon" \
            --dem "data/prepared/$rasters/dem.tif" \
            --roads "data/prepared/$rasters/roads.tif" \
            --hydro "data/prepared/$rasters/hydro.tif" \
            --target-crs "$crs" --out "$outdir" 2>&1 \
            | grep -E "Route generation|Ranked|No candidate"
    fi
}

run_area() {  # polygon rasters_dir name crs
    run_sweep "$1" "$2" "out/$3"        "$4"
    run_sweep "$1" "$2" "out/$3_short"  "$4" profiles/short_laps.json
    run_sweep "$1" "$2" "out/$3_dh"     "$4" profiles/dh_friendly.json
}

run_area examples/anaconda_search_area.geojson    anaconda    anaconda        EPSG:26912
run_area examples/warm_springs_v2.geojson         anaconda    warm_springs_v2 EPSG:26912
run_area examples/philipsburg_search_area.geojson philipsburg philipsburg     EPSG:26912
run_area examples/echo_lake_search_area.geojson   philipsburg echo_lake       EPSG:26912
run_area examples/jamul_polygon.geojson           jamul       jamul           EPSG:26911
echo "ALL_SWEEPS_COMPLETE"
