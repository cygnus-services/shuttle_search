"""Fetch USFS National Forest System Roads for a bounding box as GeoJSON.

Queries the Forest Service EDW ArcGIS REST service (EDW_RoadBasic_01) with
pagination, so you get just your area instead of the multi-GB national
RoadCore download. See docs/DATA_SOURCING.md section 3.

Usage:
    python scripts/fetch_usfs_roads.py \
        --bbox -113.30 45.95 -112.80 46.30 --out usfs_roads.geojson
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request

SERVICE = ("https://apps.fs.usda.gov/arcx/rest/services/EDW/"
           "EDW_RoadBasic_01/MapServer/0/query")
PAGE = 2000
# NB: field names are lowercase on this service; uppercase names silently
# return null values instead of erroring.
FIELDS = ("id,name,oper_maint_level,objective_maint_level,surface_type,"
          "route_status,symbol_name,openforuseto")


def fetch(bbox: tuple[float, float, float, float]) -> list[dict]:
    feats: list[dict] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "where": "1=1",
            "geometry": ",".join(str(v) for v in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": FIELDS,
            "outSR": 4326,
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
        })
        with urllib.request.urlopen(f"{SERVICE}?{params}", timeout=120) as r:
            batch = json.load(r).get("features", [])
        feats.extend(batch)
        print(f"  offset {offset}: +{len(batch)} features")
        if len(batch) < PAGE:
            return feats
        offset += PAGE


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("MINLON", "MINLAT", "MAXLON", "MAXLAT"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    feats = fetch(tuple(args.bbox))
    with open(args.out, "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)
    print(f"Saved {len(feats)} USFS road features -> {args.out}")


if __name__ == "__main__":
    main()
