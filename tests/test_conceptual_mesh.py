import warnings

import pytest
from geopandas.testing import assert_geodataframe_equal
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

import vorflow.blueprint as blueprint_module
from vorflow.blueprint import ConceptualMesh
from vorflow.fields import GeometricGrowthField


def test_resolve_overlaps_respects_z_order():
    cm = ConceptualMesh()

    outer = Polygon([(0, 0), (3, 0), (3, 3), (0, 3)])
    inner = Polygon([(1, 1), (4, 1), (4, 4), (1, 4)])

    cm.add_polygon(outer, zone_id=1, z_order=0)
    cm.add_polygon(inner, zone_id=2, z_order=1)

    clean_polys, _, _ = cm.generate()

    assert len(clean_polys) == 2
    assert clean_polys.geometry.is_valid.all()

    resolved_union = unary_union(clean_polys.geometry)
    expected_union = unary_union([outer, inner])
    assert pytest.approx(expected_union.area, rel=1e-6) == resolved_union.area


def test_resolve_overlaps_keeps_first_added_polygon_for_equal_z_order():
    cm = ConceptualMesh()
    for zone_id in range(20):
        cm.add_polygon(
            Polygon([(100, 0), (101, 0), (101, 1), (100, 1)]),
            zone_id=f"background-{zone_id}",
            z_order=-1,
        )
        cm.add_polygon(
            Polygon([(zone_id, 0), (zone_id + 2, 0), (zone_id + 2, 1), (zone_id, 1)]),
            zone_id=zone_id,
            z_order=0,
        )

    clean_polys, _, _ = cm.generate()

    containing_zone = clean_polys[clean_polys.geometry.contains(Point(3.5, 0.5))]
    assert containing_zone["zone_id"].tolist() == [2]


def test_generate_preserves_registered_raw_features():
    cm = ConceptualMesh(connectivity_tolerance=0.1)
    domain = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    field_only = Polygon([(8, 8), (12, 8), (12, 12), (8, 12)])
    crossing_line = LineString([(-1, 5), (5, 5)])
    near_corner = Point(0.05, 0.05)
    outside_point = Point(20, 20)
    cm.add_polygon(domain, zone_id="domain")
    cm.add_polygon(field_only, zone_id="field", embed=False)
    cm.add_line(crossing_line, line_id="line", resolution=1, densify=False)
    cm.add_point(near_corner, point_id="near", resolution=1)
    cm.add_point(outside_point, point_id="outside", resolution=1)

    cm.generate()

    assert [feature["zone_id"] for feature in cm.raw_polygons] == ["domain", "field"]
    assert cm.raw_polygons[0]["geometry"].equals(domain)
    assert cm.raw_polygons[1]["geometry"].equals(field_only)
    assert cm.raw_lines[0]["geometry"].equals(crossing_line)
    assert [feature["point_id"] for feature in cm.raw_points] == ["near", "outside"]
    assert cm.raw_points[0]["geometry"].equals(near_corner)
    assert cm.raw_points[1]["geometry"].equals(outside_point)


def test_generate_repeated_calls_return_identical_clean_frames_with_field_only_polygon():
    cm = ConceptualMesh(connectivity_tolerance=0.1)
    field = GeometricGrowthField(growth_factor=1.2)
    cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), zone_id="domain")
    cm.add_polygon(
        Polygon([(8, 8), (12, 8), (12, 12), (8, 12)]),
        zone_id="field",
        fields=[field],
        embed=False,
    )
    cm.add_line(
        LineString([(-1, 5), (5, 5)]),
        line_id="line",
        resolution=1,
        densify=False,
    )
    cm.add_point(Point(20, 20), point_id="outside", resolution=1)

    first = tuple(frame.copy(deep=True) for frame in cm.generate())
    second = cm.generate()

    assert first[0]["zone_id"].tolist() == ["domain", "field"]
    assert first[0].iloc[1]["fields"] == [field]
    for first_frame, second_frame in zip(first, second):
        assert_geodataframe_equal(first_frame, second_frame)


