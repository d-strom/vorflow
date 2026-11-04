"""Exercise the plugin's actual API adapters without requiring QGIS imports."""

import ast
import csv
import inspect
import math
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator
from vorflow.fields import GeometricGrowthField
from vorflow.utils import build_connectivity


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "vorflow_qgis" / "vorflow_plugin.py"


def _plugin_adapters():
    tree = ast.parse(PLUGIN.read_text(encoding="utf-8"))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_callable_parameter_info", "build_conceptual_mesh"}
    ]
    dialog = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == "VorflowDialog")
    methods = [node for node in dialog.body if isinstance(node, ast.FunctionDef)
               and node.name in {"build_disv_geometry", "_write_text", "write_disv_dataset",
                                 "format_elapsed_time"}]
    bridge = ast.ClassDef(name="PluginBridge", bases=[], keywords=[], body=methods,
                         decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[*functions, bridge], type_ignores=[]))
    namespace = {"inspect": inspect, "math": math, "os": os, "csv": csv}
    exec(compile(module, str(PLUGIN), "exec"), namespace)
    return namespace["build_conceptual_mesh"], namespace["PluginBridge"]()


@pytest.mark.parametrize(("seconds", "expected"), [
    (0, "0.0 s"),
    (59.99, "59.9 s"),
    (60, "1 min 00.0 s"),
    (3599.99, "59 min 59.9 s"),
    (3661.25, "1 h 01 min 01.2 s"),
])
def test_elapsed_time_changes_units_without_rounding_into_the_next_minute(seconds, expected):
    _, bridge = _plugin_adapters()
    assert bridge.format_elapsed_time(seconds) == expected


@pytest.mark.parametrize("fraction", [0.0, 0.5, 0.25])
def test_plugin_junction_controls_reach_upstream_and_can_disable_snapping(fraction):
    build, _ = _plugin_adapters()
    blueprint = build(ConceptualMesh, crs="EPSG:3006", connectivity_tolerance=0.001,
                      line_junction_fraction=fraction)
    blueprint.add_polygon(box(0, 0, 100, 100), zone_id="domain", resolution=10)
    blueprint.add_line(LineString([(10, 50), (90, 50)]), line_id="river", resolution=10)
    blueprint.add_line(LineString([(50, 10), (50, 49)]), line_id="tributary", resolution=10)
    _, lines, _ = blueprint.generate()

    assert blueprint.line_junction_fraction == fraction
    assert blueprint.connectivity_tolerance == 0.001
    if fraction:
        assert len(blueprint.junction_diagnostics["snapped_ends"]) == 1
        assert lines.geometry.iloc[0].intersects(lines.geometry.iloc[1])
    else:
        assert blueprint.junction_diagnostics["snapped_ends"] == []
        assert lines.geometry.iloc[0].distance(lines.geometry.iloc[1]) > 0.9


def test_plugin_rejects_legacy_api_instead_of_ignoring_junction_controls():
    build, _ = _plugin_adapters()

    class LegacyMesh:
        def __init__(self, crs=None, connectivity_tolerance=0.001):
            pytest.fail("An incompatible API must be rejected before construction")

    with pytest.raises(RuntimeError, match="Vorflow 0.3.0"):
        build(LegacyMesh, crs=None, connectivity_tolerance=0.001, line_junction_fraction=0)


def _grid():
    return gpd.GeoDataFrame(
        {"node_id": [7, 11, 42], "x": [0.2, 1.7, 1.8], "y": [0.3, 0.4, 1.6]},
        geometry=[box(0, 0, 1, 2), box(1, 0, 2, 1), box(1, 1, 2, 2)],
        crs="EPSG:3006",
    )


def _rings(vertices, cells):
    xy = {iv: (x, y) for iv, x, y in vertices}
    return [[xy[iv] for iv in cell[4]] for cell in cells]


def test_plugin_disv_keeps_generators_node_ids_and_hanging_edge_connections():
    _, bridge = _plugin_adapters()
    grid = _grid()
    vertices, cells, rows = bridge.build_disv_geometry(grid, 0.0)

    assert [row[0] for row in vertices] == list(range(1, len(vertices) + 1))
    assert [cell[0] for cell in cells] == [1, 2, 3]
    np.testing.assert_allclose([cell[1:3] for cell in cells], grid[["x", "y"]])
    assert [row[-1] for row in rows] == [7, 11, 42]
    rings = _rings(vertices, cells)
    assert (1.0, 1.0) in rings[0]
    assert all(not Polygon(ring).exterior.is_ccw for ring in rings)
    assert all(Polygon(ring).equals(geom) for ring, geom in zip(rings, grid.geometry))
    # Both right-hand cells must share a complete edge with the tall left cell.
    for cell in cells[1:]:
        assert len(set(cells[0][4]) & set(cell[4])) == 2


