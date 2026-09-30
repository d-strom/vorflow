import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

import vorflow.blueprint as blueprint_module
from vorflow.blueprint import ConceptualMesh


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
