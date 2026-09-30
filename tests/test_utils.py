import logging

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon

from vorflow.utils import (
    calculate_mesh_quality,
    check_geometry_resolution,
    resample_geometry,
    summarize_quality,
)


@pytest.fixture
def paired_polygons():
    poly_a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly_b = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])

    gdf = gpd.GeoDataFrame(
        {
            "geometry": [poly_a, poly_b],
            "x": [0.5, 1.5],
            "y": [0.5, 0.5],
        }
    )
    return gdf


def test_calculate_mesh_quality_computes_expected_scalar_metrics(paired_polygons):
    quality = calculate_mesh_quality(paired_polygons, calc_ortho=True)

    assert "area" in quality.columns
    assert "compactness" in quality.columns
    assert "ortho_error" in quality.columns

    assert quality["area"].iloc[0] == pytest.approx(1.0)
    assert quality["perimeter"].iloc[0] == pytest.approx(4.0)
    assert quality["compactness"].iloc[0] == pytest.approx(np.pi / 4, rel=1e-5)
    assert quality["drift_ratio"].iloc[0] == pytest.approx(0.0, abs=1e-8)
    assert quality["ortho_error"].iloc[0] == pytest.approx(0.0, abs=1e-6)


class TestCheckGeometryResolution:
    def test_reports_segment_statistics_for_square(self):
        square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        gdf = gpd.GeoDataFrame({"geometry": [square]})
        stats = check_geometry_resolution(gdf)
        assert stats["count"] == 4
        assert stats["min"] == pytest.approx(2.0)
        assert stats["max"] == pytest.approx(2.0)
        assert stats["mean"] == pytest.approx(2.0)
        assert stats["median"] == pytest.approx(2.0)

    def test_mixed_geometries_include_lines_and_skip_points(self):
        square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        line = LineString([(0, 0), (3, 4)])  # single 5.0 segment
        gdf = gpd.GeoDataFrame({"geometry": [square, line, Point(9, 9)]})
        stats = check_geometry_resolution(gdf)
        assert stats["count"] == 5
        assert stats["max"] == pytest.approx(5.0)

    def test_no_segments_warns_and_returns_empty_stats(self):
        gdf = gpd.GeoDataFrame({"geometry": [Point(0, 0), Point(1, 1)]})
        with pytest.warns(UserWarning, match="no valid segments"):
            stats = check_geometry_resolution(gdf)
        assert stats["count"] == 0
        assert np.isnan(stats["min"]) and np.isnan(stats["mean"])


class TestResampleGeometry:
    def test_linestring_vertices_evenly_spaced(self):
        line = LineString([(0, 0), (10, 0)])
        out = resample_geometry(line, target_spacing=2.5)
        coords = np.asarray(out.coords)
        assert len(coords) == 5  # 4 segments of 2.5
        spacing = np.linalg.norm(np.diff(coords, axis=0), axis=1)
        assert spacing == pytest.approx([2.5] * 4)
        assert out.length == pytest.approx(line.length)

    def test_polygon_preserves_shape_and_holes(self):
        outer = Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]],
        )
        out = resample_geometry(outer, target_spacing=1.0)
        assert out.is_valid
        assert len(out.interiors) == 1
        assert out.area == pytest.approx(outer.area, rel=1e-6)
        seg = np.linalg.norm(
            np.diff(np.asarray(out.exterior.coords), axis=0), axis=1
        )
        assert seg.max() <= 1.0 + 1e-9

    def test_point_and_empty_geometries_pass_through(self):
        pt = Point(1, 2)
        assert resample_geometry(pt, 1.0) is pt
        empty = LineString()
        assert resample_geometry(empty, 1.0).is_empty


class TestSummarizeQuality:
    def test_report_lists_cell_counts(self, paired_polygons, caplog):
        quality = calculate_mesh_quality(paired_polygons, calc_ortho=True)
        vlog = logging.getLogger("vorflow")
        old_propagate = vlog.propagate
        vlog.propagate = True  # let caplog's root handler see the records
        try:
            with caplog.at_level(logging.INFO, logger="vorflow.utils"):
                summarize_quality(quality)
        finally:
            vlog.propagate = old_propagate
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "Mesh Quality Report" in messages
        assert "Total Cells: 2" in messages
