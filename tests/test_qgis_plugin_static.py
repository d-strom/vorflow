"""Static checks for the QGIS wrapper without requiring a QGIS installation."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "vorflow_qgis" / "vorflow_plugin.py"


def _plugin_source():
    return PLUGIN.read_text(encoding="utf-8")


def _load_normaliser():
    tree = ast.parse(_plugin_source(), filename=str(PLUGIN))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "normalise_feature_parameters"
    )
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(PLUGIN), "exec"), namespace)
    return namespace["normalise_feature_parameters"]


def test_qgis_plugin_source_compiles_without_importing_qgis():
    compile(_plugin_source(), str(PLUGIN), "exec")


def test_plugin_only_geometry_controls_translate_to_vorflow_arguments():
    normalise = _load_normaliser()

    assert normalise({"densify": True, "densify_spacing": 2.5})["densify"] == 2.5
    assert normalise({"densify": False, "densify_spacing": 2.5})["densify"] is False
    assert normalise({"straddle_width": 0.0})["straddle_width"] is None
    assert normalise({"straddle_width": 0.4})["straddle_width"] == 0.4


def test_quality_profile_and_new_controls_are_present():
    source = _plugin_source()

    assert 'RECOMMENDED_GROWTH_FACTOR = 1.1' in source
    assert 'RECOMMENDED_GROWTH_MODEL = "continuous_metric"' in source
    for keyword in (
        "mesh_algorithm",
        "optimization_cycles",
        "tolerance_initial_delaunay",
        "boundary_centering",
        "lloyd_damping",
        "lloyd_tolerance",
        "lloyd_size_smoothing",
    ):
        assert f'"{keyword}"' in source


def test_plugin_metadata_version_matches_audit_release():
    metadata = (ROOT / "vorflow_qgis" / "metadata.txt").read_text(encoding="utf-8")
    assert "version=0.17.0" in metadata
