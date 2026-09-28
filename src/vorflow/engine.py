from __future__ import annotations

import logging
import dataclasses

import gmsh
import math
import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from .fields import (
    DEFAULT_GROWTH_FACTOR,
    ConstantField,
    GeometricGrowthField,
    MeshField,
    ThresholdField,
    _BorderGradingField,
)
from ._log import current_verbosity, verbosity_scope
from . import buffer
from ._features import (
    feature_lc,
    is_embedded,
    polygon_parts,
    positive_number,
    row_bool,
    sanitize_coords,
)


logger = logging.getLogger(__name__)



def _unit_tangent(line, d, probe):
    """Unit tangent of ``line`` at distance ``d`` along it.

    The direction is estimated from a short chord of length ``probe``. The
    caller chooses ``probe`` proportional to the line length so the estimate
    is CRS-unit independent (a fixed absolute step would span whole features
    on short lines and blunt corners on curved ones).
    """
    length = line.length
    if d >= length - probe:
        p1 = line.interpolate(max(d - probe, 0.0))
        p2 = line.interpolate(d)
    else:
        p1 = line.interpolate(d)
        p2 = line.interpolate(d + probe)
    dx, dy = p2.x - p1.x, p2.y - p1.y
    mag = math.hypot(dx, dy)
    if mag == 0:
        # Degenerate (zero-length) input: any unit vector keeps the straddle
        # pair perpendicular and non-coincident.
        return 1.0, 0.0
    return dx / mag, dy / mag


def _to_key(dim, tag):
    """Normalize a gmsh (dim, tag) pair into a hashable dict key."""
    return (int(dim), int(tag))


# Coordinate rounding used to match OCC entities across removeAllDuplicates
# (~nm) and healShapes (0.1 mm, absorbing its ~1e-6 drift).
_DEDUP_COORD_DECIMALS = 6
_HEAL_COORD_DECIMALS = 4


def _rounded(values, decimals):
    """Hashable coordinate key: ``values`` rounded to ``decimals`` places."""
    return tuple(round(v, decimals) for v in values)


def _embedded_zones(zones_gdf):
    """Zones that belong to the meshed domain (drops field-only polygons)."""
    if zones_gdf is None or zones_gdf.empty or "embed" not in zones_gdf.columns:
        return zones_gdf
    embed = zones_gdf["embed"].fillna(True).astype(bool)
    return zones_gdf[embed]


def _assign_zones_to_elements(grid, zones_gdf):
    """Assign a zone to each element by spatially joining element centroids.

    When a centroid intersects several zones (overlaps or shared borders) the
    tie is broken deterministically: highest ``z_order`` wins, then the zone
    that appears earliest in ``zones_gdf``.
    """
    if zones_gdf is None or zones_gdf.empty or "zone_id" not in zones_gdf.columns:
        grid["zone_id"] = pd.NA
        grid["z_order"] = pd.NA
        return grid

    zone_cols = ["geometry", "zone_id"]
    if "z_order" in zones_gdf.columns:
        zone_cols.append("z_order")
    zones = zones_gdf[zone_cols].reset_index(drop=True)
    centroids = gpd.GeoDataFrame(
        {"element_tag": grid["element_tag"]},
        geometry=gpd.points_from_xy(grid["centroid_x"], grid["centroid_y"]),
        crs=grid.crs,
    )
    joined = gpd.sjoin(centroids, zones, how="left", predicate="intersects")
    sort_cols, ascending = ["element_tag"], [True]
    if "z_order" in joined.columns:
        sort_cols += ["z_order", "index_right"]
        ascending += [False, True]
    else:
        sort_cols += ["index_right"]
        ascending += [True]
    joined = joined.sort_values(sort_cols, ascending=ascending, kind="mergesort")
    joined = joined.drop_duplicates(subset="element_tag")
    merge_cols = ["element_tag", "zone_id"]
    if "z_order" in joined.columns:
        merge_cols.append("z_order")
    return grid.merge(joined[merge_cols], on="element_tag", how="left")


# gmsh_map key for each fragment-input kind recorded in _GeometryInventory.
_MAP_KEY_BY_KIND = {
    'point': 'points',
    'straddle_point': 'straddle_points',
    'line': 'lines',
    'surface': 'surfaces',
    'structured_buffer_surf': 'structured_buffer_surfs',
}


@dataclasses.dataclass
class _GeometryInventory:
    """Per-call bookkeeping of the OCC entities created by ``_add_geometry``."""

    # Embedded entities take part in fragmentation; input_tag_info maps each
    # pre-fragment (dim, tag) to {'type', 'id'} so the fragment map can be
    # traced back to features.
    input_tag_info: dict = dataclasses.field(default_factory=dict)
    embedded_point_tags: list = dataclasses.field(default_factory=list)
    embedded_line_tags: list = dataclasses.field(default_factory=list)
    embedded_surface_tags: list = dataclasses.field(default_factory=list)
    # Non-embedded (field-only) entities per feature id; they do not fragment
    # but still receive size fields. Straddle pairs are keyed by their *line*
    # id, apart from point features (whose ids share the same 0..n range).
    # Field-only polygons also keep their boundary curves (poly_curves).
    nonembedded_point_tags: dict = dataclasses.field(default_factory=dict)
    nonembedded_straddle_tags: dict = dataclasses.field(default_factory=dict)
    nonembedded_line_tags: dict = dataclasses.field(default_factory=dict)
    nonembedded_surface_tags: dict = dataclasses.field(default_factory=dict)
    nonembedded_poly_curve_tags: dict = dataclasses.field(default_factory=dict)
    # (feature id, polygon) created only after fragmentation.
    pending_nonembedded_polys: list = dataclasses.field(default_factory=list)
    # Per quad-buffered feature: lc, thickness, strip corners/side lines.
    structured_buffer_specs: dict = dataclasses.field(default_factory=dict)

    def record_embedded(self, key, kind, feature_id):
        """Register an entity that takes part in fragmentation."""
        self.input_tag_info[key] = {'type': kind, 'id': feature_id}
        by_dim = (self.embedded_point_tags, self.embedded_line_tags, self.embedded_surface_tags)
        by_dim[key[0]].append(key)

    def object_tags(self):
        """Fragment inputs: surfaces, then lines, then points, each in creation order."""
        return self.embedded_surface_tags + self.embedded_line_tags + self.embedded_point_tags

    def feature_map(self):
        """A gmsh_map seeded with the non-embedded tags (shallow copies)."""
        return {
            'points': dict(self.nonembedded_point_tags),
            'straddle_points': dict(self.nonembedded_straddle_tags),
            'lines': dict(self.nonembedded_line_tags),
            'surfaces': dict(self.nonembedded_surface_tags),
            'structured_buffer_surfs': {},
            'poly_curves': dict(self.nonembedded_poly_curve_tags),
        }


