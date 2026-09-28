"""FloPy adapter: case -> flopy.utils.triangle.Triangle -> VoronoiGrid.

FloPy grades cell size only through piecewise-constant ``maximum_area``
regions, so the spec size field is cut into nested bands, the way the USGS
examples nest buffer polygons. With levels ``H_k = h_min * BAND_RATIO**k``
(capped at ``h_max``), band k is where the spec size lies in [H_k, H_k+1) and
gets the area of an equilateral triangle whose edge is the band's geometric
mean size sqrt(H_k H_k+1), so cells straddle the spec across each band. Lines and points follow the Hughes et al. (2024) practice:
lines are densified at ``h_f`` and passed with points as fixed ``nodes``,
which puts generators (not faces) on the features. FloPy has no barriers.

FloPy passes Triangle either a global ``-a<area>`` or a bare ``-a``, and only
the bare flag makes Triangle read regional areas, so with features the
background is its own region (``maximum_area=None``).

Polygon order is what ``tri2vor`` expects: domain first, then one polygon per
hole, then the band boundaries. Only the parts of a band boundary inside the
domain are added, each as a degenerate out-and-back loop (how FloPy's own
tests pass internal lines), so no segment duplicates the domain boundary.

Written centres are the Triangle vertices, i.e. the generators.
"""

import time
from pathlib import Path

import numpy as np
import shapely
from flopy.utils.triangle import Triangle
from flopy.utils.voronoi import VoronoiGrid
from shapely.geometry import Polygon

from ..case import Case, line_parts
from ..grid import Grid

TOOL = "flopy"
ANGLE = 30.0          # minimum triangle angle used in all FloPy/MF6 Voronoi examples
BAND_RATIO = 2.0      # size ratio between nested refinement bands
RING_SIMPLIFY = 0.1   # ring simplification tolerance, as a fraction of the band size


def build(case: Case, scale: float, ws: Path, triangle_exe: str) -> Grid:
    """Generate the FloPy Triangle/Voronoi grid for one case at one spec scale."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    # A global maximum_area makes FloPy pass "-a<value>" instead of a bare "-a",
    # and Triangle then ignores every regional area; with features the
    # background gets its own region instead.
    global_area = None if case.features else _tri_area(case.h_max * scale)
    tri = Triangle(model_ws=str(ws), exe_name=triangle_exe, angle=ANGLE, maximum_area=global_area,
                   nodes=_fixed_nodes(case, scale), additional_args=["-j"])
    tri.add_polygon(list(case.domain.exterior.coords))
    for ring in case.domain.interiors:
        tri.add_polygon(list(ring.coords))
        tri.add_hole(Polygon(ring).representative_point().coords[0])
    n_bands = _add_bands(tri, case, scale)
    tri.build(verbose=False)
    vor = VoronoiGrid(tri)
    t1 = time.perf_counter()
    gp = vor.get_disv_gridprops()
    t2 = time.perf_counter()
    grid = _to_grid(gp)
    grid.timings = {"mesh_s": t1 - t0, "export_s": t2 - t1}
    grid.info["n_bands"] = n_bands
    return grid


def _tri_area(h: float) -> float:
    """Area of an equilateral triangle with edge h."""
    return np.sqrt(3.0) / 4.0 * h * h


def _fixed_nodes(case: Case, scale: float) -> np.ndarray | None:
    """Point features plus lines densified at their target size."""
    nodes = [f.geometry.coords[0] for f in case.features_of("point")]
    for f in case.features_of("line"):
        for part in line_parts(f.geometry):
            dense = shapely.segmentize(part, f.h * scale)
            nodes.extend(dense.coords)
    if not nodes:
        return None
    return np.unique(np.round(np.asarray(nodes, dtype=float), 9), axis=0)


def _add_bands(tri: Triangle, case: Case, scale: float) -> int:
    """Add nested refinement band boundaries and region seeds; return the band count."""
    if not case.features:
        return 0
    levels = _size_levels(min(f.h for f in case.features) * scale, case.h_max * scale)
    n_bands = 0
    for k, (size, next_size) in enumerate(zip(levels[:-1], levels[1:])):
        outer = _within_size(case, scale, next_size).intersection(case.domain)
        if outer.is_empty:
            continue
        # the innermost band keeps the features themselves (a refinement polygon's interior)
        band = outer if k == 0 else outer.difference(_within_size(case, scale, size))
        for part in _interior_boundary(outer, case.domain, RING_SIMPLIFY * size):
            tri.add_polygon(_line_loop(part))
        for part in _polygons(band):
            tri.add_region(part.representative_point().coords[0], attribute=n_bands,
                           maximum_area=_tri_area(np.sqrt(size * next_size)))
        n_bands += 1
    background = case.domain.difference(_within_size(case, scale, levels[-1]))
    for part in _polygons(background):
        tri.add_region(part.representative_point().coords[0], attribute=n_bands,
                       maximum_area=_tri_area(case.h_max * scale))
    return n_bands


def _size_levels(h_min: float, h_max: float) -> list:
    """Band size ladder h_min * BAND_RATIO**k below h_max, then h_max."""
    levels = [h_min]
    while levels[-1] * BAND_RATIO < h_max:
        levels.append(levels[-1] * BAND_RATIO)
    return levels + [h_max]


def _interior_boundary(region, domain: Polygon, tolerance: float) -> list:
    """Parts of a region's boundary inside the domain, ending exactly on its boundary.

    The exact difference keeps the endpoints on the domain boundary, so the band
    lines close off their regions; a gap there lets Triangle's region fill leak.
    """
    lines = region.boundary.difference(domain.boundary)
    if lines is None or lines.is_empty:
        return []
    lines = shapely.line_merge(lines) if lines.geom_type == "MultiLineString" else lines
    parts = [g for g in getattr(lines, "geoms", [lines]) if g.length > tolerance]
    return [g.simplify(tolerance) for g in parts]


def _line_loop(line) -> list:
    """An open line as a degenerate closed loop (out and back), as FloPy's own tests pass lines."""
    coords = list(line.coords)
    return coords + coords[-2:0:-1]


def _within_size(case: Case, scale: float, size: float):
    """Area where the spec size is at most `size`: a union of feature buffers."""
    buffers = [f.geometry.buffer((size - f.h * scale) / (case.growth - 1.0))
               for f in case.features if f.h * scale <= size]
    return shapely.union_all(buffers)


def _polygons(geom) -> list:
    """Polygon parts of a geometry, largest first, ignoring slivers."""
    parts = [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon) and not g.is_empty]
    return sorted(parts, key=lambda g: g.area, reverse=True)


def _to_grid(gp: dict) -> Grid:
    """Grid from VoronoiGrid DISV gridprops; xc, yc are the generators."""
    vertices = np.array([[v[1], v[2]] for v in gp["vertices"]], dtype=float)
    iverts = [list(c[4:4 + c[3]]) for c in gp["cell2d"]]
    xc = np.array([[c[1], c[2]] for c in gp["cell2d"]], dtype=float)
    return Grid(tool=TOOL, vertices=vertices, iverts=iverts, xc=xc, generators=xc.copy())
