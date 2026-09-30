import warnings

import pytest
from shapely.geometry import Polygon, LineString
import gmsh

from vorflow.blueprint import ConceptualMesh
from vorflow.engine import MeshGenerator
from vorflow.fields import GeometricGrowthField
from vorflow.tessellator import VoronoiTessellator

pytestmark = pytest.mark.slow  # gmsh-heavy end-to-end tests

def test_gmsh_integration_simple_square():
    """
    Test the full pipeline with a simple square domain using the real Gmsh engine.
    """
    # 1. Setup Conceptual Model
    cm = ConceptualMesh(crs="EPSG:3857")
    # 10x10 square
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    # Zone ID 1, resolution 2.0 (coarse mesh for speed)
    cm.add_polygon(square, zone_id=1, resolution=2.0, dist_max=10.0)
    
    clean_polys, clean_lines, clean_points = cm.generate()
    
    # 2. Generate Mesh using Gmsh
    mg = MeshGenerator(background_lc=2.0, verbosity=1)
    
    success = mg.generate(clean_polys, clean_lines, clean_points)
    assert success
    
    assert mg.nodes is not None
    assert len(mg.nodes) > 0
    
    # 3. Tessellate
    vt = VoronoiTessellator(mg, cm, clip_to_boundary=True)
    grid = vt.generate()
    
    # 4. Assertions
    assert not grid.empty
    assert "zone_id" in grid.columns
    assert grid.iloc[0]["zone_id"] == 1
    
    # Check area coverage
    total_area = grid.geometry.area.sum()
    expected_area = square.area
    # Should be very close as we clip to the exact boundary
    assert pytest.approx(total_area, rel=0.01) == expected_area

def test_gmsh_integration_with_internal_line():
    """
    Test that internal lines (constraints) are respected by the mesher.
    """
    cm = ConceptualMesh(crs="EPSG:3857")
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    cm.add_polygon(square, zone_id=1, resolution=5.0, dist_max=25.0)
    
    # Diagonal line with finer resolution
    line = LineString([(1, 1), (9, 9)])
    cm.add_line(line, line_id="fault", resolution=1.0)
    
    clean_polys, clean_lines, clean_points = cm.generate()
    
    mg = MeshGenerator(background_lc=5.0, verbosity=1)
    
    success = mg.generate(clean_polys, clean_lines, clean_points)
    assert success
    
    vt = VoronoiTessellator(mg, cm, clip_to_boundary=True)
    grid = vt.generate()
    
    assert not grid.empty
    
    # We expect significantly more cells than a 5.0 resolution square would imply (approx 4 cells)
    # because of the 1.0 resolution line.
    assert len(grid) > 10


def test_gmsh_integration_tolerates_duplicate_line_vertices():
    """Duplicate consecutive line vertices should not crash mesh generation."""
    cm = ConceptualMesh(crs="EPSG:3857")
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    cm.add_polygon(square, zone_id=1, resolution=4.0, dist_max=20.0)

    line = LineString([(1, 1), (5, 5), (5, 5), (9, 9)])
    cm.add_line(line, line_id="duplicate_vertices", resolution=1.0)

    clean_polys, clean_lines, clean_points = cm.generate()

    mg = MeshGenerator(background_lc=4.0, verbosity=0)
    success = mg.generate(clean_polys, clean_lines, clean_points)

    assert success
    assert mg.nodes is not None
    assert len(mg.nodes) > 0


def test_gmsh_integration_tolerates_near_duplicate_line_vertices():
    """Near-zero segments should be cleaned before OCC line creation."""
    cm = ConceptualMesh(crs="EPSG:3857")
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    cm.add_polygon(square, zone_id=1, resolution=4.0, dist_max=20.0)

    line = LineString([(1, 8), (5, 8), (5 + 1e-9, 8), (9, 8)])
    cm.add_line(line, line_id="near_duplicate_vertices", resolution=1.0)

    clean_polys, clean_lines, clean_points = cm.generate()

    mg = MeshGenerator(background_lc=4.0, verbosity=0)
    success = mg.generate(clean_polys, clean_lines, clean_points)

    assert success
    assert mg.nodes is not None
    assert len(mg.nodes) > 0


def test_gmsh_integration_with_field_only_line_refinement():
    """A non-embedded (field-only) line should refine the mesh without partitioning it."""
    cm = ConceptualMesh(crs="EPSG:3857")
    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    cm.add_polygon(square, zone_id=1, resolution=5.0, dist_max=25.0)

    # Field-only diagonal line with finer resolution.
    line = LineString([(1, 1), (9, 9)])
    cm.add_line(line, line_id="fault", resolution=1.0, embed=False)

    clean_polys, clean_lines, clean_points = cm.generate()

    mg = MeshGenerator(background_lc=5.0, verbosity=1)
    success = mg.generate(clean_polys, clean_lines, clean_points)
    assert success

    vt = VoronoiTessellator(mg, cm, clip_to_boundary=True)
    grid = vt.generate()
    assert not grid.empty

    # Still expect refinement from the line-based size field.
    assert len(grid) > 10


