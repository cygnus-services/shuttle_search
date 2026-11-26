"""Central configuration for Shuttle Scout.

Every threshold, weight, and physical assumption lives here. User-facing
fields are in feet / miles / minutes / mph; derived ``*_m`` / ``*_s``
properties convert to the meters/seconds used internally. Nothing outside
this module (and units.py) is allowed to hard-code a unit conversion.

Load order: defaults -> optional JSON overrides (``Config.from_json``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import units

log = logging.getLogger(__name__)


@dataclass
class HardConstraints:
    """Pass/fail gates. A start/end pair failing ANY of these is discarded."""

    # Vertical drop from start to end. Default 1000 ft, floor 300 ft.
    min_drop_ft: float = 1000.0
    min_drop_floor_ft: float = 300.0  # refuse to configure below this
    # Optional cap — lap-oriented profiles set e.g. 1000 ft so epic
    # descents don't crowd out quick repeatable runs. inf = no cap.
    max_drop_ft: float = float("inf")

    # Average grade over the straight-line run between start and end.
    min_avg_grade_pct: float = 6.0
    max_avg_grade_pct: float = 100.0

    # Shuttle drive between the two road-access points.
    max_drive_time_min: float = 45.0

    # Terrain steepness. Steep IS good for gravity riding, so these are
    # deliberately permissive:
    #   <= max_rideable_slope_pct        : normal rideable tread
    #   between the two                  : very steep, strongly avoided but
    #                                      allowed if it's the only way
    #   >= cliff_slope_pct               : treated as a cliff cell (see below)
    # NOTE (10 m DEM limitation): slope alone cannot tell a 20 ft vertical
    # cliff from a sustained steep chute — a 6 m drop is smeared across a
    # 10 m cell either way. So a cliff cell is NOT hard-forbidden; it is
    # extreme-cost, and the router still tries to find a ride-around. A pair
    # is only rejected when the cliff band fully blocks the corridor (no way
    # around) — which is exactly "a cliff with no ride-around". For true
    # cliff detection, feed a 1 m lidar DEM (USGS 3DEP has it for both
    # regions); see docs/DATA_SOURCING.md.
    max_rideable_slope_pct: float = 100.0  # ~45 deg; rideable-steep below
    cliff_slope_pct: float = 150.0         # ~56 deg; extreme-cost cliff cell
    water_buffer_ft: float = 100.0        # keep routes this far from mapped water

    # Geometry sanity for a pair (straight-line horizontal separation).
    min_pair_distance_mi: float = 0.25
    max_pair_distance_mi: float = 8.0

    # Anti-degenerate-shuttle gates. A pair whose start and end anchor to
    # the same spot on a road is not a shuttle (the "drive" is seconds);
    # requiring a real drive distance kills those without forbidding
    # legitimate same-road shuttles, which drive the road's full length.
    min_shuttle_drive_mi: float = 0.4
    # And the descent must dwarf the road-access walks: route length >=
    # this multiple of (start road distance + end road distance).
    min_route_to_access_ratio: float = 3.0
    # Hard cap on intermediate climbing along the best route (feet).
    # inf = no cap; DH-oriented profiles set a few hundred feet.
    max_route_climb_ft: float = float("inf")
    # A descent must not run PAST its own pickup: if the route comes within
    # X of a drivable road partway down and then continues to an end that is
    # farther from the road, the rider would have to hike back up. Reject
    # when that overshoot exceeds this many feet.
    max_pickup_overshoot_ft: float = 300.0
    # Route-vs-road behaviour. Crossings/al ong-distance are measured on the
    # MIDDLE of the route only: arriving at (or departing from) a road is the
    # whole point, so the first and last road runs are always free.
    #   max_route_road_crossings - how many roads it may cross mid-descent
    #   max_road_along_ft        - longest mid-route stretch ON a road, i.e.
    #                              how far it may ride the road itself
    max_route_road_crossings: float = 1
    max_road_along_ft: float = 200.0

    @property
    def min_drop_m(self) -> float:
        return units.feet_to_meters(self.min_drop_ft)

    @property
    def max_drop_m(self) -> float:
        return units.feet_to_meters(self.max_drop_ft)

    @property
    def water_buffer_m(self) -> float:
        return units.feet_to_meters(self.water_buffer_ft)

    @property
    def min_pair_distance_m(self) -> float:
        return units.miles_to_meters(self.min_pair_distance_mi)

    @property
    def max_pair_distance_m(self) -> float:
        return units.miles_to_meters(self.max_pair_distance_mi)

    @property
    def max_drive_time_s(self) -> float:
        return self.max_drive_time_min * 60.0

    @property
    def min_shuttle_drive_m(self) -> float:
        return units.miles_to_meters(self.min_shuttle_drive_mi)

    def validate(self) -> None:
        if self.min_drop_ft < self.min_drop_floor_ft:
            raise ValueError(
                f"min_drop_ft={self.min_drop_ft} is below the floor of "
                f"{self.min_drop_floor_ft} ft"
            )
        if self.max_drop_ft <= self.min_drop_ft:
            raise ValueError("max_drop_ft must exceed min_drop_ft")
        if not (0 < self.min_avg_grade_pct < self.max_avg_grade_pct):
            raise ValueError("grade window must satisfy 0 < min < max")
        if self.max_drive_time_min <= 0:
            raise ValueError("max_drive_time_min must be positive")
        if self.min_pair_distance_mi >= self.max_pair_distance_mi:
            raise ValueError("pair distance window must satisfy min < max")


@dataclass
class ScoringWeights:
    """Relative weights for the soft ranking. Normalized before use, so only
    ratios matter. Elevation drop and road access deliberately dominate."""

    drop_efficiency: float = 0.30   # extra drop beyond the minimum
    access_proximity: float = 0.25  # start & end near a road (<= 1/4 mi ideal)
    intermediate_climb: float = 0.15  # penalize climbing/saddles mid-route
    grade_consistency: float = 0.15   # steady descent beats spiky one
    drivability: float = 0.15         # shorter shuttle drive scores higher

    def normalized(self) -> dict[str, float]:
        vals = {f.name: getattr(self, f.name) for f in fields(self)}
        total = sum(vals.values())
        if total <= 0:
            raise ValueError("scoring weights must sum to a positive number")
        return {k: v / total for k, v in vals.items()}


@dataclass
class Params:
    """Everything that is neither a hard gate nor a scoring weight."""

    # CRS all data is reprojected to. EPSG:26912 (NAD83 / UTM 12N, meters)
    # covers all of Montana adequately for this analysis.
    target_crs: str = "EPSG:26912"

    # Working resolution. 10 m matches the USGS 1/3 arc-second DEM.
    cell_size_m: float = 10.0

    # Candidate generation.
    road_access_mi: float = 0.25       # point must be within this of a road
    local_extrema_window_m: float = 500.0  # window for peak/valley detection
    max_start_candidates: int = 60
    max_end_candidates: int = 60
    # Minimum spacing between selected candidates of the same kind (0 = off).
    # Spreads the pool geographically instead of stacking the highest ridge.
    candidate_min_spacing_m: float = 0.0
    # Also seed START and END candidates directly on the drivable road
    # network, thinned to this spacing (0 = off). Local peaks/valleys alone
    # miss the most sought-after shape: a road-accessed high point, a ridge
    # descent, and any road below it. Road-seeded starts exclude local
    # minima; road-seeded ends exclude local maxima. ON by default.
    road_endpoint_spacing_m: float = 500.0
    # Road-seeded candidates may sit on any road class up to this (5 = all).
    # A paved street is a perfectly good pickup; what must be prevented is
    # the ROUTE riding along roads or threading street crossings to reach
    # it — see hard.max_route_road_crossings / hard.max_road_along_ft.
    road_endpoint_max_class: int = 5

    # Zone model (see shuttle_scout/zones.py): candidate points within
    # zone_radius_m of each other are one drop-off/pickup ZONE; the zone
    # polygon is their union buffered by zone_buffer_m. Two connections
    # between the same zone pair are kept separately only if their routes
    # diverge by more than zone_route_separation_m (different ridge).
    zone_radius_m: float = 400.0     # cluster + road-search radius
    zone_buffer_m: float = 250.0     # zone radius around the centroid —
                                     # ~500 m across, i.e. "drop in anywhere
                                     # along this quarter mile of road"
    zone_route_separation_m: float = 300.0
    max_ranked_pairs: int = 10         # keep this many pairs after ranking
    routes_per_pair: int = 2           # 1-2 example descent routes per pair

    # Drive-time model: least-cost path over the road network with a speed
    # per road TYPE class (burned into roads.tif by scripts/prepare_data.py).
    # avg_shuttle_speed_mph is the reference speed a cost of 1.0 represents;
    # per-class cost multipliers are avg / class speed.
    avg_shuttle_speed_mph: float = 25.0
    drive_speed_highway_mph: float = 50.0   # class 5
    drive_speed_paved_mph: float = 35.0     # class 4
    drive_speed_gravel_mph: float = 22.0    # class 3
    drive_speed_dirt_mph: float = 11.0      # class 2 (high clearance)
    drive_speed_jeep_mph: float = 5.0       # class 1 (barely a road)
    # Road stretches whose WINDOWED average grade exceeds this are treated
    # as non-drivable (no speed benefit, no road-access credit). Grade is
    # estimated as (max - min road elevation in window) / window diameter,
    # which overestimates on tight switchbacks — deliberately conservative.
    max_road_grade_pct: float = 20.0
    road_grade_window_m: float = 250.0
    offroad_gap_tolerance_m: float = 60.0  # bridge small gaps in road raster
    drive_offroad_penalty: float = 8.0     # cost multiplier off mapped roads
    def road_class_costs(self) -> dict[int, float]:
        """Per-meter drive-cost multiplier for each road type class."""
        return {
            cls: self.avg_shuttle_speed_mph / mph
            for cls, mph in (
                (5, self.drive_speed_highway_mph),
                (4, self.drive_speed_paved_mph),
                (3, self.drive_speed_gravel_mph),
                (2, self.drive_speed_dirt_mph),
                (1, self.drive_speed_jeep_mph),
            )
        }

    # Descent-routing cost surface shaping.
    # Routes may only use cells within a corridor around the pair's
    # straight line: width = max(factor * run, min). Stops "descents" that
    # wander miles through unrelated gentle terrain.
    route_corridor_factor: float = 0.75
    route_corridor_min_m: float = 1200.0
    # Riding cost multiplier inside a buffered STREAM corridor: high
    # enough that routes cross perpendicular and never follow the creek,
    # low enough that a mapped intermittent gully doesn't kill a descent.
    # (Waterbodies - lakes, river polygons - stay strictly impassable.)
    stream_crossing_cost: float = 30.0
    route_uphill_penalty: float = 12.0   # per meter climbed along a route
    route_hazard_cost: float = 1.0e6     # effectively impassable
    # Additive per-cell route cost for crossing better-than-dirt roads, so
    # descents cross them perpendicular and never run through a neighborhood.
    # Dirt (2) and jeep (1) roads are free tread. 0 = ignore roads entirely.
    route_cliff_cost: float = 800.0         # per-cell cost to cross a cliff
    route_cross_gravel_cost: float = 12.0   # class 3
    route_cross_paved_cost: float = 60.0    # class 4-5 (incl. residential)
    route_alternate_corridor_m: float = 150.0  # 2nd route must leave this corridor
    route_alternate_penalty: float = 5.0       # cost multiplier inside corridor

    # Terrain classification raster: cell values considered unrideable
    # (e.g. NLCD 11 open water, 12 perennial ice/snow, 23/24 developed).
    unrideable_terrain_classes: tuple[int, ...] = (11, 12, 23, 24)

    @property
    def road_access_m(self) -> float:
        return units.miles_to_meters(self.road_access_mi)

    @property
    def avg_shuttle_speed_mps(self) -> float:
        return units.mph_to_mps(self.avg_shuttle_speed_mph)


@dataclass
class ScoringRamps:
    """Ramped (piecewise-linear) scoring curves — the soft side of a
    parameter family. Each factor scores 1.0 inside its preferred zone and
    ramps linearly to 0.0 at the corresponding hard limit:

        drop:        0 at hard.min_drop_ft  -> 1 at drop_full_credit_ft
        access:      1 within access_preferred_ft -> 0 at params.road_access_mi
        drivability: 1 under drive_preferred_min  -> 0 at hard.max_drive_time_min
        climb:       1 at zero climb -> 0 at climb_tolerance_fraction of drop
        consistency: 1 at grade std 0 -> 0 at grade_std_ceiling_pct

    Absolute ramps (not cohort-relative), so scores are comparable across
    runs, areas, and profiles.
    """

    drop_full_credit_ft: float = 2500.0
    access_preferred_ft: float = 250.0
    drive_preferred_min: float = 15.0
    climb_tolerance_fraction: float = 0.25
    # ABSOLUTE climb ramp (feet): full credit at 0, zero at this value.
    # inf = disabled (only the fraction-of-drop ramp applies). The climb
    # score is the MIN of the two ramps.
    climb_tolerance_ft: float = float("inf")
    grade_std_ceiling_pct: float = 30.0

    @property
    def access_preferred_m(self) -> float:
        return units.feet_to_meters(self.access_preferred_ft)

    def validate(self, hard: HardConstraints, params: Params) -> None:
        if self.drop_full_credit_ft <= hard.min_drop_ft:
            raise ValueError(
                "ramps.drop_full_credit_ft must exceed hard.min_drop_ft")
        if units.feet_to_meters(self.access_preferred_ft) > params.road_access_m:
            raise ValueError(
                "ramps.access_preferred_ft must not exceed params.road_access_mi")
        if self.drive_preferred_min > hard.max_drive_time_min:
            raise ValueError(
                "ramps.drive_preferred_min must not exceed hard.max_drive_time_min")
        if not (0 < self.climb_tolerance_fraction <= 1):
            raise ValueError("ramps.climb_tolerance_fraction must be in (0, 1]")
        if self.grade_std_ceiling_pct <= 0:
            raise ValueError("ramps.grade_std_ceiling_pct must be positive")


@dataclass
class Config:
    hard: HardConstraints = field(default_factory=HardConstraints)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    params: Params = field(default_factory=Params)
    ramps: ScoringRamps = field(default_factory=ScoringRamps)

    def validate(self) -> None:
        self.hard.validate()
        self.weights.normalized()  # raises if degenerate
        self.ramps.validate(self.hard, self.params)
        if self.params.cell_size_m <= 0:
            raise ValueError("cell_size_m must be positive")
        log.info(
            "Config OK: min drop %.0f ft, grade %.0f-%.0f%%, drive <= %.0f min, "
            "road access <= %.2f mi",
            self.hard.min_drop_ft,
            self.hard.min_avg_grade_pct,
            self.hard.max_avg_grade_pct,
            self.hard.max_drive_time_min,
            self.params.road_access_mi,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        """Build a Config from defaults overridden by a JSON file shaped like
        ``{"hard": {...}, "weights": {...}, "params": {...}}`` — any subset."""
        raw = json.loads(Path(path).read_text())
        cfg = cls()
        for section_name, target in (
            ("hard", cfg.hard),
            ("weights", cfg.weights),
            ("params", cfg.params),
            ("ramps", cfg.ramps),
        ):
            for key, value in raw.get(section_name, {}).items():
                if not hasattr(target, key):
                    raise KeyError(f"unknown config key {section_name}.{key}")
                if key == "unrideable_terrain_classes":
                    value = tuple(value)
                setattr(target, key, value)
        cfg.validate()
        return cfg
