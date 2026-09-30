"""
Tests for point entity tracking through the fragment → removeAllDuplicates → healShapes pipeline.

These tests verify that dim-0 (point) entities added to the Gmsh model survive
each stage of the _add_geometry pipeline and are correctly reflected in the
returned gmsh_map, so that _embed_features and _setup_fields can find them.
"""
import pytest
import gmsh
from shapely.geometry import Point, Polygon, LineString

from vorflow.blueprint import ConceptualMesh
from vorflow.engine import MeshGenerator

pytestmark = pytest.mark.slow  # gmsh-heavy end-to-end tests



# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _build_model(polygon_coords, points, lines=None,
                 background_lc=5.0, polygon_res=5.0, point_res=1.0,
                 heal_shapes=False, heal_tolerance=1e-8,
                 heal_fix_degenerated=True, heal_fix_small_edges=True,
                 heal_fix_small_faces=True):
    """
    Build a ConceptualMesh with a single polygon + N points, run
    _add_geometry, and return (mg, gmsh_map, clean_points).
    """
    cm = ConceptualMesh(crs="EPSG:3857")
    poly = Polygon(polygon_coords)
    cm.add_polygon(poly, zone_id=1, resolution=polygon_res, dist_max=25.0)

    for i, pt in enumerate(points):
        cm.add_point(pt, point_id=f"pt_{i}", resolution=point_res, dist_min=0, dist_max=5.0)

    if lines:
        for i, ln in enumerate(lines):
            cm.add_line(ln, line_id=f"ln_{i}", resolution=point_res)

    clean_polys, clean_lines, clean_points = cm.generate()

    mg = MeshGenerator(
        background_lc=background_lc,
        verbosity=2,
        heal_shapes=heal_shapes,
        heal_tolerance=heal_tolerance,
        heal_fix_degenerated=heal_fix_degenerated,
        heal_fix_small_edges=heal_fix_small_edges,
        heal_fix_small_faces=heal_fix_small_faces,
    )
    mg._initialize_gmsh()
    gmsh_map = mg._add_geometry(clean_polys, clean_lines, clean_points)
    return mg, gmsh_map, clean_points


def _count_mapped_points(gmsh_map):
    """Count how many point feature IDs have at least one valid dim-0 tag."""
    n_features_with_tags = 0
    n_total_dim0 = 0
    for feat_id, dimtags in gmsh_map.get('points', {}).items():
        dim0_tags = [dt for dt in dimtags if isinstance(dt, (tuple, list)) and int(dt[0]) == 0]
        if dim0_tags:
            n_features_with_tags += 1
            n_total_dim0 += len(dim0_tags)
    return n_features_with_tags, n_total_dim0


def _verify_tags_exist_in_model(gmsh_map):
    """Verify every tag in gmsh_map['points'] actually exists in the synchronized model."""
    model_entities = set()
    for dim in range(3):
        for dt in gmsh.model.getEntities(dim):
            model_entities.add((int(dt[0]), int(dt[1])))

    missing = []
    for feat_id, dimtags in gmsh_map.get('points', {}).items():
        for dt in dimtags:
            if isinstance(dt, (tuple, list)) and len(dt) >= 2:
                key = (int(dt[0]), int(dt[1]))
                if key not in model_entities:
                    missing.append((feat_id, key))
    return missing


# ---------------------------------------------------------------------------
#  Test: Basic point survival (no heal)
# ---------------------------------------------------------------------------