def _constant_fields_for_surfaces(surface_tags, expected_vin):
    matches = []
    expected_surfaces = {float(tag) for tag in surface_tags}

    for field_id in gmsh.model.mesh.field.list():
        if gmsh.model.mesh.field.getType(field_id) != "Constant":
            continue

        surfaces = set(gmsh.model.mesh.field.getNumbers(field_id, "SurfacesList"))
        if surfaces != expected_surfaces:
            continue

        vin = gmsh.model.mesh.field.getNumber(field_id, "VIn")
        if vin == pytest.approx(expected_vin):
            matches.append(field_id)

    return matches


def test_embedded_polygon_field_has_constant_interior():
    """Embedded polygon size fields should stay constant inside the surface."""
    cm = ConceptualMesh(crs="EPSG:3857")
    domain = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    inner = Polygon([(5, 5), (15, 5), (15, 15), (5, 15)])

    cm.add_polygon(domain, zone_id=1, resolution=10.0, z_order=0)
    cm.add_polygon(
        inner,
        zone_id=2,
        resolution=2.0,
        z_order=1,
        fields=[GeometricGrowthField(growth_factor=1.2)],
    )
    clean_polys, clean_lines, clean_points = cm.generate()

    mg = MeshGenerator(background_lc=10.0, verbosity=0)
    gmsh.initialize()
    try:
        gmsh.model.add("embedded_polygon_field")
        gmsh_map = mg._add_geometry(clean_polys, clean_lines, clean_points)
        mg._setup_fields(gmsh_map, clean_polys, clean_lines, clean_points)

        inner_idx = int(clean_polys.index[clean_polys["zone_id"] == 2][0])
        inner_surfaces = set(gmsh_map["surfaces"][inner_idx])
        inner_surface_tags = {
            float(tag) for dim, tag in inner_surfaces if int(dim) == 2
        }

        constant_fields = _constant_fields_for_surfaces(inner_surface_tags, 2.0)

        assert constant_fields, "Expected a constant field inside the embedded polygon surface"
    finally:
        gmsh.finalize()


def test_field_only_polygon_field_has_constant_interior_without_partitioning():
    """Field-only polygons should get flat interior sizing without becoming domain zones."""
    cm = ConceptualMesh(crs="EPSG:3857")
    domain = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    field_poly = Polygon([(5, 5), (15, 5), (15, 15), (5, 15)])

    cm.add_polygon(domain, zone_id=1, resolution=10.0, z_order=0)
    cm.add_polygon(
        field_poly,
        zone_id="field-only",
        resolution=2.0,
        fields=[GeometricGrowthField(growth_factor=1.2)],
        embed=False,
    )
    clean_polys, clean_lines, clean_points = cm.generate()

    field_idx = int(clean_polys.index[clean_polys["zone_id"] == "field-only"][0])
    domain_idx = int(clean_polys.index[clean_polys["zone_id"] == 1][0])
    assert bool(clean_polys.loc[field_idx, "embed"]) is False

    mg = MeshGenerator(background_lc=10.0, verbosity=0)
    gmsh.initialize()
    try:
        gmsh.model.add("field_only_polygon_field")
        gmsh_map = mg._add_geometry(clean_polys, clean_lines, clean_points)
        mg._setup_fields(gmsh_map, clean_polys, clean_lines, clean_points)

        field_surfaces = set(gmsh_map["surfaces"][field_idx])
        field_surface_tags = {
            float(tag) for dim, tag in field_surfaces if int(dim) == 2
        }

        constant_fields = _constant_fields_for_surfaces(field_surface_tags, 2.0)
        assert constant_fields, "Expected a constant field inside the field-only polygon surface"

        embedded_domain_ids = [
            int(i)
            for i, row in clean_polys.iterrows()
            if bool(row.get("embed", True))
        ]
        assert embedded_domain_ids == [domain_idx]
    finally:
        gmsh.finalize()

    assert mg.generate(clean_polys, clean_lines, clean_points)
    assert len(mg.nodes) > 0