def test_generate_preserves_raw_inputs_when_processing_fails():
    cm = ConceptualMesh()
    cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), zone_id="domain")
    cm.add_polygon(Polygon([(2, 2), (4, 2), (4, 4), (2, 4)]), zone_id="field", embed=False)
    original_line = LineString([(1, 1), (2, 1.01), (3, 1)])
    cm.add_line(
        original_line,
        line_id="noisy",
        resolution=1,
        simplify_tolerance=0.1,
    )

    with pytest.raises(ValueError, match="connectivity_tolerance"):
        cm.generate(connectivity_tolerance=True)

    assert [feature["zone_id"] for feature in cm.raw_polygons] == ["domain", "field"]
    assert cm.raw_lines[0]["geometry"].equals(original_line)


def test_growth_factor_must_exceed_one():
    cm = ConceptualMesh()
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    with pytest.raises(ValueError, match="growth_factor"):
        cm.add_polygon(square, zone_id=1, resolution=0.5, growth_factor=1.0)
    with pytest.raises(ValueError, match="growth_factor"):
        cm.add_line(LineString([(0, 0), (1, 0)]), line_id="l", resolution=0.5, growth_factor=0.9)
    with pytest.raises(ValueError, match="growth_factor"):
        cm.add_point(Point(0, 0), point_id="p", resolution=0.5, growth_factor=True)


@pytest.mark.parametrize("growth_factor", [float("nan"), float("inf"), -float("inf")])
def test_growth_factor_must_be_finite(growth_factor):
    cm = ConceptualMesh(crs=None)
    with pytest.raises(ValueError, match="finite"):
        cm.add_point(
            Point(0, 0),
            point_id="p",
            resolution=0.5,
            growth_factor=growth_factor,
        )


def test_growth_factor_defaults_to_none_and_is_stored():
    cm = ConceptualMesh()
    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    cm.add_polygon(square, zone_id=1, resolution=0.5)
    cm.add_polygon(square, zone_id=2, resolution=0.5, growth_factor=1.3)
    assert cm.raw_polygons[0]["growth_factor"] is None
    assert cm.raw_polygons[1]["growth_factor"] == 1.3


def test_lines_and_points_snap_to_polygons():
    cm = ConceptualMesh(connectivity_tolerance=1.0)

    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    cm.add_polygon(square, zone_id=1)

    line = LineString([(-0.5, 1.0), (0.5, 1.0)])
    point = Point(-0.0005, 0.0)

    cm.add_line(line, line_id="river", resolution=0.1)
    cm.add_point(point, point_id="well", resolution=0.1)

    cm.generate()

    assert not cm.clean_lines.empty
    assert not cm.clean_points.empty

    boundary = cm.clean_polygons.iloc[0].geometry.boundary
    snapped_line = cm.clean_lines.iloc[0].geometry
    snapped_point = cm.clean_points.iloc[0].geometry

    tolerance = 1e-3
    assert snapped_line.distance(boundary) <= tolerance
    assert snapped_point.distance(boundary) <= tolerance


def test_line_snapping_defaults_to_enabled_and_can_be_disabled():
    cm = ConceptualMesh(connectivity_tolerance=0.25)
    cm.add_polygon(Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]), zone_id=1)
    cm.add_line(LineString([(0.1, 0.1), (1, 1)]), line_id="default", resolution=0.1, densify=False)
    cm.add_line(
        LineString([(0.1, 1.9), (1, 1.5)]),
        line_id="disabled",
        resolution=0.1,
        snap_to_polygons=False,
        densify=False,
    )

    _, clean_lines, _ = cm.generate()

    default_line = clean_lines.loc[clean_lines["line_id"] == "default", "geometry"].iloc[0]
    disabled_line = clean_lines.loc[clean_lines["line_id"] == "disabled", "geometry"].iloc[0]
    assert default_line.coords[0] == (0.0, 0.0)
    assert disabled_line.coords[0] == (0.1, 1.9)


def test_points_outside_domain_are_removed_after_connectivity():
    cm = ConceptualMesh(connectivity_tolerance=0.01)

    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    cm.add_polygon(square, zone_id=1)
    cm.add_line(LineString([(0, 1), (2, 1)]), line_id="river", resolution=0.5, densify=False)
    cm.add_point(Point(-0.05, 1), point_id="outside_well", resolution=0.05)

    _, _, clean_points = cm.generate()

    assert clean_points.empty


