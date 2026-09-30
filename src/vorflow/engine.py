import gmsh
import sys
import math
import numpy as np
import pandas as pd
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid
from .fields import MeshField, ThresholdField, ExponentialField, AutoLinearField, AutoExponentialField, ConstantField

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

    def _add_geometry(self, polygons_gdf, lines_gdf, points_gdf, launch_gmsh_gui=False):
        """
        Transfers Shapely geometries from GeoDataFrames into the Gmsh model.

        This method adds points, lines, and polygons to Gmsh's internal CAD
        kernel (OCC). It also handles special cases like "straddle" lines and
        pre-processes barrier features before fragmenting all geometries to
        create a consistent topological model.
        """
        input_tag_info = {}

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
        
        def to_key(dim, tag):
            return (int(dim), int(tag))

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
                        print(f"Error adding line {p1}-{p2}: {e}")
                        return None, []

                try:
                    loop_tag = gmsh.model.occ.addCurveLoop(l_tags)
                    return loop_tag, l_tags
                except Exception as e:
                    print(f"Error adding curve loop: {e}")
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
                print(f"Error creating surface: {e}")
                return None, []

            return s_tag, boundary_curve_tags
        
        # Add all point features to the Gmsh model first.
        for idx, row in points_gdf.iterrows():
            tag = gmsh.model.occ.addPoint(row.geometry.x, row.geometry.y, 0)
            key = to_key(0, tag)
            if is_embedded(row):
                input_tag_info[key] = {'type': 'point', 'id': idx}
                embedded_point_tags.append(key)
            else:
                nonembedded_point_tags.setdefault(int(idx), []).append(key)
            
        # Create a buffer zone around barrier lines. This is used to trim back
        # other lines, preventing their endpoints from interfering with the
        # meshing of the barrier features.
        barrier_buffers = []
        for idx, row in lines_gdf.iterrows():
            val = row.get('is_barrier', False)
            is_barrier = (val is True) or (str(val).lower() in ['true', '1', 'yes'])
            straddle = row.get('straddle_width')
            
            if is_barrier: 
                lc = max(row.get('lc', 10.0), 0.001)
                if straddle and straddle > 0:
                    eps = straddle / 2.0
                else:
                    eps = lc * 0.20
                
                # The trim buffer is made slightly larger than the feature's half-width
                # to ensure a clean separation between standard lines and the
                # sensitive node pairs used for straddle barriers.
                trim_eps = eps * 1.20
                
                buf = row.geometry.buffer(trim_eps, cap_style=2)
                barrier_buffers.append(buf)
        
        barrier_zone = None
        if barrier_buffers:
            barrier_zone = unary_union(barrier_buffers)
            barrier_zone = make_valid(barrier_zone)
            if self.verbosity > 0:
                print(f"Constructed Barrier Zone from {len(barrier_buffers)} barriers.")

        # Add line features to the model, handling barriers and standard lines differently.
        for idx, row in lines_gdf.iterrows():
            val = row.get('is_barrier', False)
            is_barrier = (val is True) or (str(val).lower() in ['true', '1', 'yes'])
            straddle = row.get('straddle_width')
            lc = max(row.get('lc', 10.0), 0.001)
            
            use_virtual_straddle = is_barrier or (straddle is not None and straddle > 0)

            embedded = is_embedded(row)
            
            if use_virtual_straddle:
                # For barriers or "straddle" lines, we don't add the line itself.
                # Instead, we place pairs of points along the line's path. These
                # points will become nodes in the triangular mesh, forcing the
                # subsequent Voronoi cell edges to align with the original line.
                line = row.geometry
                length = line.length
                num_segments = int(max(1, np.ceil(length / lc)))
                distances = np.linspace(0, length, num_segments + 1)
                
                if straddle and straddle > 0:
                    epsilon = straddle / 2.0
                else:
                    epsilon = lc * 0.20
                
                for d in distances:
                    p = line.interpolate(d)
                    t_val = d
                    p_near = line.interpolate(min(t_val + 0.01, length))
                    if t_val >= length - 0.001:
                         p_near = line.interpolate(max(t_val - 0.01, 0))
                         dx, dy = p.x - p_near.x, p.y - p_near.y
                    else:
                         dx, dy = p_near.x - p.x, p_near.y - p.y
                    
                    mag = np.sqrt(dx*dx + dy*dy)
                    if mag == 0: mag = 1
                    dx, dy = dx/mag, dy/mag
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
                                print(f"  Line {idx} trimmed by barrier (Len: {original_len:.2f} -> {geom.length:.2f})")
                                
                        except Exception as e:
                            print(f"Warning: Failed to trim line {idx}: {e}")
                
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
                    if part.length < 1e-6: continue

                    coords = self._sanitize_coords(list(part.coords), min_points=2)
                    if len(coords) < 2:
                        if self.verbosity > 0:
                            print(f"Warning: Skipping degenerate line part for feature {idx} after coordinate cleanup.")
                        continue

                    # Add each segment of the line to Gmsh.
                    pt_tags = [gmsh.model.occ.addPoint(x, y, 0) for x, y in coords]
                    created_segments = 0
                    for i in range(len(pt_tags) - 1):
                        try:
                            l = gmsh.model.occ.addLine(pt_tags[i], pt_tags[i+1])
                        except Exception as e:
                            if self.verbosity > 0:
                                print(
                                    f"Warning: Skipping invalid line segment {i} for feature {idx} "
                                    f"between {coords[i]} and {coords[i+1]}: {e}"
                                )
                            continue

                        key = to_key(1, l)
                        created_segments += 1
                        if embedded:
                            embedded_line_tags.append(key)
                            input_tag_info[key] = {'type': 'line', 'id': idx}
                        else:
                            nonembedded_line_tags.setdefault(int(idx), []).append(key)

                    if created_segments == 0 and self.verbosity > 0:
                        print(f"Warning: No valid line segments were created for feature {idx}.")

        # Add polygon features to the model.
        if not polygons_gdf.empty:
            print(f"Adding {len(polygons_gdf)} polygons to Gmsh...")
            for idx, row in polygons_gdf.iterrows():
                embedded = is_embedded(row)
                geom = row['geometry']
                if geom.geom_type == 'Polygon':
                    polys = [geom]
                elif geom.geom_type == 'MultiPolygon':
                    polys = geom.geoms
                else:
                    continue
                
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

                    s_tag, boundary_curve_tags = create_polygon_surface(poly)
                    if s_tag is None:
                        print(f"Warning: Skipping degenerate polygon {idx}")
                        continue

                    key = to_key(2, s_tag)
                    input_tag_info[key] = {'type': 'surface', 'id': idx}
                    embedded_surface_tags.append(key)
        #call the gui before fragmentation for debugging
        if self.verbosity > 1 and launch_gmsh_gui==True:
            gmsh.model.occ.synchronize()
            gmsh.fltk.run()

        # >>> DIAG: Pre-fragment inventory (summary)
        if self.verbosity >= 2:
            _line_feats = sorted(set(
                input_tag_info.get(to_key(dt[0], dt[1]), {}).get('id', '?')
                for dt in embedded_line_tags
            )) if embedded_line_tags else []
            print(f"\n[DIAG] Pre-fragment: {len(embedded_surface_tags)} surfs, "
                  f"{len(embedded_line_tags)} lines, {len(embedded_point_tags)} pts "
                  f"| line features: {_line_feats}")
        # <<< DIAG

        # "Fragment" combines all the individual geometries into a single,
        # topologically consistent model. This is where intersections are
        # calculated and new, smaller entities are created at overlaps.
        # Only embedded geometry participates in fragmentation.
        object_tags = embedded_surface_tags + embedded_line_tags + embedded_point_tags
        
        if not object_tags:
            print("Warning: No geometry to mesh.")
            return {
                'points': nonembedded_point_tags,
                'lines': nonembedded_line_tags,
                'surfaces': nonembedded_surface_tags,
                'straddle_surfs': {},
                'poly_curves': nonembedded_poly_curve_tags,
            }

        print(f"Fragmenting {len(object_tags)} objects...")
        out_dt, out_map = gmsh.model.occ.fragment(object_tags, [])

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
                        pass

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
                            pass

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
                print(f"removeAllDuplicates: remapped {_dup_remapped}, pruned {_dup_pruned} tag(s) from fragment map.")

            # DIAG: Per-feature point tracking after dedup
            if self.verbosity >= 2:
                _pt_feat_status = []
                for i, input_dimtag in enumerate(object_tags):
                    key = to_key(input_dimtag[0], input_dimtag[1])
                    info = input_tag_info.get(key, {})
                    if info.get('type') == 'point':
                        feat_id = info['id']
                        dim0 = [dt for dt in (out_map[i] if i < len(out_map) else [])
                                if int(dt[0]) == 0]
                        alive = [(d, t) for d, t in dim0 if (int(d), int(t)) in occ_alive]
                        _pt_feat_status.append((feat_id, len(dim0), len(alive)))
                _n_empty = sum(1 for _, n, a in _pt_feat_status if a == 0)
                print(f"[DIAG] Post-dedup point features: {len(_pt_feat_status)} total, "
                      f"{_n_empty} with 0 alive tags")
                if _n_empty > 0:
                    for fid, nd, na in _pt_feat_status:
                        if na == 0:
                            print(f"  [DIAG] Point feat_id={fid}: {nd} map entries, 0 alive")

        if self.heal_shapes:
            # Snapshot coordinates of ALL out_map entities before heal so we
            # can remap tags that healShapes renumbers.
            _pre_heal_coords = {}  # (dim, tag) -> (x, y, z) for dim-0 entries
            for i in range(len(out_map)):
                for dt in out_map[i]:
                    d, t = int(dt[0]), int(dt[1])
                    if d == 0 and (d, t) not in _pre_heal_coords:
                        try:
                            bb = gmsh.model.occ.getBoundingBox(0, t)
                            _pre_heal_coords[(d, t)] = (bb[0], bb[1], bb[2])
                        except Exception:
                            pass
                    elif d == 1 and (d, t) not in _pre_heal_coords:
                        try:
                            bb = gmsh.model.occ.getBoundingBox(1, t)
                            _pre_heal_coords[(d, t)] = (bb[0], bb[1], bb[2], bb[3], bb[4], bb[5])
                        except Exception:
                            pass
                    elif d == 2 and (d, t) not in _pre_heal_coords:
                        try:
                            bb = gmsh.model.occ.getBoundingBox(2, t)
                            _pre_heal_coords[(d, t)] = (bb[0], bb[1], bb[2], bb[3], bb[4], bb[5])
                        except Exception:
                            pass

            pre_heal = set()
            for dim in range(3):
                for dt in gmsh.model.occ.getEntities(dim):
                    pre_heal.add((int(dt[0]), int(dt[1])))

            if self.heal_tolerance > 1e-2:
                print(f"WARNING: heal_tolerance={self.heal_tolerance} is large. "
                      f"This may destroy fragment boundaries and lose surfaces/lines. "
                      f"Consider values <= 1e-3.")
            print(f"Healing OCC shapes (tolerance={self.heal_tolerance}, "
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
                        pass

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
                print(f"Heal post-processing: remapped {_heal_remapped}, pruned {_heal_pruned} tag(s) from fragment map.")

            # DIAG: Per-feature point tracking after heal
            if self.verbosity >= 2:
                _pt_feat_heal = []
                for i, input_dimtag in enumerate(object_tags):
                    key = to_key(input_dimtag[0], input_dimtag[1])
                    info = input_tag_info.get(key, {})
                    if info.get('type') == 'point':
                        feat_id = info['id']
                        dim0 = [dt for dt in (out_map[i] if i < len(out_map) else [])
                                if int(dt[0]) == 0]
                        alive = [(d, t) for d, t in dim0
                                 if (int(d), int(t)) in surviving]
                        _pt_feat_heal.append((feat_id, len(dim0), len(alive)))
                _n_empty_h = sum(1 for _, n, a in _pt_feat_heal if a == 0)
                print(f"[DIAG] Post-heal point features: {len(_pt_feat_heal)} total, "
                      f"{_n_empty_h} with 0 alive tags (remapped {_heal_remapped}, pruned {_heal_pruned})")
                if _n_empty_h > 0:
                    for fid, nd, na in _pt_feat_heal:
                        if na == 0:
                            print(f"  [DIAG] Point feat_id={fid}: {nd} map entries, 0 alive after heal")
                # Report what heal removed/added
                heal_removed = pre_heal - surviving
                heal_added = surviving - pre_heal
                dim0_removed = [(d, t) for d, t in heal_removed if d == 0]
                dim0_added = [(d, t) for d, t in heal_added if d == 0]
                if dim0_removed or dim0_added:
                    print(f"[DIAG] Heal dim-0 changes: removed {len(dim0_removed)}, added {len(dim0_added)}")
                    if dim0_removed:
                        print(f"  [DIAG] Removed point tags: {sorted(t for _, t in dim0_removed)}")
                    if dim0_added:
                        print(f"  [DIAG] Added point tags: {sorted(t for _, t in dim0_added)}")

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
            _n_auto = sum(
                1 for s in all_surfs_post
                if (lambda: (gmsh.model.mesh.getEmbedded(2, s[1]) or None) is not None)()
            ) if False else 0  # placeholder
            _n_auto = 0
            for s in all_surfs_post:
                try:
                    if gmsh.model.mesh.getEmbedded(2, s[1]):
                        _n_auto += 1
                except Exception:
                    pass

            print(f"[DIAG] Post-fragment: {len(all_surfs_post)} surfs, "
                  f"{len(all_lines_post)} lines, {len(all_pts_post)} pts")
            print(f"[DIAG] Line fragments: {_n_interior} interior, "
                  f"{_n_boundary} BOUNDARY, {_n_orphan} orphan, "
                  f"{_n_dim0} became-points | auto-embed surfs: {_n_auto}")
            if _boundary_feats:
                print(f"[DIAG] *** Lines from these features became BOUNDARIES: "
                      f"{sorted(_boundary_feats)} ***")
        # <<< DIAG

        if pending_nonembedded_polys:
            if self.verbosity > 0:
                print(f"Adding {len(pending_nonembedded_polys)} field-only polygon surface(s)...")
            for idx, poly in pending_nonembedded_polys:
                s_tag, boundary_curve_tags = create_polygon_surface(poly)
                if s_tag is None:
                    if self.verbosity > 0:
                        print(f"Warning: Skipping degenerate field-only polygon {idx}")
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
            'poly_curves': dict(nonembedded_poly_curve_tags),
        }
        
        print(f"Reconstructing Map (Input Tags: {len(object_tags)}, Out Map Len: {len(out_map)})...")
        
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
                feat_id = int(info['id'])
                
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
            else:
                print(f"Warning: Tag {key} lost during fragmentation mapping.")

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
            print(f"[DIAG] Final map: {_n_pt_feats} point features, "
                  f"{len(_empty_feats)} empty, {len(_stale_feats)} with stale tags")
            if _empty_feats:
                print(f"  [DIAG] Empty point feat_ids: {sorted(_empty_feats)}")
            if _stale_feats:
                print(f"  [DIAG] Stale point (feat_id, tag): {_stale_feats}")

        return final_map
    
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
            print(f"--- Setup Fields Debug ---")
            print(f"Polygons GDF: {len(polygons_gdf)} rows")
            print(f"Gmsh Surface Map: {len(gmsh_map.get('surfaces', {}))} entries")
            if not polygons_gdf.empty:
                first_idx = polygons_gdf.index[0]
                print(f"First Poly Index: {first_idx} (Type: {type(first_idx)})")
                if gmsh_map['surfaces']:
                    first_key = list(gmsh_map['surfaces'].keys())[0]
                    print(f"First Map Key: {first_key} (Type: {type(first_key)})")
                    print(f"Match? {first_idx in gmsh_map['surfaces']}")
                else:
                    print("Gmsh Surface Map is EMPTY.")

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

        def _auto_threshold_from_row(row, background_lc):
            """Create a default ThresholdField from dist_min/dist_max + lc.

            This centralizes the legacy behavior that used to live in
            ConceptualMesh.add_*: if a feature specifies dist_min/dist_max it
            implies a distance-based size transition around that feature.

                        Design notes:
                        - We only auto-create this field when at least one of dist_min/dist_max
                            is provided AND the feature has a valid lc.
                        - This is a *distance-based* transition around the feature:
                            - SizeMin = lc (feature resolution)
                            - SizeMax = background_lc (global resolution)
                            - DistMin >= 0.5 * lc (avoid near-zero gradients)
                            - DistMax defaults to ~5 * background_lc (transition length scale)
                            - We enforce (DistMax - DistMin) >= 3 * SizeMax for a gentle gradient.
            """
            dist_min = row.get('dist_min', None)
            dist_max = row.get('dist_max', None)

            if (dist_min is None or (isinstance(dist_min, float) and pd.isna(dist_min))) and (
                dist_max is None or (isinstance(dist_max, float) and pd.isna(dist_max))
            ):
                return None

            # We need both the feature resolution and a global background resolution
            # to define an implicit ThresholdField.
            if background_lc is None or (isinstance(background_lc, float) and pd.isna(background_lc)):
                return None

            feature_lc = row.get('lc', None)
            if feature_lc is None or (isinstance(feature_lc, float) and pd.isna(feature_lc)):
                return None

            feature_lc = float(feature_lc)
            dist_min = None if (dist_min is None or (isinstance(dist_min, float) and pd.isna(dist_min))) else float(dist_min)
            dist_max = None if (dist_max is None or (isinstance(dist_max, float) and pd.isna(dist_max))) else float(dist_max)

            # Default distances if one of the values is omitted.
            # - DistMin: at least one local element size.
            # - DistMax: a broader transition scale based on global mesh size.
            if dist_min is None:
                dist_min = feature_lc
            if dist_max is None:
                dist_max = float(background_lc) * 5.0

            # Force a reasonable relationship with the target resolution.
            # DistMin should not be smaller than the local size.
            dist_min = max(dist_min, feature_lc*0.5)

            # Enforce a minimum transition span relative to SizeMax.
            # This avoids very steep growth that can make the mesher struggle.
            min_span = 3.0 * float(background_lc)
            if (dist_max - dist_min) < min_span:
                dist_max = dist_min + min_span

            # Final safety.
            if dist_max <= dist_min:
                dist_max = dist_min + max(float(background_lc), feature_lc, 1e-3)

            return ThresholdField(size_min=feature_lc, dist_min=dist_min, dist_max=dist_max, size_max=background_lc)
        
        # Configure mesh size fields using MeshField objects attached to features.
        #
        # Data model expectations:
        # - ConceptualMesh stores the user's desired behavior in GeoDataFrame rows.
        # - Fields are created here (engine side) because the engine has the Gmsh
        #   tags and is responsible for mapping features -> CAD entities.
        #
        # How fields can be specified per feature:
        # - `fields`: list[MeshField] (the only supported explicit mechanism)
        # - `dist_min/dist_max` (+ lc): shorthand for an automatic ThresholdField
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
                row_fields = []
                row_fields.extend(_normalize_fields(row.get('fields', None)))

                # 2) Optionally add an implicit ThresholdField based on dist_min/dist_max.
                auto_field = _auto_threshold_from_row(row, global_max_lc)
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

            # Surfaces
            for fid in feature_ids_by_geom.get('surfaces', []):
                if fid in gmsh_map.get('surfaces', {}):
                    surface_tags = extract_tags(gmsh_map['surfaces'][fid])
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
            print("Explicitly embedding features into domain surfaces...")

        def is_embedded(row):
            val = row.get('embed', True)
            if pd.isna(val): return True
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
            print(f"[DIAG] Embed pool: GDF indices={_gdf_idxs}, map keys={_map_keys}, "
                  f"matched={len(_matching)}, domain_surface_tags={sorted(domain_surface_tags)}")
            # Dump bbox of ALL surfaces - shows which surfaces cover which area
            _all_surfs = gmsh.model.getEntities(2)
            for _s in _all_surfs:
                _in_pool = "POOL" if _s[1] in domain_surface_tags else "----"
                try:
                    _sbb = gmsh.model.getBoundingBox(2, _s[1])
                    print(f"[DIAG]   surf {_s[1]:3d} [{_in_pool}] "
                          f"x=[{_sbb[0]:7.1f},{_sbb[3]:7.1f}] "
                          f"y=[{_sbb[1]:7.1f},{_sbb[4]:7.1f}]")
                except Exception:
                    print(f"[DIAG]   surf {_s[1]:3d} [{_in_pool}] bbox FAILED")
            # Which feature id maps to which surface tags?
            for _feat_id, _dts in gmsh_map.get('surfaces', {}).items():
                _stags = [int(dt[1]) for dt in _dts if isinstance(dt, (tuple,list)) and dt[0]==2]
                print(f"[DIAG]   map[surfaces][{_feat_id}] -> tags {_stags}")
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
                pass

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
                    pass
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
            print(f"[DIAG] Embed results: {_elog['ok']} OK, "
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
                print(f"[DIAG] *** {_filt} entities found nearby surfaces but NONE "
                      f"were in domain_surface_tags — likely missing domain surface! ***")
            if _elog['conflict_tags']:
                uniq = sorted(set(_elog['conflict_tags']))
                print(f"[DIAG] *** {len(uniq)} unique line tags had BOUNDARY CONFLICTS "
                      f"(first 10): {uniq[:10]} ***")
            if _elog['fail_tags']:
                print(f"[DIAG] *** Failed embeds: {_elog['fail_tags'][:5]} ***")
            if _elog['boundary_tags']:
                uniq = _elog['boundary_tags'][:10]
                print(f"[DIAG] Boundary line fragments skipped (first 10): {uniq}")
            if _elog['multi_tags']:
                print(f"[DIAG] Multi-surface embed candidates collapsed "
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
        self._initialize_gmsh()
        try:
            print("Transferring Geometry to Gmsh...")
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
                print(f"[DIAG] Post-embed: {_with_emb}/{len(all_surfs)} surfaces have embeddings "
                      f"({_total_emb} total entities) | "
                      f"{_without_emb} surfaces empty")
                print(f"[DIAG] Line map: {_map_int} interior, {_map_bnd} boundary, {_map_miss} missing")
            # <<< DIAG
            
            print("Setting up Resolution Fields...")
            self._setup_fields(gmsh_map, clean_polys, clean_lines, clean_points)
            
            # Set the core meshing algorithm.
            gmsh.option.setNumber("Mesh.Algorithm", self.mesh_algorithm) 
            
            # Set the number of internal smoothing steps.
            gmsh.option.setNumber("Mesh.Smoothing", self.smoothing_steps)

            # Tolerance for the initial Delaunay insertion — helps with
            # "Could not insert point" from near-degenerate geometry.
            gmsh.option.setNumber("Mesh.ToleranceInitialDelaunay", self.tolerance_initial_delaunay)

            print("Generating Triangular Mesh...")
            gmsh.model.mesh.generate(2)
            
            # Run explicit optimization passes after generation for higher quality.
            if self.optimization_cycles > 0:
                if self.verbosity > 0:
                    print(f"Running {self.optimization_cycles} Optimization Cycles (Relocate2D & Laplace2D)...")
                
                for i in range(self.optimization_cycles):
                    if self.verbosity > 1:
                        print(f"  -> Cycle {i+1}/{self.optimization_cycles}")
                    # Moves nodes to improve element shape (compactness).
                    gmsh.model.mesh.optimize("Relocate2D",niter=1)
                    # Smooths the mesh to relax gradients (reduces drift).
                    gmsh.model.mesh.optimize("Laplace2D",niter=1)

            
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

            # 1) Domain surfaces: embedded polygons only
            domain_surface_tags: list[int] = []
            if clean_polys is not None and not clean_polys.empty and 'embed' in clean_polys.columns:
                embedded_poly_ids = [int(i) for i, r in clean_polys.iterrows() if _is_embedded_row(r)]
            elif clean_polys is not None and not clean_polys.empty:
                # Historical behavior: polygons were all embedded.
                embedded_poly_ids = [int(i) for i in clean_polys.index]
            else:
                embedded_poly_ids = []

            for fid in embedded_poly_ids:
                if fid in gmsh_map.get('surfaces', {}):
                    for dimtag in gmsh_map['surfaces'][fid]:
                        if isinstance(dimtag, (tuple, list)) and len(dimtag) >= 2 and int(dimtag[0]) == 2:
                            domain_surface_tags.append(int(dimtag[1]))

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
            print(f"Mesh Generation Failed: {e}")
            self._finalize_gmsh()
            raise e
