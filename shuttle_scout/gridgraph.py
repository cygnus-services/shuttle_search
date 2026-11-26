"""Grid-graph shortest paths on scipy.sparse.csgraph.

Replaces skimage.graph.MCP_Geometric (which SIGBUS-crashes intermittently
on Python 3.14/arm64 with numpy 2.5). Semantics are kept identical:

* nodes = grid cells, 8-connected;
* edge weight = euclidean step length (in meters) times the mean of the
  two endpoint cells' per-meter costs — exactly MCP_Geometric's metric;
* cells with cost >= IMPASSABLE (or non-finite) are excluded from the
  graph entirely.

Because scipy's Dijkstra is dense-graph-agnostic, callers keep it fast by
passing a ``mask`` restricting the graph to the cells that matter (near
roads for drive time, a corridor bbox for descent routes).
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra as _dijkstra

IMPASSABLE = 1.0e7  # per-meter cost treated as "never cross"

_OFFSETS = ((-1, -1), (-1, 0), (-1, 1), (0, -1))  # half; graph is undirected


class GridGraph:
    """CSR graph over the passable cells of ``mask`` (or the whole grid)."""

    def __init__(self, cost: np.ndarray, cell_size_m: float,
                 mask: np.ndarray | None = None):
        passable = np.isfinite(cost) & (cost < IMPASSABLE)
        if mask is not None:
            passable &= mask
        self.shape = cost.shape
        self.node_id = np.full(cost.shape, -1, dtype=np.int64)
        rr, cc = np.nonzero(passable)
        self.n = len(rr)
        self.node_id[rr, cc] = np.arange(self.n)
        self._rows, self._cols = rr, cc

        srcs, dsts, wts = [], [], []
        for dr, dc in _OFFSETS:
            a = _shift_valid(passable, dr, dc)
            if not a.any():
                continue
            b_r, b_c = np.nonzero(a)
            step = cell_size_m * (np.sqrt(2.0) if dr and dc else 1.0)
            srcs.append(self.node_id[b_r, b_c])
            dsts.append(self.node_id[b_r + dr, b_c + dc])
            wts.append(step * 0.5 * (cost[b_r, b_c].astype(np.float64)
                                     + cost[b_r + dr, b_c + dc]))
        if srcs:
            s = np.concatenate(srcs)
            d = np.concatenate(dsts)
            w = np.concatenate(wts)
        else:
            s = d = np.array([], dtype=np.int64)
            w = np.array([], dtype=np.float64)
        self.csr = coo_matrix((w, (s, d)), shape=(self.n, self.n)).tocsr()

    def node_of(self, row: int, col: int) -> int:
        """Node index for a cell, or -1 if the cell is not in the graph."""
        return int(self.node_id[row, col])

    def costs_from(self, row: int, col: int) -> np.ndarray | None:
        """Cumulative cost from one source cell to every graph node."""
        src = self.node_of(row, col)
        if src < 0:
            return None
        return _dijkstra(self.csr, directed=False, indices=src)

    def shortest_path(
        self, start: tuple[int, int], end: tuple[int, int]
    ) -> tuple[np.ndarray, np.ndarray, float] | None:
        """(path_rows, path_cols, total_cost) start->end, or None if
        either endpoint is off-graph or unreachable."""
        s, e = self.node_of(*start), self.node_of(*end)
        if s < 0 or e < 0:
            return None
        dist, pred = _dijkstra(self.csr, directed=False, indices=s,
                               return_predecessors=True)
        if not np.isfinite(dist[e]):
            return None
        nodes = [e]
        while nodes[-1] != s:
            p = pred[nodes[-1]]
            if p < 0:
                return None
            nodes.append(int(p))
        nodes.reverse()
        idx = np.array(nodes, dtype=np.int64)
        return self._rows[idx], self._cols[idx], float(dist[e])


def _shift_valid(passable: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """Cells that are passable AND whose (dr, dc) neighbor is passable."""
    out = np.zeros_like(passable)
    rows, cols = passable.shape
    r0, r1 = max(0, -dr), min(rows, rows - dr)
    c0, c1 = max(0, -dc), min(cols, cols - dc)
    out[r0:r1, c0:c1] = (passable[r0:r1, c0:c1]
                         & passable[r0 + dr:r1 + dr, c0 + dc:c1 + dc])
    return out
