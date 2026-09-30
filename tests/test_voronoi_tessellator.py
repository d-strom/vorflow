import logging

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from vorflow.blueprint import ConceptualMesh
import vorflow.tessellator as tessellator_module
from vorflow.tessellator import VoronoiTessellator
from vorflow.utils import boundary_connectivity_report, build_connectivity


class DummyMeshGenerator:
    def __init__(self, nodes, tags, zones_gdf):
        self.nodes = nodes
        self.node_tags = tags
        self.zones_gdf = zones_gdf


def _build_conceptual_mesh():
    cm = ConceptualMesh()
    box = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    cm.add_polygon(box, zone_id=42)
    return cm


def _plain_barrier_tessellator():
    cm = _build_conceptual_mesh()
    cm.add_line(
        LineString([(0.75, 0), (0.75, 1)]),
        line_id="barrier",
        resolution=1.0,
        is_barrier=True,
    )
    cm.generate()
    mesh_gen = DummyMeshGenerator(nodes=np.empty((0, 2)), tags=[], zones_gdf=cm.clean_polygons)
    return VoronoiTessellator(mesh_gen, cm), cm.clean_lines.iloc[0].geometry


def test_enforce_barriers_preserves_uint64_ids_and_split_fragments():
    """Catches coercion of newly assigned barrier-fragment IDs to float."""
    tessellator, barrier = _plain_barrier_tessellator()
    original_id = 2**64 - 2
    grid = gpd.GeoDataFrame(
        {"node_id": np.array([original_id], dtype=np.uint64), "x": [0.5], "y": [0.5]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
    )

    result = tessellator._enforce_barriers(grid)

    assert result["node_id"].dtype == np.dtype("uint64")
    assert result["node_id"].is_unique
    assert set(result["node_id"]) == {original_id, original_id + 1}
    assert result.loc[result["node_id"] == original_id, "geometry"].iloc[0].area == pytest.approx(0.75)
    assert result.loc[result["node_id"] == original_id + 1, "geometry"].iloc[0].area == pytest.approx(0.25)
    assert result.geometry.area.sum() == pytest.approx(1.0)
    assert not result.geometry.crosses(barrier).any()


def test_enforce_barriers_retains_cell_and_logs_warning_when_split_fails(monkeypatch, caplog):
    """Catches removing a cell when Shapely raises while splitting it."""
    tessellator, _ = _plain_barrier_tessellator()
    grid = gpd.GeoDataFrame(
        {"node_id": np.array([7], dtype=np.uint64), "x": [0.5], "y": [0.5]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
    )

    def raise_split_error(*args, **kwargs):
        raise RuntimeError("split failed")

    monkeypatch.setattr(tessellator_module, "split", raise_split_error)
    vorflow_logger = logging.getLogger("vorflow")
    old_propagate = vorflow_logger.propagate
    vorflow_logger.propagate = True
    try:
        with caplog.at_level("WARNING", logger="vorflow.tessellator"):
            result = tessellator._enforce_barriers(grid)
    finally:
        vorflow_logger.propagate = old_propagate

    assert result.equals(grid)
    assert "Warning: Failed to split cell 7: split failed" in caplog.messages


def test_voronoi_clips_to_domain_and_assigns_zones():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()

    nodes = np.array(
        [
            [0.2, 0.2],
            [0.8, 0.2],
            [0.2, 0.8],
            [0.8, 0.8],
        ]
    )
    tags = [1, 2, 3, 4]

    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    tessellator = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True)
    grid = tessellator.generate()

    assert not grid.empty
    assert grid["zone_id"].nunique() == 1
    assert grid["zone_id"].iloc[0] == 42

    domain = clean_polys.iloc[0].geometry
    assert grid.geometry.apply(lambda cell: cell.intersects(domain)).all()

    assert set(grid["node_id"]) == set(tags)
    assert "centroid_x" in grid.columns
    assert "centroid_y" in grid.columns


def test_voronoi_assigns_equal_priority_shared_border_nodes_by_zone_index(monkeypatch):
    cm = ConceptualMesh()
    cm.add_polygon(Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]), zone_id="left", z_order=0)
    cm.add_polygon(Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]), zone_id="right", z_order=0)
    clean_polys, _, _ = cm.generate()

    nodes = np.array([[0.5, 0.5], [1.0, 0.5], [1.5, 0.5], [1.0, 0.25]])
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    def reversed_shared_border_matches(points, zones, how, predicate):
        records = []
        for _, point in points.iterrows():
            records.extend(
                [
                    {
                        "node_id": point["node_id"],
                        "geometry": point.geometry,
                        "index_right": 1,
                        "zone_id": "right",
                        "z_order": 0,
                    },
                    {
                        "node_id": point["node_id"],
                        "geometry": point.geometry,
                        "index_right": 0,
                        "zone_id": "left",
                        "z_order": 0,
                    },
                ]
            )
        return gpd.GeoDataFrame(records, geometry="geometry", crs=points.crs)

    monkeypatch.setattr(gpd, "sjoin", reversed_shared_border_matches)
    grid = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True).generate()

    shared_border_zones = grid.loc[grid["node_id"].isin([2, 4]), "zone_id"]
    assert shared_border_zones.tolist() == ["left", "left"]