class TestPointTrackingNoHeal:
    """Points should survive fragment + removeAllDuplicates without heal."""

    def test_single_point_inside_polygon(self):
        """One point in the center of a square."""
        mg, gmap, pts = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 5)],
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 1, f"Expected 1 point feature mapped, got {n_feat}"
        assert n_tags >= 1, f"Expected >= 1 dim-0 tag, got {n_tags}"
        assert _verify_tags_exist_in_model(gmap) == [], "Stale tags in map"

    def test_multiple_points_spread(self):
        """Several points spread across a polygon."""
        pts = [Point(2, 2), Point(5, 5), Point(8, 8), Point(3, 7)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=pts,
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)} point features, got {n_feat}"
        assert n_tags >= len(pts), f"Expected >= {len(pts)} dim-0 tags, got {n_tags}"
        assert _verify_tags_exist_in_model(gmap) == []

    def test_point_on_polygon_vertex(self):
        """Point exactly on a polygon vertex — fragment may merge them."""
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(0, 0)],
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 1, f"Expected 1 point feature, got {n_feat}"
        # The tag might have been merged with the polygon vertex, but the
        # map entry must still reference a valid dim-0 entity.
        assert n_tags >= 1, f"Expected >= 1 dim-0 tag, got {n_tags}"
        assert _verify_tags_exist_in_model(gmap) == []

    def test_point_on_polygon_edge(self):
        """Point on a polygon edge midpoint."""
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 0)],
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 1
        assert n_tags >= 1
        assert _verify_tags_exist_in_model(gmap) == []

    def test_many_points(self):
        """29 points (matching user's real case) inside a large polygon."""
        import random
        random.seed(42)
        pts = [Point(random.uniform(1, 99), random.uniform(1, 99)) for _ in range(29)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (100, 0), (100, 100), (0, 100)],
            points=pts,
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 29, f"Expected 29 point features, got {n_feat}"
        assert n_tags >= 29
        assert _verify_tags_exist_in_model(gmap) == []


# ---------------------------------------------------------------------------
#  Test: Points with heal_shapes ON (all fix options OFF)
# ---------------------------------------------------------------------------

class TestPointTrackingHealAllOff:
    """Points should survive when heal_shapes=True but all fix flags are False."""

    def test_single_point_heal_noop(self):
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 5)],
            heal_shapes=True,
            heal_tolerance=1e-8,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 1
        assert n_tags >= 1
        assert _verify_tags_exist_in_model(gmap) == []

    def test_multiple_points_heal_noop(self):
        pts = [Point(2, 2), Point(5, 5), Point(8, 8)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=pts,
            heal_shapes=True,
            heal_tolerance=1e-8,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)}, got {n_feat}"
        assert n_tags >= len(pts)
        assert _verify_tags_exist_in_model(gmap) == []

    def test_many_points_heal_noop(self):
        """29 points, heal on but all fixes off."""
        import random
        random.seed(42)
        pts = [Point(random.uniform(1, 99), random.uniform(1, 99)) for _ in range(29)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (100, 0), (100, 100), (0, 100)],
            points=pts,
            heal_shapes=True,
            heal_tolerance=1e-8,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 29, f"Expected 29, got {n_feat}"
        assert n_tags >= 29
        assert _verify_tags_exist_in_model(gmap) == []

    def test_large_tolerance_heal_all_off(self):
        """heal_tolerance=1 (large) but all fix flags off — should be no-op."""
        pts = [Point(2, 2), Point(5, 5), Point(8, 8)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=pts,
            heal_shapes=True,
            heal_tolerance=1.0,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)}, got {n_feat}"
        assert n_tags >= len(pts)
        assert _verify_tags_exist_in_model(gmap) == []


# ---------------------------------------------------------------------------
#  Test: Points with heal_shapes ON (default fix options)
# ---------------------------------------------------------------------------

class TestPointTrackingHealDefaults:
    """Points should survive healShapes with default fix options at small tolerance."""

    def test_single_point_heal_defaults(self):
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 5)],
            heal_shapes=True,
            heal_tolerance=1e-8,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 1
        assert n_tags >= 1
        assert _verify_tags_exist_in_model(gmap) == []

    def test_many_points_heal_defaults(self):
        import random
        random.seed(42)
        pts = [Point(random.uniform(1, 99), random.uniform(1, 99)) for _ in range(29)]
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (100, 0), (100, 100), (0, 100)],
            points=pts,
            heal_shapes=True,
            heal_tolerance=1e-8,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 29, f"Expected 29, got {n_feat}"
        assert n_tags >= 29
        assert _verify_tags_exist_in_model(gmap) == []


