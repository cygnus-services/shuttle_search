"""Command-line entry point.

Example:
    python -m shuttle_scout \\
        --polygon examples/anaconda_search_area.geojson \\
        --dem data/dem.tif --roads data/roads.tif --hydro data/hydro.tif \\
        --terrain data/nlcd.tif --out out/ --config my_overrides.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import Config
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="shuttle_scout",
        description="Scout shuttle MTB start/end candidates in a polygon.",
    )
    ap.add_argument("--polygon", required=True, help="GeoJSON search polygon")
    ap.add_argument("--dem", required=True, help="Elevation GeoTIFF")
    ap.add_argument("--roads", required=True,
                    help="Road GeoTIFF (nonzero = road)")
    ap.add_argument("--hydro", required=True,
                    help="Hydrography GeoTIFF (nonzero = water)")
    ap.add_argument("--terrain", default=None,
                    help="Optional land-cover class GeoTIFF (e.g. NLCD)")
    ap.add_argument("--out", required=True, help="Output directory")
    ap.add_argument("--config", default=None,
                    help="JSON file overriding config defaults")
    ap.add_argument("--profile", default=None,
                    help="Named parameter family from profiles/ "
                         "(e.g. big_vertical, short_laps); "
                         "mutually exclusive with --config")
    ap.add_argument("--dem-units", choices=("meters", "feet"),
                    default="meters", help="Vertical units of the DEM")
    ap.add_argument("--min-drop-ft", type=float, default=None,
                    help="Override minimum elevation drop")
    ap.add_argument("--target-crs", default=None,
                    help="Analysis CRS override (e.g. EPSG:26911 for UTM 11N "
                         "outside Montana); stacks with --config/--profile")
    ap.add_argument("--zones", action="store_true",
                    help="Also emit start/end ZONE polygons with one "
                         "representative trail per zone pair")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.profile and args.config:
        ap.error("--profile and --config are mutually exclusive")
    config_path = args.config
    if args.profile:
        profile_path = (Path(__file__).resolve().parents[1] / "profiles"
                        / f"{args.profile}.json")
        if not profile_path.exists():
            available = sorted(p.stem for p in profile_path.parent.glob("*.json"))
            ap.error(f"unknown profile '{args.profile}'; available: "
                     f"{', '.join(available)}")
        config_path = profile_path

    cfg = Config.from_json(config_path) if config_path else Config()
    if args.min_drop_ft is not None:
        cfg.hard.min_drop_ft = args.min_drop_ft
    if args.target_crs:
        cfg.params.target_crs = args.target_crs
    cfg.validate()

    ranked = run(
        polygon_path=args.polygon,
        dem_path=args.dem,
        roads_path=args.roads,
        hydro_path=args.hydro,
        out_dir=args.out,
        cfg=cfg,
        terrain_path=args.terrain,
        dem_vertical_units=args.dem_units,
        zones=args.zones,
    )
    if not ranked:
        logging.warning("No candidate pairs passed the hard constraints.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
