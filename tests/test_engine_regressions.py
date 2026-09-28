"""Regression tests for MeshGenerator bookkeeping, logging and lazy exports."""
import logging
import warnings

import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon, box

import vorflow
from vorflow.blueprint import ConceptualMesh
from vorflow.engine import MeshGenerator, _SurvivorIndex

pytestmark = pytest.mark.slow  # gmsh-heavy end-to-end tests


def test_survivor_index_picks_same_tag_then_nearest_within_tolerance():
    survivors = _SurvivorIndex({(0, 1): (0.0, 0.0, 0.0), (0, 2): (4e-7, 0.0, 0.0),
                                (0, 3): (0.5, 0.0, 0.0)}, tolerance=1e-6)
    # Two survivors round to the same 6-decimal key; the nearer one wins.
    assert survivors.match(0, 9, (3e-7, 0.0, 0.0)) == 2
    assert survivors.match(0, 1, (3e-7, 0.0, 0.0)) == 1
    # Offsets straddling a rounding boundary still match.
    assert survivors.match(0, 9, (0.5000004, 0.0, 0.0)) == 3
    assert survivors.match(0, 9, (0.25, 0.0, 0.0)) is None
    assert survivors.match(1, 9, (0.0, 0.0, 0.0)) is None


def _barrier_model(with_point):
    """Domain with a barrier at x=20 and, optionally, a fine point far from it."""
    cm = ConceptualMesh()
    cm.add_polygon(box(0, 0, 100, 100), zone_id=1, resolution=10)
    cm.add_line(LineString([(20, 5), (20, 95)]), "barrier", resolution=10, is_barrier=True)
    if with_point:
        cm.add_point(Point(80, 80), "well", resolution=0.5)
    return cm.generate()


def test_point_field_does_not_leak_onto_barrier_with_same_feature_id():
    # Line 0's straddle points and point feature 0 used to share
    # gmsh_map['points'][0], so the point's fine field also refined the barrier.
    def nodes_near_barrier(with_point):
        mg = MeshGenerator(background_lc=10, verbosity=0)
        assert mg.generate(*_barrier_model(with_point))
        return int(np.sum(np.abs(mg.nodes[:, 0] - 20) < 5))

    assert nodes_near_barrier(True) == nodes_near_barrier(False)


def test_barrier_straddle_points_are_mapped_separately_from_point_features():
    polys, lines, points = _barrier_model(with_point=True)
    mg = MeshGenerator(background_lc=10, verbosity=0)
    mg._initialize_gmsh()
    try:
        gmsh_map = mg._add_geometry(polys, lines, points)
    finally:
        mg._finalize_gmsh()
    assert len(gmsh_map["points"][0]) == 1
    assert len(gmsh_map["straddle_points"][0]) > 2


def test_element_grid_is_built_lazily_and_cached():
    mg = MeshGenerator(background_lc=10, verbosity=0)
    assert mg.generate(*_barrier_model(with_point=False))
    assert mg.element_grid is None
    grid = mg.get_element_grid()
    assert not grid.empty
    assert mg.element_grid is not None
    assert len(mg.get_element_grid("triangles")) == len(grid)


def test_element_grid_zones_ignore_field_only_polygons():
    cm = ConceptualMesh()
    cm.add_polygon(box(0, 0, 20, 20), zone_id="domain", resolution=4)
    cm.add_polygon(box(5, 5, 15, 15), zone_id="sizing", resolution=1, z_order=5, embed=False)
    mg = MeshGenerator(background_lc=4, verbosity=0)
    assert mg.generate(*cm.generate())
    assert mg.get_element_grid()["zone_id"].eq("domain").all()


def test_mesh_generator_verbosity_does_not_change_package_level():
    logger = logging.getLogger("vorflow")
    before = logger.level
    MeshGenerator(background_lc=10, verbosity=0)
    assert logger.level == before

    mg = MeshGenerator(background_lc=10, verbosity=0)
    assert mg.generate(*_barrier_model(with_point=False))
    assert logger.level == before


def test_mesh_generator_verbosity_scopes_generate_output(caplog):
    model = _barrier_model(with_point=False)
    vorflow.set_verbosity(1, console=False)
    try:
        with caplog.at_level(logging.INFO, logger="vorflow"):
            MeshGenerator(background_lc=10, verbosity=0).generate(*model)
        assert not [r for r in caplog.records if r.levelno == logging.INFO]

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="vorflow"):
            MeshGenerator(background_lc=10).generate(*model)
        assert any("Generating Triangular Mesh" in r.getMessage() for r in caplog.records)
    finally:
        vorflow.set_verbosity(1)


def _legacy_zone_mesh(**legacy_kwargs):
    """Mesh a domain holding one zone added with pre-0.1 add_polygon keywords."""
    cm = ConceptualMesh()
    cm.add_polygon(box(0, 0, 60, 60), zone_id="domain", resolution=8)
    with pytest.warns(DeprecationWarning):
        cm.add_polygon(box(20, 20, 40, 40), zone_id="zone", resolution=4, z_order=1, **legacy_kwargs)
    mg = MeshGenerator(background_lc=8, verbosity=0)
    with warnings.catch_warnings():
        # dist_max (via dist_max_out) also warns from the engine's legacy path.
        warnings.simplefilter("ignore", DeprecationWarning)
        assert mg.generate(*cm.generate())
    return mg


def test_legacy_polygon_keywords_are_accepted_with_deprecation_warnings():
    mg = _legacy_zone_mesh(mesh_refinement=True, dist_max_out=10.0)
    assert len(mg.nodes) > 0


def test_legacy_border_density_grades_from_fine_border_to_resolution():
    # Densified boundary vertices alone force tiny edges on the border with an
    # abrupt jump inward (mean gamma ~0.77 there); the border field grades the
    # size smoothly into the zone.
    mg = _legacy_zone_mesh(border_density=1.0, dist_max_in=4.0)
    grid = mg.get_element_grid().merge(
        mg.get_triangular_quality()[["element_tag", "gamma"]], on="element_tag"
    )
    zone = box(20, 20, 40, 40)
    distance_to_border = grid.geometry.centroid.apply(zone.exterior.distance)
    transition = grid[(distance_to_border > 1) & (distance_to_border < 3)]
    assert transition.geometry.area.mean() < 1.5
    assert grid[distance_to_border < 3]["gamma"].mean() > 0.9


def test_generate_rejects_missing_background_lc_before_touching_gmsh():
    import gmsh

    mg = MeshGenerator(verbosity=0)
    with pytest.raises(ValueError, match="background_lc must be provided"):
        mg.generate(*_barrier_model(with_point=False))
    assert not gmsh.is_initialized()


def test_heal_remap_keeps_surfaces_with_identical_bounding_boxes_apart():
    # Two triangles tiling a square share a bounding box; matching healed
    # surfaces by bbox alone gave the fine triangle's field to both.
    cm = ConceptualMesh()
    cm.add_polygon(Polygon([(0, 0), (10, 0), (10, 10)]), zone_id="fine", resolution=1)
    cm.add_polygon(Polygon([(0, 0), (10, 10), (0, 10)]), zone_id="coarse", resolution=3)
    clean = cm.generate()
    maps = {}
    for heal in (False, True):
        mg = MeshGenerator(background_lc=3, heal_shapes=heal, verbosity=0)
        mg._initialize_gmsh()
        try:
            maps[heal] = mg._add_geometry(*clean)["surfaces"]
        finally:
            mg._finalize_gmsh()
    assert maps[True] == maps[False]
    assert len(maps[True][0]) == len(maps[True][1]) == 1