# ---------------------------------------------------------------------------
#  Test: Points with large coordinates (projected CRS like user's ~585000)
# ---------------------------------------------------------------------------

class TestPointTrackingLargeCoords:
    """Test with coordinate magnitudes matching real projected CRS data."""

    ORIGIN_X = 584000.0
    ORIGIN_Y = 2366000.0

    def _large_poly(self):
        ox, oy = self.ORIGIN_X, self.ORIGIN_Y
        return [(ox, oy), (ox + 13000, oy), (ox + 13000, oy + 9000), (ox, oy + 9000)]

    def test_large_coords_no_heal(self):
        ox, oy = self.ORIGIN_X, self.ORIGIN_Y
        pts = [
            Point(ox + 1000, oy + 1000),
            Point(ox + 6000, oy + 4500),
            Point(ox + 12000, oy + 8000),
        ]
        mg, gmap, _ = _build_model(
            polygon_coords=self._large_poly(),
            points=pts,
            background_lc=500.0,
            polygon_res=500.0,
            point_res=100.0,
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)}, got {n_feat}"
        assert n_tags >= len(pts)
        assert _verify_tags_exist_in_model(gmap) == []

    def test_large_coords_heal_all_off(self):
        ox, oy = self.ORIGIN_X, self.ORIGIN_Y
        pts = [
            Point(ox + 1000, oy + 1000),
            Point(ox + 6000, oy + 4500),
            Point(ox + 12000, oy + 8000),
        ]
        mg, gmap, _ = _build_model(
            polygon_coords=self._large_poly(),
            points=pts,
            background_lc=500.0,
            polygon_res=500.0,
            point_res=100.0,
            heal_shapes=True,
            heal_tolerance=1.0,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)}, got {n_feat}"
        assert n_tags >= len(pts)
        assert _verify_tags_exist_in_model(gmap) == []

    def test_large_coords_heal_defaults(self):
        ox, oy = self.ORIGIN_X, self.ORIGIN_Y
        pts = [
            Point(ox + 1000, oy + 1000),
            Point(ox + 6000, oy + 4500),
            Point(ox + 12000, oy + 8000),
        ]
        mg, gmap, _ = _build_model(
            polygon_coords=self._large_poly(),
            points=pts,
            background_lc=500.0,
            polygon_res=500.0,
            point_res=100.0,
            heal_shapes=True,
            heal_tolerance=1e-8,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == len(pts), f"Expected {len(pts)}, got {n_feat}"
        assert n_tags >= len(pts)
        assert _verify_tags_exist_in_model(gmap) == []

    def test_29_points_large_coords_heal_all_off_tol1(self):
        """Closest to user's actual scenario: 29 pts, large coords, heal on, all off, tol=1."""
        import random
        random.seed(99)
        ox, oy = self.ORIGIN_X, self.ORIGIN_Y
        pts = [
            Point(ox + random.uniform(500, 12500), oy + random.uniform(500, 8500))
            for _ in range(29)
        ]
        mg, gmap, _ = _build_model(
            polygon_coords=self._large_poly(),
            points=pts,
            background_lc=500.0,
            polygon_res=500.0,
            point_res=100.0,
            heal_shapes=True,
            heal_tolerance=1.0,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 29, f"Expected 29, got {n_feat}"
        assert n_tags >= 29
        missing = _verify_tags_exist_in_model(gmap)
        assert missing == [], f"Stale tags: {missing}"


# ---------------------------------------------------------------------------
#  Test: Points with multiple overlapping polygons
# ---------------------------------------------------------------------------

class TestPointTrackingMultiPolygon:
    """Points inside overlapping polygons (forces non-trivial fragmentation)."""

    def test_points_in_overlapping_polygons_no_heal(self):
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                        zone_id=1, resolution=5.0, dist_max=25.0, z_order=0)
        cm.add_polygon(Polygon([(3, 3), (7, 3), (7, 7), (3, 7)]),
                        zone_id=2, resolution=2.0, dist_max=10.0, z_order=1)
        cm.add_point(Point(5, 5), point_id="center", resolution=0.5, dist_min=0, dist_max=3)
        cm.add_point(Point(1, 1), point_id="outer", resolution=0.5, dist_min=0, dist_max=3)

        clean_polys, clean_lines, clean_points = cm.generate()
        mg = MeshGenerator(background_lc=5.0, verbosity=2, heal_shapes=False)
        mg._initialize_gmsh()
        gmap = mg._add_geometry(clean_polys, clean_lines, clean_points)

        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 2, f"Expected 2, got {n_feat}"
        assert n_tags >= 2
        assert _verify_tags_exist_in_model(gmap) == []

    def test_points_in_overlapping_polygons_heal_all_off(self):
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                        zone_id=1, resolution=5.0, dist_max=25.0, z_order=0)
        cm.add_polygon(Polygon([(3, 3), (7, 3), (7, 7), (3, 7)]),
                        zone_id=2, resolution=2.0, dist_max=10.0, z_order=1)
        cm.add_point(Point(5, 5), point_id="center", resolution=0.5, dist_min=0, dist_max=3)
        cm.add_point(Point(1, 1), point_id="outer", resolution=0.5, dist_min=0, dist_max=3)

        clean_polys, clean_lines, clean_points = cm.generate()
        mg = MeshGenerator(
            background_lc=5.0, verbosity=2,
            heal_shapes=True, heal_tolerance=1.0,
            heal_fix_degenerated=False, heal_fix_small_edges=False, heal_fix_small_faces=False,
        )
        mg._initialize_gmsh()
        gmap = mg._add_geometry(clean_polys, clean_lines, clean_points)

        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat == 2, f"Expected 2, got {n_feat}"
        assert n_tags >= 2
        assert _verify_tags_exist_in_model(gmap) == []


