"""FloPy adapter: case -> flopy.utils.triangle.Triangle -> VoronoiGrid.

FloPy grades cell size only through piecewise-constant ``maximum_area``
regions, so the spec size field is cut into nested bands, the way the USGS
examples nest buffer polygons. With levels ``H_k = h_min * BAND_RATIO**k``
(capped at ``h_max``), band k is where the spec size lies in [H_k, H_k+1) and
gets the area of an equilateral triangle whose edge is the band's geometric
mean size sqrt(H_k H_k+1), so cells straddle the spec across each band. Lines and points follow the Hughes et al. (2024) practice:
lines are densified at ``h_f`` and passed with points as fixed ``nodes``,
which puts generators (not faces) on the features. FloPy has no barriers.
Boundary lines (``boundary: true``) densify the domain ring they lie on instead.

FloPy passes Triangle either a global ``-a<area>`` or a bare ``-a``, and only
the bare flag makes Triangle read regional areas, so with features the
background is its own region (``maximum_area=None``).

Polygon order is what ``tri2vor`` expects: domain first, then one polygon per
hole, then the band boundaries. Only the parts of a band boundary inside the
domain are added, each as a degenerate out-and-back loop (how FloPy's own
tests pass internal lines), so no segment duplicates the domain boundary.

Where a band boundary grazes the domain boundary (or a hole), the piece
between them only cuts off a sliver, which Triangle's minimum angle fills with
tiny cells. Pieces that stay within ``SLIVER_FRACTION`` of the band size of the
domain boundary are left out, and the sliver they cut off gets no region seed,
so it joins its neighbour: what a user nesting buffer polygons by hand would do.
Likewise a band line that passes a fixed node closer than ``SLIVER_FRACTION`` of
the spec size there is routed through the node.

Written centres are the Triangle vertices, i.e. the generators.
"""

import time
from pathlib import Path

import numpy as np
import shapely
from flopy.utils.triangle import Triangle
from flopy.utils.voronoi import VoronoiGrid
from shapely.geometry import Polygon
from shapely.ops import nearest_points

from ..case import Case, line_parts, size_field
from ..grid import Grid

TOOL = "flopy"
ANGLE = 30.0          # minimum triangle angle used in all FloPy/MF6 Voronoi examples
BAND_RATIO = 2.0      # size ratio between nested refinement bands
RING_SIMPLIFY = 0.1   # ring simplification tolerance, as a fraction of the band size
SLIVER_FRACTION = 0.5  # band pieces thinner than this fraction of the band size are slivers


