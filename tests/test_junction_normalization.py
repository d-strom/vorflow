"""Tests for the isolated embedded line-junction normalisation patch."""
from shapely.geometry import LineString, Polygon

from vorflow.blueprint import ConceptualMesh, _normalise_embedded_line_junctions


def test_exact_crossing_is_explicitly_noded():
    records = [
        {"line_id": "horizontal", "geometry": LineString([(0, 5), (10, 5)]), "embed": True},
        {"line_id": "vertical", "geometry": LineString([(5, 0), (5, 10)]), "embed": True},
    ]
    result, report = _normalise_embedded_line_junctions(records, 0.001)
    assert report["junction_count"] == 1
    assert report["noded_pairs"] == 1
    assert any((x, y) == (5.0, 5.0) for x, y in result[0]["geometry"].coords)
    assert any((x, y) == (5.0, 5.0) for x, y in result[1]["geometry"].coords)


def test_exact_crossing_preserves_existing_vertex_order():
    records = [
        {"line_id": "horizontal", "geometry": LineString([(0, 5), (3, 5), (10, 5)]), "embed": True},
        {"line_id": "vertical", "geometry": LineString([(7, 0), (7, 10)]), "embed": True},
    ]
    result, _ = _normalise_embedded_line_junctions(records, 0.001)
    assert list(result[0]["geometry"].coords) == [
        (0.0, 5.0), (3.0, 5.0), (7.0, 5.0), (10.0, 5.0)
    ]


def test_nearby_endpoint_is_snapped_but_parallel_lines_are_not_merged():
    records = [
        {"line_id": "a", "geometry": LineString([(0, 0), (10, 0)]), "embed": True},
        {"line_id": "b", "geometry": LineString([(10.0005, 0.0002), (10.0005, 5)]), "embed": True},
        {"line_id": "parallel", "geometry": LineString([(0, 0.0005), (9, 0.0005)]), "embed": True},
    ]
    result, report = _normalise_embedded_line_junctions(records, 0.001)
    assert report["endpoint_snaps"] == 1
    assert result[1]["geometry"].coords[0][:2] == result[0]["geometry"].coords[-1][:2]
    assert list(result[2]["geometry"].coords) == [(0.0, 0.0005), (9.0, 0.0005)]


def test_field_only_lines_are_left_untouched():
    records = [
        {"line_id": "embedded", "geometry": LineString([(0, 0), (10, 10)]), "embed": True},
        {"line_id": "field", "geometry": LineString([(0, 10), (10, 0)]), "embed": False},
    ]
    result, report = _normalise_embedded_line_junctions(records, 0.001)
    assert report["junction_count"] == 0
    assert list(result[1]["geometry"].coords) == list(records[1]["geometry"].coords)


def _public_mesh(**kwargs):
    mesh = ConceptualMesh(**kwargs)
    mesh.add_polygon(Polygon([(0, 0), (12, 0), (12, 12), (0, 12)]), zone_id="domain")
    return mesh


def test_normalisation_can_be_disabled_through_public_api():
    mesh = _public_mesh(normalize_line_junctions=False)
    mesh.add_line(LineString([(1, 6), (11, 6)]), line_id="horizontal", resolution=1, densify=False)
    mesh.add_line(LineString([(6, 1), (6, 11)]), line_id="vertical", resolution=1, densify=False)

    _, lines, _ = mesh.generate()

    assert mesh.junction_diagnostics == {
        "enabled": False,
        "junction_count": 0,
        "noded_pairs": 0,
        "endpoint_snaps": 0,
        "junctions": [],
    }
    assert all((6.0, 6.0) not in geometry.coords for geometry in lines.geometry)


def test_generate_can_override_junction_tolerance():
    mesh = _public_mesh(junction_tolerance=0)
    mesh.add_line(LineString([(1, 6), (6, 6)]), line_id="left", resolution=1, densify=False)
    mesh.add_line(
        LineString([(6.0005, 6.0002), (6.0005, 11)]),
        line_id="up",
        resolution=1,
        densify=False,
    )

    mesh.generate(junction_tolerance=0.001)

    assert mesh.junction_diagnostics["endpoint_snaps"] == 1
    assert mesh.junction_diagnostics["tolerance"] == 0.001
