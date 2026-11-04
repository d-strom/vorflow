"""Compare Lloyd caps on identical generators; timings exclude metric calculation."""
import json
import sys
from time import perf_counter

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, box
from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator, set_verbosity
from vorflow.fields import GeometricGrowthField
from vorflow.utils import build_connectivity


def feature(size):
    return dict(resolution=size, fields=[GeometricGrowthField(
        growth_factor=1.1, growth_model="continuous_metric", sampling=25)])


def setup(case):
    cm = ConceptualMesh(crs="EPSG:3006")
    domain = box(0, 0, 640, 420)
    if case == "concave":
        domain = Polygon([(0, 0), (640, 0), (640, 420), (370, 420),
                          (370, 300), (320, 310), (315, 420), (0, 420)])
    cm.add_polygon(domain, "domain", resolution=50, z_order=-1000)
    if case == "wells":
        for x, y in [(150, 150), (470, 270), (450, 115)]:
            cm.add_point(Point(x, y), f"well-{x}-{y}", hex_ring=True, **feature(10))
    else:
        cm.add_polygon(box(220, 100, 430, 300), "zone", **feature(27))
        cm.add_line(LineString([(50, 105), (135, 155), (240, 183), (590, 220)]),
                    "main", snap_to_polygons=False, **feature(13))
        cm.add_line(LineString([(131, 360), (135, 157)]), "branch",
                    snap_to_polygons=False, **feature(13))
    return cm, domain


def metrics(grid, domain):
    areas = grid.area.to_numpy()
    centers = shapely.points(grid.x.to_numpy(), grid.y.to_numpy())
    centroid_error = shapely.distance(centers, grid.geometry.centroid.to_numpy()) / np.sqrt(areas)
    adjacency = build_connectivity(grid, center="centroid")
    generator = build_connectivity(grid, center="generator")
    a, b = adjacency.cell_id_1.to_numpy(), adjacency.cell_id_2.to_numpy()
    ratios = np.maximum(areas[a] / areas[b], areas[b] / areas[a])
    short = generator.geometry.length.to_numpy() < 0.2 * shapely.length(generator.connector.to_numpy())
    return dict(cells=len(grid), invalid=int((~grid.is_valid).sum()),
                area_delta=abs(float(areas.sum()) - domain.area),
                area_jump_p95=float(np.quantile(ratios, 0.95)),
                centroid_drift_p95=float(np.quantile(centroid_error, 0.95)),
                centroid_ortho_p95=float(np.nanquantile(adjacency.ortho_error, 0.95)),
                generator_ortho_p95=float(np.nanquantile(generator.ortho_error, 0.95)),
                short_pct=100 * float(np.mean(short)))


set_verbosity(0)
caps = [int(n) for n in sys.argv[1:]] or [0, 10, 20, 50, 100]
for case in ["wells", "lines_zones", "concave"]:
    cm, domain = setup(case)
    start = perf_counter()
    mesh = MeshGenerator(background_lc=50, conceptual_mesh=cm, verbosity=0)
    mesh.generate()
    gmsh_s = perf_counter() - start
    for cap in caps:
        start = perf_counter()
        tess = VoronoiTessellator(mesh, boundary_centering="inset_mirror",
                                  lloyd_iterations=cap, min_face_fraction=0.2)
        grid = tess.generate()
        seconds = perf_counter() - start
        print(json.dumps(dict(case=case, cap=cap, gmsh_s=gmsh_s, tess_s=seconds,
                              passes=(tess.lloyd_report or {}).get("iterations", 0),
                              **metrics(grid, domain))), flush=True)