def build(case: Case, scale: float, ws: Path, triangle_exe: str) -> Grid:
    """Generate the FloPy Triangle/Voronoi grid for one case at one spec scale."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    # A global maximum_area makes FloPy pass "-a<value>" instead of a bare "-a",
    # and Triangle then ignores every regional area; with features the
    # background gets its own region instead.
    global_area = None if case.features else _tri_area(case.h_max * scale)
    nodes = _fixed_nodes(case, scale)
    tri = Triangle(model_ws=str(ws), exe_name=triangle_exe, angle=ANGLE, maximum_area=global_area,
                   nodes=nodes, additional_args=["-j"])
    lines, regions, n_bands = _plan_bands(case, scale)
    if nodes is not None:
        near = SLIVER_FRACTION * size_field(case, nodes, scale)
        lines = [_through_nodes(line, nodes, near) for line in lines]
    # Band lines end on the domain boundary; their end points become ring
    # vertices so Triangle sees them exactly on the ring (a point a rounding
    # error off a slanted segment breaks its segment recovery).
    ends = [line.coords[i] for line in lines for i in (0, -1)]
    tri.add_polygon(_boundary_ring(case.domain.exterior, case, scale, ends))
    for ring in case.domain.interiors:
        tri.add_polygon(_boundary_ring(ring, case, scale, ends))
        tri.add_hole(Polygon(ring).representative_point().coords[0])
    for line in lines:
        tri.add_polygon(_line_loop(line))
    for xy, attribute, area in regions:
        tri.add_region(xy, attribute=attribute, maximum_area=area)
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
        if f.boundary:
            continue                    # applied to the domain rings (_boundary_ring)
        for part in line_parts(f.geometry):
            dense = shapely.segmentize(part, f.h * scale)
            nodes.extend(dense.coords)
    if not nodes:
        return None
    return np.unique(np.round(np.asarray(nodes, dtype=float), 9), axis=0)


def _through_nodes(line, nodes: np.ndarray, distances: np.ndarray):
    """Line with the nodes that lie closer to it than ``distances`` inserted as vertices.

    A fixed node just off a band line leaves a gap that Triangle fills with tiny cells.
    """
    offsets = shapely.distance(line, shapely.points(nodes))
    near = [tuple(nodes[i]) for i in np.flatnonzero((offsets > 0) & (offsets < distances))]
    stations = [(line.project(shapely.Point(xy)), xy) for xy in near]
    stations = [(s, xy) for s, xy in stations if 0.0 < s < line.length]
    if not stations:
        return line
    vertices = [(line.project(shapely.Point(xy)), xy) for xy in line.coords]
    vertices[0], vertices[-1] = (0.0, vertices[0][1]), (line.length, vertices[-1][1])
    return shapely.LineString([xy for _, xy in sorted(vertices + stations)])


def _boundary_ring(ring, case: Case, scale: float, ends: list) -> list:
    """Ring coordinates with the band-line ``ends`` on it inserted as vertices, and
    segments under a boundary feature split to its size."""
    coords = list(ring.coords)
    boundary = [f for f in case.features_of("line") if f.boundary]
    tol = 1e-9 * max(np.ptp(np.asarray(case.domain.exterior.coords), axis=0))
    out = [coords[0]]
    for a, b in zip(coords[:-1], coords[1:]):
        segment = shapely.LineString([a, b])
        on = sorted({e for e in ends if segment.distance(shapely.Point(e)) <= tol and e not in (a, b)},
                    key=lambda e: segment.project(shapely.Point(e)))
        for p, q in zip([a] + on, on + [b]):
            piece = shapely.LineString([p, q])
            mid = piece.interpolate(0.5, normalized=True)
            sizes = [f.h * scale for f in boundary if f.geometry.distance(mid) <= tol]
            if sizes:
                piece = shapely.segmentize(piece, min(sizes))
            out.extend(list(piece.coords)[1:-1] + [q])
    return out


def _plan_bands(case: Case, scale: float) -> tuple:
    """Nested refinement band lines and region seeds: (lines, [(xy, attribute, area)], band count)."""
    lines, regions = [], []
    if not case.features:
        return lines, regions, 0
    interior = [f for f in case.features if not f.boundary]
    if not interior:
        return lines, regions, 0
    levels = _size_levels(min(f.h for f in interior) * scale, case.h_max * scale)
    n_bands, dropped = 0, []
    for k, (size, next_size) in enumerate(zip(levels[:-1], levels[1:])):
        outer = _within_size(case, scale, next_size).intersection(case.domain)
        if outer.is_empty:
            continue
        # the innermost band keeps the features themselves (a refinement polygon's interior)
        band = outer if k == 0 else outer.difference(_within_size(case, scale, size))
        sliver = SLIVER_FRACTION * size
        for part in _interior_boundary(outer, case.domain, RING_SIMPLIFY * size):
            if _hugs(part, case.domain.boundary, sliver):
                dropped.append((part, sliver))
            else:
                lines.append(part)
        for part in _seed_parts(band, dropped, case.domain.boundary):
            regions.append((part.representative_point().coords[0], n_bands, _tri_area(np.sqrt(size * next_size))))
        n_bands += 1
    background = case.domain.difference(_within_size(case, scale, levels[-1]))
    for part in _seed_parts(background, dropped, case.domain.boundary):
        regions.append((part.representative_point().coords[0], n_bands, _tri_area(case.h_max * scale)))
    return _split_at_contacts(lines, case.domain), regions, n_bands


def _split_at_contacts(lines: list, domain: Polygon) -> list:
    """Band lines split wherever they touch the domain boundary, with contacts snapped onto it,
    and without the pieces that run along it.

    A band can touch the boundary at a single point (a tangency), so a line
    passes within round-off of the boundary partway along. Triangle runs out of
    precision on such a near-contact. Every vertex within the ring tolerance of
    the boundary becomes a line end at one shared snapped point, which
    _boundary_ring then inserts as a ring vertex.
    """
    tol = 1e-9 * max(np.ptp(np.asarray(domain.exterior.coords), axis=0))
    snapped = []

    def snap(xy):
        point = shapely.Point(xy)
        if domain.boundary.distance(point) > tol:
            return None
        for known in snapped:
            if abs(known[0] - xy[0]) <= tol and abs(known[1] - xy[1]) <= tol:
                return known
        known = tuple(nearest_points(domain.boundary, point)[0].coords[0])
        snapped.append(known)
        return known

    out = []
    for line in lines:
        piece = []
        for xy in line.coords:
            contact = snap(xy)
            piece.append(contact or xy)
            if contact is not None and len(piece) > 1:
                out.append(shapely.LineString(piece))
                piece = [contact]
        if len(piece) > 1:
            out.append(shapely.LineString(piece))
    # Pieces running along the boundary (left by the difference in _interior_boundary
    # where a band outline and the ring have different vertices) would duplicate it.
    return [line for line in out if line.length > tol
            and domain.boundary.distance(line.interpolate(0.5, normalized=True)) > tol]


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
    Segments the difference leaves along the boundary (where the region outline
    and the ring have different vertices) are dropped before merging, or
    simplify would pull them a little inside the boundary and leave a sliver.
    """
    lines = region.boundary.difference(domain.boundary)
    if lines is None or lines.is_empty:
        return []
    tol = 1e-9 * max(np.ptp(np.asarray(domain.exterior.coords), axis=0))
    segments = [shapely.LineString(pair) for part in getattr(lines, "geoms", [lines])
                for pair in zip(part.coords[:-1], part.coords[1:])]
    segments = [s for s in segments if domain.boundary.distance(s.interpolate(0.5, normalized=True)) > tol]
    if not segments:
        return []
    lines = shapely.line_merge(shapely.MultiLineString(segments))
    parts = [g for g in getattr(lines, "geoms", [lines]) if g.length > tolerance]
    return [g.simplify(tolerance) for g in parts]