# ---------------------------------------------------------------------------
#  Test: Points with lines (combined features)
# ---------------------------------------------------------------------------

class TestPointTrackingWithLines:
    """Points + lines together — fragments create more complex topology."""

    def test_points_and_line_no_heal(self):
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 5), Point(2, 8)],
            lines=[LineString([(1, 1), (9, 9)])],
            heal_shapes=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat >= 2, f"Expected >= 2, got {n_feat}"
        assert n_tags >= 2
        assert _verify_tags_exist_in_model(gmap) == []

    def test_points_and_line_heal_all_off(self):
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(5, 5), Point(2, 8)],
            lines=[LineString([(1, 1), (9, 9)])],
            heal_shapes=True,
            heal_tolerance=1.0,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat >= 2, f"Expected >= 2, got {n_feat}"
        assert n_tags >= 2
        assert _verify_tags_exist_in_model(gmap) == []

    def test_point_on_line_endpoint(self):
        """Point coincident with a line endpoint — high merge probability."""
        mg, gmap, _ = _build_model(
            polygon_coords=[(0, 0), (10, 0), (10, 10), (0, 10)],
            points=[Point(1, 1)],
            lines=[LineString([(1, 1), (9, 9)])],
            heal_shapes=True,
            heal_tolerance=1e-8,
            heal_fix_degenerated=False,
            heal_fix_small_edges=False,
            heal_fix_small_faces=False,
        )
        n_feat, n_tags = _count_mapped_points(gmap)
        assert n_feat >= 1
        assert n_tags >= 1
        assert _verify_tags_exist_in_model(gmap) == []


# ---------------------------------------------------------------------------
#  Test: Full pipeline (generate mesh, check for mesh nodes near points)
# ---------------------------------------------------------------------------

