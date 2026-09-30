"""mf6Voronoi adapter: case -> shapefiles -> createVoronoi -> meshShape DISV.

Runs the documented workflow (``addLimit``, ``addLayer``,
``generateOrgDistVertices``, ``createPointCloud``, ``generateVoronoi``,
``getVoronoiAsShp``, ``meshShape.get_gridprops_disv``) on files, as a user
would. Features are grouped into one layer per (kind, h), since a layer has a
single ``layerRef``.

Spec translation: ``layerRef = h_f``, ``maxRef = h_max``. The ring-growth
``multiplier`` has no direct spec equivalent; ``MULTIPLIER`` is calibrated for
growth 1.2 by ``workflow.py`` (``calibrate=True``). mf6Voronoi has no barrier
support, so barrier lines are ordinary refinement lines. A case with no
features gets the domain boundary as a layer at ``h_max``, because the tool
needs at least one layer.

Written centres are polygon centroids (``meshShape``). Generators are the
point cloud ``modelDis['vertexTotal']``, matched to cells by point-in-polygon;
cells with no or several generators inside are counted in ``info``.
"""

import contextlib
import io
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon

from mf6Voronoi.geoVoronoi import createVoronoi
from mf6Voronoi.meshProperties import meshShape
from mf6Voronoi.utils import getVoronoiAsShp

from ..case import Case
from ..grid import Grid

TOOL = "mf6voronoi"
# Best fit to growth 1.2 on c0_point_grading (median |log h/h_spec| 0.09);
# the value most mf6Voronoi examples use, 1.5, grades much faster (0.46).
MULTIPLIER = 1.05


def build(case: Case, scale: float, ws: Path, multiplier: float = MULTIPLIER) -> Grid:
    """Generate the mf6Voronoi grid for one case at one spec scale."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    limit_path, layers = _write_inputs(case, ws)
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):         # tool prints banners and progress
        vor = createVoronoi(meshName=case.id, maxRef=case.h_max * scale, multiplier=multiplier)
        vor.addLimit("limit", str(limit_path))
        for name, (path, h) in layers.items():
            vor.addLayer(name, str(path), h * scale)
        vor.generateOrgDistVertices()
        vor.createPointCloud(verbose=False)
        vor.generateVoronoi()
        t1 = time.perf_counter()
        mesh_path = ws / "voronoi.shp"
        getVoronoiAsShp(vor.modelDis, shapePath=str(mesh_path))
        disv = meshShape(str(mesh_path)).get_gridprops_disv(save_spatial_index=False)
    t2 = time.perf_counter()
    grid = _to_grid(disv, np.asarray(vor.modelDis["vertexTotal"], dtype=float))
    grid.timings = {"mesh_s": t1 - t0, "export_s": t2 - t1}
    grid.info["multiplier"] = multiplier
    grid.info["boundary_layer_added"] = not case.features
    return grid


def _write_inputs(case: Case, ws: Path) -> tuple:
    """Write the limit and one shapefile per (kind, h) layer."""
    limit_path = ws / "limit.shp"
    gpd.GeoDataFrame(geometry=[case.domain], crs=case.crs).to_file(limit_path)
    groups = {}
    for f in case.features:
        groups.setdefault((f.kind, f.h), []).append(f.geometry)
    if not groups:
        # mf6Voronoi needs at least one layer (minRef is taken over layers);
        # a uniform grid is made by seeding the limit boundary at h_max.
        groups[("boundary", case.h_max)] = [case.domain.boundary]
    layers = {}
    for (kind, h), geoms in groups.items():
        name = f"{kind}_{h:g}".replace(".", "p")
        path = ws / f"{name}.shp"
        gpd.GeoDataFrame(geometry=geoms, crs=case.crs).explode(index_parts=False).to_file(path)
        layers[name] = (path, h)
    return limit_path, layers


def _to_grid(disv: dict, points: np.ndarray) -> Grid:
    """Grid from meshShape's disvDict; generators matched by point-in-polygon."""
    vertices = np.asarray(disv["uniqueVerticesList"], dtype=float)
    iverts = [list(row[4:4 + row[3]]) for row in disv["cell2d"]]
    xc = np.array([[row[1], row[2]] for row in disv["cell2d"]], dtype=float)
    generators, n_missing, n_multiple = _match_generators(vertices, iverts, points)
    grid = Grid(tool=TOOL, vertices=vertices, iverts=iverts, xc=xc, generators=generators)
    grid.info.update({"n_cells_without_generator": n_missing, "n_cells_multiple_generators": n_multiple})
    return grid


def _match_generators(vertices: np.ndarray, iverts: list, points: np.ndarray) -> tuple:
    """Generator inside each cell; cells without exactly one fall back to the written centroid."""
    cells = gpd.GeoDataFrame({"cell": np.arange(len(iverts))},
                             geometry=[Polygon(vertices[r]) for r in iverts])
    pts = gpd.GeoDataFrame({"pt": np.arange(len(points))},
                           geometry=gpd.points_from_xy(points[:, 0], points[:, 1]))
    hits = gpd.sjoin(pts, cells, predicate="within", how="inner")
    counts = hits.groupby("cell").size().reindex(cells["cell"], fill_value=0).to_numpy()
    generators = np.column_stack([cells.centroid.x, cells.centroid.y])
    single = hits[hits["cell"].map(lambda c: counts[c] == 1)]
    generators[single["cell"].to_numpy()] = points[single["pt"].to_numpy()]
    return generators, int(np.sum(counts == 0)), int(np.sum(counts > 1))
