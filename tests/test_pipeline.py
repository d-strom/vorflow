import numpy as np
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from vorflow.blueprint import ConceptualMesh
from vorflow.tessellator import VoronoiTessellator
from vorflow.utils import build_connectivity


class FakeMeshGenerator:
    def __init__(self, clean_polygons, spacing=0.35):
        domain = unary_union(clean_polygons.geometry)
        minx, miny, maxx, maxy = domain.bounds

        nx = max(3, int(np.ceil((maxx - minx) / spacing)) + 1)
        ny = max(3, int(np.ceil((maxy - miny) / spacing)) + 1)

        xs = np.linspace(0.0, 1.0, nx)
        ys = np.linspace(0.0, 1.0, ny)

        points = []
        for x_norm in xs:
            for y_norm in ys:
                x = minx + x_norm * max(0.0, maxx - minx)
                y = miny + y_norm * max(0.0, maxy - miny)
                candidate = Point(x, y)
                if domain.contains(candidate) or domain.touches(candidate):
                    points.append([x, y])

        points_np = np.array(points)
        points_np = (
            np.unique(points_np, axis=0)
            if points_np.size != 0
            else np.empty((0, 2))
        )

        if points_np.shape[0] < 3:
            center = Point((minx + maxx) / 2.0, (miny + maxy) / 2.0)
            offsets = [[-spacing, 0], [spacing, 0], [0, spacing]]
            points_np = np.array([[center.x + dx, center.y + dy] for dx, dy in offsets])

        self.nodes = points_np
        self.node_tags = list(range(len(points_np)))
        self.zones_gdf = clean_polygons


class EmptyMeshGenerator:
    def __init__(self):
        self.nodes = np.empty((0, 2))
        self.node_tags = []
        self.zones_gdf = gpd.GeoDataFrame()


def _build_simple_conceptual_mesh():
    cm = ConceptualMesh(crs="EPSG:3857")
    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    cm.add_polygon(square, zone_id=99, densify=0.5)
    return cm


def test_full_pipeline_assigns_zones_and_covers_domain():
    cm = _build_simple_conceptual_mesh()
    clean_polys, clean_lines, clean_points = cm.generate()

    mesh_gen = FakeMeshGenerator(clean_polys)
    tessellator = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True)
    final_grid = tessellator.generate()

    assert not final_grid.empty
    assert set(final_grid["zone_id"]) == {99}
    assert set(final_grid["node_id"]) == set(mesh_gen.node_tags)
    assert "centroid_x" in final_grid.columns
    assert "centroid_y" in final_grid.columns

    domain_area = unary_union(clean_polys.geometry).area
    grid_area = unary_union(final_grid.geometry).area
    assert pytest.approx(domain_area, rel=1e-2) == grid_area


def test_pipeline_reports_empty_when_no_domain():
    cm = ConceptualMesh()
    tessellator = VoronoiTessellator(EmptyMeshGenerator(), cm, clip_to_boundary=True)
    grid = tessellator.generate()

    assert grid.empty


def test_barrier_cells_keep_orthogonal_generator_centres():
    """Catches barrier-split fragments carrying a centroid instead of a Voronoi generator.

    A lattice has no straddle pairs, so the oblique barrier crosses a row of
    cells, including both boundary cells at its ends. MODFLOW 6 uses x/y as
    the cell centre; any fragment without a generator of its own gives
    non-orthogonal connections (up to 49 degrees on this grid).
    """
    cm = _build_simple_conceptual_mesh()
    barrier = LineString([(0, 0.6), (2, 1.3)])
    cm.add_line(barrier, line_id="fault", resolution=1.0, is_barrier=True)
    clean_polys, _, _ = cm.generate()
    mesh_gen = FakeMeshGenerator(clean_polys)
    tessellator = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True)

    grid = tessellator.generate()

    assert tessellator.n_barrier_mirrors > 0
    # Every cell is a Voronoi cell: mesh nodes plus mirrors, no leftover fragments.
    assert len(grid) == len(mesh_gen.nodes) + tessellator.n_barrier_mirrors
    assert grid["node_id"].is_unique
    original = grid.set_index("node_id").loc[mesh_gen.node_tags]
    np.testing.assert_array_equal(original[["x", "y"]].to_numpy(), mesh_gen.nodes)
    # The barrier runs along cell faces only.
    faces = unary_union(grid.boundary).buffer(1e-9)
    assert barrier.difference(faces).length == pytest.approx(0.0, abs=1e-9)
    connectivity = build_connectivity(grid, center="generator")
    assert connectivity["ortho_error"].max() < 1e-6
    # Lattice nodes are cocircular, so Qhull leaves roundoff-length edges
    # elsewhere; only the cells along the barrier are checked.
    for cell in grid.geometry[grid.intersects(barrier)]:
        coords = np.asarray(cell.exterior.coords)
        assert np.hypot(*np.diff(coords, axis=0).T).min() > 1e-9
    assert pytest.approx(unary_union(clean_polys.geometry).area, rel=1e-9) == grid.geometry.area.sum()
