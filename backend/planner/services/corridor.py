"""The heatmap's forecast grid: the cells the routes pass through.

The finest grid that fits the budget is used (7 mi on most trips). On the free
weather tier the budget is small, so very long trips get a coarser heatmap; a paid
key raises it (HEATMAP_MAX_CELLS). Checkpoints don't use this grid: they're read
at their own locations.
"""
from __future__ import annotations

from django.conf import settings

from ..domain.geo import point_at
from ..domain.route import Route
from .weather import Cell, snap

CELL_SIZES_DEG = (0.1, 0.15, 0.2, 0.3)  # 0.1 deg is ~7 mi
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
        if len(cells) <= settings.HEATMAP_MAX_CELLS:
            return size, cells
    return size, cells