def test_boundary_centering_default_matches_clip_mode():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()

    nodes = np.array(
        [
            [0.0, 0.25],
            [0.0, 0.75],
            [0.5, 0.25],
            [0.5, 0.75],
            [1.0, 0.25],
            [1.0, 0.75],
        ]
    )
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    default_grid = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True).generate()
    clip_grid = VoronoiTessellator(
        mesh_gen,
        cm,
        clip_to_boundary=True,
        boundary_centering="clip",
    ).generate()

    ordered_default = default_grid.sort_values("node_id").reset_index(drop=True)
    ordered_clip = clip_grid.sort_values("node_id").reset_index(drop=True)

    assert ordered_default["node_id"].tolist() == ordered_clip["node_id"].tolist()
    assert np.allclose(ordered_default["x"], ordered_clip["x"])
    assert np.allclose(ordered_default["y"], ordered_clip["y"])
    assert all(
        geom_a.equals_exact(geom_b, tolerance=1e-12)
        for geom_a, geom_b in zip(ordered_default.geometry, ordered_clip.geometry)
    )
    assert "source_x" not in default_grid.columns
    assert "boundary_centered" not in default_grid.columns


def test_boundary_inset_mirror_shifts_non_corner_boundary_centers_inward():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()

    nodes = np.array(
        [
            [0.0, 0.25],
            [0.0, 0.75],
            [0.5, 0.25],
            [0.5, 0.75],
            [1.0, 0.25],
            [1.0, 0.75],
        ]
    )
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    grid = VoronoiTessellator(
        mesh_gen,
        cm,
        clip_to_boundary=True,
        boundary_centering="inset_mirror",
    ).generate()

    assert not grid.empty
    assert {"source_x", "source_y", "boundary_centering", "boundary_inset", "boundary_centered"}.issubset(
        grid.columns
    )

    shifted_left = grid[np.isclose(grid["source_x"], 0.0)]
    shifted_right = grid[np.isclose(grid["source_x"], 1.0)]
    assert shifted_left["boundary_centered"].all()
    assert shifted_right["boundary_centered"].all()
    assert (shifted_left["x"] > shifted_left["source_x"]).all()
    assert (shifted_right["x"] < shifted_right["source_x"]).all()
    assert (grid["boundary_inset"] >= 0.0).all()


def test_boundary_inset_mirror_offsets_scale_with_local_spacing():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()

    nodes = np.array(
        [
            [0.0, 0.25],
            [0.0, 0.75],
            [1.0, 0.20],
            [1.0, 0.40],
            [0.5, 0.5],
        ]
    )
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)
    tessellator = VoronoiTessellator(mesh_gen, cm, boundary_centering="inset_mirror")

    prepared, _, ghosts, metadata = tessellator._prepare_boundary_centered_nodes(nodes, tags)

    coarse_inset = metadata.loc[metadata["source_x"] == 0.0, "boundary_inset"].iloc[0]
    fine_inset = metadata.loc[metadata["source_x"] == 1.0, "boundary_inset"].iloc[0]

    assert coarse_inset > fine_inset
    assert np.isclose(coarse_inset, 0.25)
    assert np.isclose(fine_inset, 0.10)
    assert len(ghosts) == int(metadata["boundary_centered"].sum())
    assert not np.allclose(prepared, nodes)


def test_boundary_inset_mirror_skips_sharp_corners():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()

    nodes = np.array(
        [
            [0.0, 0.0],
            [0.0, 0.5],
            [0.5, 0.5],
            [1.0, 0.5],
        ]
    )
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)
    tessellator = VoronoiTessellator(mesh_gen, cm, boundary_centering="inset_mirror")

    prepared, _, _, metadata = tessellator._prepare_boundary_centered_nodes(nodes, tags)

    corner_row = metadata[metadata["node_id"] == 1].iloc[0]
    assert not bool(corner_row["boundary_centered"])
    assert corner_row["boundary_inset"] == 0.0
    assert np.allclose(prepared[0], nodes[0])