class TestPointEmbeddingEndToEnd:
    """Full pipeline: points should produce mesh vertices at their locations."""

    def _get_mesh_node_near(self, mg, x, y, tol):
        """Check if any mesh node is within tol of (x, y)."""
        if mg.nodes is None:
            return False
        import numpy as np
        dists = np.sqrt((mg.nodes[:, 0] - x) ** 2 + (mg.nodes[:, 1] - y) ** 2)
        return bool(np.any(dists < tol))

    def test_embedded_point_creates_mesh_vertex_no_heal(self):
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                        zone_id=1, resolution=2.0, dist_max=10.0)
        cm.add_point(Point(5, 5), point_id="well", resolution=0.5, dist_min=0, dist_max=3)
        clean_polys, clean_lines, clean_points = cm.generate()

        mg = MeshGenerator(background_lc=2.0, verbosity=2, heal_shapes=False)
        success = mg.generate(clean_polys, clean_lines, clean_points)
        assert success
        assert self._get_mesh_node_near(mg, 5.0, 5.0, 0.01), \
            "No mesh node found near embedded point (5,5) with heal OFF"

    def test_embedded_point_creates_mesh_vertex_heal_all_off(self):
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                        zone_id=1, resolution=2.0, dist_max=10.0)
        cm.add_point(Point(5, 5), point_id="well", resolution=0.5, dist_min=0, dist_max=3)
        clean_polys, clean_lines, clean_points = cm.generate()

        mg = MeshGenerator(
            background_lc=2.0, verbosity=2,
            heal_shapes=True, heal_tolerance=1.0,
            heal_fix_degenerated=False, heal_fix_small_edges=False, heal_fix_small_faces=False,
        )
        success = mg.generate(clean_polys, clean_lines, clean_points)
        assert success
        assert self._get_mesh_node_near(mg, 5.0, 5.0, 0.01), \
            "No mesh node found near embedded point (5,5) with heal ON (all off, tol=1)"

    def test_multiple_points_create_mesh_vertices_heal_all_off(self):
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(Polygon([(0, 0), (20, 0), (20, 20), (0, 20)]),
                        zone_id=1, resolution=5.0, dist_max=25.0)
        test_pts = [(5, 5), (15, 5), (10, 15)]
        for i, (x, y) in enumerate(test_pts):
            cm.add_point(Point(x, y), point_id=f"pt{i}", resolution=1.0, dist_min=0, dist_max=5)
        clean_polys, clean_lines, clean_points = cm.generate()

        mg = MeshGenerator(
            background_lc=5.0, verbosity=2,
            heal_shapes=True, heal_tolerance=1.0,
            heal_fix_degenerated=False, heal_fix_small_edges=False, heal_fix_small_faces=False,
        )
        success = mg.generate(clean_polys, clean_lines, clean_points)
        assert success

        missing = []
        for x, y in test_pts:
            if not self._get_mesh_node_near(mg, x, y, 0.1):
                missing.append((x, y))
        assert missing == [], f"No mesh node near these points: {missing}"

    def test_large_coords_embedded_point_heal_all_off(self):
        """User scenario: large projected coords, heal on, all off, tol=1."""
        ox, oy = 584000.0, 2366000.0
        cm = ConceptualMesh(crs="EPSG:3857")
        cm.add_polygon(
            Polygon([(ox, oy), (ox + 13000, oy), (ox + 13000, oy + 9000), (ox, oy + 9000)]),
            zone_id=1, resolution=500.0, dist_max=2000.0,
        )
        test_pts = [
            (ox + 2000, oy + 2000),
            (ox + 6500, oy + 4500),
            (ox + 11000, oy + 7000),
        ]
        for i, (x, y) in enumerate(test_pts):
            cm.add_point(Point(x, y), point_id=f"well_{i}", resolution=100.0, dist_min=0, dist_max=500)
        clean_polys, clean_lines, clean_points = cm.generate()

        mg = MeshGenerator(
            background_lc=500.0, verbosity=2,
            heal_shapes=True, heal_tolerance=1.0,
            heal_fix_degenerated=False, heal_fix_small_edges=False, heal_fix_small_faces=False,
        )
        success = mg.generate(clean_polys, clean_lines, clean_points)
        assert success

        missing = []
        for x, y in test_pts:
            if not self._get_mesh_node_near(mg, x, y, 1.0):
                missing.append((x, y))
        assert missing == [], f"No mesh node near these points: {missing}"