def _line_loop(line) -> list:
    """An open line as a degenerate closed loop (out and back), as FloPy's own tests pass lines."""
    coords = list(line.coords)
    return coords + coords[-2:0:-1]


def _within_size(case: Case, scale: float, size: float):
    """Area where the spec size is at most `size`: a union of feature buffers.

    Boundary lines are left out: FloPy sets a boundary size by densifying the
    ring (_boundary_ring), and Triangle grades away from it. Their buffers
    would run along the boundary and touch it tangentially, which Triangle
    cannot mesh.
    """
    buffers = [f.geometry.buffer((size - f.h * scale) / (case.growth - 1.0))
               for f in case.features if f.h * scale <= size and not f.boundary]
    return shapely.union_all(buffers)


def _hugs(line, boundary, distance: float) -> bool:
    """True if every point of ``line`` (sampled at distance / 2) is within ``distance`` of ``boundary``."""
    samples = shapely.points(shapely.get_coordinates(shapely.segmentize(line, distance / 2.0)))
    return bool(shapely.distance(boundary, samples).max() < distance)


def _seed_parts(geom, dropped: list, boundary) -> list:
    """Polygon parts that get a region seed: all but the slivers cut off by dropped pieces.

    A sliver touches a dropped (piece, sliver distance) and lies within that
    distance of the domain boundary; its neighbour across the piece does not.
    """
    return [part for part in _polygons(geom)
            if not any(part.distance(piece) < 1e-9 and _hugs(part.exterior, boundary, width)
                       for piece, width in dropped)]


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
