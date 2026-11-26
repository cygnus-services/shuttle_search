"""Make a search-polygon GeoJSON from a center point or two corners.

Read the coordinates off the QGIS status bar (or right-click > Copy
Coordinate with the Lat Lon Tools plugin), then:

    # 6 x 4 mile box centered on a point:
    python scripts/make_polygon.py --center -113.32 46.22 \
        --width-mi 6 --height-mi 4 --out examples/my_area.geojson

    # or two opposite corners:
    python scripts/make_polygon.py --corners -113.40 46.18 -113.25 46.28 \
        --out examples/my_area.geojson

Coordinates are WGS84 lon lat (lon negative in Montana; if you paste
"46.22, -113.32" order from a lat/lon readout, the script detects and
swaps it).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def _fix_order(a: float, b: float) -> tuple[float, float]:
    """Return (lon, lat); swaps if the pair looks like (lat, lon)."""
    if a > 0 and b < 0:  # Montana: lat positive, lon negative
        return b, a
    return a, b


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--center", nargs=2, type=float, metavar=("LON", "LAT"))
    g.add_argument("--corners", nargs=4, type=float,
                   metavar=("LON1", "LAT1", "LON2", "LAT2"))
    ap.add_argument("--width-mi", type=float, default=5.0)
    ap.add_argument("--height-mi", type=float, default=5.0)
    ap.add_argument("--name", default="custom search area")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    if args.center:
        lon, lat = _fix_order(*args.center)
        dlat = args.height_mi / 2 / 69.0  # miles per degree latitude
        dlon = args.width_mi / 2 / (69.0 * math.cos(math.radians(lat)))
        lons = (lon - dlon, lon + dlon)
        lats = (lat - dlat, lat + dlat)
    else:
        lon1, lat1 = _fix_order(*args.corners[:2])
        lon2, lat2 = _fix_order(*args.corners[2:])
        lons = (min(lon1, lon2), max(lon1, lon2))
        lats = (min(lat1, lat2), max(lat1, lat2))

    ring = [[lons[0], lats[0]], [lons[1], lats[0]], [lons[1], lats[1]],
            [lons[0], lats[1]], [lons[0], lats[0]]]
    args.out.write_text(json.dumps({
        "type": "Feature",
        "properties": {"name": args.name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }, indent=1))
    w = (lons[1] - lons[0]) * 69.0 * math.cos(math.radians(sum(lats) / 2))
    h = (lats[1] - lats[0]) * 69.0
    print(f"wrote {args.out}: {w:.1f} x {h:.1f} mi, "
          f"lon {lons[0]:.4f}..{lons[1]:.4f}, lat {lats[0]:.4f}..{lats[1]:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
