"""Check the QGIS wrapper's planar measurements on existing Voronoi grids."""

import ast
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Polygon, box


def _load_adapters():
    path = Path(__file__).resolve().parents[1] / "vorflow_qgis" / "vorflow_plugin.py"
    names = {"qgis_to_shapely", "_polygon_ring_metrics", "_shared_face_metrics",
             "build_voronoi_quality_grid"}
    nodes = [node for node in ast.parse(path.read_text(encoding="utf-8")).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
    return scope


def test_qgis_input_is_planar_without_modifying_source_wkb():
    polygon = shapely.from_wkt("POLYGON ZM ((0 0 4 1, 1 0 5 2, 1 1 6 3, 0 0 4 1))")

    class SourceGeometry:
        def asWkb(self):
            return polygon.wkb

    converted = _load_adapters()["qgis_to_shapely"](SourceGeometry())
    assert shapely.get_coordinate_dimension(converted) == 2
    assert converted.equals_exact(shapely.force_2d(polygon), 0)
    assert polygon.has_z and polygon.has_m


def test_quality_metrics_on_polygon_z_match_xy_without_rewriting_geometry():
    geometries = [
        Polygon([(0, 0, 5), (1, 0, 6), (1, 1, 9), (0, 1, 7)]),
        Polygon([(1, 0, 4), (2, 0, 3), (2, 1, 7), (1, 1, 5)]),
    ]
    grid = gpd.GeoDataFrame({"node_id": [7, 11]}, geometry=geometries, crs="EPSG:3010")
    planar = grid.set_geometry(shapely.force_2d(grid.geometry))
    quality = _load_adapters()["build_voronoi_quality_grid"]
    result = quality(grid)

    assert result.geometry.to_wkb().equals(grid.geometry.to_wkb())
    assert result["cell_area"].tolist() == [1.0, 1.0]
    assert result["cell_min_edge"].tolist() == [1.0, 1.0]
    assert result["cell_neighbour_count"].tolist() == [1, 1]
    pd.testing.assert_frame_equal(
        result.drop(columns="geometry"), quality(planar).drop(columns="geometry")
    )


def test_quality_counts_shared_edge_even_with_an_isolated_point_contact():
    left = box(0, 0, 2, 2)
    right = Polygon([(2, 0), (4, 0), (4, 2), (2, 2), (3, 1), (2, 1)])
    assert left.boundary.intersection(right.boundary).geom_type == "GeometryCollection"
    grid = gpd.GeoDataFrame(geometry=[left, right], crs="EPSG:3010")
    result = _load_adapters()["build_voronoi_quality_grid"](grid)
    assert result["cell_neighbour_count"].tolist() == [1, 1]
    assert result["orthogonality_min"].between(0, 1).all()