def test_points_snapped_to_domain_boundary_are_kept():
    cm = ConceptualMesh(connectivity_tolerance=0.1)

    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    cm.add_polygon(square, zone_id=1)
    cm.add_line(LineString([(0, 1), (2, 1)]), line_id="river", resolution=0.5, densify=False)
    cm.add_point(Point(-0.05, 1), point_id="snapped_well", resolution=0.05)

    _, _, clean_points = cm.generate()

    assert len(clean_points) == 1
    assert clean_points.iloc[0].geometry.equals(Point(0, 1))


def test_lines_are_clipped_to_domain_after_connectivity():
    cm = ConceptualMesh(connectivity_tolerance=0.0)

    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    cm.add_polygon(square, zone_id=1)
    cm.add_line(LineString([(-1, 1), (3, 1)]), line_id="crossing_line", resolution=0.5, densify=False)

    _, clean_lines, _ = cm.generate()

    assert len(clean_lines) == 1
    assert clean_lines.iloc[0].geometry.equals(LineString([(0, 1), (2, 1)]))


def test_generate_uses_constructor_connectivity_tolerance(monkeypatch):
    cm = ConceptualMesh(connectivity_tolerance=0.25)
    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])

    cm.add_polygon(square, zone_id=1)
    cm.add_line(LineString([(0, 0), (1, 0)]), line_id="river", resolution=0.1, densify=False)
    cm.add_point(Point(0.1, 0.1), point_id="well", resolution=0.1)

    recorded_tolerances = []

    def fake_snap(geometry, reference_geometry, tolerance):
        recorded_tolerances.append(tolerance)
        return geometry

    monkeypatch.setattr(blueprint_module, "snap", fake_snap)

    cm.generate()

    assert recorded_tolerances == [pytest.approx(0.25), pytest.approx(0.25)]


def test_generate_can_override_connectivity_tolerance(monkeypatch):
    cm = ConceptualMesh(connectivity_tolerance=1.0)
    square = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])

    cm.add_polygon(square, zone_id=1)
    cm.add_line(LineString([(0, 0), (1, 0)]), line_id="river", resolution=0.1, densify=False)
    cm.add_point(Point(0.1, 0.1), point_id="well", resolution=0.1)

    recorded_tolerances = []

    def fake_snap(geometry, reference_geometry, tolerance):
        recorded_tolerances.append(tolerance)
        return geometry

    monkeypatch.setattr(blueprint_module, "snap", fake_snap)

    cm.generate(connectivity_tolerance=0.05)

    assert recorded_tolerances == [pytest.approx(0.05), pytest.approx(0.05)]

def test_polygon_simplification():
    """Test that polygons are simplified when tolerance is provided."""
    cm = ConceptualMesh()
    
    # Create a "noisy" square with a tiny bump on the top edge
    # (0,1) -> (0.5, 1.001) -> (1,1)
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0.5, 1.001), (0, 1)])
    
    # Add with a tolerance larger than the noise (0.001)
    cm.add_polygon(poly, zone_id=1, simplify_tolerance=0.01)
    
    clean_polys, _, _ = cm.generate()
    
    simplified_geom = clean_polys.iloc[0].geometry
    
    # The original polygon has 5 vertices + closing = 6 points in exterior ring
    # The simplified one should remove the bump, leaving 4 corners + closing = 5 points
    assert len(simplified_geom.exterior.coords) == 5
    assert len(simplified_geom.exterior.coords) < len(poly.exterior.coords)

def test_line_simplification():
    """Test that lines are simplified when tolerance is provided."""
    cm = ConceptualMesh()
    # Noisy line: straight but with a midpoint slightly off
    line = LineString([(0, 0), (0.5, 0.001), (1, 0)])
    
    cm.add_line(line, line_id="noisy_line", resolution=0.1, simplify_tolerance=0.01, densify=False)
    
    _, clean_lines, _ = cm.generate()
    
    simplified_line = clean_lines.iloc[0].geometry
    # Should be simplified to just start and end points
    assert len(simplified_line.coords) == 2

