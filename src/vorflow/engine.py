from __future__ import annotations

import logging
import gmsh
import math
import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.ops import linemerge, unary_union
from shapely.validation import make_valid
from .fields import (
    DEFAULT_GROWTH_FACTOR,
    ConstantField,
    GeometricGrowthField,
    MeshField,
    ThresholdField,
)
from ._log import set_verbosity


logger = logging.getLogger(__name__)


# Half-cell gap left between a trimmed (lower-priority) quad buffer and the
# continuous (higher-priority) one it crosses. The loser is trimmed to the
# winner's footprint plus this many local cell widths, so its quads butt up
# against the winner's structured node row with one clean unstructured row in
# between. Tunable; larger values widen the gap if slivers appear.
QUAD_BUFFER_CROSSING_GAP = 0.5

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


class MeshGenerator:
    def __init__(self, background_lc=None, verbosity=0, mesh_algorithm=6,
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
            verbosity (int): Gmsh verbosity level (0=silent, 1=basic, 2=debug).
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
        # The documented verbosity scale (0=silent, 1=basic, 2=debug) also
        # drives the package logger so console output honors it.
        set_verbosity(verbosity)
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
        self.diagnostics = {}

    def _sanitize_coords(self, coords, *, min_spacing=1e-5, require_closed=False, min_points=2):
        """Remove invalid and near-duplicate coordinates before OCC creation."""
        clean_coords = []
        for pt in coords:
            if len(pt) < 2:
                continue
            x = float(pt[0])
            y = float(pt[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if clean_coords:
                dist = math.sqrt((x - clean_coords[-1][0])**2 + (y - clean_coords[-1][1])**2)
                if dist <= min_spacing:
                    continue
            clean_coords.append((x, y))

        if require_closed and len(clean_coords) > 1:
            dist = math.sqrt(
                (clean_coords[0][0] - clean_coords[-1][0])**2 +
                (clean_coords[0][1] - clean_coords[-1][1])**2
            )
            if dist <= min_spacing:
                clean_coords.pop()

        if len(clean_coords) < min_points:
            return []
        return clean_coords
    
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
        gmsh.option.setNumber("General.Verbosity", self.verbosity)
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

        Embedded polygon surfaces plus straddle and structured-buffer strips.
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
        for map_key in ('straddle_surfs', 'structured_buffer_surfs'):
            for dimtags in gmsh_map.get(map_key, {}).values():
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

    def _collect_element_grid(self, zones_gdf=None, surface_tags=None):
        """Collect gmsh 2D element polygons while the model is live."""
        crs = getattr(zones_gdf, "crs", None)
        element_types, element_tags, element_node_tags = self._get_2d_elements(surface_tags)
        if len(element_tags) == 0:
            warnings.warn("gmsh returned no 2D elements; element grid is empty.")
            return self._empty_element_grid(crs)

        node_tags, node_coords, _ = gmsh.model.mesh.getNodes()
        coords_3d = np.asarray(node_coords, dtype=float).reshape(-1, 3)
        node_xy = {
            int(tag): (float(coord[0]), float(coord[1]))
            for tag, coord in zip(node_tags, coords_3d)
        }

        records = []
        for element_type, tags_for_type, nodes_for_type in zip(
            element_types,
            element_tags,
            element_node_tags,
        ):
            element_name, _, _, num_nodes, _, num_primary_nodes = gmsh.model.mesh.getElementProperties(
                int(element_type)
            )
            num_nodes = int(num_nodes)
            num_primary_nodes = int(num_primary_nodes) if int(num_primary_nodes) > 0 else num_nodes
            if num_nodes <= 0 or num_primary_nodes < 3:
                continue

            tags = np.asarray(tags_for_type, dtype=np.int64)
            flat_nodes = np.asarray(nodes_for_type, dtype=np.int64)
            if len(tags) == 0 or len(flat_nodes) == 0:
                continue

            connectivity = flat_nodes.reshape((len(tags), num_nodes))
            element_name_lower = element_name.lower()
            is_triangle = "triangle" in element_name_lower
            is_quad = "quadrangle" in element_name_lower or "quadrilateral" in element_name_lower

            for element_tag, element_nodes in zip(tags, connectivity):
                primary_nodes = [int(tag) for tag in element_nodes[:num_primary_nodes]]
                try:
                    coords = [node_xy[int(tag)] for tag in primary_nodes]
                except KeyError:
                    continue

                polygon = Polygon(coords)
                if polygon.is_empty or polygon.area <= 0:
                    continue
                if not polygon.is_valid:
                    polygon = make_valid(polygon)
                if polygon.geom_type != "Polygon" or polygon.is_empty or polygon.area <= 0:
                    continue

                centroid = polygon.centroid
                records.append(
                    {
                        "element_tag": int(element_tag),
                        "element_type": int(element_type),
                        "element_name": element_name,
                        "is_triangle": bool(is_triangle),
                        "is_quad": bool(is_quad),
                        "node_tags": tuple(primary_nodes),
                        "centroid_x": float(centroid.x),
                        "centroid_y": float(centroid.y),
                        "geometry": polygon,
                    }
                )

        if not records:
            warnings.warn("gmsh returned no usable 2D elements; element grid is empty.")
            return self._empty_element_grid(crs)

        grid = gpd.GeoDataFrame(records, geometry="geometry", crs=crs)
        grid = grid.sort_values("element_tag").reset_index(drop=True)

        return _assign_zones_to_elements(grid, zones_gdf)

    def get_element_grid(self, element_filter="all"):
        """
        Return cached gmsh 2D element polygons for the generated mesh.

        ``element_filter`` may be ``"all"``, ``"triangles"``, or ``"quads"``.
        The exporter is independent of the Voronoi tessellator and can represent
        mixed tri/quad meshes produced by future structured-buffer workflows.

        Each element is assigned the zone whose polygon intersects the element
        centroid. Ties (overlapping zones or centroids on shared borders) are
        broken deterministically: highest ``z_order`` wins, then the zone that
        appears earliest in the conceptual-mesh polygon table.
        """
        if self.element_grid is None:
            raise RuntimeError(
                "Element grid is not available. Call MeshGenerator.generate() first."
            )
        if element_filter not in {"all", "triangles", "quads"}:
            raise ValueError("element_filter must be one of 'all', 'triangles', or 'quads'.")

        grid = self.element_grid
        if element_filter == "triangles":
            grid = grid[grid["is_triangle"]]
        elif element_filter == "quads":
            grid = grid[grid["is_quad"]]
        return grid.copy()

    def _dedup_and_remap_fragment_map(self, out_map, object_tags, input_tag_info):
        """Remove duplicate OCC entities and remap the fragment map.

        removeAllDuplicates() can merge coincident entities (changing
        tags) without returning a mapping, so point coordinates are
        snapshotted beforehand and out_map tags that disappear are
        remapped to the surviving entity at the same location.
        Mutates out_map in place.
        """
        # Remove geometrically coincident (duplicate) entities left by
        # fragmentation.  Unlike healShapes this does NOT delete or merge
        # entities based on a size tolerance, so it cannot destroy surfaces
        # or convert interior lines into boundaries.
        #
        # Because removeAllDuplicates() can merge entities (changing tags)
        # without returning a mapping, we snapshot the coordinates of all
        # out_map entries beforehand and remap any that disappear.
        _pre_dedup_coords = {}  # (dim, tag) -> (x, y, z)  for dim-0 entries
        for i in range(len(out_map)):
            for dt in out_map[i]:
                d, t = int(dt[0]), int(dt[1])
                if d == 0 and (d, t) not in _pre_dedup_coords:
                    try:
                        bb = gmsh.model.occ.getBoundingBox(0, t)
                        _pre_dedup_coords[(d, t)] = (bb[0], bb[1], bb[2])
                    except Exception:
                        logger.debug("Pre-dedup snapshot: no bounding box for "
                                     "point %d; it cannot be remapped if "
                                     "removeAllDuplicates renumbers it.", t)

        gmsh.model.occ.removeAllDuplicates()

        # Refresh out_map: replace tags killed by removeAllDuplicates with
        # the surviving entity at the same location.
        if len(out_map) > 0:
            occ_alive = set()
            _alive_pts_by_coord = {}  # (round_x, round_y, round_z) -> tag
            for dim in range(3):
                for dt in gmsh.model.occ.getEntities(dim):
                    d, t = int(dt[0]), int(dt[1])
                    occ_alive.add((d, t))
                    if d == 0:
                        try:
                            bb = gmsh.model.occ.getBoundingBox(0, t)
                            # Round to ~nm precision to match coordinates
                            coord_key = (round(bb[0], 6), round(bb[1], 6), round(bb[2], 6))
                            _alive_pts_by_coord[coord_key] = t
                        except Exception:
                            logger.debug("Post-dedup survey: no bounding box "
                                         "for surviving point %d.", t)

            _dup_pruned = 0
            _dup_remapped = 0
            for i in range(len(out_map)):
                new_entries = []
                for dt in out_map[i]:
                    d, t = int(dt[0]), int(dt[1])
                    if (d, t) in occ_alive:
                        new_entries.append(dt)
                    elif d == 0 and (d, t) in _pre_dedup_coords:
                        # Tag was killed by dedup — find the surviving point
                        x, y, z = _pre_dedup_coords[(d, t)]
                        coord_key = (round(x, 6), round(y, 6), round(z, 6))
                        new_tag = _alive_pts_by_coord.get(coord_key)
                        if new_tag is not None:
                            new_entries.append((0, new_tag))
                            _dup_remapped += 1
                        else:
                            _dup_pruned += 1
                    else:
                        _dup_pruned += 1
                out_map[i] = new_entries
            if _dup_pruned > 0 or _dup_remapped > 0:
                logger.info(f"removeAllDuplicates: remapped {_dup_remapped}, pruned {_dup_pruned} tag(s) from fragment map.")

            # DIAG: Per-feature point tracking after dedup
            if self.verbosity >= 2:
                _pt_feat_status = []
                for i, input_dimtag in enumerate(object_tags):
                    key = _to_key(input_dimtag[0], input_dimtag[1])
                    info = input_tag_info.get(key, {})
                    if info.get('type') == 'point':
                        feat_id = info['id']
                        dim0 = [dt for dt in (out_map[i] if i < len(out_map) else [])
                                if int(dt[0]) == 0]
                        alive = [(d, t) for d, t in dim0 if (int(d), int(t)) in occ_alive]
                        _pt_feat_status.append((feat_id, len(dim0), len(alive)))
                _n_empty = sum(1 for _, n, a in _pt_feat_status if a == 0)
                logger.debug(f"[DIAG] Post-dedup point features: {len(_pt_feat_status)} total, "
                             f"{_n_empty} with 0 alive tags")
                if _n_empty > 0:
                    for fid, nd, na in _pt_feat_status:
                        if na == 0:
                            logger.debug(f"  [DIAG] Point feat_id={fid}: {nd} map entries, 0 alive")

    def _heal_and_remap_fragment_map(self, out_map, object_tags, input_tag_info):
        """Optionally heal OCC shapes, synchronize, and remap the map.

        healShapes rebuilds OCC topology - renumbering entities and even
        reusing a tag number for a DIFFERENT entity - so remapping is
        done purely by coordinate matching, never by tag identity.
        Mutates out_map in place. Always synchronizes the OCC model,
        even when heal_shapes is off.
        """
        if self.heal_shapes:
            # Snapshot coordinates of ALL out_map entities before heal so we
            # can remap tags that healShapes renumbers.
            _pre_heal_coords = {}  # (dim, tag) -> (x, y, z) for dim-0 entries
            for i in range(len(out_map)):
                for dt in out_map[i]:
                    d, t = int(dt[0]), int(dt[1])
                    if (d, t) not in _pre_heal_coords and d in (0, 1, 2):
                        try:
                            bb = gmsh.model.occ.getBoundingBox(d, t)
                            if d == 0:
                                _pre_heal_coords[(d, t)] = (bb[0], bb[1], bb[2])
                            else:
                                _pre_heal_coords[(d, t)] = (bb[0], bb[1], bb[2], bb[3], bb[4], bb[5])
                        except Exception:
                            logger.debug("Pre-heal snapshot: no bounding box "
                                         "for entity (dim %d, tag %d); it "
                                         "cannot be remapped if healShapes "
                                         "renumbers it.", d, t)

            pre_heal = set()
            for dim in range(3):
                for dt in gmsh.model.occ.getEntities(dim):
                    pre_heal.add((int(dt[0]), int(dt[1])))

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

        gmsh.model.occ.synchronize()

        # After healing, entity tags may have been renumbered (healShapes
        # rebuilds OCC topology even when all fix flags are off).
        # Remap out_map entries using coordinate matching, similar to dedup.
        if self.heal_shapes and len(out_map) > 0:
            surviving = set()
            # Build coordinate lookup for surviving entities per dimension.
            # healShapes introduces ~1e-6 coordinate drift, so we round to
            # 4 decimal places (0.1 mm) — enough to distinguish any two
            # intentionally distinct points while absorbing the drift.
            _HEAL_ROUND = 4
            _heal_alive_by_dim = {0: {}, 1: {}, 2: {}}  # dim -> coord_key -> tag
            for dim in range(3):
                for dt in gmsh.model.getEntities(dim):
                    d, t = int(dt[0]), int(dt[1])
                    surviving.add((d, t))
                    try:
                        bb = gmsh.model.getBoundingBox(d, t)
                        if d == 0:
                            coord_key = (round(bb[0], _HEAL_ROUND), round(bb[1], _HEAL_ROUND), round(bb[2], _HEAL_ROUND))
                        else:
                            coord_key = (round(bb[0], _HEAL_ROUND), round(bb[1], _HEAL_ROUND), round(bb[2], _HEAL_ROUND),
                                         round(bb[3], _HEAL_ROUND), round(bb[4], _HEAL_ROUND), round(bb[5], _HEAL_ROUND))
                        _heal_alive_by_dim[d][coord_key] = t
                    except Exception:
                        logger.debug("Post-heal survey: no bounding box for "
                                     "surviving entity (dim %d, tag %d).", d, t)

            # healShapes can reuse the same tag number for a DIFFERENT entity,
            # so we must ALWAYS remap by coordinates — never trust tag identity.
            _heal_remapped = 0
            _heal_pruned = 0
            _heal_kept = 0
            for i in range(len(out_map)):
                new_entries = []
                for dt in out_map[i]:
                    d, t = int(dt[0]), int(dt[1])
                    if (d, t) not in _pre_heal_coords:
                        # Entity wasn't snapshotted (shouldn't happen); keep if alive
                        if (d, t) in surviving:
                            new_entries.append(dt)
                            _heal_kept += 1
                        else:
                            _heal_pruned += 1
                        continue

                    # Look up the old coordinates and find the matching new tag
                    old_coords = _pre_heal_coords[(d, t)]
                    if d == 0:
                        coord_key = (round(old_coords[0], _HEAL_ROUND),
                                     round(old_coords[1], _HEAL_ROUND),
                                     round(old_coords[2], _HEAL_ROUND))
                    else:
                        coord_key = (round(old_coords[0], _HEAL_ROUND), round(old_coords[1], _HEAL_ROUND),
                                     round(old_coords[2], _HEAL_ROUND), round(old_coords[3], _HEAL_ROUND),
                                     round(old_coords[4], _HEAL_ROUND), round(old_coords[5], _HEAL_ROUND))
                    new_tag = _heal_alive_by_dim.get(d, {}).get(coord_key)
                    if new_tag is not None:
                        if new_tag == t:
                            new_entries.append(dt)
                            _heal_kept += 1
                        else:
                            new_entries.append((d, new_tag))
                            _heal_remapped += 1
                    else:
                        _heal_pruned += 1
                out_map[i] = new_entries

            if _heal_remapped > 0 or _heal_pruned > 0:
                logger.info(f"Heal post-processing: remapped {_heal_remapped}, pruned {_heal_pruned} tag(s) from fragment map.")

            # DIAG: Per-feature point tracking after heal
            if self.verbosity >= 2:
                _pt_feat_heal = []
                for i, input_dimtag in enumerate(object_tags):
                    key = _to_key(input_dimtag[0], input_dimtag[1])
                    info = input_tag_info.get(key, {})
                    if info.get('type') == 'point':
                        feat_id = info['id']
                        dim0 = [dt for dt in (out_map[i] if i < len(out_map) else [])
                                if int(dt[0]) == 0]
                        alive = [(d, t) for d, t in dim0
                                 if (int(d), int(t)) in surviving]
                        _pt_feat_heal.append((feat_id, len(dim0), len(alive)))
                _n_empty_h = sum(1 for _, n, a in _pt_feat_heal if a == 0)
                logger.debug(f"[DIAG] Post-heal point features: {len(_pt_feat_heal)} total, "
                             f"{_n_empty_h} with 0 alive tags (remapped {_heal_remapped}, pruned {_heal_pruned})")
                if _n_empty_h > 0:
                    for fid, nd, na in _pt_feat_heal:
                        if na == 0:
                            logger.debug(f"  [DIAG] Point feat_id={fid}: {nd} map entries, 0 alive after heal")
                # Report what heal removed/added
                heal_removed = pre_heal - surviving
                heal_added = surviving - pre_heal
                dim0_removed = [(d, t) for d, t in heal_removed if d == 0]
                dim0_added = [(d, t) for d, t in heal_added if d == 0]
                if dim0_removed or dim0_added:
                    logger.debug(f"[DIAG] Heal dim-0 changes: removed {len(dim0_removed)}, added {len(dim0_added)}")
                    if dim0_removed:
                        logger.debug(f"  [DIAG] Removed point tags: {sorted(t for _, t in dim0_removed)}")
                    if dim0_added:
                        logger.debug(f"  [DIAG] Added point tags: {sorted(t for _, t in dim0_added)}")


    def _add_geometry(self, polygons_gdf, lines_gdf, points_gdf, launch_gmsh_gui=False):
        """
        Transfers Shapely geometries from GeoDataFrames into the Gmsh model.

        This method adds points, lines, and polygons to Gmsh's internal CAD
        kernel (OCC). It also handles special cases like "straddle" lines and
        pre-processes barrier features before fragmenting all geometries to
        create a consistent topological model.
        """
        input_tag_info = {}

        # Refinement disks recorded where quad buffers cross (see
        # record_quad_buffer_crossings); consumed in _setup_fields.
        self._quad_buffer_crossings = []

        # Non-embedded geometry does not participate in fragmentation.
        # We still track it so mesh-size fields can be applied later.
        nonembedded_point_tags = {}
        nonembedded_line_tags = {}
        nonembedded_surface_tags = {}
        # For non-embedded polygons, we track their boundary curves so size
        # fields can be applied without forcing the polygon to cut/fragment the domain.
        nonembedded_poly_curve_tags = {}
        pending_nonembedded_polys = []

        # Embedded geometry DOES participate in fragmentation.
        embedded_point_tags = []
        embedded_line_tags = []
        embedded_surface_tags = []
        
        to_key = _to_key

        def is_embedded(row) -> bool:
            val = row.get('embed', True)
            if pd.isna(val):
                return True
            return bool(val)

        def create_polygon_surface(poly):
            """Create a Gmsh plane surface and return its tag plus boundary curves."""
            if poly.is_empty:
                return None, []

            poly = self._force_close_polygon(poly)

            def create_loop(coords):
                clean_coords = self._sanitize_coords(
                    coords,
                    min_spacing=1e-5,
                    require_closed=True,
                    min_points=3,
                )

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
                        logger.error(f"Error adding line {p1}-{p2}: {e}")
                        return None, []

                try:
                    loop_tag = gmsh.model.occ.addCurveLoop(l_tags)
                    return loop_tag, l_tags
                except Exception as e:
                    logger.error(f"Error adding curve loop: {e}")
                    return None, []

            exterior_loop_tag, exterior_lines = create_loop(list(poly.exterior.coords))
            if exterior_loop_tag is None:
                return None, []

            loops = [exterior_loop_tag]
            boundary_curve_tags = list(exterior_lines)
            for interior in poly.interiors:
                interior_loop_tag, interior_lines = create_loop(list(interior.coords))
                if interior_loop_tag is not None:
                    loops.append(interior_loop_tag)
                    boundary_curve_tags.extend(interior_lines)

            try:
                s_tag = gmsh.model.occ.addPlaneSurface(loops)
            except Exception as e:
                logger.error(f"Error creating surface: {e}")
                return None, []

            return s_tag, boundary_curve_tags

        def row_bool(row, column, default=False):
            val = row.get(column, default)
            if pd.isna(val):
                return bool(default)
            return (val is True) or (str(val).lower() in ['true', '1', 'yes'])

        def positive_number(value):
            if value is None or pd.isna(value):
                return None
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        def feature_lc(row):
            lc = positive_number(row.get('lc'))
            if lc is None:
                lc = positive_number(self.background_lc)
            return max(lc if lc is not None else 10.0, 0.001)

        def quad_buffer_thickness(row):
            value = row.get('quad_buffer_thickness', 1)
            if value is None or pd.isna(value):
                return 1
            value = int(value)
            if value not in (1, 2):
                raise ValueError("quad_buffer_thickness must be either 1 or 2.")
            return value

        def polygon_parts(geom):
            if geom.is_empty:
                return []
            if isinstance(geom, Polygon):
                return [geom]
            if isinstance(geom, MultiPolygon):
                return list(geom.geoms)
            if hasattr(geom, "geoms"):
                parts = []
                for part in geom.geoms:
                    parts.extend(polygon_parts(part))
                return parts
            return []

        def line_parts(geom):
            if geom.is_empty:
                return []
            if isinstance(geom, LineString):
                return [geom]
            if isinstance(geom, MultiLineString):
                return [part for part in geom.geoms if part.length > 0]
            if hasattr(geom, "geoms"):
                parts = []
                for part in geom.geoms:
                    parts.extend(line_parts(part))
                return parts
            return []

        def coerce_offset_line(geom):
            if isinstance(geom, LineString):
                return geom
            if isinstance(geom, MultiLineString):
                merged = linemerge(geom)
                if isinstance(merged, LineString):
                    return merged
                lines = [part for part in merged.geoms if part.length > 0] if hasattr(merged, "geoms") else []
                return max(lines, key=lambda line: line.length) if lines else None
            return None

        def domain_union_geometry():
            if polygons_gdf is None or polygons_gdf.empty:
                return None
            embedded = []
            for _, poly_row in polygons_gdf.iterrows():
                if is_embedded(poly_row):
                    embedded.append(poly_row.geometry)
            if not embedded:
                return None
            return make_valid(unary_union(embedded))

        domain_geom_for_buffers = domain_union_geometry()

        def add_structured_buffer_surface(buffer_geom, feature_id, input_type,
                                          corners=None, side_lines=None):
            """Create OCC surfaces for a buffer geometry; returns [(key, strip_info), ...]."""
            created = []
            if domain_geom_for_buffers is not None and not domain_geom_for_buffers.is_empty:
                buffer_geom = buffer_geom.intersection(domain_geom_for_buffers)
            buffer_geom = make_valid(buffer_geom)
            parts = [
                poly for poly in polygon_parts(buffer_geom)
                if not poly.is_empty and poly.area > 0
            ]
            if corners is not None and len(parts) != 1:
                # The recorded whole-strip corners no longer apply; pieces are
                # re-cornered individually from the side lines post-fragment.
                corners = None
            for poly in parts:
                s_tag, boundary_curve_tags = create_polygon_surface(poly)
                if s_tag is None:
                    continue
                key = to_key(2, s_tag)
                input_tag_info[key] = {'type': input_type, 'id': feature_id}
                embedded_surface_tags.append(key)
                created.append((key, {'corners': corners, 'side_lines': side_lines}))
            return created

        # Strip footprints collected for ring-vertex protection (see
        # push_ring_vertices_off_strips below).
        line_strip_polygons = []

        def plan_line_strip(row):
            # Pure-geometry planning (no OCC, no trimming): build the untrimmed
            # strip polygon(s) for a quad-buffered line so footprints exist for
            # all features before any are trimmed. Returns a list of
            # {'strip', 'corners', 'side_lines'} dicts, one per line part.
            line = row.geometry
            lc = feature_lc(row)
            thickness = quad_buffer_thickness(row)
            offset = thickness * lc / 2.0
            plans = []
            for part in line_parts(line):
                if part.length <= 0:
                    continue
                # gmshflow recipe: simplify then segmentize before offsetting so
                # both offsets are symmetric and split into ~lc-long segments,
                # which keeps the transfinite divisions equal on opposite sides
                # of the strip. (Adds one OCC curve per ~lc of feature length.)
                work = part.simplify(lc * 1.5)
                work = work.segmentize(lc)
                pos = coerce_offset_line(
                    work.offset_curve(offset, quad_segs=1, join_style=2, mitre_limit=5.0)
                )
                neg = coerce_offset_line(
                    work.offset_curve(-offset, quad_segs=1, join_style=2, mitre_limit=5.0)
                )
                if pos is None or neg is None:
                    warnings.warn(
                        f"Skipping structured buffer for line feature {row.name} after offset split."
                    )
                    continue
                # Clip the offset lines (not the strip polygon) to the domain so
                # their endpoints remain the true strip corners.
                if domain_geom_for_buffers is not None and not domain_geom_for_buffers.is_empty:
                    pos = coerce_offset_line(pos.intersection(domain_geom_for_buffers))
                    neg = coerce_offset_line(neg.intersection(domain_geom_for_buffers))
                    if pos is None or neg is None:
                        warnings.warn(
                            f"Skipping structured buffer for line feature {row.name} after domain clipping."
                        )
                        continue

                pos_coords = self._sanitize_coords(list(pos.coords), min_points=2)
                neg_coords = self._sanitize_coords(list(neg.coords), min_points=2)
                if len(pos_coords) < 2 or len(neg_coords) < 2:
                    continue

                strip = Polygon(pos_coords + list(reversed(neg_coords)))
                if not strip.is_valid:
                    strip = make_valid(strip)
                # Corner order matches gmshflow's setTransfiniteSurface(..., "Left", ...).
                corners = [
                    tuple(neg_coords[0]),
                    tuple(neg_coords[-1]),
                    tuple(pos_coords[-1]),
                    tuple(pos_coords[0]),
                ]
                plans.append({'strip': strip, 'corners': corners, 'side_lines': (pos, neg)})
            return plans

        def plan_polygon_band(row):
            # gmshflow parity (create_surfacegrid_from_buffer_poly): the band is
            # the annulus between the +/- offsets of the simplified outline, and
            # the zone interior is meshed from the inner offset, so the original
            # boundary never becomes mesh edges. thickness=1 leaves no nodes on
            # the outline (the Voronoi faces trace the shape); thickness=2 puts
            # a node row on it (a row of ~square cells centered on the shape).
            # Bands are annuli: no 4-corner transfinite structure is possible,
            # so they are meshed quasi-structured (recombined quads with ~lc
            # curve divisions). Pure geometry; returns the untrimmed band or None.
            geom = row.geometry
            lc = feature_lc(row)
            thickness = quad_buffer_thickness(row)
            offset = thickness * lc / 2.0
            # The band leaves little room to mesh, so simplify first.
            work = make_valid(geom.simplify(lc * 1.5))
            inner = make_valid(work.buffer(-offset, quad_segs=1, join_style=2, mitre_limit=5.0))
            outer = make_valid(work.buffer(offset, quad_segs=1, join_style=2, mitre_limit=5.0))
            inner_parts = [
                p for p in polygon_parts(inner) if not p.is_empty and p.area > 0
            ]
            if not inner_parts or outer.is_empty:
                warnings.warn(
                    f"Polygon feature {row.name} is too narrow for a quad_buffer band of "
                    f"width {2.0 * offset:g}; meshing it without the structured buffer."
                )
                return None
            inner = inner_parts[0] if len(inner_parts) == 1 else MultiPolygon(inner_parts)
            # Difference (rather than boundary.buffer) so the band's inner ring
            # and the interior surface share exact coordinates and OCC merges
            # them into a single curve.
            return make_valid(outer.difference(inner))

        def clean_trimmed_pieces(geom, lc, feature_label):
            # After one-sided trimming, drop "sleeve" slivers (thin wedges from
            # shallow-angle/tangential overlaps) that would force bad elements.
            # Morphological opening (mitre joins keep rectangles square) removes
            # whiskers; the area + erosion tests drop pieces thinner than ~0.8
            # cells. Dropped gaps are filled by unstructured elements.
            kept = []
            dropped = 0
            for part in polygon_parts(make_valid(geom)):
                if part.is_empty or part.area <= 0:
                    continue
                opened = make_valid(
                    part.buffer(-0.25 * lc, join_style=2).buffer(0.25 * lc, join_style=2)
                )
                candidates = polygon_parts(opened) if not opened.is_empty else []
                if not candidates:
                    dropped += 1
                    continue
                for sub in candidates:
                    sub = make_valid(sub.simplify(0.1 * lc))
                    if sub.is_empty or sub.area < 0.5 * lc * lc:
                        dropped += 1
                        continue
                    eroded = sub.buffer(-0.4 * lc)
                    if eroded.is_empty or getattr(eroded, 'area', 0.0) <= 0:
                        dropped += 1
                        continue
                    kept.append(sub)
            if dropped:
                warnings.warn(
                    f"Structured buffer for {feature_label} dropped {dropped} sliver "
                    "piece(s) at a crossing (too thin to mesh); that gap is filled with "
                    "unstructured elements. Flip z_order or simplify the geometry to avoid it."
                )
            return kept

        def create_line_structured_buffer(row):
            key = ('line', int(row.name))
            plan = strip_plans.get(key)
            if plan is None:
                return []
            lc = plan['lc']
            obstacles = higher_priority_obstacles(key)
            record_quad_buffer_crossings(key)
            created = []
            feature_label = f"line feature {row.name}"
            for part_plan in plan['parts']:
                strip = part_plan['strip']
                corners = part_plan['corners']
                side_lines = part_plan['side_lines']
                if obstacles is not None and strip.intersects(obstacles):
                    warnings.warn(
                        f"Structured buffer for {feature_label} crosses a higher-priority "
                        "protected feature; it is trimmed at the crossing (set z_order to "
                        "choose which feature stays continuous)."
                    )
                    pieces = clean_trimmed_pieces(
                        make_valid(strip.difference(obstacles)), lc, feature_label
                    )
                    corners = None
                    if not pieces:
                        continue
                    strip = make_valid(unary_union(pieces) if len(pieces) > 1 else pieces[0])
                line_strip_polygons.append(strip)
                created.extend(
                    add_structured_buffer_surface(
                        strip, key, 'structured_buffer_surf',
                        corners=corners, side_lines=side_lines,
                    )
                )
            return created

        def create_polygon_structured_buffer(row):
            key = ('poly', int(row.name))
            plan = strip_plans.get(key)
            if plan is None:
                return [], None
            lc = plan['lc']
            band = plan['band']
            obstacles = higher_priority_obstacles(key)
            record_quad_buffer_crossings(key)
            feature_label = f"polygon feature {row.name}"
            if obstacles is not None and band.intersects(obstacles):
                warnings.warn(
                    f"Structured buffer for {feature_label} crosses a higher-priority "
                    "protected feature; it is trimmed at the crossing (set z_order to "
                    "choose which feature stays continuous)."
                )
                pieces = clean_trimmed_pieces(
                    make_valid(band.difference(obstacles)), lc, feature_label
                )
                if not pieces:
                    return [], None
                band = make_valid(unary_union(pieces) if len(pieces) > 1 else pieces[0])
            created = add_structured_buffer_surface(
                band, key, 'structured_buffer_surf'
            )
            if not created:
                return [], None
            return created, band

        structured_buffer_specs = {}
        
        # Add all point features to the Gmsh model first.
        for idx, row in points_gdf.iterrows():
            tag = gmsh.model.occ.addPoint(row.geometry.x, row.geometry.y, 0)
            key = to_key(0, tag)
            if is_embedded(row):
                input_tag_info[key] = {'type': 'point', 'id': idx}
                embedded_point_tags.append(key)
            else:
                nonembedded_point_tags.setdefault(int(idx), []).append(key)
            
        # Build a protection corridor around each barrier/straddle/quad-buffer
        # feature. Their union (the "barrier zone") trims standard lines away
        # from these sensitive regions. Quad-buffer strips that cross each other
        # are resolved by priority (see strip_plans / higher_priority_obstacles
        # below): the winner stays continuous and only the loser is trimmed.
        def feature_protection_epsilon(row):
            lc = feature_lc(row)
            if row_bool(row, 'quad_buffer', False):
                return quad_buffer_thickness(row) * lc / 2.0
            straddle = positive_number(row.get('straddle_width'))
            if straddle:
                return straddle / 2.0
            return lc * 0.20

        def corridor_geometry(basis, eps, min_half_width=0.0):
            # The corridor is made slightly larger than the feature's half-width
            # to ensure a clean separation between standard lines and the
            # sensitive node pairs used for straddle barriers.
            return basis.buffer(max(eps * 1.20, min_half_width), cap_style=2)

        corridors_by_feature = {}
        for idx, row in lines_gdf.iterrows():
            if (
                row_bool(row, 'is_barrier', False)
                or row_bool(row, 'quad_buffer', False)
                or positive_number(row.get('straddle_width'))
            ):
                corridors_by_feature[('line', int(idx))] = (
                    row.geometry, feature_protection_epsilon(row)
                )
        if not polygons_gdf.empty:
            for idx, row in polygons_gdf.iterrows():
                if row_bool(row, 'quad_buffer', False) and is_embedded(row):
                    corridors_by_feature[('poly', int(idx))] = (
                        row.geometry.boundary, feature_protection_epsilon(row)
                    )

        barrier_zone = None
        if corridors_by_feature:
            barrier_zone = make_valid(unary_union([
                corridor_geometry(basis, eps)
                for basis, eps in corridors_by_feature.values()
            ]))
            if self.verbosity > 0:
                logger.info(f"Constructed Barrier Zone from {len(corridors_by_feature)} protected features.")

        # --- Quad-buffer crossing priority -------------------------------
        # Plan every quad-buffer footprint up front (pure geometry, no OCC) so
        # that when two cross, one stays continuous and only the lower-priority
        # one is trimmed -- against the winner's actual footprint plus a half-
        # cell gap, instead of both yielding to an inflated corridor (which left
        # a hole filled by coarse background triangles). Priority key (lower
        # wins): user z_order, then finer lc, then wider strip, then line over
        # polygon, then insertion order.
        def feature_z_order(row):
            val = row.get('z_order', 0)
            if val is None or pd.isna(val):
                return 0.0
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0

        strip_plans = {}
        _feat_counter = 0
        for idx, row in lines_gdf.iterrows():
            if not row_bool(row, 'quad_buffer', False):
                continue
            parts = plan_line_strip(row)
            if not parts:
                continue
            lc = feature_lc(row)
            thickness = quad_buffer_thickness(row)
            strip_plans[('line', int(idx))] = {
                'kind': 'line',
                'parts': parts,
                'footprint': make_valid(unary_union([p['strip'] for p in parts])),
                'lc': lc,
                'thickness': thickness,
                'width': thickness * lc,
                'priority_key': (-feature_z_order(row), lc, -(thickness * lc), 0, _feat_counter),
            }
            _feat_counter += 1
        if not polygons_gdf.empty:
            for idx, row in polygons_gdf.iterrows():
                if not (is_embedded(row) and row_bool(row, 'quad_buffer', False)):
                    continue
                band = plan_polygon_band(row)
                if band is None or band.is_empty:
                    continue
                lc = feature_lc(row)
                thickness = quad_buffer_thickness(row)
                strip_plans[('poly', int(idx))] = {
                    'kind': 'poly',
                    'band': band,
                    'footprint': make_valid(band),
                    'lc': lc,
                    'thickness': thickness,
                    'width': thickness * lc,
                    'priority_key': (-feature_z_order(row), lc, -(thickness * lc), 1, _feat_counter),
                }
                _feat_counter += 1

        def higher_priority_obstacles(self_key):
            # Union of geometry a quad buffer must keep clear of: the footprints
            # of strictly higher-priority quad buffers (buffered by a half-cell
            # so the loser's quads butt up against the winner's structured row),
            # plus the corridors of non-quad protected features (barrier/straddle
            # lines, which still trim mutually).
            self_plan = strip_plans.get(self_key)
            if self_plan is None:
                return None
            self_pkey = self_plan['priority_key']
            lc_self = self_plan['lc']
            geoms = []
            for key, plan in strip_plans.items():
                if key == self_key:
                    continue
                if plan['priority_key'] < self_pkey:
                    geoms.append(
                        plan['footprint'].buffer(
                            QUAD_BUFFER_CROSSING_GAP * lc_self, cap_style=2
                        )
                    )
            for key, (basis, eps) in corridors_by_feature.items():
                if key in strip_plans or key == self_key:
                    continue
                geoms.append(corridor_geometry(basis, eps, min_half_width=0.6 * lc_self))
            if not geoms:
                return None
            return make_valid(unary_union(geoms))

        def record_quad_buffer_crossings(self_key):
            # From the loser's side, record a refinement disk over each crossing
            # region so the gap fill is pinned to min(lc) rather than jumping to
            # the background size next to the dense strip rows.
            self_plan = strip_plans.get(self_key)
            if self_plan is None:
                return
            self_fp = self_plan['footprint']
            self_pkey = self_plan['priority_key']
            lc_self = self_plan['lc']
            w_self = self_plan['width']
            for key, plan in strip_plans.items():
                if key == self_key or not (plan['priority_key'] < self_pkey):
                    continue
                inter = make_valid(self_fp.intersection(plan['footprint']))
                for part in polygon_parts(inter):
                    if part.is_empty or part.area <= 0:
                        continue
                    minx, miny, maxx, maxy = part.bounds
                    part_radius = 0.5 * math.hypot(maxx - minx, maxy - miny)
                    size = min(lc_self, plan['lc'])
                    radius = part_radius + 0.5 * (w_self + plan['width']) + size
                    self._quad_buffer_crossings.append({
                        'x': part.centroid.x,
                        'y': part.centroid.y,
                        'size': size,
                        'radius': radius,
                    })

        # Add line features to the model, handling barriers and standard lines differently.
        for idx, row in lines_gdf.iterrows():
            is_barrier = row_bool(row, 'is_barrier', False)
            quad_buffer = row_bool(row, 'quad_buffer', False)
            straddle = positive_number(row.get('straddle_width'))
            lc = feature_lc(row)
            
            use_structured_buffer = quad_buffer
            use_virtual_straddle = not use_structured_buffer and (is_barrier or straddle is not None)

            embedded = is_embedded(row)
            
            if use_structured_buffer:
                created = create_line_structured_buffer(row)
                if created:
                    structured_buffer_specs[('line', int(idx))] = {
                        'lc': lc,
                        'thickness': quad_buffer_thickness(row),
                        'kind': 'line',
                        'strips': [info for _, info in created],
                        'n_surfaces_created': len(created),
                    }
                elif self.verbosity > 0:
                    logger.warning(f"Warning: Structured buffer requested for line {idx}, but no buffer surface was created.")

            elif use_virtual_straddle:
                # For barriers or "straddle" lines, we don't add the line itself.
                # Instead, we place pairs of points along the line's path. These
                # points will become nodes in the triangular mesh, forcing the
                # subsequent Voronoi cell edges to align with the original line.
                line = row.geometry
                length = line.length
                num_segments = int(max(1, np.ceil(length / lc)))
                distances = np.linspace(0, length, num_segments + 1)
                
                if straddle:
                    epsilon = straddle / 2.0
                else:
                    epsilon = lc * 0.20
                
                # Tangent probe proportional to line length so the offsets
                # work for any CRS units and for lines shorter than the old
                # fixed 0.01 step.
                probe = max(length * 1e-4, 1e-12)
                for d in distances:
                    p = line.interpolate(d)
                    dx, dy = _unit_tangent(line, d, probe)
                    nx, ny = -dy, dx
                    
                    # Create two points, offset from the original line by the normal.
                    lx, ly = p.x + nx*epsilon, p.y + ny*epsilon
                    lt = gmsh.model.occ.addPoint(lx, ly, 0)
                    k_l = to_key(0, lt)
                    if embedded:#TODO probably this always true for barriers
                        input_tag_info[k_l] = {'type': 'point', 'id': idx}
                        embedded_point_tags.append(k_l)
                    else:
                        nonembedded_point_tags.setdefault(int(idx), []).append(k_l)
                    
                    rx, ry = p.x - nx*epsilon, p.y - ny*epsilon
                    rt = gmsh.model.occ.addPoint(rx, ry, 0)
                    k_r = to_key(0, rt)
                    if embedded:
                        input_tag_info[k_r] = {'type': 'point', 'id': idx}
                        embedded_point_tags.append(k_r)
                    else:
                        nonembedded_point_tags.setdefault(int(idx), []).append(k_r)

            else:
                # This is a standard line feature that will act as a constraint
                # in the mesh, but not a hard barrier.
                geom = row.geometry
                
                # Trim the line against the barrier zone to avoid intersections.
                if barrier_zone:
                    if geom.intersects(barrier_zone):
                        try:
                            original_len = geom.length
                            geom = geom.difference(barrier_zone)
                            
                            if self.verbosity > 1:
                                logger.info(f"  Line {idx} trimmed by barrier (Len: {original_len:.2f} -> {geom.length:.2f})")
                                
                        except Exception as e:
                            logger.warning(f"Warning: Failed to trim line {idx}: {e}")
                
                if geom.is_empty:
                    continue
                
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

                    coords = self._sanitize_coords(list(part.coords), min_points=2)
                    if len(coords) < 2:
                        if self.verbosity > 0:
                            logger.warning(f"Warning: Skipping degenerate line part for feature {idx} after coordinate cleanup.")
                        continue

                    # Add each segment of the line to Gmsh.
                    pt_tags = [gmsh.model.occ.addPoint(x, y, 0) for x, y in coords]
                    created_segments = 0
                    for i in range(len(pt_tags) - 1):
                        try:
                            line_tag = gmsh.model.occ.addLine(pt_tags[i], pt_tags[i+1])
                        except Exception as e:
                            logger.warning(
                                f"Warning: Skipping invalid line segment {i} for feature {idx} "
                                f"between {coords[i]} and {coords[i+1]}: {e}"
                            )
                            continue

                        key = to_key(1, line_tag)
                        created_segments += 1
                        if embedded:
                            embedded_line_tags.append(key)
                            input_tag_info[key] = {'type': 'line', 'id': idx}
                        else:
                            nonembedded_line_tags.setdefault(int(idx), []).append(key)

                    if created_segments == 0 and self.verbosity > 0:
                        logger.warning(f"Warning: No valid line segments were created for feature {idx}.")

        def push_ring_vertices_off_strips(poly):
            """Move polygon ring vertices out of structured strip interiors.

            A ring vertex strictly inside a strip (e.g. a densified midpoint
            landing on the buffered feature line) subdivides the strip's end
            caps during fragmentation and injects a node into the protected
            corridor, breaking the transfinite structure. Project such
            vertices onto the strip boundary instead — a move of at most half
            the strip width, collinear when the ring crosses the strip
            straight.
            """
            if not line_strip_polygons:
                return poly
            moved = 0

            def adjust(coords):
                nonlocal moved
                out = []
                for x, y in coords:
                    point = Point(x, y)
                    for strip in line_strip_polygons:
                        if strip.contains(point):
                            boundary = strip.boundary
                            point = boundary.interpolate(boundary.project(point))
                            moved += 1
                            break
                    out.append((point.x, point.y))
                return out

            exterior = adjust(list(poly.exterior.coords))
            interiors = [adjust(list(ring.coords)) for ring in poly.interiors]
            if not moved:
                return poly
            adjusted = Polygon(exterior, interiors)
            if not adjusted.is_valid:
                adjusted = make_valid(adjusted)
            if adjusted.geom_type != 'Polygon' or adjusted.is_empty:
                return poly
            if self.verbosity > 0:
                logger.info(f"Moved {moved} zone-ring vertex(es) off structured buffer strips.")
            return adjusted

        # Add polygon features to the model.
        if not polygons_gdf.empty:
            logger.info(f"Adding {len(polygons_gdf)} polygons to Gmsh...")
            # First pass: create the quad-buffer band surfaces and collect their
            # footprints. The band hugs the full feature boundary, so it is
            # created once per feature rather than once per MultiPolygon part.
            polygon_band_geoms = []
            for idx, row in polygons_gdf.iterrows():
                if not (is_embedded(row) and row_bool(row, 'quad_buffer', False)):
                    continue
                created, band_geom = create_polygon_structured_buffer(row)
                if created:
                    structured_buffer_specs[('poly', int(idx))] = {
                        'lc': feature_lc(row),
                        'thickness': quad_buffer_thickness(row),
                        'kind': 'polygon',
                        'strips': [],
                        'n_surfaces_created': len(created),
                    }
                    polygon_band_geoms.append(band_geom)
                elif self.verbosity > 0:
                    logger.warning(f"Warning: Structured buffer requested for polygon {idx}, but no buffer surface was created.")
            buffer_footprints = polygon_band_geoms + line_strip_polygons
            buffer_footprints_union = (
                make_valid(unary_union(buffer_footprints)) if buffer_footprints else None
            )

            for idx, row in polygons_gdf.iterrows():
                embedded = is_embedded(row)
                geom = row['geometry']
                if geom.geom_type not in ('Polygon', 'MultiPolygon'):
                    continue
                # Mesh every embedded polygon minus the band/strip footprints,
                # so the buffer surfaces tile the plane with their neighbours
                # exactly (shared curves merged by removeAllDuplicates) instead
                # of relying on OCC fragment to cut overlapping faces — which
                # silently refuses in some trimmed-crossing configurations and
                # leaves double-meshed regions. It also keeps a buffered zone's
                # outline out of the mesh entirely: overlap resolution makes
                # neighbours share that outline (e.g. the domain piece has a
                # hole there), so subtracting only from the buffered zone itself
                # would still pin mesh nodes onto it. Zone assignment uses the
                # original polygons, so zone extents are unchanged.
                if (
                    embedded
                    and buffer_footprints_union is not None
                    and geom.intersects(buffer_footprints_union)
                ):
                    geom = make_valid(geom.difference(buffer_footprints_union))
                polys = polygon_parts(geom)

                for poly in polys:
                    if poly.is_empty:
                        continue

                    if not embedded:
                        # Defer field-only polygon creation until after
                        # fragmentation/dedup/healing. If these overlapping
                        # surfaces exist during global OCC cleanup they can cut
                        # or renumber embedded domain surfaces, which violates
                        # embed=False semantics.
                        pending_nonembedded_polys.append((int(idx), poly))
                        continue

                    poly = push_ring_vertices_off_strips(poly)
                    s_tag, boundary_curve_tags = create_polygon_surface(poly)
                    if s_tag is None:
                        logger.warning(f"Warning: Skipping degenerate polygon {idx}")
                        continue

                    key = to_key(2, s_tag)
                    input_tag_info[key] = {'type': 'surface', 'id': idx}
                    embedded_surface_tags.append(key)
        #call the gui before fragmentation for debugging
        if self.verbosity > 1 and launch_gmsh_gui:
            gmsh.model.occ.synchronize()
            gmsh.fltk.run()

        # >>> DIAG: Pre-fragment inventory (summary)
        if self.verbosity >= 2:
            _line_feats = sorted(set(
                input_tag_info.get(to_key(dt[0], dt[1]), {}).get('id', '?')
                for dt in embedded_line_tags
            )) if embedded_line_tags else []
            logger.debug(f"\n[DIAG] Pre-fragment: {len(embedded_surface_tags)} surfs, "
                         f"{len(embedded_line_tags)} lines, {len(embedded_point_tags)} pts "
                         f"| line features: {_line_feats}")
        # <<< DIAG

        # "Fragment" combines all the individual geometries into a single,
        # topologically consistent model. This is where intersections are
        # calculated and new, smaller entities are created at overlaps.
        # Only embedded geometry participates in fragmentation.
        object_tags = embedded_surface_tags + embedded_line_tags + embedded_point_tags
        
        if not object_tags:
            logger.warning("Warning: No geometry to mesh.")
            return {
                'points': nonembedded_point_tags,
                'lines': nonembedded_line_tags,
                'surfaces': nonembedded_surface_tags,
                'straddle_surfs': {},
                'structured_buffer_surfs': {},
                'poly_curves': nonembedded_poly_curve_tags,
            }

        logger.info(f"Fragmenting {len(object_tags)} objects...")
        out_dt, out_map = gmsh.model.occ.fragment(object_tags, [])

        self._dedup_and_remap_fragment_map(out_map, object_tags, input_tag_info)

        self._heal_and_remap_fragment_map(out_map, object_tags, input_tag_info)

        # >>> DIAG: Post-fragment summary
        if self.verbosity >= 2:
            all_surfs_post = gmsh.model.getEntities(2)
            all_lines_post = gmsh.model.getEntities(1)
            all_pts_post   = gmsh.model.getEntities(0)

            # Classify line fragments: boundary vs interior vs orphan
            _n_boundary, _n_interior, _n_orphan, _n_dim0 = 0, 0, 0, 0
            _boundary_feats = set()  # feature names whose lines became boundaries
            for i, input_dimtag in enumerate(object_tags):
                key = to_key(input_dimtag[0], input_dimtag[1])
                info = input_tag_info.get(key, {})
                if info.get('type') != 'line':
                    continue
                res = out_map[i] if i < len(out_map) else [input_dimtag]
                for dt in res:
                    dim_r, tag_r = int(dt[0]), int(dt[1])
                    if dim_r == 0:
                        _n_dim0 += 1
                        continue
                    try:
                        gmsh.model.getBoundingBox(dim_r, tag_r)
                        up, _ = gmsh.model.getAdjacencies(1, tag_r)
                        if len(up) > 0:
                            _n_boundary += 1
                            _boundary_feats.add(info.get('id', '?'))
                        else:
                            _n_interior += 1
                    except Exception:
                        _n_orphan += 1

            # Count auto-embeddings
            _n_auto = 0
            for s in all_surfs_post:
                try:
                    if gmsh.model.mesh.getEmbedded(2, s[1]):
                        _n_auto += 1
                except Exception:
                    logger.debug("getEmbedded failed for surface %d during "
                                 "post-fragment diagnostics.", s[1])

            logger.debug(f"[DIAG] Post-fragment: {len(all_surfs_post)} surfs, "
                         f"{len(all_lines_post)} lines, {len(all_pts_post)} pts")
            logger.debug(f"[DIAG] Line fragments: {_n_interior} interior, "
                         f"{_n_boundary} BOUNDARY, {_n_orphan} orphan, "
                         f"{_n_dim0} became-points | auto-embed surfs: {_n_auto}")
            if _boundary_feats:
                logger.debug(f"[DIAG] *** Lines from these features became BOUNDARIES: "
                             f"{sorted(_boundary_feats)} ***")
        # <<< DIAG

        if pending_nonembedded_polys:
            if self.verbosity > 0:
                logger.info(f"Adding {len(pending_nonembedded_polys)} field-only polygon surface(s)...")
            for idx, poly in pending_nonembedded_polys:
                s_tag, boundary_curve_tags = create_polygon_surface(poly)
                if s_tag is None:
                    if self.verbosity > 0:
                        logger.warning(f"Warning: Skipping degenerate field-only polygon {idx}")
                    continue
                nonembedded_surface_tags.setdefault(int(idx), []).append(to_key(2, s_tag))
                nonembedded_poly_curve_tags.setdefault(int(idx), []).extend(
                    [(1, int(t)) for t in boundary_curve_tags]
                )
            gmsh.model.occ.synchronize()
        
        # After fragmentation, we need to rebuild our map of which original
        # feature corresponds to which new Gmsh tags.
        final_map = {
            'points': dict(nonembedded_point_tags),
            'lines': dict(nonembedded_line_tags),
            'surfaces': dict(nonembedded_surface_tags),
            'straddle_surfs': {},
            'structured_buffer_surfs': {},
            'poly_curves': dict(nonembedded_poly_curve_tags),
        }
        
        logger.info(f"Reconstructing Map (Input Tags: {len(object_tags)}, Out Map Len: {len(out_map)})...")
        
        for i, input_dimtag in enumerate(object_tags):
            if i < len(out_map):
                res_tags = out_map[i]
            else:
                res_tags = [input_dimtag]

            # Look up the original feature ID using the pre-fragmentation tag.
            key = to_key(input_dimtag[0], input_dimtag[1])
            
            if key in input_tag_info:
                info = input_tag_info[key]
                kind = info['type']
                # Structured-buffer ids are ('line'|'poly', idx) tuples so line
                # and polygon features with the same index cannot collide.
                feat_id = info['id'] if isinstance(info['id'], tuple) else int(info['id'])
                
                if kind == 'point':
                    if feat_id not in final_map['points']:
                        final_map['points'][feat_id] = []
                    final_map['points'][feat_id].extend(res_tags)
                    
                elif kind == 'line':
                    if feat_id not in final_map['lines']:
                        final_map['lines'][feat_id] = []
                    final_map['lines'][feat_id].extend(res_tags)
                    
                elif kind == 'surface':
                    if feat_id not in final_map['surfaces']:
                        final_map['surfaces'][feat_id] = []
                    final_map['surfaces'][feat_id].extend(res_tags)
                    
                elif kind == 'straddle_surf':
                    if feat_id not in final_map['straddle_surfs']:
                        final_map['straddle_surfs'][feat_id] = []
                    final_map['straddle_surfs'][feat_id].extend(res_tags)

                elif kind == 'structured_buffer_surf':
                    if feat_id not in final_map['structured_buffer_surfs']:
                        final_map['structured_buffer_surfs'][feat_id] = []
                    final_map['structured_buffer_surfs'][feat_id].extend(res_tags)
            else:
                logger.warning(f"Warning: Tag {key} lost during fragmentation mapping.")

        # DIAG: Final map point summary
        if self.verbosity >= 2:
            _n_pt_feats = len(final_map.get('points', {}))
            _empty_feats = []
            _stale_feats = []
            model_ents = set()
            for dim in range(3):
                for dt in gmsh.model.getEntities(dim):
                    model_ents.add((int(dt[0]), int(dt[1])))
            for fid, dimtags in final_map.get('points', {}).items():
                dim0 = [dt for dt in dimtags if isinstance(dt, (tuple, list)) and int(dt[0]) == 0]
                if not dim0:
                    _empty_feats.append(fid)
                else:
                    for dt in dim0:
                        if (int(dt[0]), int(dt[1])) not in model_ents:
                            _stale_feats.append((fid, int(dt[1])))
            logger.debug(f"[DIAG] Final map: {_n_pt_feats} point features, "
                         f"{len(_empty_feats)} empty, {len(_stale_feats)} with stale tags")
            if _empty_feats:
                logger.debug(f"  [DIAG] Empty point feat_ids: {sorted(_empty_feats)}")
            if _stale_feats:
                logger.debug(f"  [DIAG] Stale point (feat_id, tag): {_stale_feats}")

        # Safety net: OCC's fragment map can omit pieces of an input surface
        # (observed when a buffer strip with boundaries coincident to the
        # densified domain edge splits the domain). An unclaimed 2D entity
        # would silently lose its mesh nodes and field sizing downstream, so
        # re-attach each orphan to the embedded polygon feature containing it.
        claimed_surfaces = set()
        for map_key in ('surfaces', 'straddle_surfs', 'structured_buffer_surfs'):
            for dimtags in final_map.get(map_key, {}).values():
                for dt in dimtags:
                    if isinstance(dt, (tuple, list)) and len(dt) >= 2 and int(dt[0]) == 2:
                        claimed_surfaces.add(int(dt[1]))
        orphan_surfaces = [
            int(tag) for dim, tag in gmsh.model.getEntities(2)
            if int(tag) not in claimed_surfaces
        ]
        if orphan_surfaces and polygons_gdf is not None and not polygons_gdf.empty:
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

        self._apply_structured_buffer_meshing(final_map, structured_buffer_specs)

        return final_map

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
            if n_created and len(surf_tags) > n_created and self.verbosity > 0:
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

        if self.verbosity > 0:
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
        if self.verbosity > 0:
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
        if self.background_lc is None:
            raise ValueError(
                "MeshGenerator.background_lc must be provided. "
                "If you don't want to constrain the mesh, pass a very large value."
            )
        global_max_lc = float(self.background_lc)
        if not math.isfinite(global_max_lc) or global_max_lc <= 0.0:
            raise ValueError(
                "MeshGenerator.background_lc must be a positive finite number. "
                f"Got {self.background_lc!r}."
            )

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
        # - `feature_lc` is part of grouping because Auto* fields compute their
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

                if not row_fields:
                    continue

                # Cache the feature lc for Auto* fields.
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
                # Straddle/barrier lines may have been converted into points.
                elif fid in gmsh_map.get('points', {}):
                    tags_dict['points'].extend(extract_tags(gmsh_map['points'][fid]))
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
            tags_dict['_verbosity'] = self.verbosity
            
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
            gmsh.model.mesh.field.setNumber(ball, "Radius", float(crossing['radius']))
            gmsh.model.mesh.field.setNumber(ball, "XCenter", float(crossing['x']))
            gmsh.model.mesh.field.setNumber(ball, "YCenter", float(crossing['y']))
            gmsh.model.mesh.field.setNumber(ball, "ZCenter", 0.0)
            gmsh.model.mesh.field.setNumber(ball, "VIn", float(crossing['size']))
            gmsh.model.mesh.field.setNumber(ball, "VOut", global_max_lc)
            gmsh.model.mesh.field.setNumber(ball, "Thickness", 3.0 * float(crossing['size']))
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
        if self.verbosity > 0:
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
        if self.verbosity >= 2:
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
                    if idx in gmsh_map.get('points', {}): 
                        for dt in gmsh_map['points'][idx]:
                            if dt[0] == 0:
                                embed_entity(0, dt[1])

        # >>> DIAG: Embed summary
        if self.verbosity >= 2:
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
        self.triangular_quality = None
        self.element_grid = None
        self._initialize_gmsh()
        try:
            logger.info("Transferring Geometry to Gmsh...")
            gmsh_map = self._add_geometry(clean_polys, clean_lines, clean_points, launch_gmsh_gui=launch_gmsh_gui)
            
            # Ensure features are correctly embedded in surfaces before meshing
            self._embed_features(gmsh_map, clean_polys, clean_lines, clean_points)

            # >>> DIAG: Post-embed summary
            if self.verbosity >= 2:
                all_surfs = gmsh.model.getEntities(2)
                _with_emb, _without_emb, _total_emb = 0, 0, 0
                for surf_dt in all_surfs:
                    try:
                        emb = gmsh.model.mesh.getEmbedded(2, surf_dt[1])
                        if emb:
                            _with_emb += 1
                            _total_emb += len(emb)
                        else:
                            _without_emb += 1
                    except Exception:
                        _without_emb += 1
                # Count line boundary vs interior in gmsh_map
                _map_bnd, _map_int, _map_miss = 0, 0, 0
                for feat_id, dimtags in gmsh_map.get('lines', {}).items():
                    for dt in dimtags:
                        if not (isinstance(dt, (tuple, list)) and len(dt) >= 2):
                            continue
                        if int(dt[0]) != 1:
                            continue
                        try:
                            up, _ = gmsh.model.getAdjacencies(1, int(dt[1]))
                            if len(up) > 0:
                                _map_bnd += 1
                            else:
                                _map_int += 1
                        except Exception:
                            _map_miss += 1
                logger.debug(f"[DIAG] Post-embed: {_with_emb}/{len(all_surfs)} surfaces have embeddings "
                             f"({_total_emb} total entities) | "
                             f"{_without_emb} surfaces empty")
                logger.debug(f"[DIAG] Line map: {_map_int} interior, {_map_bnd} boundary, {_map_miss} missing")
            # <<< DIAG
            
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
                if self.verbosity > 0:
                    logger.info(f"Running {self.optimization_cycles} Optimization Cycles (Relocate2D & Laplace2D)...")
                
                for i in range(self.optimization_cycles):
                    if self.verbosity > 1:
                        logger.info(f"  -> Cycle {i+1}/{self.optimization_cycles}")
                    # Moves nodes to improve element shape (compactness).
                    gmsh.model.mesh.optimize("Relocate2D",niter=1)
                    # Smooths the mesh to relax gradients (reduces drift).
                    gmsh.model.mesh.optimize("Laplace2D",niter=1)

            meshed_surface_tags = self._meshed_surface_tags(gmsh_map, clean_polys)
            self.triangular_quality = self._collect_triangular_quality(meshed_surface_tags)
            self.element_grid = self._collect_element_grid(clean_polys, meshed_surface_tags)
            
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
                        elif int(fid) in gmsh_map.get('points', {}):
                            for dimtag in gmsh_map['points'][int(fid)]:
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
            logger.info(f"Mesh Generation Failed: {e}")
            self._finalize_gmsh()
            raise e
