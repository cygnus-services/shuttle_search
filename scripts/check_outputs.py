"""Validate that an output folder is internally consistent.

Catches the failure mode that is hardest to spot in QGIS: layers written by
different code paths (or left over from a crashed run) disagreeing about
rank numbering, so a connection appears to have no shuttle drive when it is
really just numbered differently. That looks like missing data on the map
and is very slow to debug visually.

    python scripts/check_outputs.py out/*_zones out/anaconda
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def ranks(path: Path) -> set:
    if not path.exists():
        return set()
    feats = json.loads(path.read_text())["features"]
    return {f["properties"].get("rank") for f in feats
            if f["properties"].get("rank") is not None}


def check(folder: Path) -> list[str]:
    problems = []
    zone_mode = (folder / "zone_routes.geojson").exists()
    trails = ranks(folder / ("zone_routes.geojson" if zone_mode
                             else "routes.geojson"))
    drives = ranks(folder / "drive_routes.geojson")
    if not trails:
        return [f"{folder.name}: no trail layer found"]
    if not trails <= drives:
        problems.append(
            f"{folder.name}: connections {sorted(trails - drives)} have no "
            f"drive route (trails={sorted(trails)} drives={sorted(drives)})")

    if zone_mode:
        for layer in ("start_zones", "end_zones"):
            zr = ranks(folder / f"{layer}.geojson")
            if zr and not zr <= trails:
                problems.append(
                    f"{folder.name}: {layer} has ranks {sorted(zr - trails)} "
                    "with no matching trail")
        stale = [p.name for p in folder.glob("*points*.geojson")]
        if stale:
            problems.append(f"{folder.name}: stale point layers present: "
                            f"{stale} (zone mode should not emit them)")

    # Files written more than a few minutes apart usually mean a crashed or
    # partial run left the folder mixing two generations of results.
    times = [p.stat().st_mtime for p in folder.glob("*.geojson")]
    if times and max(times) - min(times) > 300:
        problems.append(f"{folder.name}: layers written {int(max(times) - min(times))}s "
                        "apart — folder may mix two runs; re-run it")
    return problems


def main(argv=None) -> int:
    args = (argv or sys.argv[1:]) or [str(p) for p in Path("out").iterdir()
                                      if p.is_dir()]
    all_problems = []
    for a in args:
        f = Path(a)
        if f.is_dir():
            all_problems += check(f)
    if all_problems:
        print("PROBLEMS:")
        for p in all_problems:
            print("  -", p)
        return 1
    print(f"OK: {len(args)} folder(s) internally consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