# ---------------------------------------------------------------------------
#  Test: Isolation — raw Gmsh API to prove what removeAllDuplicates/healShapes do
# ---------------------------------------------------------------------------

class TestRawGmshPointBehavior:
    """
    Directly test Gmsh API behavior to isolate whether removeAllDuplicates
    or healShapes destroy/renumber dim-0 entities.
    """

    def test_removeAllDuplicates_preserves_interior_point(self):
        """A point inside a surface should survive removeAllDuplicates."""
        gmsh.initialize()
        gmsh.model.add("test_dup")
        occ = gmsh.model.occ

        # Square surface
        p1 = occ.addPoint(0, 0, 0)
        p2 = occ.addPoint(10, 0, 0)
        p3 = occ.addPoint(10, 10, 0)
        p4 = occ.addPoint(0, 10, 0)
        l1 = occ.addLine(p1, p2)
        l2 = occ.addLine(p2, p3)
        l3 = occ.addLine(p3, p4)
        l4 = occ.addLine(p4, p1)
        cl = occ.addCurveLoop([l1, l2, l3, l4])
        s = occ.addPlaneSurface([cl])

        # Interior point
        pt_interior = occ.addPoint(5, 5, 0)

        # Fragment
        all_tags = [(2, s), (0, pt_interior)]
        out_dt, out_map = occ.fragment(all_tags, [])

        pts_before = set(t for d, t in occ.getEntities(0))
        occ.removeAllDuplicates()
        pts_after = set(t for d, t in occ.getEntities(0))

        # The interior point should not have been removed
        # (it's not a duplicate of any vertex)
        assert len(pts_after) >= len(pts_before), \
            f"removeAllDuplicates removed points: before={pts_before}, after={pts_after}"

    def test_removeAllDuplicates_merges_coincident_points(self):
        """Two coincident points should be merged by removeAllDuplicates."""
        gmsh.initialize()
        gmsh.model.add("test_dup_merge")
        occ = gmsh.model.occ

        occ.addPoint(5, 5, 0)
        occ.addPoint(5, 5, 0)

        pts_before = set(t for d, t in occ.getEntities(0))
        assert len(pts_before) == 2

        occ.removeAllDuplicates()
        pts_after = set(t for d, t in occ.getEntities(0))

        # After dedup, only one should remain
        assert len(pts_after) == 1, f"Expected 1 point after dedup, got {pts_after}"

    def test_healShapes_all_off_preserves_points(self):
        """healShapes with all fix options off should not remove points (may renumber)."""
        gmsh.initialize()
        gmsh.model.add("test_heal_noop")
        occ = gmsh.model.occ

        p1 = occ.addPoint(0, 0, 0)
        p2 = occ.addPoint(10, 0, 0)
        p3 = occ.addPoint(10, 10, 0)
        p4 = occ.addPoint(0, 10, 0)
        l1 = occ.addLine(p1, p2)
        l2 = occ.addLine(p2, p3)
        l3 = occ.addLine(p3, p4)
        l4 = occ.addLine(p4, p1)
        cl = occ.addCurveLoop([l1, l2, l3, l4])
        s = occ.addPlaneSurface([cl])
        pt = occ.addPoint(5, 5, 0)

        all_tags = [(2, s), (0, pt)]
        occ.fragment(all_tags, [])

        pts_before_heal = set(t for d, t in occ.getEntities(0))
        n_before = len(pts_before_heal)

        # Save coordinates of interior point for verification
        bb = occ.getBoundingBox(0, pt)
        pt_coord = (round(bb[0], 6), round(bb[1], 6), round(bb[2], 6))

        occ.healShapes(
            [], tolerance=1.0,
            fixDegenerated=False, fixSmallEdges=False, fixSmallFaces=False,
            sewFaces=False, makeSolids=False,
        )
        pts_after_heal = occ.getEntities(0)
        n_after = len(pts_after_heal)

        # NOTE: healShapes renumbers tags even with all options off.
        # What matters is that the same NUMBER of points survive and
        # coordinates are preserved.
        assert n_after == n_before, \
            f"healShapes(all off) lost points: {n_before} -> {n_after}"

        # Verify the interior point's coordinates still exist
        found = False
        for d, t in pts_after_heal:
            bb2 = occ.getBoundingBox(0, t)
            c2 = (round(bb2[0], 6), round(bb2[1], 6), round(bb2[2], 6))
            if c2 == pt_coord:
                found = True
                break
        assert found, f"Interior point at {pt_coord} not found after healShapes"

    def test_healShapes_all_off_preserves_points_large_coords(self):
        """Same as above but with large coordinates matching user scenario."""
        gmsh.initialize()
        gmsh.model.add("test_heal_large")
        occ = gmsh.model.occ

        ox, oy = 584000.0, 2366000.0
        p1 = occ.addPoint(ox, oy, 0)
        p2 = occ.addPoint(ox + 13000, oy, 0)
        p3 = occ.addPoint(ox + 13000, oy + 9000, 0)
        p4 = occ.addPoint(ox, oy + 9000, 0)
        l1 = occ.addLine(p1, p2)
        l2 = occ.addLine(p2, p3)
        l3 = occ.addLine(p3, p4)
        l4 = occ.addLine(p4, p1)
        cl = occ.addCurveLoop([l1, l2, l3, l4])
        s = occ.addPlaneSurface([cl])
        pt = occ.addPoint(ox + 6000, oy + 4500, 0)

        all_tags = [(2, s), (0, pt)]
        occ.fragment(all_tags, [])

        n_before = len(occ.getEntities(0))
        bb = occ.getBoundingBox(0, pt)
        pt_coord = (round(bb[0], 6), round(bb[1], 6), round(bb[2], 6))

        occ.healShapes(
            [], tolerance=1.0,
            fixDegenerated=False, fixSmallEdges=False, fixSmallFaces=False,
            sewFaces=False, makeSolids=False,
        )
        pts_after = occ.getEntities(0)
        n_after = len(pts_after)

        assert n_after == n_before, \
            f"healShapes(all off, tol=1) lost points with large coords: {n_before} -> {n_after}"

        found = False
        for d, t in pts_after:
            bb2 = occ.getBoundingBox(0, t)
            c2 = (round(bb2[0], 6), round(bb2[1], 6), round(bb2[2], 6))
            if c2 == pt_coord:
                found = True
                break
        assert found, f"Interior point at {pt_coord} not found after healShapes (large coords)"

    def test_synchronize_preserves_occ_points(self):
        """Verify that occ.synchronize() doesn't lose dim-0 entities."""
        gmsh.initialize()
        gmsh.model.add("test_sync")
        occ = gmsh.model.occ

        p1 = occ.addPoint(0, 0, 0)
        p2 = occ.addPoint(10, 0, 0)
        p3 = occ.addPoint(10, 10, 0)
        p4 = occ.addPoint(0, 10, 0)
        l1 = occ.addLine(p1, p2)
        l2 = occ.addLine(p2, p3)
        l3 = occ.addLine(p3, p4)
        l4 = occ.addLine(p4, p1)
        cl = occ.addCurveLoop([l1, l2, l3, l4])
        s = occ.addPlaneSurface([cl])
        pt = occ.addPoint(5, 5, 0)

        all_tags = [(2, s), (0, pt)]
        occ.fragment(all_tags, [])
        occ.removeAllDuplicates()

        occ_pts_before_sync = set(t for d, t in occ.getEntities(0))
        occ.synchronize()
        model_pts_after_sync = set(t for d, t in gmsh.model.getEntities(0))

        assert occ_pts_before_sync == model_pts_after_sync, \
            f"synchronize lost points: occ={occ_pts_before_sync}, model={model_pts_after_sync}"
