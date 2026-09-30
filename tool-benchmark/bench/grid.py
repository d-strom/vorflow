"""Common grid form every adapter returns, and DISV conversion.

A ``Grid`` holds the DISV geometry *as the tool produces it*: shared vertices,
per-cell vertex lists (``iverts``) and the cell centres the tool writes to
``cell2d`` (``xc``). ``generators`` holds the true Voronoi generator points
when the tool exposes them. Metrics and MF6 runs can swap the centre
convention (written, generator or centroid) without touching the geometry:
the centre ablation in docs/benchmark-plan.md.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from flopy.utils.cvfdutil import to_cvfd
from shapely.geometry import Polygon

CENTRES = ("written", "generator", "centroid")


@dataclass
class Grid:
    tool: str
    vertices: np.ndarray            # (nvert, 2)
    iverts: list                    # per cell, vertex indices as the tool writes them
    xc: np.ndarray                  # (ncpl, 2) centres the tool writes to cell2d
    generators: np.ndarray | None   # (ncpl, 2) Voronoi generators, if known
    timings: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)

    @property
    def ncpl(self) -> int:
        return len(self.iverts)

    @property
    def nvert(self) -> int:
        return len(self.vertices)

    def centres(self, which: str) -> np.ndarray:
        """Cell centres for one convention: 'written', 'generator' or 'centroid'."""
        assert which in CENTRES, f"unknown centre convention {which!r}"
        if which == "written":
            return self.xc
        if which == "centroid":
            return shapely.get_coordinates(shapely.centroid(cell_polygons(self)))
        assert self.generators is not None, f"{self.tool}: generators not available"
        return self.generators


def open_rings(iverts: list) -> list:
    """Vertex lists without a repeated closing vertex."""
    return [list(r[:-1]) if len(r) > 1 and r[0] == r[-1] else list(r) for r in iverts]


def cell_polygons(grid: Grid) -> list:
    """Shapely polygon per cell."""
    return [Polygon(grid.vertices[ring]) for ring in open_rings(grid.iverts)]


def grid_from_polygons(tool: str, polygons: list, xc: np.ndarray, generators=None) -> Grid:
    """Build a Grid from cell polygons with FloPy's to_cvfd (the documented FloPy route).

    The hanging-node check is skipped: it is meant for quadtree grids and does
    not terminate on some Voronoi grids (vorflow's V4 barrier grid). Hanging
    nodes it would have patched show up as disconnected faces in the metrics.
    """
    vertdict = {i: list(p.exterior.coords) for i, p in enumerate(polygons)}
    verts, iverts = to_cvfd(vertdict, skip_hanging_node_check=True, verbose=False)
    return Grid(
        tool=tool,
        vertices=np.asarray(verts, dtype=float),
        iverts=[list(r) for r in iverts],
        xc=np.asarray(xc, dtype=float),
        generators=None if generators is None else np.asarray(generators, dtype=float),
    )


def to_gridprops(grid: Grid, centres: str = "written", origin=(0.0, 0.0)) -> dict:
    """DISV gridprops for flopy.mf6.ModflowGwfdisv, with the chosen centres.

    Coordinates are written relative to ``origin``. FloPy writes 9 significant
    digits, which at UTM northings is about 1 cm and can collapse short faces.
    """
    xy = grid.centres(centres) - origin
    vertices = [[i, float(x), float(y)] for i, (x, y) in enumerate(grid.vertices - origin)]
    cell2d = [
        [i, float(xy[i, 0]), float(xy[i, 1]), len(ring)] + [int(v) for v in ring]
        for i, ring in enumerate(grid.iverts)
    ]
    return {"ncpl": grid.ncpl, "nvert": grid.nvert, "vertices": vertices, "cell2d": cell2d}


def read_disv(path: Path, tool: str) -> Grid:
    """Read the VERTICES and CELL2D blocks of an MF6 DISV file."""
    blocks = _disv_blocks(Path(path).read_text().splitlines())
    vertices = np.array([[float(t) for t in row[1:3]] for row in blocks["VERTICES"]])
    iverts, xc = [], []
    for row in blocks["CELL2D"]:
        nv = int(row[3])
        xc.append([float(row[1]), float(row[2])])
        iverts.append([int(v) - 1 for v in row[4:4 + nv]])      # 1-based on file
    return Grid(tool=tool, vertices=vertices, iverts=iverts, xc=np.array(xc), generators=None)


def _disv_blocks(lines: list) -> dict:
    """Rows (token lists) of each BEGIN/END block, keyed by block name."""
    blocks, name = {}, None
    for line in lines:
        tokens = line.split("#")[0].replace(",", " ").split()
        if not tokens:
            continue
        key = tokens[0].upper()
        if key == "BEGIN":
            name = tokens[1].upper()
            blocks[name] = []
        elif key == "END":
            name = None
        elif name is not None:
            blocks[name].append(tokens)
    return blocks