def _staggered_lattice_nodes():
    """Unit-box lattice with boundary nodes on the edges and staggered interior rows."""
    nodes = []
    for x in (0.25, 0.5, 0.75):
        nodes.append([x, 0.0])
        nodes.append([x, 1.0])
    for y in (0.25, 0.5, 0.75):
        nodes.append([0.0, y])
        nodes.append([1.0, y])
    for x in (0.375, 0.625):
        nodes.append([x, 0.25])
        nodes.append([x, 0.75])
    for x in (0.25, 0.5, 0.75):
        nodes.append([x, 0.5])
    return np.array(nodes, dtype=float)


def test_boundary_connectivity_report_restricts_to_boundary_cells():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()
    domain_geom = unary_union(clean_polys.geometry)

    coords = [0.125, 0.375, 0.625, 0.875]
    nodes = np.array([[x, y] for x in coords for y in coords])
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    grid = VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True).generate()
    full = build_connectivity(grid, center="centroid")
    report = boundary_connectivity_report(grid, domain_geom, center="centroid")

    assert not report.empty
    assert len(report) < len(full)

    boundary = domain_geom.boundary
    cell_geoms = grid.geometry.reset_index(drop=True)
    boundary_cells = {
        i for i, geom in enumerate(cell_geoms) if geom.distance(boundary) <= 1e-8
    }
    assert (
        report["cell_id_1"].isin(boundary_cells) | report["cell_id_2"].isin(boundary_cells)
    ).all()

    interior_pairs = full[
        ~full["cell_id_1"].isin(boundary_cells) & ~full["cell_id_2"].isin(boundary_cells)
    ]
    assert not interior_pairs.empty
    merged = report.merge(
        interior_pairs[["cell_id_1", "cell_id_2"]],
        on=["cell_id_1", "cell_id_2"],
        how="inner",
    )
    assert merged.empty


def test_inset_mirror_improves_boundary_cell_centroid_orthogonality():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()
    domain_geom = unary_union(clean_polys.geometry)

    nodes = _staggered_lattice_nodes()
    tags = np.arange(1, len(nodes) + 1)
    mesh_gen = DummyMeshGenerator(nodes=nodes, tags=tags, zones_gdf=clean_polys)

    clip_grid = VoronoiTessellator(
        mesh_gen, cm, clip_to_boundary=True, boundary_centering="clip"
    ).generate()
    mirror_grid = VoronoiTessellator(
        mesh_gen, cm, clip_to_boundary=True, boundary_centering="inset_mirror"
    ).generate()

    clip_report = boundary_connectivity_report(clip_grid, domain_geom, center="centroid")
    mirror_report = boundary_connectivity_report(mirror_grid, domain_geom, center="centroid")

    assert not clip_report.empty
    assert not mirror_report.empty

    clip_error = clip_report["ortho_error"].mean()
    mirror_error = mirror_report["ortho_error"].mean()
    assert clip_error > 1.0  # the staggered clip-mode grid is measurably non-orthogonal
    assert mirror_error < clip_error


def test_boundary_centering_rejects_invalid_mode():
    cm = _build_conceptual_mesh()
    clean_polys, _, _ = cm.generate()
    mesh_gen = DummyMeshGenerator(
        nodes=np.array([[0.2, 0.2], [0.8, 0.2], [0.5, 0.8]]),
        tags=np.array([1, 2, 3]),
        zones_gdf=clean_polys,
    )

    with pytest.raises(ValueError, match="boundary_centering"):
        VoronoiTessellator(mesh_gen, cm, boundary_centering="mirror")


class TestExportToShapefile:
    def _tessellator_with_grid(self):
        cm = _build_conceptual_mesh()
        clean_polys, _, _ = cm.generate()
        nodes = np.array([[0.2, 0.2], [0.8, 0.2], [0.2, 0.8], [0.8, 0.8]])
        mesh_gen = DummyMeshGenerator(nodes=nodes, tags=[1, 2, 3, 4], zones_gdf=clean_polys)
        return VoronoiTessellator(mesh_gen, cm, clip_to_boundary=True)

    def test_writes_readable_shapefile(self, tmp_path):
        tess = self._tessellator_with_grid()
        grid = tess.generate()
        path = tmp_path / "grid.shp"
        tess.export_to_shapefile(str(path))
        assert path.exists()
        back = gpd.read_file(path)
        assert len(back) == len(grid)
        assert back.geometry.is_valid.all()
        assert back.geometry.area.sum() == pytest.approx(grid.geometry.area.sum())

    def test_no_grid_writes_nothing(self, tmp_path):
        tess = self._tessellator_with_grid()  # generate() never called
        path = tmp_path / "grid.shp"
        tess.export_to_shapefile(str(path))
        assert not path.exists()