def test_point_deduplication():
    """Test that close points are merged and the finest resolution is kept."""
    cm = ConceptualMesh()
    p1 = Point(0, 0)
    p2 = Point(0.0001, 0) # Very close to p1
    
    # Case 1: No simplification (default) -> Should keep both
    cm.add_point(p1, "p1", resolution=1.0)
    cm.add_point(p2, "p2", resolution=0.5) 
    
    _, _, clean_points = cm.generate()
    assert len(clean_points) == 2
    
    # Case 2: With simplification -> Should merge
    cm2 = ConceptualMesh()
    # p2 has finer resolution (0.5), so it should be the one kept
    cm2.add_point(p1, "p1", resolution=1.0, simplify_tolerance=0.01)
    cm2.add_point(p2, "p2", resolution=0.5, simplify_tolerance=0.01)
    
    _, _, clean_points_merged = cm2.generate()
    
    assert len(clean_points_merged) == 1
    
    # Verify we kept the point with the finer resolution (0.5)
    kept_point = clean_points_merged.iloc[0]
    assert kept_point['lc'] == 0.5
    assert kept_point['point_id'] == "p2"

def test_line_densification_options():
    """Test the three modes of line densification: False, True, and float."""
    cm = ConceptualMesh()
    # A line of length 10
    line = LineString([(0, 0), (10, 0)])
    
    # 1. densify=False: Should NOT add vertices
    cm.add_line(line, "no_densify", resolution=1.0, densify=False)
    
    # 2. densify=True (default): Should use resolution (1.0) -> ~10 segments
    cm.add_line(line, "default_densify", resolution=1.0, densify=True)
    
    # 3. densify=5.0: Should use custom spacing (5.0) -> ~2 segments
    cm.add_line(line, "custom_densify", resolution=1.0, densify=5.0)
    
    _, clean_lines, _ = cm.generate()
    
    # Check 1: No densification
    l1 = clean_lines[clean_lines['line_id'] == "no_densify"].iloc[0].geometry
    assert len(l1.coords) == 2 # Just start and end
    
    # Check 2: Default densification (lc=1.0)
    l2 = clean_lines[clean_lines['line_id'] == "default_densify"].iloc[0].geometry
    # Should have 11 points (10 segments)
    assert len(l2.coords) == 11 
    
    # Check 3: Custom densification (val=5.0)
    l3 = clean_lines[clean_lines['line_id'] == "custom_densify"].iloc[0].geometry
    # Should have roughly 3 points (2 segments)
    assert len(l3.coords) == 3

@pytest.mark.parametrize("bool_tol", [True, False])
def test_simplify_tolerance_bool_is_rejected(bool_tol):
    cm = ConceptualMesh()

    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    with pytest.raises(ValueError):
        cm.add_polygon(poly, zone_id=1, simplify_tolerance=bool_tol)

    line = LineString([(0, 0), (1, 0)])
    with pytest.raises(ValueError):
        cm.add_line(line, line_id="l1", resolution=0.1, simplify_tolerance=bool_tol, densify=False)

    pt = Point(0, 0)
    with pytest.raises(ValueError):
        cm.add_point(pt, point_id="p1", resolution=0.1, simplify_tolerance=bool_tol)


@pytest.mark.parametrize("bad_tolerance", [True, False, -1])
def test_connectivity_tolerance_validation(bad_tolerance):
    with pytest.raises(ValueError):
        ConceptualMesh(connectivity_tolerance=bad_tolerance)


class TestCrsHandling:
    def test_geographic_crs_warns(self):
        with pytest.warns(UserWarning, match="geographic"):
            ConceptualMesh(crs="EPSG:4326")

    def test_projected_crs_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ConceptualMesh(crs="EPSG:32618")

    def test_default_crs_is_none_and_does_not_warn(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cm = ConceptualMesh()
        assert cm.crs is None

    def test_none_crs_propagates_to_outputs(self):
        cm = ConceptualMesh()
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), zone_id=1)
        clean_polys, _, _ = cm.generate()
        assert clean_polys.crs is None

    def test_explicit_projected_crs_propagates_to_outputs(self):
        cm = ConceptualMesh(crs="EPSG:32618")
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), zone_id=1)
        clean_polys, _, _ = cm.generate()
        assert clean_polys.crs is not None
        assert clean_polys.crs.to_epsg() == 32618
