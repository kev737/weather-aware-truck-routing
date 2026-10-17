"""Which forecast grid cells to download for a trip.

One set of cells serves both the checkpoints and the 0-48 h heatmap: the cells
the routes pass through, sampled every few miles. The free weather API allows
about 600 locations a minute, so the grid gets coarser on very long trips.
"""
from __future__ import annotations

from collections.abc import Iterable

from ..domain.geo import LatLon, point_at
from ..domain.route import Route
from .weather import Cell, snap

CELL_SIZES_DEG = (0.1, 0.15, 0.2, 0.3)  # 0.1 deg is ~7 mi, finer than the 10 mi checkpoint spacing
MAX_CELLS = 500
SAMPLE_STEP_MI = 5  # under a cell's width, so no cell along the road is skipped


def route_cells(routes: list[Route], cell_deg: float) -> set[Cell]:
    cells: set[Cell] = set()
    for route in routes:
        mile = 0.0
        while mile < route.distance_mi:
            cells.add(snap(point_at(route.coords, route.cum_mi, mile), cell_deg))
            mile += SAMPLE_STEP_MI
        cells.add(snap(route.coords[-1], cell_deg))
    return cells


def pick_grid(routes: list[Route]) -> tuple[float, set[Cell]]:
    """The finest grid that keeps the download under the budget, and its cells."""
    for size in CELL_SIZES_DEG:
        cells = route_cells(routes, size)
        if len(cells) <= MAX_CELLS:
            return size, cells
    return size, cells


def with_points(cells: set[Cell], points: Iterable[LatLon], cell_deg: float) -> set[Cell]:
    """Add the cells of specific points (the checkpoints) in case sampling missed one."""
    return cells | {snap(p, cell_deg) for p in points}