def test_plugin_disv_files_preserve_topology_and_result_mapping(tmp_path):
    _, bridge = _plugin_adapters()
    vertices, cells, rows = bridge.build_disv_geometry(_grid(), 0.0)
    bridge.write_disv_dataset(str(tmp_path), "sample", vertices, cells, rows,
                              2, 10.0, -10.0, 5.0, 1.0, "METERS", "EPSG:3006")
    text = (tmp_path / "sample.disv").read_text()
    cell_block = text.split("BEGIN CELL2D\n")[1].split("END CELL2D")[0]
    saved = [line.split() for line in cell_block.strip().splitlines()]
    np.testing.assert_allclose([[float(v) for v in row[1:3]] for row in saved],
                               _grid()[["x", "y"]])
    assert [int(row[3]) for row in saved] == [5, 4, 4]
    assert all(int(iv) >= 1 for row in saved for iv in row[4:])
    with (tmp_path / "sample_cell2d.csv").open(newline="") as handle:
        mapping = list(csv.DictReader(handle))
    assert [(int(row["icell2d"]), int(row["node_id"])) for row in mapping] == [(1, 7), (2, 11), (3, 42)]


def test_plugin_simulation_loads_in_flopy_with_generator_centres(tmp_path):
    flopy = pytest.importorskip("flopy")
    _, bridge = _plugin_adapters()
    vertices, cells, rows = bridge.build_disv_geometry(_grid(), 0.0)
    bridge.write_disv_dataset(str(tmp_path), "sample", vertices, cells, rows,
                              2, 10.0, -10.0, 5.0, 1.0, "METERS", "EPSG:3006")
    simulation = flopy.mf6.MFSimulation.load(sim_ws=str(tmp_path), verbosity_level=0)
    model = simulation.get_model("sample")
    assert model.disv.ncpl.get_data() == 3
    assert model.disv.nlay.get_data() == 2
    np.testing.assert_allclose(model.modelgrid.xcellcenters, _grid()["x"])
    np.testing.assert_allclose(model.modelgrid.ycellcenters, _grid()["y"])
    assert sorted(model.modelgrid.neighbors(method="rook")[0]) == [1, 2]


@pytest.mark.parametrize("tolerance", [-1.0, float("nan"), float("inf")])
def test_plugin_rejects_invalid_disv_tolerance(tolerance):
    _, bridge = _plugin_adapters()
    with pytest.raises(ValueError, match="tolerance"):
        bridge.build_disv_geometry(_grid(), tolerance)


@pytest.mark.slow
def test_plugin_quality_profile_mesh_exports_with_every_upstream_connection():
    build, bridge = _plugin_adapters()
    blueprint = build(ConceptualMesh, crs="EPSG:3006", connectivity_tolerance=0.001,
                      line_junction_fraction=0.5)
    blueprint.add_polygon(box(0, 0, 100, 100), zone_id="domain", resolution=20)
    blueprint.add_point(Point(50, 50), point_id="well", resolution=5, hex_ring=True,
                        fields=[GeometricGrowthField(growth_factor=1.1,
                                growth_model="continuous_metric", sampling=25)])
    mesher = MeshGenerator(conceptual_mesh=blueprint, background_lc=20, verbosity=0)
    mesher.generate()
    grid = VoronoiTessellator(mesher, boundary_centering="inset_mirror",
                             boundary_inset_fraction=0.25, lloyd_iterations=100,
                             lloyd_size_smoothing=5, min_face_fraction=0.2).generate()
    vertices, cells, _ = bridge.build_disv_geometry(grid, 0.0)
    rings = _rings(vertices, cells)
    assert all(Polygon(ring).symmetric_difference(geom).area < 1e-8
               for ring, geom in zip(rings, grid.geometry))
    shared = {}
    for position, cell in enumerate(cells):
        ids = cell[4]
        for edge in zip(ids, ids[1:] + ids[:1]):
            shared.setdefault(frozenset(edge), set()).add(position)
    actual = {frozenset(pair) for pair in shared.values() if len(pair) == 2}
    connectivity = build_connectivity(grid)
    expected = {frozenset(pair) for pair in zip(connectivity["cell_id_1"], connectivity["cell_id_2"])}
    assert expected <= actual
    assert grid.node_id.dtype == np.dtype("int64")