def test_gmsh_integration_overlapping_polygon_with_hole():
    """
    Test the case where an overlapping polygon (Zone 2) has a hole in its center.
    Zone 1 (Domain) should fill the area outside Zone 2 AND the area inside Zone 2's hole.
    """
    cm = ConceptualMesh(crs="EPSG:3857")
    
    # 1. Base Domain (Large Square) - Zone 1
    # 20x20 square
    domain = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    cm.add_polygon(domain, zone_id=1, resolution=5.0, dist_max=25.0, z_order=0)
    
    # 2. Overlapping Polygon with Hole (Donut) - Zone 2
    # Outer: 5,5 to 15,15
    # Hole: 8,8 to 12,12
    donut_shell = [(5, 5), (15, 5), (15, 15), (5, 15)]
    donut_hole = [(8, 8), (12, 8), (12, 12), (8, 12)]
    donut = Polygon(donut_shell, [donut_hole])
    
    cm.add_polygon(donut, zone_id=2, resolution=2.0, dist_max=10.0, z_order=1)
    
    # 3. Generate Conceptual Mesh
    clean_polys, clean_lines, clean_points = cm.generate()
    
    # Verify Zone 2 (The Donut)
    zone2_geom = clean_polys[clean_polys['zone_id'] == 2].geometry.iloc[0]
    assert zone2_geom.geom_type == 'Polygon'
    assert len(zone2_geom.interiors) == 1, "Zone 2 should preserve its hole"
    
    # Verify Zone 1 (The Background)
    # Zone 1 should be split into the outer frame and the inner core (inside the donut hole)
    # This might result in a MultiPolygon or multiple Polygon rows depending on implementation
    zone1_rows = clean_polys[clean_polys['zone_id'] == 1]
    # It usually explodes MultiPolygons into multiple rows in _resolve_overlaps
    # So we expect at least 2 parts (outer frame and inner core)
    # Note: The outer frame might be one polygon with a hole (where the donut is), 
    # and the inner core is another polygon.
    assert len(zone1_rows) >= 2, "Zone 1 should be split into outer and inner parts"
    
    # 4. Generate Mesh
    mg = MeshGenerator(background_lc=5.0, verbosity=1)
    success = mg.generate(clean_polys, clean_lines, clean_points)
    
    assert success, "Mesh generation failed for donut overlap scenario"
    assert len(mg.nodes) > 0
    
    # 5. Tessellate
    vt = VoronoiTessellator(mg, cm, clip_to_boundary=True)
    grid = vt.generate()
    
    assert not grid.empty
    
    # Check that we have cells from both zones
    assert 1 in grid['zone_id'].values
    assert 2 in grid['zone_id'].values
    
    # Check total area conservation
    total_area = grid.geometry.area.sum()
    expected_area = domain.area # 400
    assert pytest.approx(total_area, rel=0.01) == expected_area


def _domain_with_fine_zone(*, growth_factor=None, dist_min=None, dist_max=None, fields=None):
    domain = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])
    fine = Polygon([(8, 8), (12, 8), (12, 12), (8, 12)])
    cm = ConceptualMesh(crs=None)
    cm.add_polygon(domain, zone_id="domain", resolution=4.0)
    cm.add_polygon(
        fine,
        zone_id="fine",
        resolution=0.5,
        z_order=1,
        densify=True,
        growth_factor=growth_factor,
        dist_min=dist_min,
        dist_max=dist_max,
        fields=fields,
    )
    clean = cm.generate()
    mg = MeshGenerator(background_lc=4.0, verbosity=0, smoothing_steps=0, optimization_cycles=0)
    return mg, clean


def test_default_field_is_geometric_growth_and_refines_without_dist_or_fields():
    # A feature finer than the background now refines by default via an implicit
    # GeometricGrowthField -- no dist_min/dist_max or explicit fields required,
    # and no deprecation warning.
    mg, clean = _domain_with_fine_zone()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert mg.generate(*clean)
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]

    grid = mg.get_element_grid()
    cent = grid.geometry.centroid
    inner = grid[cent.x.between(8, 12) & cent.y.between(8, 12)]
    outer = grid[(cent.x < 4) | (cent.x > 16)]
    assert not inner.empty and not outer.empty
    # Graded halo: cells in the fine zone are much smaller than far away.
    assert inner.geometry.area.mean() < 0.25 * outer.geometry.area.mean()


def test_dist_params_emit_deprecation_warning_but_still_mesh():
    # The legacy dist_min/dist_max linear-threshold path still works (back-compat)
    # but now warns that it is deprecated in favor of GeometricGrowthField.
    mg, clean = _domain_with_fine_zone(dist_min=0.5, dist_max=10.0)
    with pytest.warns(DeprecationWarning, match="dist_min/dist_max are deprecated"):
        assert mg.generate(*clean)
    assert not mg.get_element_grid().empty


def test_growth_factor_controls_default_refinement_spread():
    # A slower growth factor keeps cells fine over a larger region, so it yields
    # more elements than a fast one -- confirming the parameter is wired through.
    mg_slow, clean_slow = _domain_with_fine_zone(growth_factor=1.05)
    assert mg_slow.generate(*clean_slow)
    slow = len(mg_slow.get_element_grid())

    mg_fast, clean_fast = _domain_with_fine_zone(growth_factor=2.0)
    assert mg_fast.generate(*clean_fast)
    fast = len(mg_fast.get_element_grid())

    assert slow > fast


def test_engine_rejects_invalid_growth_factor_in_prebuilt_feature_table():
    mg, clean = _domain_with_fine_zone()
    clean_polys, clean_lines, clean_points = clean
    fine = clean_polys["zone_id"] == "fine"
    clean_polys.loc[fine, "growth_factor"] = 1.0

    with pytest.raises(ValueError, match="growth_factor"):
        mg.generate(clean_polys, clean_lines, clean_points)


@pytest.mark.parametrize("background_lc", [0.0, -1.0, float("nan"), float("inf")])
def test_engine_rejects_invalid_background_size(background_lc):
    mg, clean = _domain_with_fine_zone()
    mg.background_lc = background_lc

    with pytest.raises(ValueError, match="background_lc"):
        mg.generate(*clean)
