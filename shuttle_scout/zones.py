"""Zone model: collapse point-pairs into start/end ZONES + one trail each.

A shuttle drop-off is not a GPS point — it is "anywhere along this stretch
of road at the top", and the pickup is "anywhere along this bit of valley
road". The point model is an artifact of extrema detection: the exact 10 m
cell we call "the start" is the argmax of a neighborhood and is arbitrary
at the +/-100 m level.

This module groups the surviving pairs into (start zone -> end zone)
relationships and keeps ONE representative connection per relationship,
which natively replaces three hacks in the point pipeline: the dedup
radius, the aggregated "#3 · #8 · #10" labels, and much of the
degenerate-pair filtering.

Distinct-trail guard: two different ridges dropping off the same summit
zone into the same valley zone are two trails, not one. A second (third,
...) connection is kept when its route diverges from the accepted one by
more than ``zone_route_separation_m`` at its point of maximum departure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from .candidates import CandidatePair
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Zone:
    kind: str                     # "start" | "end"
    index: int                    # 1-based id within its kind
    points: list                  # CandidatePoint members
    polygon: object               # shapely geometry (target CRS)

    @property
    def elev_range_ft(self) -> tuple[float, float]:
        from . import units
        e = [units.meters_to_feet(p.elev_m) for p in self.points]
        return (min(e), max(e))


@dataclass
class ZoneConnection:
    start_zone: Zone
    end_zone: Zone
    best: CandidatePair           # representative (highest scoring) pair
    n_collapsed: int              # how many point-pairs this zone-pair had
    extra: list = field(default_factory=list)  # genuinely distinct siblings
    rank: int | None = None


def _cluster(points: list, radius_m: float) -> list[list[int]]:
    """Single-linkage spatial clustering (union-find). O(n^2), n is small."""
    n = len(points)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            if np.hypot(points[i].x - points[j].x,
                        points[i].y - points[j].y) <= radius_m:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _route_divergence_m(a: CandidatePair, b: CandidatePair,
                        cell_size_m: float) -> float:
    """Max separation between two pairs' primary routes, sampled coarsely."""
    if not a.routes or not b.routes:
        return float("inf")
    ra, rb = a.routes[0], b.routes[0]
    n = 12
    ia = np.linspace(0, len(ra.rows) - 1, n).astype(int)
    ib = np.linspace(0, len(rb.rows) - 1, n).astype(int)
    d = np.hypot(ra.rows[ia] - rb.rows[ib], ra.cols[ia] - rb.cols[ib])
    return float(d.max()) * cell_size_m


def _road_xy(data, terrain) -> np.ndarray:
    """(N, 2) map coords of every drivable road cell, vectorized."""
    rows, cols = np.nonzero(terrain.drivable_roads)
    t = data.grid.transform
    x = t.c + (cols + 0.5) * t.a
    y = t.f + (rows + 0.5) * t.e
    return np.column_stack([x, y])


def _zone_polygon(members, road_xy: np.ndarray, cfg: Config):
    """Zone geometry: a circle on the CENTROID of the clustered candidates,
    sized to cover them plus a margin.

    Deliberately simple — the zone communicates "drop in around here",
    and the exact outline carries no information the search actually used."""
    p = cfg.params
    cx = float(np.mean([m.x for m in members]))
    cy = float(np.mean([m.y for m in members]))
    spread = max((np.hypot(m.x - cx, m.y - cy) for m in members), default=0.0)
    return Point(cx, cy).buffer(float(spread) + p.zone_buffer_m)


def build_zones(pairs: list[CandidatePair], cfg: Config,
                cell_size_m: float, data=None, terrain=None
                ) -> list[ZoneConnection]:
    """Group scored pairs into zone-to-zone connections, best-first."""
    if not pairs:
        return []
    p = cfg.params
    radius = p.zone_radius_m

    starts = list({(pr.start.row, pr.start.col): pr.start for pr in pairs}.values())
    ends = list({(pr.end.row, pr.end.col): pr.end for pr in pairs}.values())
    s_groups = _cluster(starts, radius)
    e_groups = _cluster(ends, radius)

    road_xy = (_road_xy(data, terrain)
               if data is not None and terrain is not None
               else np.empty((0, 2)))

    def make_zones(pts, groups, kind) -> tuple[list[Zone], dict]:
        zones, lookup = [], {}
        for i, g in enumerate(groups, start=1):
            members = [pts[k] for k in g]
            poly = _zone_polygon(members, road_xy, cfg)
            z = Zone(kind=kind, index=i, points=members, polygon=poly)
            zones.append(z)
            for m in members:
                lookup[(m.row, m.col)] = z
        return zones, lookup

    _, s_lookup = make_zones(starts, s_groups, "start")
    _, e_lookup = make_zones(ends, e_groups, "end")

    # Group pairs by the zone relationship they express.
    buckets: dict[tuple[int, int], list[CandidatePair]] = {}
    for pr in pairs:
        key = (s_lookup[(pr.start.row, pr.start.col)].index,
               e_lookup[(pr.end.row, pr.end.col)].index)
        buckets.setdefault(key, []).append(pr)

    conns: list[ZoneConnection] = []
    for (si, ei), group in buckets.items():
        group.sort(key=lambda q: q.score or 0.0, reverse=True)
        best = group[0]
        # Keep siblings whose route genuinely diverges (different ridge).
        extra = []
        for cand in group[1:]:
            if all(_route_divergence_m(cand, k, cell_size_m)
                   > p.zone_route_separation_m for k in [best, *extra]):
                extra.append(cand)
        conns.append(ZoneConnection(
            start_zone=s_lookup[(best.start.row, best.start.col)],
            end_zone=e_lookup[(best.end.row, best.end.col)],
            best=best, n_collapsed=len(group), extra=extra,
        ))

    conns.sort(key=lambda c: c.best.score or 0.0, reverse=True)
    conns = conns[:p.max_ranked_pairs]
    for i, c in enumerate(conns, start=1):
        c.rank = i
        c.best.rank = i
        for j, x in enumerate(c.extra):
            x.rank = i
    log.info(
        "Zones: %d start zones, %d end zones -> %d connections "
        "(from %d point-pairs; %d distinct siblings kept)",
        len(s_groups), len(e_groups), len(conns), len(pairs),
        sum(len(c.extra) for c in conns),
    )
    return conns
