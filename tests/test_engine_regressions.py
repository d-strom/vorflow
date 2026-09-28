"""Regression tests for MeshGenerator bookkeeping, logging and lazy exports."""
import logging

import numpy as np
import pytest
from shapely.geometry import LineString, Point, box

import vorflow
from vorflow.blueprint import ConceptualMesh
from vorflow.engine import MeshGenerator

pytestmark = pytest.mark.slow  # gmsh-heavy end-to-end tests


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