class MeshGenerator:
    def __init__(self, background_lc=None, verbosity=None, mesh_algorithm=6,
                 smoothing_steps=10, optimization_cycles=2,
                 tolerance_initial_delaunay=1e-8,
                 heal_shapes=False, heal_tolerance=1e-8,
                 heal_fix_degenerated=True, heal_fix_small_edges=True,
                 heal_fix_small_faces=True, diagnose=False):
        """
        Initializes the Gmsh-based mesh generator.

        This class is responsible for taking clean geometric inputs and using Gmsh
        to produce a high-quality triangular mesh.

        Args:
            background_lc (float, optional): The default target mesh size for areas
                not controlled by a specific refinement field.
            verbosity (int, optional): Output level while ``generate()`` runs
                (0=warnings only, 1=progress, 2=debug diagnostics). It applies to
                both vorflow's logger and Gmsh, and only for the duration of
                ``generate()``. None (default) follows the package-wide level set
                with ``vorflow.set_verbosity()``.
            mesh_algorithm (int): The 2D mesh algorithm to use. Common choices are
                5 (Delaunay) for speed or 6 (Frontal-Delaunay) for quality.
            smoothing_steps (int): Number of internal Lloyd smoothing iterations
                performed by Gmsh during mesh generation.
            optimization_cycles (int): Number of explicit optimization passes
                (e.g., Relocate2D, Laplace2D) to run after the initial mesh is generated.
            tolerance_initial_delaunay (float): Tolerance for the initial Delaunay
                point insertion. Increase this (e.g. 1e-4, 1e-2) to handle
                "Could not insert point" errors caused by near-degenerate geometry
                after fragmentation. This is a meshing-phase tolerance — it does NOT
                alter the CAD topology, so no surfaces or lines are lost.
                Default is 1e-8 (Gmsh default).
            heal_shapes (bool): If True, run OCC topology healing after
                fragmentation. This can fix degenerate geometry that causes
                meshing failures, but may also merge or delete small entities.
                Use with caution on complex models — keep heal_tolerance small.
                Default is False.
            heal_tolerance (float): Size threshold for healShapes. Entities
                smaller than this may be removed or merged. Default 1e-8. only works if heal_shapes=True.
            heal_fix_degenerated (bool): Fix degenerated edges/faces. Default True. Only works if heal_shapes=True.
            heal_fix_small_edges (bool): Remove edges smaller than tolerance. Default True. Only works if heal_shapes=True.
            heal_fix_small_faces (bool): Remove faces smaller than tolerance. Default True. Only works if heal_shapes=True.
            diagnose (bool): If True, retain structured diagnostic details from
                geometry transfer, embedding, and meshing steps.
        """
        self.background_lc = background_lc
        self.verbosity = verbosity
        # Resolved level used by internal gates; refreshed at each generate().
        self._verbosity = self._resolve_verbosity()
        self.mesh_algorithm = mesh_algorithm
        self.smoothing_steps = smoothing_steps
        self.optimization_cycles = optimization_cycles
        self.tolerance_initial_delaunay = tolerance_initial_delaunay
        self.heal_shapes = heal_shapes
        self.heal_tolerance = heal_tolerance
        self.heal_fix_degenerated = heal_fix_degenerated
        self.heal_fix_small_edges = heal_fix_small_edges
        self.heal_fix_small_faces = heal_fix_small_faces
        self.diagnose = bool(diagnose)

        self.initialized = False
        self.nodes = None
        self.node_tags = None
        self.zones_gdf = None
        self.triangular_quality = None
        self.element_grid = None
        self._element_data = None
        self.diagnostics = {}

    def _validate_background_lc(self):
        """Fail before any Gmsh work if background_lc is missing or not positive."""
        if self.background_lc is None:
            raise ValueError(
                "MeshGenerator.background_lc must be provided. "
                "If you don't want to constrain the mesh, pass a very large value."
            )
        value = float(self.background_lc)
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(
                "MeshGenerator.background_lc must be a positive finite number. "
                f"Got {self.background_lc!r}."
            )

    def _resolve_verbosity(self) -> int:
        """Verbosity in effect: the explicit setting, else the package level."""
        if self.verbosity is None:
            return current_verbosity()
        return int(self.verbosity)

    def _force_close_polygon(self, poly):
        """Ensure a polygon's exterior and interior rings are closed."""
        if not isinstance(poly, Polygon):
            return poly

        # Close exterior ring
        if poly.exterior.coords[0] != poly.exterior.coords[-1]:
            exterior_coords = list(poly.exterior.coords)
            exterior_coords.append(exterior_coords[0])
            poly = Polygon(exterior_coords, [list(i.coords) for i in poly.interiors])

        # Close interior rings
        new_interiors = []
        for interior in poly.interiors:
            if interior.coords[0] != interior.coords[-1]:
                interior_coords = list(interior.coords)
                interior_coords.append(interior_coords[0])
                new_interiors.append(interior_coords)
            else:
                new_interiors.append(list(interior.coords))
        
        return Polygon(poly.exterior, new_interiors)

    def _initialize_gmsh(self):
        # If Gmsh is already initialized (e.g. leftover from a previous failed
        # run in the same Jupyter kernel), tear it down first so we start clean.
        if gmsh.is_initialized():
            gmsh.finalize()
        gmsh.initialize()
        gmsh.option.setNumber("General.Verbosity", self._verbosity)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-6)
        gmsh.option.setNumber("Geometry.OCCBooleanPreserveNumbering", 1)
        gmsh.model.add("mesh_model")
        self.initialized = True

    def _finalize_gmsh(self):
        if gmsh.is_initialized():
            gmsh.finalize()
            self.initialized = False

    @staticmethod
    def _meshed_surface_tags(gmsh_map, clean_polys):
        """Surface tags composing the meshed domain.

        Embedded polygon surfaces plus structured-buffer strips.
        Field-only (embed=False) surfaces are excluded: gmsh meshes them as
        standalone entities, but they are not part of the deliverable mesh and
        must not pollute element/quality/node collection.
        """
        def is_embedded_row(row):
            val = row.get('embed', True)
            return True if pd.isna(val) else bool(val)

        if clean_polys is not None and not clean_polys.empty:
            if 'embed' in clean_polys.columns:
                poly_ids = [int(i) for i, r in clean_polys.iterrows() if is_embedded_row(r)]
            else:
                poly_ids = [int(i) for i in clean_polys.index]
        else:
            poly_ids = []

        tags, seen = [], set()

        def add_dimtags(dimtags):
            for dimtag in dimtags:
                if isinstance(dimtag, (tuple, list)) and len(dimtag) >= 2 and int(dimtag[0]) == 2:
                    tag = int(dimtag[1])
                    if tag not in seen:
                        seen.add(tag)
                        tags.append(tag)

        for fid in poly_ids:
            add_dimtags(gmsh_map.get('surfaces', {}).get(fid, []))
        for dimtags in gmsh_map.get('structured_buffer_surfs', {}).values():
            add_dimtags(dimtags)
        return tags

    @staticmethod
    def _get_2d_elements(surface_tags=None):
        """getElements(dim=2), optionally restricted to specific surfaces."""
        if not surface_tags:
            return gmsh.model.mesh.getElements(dim=2)
        by_type = {}
        for tag in surface_tags:
            try:
                element_types, element_tags, element_nodes = gmsh.model.mesh.getElements(2, int(tag))
            except Exception:
                continue
            for etype, etags, enodes in zip(element_types, element_tags, element_nodes):
                bucket = by_type.setdefault(int(etype), ([], []))
                bucket[0].append(np.asarray(etags, dtype=np.int64))
                bucket[1].append(np.asarray(enodes, dtype=np.int64))
        types = list(by_type.keys())
        tags = [np.concatenate(by_type[t][0]) for t in types]
        nodes = [np.concatenate(by_type[t][1]) for t in types]
        return types, tags, nodes

    def _collect_triangular_quality(self, surface_tags=None):
        """Collect gmsh 2D element quality metrics while the model is live."""
        quality_columns = [
            "minSICN",
            "minDetJac",
            "maxDetJac",
            "minSJ",
            "minSIGE",
            "gamma",
            "innerRadius",
            "outerRadius",
            "minIsotropy",
            "angleShape",
            "minEdge",
            "maxEdge",
        ]
        metadata_columns = ["element_tag", "element_type", "element_name", "is_triangle"]
        element_types, element_tags, _ = self._get_2d_elements(surface_tags)
        if len(element_tags) == 0:
            return pd.DataFrame(columns=metadata_columns + quality_columns)

        frames = []
        unavailable_quality_measures = {}
        for element_type, tags_for_type in zip(element_types, element_tags):
            tags = np.asarray(tags_for_type, dtype=np.int64)
            if len(tags) == 0:
                continue

            element_name, _, _, _, _, _ = gmsh.model.mesh.getElementProperties(int(element_type))
            qualities = {
                "element_tag": tags,
                "element_type": int(element_type),
                "element_name": element_name,
                "is_triangle": "triangle" in element_name.lower(),
            }
            for measure in quality_columns:
                if measure in unavailable_quality_measures:
                    qualities[measure] = np.full(len(tags), np.nan)
                    continue
                try:
                    qualities[measure] = gmsh.model.mesh.getElementQualities(tags, measure)
                except Exception as exc:
                    if "Unknown quality name" not in str(exc):
                        raise
                    unavailable_quality_measures[measure] = str(exc)
                    qualities[measure] = np.full(len(tags), np.nan)

            frames.append(pd.DataFrame(qualities))

        if not frames:
            return pd.DataFrame(columns=metadata_columns + quality_columns)

        if unavailable_quality_measures:
            logger.warning(
                "Gmsh %s does not provide quality measures %s; "
                "their report columns contain NaN.",
                getattr(gmsh, "__version__", "unknown"),
                ", ".join(sorted(unavailable_quality_measures)),
            )

        return pd.concat(frames, ignore_index=True)[metadata_columns + quality_columns]

    def get_triangular_quality(self):
        """
        Return cached gmsh 2D element quality metrics for the generated mesh.

        The metrics are collected during ``generate()`` before gmsh is finalized,
        so this method can be called after the normal mesh-generation lifecycle.
        The report includes all 2D element types and marks triangle elements in
        ``is_triangle`` so mixed tri/quad meshes are explicit.
        """
        if self.triangular_quality is None:
            raise RuntimeError(
                "Triangular quality is not available. Call MeshGenerator.generate() first."
            )
        return self.triangular_quality.copy()

    def _empty_element_grid(self, crs=None):
        return gpd.GeoDataFrame(
            columns=[
                "element_tag",
                "element_type",
                "element_name",
                "is_triangle",
                "is_quad",
                "node_tags",
                "centroid_x",
                "centroid_y",
                "zone_id",
                "z_order",
                "geometry",
            ],
            geometry="geometry",
            crs=crs,
        )

    def _capture_element_data(self, surface_tags=None):
        """Copy raw 2D element connectivity and node coordinates while gmsh is live."""
        element_types, element_tags, element_node_tags = self._get_2d_elements(surface_tags)
        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        blocks = []
        for element_type, tags_for_type, nodes_for_type in zip(
            element_types, element_tags, element_node_tags
        ):
            element_name, _, _, num_nodes, _, num_primary_nodes = gmsh.model.mesh.getElementProperties(
                int(element_type)
            )
            num_nodes = int(num_nodes)
            num_primary_nodes = int(num_primary_nodes) if int(num_primary_nodes) > 0 else num_nodes
            tags = np.asarray(tags_for_type, dtype=np.int64)
            flat_nodes = np.asarray(nodes_for_type, dtype=np.int64)
            if num_nodes <= 0 or num_primary_nodes < 3 or len(tags) == 0 or len(flat_nodes) == 0:
                continue
            connectivity = flat_nodes.reshape((len(tags), num_nodes))[:, :num_primary_nodes]
            blocks.append({
                "element_type": int(element_type),
                "element_name": element_name,
                "tags": tags,
                "connectivity": connectivity.copy(),
            })
        return {
            "blocks": blocks,
            "node_tags": np.asarray(node_tags, dtype=np.int64),
            "node_xy": np.asarray(node_coords, dtype=float).reshape(-1, 3)[:, :2].copy(),
        }

    @staticmethod
    def _element_block_frame(block, node_index, node_xy, crs):
        """Build the element polygons of one gmsh element type."""
        connectivity = block["connectivity"]
        known = np.isin(connectivity, node_index.index.to_numpy()).all(axis=1)
        tags = block["tags"][known]
        connectivity = connectivity[known]
        if len(tags) == 0:
            return None
        rows = node_index.loc[connectivity.ravel()].to_numpy().reshape(connectivity.shape)
        polygons = shapely.polygons(node_xy[rows])
        invalid = ~shapely.is_valid(polygons)
        if invalid.any():
            polygons[invalid] = shapely.make_valid(polygons[invalid])
        usable = (
            (shapely.get_type_id(polygons) == shapely.GeometryType.POLYGON)
            & ~shapely.is_empty(polygons)
            & (shapely.area(polygons) > 0)
        )
        polygons, tags, connectivity = polygons[usable], tags[usable], connectivity[usable]
        if len(tags) == 0:
            return None
        name = block["element_name"]
        name_lower = name.lower()
        centroids = shapely.centroid(polygons)
        return gpd.GeoDataFrame(
            {
                "element_tag": tags,
                "element_type": block["element_type"],
                "element_name": name,
                "is_triangle": "triangle" in name_lower,
                "is_quad": "quadrangle" in name_lower or "quadrilateral" in name_lower,
                "node_tags": [tuple(int(t) for t in row) for row in connectivity],
                "centroid_x": shapely.get_x(centroids),
                "centroid_y": shapely.get_y(centroids),
            },
            geometry=polygons,
            crs=crs,
        )

    def _build_element_grid(self, element_data, zones_gdf=None):
        """Turn captured element data into a zoned element-polygon GeoDataFrame."""
        crs = getattr(zones_gdf, "crs", None)
        if not element_data["blocks"]:
            warnings.warn("gmsh returned no 2D elements; element grid is empty.")
            return self._empty_element_grid(crs)

        node_index = pd.Series(
            np.arange(len(element_data["node_tags"])), index=element_data["node_tags"]
        )
        node_index = node_index[~node_index.index.duplicated()]
        frames = [
            self._element_block_frame(block, node_index, element_data["node_xy"], crs)
            for block in element_data["blocks"]
        ]
        frames = [frame for frame in frames if frame is not None]
        if not frames:
            warnings.warn("gmsh returned no usable 2D elements; element grid is empty.")
            return self._empty_element_grid(crs)

        grid = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), geometry="geometry", crs=crs)
        grid = grid.sort_values("element_tag").reset_index(drop=True)
        return _assign_zones_to_elements(grid, _embedded_zones(zones_gdf))

    def get_element_grid(self, element_filter="all"):
        """
        Return gmsh 2D element polygons for the generated mesh.

        The raw element data is captured during ``generate()``; the polygons
        are built on the first call and cached, so users who only need the
        Voronoi grid do not pay for them.

        ``element_filter`` may be ``"all"``, ``"triangles"``, or ``"quads"``.
        The exporter is independent of the Voronoi tessellator and can represent
        mixed tri/quad meshes produced by future structured-buffer workflows.

        Each element is assigned the zone whose polygon intersects the element
        centroid; field-only (``embed=False``) polygons never assign zones. Ties (overlapping zones or centroids on shared borders) are
        broken deterministically: highest ``z_order`` wins, then the zone that
        appears earliest in the conceptual-mesh polygon table.
        """
        if self.element_grid is None and self._element_data is None:
            raise RuntimeError(
                "Element grid is not available. Call MeshGenerator.generate() first."
            )
        if element_filter not in {"all", "triangles", "quads"}:
            raise ValueError("element_filter must be one of 'all', 'triangles', or 'quads'.")
        if self.element_grid is None:
            self.element_grid = self._build_element_grid(self._element_data, self.zones_gdf)
            self._element_data = None

        grid = self.element_grid
        if element_filter == "triangles":
            grid = grid[grid["is_triangle"]]
        elif element_filter == "quads":
            grid = grid[grid["is_quad"]]
        return grid.copy()

    def _dedup_and_remap_fragment_map(self, out_map, object_tags, input_tag_info):
        """Run removeAllDuplicates and remap the point tags it kills, in place.

        Unlike healShapes this does not delete or merge entities by a size
        tolerance, so it cannot destroy surfaces or turn interior lines into
        boundaries. It can merge coincident entities (changing tags) without
        returning a mapping, so point coordinates are snapshotted beforehand
        and out_map tags that disappear are remapped to the surviving point at
        the same location.
        """
        pre_dedup_coords = self._snapshot_point_coords(out_map)
        gmsh.model.occ.removeAllDuplicates()
        if len(out_map) == 0:
            return

        occ_alive = set()
        alive_pts_by_coord = {}  # rounded (x, y, z) -> surviving point tag
        for dim in range(3):
            for dt in gmsh.model.occ.getEntities(dim):
                d, t = int(dt[0]), int(dt[1])
                occ_alive.add((d, t))
                if d == 0:
                    try:
                        bb = gmsh.model.occ.getBoundingBox(0, t)
                        alive_pts_by_coord[_rounded(bb[:3], _DEDUP_COORD_DECIMALS)] = t
                    except Exception:
                        # gmsh raises plain Exception for an entity it cannot
                        # measure; that point simply cannot be a remap target.
                        logger.debug("Post-dedup survey: no bounding box "
                                     "for surviving point %d.", t)

        remapped = 0
        pruned = 0
        for i in range(len(out_map)):
            new_entries = []
            for dt in out_map[i]:
                d, t = int(dt[0]), int(dt[1])
                if (d, t) in occ_alive:
                    new_entries.append(dt)
                elif d == 0 and (d, t) in pre_dedup_coords:
                    # Tag was killed by dedup: find the surviving point.
                    coord_key = _rounded(pre_dedup_coords[(d, t)], _DEDUP_COORD_DECIMALS)
                    new_tag = alive_pts_by_coord.get(coord_key)
                    if new_tag is not None:
                        new_entries.append((0, new_tag))
                        remapped += 1
                    else:
                        pruned += 1
                else:
                    pruned += 1
            out_map[i] = new_entries
        if pruned > 0 or remapped > 0:
            logger.info(f"removeAllDuplicates: remapped {remapped}, pruned {pruned} tag(s) from fragment map.")

        self._log_point_survival_after_dedup(object_tags, out_map, input_tag_info, occ_alive)

    @staticmethod
    def _snapshot_point_coords(out_map):
        """(x, y, z) of every dim-0 entity in out_map, keyed by (0, tag)."""
        coords = {}
        for entries in out_map:
            for dt in entries:
                d, t = int(dt[0]), int(dt[1])
                if d == 0 and (d, t) not in coords:
                    try:
                        bb = gmsh.model.occ.getBoundingBox(0, t)
                        coords[(d, t)] = (bb[0], bb[1], bb[2])
                    except Exception:
                        # gmsh raises plain Exception for an entity it cannot
                        # measure; without coordinates it cannot be remapped.
                        logger.debug("Pre-dedup snapshot: no bounding box for "
                                     "point %d; it cannot be remapped if "
                                     "removeAllDuplicates renumbers it.", t)
        return coords

    def _heal_and_remap_fragment_map(self, out_map, object_tags, input_tag_info):
        """Optionally heal OCC shapes, synchronize, and remap out_map by coordinates, in place.

        healShapes rebuilds OCC topology - renumbering entities and even
        reusing a tag number for a DIFFERENT entity - so remapping is done
        purely by coordinate matching, never by tag identity. Always
        synchronizes the OCC model, even when heal_shapes is off.
        """
        if not self.heal_shapes:
            gmsh.model.occ.synchronize()
            return

        pre_heal_coords = self._snapshot_entity_bboxes(out_map)
        pre_heal = set()
        for dim in range(3):
            for dt in gmsh.model.occ.getEntities(dim):
                pre_heal.add((int(dt[0]), int(dt[1])))

        self._heal_occ_shapes()
        gmsh.model.occ.synchronize()
        if len(out_map) == 0:
            return

        surviving, remapped, pruned = self._remap_after_heal(out_map, pre_heal_coords)
        if remapped > 0 or pruned > 0:
            logger.info(f"Heal post-processing: remapped {remapped}, pruned {pruned} tag(s) from fragment map.")

        self._log_point_survival_after_heal(
            object_tags, out_map, input_tag_info, surviving, remapped, pruned
        )
        self._log_heal_dim0_changes(pre_heal, surviving)

    @staticmethod
    def _snapshot_entity_bboxes(out_map):
        """Point coordinates (dim 0) or bounding boxes (dims 1-2) of out_map entities, keyed by (dim, tag)."""
        coords = {}
        for entries in out_map:
            for dt in entries:
                d, t = int(dt[0]), int(dt[1])
                if (d, t) not in coords and d in (0, 1, 2):
                    try:
                        bb = gmsh.model.occ.getBoundingBox(d, t)
                        coords[(d, t)] = tuple(bb[:3]) if d == 0 else tuple(bb[:6])
                    except Exception:
                        # gmsh raises plain Exception for an entity it cannot
                        # measure; without coordinates it cannot be remapped.
                        logger.debug("Pre-heal snapshot: no bounding box "
                                     "for entity (dim %d, tag %d); it "
                                     "cannot be remapped if healShapes "
                                     "renumbers it.", d, t)
        return coords

    def _heal_occ_shapes(self):
        """Run OCC healShapes with the configured tolerance and fix flags."""
        if self.heal_tolerance > 1e-2:
            logger.info(f"WARNING: heal_tolerance={self.heal_tolerance} is large. "
                        f"This may destroy fragment boundaries and lose surfaces/lines. "
                        f"Consider values <= 1e-3.")
        logger.info(f"Healing OCC shapes (tolerance={self.heal_tolerance}, "
                    f"degenerated={self.heal_fix_degenerated}, "
                    f"small_edges={self.heal_fix_small_edges}, "
                    f"small_faces={self.heal_fix_small_faces})...")
        gmsh.model.occ.healShapes(
            [], tolerance=self.heal_tolerance,
            fixDegenerated=self.heal_fix_degenerated,
            fixSmallEdges=self.heal_fix_small_edges,
            fixSmallFaces=self.heal_fix_small_faces,
            sewFaces=False,
            makeSolids=False,
        )

    @staticmethod
    def _remap_after_heal(out_map, pre_heal_coords):
        """Remap out_map entries to the healed entity at the same coordinates; returns (surviving, remapped, pruned).

        healShapes introduces ~1e-6 coordinate drift, so coordinates are
        rounded to _HEAL_COORD_DECIMALS -- enough to distinguish any two
        intentionally distinct points while absorbing the drift.
        """
        surviving = set()
        alive_by_dim = {0: {}, 1: {}, 2: {}}  # dim -> rounded coords -> tag
        for dim in range(3):
            for dt in gmsh.model.getEntities(dim):
                d, t = int(dt[0]), int(dt[1])
                surviving.add((d, t))
                try:
                    bb = gmsh.model.getBoundingBox(d, t)
                    n_coords = 3 if d == 0 else 6
                    alive_by_dim[d][_rounded(bb[:n_coords], _HEAL_COORD_DECIMALS)] = t
                except Exception:
                    # gmsh raises plain Exception for an entity it cannot
                    # measure; it simply cannot be a remap target.
                    logger.debug("Post-heal survey: no bounding box for "
                                 "surviving entity (dim %d, tag %d).", d, t)

        remapped = 0
        pruned = 0
        for i in range(len(out_map)):
            new_entries = []
            for dt in out_map[i]:
                d, t = int(dt[0]), int(dt[1])
                if (d, t) not in pre_heal_coords:
                    # Entity wasn't snapshotted (shouldn't happen); keep if alive.
                    if (d, t) in surviving:
                        new_entries.append(dt)
                    else:
                        pruned += 1
                    continue
                coord_key = _rounded(pre_heal_coords[(d, t)], _HEAL_COORD_DECIMALS)
                new_tag = alive_by_dim.get(d, {}).get(coord_key)
                if new_tag is None:
                    pruned += 1
                elif new_tag == t:
                    new_entries.append(dt)
                else:
                    new_entries.append((d, new_tag))
                    remapped += 1
            out_map[i] = new_entries
        return surviving, remapped, pruned

    @staticmethod
    def _point_feature_survival(object_tags, out_map, input_tag_info, alive):
        """(feature id, n dim-0 map entries, n alive) for each point feature in the fragment map."""
        status = []
        for i, input_dimtag in enumerate(object_tags):
            info = input_tag_info.get(_to_key(input_dimtag[0], input_dimtag[1]), {})
            if info.get('type') != 'point':
                continue
            dim0 = [dt for dt in (out_map[i] if i < len(out_map) else [])
                    if int(dt[0]) == 0]
            n_alive = len([dt for dt in dim0 if (int(dt[0]), int(dt[1])) in alive])
            status.append((info['id'], len(dim0), n_alive))
        return status

    def _log_point_survival_after_dedup(self, object_tags, out_map, input_tag_info, occ_alive):
        """[DIAG] Point features left with no alive tag after removeAllDuplicates."""
        if self._verbosity < 2:
            return
        status = self._point_feature_survival(object_tags, out_map, input_tag_info, occ_alive)
        n_empty = sum(1 for _, _, n_alive in status if n_alive == 0)
        logger.debug(f"[DIAG] Post-dedup point features: {len(status)} total, "
                     f"{n_empty} with 0 alive tags")
        if n_empty > 0:
            for fid, n_entries, n_alive in status:
                if n_alive == 0:
                    logger.debug(f"  [DIAG] Point feat_id={fid}: {n_entries} map entries, 0 alive")

    def _log_point_survival_after_heal(self, object_tags, out_map, input_tag_info,
                                       surviving, remapped, pruned):
        """[DIAG] Point features left with no alive tag after healShapes."""
        if self._verbosity < 2:
            return
        status = self._point_feature_survival(object_tags, out_map, input_tag_info, surviving)
        n_empty = sum(1 for _, _, n_alive in status if n_alive == 0)
        logger.debug(f"[DIAG] Post-heal point features: {len(status)} total, "
                     f"{n_empty} with 0 alive tags (remapped {remapped}, pruned {pruned})")
        if n_empty > 0:
            for fid, n_entries, n_alive in status:
                if n_alive == 0:
                    logger.debug(f"  [DIAG] Point feat_id={fid}: {n_entries} map entries, 0 alive after heal")

    def _log_heal_dim0_changes(self, pre_heal, surviving):
        """[DIAG] Point tags healShapes removed or added."""
        if self._verbosity < 2:
            return
        dim0_removed = [(d, t) for d, t in pre_heal - surviving if d == 0]
        dim0_added = [(d, t) for d, t in surviving - pre_heal if d == 0]
        if dim0_removed or dim0_added:
            logger.debug(f"[DIAG] Heal dim-0 changes: removed {len(dim0_removed)}, added {len(dim0_added)}")
            if dim0_removed:
                logger.debug(f"  [DIAG] Removed point tags: {sorted(t for _, t in dim0_removed)}")
            if dim0_added:
                logger.debug(f"  [DIAG] Added point tags: {sorted(t for _, t in dim0_added)}")

    def _add_geometry(self, polygons_gdf, lines_gdf, points_gdf, launch_gmsh_gui=False):
        """Transfer the clean features into the OCC model and fragment them; returns gmsh_map."""
        inventory = _GeometryInventory()
        domain = buffer.domain_union_geometry(polygons_gdf)

        self._add_point_features(points_gdf, inventory)

        corridors = buffer.protected_corridors(polygons_gdf, lines_gdf, self.background_lc)
        barrier_zone = self._build_barrier_zone(corridors)
        plans = buffer.plan_quad_buffers(polygons_gdf, lines_gdf, self.background_lc, domain)
        self._quad_buffer_crossings = buffer.find_all_crossings(plans)

        line_strips = self._add_line_features(
            lines_gdf, inventory, plans, corridors, barrier_zone, domain
        )
        self._add_polygon_features(
            polygons_gdf, inventory, plans, corridors, domain, line_strips
        )

        # Call the GUI before fragmentation for debugging.
        if self._verbosity > 1 and launch_gmsh_gui:
            gmsh.model.occ.synchronize()
            gmsh.fltk.run()

        self._log_pre_fragment_diagnostics(inventory)

        object_tags = inventory.object_tags()
        if not object_tags:
            logger.warning("Warning: No geometry to mesh.")
            return inventory.feature_map()

        # "Fragment" combines all the individual geometries into a single,
        # topologically consistent model. Only embedded geometry takes part.
        logger.info(f"Fragmenting {len(object_tags)} objects...")
        out_dt, out_map = gmsh.model.occ.fragment(object_tags, [])
        self._dedup_and_remap_fragment_map(out_map, object_tags, inventory.input_tag_info)
        self._heal_and_remap_fragment_map(out_map, object_tags, inventory.input_tag_info)

        self._log_post_fragment_diagnostics(object_tags, out_map, inventory.input_tag_info)

        self._add_field_only_polygons(inventory)
        final_map = self._rebuild_feature_map(object_tags, out_map, inventory)

        self._log_final_map_diagnostics(final_map)
        self._recover_orphan_surfaces(final_map, polygons_gdf)
        self._apply_structured_buffer_meshing(final_map, inventory.structured_buffer_specs)
        return final_map

    def _add_point_features(self, points_gdf, inventory):
        """Add one OCC point per point feature."""
        for idx, row in points_gdf.iterrows():
            tag = gmsh.model.occ.addPoint(row.geometry.x, row.geometry.y, 0)
            key = _to_key(0, tag)
            if is_embedded(row):
                inventory.record_embedded(key, 'point', idx)
            else:
                inventory.nonembedded_point_tags.setdefault(int(idx), []).append(key)

    def _build_barrier_zone(self, corridors):
        """Union of the protection corridors (None if there are none), logged at verbosity 1."""
        zone = buffer.barrier_zone(corridors)
        if zone is not None and self._verbosity > 0:
            logger.info(f"Constructed Barrier Zone from {len(corridors)} protected features.")
        return zone

    def _add_line_features(self, lines_gdf, inventory, plans, corridors, barrier_zone, domain):
        """Add each line as a quad buffer, straddle point pairs or plain curves; returns the strip footprints."""
        line_strips = []
        for idx, row in lines_gdf.iterrows():
            is_barrier = row_bool(row, 'is_barrier', False)
            quad_buffer = row_bool(row, 'quad_buffer', False)
            straddle = positive_number(row.get('straddle_width'))
            lc = feature_lc(row, self.background_lc)
            embedded = is_embedded(row)
            if quad_buffer:
                line_strips.extend(self._add_line_quad_buffer(
                    idx, row, lc, inventory, plans, corridors, domain
                ))
            elif is_barrier or straddle is not None:
                self._add_straddle_points(idx, row.geometry, lc, straddle, embedded, inventory)
            else:
                self._add_standard_line(idx, row.geometry, embedded, barrier_zone, inventory)
        return line_strips

    def _add_line_quad_buffer(self, idx, row, lc, inventory, plans, corridors, domain):
        """Create a quad-buffered line's strip surfaces, trimmed by priority; returns the strip footprints."""
        key = ('line', int(idx))
        plan = plans.get(key)
        strips, created = [], []
        if plan is not None:
            obstacles = buffer.higher_priority_obstacles(key, plans, corridors)
            feature_label = f"line feature {row.name}"
            for part in plan.parts:
                strip, trimmed = buffer.trim_against_obstacles(
                    part.strip, obstacles, plan.lc, feature_label
                )
                if strip is None:
                    continue
                strips.append(strip)
                created.extend(self._add_buffer_surfaces(
                    strip, key, domain, inventory,
                    corners=None if trimmed else part.corners,
                    side_lines=part.side_lines,
                ))
        if created:
            inventory.structured_buffer_specs[key] = {
                'lc': lc,
                'thickness': buffer.quad_buffer_thickness(row),
                'kind': 'line',
                'strips': [info for _, info in created],
                'n_surfaces_created': len(created),
            }
        elif self._verbosity > 0:
            logger.warning(f"Warning: Structured buffer requested for line {idx}, but no buffer surface was created.")
        return strips

    def _add_straddle_points(self, idx, line, lc, straddle, embedded, inventory):
        """Place point pairs at +/-eps along a barrier/straddle line so Voronoi edges follow it.

        The line itself is not added; the pairs become mesh nodes whose
        Voronoi edges trace the original line.
        """
        length = line.length
        num_segments = int(max(1, np.ceil(length / lc)))
        distances = np.linspace(0, length, num_segments + 1)
        epsilon = straddle / 2.0 if straddle else lc * 0.20
        # Tangent probe proportional to line length so the offsets work for
        # any CRS units and for lines shorter than a fixed step.
        probe = max(length * 1e-4, 1e-12)
        for d in distances:
            p = line.interpolate(d)
            dx, dy = _unit_tangent(line, d, probe)
            nx, ny = -dy, dx
            for sign in (1.0, -1.0):
                tag = gmsh.model.occ.addPoint(p.x + sign * nx * epsilon, p.y + sign * ny * epsilon, 0)
                key = _to_key(0, tag)
                if embedded:
                    inventory.record_embedded(key, 'straddle_point', idx)
                else:
                    inventory.nonembedded_straddle_tags.setdefault(int(idx), []).append(key)

    def _add_standard_line(self, idx, geom, embedded, barrier_zone, inventory):
        """Add a plain constraint line, trimmed off the barrier zone, as OCC segments."""
        if barrier_zone and geom.intersects(barrier_zone):
            try:
                original_len = geom.length
                geom = geom.difference(barrier_zone)
                if self._verbosity > 1:
                    logger.info(f"  Line {idx} trimmed by barrier (Len: {original_len:.2f} -> {geom.length:.2f})")
            except Exception as e:
                # Broad on purpose: a failed trim keeps the untrimmed line
                # rather than aborting the whole mesh.
                logger.warning(f"Warning: Failed to trim line {idx}: {e}")
        if geom.is_empty:
            return
        # A line might be split into multiple parts after being trimmed.
        if geom.geom_type == 'LineString':
            parts = [geom]
        elif geom.geom_type == 'MultiLineString':
            parts = geom.geoms
        else:
            parts = []
        for part in parts:
            # Filter out tiny fragments that might remain after trimming.
            if part.length < 1e-6:
                continue
            self._add_polyline(idx, part, embedded, inventory)

    def _add_polyline(self, idx, part, embedded, inventory):
        """Add one LineString as a chain of OCC segments."""
        coords = sanitize_coords(list(part.coords), min_points=2)
        if len(coords) < 2:
            if self._verbosity > 0:
                logger.warning(f"Warning: Skipping degenerate line part for feature {idx} after coordinate cleanup.")
            return
        pt_tags = [gmsh.model.occ.addPoint(x, y, 0) for x, y in coords]
        created_segments = 0
        for i in range(len(pt_tags) - 1):
            try:
                line_tag = gmsh.model.occ.addLine(pt_tags[i], pt_tags[i+1])
            except Exception as e:
                # gmsh's Python API raises plain Exception on OCC errors.
                logger.warning(
                    f"Warning: Skipping invalid line segment {i} for feature {idx} "
                    f"between {coords[i]} and {coords[i+1]}: {e}"
                )
                continue
            key = _to_key(1, line_tag)
            created_segments += 1
            if embedded:
                inventory.record_embedded(key, 'line', idx)
            else:
                inventory.nonembedded_line_tags.setdefault(int(idx), []).append(key)
        if created_segments == 0 and self._verbosity > 0:
            logger.warning(f"Warning: No valid line segments were created for feature {idx}.")

    def _add_polygon_features(self, polygons_gdf, inventory, plans, corridors, domain, line_strips):
        """Add quad-buffer bands, then every polygon minus the buffer footprints."""
        if polygons_gdf.empty:
            return
        logger.info(f"Adding {len(polygons_gdf)} polygons to Gmsh...")
        band_geoms = self._add_polygon_quad_buffers(polygons_gdf, inventory, plans, corridors, domain)
        footprints = band_geoms + line_strips
        footprints_union = make_valid(unary_union(footprints)) if footprints else None
        for idx, row in polygons_gdf.iterrows():
            self._add_polygon_feature(idx, row, footprints_union, line_strips, inventory)

    def _add_polygon_quad_buffers(self, polygons_gdf, inventory, plans, corridors, domain):
        """Create the band surfaces of embedded quad-buffered polygons; returns the band footprints.

        A band hugs the full feature outline, so it is created once per
        feature rather than once per MultiPolygon part.
        """
        band_geoms = []
        for idx, row in polygons_gdf.iterrows():
            if not (is_embedded(row) and row_bool(row, 'quad_buffer', False)):
                continue
            key = ('poly', int(idx))
            created, band = self._add_polygon_band(key, inventory, plans, corridors, domain)
            if created:
                inventory.structured_buffer_specs[key] = {
                    'lc': feature_lc(row, self.background_lc),
                    'thickness': buffer.quad_buffer_thickness(row),
                    'kind': 'polygon',
                    'strips': [],
                    'n_surfaces_created': len(created),
                }
                band_geoms.append(band)
            elif self._verbosity > 0:
                logger.warning(f"Warning: Structured buffer requested for polygon {idx}, but no buffer surface was created.")
        return band_geoms

    def _add_polygon_band(self, key, inventory, plans, corridors, domain):
        """Create one polygon's band surfaces, trimmed by priority; returns (created, band) or ([], None)."""
        plan = plans.get(key)
        if plan is None:
            return [], None
        obstacles = buffer.higher_priority_obstacles(key, plans, corridors)
        band, _ = buffer.trim_against_obstacles(
            plan.band, obstacles, plan.lc, f"polygon feature {key[1]}"
        )
        if band is None:
            return [], None
        created = self._add_buffer_surfaces(band, key, domain, inventory)
        if not created:
            return [], None
        return created, band

    def _add_polygon_feature(self, idx, row, footprints_union, line_strips, inventory):
        """Add an embedded polygon (minus buffer footprints) as surfaces, or defer a field-only one."""
        embedded = is_embedded(row)
        geom = row['geometry']
        if geom.geom_type not in ('Polygon', 'MultiPolygon'):
            return
        # Mesh every embedded polygon minus the band/strip footprints, so the
        # buffer surfaces tile the plane with their neighbours exactly (shared
        # curves merged by removeAllDuplicates) instead of relying on OCC
        # fragment to cut overlapping faces -- which silently refuses in some
        # trimmed-crossing configurations and leaves double-meshed regions. It
        # also keeps a buffered zone's outline out of the mesh entirely:
        # overlap resolution makes neighbours share that outline, so
        # subtracting only from the buffered zone itself would still pin mesh
        # nodes onto it. Zone assignment uses the original polygons, so zone
        # extents are unchanged.
        if (
            embedded
            and footprints_union is not None
            and geom.intersects(footprints_union)
        ):
            geom = make_valid(geom.difference(footprints_union))
        for poly in polygon_parts(geom):
            if poly.is_empty:
                continue
            if not embedded:
                # Defer field-only polygon creation until after
                # fragmentation/dedup/healing: overlapping surfaces present
                # during global OCC cleanup can cut or renumber embedded
                # domain surfaces, which violates embed=False semantics.
                inventory.pending_nonembedded_polys.append((int(idx), poly))
                continue
            poly, moved = buffer.push_ring_vertices_off_strips(poly, line_strips)
            if moved and self._verbosity > 0:
                logger.info(f"Moved {moved} zone-ring vertex(es) off structured buffer strips.")
            s_tag, _ = self._create_polygon_surface(poly)
            if s_tag is None:
                logger.warning(f"Warning: Skipping degenerate polygon {idx}")
                continue
            inventory.record_embedded(_to_key(2, s_tag), 'surface', idx)

    def _add_buffer_surfaces(self, buffer_geom, feature_key, domain, inventory,
                             corners=None, side_lines=None):
        """Create OCC surfaces for a buffer footprint clipped to the domain; returns [(key, strip_info)]."""
        if domain is not None and not domain.is_empty:
            buffer_geom = buffer_geom.intersection(domain)
        buffer_geom = make_valid(buffer_geom)
        parts = [
            poly for poly in polygon_parts(buffer_geom)
            if not poly.is_empty and poly.area > 0
        ]
        if corners is not None and len(parts) != 1:
            # The recorded whole-strip corners no longer apply; pieces are
            # re-cornered individually from the side lines post-fragment.
            corners = None
        created = []
        for poly in parts:
            s_tag, _ = self._create_polygon_surface(poly)
            if s_tag is None:
                continue
            key = _to_key(2, s_tag)
            inventory.record_embedded(key, 'structured_buffer_surf', feature_key)
            created.append((key, {'corners': corners, 'side_lines': side_lines}))
        return created

    def _add_field_only_polygons(self, inventory):
        """Create the deferred field-only (embed=False) polygon surfaces after fragmentation."""
        pending = inventory.pending_nonembedded_polys
        if not pending:
            return
        if self._verbosity > 0:
            logger.info(f"Adding {len(pending)} field-only polygon surface(s)...")
        for idx, poly in pending:
            s_tag, boundary_curve_tags = self._create_polygon_surface(poly)
            if s_tag is None:
                if self._verbosity > 0:
                    logger.warning(f"Warning: Skipping degenerate field-only polygon {idx}")
                continue
            inventory.nonembedded_surface_tags.setdefault(int(idx), []).append(_to_key(2, s_tag))
            inventory.nonembedded_poly_curve_tags.setdefault(int(idx), []).extend(
                [(1, int(t)) for t in boundary_curve_tags]
            )
        gmsh.model.occ.synchronize()

    def _create_polygon_surface(self, poly):
        """Create an OCC plane surface; returns (surface tag, boundary curve tags) or (None, [])."""
        if poly.is_empty:
            return None, []
        poly = self._force_close_polygon(poly)
        exterior_loop_tag, exterior_lines = self._create_curve_loop(list(poly.exterior.coords))
        if exterior_loop_tag is None:
            return None, []
        loops = [exterior_loop_tag]
        boundary_curve_tags = list(exterior_lines)
        for interior in poly.interiors:
            interior_loop_tag, interior_lines = self._create_curve_loop(list(interior.coords))
            if interior_loop_tag is not None:
                loops.append(interior_loop_tag)
                boundary_curve_tags.extend(interior_lines)
        try:
            s_tag = gmsh.model.occ.addPlaneSurface(loops)
        except Exception as e:
            # gmsh's Python API raises plain Exception on OCC errors.
            logger.error(f"Error creating surface: {e}")
            return None, []
        return s_tag, boundary_curve_tags

    @staticmethod
    def _create_curve_loop(coords):
        """Create an OCC curve loop through a ring's coordinates; returns (loop tag, curve tags) or (None, [])."""
        clean_coords = sanitize_coords(coords, min_spacing=1e-5, require_closed=True, min_points=3)
        if len(clean_coords) < 3:
            return None, []
        p_tags = [gmsh.model.occ.addPoint(x, y, 0) for x, y in clean_coords]
        l_tags = []
        for i in range(len(p_tags)):
            p1 = p_tags[i]
            p2 = p_tags[(i + 1) % len(p_tags)]
            try:
                l_tags.append(gmsh.model.occ.addLine(p1, p2))
            except Exception as e:
                # gmsh's Python API raises plain Exception on OCC errors.
                logger.error(f"Error adding line {p1}-{p2}: {e}")
                return None, []
        try:
            return gmsh.model.occ.addCurveLoop(l_tags), l_tags
        except Exception as e:
            # gmsh's Python API raises plain Exception on OCC errors.
            logger.error(f"Error adding curve loop: {e}")
            return None, []

    def _rebuild_feature_map(self, object_tags, out_map, inventory):
        """Map each feature id to its post-fragment dimtags, starting from the non-embedded tags."""
        final_map = inventory.feature_map()
        logger.info(f"Reconstructing Map (Input Tags: {len(object_tags)}, Out Map Len: {len(out_map)})...")
        for i, input_dimtag in enumerate(object_tags):
            res_tags = out_map[i] if i < len(out_map) else [input_dimtag]
            info = inventory.input_tag_info.get(_to_key(input_dimtag[0], input_dimtag[1]))
            assert info is not None, f"fragment input {input_dimtag} was never recorded"
            # Structured-buffer ids are ('line'|'poly', idx) tuples so line
            # and polygon features with the same index cannot collide.
            feat_id = info['id'] if isinstance(info['id'], tuple) else int(info['id'])
            final_map[_MAP_KEY_BY_KIND[info['type']]].setdefault(feat_id, []).extend(res_tags)
        return final_map

    def _recover_orphan_surfaces(self, final_map, polygons_gdf):
        """Attach surfaces the fragment map dropped to the embedded polygon covering (or nearest) them.

        OCC's fragment map can omit pieces of an input surface (observed when
        a buffer strip with boundaries coincident to the densified domain edge
        splits the domain). An unclaimed surface would silently lose its mesh
        nodes and field sizing downstream.
        """
        claimed_surfaces = set()
        for map_key in ('surfaces', 'structured_buffer_surfs'):
            for dimtags in final_map.get(map_key, {}).values():
                for dt in dimtags:
                    if isinstance(dt, (tuple, list)) and len(dt) >= 2 and int(dt[0]) == 2:
                        claimed_surfaces.add(int(dt[1]))
        orphan_surfaces = [
            int(tag) for dim, tag in gmsh.model.getEntities(2)
            if int(tag) not in claimed_surfaces
        ]
        if not orphan_surfaces or polygons_gdf is None or polygons_gdf.empty:
            return
        embedded_polys = [
            (int(idx), row.geometry)
            for idx, row in polygons_gdf.iterrows()
            if is_embedded(row)
        ]
        recovered = 0
        for surf_tag in orphan_surfaces:
            try:
                cx, cy, _ = gmsh.model.occ.getCenterOfMass(2, surf_tag)
            except Exception:
                # gmsh raises plain Exception for entities without mass
                # properties; such a surface cannot be located, so skip it.
                continue
            center = Point(cx, cy)
            owner = None
            for fid, geom in embedded_polys:
                if geom.covers(center):
                    owner = fid
                    break
            if owner is None and embedded_polys:
                owner = min(embedded_polys, key=lambda item: item[1].distance(center))[0]
            if owner is not None:
                final_map['surfaces'].setdefault(owner, []).append((2, surf_tag))
                recovered += 1
        if recovered:
            logger.info(
                f"Recovered {recovered} orphan surface(s) the fragment map had "
                "dropped; re-attached to their containing polygon features."
            )

    def _log_pre_fragment_diagnostics(self, inventory):
        """[DIAG] Counts of the entities about to be fragmented."""
        if self._verbosity < 2:
            return
        line_feats = sorted(set(
            inventory.input_tag_info.get(_to_key(dt[0], dt[1]), {}).get('id', '?')
            for dt in inventory.embedded_line_tags
        )) if inventory.embedded_line_tags else []
        logger.debug(f"\n[DIAG] Pre-fragment: {len(inventory.embedded_surface_tags)} surfs, "
                     f"{len(inventory.embedded_line_tags)} lines, {len(inventory.embedded_point_tags)} pts "
                     f"| line features: {line_feats}")

    def _log_post_fragment_diagnostics(self, object_tags, out_map, input_tag_info):
        """[DIAG] Model size after fragmentation and how line fragments ended up."""
        if self._verbosity < 2:
            return
        all_surfs = gmsh.model.getEntities(2)
        all_lines = gmsh.model.getEntities(1)
        all_pts = gmsh.model.getEntities(0)

        # Classify line fragments: boundary vs interior vs orphan.
        n_boundary, n_interior, n_orphan, n_dim0 = 0, 0, 0, 0
        boundary_feats = set()  # feature ids whose lines became boundaries
        for i, input_dimtag in enumerate(object_tags):
            info = input_tag_info.get(_to_key(input_dimtag[0], input_dimtag[1]), {})
            if info.get('type') != 'line':
                continue
            res = out_map[i] if i < len(out_map) else [input_dimtag]
            for dt in res:
                dim_r, tag_r = int(dt[0]), int(dt[1])
                if dim_r == 0:
                    n_dim0 += 1
                    continue
                try:
                    gmsh.model.getBoundingBox(dim_r, tag_r)
                    up, _ = gmsh.model.getAdjacencies(1, tag_r)
                    if len(up) > 0:
                        n_boundary += 1
                        boundary_feats.add(info.get('id', '?'))
                    else:
                        n_interior += 1
                except Exception:
                    # gmsh raises plain Exception for an entity no longer in
                    # the model; count it as an orphan.
                    n_orphan += 1

        n_auto = 0
        for s in all_surfs:
            try:
                if gmsh.model.mesh.getEmbedded(2, s[1]):
                    n_auto += 1
            except Exception:
                # gmsh raises plain Exception; diagnostics only, so log and go on.
                logger.debug("getEmbedded failed for surface %d during "
                             "post-fragment diagnostics.", s[1])

        logger.debug(f"[DIAG] Post-fragment: {len(all_surfs)} surfs, "
                     f"{len(all_lines)} lines, {len(all_pts)} pts")
        logger.debug(f"[DIAG] Line fragments: {n_interior} interior, "
                     f"{n_boundary} BOUNDARY, {n_orphan} orphan, "
                     f"{n_dim0} became-points | auto-embed surfs: {n_auto}")
        if boundary_feats:
            logger.debug(f"[DIAG] *** Lines from these features became BOUNDARIES: "
                         f"{sorted(boundary_feats)} ***")

    def _log_final_map_diagnostics(self, final_map):
        """[DIAG] Point features with no, or stale, tags in the rebuilt map."""
        if self._verbosity < 2:
            return
        model_ents = set()
        for dim in range(3):
            for dt in gmsh.model.getEntities(dim):
                model_ents.add((int(dt[0]), int(dt[1])))
        empty_feats = []
        stale_feats = []
        for fid, dimtags in final_map.get('points', {}).items():
            dim0 = [dt for dt in dimtags if isinstance(dt, (tuple, list)) and int(dt[0]) == 0]
            if not dim0:
                empty_feats.append(fid)
            else:
                for dt in dim0:
                    if (int(dt[0]), int(dt[1])) not in model_ents:
                        stale_feats.append((fid, int(dt[1])))
        logger.debug(f"[DIAG] Final map: {len(final_map.get('points', {}))} point features, "
                     f"{len(empty_feats)} empty, {len(stale_feats)} with stale tags")
        if empty_feats:
            logger.debug(f"  [DIAG] Empty point feat_ids: {sorted(empty_feats)}")
        if stale_feats:
            logger.debug(f"  [DIAG] Stale point (feat_id, tag): {stale_feats}")

    def _log_post_embed_diagnostics(self, gmsh_map):
        """[DIAG] Surfaces carrying embedded entities and how mapped lines sit in the model."""
        if self._verbosity < 2:
            return
        all_surfs = gmsh.model.getEntities(2)
        with_emb, without_emb, total_emb = 0, 0, 0
        for surf_dt in all_surfs:
            try:
                emb = gmsh.model.mesh.getEmbedded(2, surf_dt[1])
                if emb:
                    with_emb += 1
                    total_emb += len(emb)
                else:
                    without_emb += 1
            except Exception:
                # gmsh raises plain Exception; count the surface as empty.
                without_emb += 1
        # Count line boundary vs interior in gmsh_map.
        map_bnd, map_int, map_miss = 0, 0, 0
        for dimtags in gmsh_map.get('lines', {}).values():
            for dt in dimtags:
                if not (isinstance(dt, (tuple, list)) and len(dt) >= 2):
                    continue
                if int(dt[0]) != 1:
                    continue
                try:
                    up, _ = gmsh.model.getAdjacencies(1, int(dt[1]))
                    if len(up) > 0:
                        map_bnd += 1
                    else:
                        map_int += 1
                except Exception:
                    # gmsh raises plain Exception for a curve not in the model.
                    map_miss += 1
        logger.debug(f"[DIAG] Post-embed: {with_emb}/{len(all_surfs)} surfaces have embeddings "
                     f"({total_emb} total entities) | "
                     f"{without_emb} surfaces empty")
        logger.debug(f"[DIAG] Line map: {map_int} interior, {map_bnd} boundary, {map_miss} missing")

    @staticmethod
    def _entity_length(dim, tag):
        try:
            return float(gmsh.model.occ.getMass(int(dim), int(tag)))
        except Exception:
            try:
                return float(gmsh.model.getMass(int(dim), int(tag)))
            except Exception:
                return None

    @staticmethod
    def _surface_boundary_point_coords(surf_tag):
        """Map of point tag -> (x, y) for a surface's boundary points."""
        try:
            boundary_points = gmsh.model.getBoundary(
                [(2, int(surf_tag))], oriented=False, recursive=True
            )
        except Exception:
            return {}
        candidates = {}
        for dim, tag in boundary_points:
            if int(dim) != 0:
                continue
            try:
                xyz = gmsh.model.getValue(0, int(tag), [])
            except Exception:
                continue
            candidates[int(tag)] = (float(xyz[0]), float(xyz[1]))
        return candidates

    def _derive_strip_corners_on_surface(self, surf_tag, side_lines, tol):
        """Derive the 4 corner point tags of a strip *piece* from its side lines.

        When fragmentation splits a strip (e.g. an embedded zone boundary
        crosses it), each piece is still a 4-sided strip whose corners are the
        extreme boundary points lying on the original positive/negative offset
        curves. Returns corner tags in "Left" order, or None.
        """
        if not side_lines:
            return None
        pos, neg = side_lines
        if pos is None or neg is None:
            return None
        candidates = self._surface_boundary_point_coords(surf_tag)
        if len(candidates) < 4:
            return None

        def extremes_on(line):
            hits = []
            for tag, (x, y) in candidates.items():
                point = Point(x, y)
                if line.distance(point) <= tol:
                    hits.append((float(line.project(point)), tag))
            if len(hits) < 2:
                return None
            hits.sort()
            return hits[0][1], hits[-1][1]

        neg_ends = extremes_on(neg)
        pos_ends = extremes_on(pos)
        if neg_ends is None or pos_ends is None:
            return None
        corner_tags = [neg_ends[0], neg_ends[1], pos_ends[1], pos_ends[0]]
        if len(set(corner_tags)) != 4:
            return None
        return corner_tags

    def _locate_corner_tags_on_surface(self, surf_tag, corner_coords, tol):
        """Match recorded strip corner coordinates to point tags on a surface.

        Only the surface's own boundary points are considered, so coordinate
        collisions with the rest of the model are impossible. Returns the four
        point tags in corner order, or None if any corner has no boundary
        point within ``tol``.
        """
        candidates = self._surface_boundary_point_coords(surf_tag)
        if len(candidates) < 4:
            return None

        corner_tags = []
        for cx, cy in corner_coords:
            best_tag, best_dist = None, None
            for tag, (px, py) in candidates.items():
                dist = math.hypot(px - cx, py - cy)
                if best_dist is None or dist < best_dist:
                    best_tag, best_dist = tag, dist
            if best_dist is None or best_dist > tol:
                return None
            corner_tags.append(best_tag)
        if len(set(corner_tags)) != 4:
            return None
        return corner_tags

    def _partition_boundary_chains(self, surf_tag, corner_tags):
        """Order a surface's boundary curves into 4 chains cut at the corners.

        Returns a list of (start_corner, end_corner, [curve_tags]) tuples, or
        None when the boundary is not a single closed loop through all four
        corner points (e.g. the strip was split by fragmentation).
        """
        try:
            boundary = gmsh.model.getBoundary(
                [(2, int(surf_tag))], oriented=False, recursive=False
            )
        except Exception:
            return None
        curve_tags = [int(tag) for dim, tag in boundary if int(dim) == 1]
        if len(curve_tags) < 4:
            return None

        endpoints = {}
        point_curves = {}
        for curve in curve_tags:
            try:
                pts = gmsh.model.getBoundary([(1, curve)], oriented=False, recursive=False)
            except Exception:
                return None
            point_pair = [int(tag) for dim, tag in pts if int(dim) == 0]
            if len(point_pair) != 2 or point_pair[0] == point_pair[1]:
                return None
            endpoints[curve] = point_pair
            for point in point_pair:
                point_curves.setdefault(point, []).append(curve)
        if any(len(curves) != 2 for curves in point_curves.values()):
            return None

        corner_set = {int(tag) for tag in corner_tags}
        if len(corner_set) != 4 or not corner_set.issubset(point_curves.keys()):
            return None

        start = int(corner_tags[0])
        point = start
        curve = point_curves[start][0]
        chains = []
        chain_start = start
        current = []
        visited = set()
        for _ in range(len(curve_tags)):
            if curve in visited:
                return None
            visited.add(curve)
            current.append(curve)
            a, b = endpoints[curve]
            point = b if point == a else a
            if point in corner_set:
                chains.append((chain_start, point, current))
                chain_start = point
                current = []
            next_curves = [c for c in point_curves[point] if c != curve]
            if len(next_curves) != 1:
                return None
            curve = next_curves[0]
        if current or len(chains) != 4 or chains[-1][1] != start:
            return None
        return chains

    @staticmethod
    def _distribute_chain_points(lengths, total_points):
        """Split a chain's transfinite point budget across its curves.

        Returns per-curve point counts whose segment total matches
        ``total_points - 1`` exactly, or None if the chain has more curves
        than segments.
        """
        total_segments = total_points - 1
        n = len(lengths)
        if total_segments < n:
            return None
        total_length = sum(lengths)
        segments = [
            max(1, int(round(total_segments * length / total_length)))
            for length in lengths
        ]
        drift = total_segments - sum(segments)
        order = sorted(range(n), key=lambda i: -lengths[i])
        attempts = 0
        while drift != 0 and attempts < 10 * n:
            i = order[attempts % n]
            step = 1 if drift > 0 else -1
            if segments[i] + step >= 1:
                segments[i] += step
                drift -= step
            attempts += 1
        if drift != 0:
            return None
        return [s + 1 for s in segments]

    def _apply_transfinite_strip(self, surf_tag, corner_tags, lc, thickness):
        """Apply a 4-corner transfinite structure to a relocated buffer strip.

        Opposite sides of a transfinite surface must carry equal point counts,
        so the along-feature target is computed once from the longer side and
        distributed across each side's curves. End caps get ``thickness + 1``
        points, matching gmshflow. Returns True on success.
        """
        chains = self._partition_boundary_chains(surf_tag, corner_tags)
        if chains is None:
            return False

        ct = [int(tag) for tag in corner_tags]
        roles = {
            frozenset((ct[0], ct[1])): 'side',
            frozenset((ct[2], ct[3])): 'side',
            frozenset((ct[1], ct[2])): 'cap',
            frozenset((ct[3], ct[0])): 'cap',
        }
        sides, caps = [], []
        for start_corner, end_corner, curves in chains:
            role = roles.get(frozenset((start_corner, end_corner)))
            if role == 'side':
                sides.append(curves)
            elif role == 'cap':
                caps.append(curves)
            else:
                return False
        if len(sides) != 2 or len(caps) != 2:
            return False

        def chain_lengths(chains_group):
            result = []
            for curves in chains_group:
                lengths = [self._entity_length(1, curve) for curve in curves]
                if any(v is None or not math.isfinite(v) or v <= 0 for v in lengths):
                    return None
                result.append(lengths)
            return result

        side_lengths = chain_lengths(sides)
        cap_lengths = chain_lengths(caps)
        if side_lengths is None or cap_lengths is None:
            return False

        total_points = max(
            2,
            int(round(max(sum(lengths) for lengths in side_lengths) / max(lc, 1e-12))) + 1,
        )
        # A cap subdivided into more curves than the strip has cell rows (e.g.
        # by a densified domain-boundary vertex) cannot carry thickness+1
        # points; forcing more would interpolate a node row onto the feature
        # line, so the caller falls back to recombine-only instead.
        cap_divisions = [
            self._distribute_chain_points(lengths, int(thickness) + 1)
            for lengths in cap_lengths
        ]
        if any(divisions is None for divisions in cap_divisions):
            return False

        try:
            for curves, lengths in zip(sides, side_lengths):
                divisions = self._distribute_chain_points(lengths, total_points)
                if divisions is None:
                    return False
                for curve, points in zip(curves, divisions):
                    gmsh.model.mesh.setTransfiniteCurve(int(curve), int(points))
            for curves, divisions in zip(caps, cap_divisions):
                for curve, points in zip(curves, divisions):
                    gmsh.model.mesh.setTransfiniteCurve(int(curve), int(points))
            gmsh.model.mesh.setTransfiniteSurface(int(surf_tag), "Left", ct)
        except Exception as e:
            warnings.warn(
                f"Could not apply transfinite structure to buffer surface {surf_tag}: {e}"
            )
            return False
        return True

    def _set_default_buffer_curve_divisions(self, surf_tag, lc):
        """Recombine-only fallback: seed each boundary curve at ~lc spacing."""
        try:
            boundary = gmsh.model.getBoundary(
                [(2, int(surf_tag))], oriented=False, recursive=False
            )
        except Exception:
            boundary = []
        for dim, tag in boundary:
            if int(dim) != 1:
                continue
            length = self._entity_length(1, tag)
            if length is None or not math.isfinite(length) or length <= 0:
                continue
            divisions = max(2, int(round(length / lc)) + 1)
            try:
                gmsh.model.mesh.setTransfiniteCurve(int(tag), divisions)
            except Exception as e:
                warnings.warn(f"Could not set transfinite divisions on curve {tag}: {e}")

    def _apply_structured_buffer_meshing(self, final_map, structured_buffer_specs):
        """Apply transfinite/recombine constraints to relocated buffer surfaces.

        Runs after fragmentation/dedup so OCC re-tagging cannot break the
        structured constraints. Line strips get a true 4-corner transfinite
        structure located by their recorded corner coordinates; polygon bands
        (annuli) and any strip that was trimmed or split are meshed
        recombine-only.
        """
        structured_surfaces = final_map.get('structured_buffer_surfs', {})
        if not structured_surfaces:
            return

        gmsh.option.setNumber("Mesh.RecombinationAlgorithm", 0)
        transfinite_count = 0
        recombine_only_count = 0
        for feat_id, dimtags in structured_surfaces.items():
            spec = structured_buffer_specs.get(feat_id, {})
            lc = max(float(spec.get('lc', self.background_lc or 1.0)), 0.001)
            thickness = int(spec.get('thickness', 1))
            strips = [info for info in spec.get('strips', []) if isinstance(info, dict)]
            corner_sets = [info['corners'] for info in strips if info.get('corners')]
            side_line_sets = [info['side_lines'] for info in strips if info.get('side_lines')]
            surf_tags = [
                int(dt[1])
                for dt in dimtags
                if isinstance(dt, (tuple, list)) and len(dt) >= 2 and int(dt[0]) == 2
            ]

            n_created = int(spec.get('n_surfaces_created', 0) or 0)
            if n_created and len(surf_tags) > n_created and self._verbosity > 0:
                logger.info(
                          f"Structured buffer for feature {feat_id} was split by fragmentation "
                          f"({n_created} surface(s) became {len(surf_tags)}); applying the "
                          "transfinite structure per piece."
                )

            tol = max(1e-4, lc * 1e-3)
            for surf_tag in surf_tags:
                structured = False
                # Fast path: the recorded whole-strip corners survived intact.
                for corners in corner_sets:
                    corner_tags = self._locate_corner_tags_on_surface(surf_tag, corners, tol)
                    if corner_tags is not None:
                        structured = self._apply_transfinite_strip(
                            surf_tag, corner_tags, lc, thickness
                        )
                        if structured:
                            break
                # Split/trimmed pieces: re-derive each piece's corners from the
                # extreme boundary points on the original offset side lines.
                if not structured:
                    for side_lines in side_line_sets:
                        corner_tags = self._derive_strip_corners_on_surface(
                            surf_tag, side_lines, tol
                        )
                        if corner_tags is not None:
                            structured = self._apply_transfinite_strip(
                                surf_tag, corner_tags, lc, thickness
                            )
                            if structured:
                                break
                if not structured and (corner_sets or side_line_sets):
                    warnings.warn(
                        f"Could not apply transfinite structure to buffer surface "
                        f"{surf_tag} of feature {feat_id} (the strip was altered by "
                        "fragmentation, e.g. end caps subdivided where the strip meets "
                        "the domain boundary); meshing it recombine-only."
                    )
                if not structured:
                    self._set_default_buffer_curve_divisions(surf_tag, lc)
                try:
                    gmsh.model.mesh.setRecombine(2, int(surf_tag))
                    gmsh.model.mesh.setAlgorithm(2, int(surf_tag), 8)
                except Exception as e:
                    warnings.warn(
                        f"Could not apply recombination to buffer surface {surf_tag}: {e}"
                    )
                if structured:
                    transfinite_count += 1
                else:
                    recombine_only_count += 1

        if self._verbosity > 0:
            logger.info(
                      f"Applied structured quad-buffer meshing to "
                      f"{transfinite_count + recombine_only_count} surface(s) "
                      f"({transfinite_count} transfinite, {recombine_only_count} recombine-only)."
            )
    
    def _setup_fields(self, gmsh_map, polygons_gdf, lines_gdf, points_gdf):
        """
        Configures Gmsh mesh size fields based on the input features.

        This method creates and combines various fields (`Distance`, `Threshold`,
        `MathEval`) to control the mesh element size across the domain. It uses
        the parameters (e.g., `lc`, `dist_min`, `dist_max`) from the
        original conceptual model features to define how the mesh should be
        refined near points, along lines, and within polygons.
        """
        if self._verbosity > 0:
            logger.info("--- Setup Fields Debug ---")
            logger.info(f"Polygons GDF: {len(polygons_gdf)} rows")
            logger.info(f"Gmsh Surface Map: {len(gmsh_map.get('surfaces', {}))} entries")
            if not polygons_gdf.empty:
                first_idx = polygons_gdf.index[0]
                logger.info(f"First Poly Index: {first_idx} (Type: {type(first_idx)})")
                if gmsh_map['surfaces']:
                    first_key = list(gmsh_map['surfaces'].keys())[0]
                    logger.info(f"First Map Key: {first_key} (Type: {type(first_key)})")
                    logger.info(f"Match? {first_idx in gmsh_map['surfaces']}")
                else:
                    logger.info("Gmsh Surface Map is EMPTY.")

        # Collect all created Gmsh field ids so we can combine them at the end.
        field_list = []
        
        # The global background mesh size is always required.
        # We create a Constant field for it and always set a background mesh.
        self._validate_background_lc()
        global_max_lc = float(self.background_lc)

        def extract_tags(entry_list):
            """Return a clean list of integer tags from Gmsh's dimtag-ish output.

            Gmsh commonly returns lists of (dim, tag) tuples; some maps in this
            code also store raw tag ints. We normalize both to an int tag list.
            """
            clean_tags = []
            for item in entry_list:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    clean_tags.append(item[1])
                else:
                    clean_tags.append(item)
            return clean_tags

        def get_row_param(row, key, default):
            if key in row and not pd.isna(row[key]):
                return float(row[key])
            return float(default)

        def _normalize_fields(value):
            """Normalize feature field specifications to a list[MeshField].

            Supported inputs:
            - None / NaN -> []
            - MeshField  -> [field]
            - list/tuple/set of mixed values -> only MeshField entries are kept
            """
            if value is None:
                return []
            if isinstance(value, float) and pd.isna(value):
                return []
            if isinstance(value, MeshField):
                return [value]
            if isinstance(value, (list, tuple, set)):
                return [v for v in value if isinstance(v, MeshField)]
            return []

        def _auto_field_from_row(row, background_lc, has_explicit_fields):
            """Build the implicit size field that backs a feature's resolution.

            Default: a GeometricGrowthField that grows the mesh from the
            feature size up to the background size at the feature's growth_factor
            (DEFAULT_GROWTH_FACTOR when unset). Created only when the feature is
            finer than the background and has no explicit ``fields``.

            Legacy (deprecated): if dist_min/dist_max are supplied, honor them as
            the old linear ThresholdField and emit a DeprecationWarning. This path
            is kept (even alongside explicit fields) so existing models still mesh.
            """
            if background_lc is None or (isinstance(background_lc, float) and pd.isna(background_lc)):
                return None

            feature_lc = row.get('lc', None)
            if feature_lc is None or (isinstance(feature_lc, float) and pd.isna(feature_lc)):
                return None
            feature_lc = float(feature_lc)

            dist_min = row.get('dist_min', None)
            dist_max = row.get('dist_max', None)
            dist_min = None if (dist_min is None or (isinstance(dist_min, float) and pd.isna(dist_min))) else float(dist_min)
            dist_max = None if (dist_max is None or (isinstance(dist_max, float) and pd.isna(dist_max))) else float(dist_max)

            if dist_min is not None or dist_max is not None:
                # --- Legacy linear ThresholdField (deprecated) ---
                warnings.warn(
                    "dist_min/dist_max are deprecated for feature size transitions; they "
                    "select the legacy linear ThresholdField. Omit them to use the default "
                    "GeometricGrowthField (tune it with growth_factor), or pass an explicit "
                    "ThresholdField in `fields` to keep a linear ramp.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                # DistMin: at least one local element size; DistMax: broad scale.
                if dist_min is None:
                    dist_min = feature_lc
                if dist_max is None:
                    dist_max = float(background_lc) * 5.0
                dist_min = max(dist_min, feature_lc * 0.5)
                # Enforce a gentle gradient relative to SizeMax.
                min_span = 3.0 * float(background_lc)
                if (dist_max - dist_min) < min_span:
                    dist_max = dist_min + min_span
                if dist_max <= dist_min:
                    dist_max = dist_min + max(float(background_lc), feature_lc, 1e-3)
                return ThresholdField(size_min=feature_lc, dist_min=dist_min, dist_max=dist_max, size_max=background_lc)

            # --- Default GeometricGrowthField ---
            # Only when the user has not supplied an explicit field and the
            # feature is actually finer than the background (else nothing to do).
            if has_explicit_fields or feature_lc >= float(background_lc):
                return None
            growth = row.get('growth_factor', None)
            if growth is None or (isinstance(growth, float) and pd.isna(growth)):
                growth = DEFAULT_GROWTH_FACTOR
            growth = float(growth)
            return GeometricGrowthField(growth_factor=growth)

        def _border_field_from_row(row):
            """Border grading backing the deprecated add_polygon(border_density=...)."""
            border_lc = row.get('border_lc', None)
            if border_lc is None or pd.isna(border_lc):
                return None
            return _BorderGradingField(
                border_size=float(border_lc),
                dist_min=get_row_param(row, 'dist_min', 0.0),
                dist_max=None if pd.isna(row.get('dist_max_in', None)) else float(row['dist_max_in']),
            )

        # Configure mesh size fields using MeshField objects attached to features.
        #
        # Data model expectations:
        # - ConceptualMesh stores the user's desired behavior in GeoDataFrame rows.
        # - Fields are created here (engine side) because the engine has the Gmsh
        #   tags and is responsible for mapping features -> CAD entities.
        #
        # How fields can be specified per feature:
        # - `fields`: list[MeshField] (the only supported explicit mechanism)
        # - resolution (+ growth_factor): default implicit GeometricGrowthField
        # - `dist_min/dist_max` (+ lc): DEPRECATED shorthand for a linear ThresholdField
        #
        # Grouping:
        # - We build ONE gmsh field per unique (field parameters + lc).
        # - We intentionally do NOT split by geometry type because a single Gmsh
        #   Distance/Threshold field can target points/curves/surfaces at once.
        # - `feature_lc` is part of grouping because growth fields compute their
        #   transition based on the local target size.
        #
        # Each group accumulates feature ids per geometry type so we can later
        # gather all relevant gmsh tags into a single tags_dict.
        field_objects = {}
        for gdf, geom_type in [(points_gdf, 'points'), (lines_gdf, 'lines'), (polygons_gdf, 'surfaces')]:
            for idx, row in gdf.iterrows():
                # 1) Collect explicitly specified fields.
                explicit_fields = _normalize_fields(row.get('fields', None))
                row_fields = list(explicit_fields)

                # 2) Add the implicit size field backing the feature's
                #    resolution: GeometricGrowthField by default, or the legacy
                #    ThresholdField when dist_min/dist_max are given (deprecated).
                auto_field = _auto_field_from_row(
                    row, global_max_lc, has_explicit_fields=bool(explicit_fields)
                )
                if auto_field is not None:
                    row_fields.append(auto_field)
                if geom_type == 'surfaces':
                    border_field = _border_field_from_row(row)
                    if border_field is not None:
                        row_fields.append(border_field)

                if not row_fields:
                    continue

                # Cache the feature lc for growth fields.
                feature_lc = row.get('lc', None)
                if feature_lc is None or (isinstance(feature_lc, float) and pd.isna(feature_lc)):
                    feature_lc = None
                else:
                    feature_lc = float(feature_lc)

                for field in row_fields:
                    if field is None or not isinstance(field, MeshField):
                        continue
                    key = (hash(field), feature_lc)
                    if key not in field_objects:
                        field_objects[key] = {
                            'field': field,
                            'feature_lc': feature_lc,
                            'feature_ids_by_geom': {'points': [], 'lines': [], 'surfaces': []},
                        }
                    field_objects[key]['feature_ids_by_geom'][geom_type].append(int(idx))
        
        # Now create and apply each unique field to the corresponding features.
        # We gather all Gmsh entity tags for the features and let the MeshField
        # implementation create the appropriate Distance/Threshold/etc field.
        for key, info in field_objects.items():
            field = info['field']
            feature_lc = info.get('feature_lc', None)
            feature_ids_by_geom = info.get('feature_ids_by_geom', {'points': [], 'lines': [], 'surfaces': []})
            
            # Gather all Gmsh tags for the features using this field.
            # Note: embedded geometry is mapped under gmsh_map[geom_type].
            # For embed=False polygons, we keep their boundary curves under
            # gmsh_map['poly_curves'] so fields can still be applied without
            # cutting/fragmenting the domain.
            tags_dict = {
                'points': [],
                'lines': [],
                'surfaces': [],
                'embedded_surfaces': [],
                'field_only_surfaces': [],
            }

            # Points
            for fid in feature_ids_by_geom.get('points', []):
                if fid in gmsh_map.get('points', {}):
                    tags_dict['points'].extend(extract_tags(gmsh_map['points'][fid]))

            # Lines
            for fid in feature_ids_by_geom.get('lines', []):
                if fid in gmsh_map.get('lines', {}):
                    tags_dict['lines'].extend(extract_tags(gmsh_map['lines'][fid]))
                # Straddle/barrier lines are represented by point pairs.
                elif fid in gmsh_map.get('straddle_points', {}):
                    tags_dict['points'].extend(extract_tags(gmsh_map['straddle_points'][fid]))
                elif ('line', fid) in gmsh_map.get('structured_buffer_surfs', {}):
                    # Buffer strips are embedded surfaces; list them as such so
                    # distance-growth fields target their boundary curves
                    # (an empty 'embedded_surfaces' would disable the field).
                    surface_tags = extract_tags(gmsh_map['structured_buffer_surfs'][('line', fid)])
                    tags_dict['surfaces'].extend(surface_tags)
                    tags_dict['embedded_surfaces'].extend(surface_tags)

            # Surfaces
            for fid in feature_ids_by_geom.get('surfaces', []):
                if fid in gmsh_map.get('surfaces', {}):
                    surface_tags = extract_tags(gmsh_map['surfaces'][fid])
                    # A buffered polygon's outline lives in its band surfaces
                    # (the interior is inset), so include them for field
                    # targeting too.
                    if ('poly', fid) in gmsh_map.get('structured_buffer_surfs', {}):
                        surface_tags = surface_tags + extract_tags(
                            gmsh_map['structured_buffer_surfs'][('poly', fid)]
                        )
                    tags_dict['surfaces'].extend(surface_tags)

                    try:
                        embed_val = polygons_gdf.loc[fid].get('embed', True)
                        embedded = True if pd.isna(embed_val) else bool(embed_val)
                    except Exception:
                        embedded = True

                    if embedded:
                        tags_dict['embedded_surfaces'].extend(surface_tags)
                    else:
                        tags_dict['field_only_surfaces'].extend(surface_tags)
                # Field-only polygons (embed=False): apply distance-based fields to boundary curves.
                elif fid in gmsh_map.get('poly_curves', {}):
                    curve_dimtags = gmsh_map['poly_curves'][fid]
                    tags_dict['lines'].extend(extract_tags(curve_dimtags))

            if not any(tags_dict.values()):
                continue

            # Private metadata for built-in field helpers; custom MeshField
            # implementations can ignore it because tag lists remain unchanged.
            tags_dict['_verbosity'] = self._verbosity
            
            # Create the Gmsh field using the provided MeshField object.
            f_id = field.create(
                gmsh_api=gmsh,
                tags_dict=tags_dict,
                background_lc=global_max_lc,
                feature_lc=feature_lc
            )
            
            if f_id is not None:
                field_list.append(f_id)

        # Crossing refinement: where two quad buffers cross, the lower-priority
        # one is trimmed away, leaving a small gap that the unstructured mesher
        # would otherwise fill at the background size right next to the dense
        # strip rows -- the size jump and quality crater the user sees. Pin each
        # crossing region to min(lc) of the two features with a Ball field
        # (rotation-agnostic, no OCC geometry added). The Min field below takes
        # the smallest requested size, and transfinite strips ignore size fields,
        # so the continuous winner is unaffected.
        for crossing in getattr(self, '_quad_buffer_crossings', []):
            ball = gmsh.model.mesh.field.add("Ball")
            gmsh.model.mesh.field.setNumber(ball, "Radius", float(crossing.radius))
            gmsh.model.mesh.field.setNumber(ball, "XCenter", float(crossing.x))
            gmsh.model.mesh.field.setNumber(ball, "YCenter", float(crossing.y))
            gmsh.model.mesh.field.setNumber(ball, "ZCenter", 0.0)
            gmsh.model.mesh.field.setNumber(ball, "VIn", float(crossing.size))
            gmsh.model.mesh.field.setNumber(ball, "VOut", global_max_lc)
            gmsh.model.mesh.field.setNumber(ball, "Thickness", 3.0 * float(crossing.size))
            field_list.append(ball)

        #now lets add the background constant field if specified
        if self.background_lc is not None:
            const_field = ConstantField(size=self.background_lc)
            f_id = const_field.create(
                gmsh_api=gmsh,
                tags_dict={},
                background_lc=self.background_lc,
                feature_lc=None
            )
            if f_id is not None:
                field_list.append(f_id)

        # Combine all active fields using a Min field and set it as the background mesh.
        # At any (x,y), Gmsh will take the smallest requested element size.
        if field_list:
            min_field = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(min_field, "FieldsList", [float(f) for f in field_list])
            gmsh.model.mesh.field.setAsBackgroundMesh(min_field)

        # Disable Gmsh's default sizing mechanisms so fields fully control mesh size.
        # Otherwise, mesh sizing from points/curvature/boundary can compete with fields.
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


    def _embed_features(self, gmsh_map, polygons_gdf, lines_gdf, points_gdf):
        """
        Explicitly embeds features into domain surfaces to ensure mesh conformity.
        
        This handles cases where fragmentation splits surfaces, requiring
        geometric discovery to find the correct surface for points/lines.
        """
        if self._verbosity > 0:
            logger.info("Explicitly embedding features into domain surfaces...")

        def is_embedded(row):
            val = row.get('embed', True)
            if pd.isna(val):
                return True
            return bool(val)

        # 1. Collect Domain Surfaces (Candidate Pool)
        domain_surface_tags = set()
        if not polygons_gdf.empty:
            for idx, row in polygons_gdf.iterrows():
                if is_embedded(row) and idx in gmsh_map.get('surfaces', {}):
                    for dt in gmsh_map['surfaces'][idx]:
                        # Ensure we are tracking actual surfaces (dim=2)
                        if isinstance(dt, (tuple, list)) and len(dt) >= 2 and dt[0] == 2:
                            domain_surface_tags.add(dt[1])

        # >>> DIAG: domain surface collection summary
        if self._verbosity >= 2:
            _gdf_idxs = list(polygons_gdf.index) if not polygons_gdf.empty else []
            _map_keys = list(gmsh_map.get('surfaces', {}).keys())
            _matching = [i for i in _gdf_idxs if i in gmsh_map.get('surfaces', {})]
            logger.debug(f"[DIAG] Embed pool: GDF indices={_gdf_idxs}, map keys={_map_keys}, "
                         f"matched={len(_matching)}, domain_surface_tags={sorted(domain_surface_tags)}")
            # Dump bbox of ALL surfaces - shows which surfaces cover which area
            _all_surfs = gmsh.model.getEntities(2)
            for _s in _all_surfs:
                _in_pool = "POOL" if _s[1] in domain_surface_tags else "----"
                try:
                    _sbb = gmsh.model.getBoundingBox(2, _s[1])
                    logger.debug(f"[DIAG]   surf {_s[1]:3d} [{_in_pool}] "
                                 f"x=[{_sbb[0]:7.1f},{_sbb[3]:7.1f}] "
                                 f"y=[{_sbb[1]:7.1f},{_sbb[4]:7.1f}]")
                except Exception:
                    logger.debug(f"[DIAG]   surf {_s[1]:3d} [{_in_pool}] bbox FAILED")
            # Which feature id maps to which surface tags?
            for _feat_id, _dts in gmsh_map.get('surfaces', {}).items():
                _stags = [int(dt[1]) for dt in _dts if isinstance(dt, (tuple,list)) and dt[0]==2]
                logger.debug(f"[DIAG]   map[surfaces][{_feat_id}] -> tags {_stags}")
        # <<< DIAG

        if not domain_surface_tags:
            return 

        # >>> DIAG: Accumulator for embed summary
        _elog = {'ok': 0, 'conflict': 0, 'skip_bbox': 0, 'skip_no_cand': 0,
                 'skip_no_match': 0, 'failed': 0, 'boundary_skip': 0,
                 'multi_match': 0, 'inside_failed': 0,
                 'conflict_tags': [], 'fail_tags': [], 'boundary_tags': [],
                 'multi_tags': [], 'inside_fail_tags': [], 'records': []}
        # <<< DIAG

        # Helper for geometric embedding search and application.
        # We pre-compute surface bboxes for a fast spatial filter, then confirm
        # against trimmed surfaces with gmsh.model.isInside(). Do not use
        # getClosestPoint() here: for coplanar OCC surfaces it can project onto
        # the support plane outside the trimmed face, causing false multi-surface
        # embeds and over-constraining Gmsh.
        _surf_bboxes = {}
        for _st in domain_surface_tags:
            try:
                _bb = gmsh.model.getBoundingBox(2, _st)
                _surf_bboxes[_st] = _bb  # (xmin, ymin, zmin, xmax, ymax, zmax)
            except Exception:
                logger.debug("No bounding box for domain surface %d; it is "
                             "excluded from the embedding candidate pool.", _st)

        def _bbox_contains_point(sbb, pt, eps=1e-4):
            return (
                sbb[0] - eps <= pt[0] <= sbb[3] + eps and
                sbb[1] - eps <= pt[1] <= sbb[4] + eps and
                sbb[2] - eps <= pt[2] <= sbb[5] + eps
            )

        def _entity_sample_points(dim, tag, bbox):
            xmin, ymin, zmin, xmax, ymax, zmax = bbox
            if dim == 0:
                return [((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)]
            if dim != 1:
                return []

            pmin, pmax = gmsh.model.getParametrizationBounds(1, tag)
            lo = float(pmin[0])
            hi = float(pmax[0])
            if not (math.isfinite(lo) and math.isfinite(hi)):
                return []
            if hi < lo:
                lo, hi = hi, lo

            # Avoid exact endpoints: line ends commonly lie on partition
            # boundaries and are ambiguous. Interior samples identify the
            # trimmed surface that actually owns the line fragment.
            params = [lo + (hi - lo) * f for f in (0.25, 0.5, 0.75)]
            points = []
            seen = set()
            for param in params:
                val = gmsh.model.getValue(1, tag, [param])
                pt = (float(val[0]), float(val[1]), float(val[2]))
                key = (round(pt[0], 8), round(pt[1], 8), round(pt[2], 8))
                if key not in seen:
                    seen.add(key)
                    points.append(pt)
            return points

        def _surface_area(surf_tag):
            try:
                return float(gmsh.model.occ.getMass(2, int(surf_tag)))
            except Exception:
                try:
                    return float(gmsh.model.getMass(2, int(surf_tag)))
                except Exception:
                    return float("inf")

        def embed_entity(dim, tag):
            # 1. Verify entity exists
            try:
                bbox = gmsh.model.getBoundingBox(dim, tag)
            except Exception:
                _elog['skip_bbox'] += 1
                return
            
            xmin, ymin, zmin, xmax, ymax, zmax = bbox
            
            # Check if line is already a boundary of some surface. Boundary
            # curves already constrain their adjacent surfaces; explicitly
            # embedding them elsewhere duplicates constraints and can make Gmsh
            # non-terminating on dense partitioned geometries.
            is_boundary_of = set()
            if dim == 1:
                try:
                    up, _down = gmsh.model.getAdjacencies(1, tag)
                    is_boundary_of = {int(v) for v in up}
                except Exception:
                    logger.debug("getAdjacencies failed for curve %d; treating "
                                 "it as interior for embedding.", tag)
                if is_boundary_of:
                    _elog['boundary_skip'] += 1
                    _elog['boundary_tags'].append((int(tag), sorted(is_boundary_of)))
                    return

            # 2. Sample the entity inside its extent.
            try:
                sample_points = _entity_sample_points(dim, tag, bbox)
            except Exception:
                sample_points = []
            if not sample_points:
                _elog['skip_no_match'] += 1
                return

            # 3. Fast bbox pre-filter: only test surfaces whose bbox contains at
            # least one sampled point.
            candidates = set()
            eps = 1e-4
            for surf_tag, sbb in _surf_bboxes.items():
                if any(_bbox_contains_point(sbb, pt, eps=eps) for pt in sample_points):
                    candidates.add(int(surf_tag))

            if not candidates:
                _elog['skip_no_cand'] += 1
                return

            # 4. Confirm with isInside(). For lines, require all interior sample
            # points to be inside a single trimmed surface.
            target_matches = []
            flat_points = []
            for pt in sample_points:
                flat_points.extend([pt[0], pt[1], pt[2]])
            for surf_tag in sorted(candidates):
                try:
                    inside_count = int(gmsh.model.isInside(2, int(surf_tag), flat_points))
                    if inside_count == len(sample_points):
                        target_matches.append(surf_tag)
                except Exception:
                    _elog['inside_failed'] += 1
                    _elog['inside_fail_tags'].append((int(tag), int(surf_tag)))

            # 5. Embed the entity into the verified surfaces
            if target_matches:
                target_matches = sorted(set(target_matches))
                if len(target_matches) > 1:
                    _elog['multi_match'] += 1
                    _elog['multi_tags'].append((int(tag), list(target_matches)))
                    # Nested or overlapping source polygons can still produce
                    # multiple containing faces. Choose the smallest trimmed
                    # surface as the most local owner instead of embedding the
                    # same entity into every containing face.
                    target_matches = [min(target_matches, key=_surface_area)]
                for st in target_matches:
                    try:
                        gmsh.model.mesh.embed(dim, [tag], 2, st)
                        _elog['ok'] += 1
                        if self.diagnose:
                            _elog['records'].append({
                                'dim': int(dim),
                                'tag': int(tag),
                                'surface': int(st),
                                'bbox': tuple(float(v) for v in bbox),
                                'sample_points': sample_points,
                            })
                    except Exception as e:
                        _elog['failed'] += 1
                        _elog['fail_tags'].append((tag, str(e)[:60]))
            else:
                _elog['skip_no_match'] += 1

        # Iterate and Embed Points
        if points_gdf is not None and not points_gdf.empty:
            for idx, row in points_gdf.iterrows():
                if is_embedded(row) and idx in gmsh_map.get('points', {}):
                    for dt in gmsh_map['points'][idx]:
                        if dt[0] == 0:
                            embed_entity(0, dt[1])

        # Iterate and Embed Lines
        if lines_gdf is not None and not lines_gdf.empty:
            for idx, row in lines_gdf.iterrows():
                if is_embedded(row):
                    # Standard Lines
                    if idx in gmsh_map.get('lines', {}):
                        for dt in gmsh_map['lines'][idx]:
                            if dt[0] == 1:
                                embed_entity(1, dt[1])
                    # Barrier/Straddle Points (these are points derived from lines)
                    if idx in gmsh_map.get('straddle_points', {}):
                        for dt in gmsh_map['straddle_points'][idx]:
                            if dt[0] == 0:
                                embed_entity(0, dt[1])

        # >>> DIAG: Embed summary
        if self._verbosity >= 2:
            _filt = _elog.get('skip_filtered', 0)
            logger.debug(f"[DIAG] Embed results: {_elog['ok']} OK, "
                         f"{_elog['conflict']} boundary-conflicts, "
                         f"{_elog['failed']} failed, "
                         f"{_elog['skip_bbox']} no-bbox, "
                         f"{_elog['skip_no_cand']} empty-bbox, "
                         f"{_filt} filtered-out, "
                         f"{_elog['skip_no_match']} no-match, "
                         f"{_elog['boundary_skip']} boundary-skip, "
                         f"{_elog['multi_match']} multi-match, "
                         f"{_elog['inside_failed']} inside-failed")
            if _filt > 0:
                logger.debug(f"[DIAG] *** {_filt} entities found nearby surfaces but NONE "
                             f"were in domain_surface_tags — likely missing domain surface! ***")
            if _elog['conflict_tags']:
                uniq = sorted(set(_elog['conflict_tags']))
                logger.debug(f"[DIAG] *** {len(uniq)} unique line tags had BOUNDARY CONFLICTS "
                             f"(first 10): {uniq[:10]} ***")
            if _elog['fail_tags']:
                logger.debug(f"[DIAG] *** Failed embeds: {_elog['fail_tags'][:5]} ***")
            if _elog['boundary_tags']:
                uniq = _elog['boundary_tags'][:10]
                logger.debug(f"[DIAG] Boundary line fragments skipped (first 10): {uniq}")
            if _elog['multi_tags']:
                logger.debug(f"[DIAG] Multi-surface embed candidates collapsed "
                             f"(first 10): {_elog['multi_tags'][:10]}")
        # <<< DIAG

        self.diagnostics['embedding'] = {
            'ok': _elog['ok'],
            'failed': _elog['failed'],
            'skip_bbox': _elog['skip_bbox'],
            'skip_no_cand': _elog['skip_no_cand'],
            'skip_no_match': _elog['skip_no_match'],
            'boundary_skip': _elog['boundary_skip'],
            'multi_match': _elog['multi_match'],
            'inside_failed': _elog['inside_failed'],
            'boundary_tags': list(_elog['boundary_tags']),
            'multi_tags': list(_elog['multi_tags']),
            'fail_tags': list(_elog['fail_tags']),
        }
        if self.diagnose:
            self.diagnostics['embedding']['records'] = list(_elog['records'])


    def generate(self, clean_polys, clean_lines, clean_points, output_file=None, launch_gmsh_gui=False):
        """
        Executes the full mesh generation workflow.

        This method orchestrates the entire process:
        1. Initializes Gmsh.
        2. Transfers geometries into the Gmsh model.
        3. Sets up mesh size fields.
        4. Generates the 2D triangular mesh.
        5. Performs optional post-generation optimization.
        6. Extracts the resulting nodes and their tags.

        Args:
            clean_polys (GeoDataFrame): Non-overlapping polygons.
            clean_lines (GeoDataFrame): Snapped and cleaned lines.
            clean_points (GeoDataFrame): Snapped and cleaned points.
            output_file (str, optional): If provided, saves the mesh to this path.
            launch_gmsh_gui (Boolean, optional): This allow to see triangular mesh results
                using the GMSH GUI, and allow to review visually the fields and the triangular
                mesh quality

        Returns:
            bool: True if generation was successful.
        
        Raises:
            Exception: If any step in the Gmsh process fails.
        """
        self._validate_background_lc()
        with verbosity_scope(self.verbosity):
            self._verbosity = self._resolve_verbosity()
            return self._generate(clean_polys, clean_lines, clean_points,
                                  output_file=output_file, launch_gmsh_gui=launch_gmsh_gui)

    def _generate(self, clean_polys, clean_lines, clean_points, output_file=None, launch_gmsh_gui=False):
        """Run the Gmsh workflow behind ``generate()``."""
        self.triangular_quality = None
        self.element_grid = None
        self._element_data = None
        self._initialize_gmsh()
        try:
            logger.info("Transferring Geometry to Gmsh...")
            gmsh_map = self._add_geometry(clean_polys, clean_lines, clean_points, launch_gmsh_gui=launch_gmsh_gui)
            
            # Ensure features are correctly embedded in surfaces before meshing
            self._embed_features(gmsh_map, clean_polys, clean_lines, clean_points)

            self._log_post_embed_diagnostics(gmsh_map)

            logger.info("Setting up Resolution Fields...")
            self._setup_fields(gmsh_map, clean_polys, clean_lines, clean_points)
            
            # Set the core meshing algorithm.
            gmsh.option.setNumber("Mesh.Algorithm", self.mesh_algorithm) 
            
            # Set the number of internal smoothing steps.
            gmsh.option.setNumber("Mesh.Smoothing", self.smoothing_steps)

            # Tolerance for the initial Delaunay insertion — helps with
            # "Could not insert point" from near-degenerate geometry.
            gmsh.option.setNumber("Mesh.ToleranceInitialDelaunay", self.tolerance_initial_delaunay)

            logger.info("Generating Triangular Mesh...")
            gmsh.model.mesh.generate(2)
            
            # Run explicit optimization passes after generation for higher quality.
            if self.optimization_cycles > 0:
                if self._verbosity > 0:
                    logger.info(f"Running {self.optimization_cycles} Optimization Cycles (Relocate2D & Laplace2D)...")
                
                for i in range(self.optimization_cycles):
                    if self._verbosity > 1:
                        logger.info(f"  -> Cycle {i+1}/{self.optimization_cycles}")
                    # Moves nodes to improve element shape (compactness).
                    gmsh.model.mesh.optimize("Relocate2D",niter=1)
                    # Smooths the mesh to relax gradients (reduces drift).
                    gmsh.model.mesh.optimize("Laplace2D",niter=1)

            meshed_surface_tags = self._meshed_surface_tags(gmsh_map, clean_polys)
            self.triangular_quality = self._collect_triangular_quality(meshed_surface_tags)
            self._element_data = self._capture_element_data(meshed_surface_tags)
            
            if output_file:
                gmsh.write(output_file)

            # --- Node extraction (domain-only) ---
            # Do NOT use gmsh.model.mesh.getNodes() without args here.
            # That returns nodes from all entities, including standalone 1D meshes
            # on curves (e.g. field-only rivers) and any non-fragmented 2D surfaces.
            # Those extra nodes can unintentionally constrain downstream Voronoi
            # tessellation.

            def _is_embedded_row(row) -> bool:
                val = row.get('embed', True)
                if pd.isna(val):
                    return True
                return bool(val)

            def _accumulate_nodes(dim: int, ent_tag: int, include_boundary: bool, tag_to_xy: dict[int, tuple[float, float]]):
                nt, nc, _ = gmsh.model.mesh.getNodes(dim, int(ent_tag), includeBoundary=bool(include_boundary))
                if len(nt) == 0:
                    return
                pts = np.array(nc, dtype=float).reshape(-1, 3)
                for t, p in zip(nt, pts):
                    tt = int(t)
                    if tt not in tag_to_xy:
                        tag_to_xy[tt] = (float(p[0]), float(p[1]))

            tag_to_xy: dict[int, tuple[float, float]] = {}

            # 1) Surfaces of the meshed domain: embedded polygons plus
            # straddle/structured-buffer strips (their nodes are Voronoi
            # generators too). Field-only surfaces are excluded.
            domain_surface_tags = self._meshed_surface_tags(gmsh_map, clean_polys)

            # If we cannot determine domain surfaces from the map, fall back to
            # all 2D nodes (still avoids 1D-only nodes).
            if not domain_surface_tags:
                node_tags, coords, _ = gmsh.model.mesh.getNodes(2, -1, includeBoundary=True)
                nodes_3d = np.array(coords, dtype=float).reshape(-1, 3)
                self.nodes = nodes_3d[:, :2]
                self.node_tags = node_tags
            else:
                for s in domain_surface_tags:
                    _accumulate_nodes(2, s, True, tag_to_xy)

                # 2) Embedded constraints (optional safety)
                if clean_points is not None and not clean_points.empty and 'embed' in clean_points.columns:
                    for fid, row in clean_points.iterrows():
                        if not _is_embedded_row(row):
                            continue
                        if int(fid) in gmsh_map.get('points', {}):
                            for dimtag in gmsh_map['points'][int(fid)]:
                                if isinstance(dimtag, (tuple, list)) and len(dimtag) >= 2 and int(dimtag[0]) == 0:
                                    _accumulate_nodes(0, int(dimtag[1]), True, tag_to_xy)

                if clean_lines is not None and not clean_lines.empty and 'embed' in clean_lines.columns:
                    for fid, row in clean_lines.iterrows():
                        if not _is_embedded_row(row):
                            continue

                        if int(fid) in gmsh_map.get('lines', {}):
                            for dimtag in gmsh_map['lines'][int(fid)]:
                                if isinstance(dimtag, (tuple, list)) and len(dimtag) >= 2 and int(dimtag[0]) == 1:
                                    _accumulate_nodes(1, int(dimtag[1]), True, tag_to_xy)
                        # Straddle/barrier lines may have been converted into points.
                        elif int(fid) in gmsh_map.get('straddle_points', {}):
                            for dimtag in gmsh_map['straddle_points'][int(fid)]:
                                if isinstance(dimtag, (tuple, list)) and len(dimtag) >= 2 and int(dimtag[0]) == 0:
                                    _accumulate_nodes(0, int(dimtag[1]), True, tag_to_xy)

                # Finalize de-duplicated node arrays
                node_tags = np.array(list(tag_to_xy.keys()), dtype=np.uint64)
                nodes_xy = np.array([tag_to_xy[int(t)] for t in node_tags], dtype=float)
                self.nodes = nodes_xy
                self.node_tags = node_tags

            self.zones_gdf = clean_polys
            if launch_gmsh_gui:
                gmsh.fltk.run()
            self._finalize_gmsh()
            return True

        except Exception as e:
            logger.error(f"Mesh Generation Failed: {e}")
            self._finalize_gmsh()
            raise e
