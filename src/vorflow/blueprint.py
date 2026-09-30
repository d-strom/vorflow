import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Polygon, LineString, Point, box, MultiPolygon
from shapely.ops import unary_union, snap, linemerge
from shapely.validation import make_valid
from .fields import ThresholdField, ExponentialField, AutoLinearField, AutoExponentialField, ConstantField
from shapely.strtree import STRtree

# Constants for geometry simplification and reporting
SIGNIFICANT_REDUCTION_PCT = 1.0
DEFAULT_CONNECTIVITY_TOLERANCE = 1e-3

def _coerce_connectivity_tolerance(value, parameter_name="connectivity_tolerance"):
    if isinstance(value, bool):
        raise ValueError(
            f"{parameter_name} must be a non-negative number. Boolean values are not supported."
        )
    if not isinstance(value, (int, float)):
        raise TypeError(
            f"{parameter_name} must be a non-negative number. Got {type(value).__name__}."
        )
    if value < 0:
        raise ValueError(f"{parameter_name} must be non-negative. Got {value}.")
    return float(value)

class ConceptualMesh:
    def __init__(self, crs="EPSG:4326", connectivity_tolerance=DEFAULT_CONNECTIVITY_TOLERANCE):
        """
        Initializes the conceptual model, which holds raw geometric inputs.

        This class acts as a staging area for geometric features (polygons, lines,
        points) before they are processed into a clean, non-overlapping set of
        inputs for the mesh generator.

        Args:
            crs: The coordinate reference system for the project (e.g., "EPSG:4326").
            connectivity_tolerance (float, optional): Default snapping tolerance used
                during topology cleanup in generate(). Larger values make lines and
                points connect more aggressively to nearby geometry.
        """
        self.crs = crs
        self.connectivity_tolerance = _coerce_connectivity_tolerance(connectivity_tolerance)
        # Store raw geometric inputs before processing.
        self.raw_polygons = [] 
        self.raw_lines = []
        self.raw_points = []
        
        # Geometries after cleaning, snapping, and processing.
        self.domain_boundary = None
        self.clean_polygons = gpd.GeoDataFrame()
        self.clean_lines = gpd.GeoDataFrame()
        self.clean_points = gpd.GeoDataFrame()

    def add_polygon(
        self,
        geometry,
        zone_id,
        resolution=None,
        z_order=0,
        dist_min=None,
        dist_max=None,
        densify=None,
        fields=None,
        embed=True,
        simplify_tolerance=None,
    ):
        """
        Adds a polygon feature, such as a model boundary or a refinement zone.

        Args:
            geometry (shapely.Polygon): The polygon geometry.
            zone_id (int or str): A unique identifier for the zone.
            resolution (float, optional): Target mesh size within this polygon. If None,
                the background mesh size will be used.
            z_order (int): Stacking order for resolving overlaps. Higher values are
                processed first and will "cut" into lower-order polygons.
            dist_min (float, optional): Distance from the polygon boundary where the mesh
                size is held constant at the boundary's resolution.
            dist_max (float, optional): Distance from the polygon boundary over which the mesh
                transitions to the background resolution.
            densify (float|bool|None, optional): Controls polygon boundary densification:
                - If False, disables densification.
                - If True, densifies using `resolution` (lc). Requires `resolution` to be set.
                - If a float, densifies so no boundary segment is longer than this value.
                Raises ValueError if non-positive when specified as a float.
            simplify_tolerance (float|int|None, optional): If a number > 0, applies Douglas-Peucker
                simplification with this tolerance. If None or 0, no simplification is applied.
                Raises ValueError if negative. Boolean values are not supported.
            fields (list, optional): List of MeshField objects.
            embed (bool): If True, the polygon is embedded in the mesh. If False, it is used only for fields.
        """
        if not geometry.is_valid:
            geometry = make_valid(geometry)

        if isinstance(simplify_tolerance, bool):
            raise ValueError(
                "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                "Boolean values are not supported."
            )
        if isinstance(simplify_tolerance, (int, float)) and simplify_tolerance < 0:
            raise ValueError(f"simplify_tolerance must be non-negative. Got {simplify_tolerance}.")

        if isinstance(densify, (int, float)) and not isinstance(densify, bool) and densify <= 0:
            raise ValueError(f"densify must be positive when specified as a float. Got {densify}.")
        if densify is True and (resolution is None or resolution <= 0):
            raise ValueError("densify=True for polygons requires a positive `resolution` (lc).")

        if resolution is not None and resolution <= 0:
            raise ValueError(f"resolution must be positive (or None). Got {resolution}.")

        if dist_max is not None and dist_max < 0:
            raise ValueError(f"dist_max must be non-negative (or None). Got {dist_max}.")
    
        if dist_min is not None and dist_min < 0:
            raise ValueError(f"dist_min must be non-negative (or None). Got {dist_min}.")

        if fields is None:
            fields = []

        self.raw_polygons.append(
            {
                "geometry": geometry,
                "zone_id": zone_id,
                "lc": resolution,
                "z_order": z_order,
                "dist_min": dist_min,
                "dist_max": dist_max,
                "densify": densify,
                "simplify_tolerance": simplify_tolerance,
                'fields': fields,
                'embed': embed
            }
        )

    def add_line(self, geometry, line_id, resolution, snap_to_polygons=True, is_barrier=False,                 
                  dist_min=None, dist_max=None, straddle_width=None, fields=None, embed=True, densify=True, simplify_tolerance=None):
        """
        Adds a line feature, such as a river, fault, or other linear boundary.

        Args:
            geometry (shapely.LineString): The line geometry.
            line_id (str): A unique identifier for the line.
            resolution (float): Target mesh size along the line.
            snap_to_polygons (bool): If True, the line's endpoints will be snapped to
                nearby polygon boundaries to ensure connectivity.
            is_barrier (bool): If True, the line is treated as a flow barrier. The mesh
                will be constructed to prevent cell faces from crossing it.
            dist_min (float, optional): Distance from the line where the mesh size is
                held constant at the line's resolution.
            dist_max (float, optional): Distance from the line over which the mesh
                transitions to the background resolution.
            straddle_width (float, optional): If set, forces Voronoi cell edges to align
                perfectly with the line by creating a "virtual straddle" of mesh nodes.
            fields (list, optional): List of MeshField objects.
            embed (bool): If True, the line is embedded in the mesh. If False, it is used only for fields.
            densify (float or bool, optional): Controls line densification:
                - If False, disables densification.
                - If True, densifies the line using the `resolution` value.
                - If a float, densifies the line so that no segment is longer than this value.
                Raises ValueError if negative or zero.
            simplify_tolerance (float|int|None, optional): If a number > 0, simplifies the line with
                this tolerance using Douglas-Peucker algorithm. If None or 0, no simplification is applied.
                Raises ValueError if negative. Boolean values are not supported.
        """
        if not geometry.is_valid:
            geometry = make_valid(geometry)

        if isinstance(simplify_tolerance, bool):
            raise ValueError(
                "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                "Boolean values are not supported."
            )
        if isinstance(simplify_tolerance, (int, float)) and simplify_tolerance < 0:
            raise ValueError(f"simplify_tolerance must be non-negative. Got {simplify_tolerance}.")
        
        if isinstance(densify, (int, float)) and not isinstance(densify, bool) and densify <= 0:
            raise ValueError(f"densify must be positive when specified as a float. Got {densify}.")

        if fields is None:
            fields = []
        
        self.raw_lines.append({
            'geometry': geometry,
            'line_id': line_id,
            'lc': resolution,
            'is_barrier': is_barrier,
            'dist_min': dist_min,
            'dist_max': dist_max,
            'straddle_width': straddle_width,
            'fields': fields,
            'embed': embed,
            'densify': densify,
            'simplify_tolerance': simplify_tolerance
        })

    def add_point(self, geometry, point_id, resolution, dist_min=None, dist_max=None, fields=None, embed=True, simplify_tolerance=None):
        """
        Adds a point feature, such as a well or an observation point.

        Args:
            geometry (shapely.Point): The point geometry.
            point_id (str): A unique identifier for the point.
            resolution (float): Target mesh size at the point.
            dist_min (float, optional): Distance from the point where the mesh size is
                held constant at the point's resolution.
            dist_max (float, optional): Distance from the point over which the mesh
                transitions to the background resolution.
            simplify_tolerance (float|int|None, optional): If a number > 0, merges points that are closer
                than this tolerance. If None or 0, no merging is applied. Raises ValueError if negative.
                Boolean values are not supported.
        """
        if isinstance(simplify_tolerance, bool):
            raise ValueError(
                "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                "Boolean values are not supported."
            )
        if isinstance(simplify_tolerance, (int, float)) and simplify_tolerance < 0:
            raise ValueError(f"simplify_tolerance must be non-negative. Got {simplify_tolerance}.")

        if fields is None:
            fields = []
        self.raw_points.append({
            'geometry': geometry,
            'point_id': point_id,
            'lc': resolution,
            'dist_min': dist_min,
            'dist_max': dist_max,
            'fields': fields,
            'embed': embed,
            'simplify_tolerance': simplify_tolerance
        })
    def _apply_simplification(self):
        """
        Applies geometry simplification to raw polygons, lines, and points
        based on their specified tolerances to reduce geometric complexity.

        For polygons and lines the Douglas-Peucker algorithm is used.
        For points, this method performs deduplication: points that are within
        a specified tolerance of each other are merged, and only the point with the
        finest (smallest) resolution is kept.
        """
        # Simplify Polygons
        for i, poly_data in enumerate(self.raw_polygons):
            tol = poly_data.get('simplify_tolerance')
            if isinstance(tol, bool):
                raise ValueError(
                    "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                    "Boolean values are not supported."
                )

            if tol is not None and tol > 0:
                org_area = poly_data['geometry'].area
                simplified_geom = poly_data['geometry'].simplify(tol, preserve_topology=True)
                self.raw_polygons[i]['geometry'] = simplified_geom
                new_area = simplified_geom.area
                if new_area < org_area and org_area > 0:
                    reduction_pct = 100 * (org_area - new_area) / org_area
                    if reduction_pct > SIGNIFICANT_REDUCTION_PCT:
                        print(
                            f"Simplified polygon (zone_id={poly_data['zone_id']}) "
                            f"reduced area by {reduction_pct:.2f}% using tolerance {tol}."
                        )

        # Simplify Lines
        for i, line_data in enumerate(self.raw_lines):
            tol = line_data.get('simplify_tolerance')
            if isinstance(tol, bool):
                raise ValueError(
                    "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                    "Boolean values are not supported."
                )

            if tol is not None and tol > 0:
                org_length = line_data['geometry'].length
                simplified_geom = line_data['geometry'].simplify(tol, preserve_topology=True)
                self.raw_lines[i]['geometry'] = simplified_geom
                new_length = simplified_geom.length
                if new_length < org_length and org_length > 0:
                    reduction_pct = 100 * (org_length - new_length) / org_length
                    if reduction_pct > SIGNIFICANT_REDUCTION_PCT:
                        print(
                            f"Simplified line (line_id={line_data['line_id']}) "
                            f"reduced length by {reduction_pct:.2f}% using tolerance {tol}."
                        )

        # Merge points that are very close to each other (deduplication)
        if self.raw_points:
            # Let's sort by resolution first
            sorted_points = sorted(
                self.raw_points,
                key=lambda x: x['lc'] if x['lc'] is not None else float('inf')
            )
            final_points = []
            geoms = [p['geometry'] for p in sorted_points]
            tree = STRtree(geoms)
            kept_indices = set()

            for i, point_data in enumerate(sorted_points):
                current_geom = point_data['geometry']
                tol = point_data.get('simplify_tolerance')

                if isinstance(tol, bool):
                    raise ValueError(
                        "simplify_tolerance must be a non-negative number (or None/0 to disable). "
                        "Boolean values are not supported."
                    )

                # None or <=0 => no merging for this point (keep as-is)
                if tol is None or tol <= 0:
                    final_points.append(point_data)
                    kept_indices.add(i)
                    continue

                is_merged = False

                # Query tree for potential neighbors
                # tree.query returns indices of geometries that intersect the buffer
                search_area = current_geom.buffer(tol)
                candidate_indices = tree.query(search_area)

                for candidate_idx in candidate_indices:
                    if candidate_idx in kept_indices:
                        if geoms[candidate_idx].distance(current_geom) < tol:
                            is_merged = True
                            break

                if not is_merged:
                    final_points.append(point_data)
                    kept_indices.add(i)

            if len(self.raw_points) != len(final_points):
                print(
                    f"Simplification merged {len(self.raw_points) - len(final_points)} "
                    f"points out of {len(self.raw_points)}"
                )
            self.raw_points = final_points

    def _resolve_overlaps(self):
        """
        Processes polygons based on their `z_order` to create a flat,
        non-overlapping planar partition. Higher `z_order` polygons "cookie-cut"
        lower ones.
        """
        # Sort polygons by priority, with the highest z_order processed first.
        if not self.raw_polygons:
            # If this is empty, just create an empty GeoDataFrame.
            self.clean_polygons = gpd.GeoDataFrame(
                columns=[
                    "geometry",
                    "zone_id",
                    "lc",
                    "z_order",
                    "dist_min",
                    "dist_max",
                    "densify",
                    "simplify_tolerance",
                    "fields",
                    "embed",
                ],
                crs=self.crs,
            )
            return
        df = pd.DataFrame(self.raw_polygons)
        df = df.sort_values(by='z_order', ascending=False)
        
        processed_geoms = []
        occupied_space = None # Tracks the union of all higher-priority polygons.
        
        final_features = []

        for idx, row in df.iterrows():
            current_geo = row['geometry']
            
            if occupied_space is None:
                # The first (highest priority) polygon is added unmodified.
                final_geo = current_geo
                occupied_space = current_geo
            else:
                # Subtract the already-occupied space from the current polygon.
                try:
                    final_geo = current_geo.difference(occupied_space)
                except Exception as e:
                    # If the standard difference fails, try again with valid geometries.
                    current_geo = make_valid(current_geo)
                    occupied_space = make_valid(occupied_space)
                    final_geo = current_geo.difference(occupied_space)

                # Add the current polygon's footprint to the occupied space.
                occupied_space = unary_union([occupied_space, current_geo])
            
            # Skip if the polygon was completely covered by higher-priority ones.
            if final_geo.is_empty:
                continue
                
            # Ensure the resulting geometry is valid before exploding.
            final_geo = make_valid(final_geo)

            # If the difference operation resulted in a MultiPolygon, explode it into
            # individual Polygons, each inheriting the parent's attributes.
            if final_geo.geom_type == 'MultiPolygon':
                for part in final_geo.geoms:
                    feat = row.copy()
                    feat['geometry'] = make_valid(part) # Ensure each part is valid
                    final_features.append(feat)
            else:
                feat = row.copy()
                feat['geometry'] = final_geo
                final_features.append(feat)

        self.clean_polygons = gpd.GeoDataFrame(final_features, crs=self.crs)

    def _enforce_connectivity(self, connectivity_tolerance=None):
        """
        Snaps features together to ensure they are topologically connected before
        being passed to the mesher. This is crucial for Gmsh to correctly

        interpret shared boundaries.
        """
        if connectivity_tolerance is None:
            tolerance = self.connectivity_tolerance
        else:
            tolerance = _coerce_connectivity_tolerance(connectivity_tolerance)

        # 1. Collect all polygon boundaries into a single geometry.
        # We snap to the linear boundaries, not the polygon areas.
        if not self.clean_polygons.empty:
            poly_boundaries = unary_union(self.clean_polygons.geometry.boundary)
        else:
            poly_boundaries = None

        # 2. Snap lines to polygon boundaries.
        # This ensures that features like rivers connect precisely to zone edges.
        if self.raw_lines and poly_boundaries is not None and not poly_boundaries.is_empty:
            print(f"Snapping {len(self.raw_lines)} lines to polygon boundaries (tol={tolerance})...")
            for i, line_data in enumerate(self.raw_lines):
                original_line = line_data['geometry']
                snapped_line = snap(original_line, poly_boundaries, tolerance)
                self.raw_lines[i]['geometry'] = snapped_line

        # 3. Snap points to all other geometries (lines and polygon boundaries).
        # This ensures points like wells are located exactly on a feature.
        if self.raw_points:
            geoms_to_snap_to = []
            if poly_boundaries is not None and not poly_boundaries.is_empty:
                geoms_to_snap_to.append(poly_boundaries)
            
            if self.raw_lines:
                # Use the (potentially modified) snapped lines for snapping points.
                lines_union = unary_union([d['geometry'] for d in self.raw_lines])
                geoms_to_snap_to.append(lines_union)
            
            if geoms_to_snap_to:
                reference_geom = unary_union(geoms_to_snap_to)
                
                print(f"Snapping {len(self.raw_points)} points to geometry (tol={tolerance})...")
                for i, point_data in enumerate(self.raw_points):
                    original_point = point_data['geometry']
                    snapped_point = snap(original_point, reference_geom, tolerance)
                    self.raw_points[i]['geometry'] = snapped_point


    def _clip_features_to_domain(self):
        """Remove or trim line/point features that remain outside the meshing domain."""
        if self.clean_polygons.empty:
            return

        domain_union = unary_union(self.clean_polygons.geometry)
        if domain_union.is_empty:
            return
        domain_union = make_valid(domain_union)

        clipped_lines = []
        for line_data in self.raw_lines:
            geom = line_data.get("geometry")
            if geom is None or geom.is_empty:
                continue
            try:
                clipped = geom.intersection(domain_union)
            except Exception:
                clipped = make_valid(geom).intersection(domain_union)

            if clipped.is_empty:
                continue

            line_parts = []
            if clipped.geom_type in ("LineString", "MultiLineString"):
                line_parts = [clipped] if clipped.geom_type == "LineString" else list(clipped.geoms)
            elif clipped.geom_type == "GeometryCollection":
                line_parts = [
                    part for part in clipped.geoms
                    if part.geom_type in ("LineString", "MultiLineString") and not part.is_empty
                ]

            for part in line_parts:
                if part.geom_type == "MultiLineString":
                    for subpart in part.geoms:
                        if subpart.length > 0:
                            feat = line_data.copy()
                            feat["geometry"] = subpart
                            clipped_lines.append(feat)
                elif part.length > 0:
                    feat = line_data.copy()
                    feat["geometry"] = part
                    clipped_lines.append(feat)

        removed_lines = len(self.raw_lines) - len(clipped_lines)
        if removed_lines > 0:
            print(f"Clipped/removed {removed_lines} line feature(s) outside the domain.")
        self.raw_lines = clipped_lines

        kept_points = []
        for point_data in self.raw_points:
            geom = point_data.get("geometry")
            if geom is None or geom.is_empty:
                continue
            if domain_union.covers(geom):
                kept_points.append(point_data)

        removed_points = len(self.raw_points) - len(kept_points)
        if removed_points > 0:
            print(f"Removed {removed_points} point feature(s) outside the domain.")
        self.raw_points = kept_points


    def generate(self, connectivity_tolerance=None):
        """
        Runs the full preprocessing workflow: resolves polygon overlaps,
        ensures topological connectivity, and prepares clean GeoDataFrames
        for the mesher.

        Args:
            connectivity_tolerance (float, optional): Override for the instance's
                default topology snapping tolerance during this preprocessing run.
        """
        print("Applying optional geometry simplification...")
        self._apply_simplification()

        # --- Embed semantics for polygons ---
        # Polygons with embed=True define the actual meshing domain and therefore
        # participate in the cookie-cutter (overlap resolution) process.
        # Polygons with embed=False are refinement-only (field-only) regions and
        # must NOT affect domain topology.
        embedded_polys = [p for p in self.raw_polygons if bool(p.get("embed", True))]
        field_only_polys = [p for p in self.raw_polygons if not bool(p.get("embed", True))]

        # Only embedded polygons are used to build the domain partition.
        self.raw_polygons = embedded_polys

        print("Resolving polygon overlaps...")
        self._resolve_overlaps()
        
        print("Enforcing strict topology...")
        self._enforce_connectivity(connectivity_tolerance=connectivity_tolerance)

        print("Clipping features to domain...")
        self._clip_features_to_domain()
        
        # Promote the processed raw geometries to final "clean" GeoDataFrames.
        if self.raw_lines:
            self.clean_lines = gpd.GeoDataFrame(self.raw_lines, crs=self.crs)
        else:
            self.clean_lines = gpd.GeoDataFrame(
                columns=[
                    'geometry',
                    'line_id',
                    'lc',
                    'is_barrier',
                    'dist_min',
                    'dist_max',
                    'straddle_width',
                    'fields',
                    'embed',
                    'densify',
                    'simplify_tolerance',
                ],
                crs=self.crs,
            )
   
        # Clean Points
        if self.raw_points:
            self.clean_points = gpd.GeoDataFrame(self.raw_points, crs=self.crs)
        else:
            self.clean_points = gpd.GeoDataFrame(
                columns=['geometry', 'point_id', 'lc', 'dist_min', 'dist_max', 'fields', 'embed', 'simplify_tolerance'],
                crs=self.crs,
            )

        # Clip field-only polygons to the final embedded domain.
        # Field-only polygons should not extend outside the domain, but they also
        # should not cut/modify domain topology.
        if field_only_polys and not self.clean_polygons.empty:
            domain_union = unary_union(self.clean_polygons.geometry)
            domain_union = make_valid(domain_union)

            clipped_features = []
            for poly_data in field_only_polys:
                geom = poly_data.get("geometry")
                if geom is None or geom.is_empty:
                    continue
                geom = make_valid(geom)
                try:
                    clipped = geom.intersection(domain_union)
                except Exception:
                    clipped = make_valid(geom).intersection(make_valid(domain_union))

                if clipped.is_empty:
                    continue

                feat = poly_data.copy()
                feat["geometry"] = make_valid(clipped)
                clipped_features.append(feat)

            if clipped_features:
                field_only_gdf = gpd.GeoDataFrame(clipped_features, crs=self.crs)
                # Align schemas before concat to avoid pandas dtype inference warnings
                # while preserving the canonical polygon columns.
                polygon_columns = [
                    'geometry',
                    'zone_id',
                    'lc',
                    'z_order',
                    'dist_min',
                    'dist_max',
                    'fields',
                    'embed',
                    'densify',
                    'simplify_tolerance',
                ]
                self.clean_polygons = self.clean_polygons.reindex(columns=polygon_columns)
                field_only_gdf = field_only_gdf.reindex(columns=polygon_columns)
                # Drop all-null non-geometry columns only during concat; they are
                # restored immediately after so the public GeoDataFrame shape is unchanged.
                concat_frames = [
                    frame.dropna(axis=1, how='all')
                    for frame in (self.clean_polygons, field_only_gdf)
                ]
                self.clean_polygons = gpd.GeoDataFrame(
                    pd.concat(concat_frames, ignore_index=True).reindex(columns=polygon_columns),
                    crs=self.crs,
                )

        print("Densifying geometry...")
        self._apply_densification()
        
        return self.clean_polygons, self.clean_lines, self.clean_points

    
    def _densify_geometry(self, geometry, resolution):
        """
        Recursively adds vertices to LineStrings and Polygon boundaries to ensure
        that no segment is longer than the specified resolution. This is critical
        for forcing the mesh to respect a desired element size along a feature.
        """
        def densify_line(line, max_segment_length):
            if not isinstance(line, LineString):
                return line
            
            coords = list(line.coords)
            new_coords = [coords[0]]
            
            for i in range(len(coords) - 1):
                p1 = np.array(coords[i])
                p2 = np.array(coords[i+1])
                segment_length = np.linalg.norm(p2 - p1)
                
                if segment_length > max_segment_length:
                    num_segments = int(np.ceil(segment_length / max_segment_length))
                    # Add intermediate points along the segment.
                    for j in range(1, num_segments):
                        t = j / num_segments
                        p_new = p1 + t * (p2 - p1)
                        new_coords.append(tuple(p_new))
                
                # Always add the original endpoint of the segment.
                new_coords.append(coords[i+1])
            
            return LineString(new_coords)

        if geometry.geom_type == 'LineString':
            return densify_line(geometry, resolution)
            
            
        elif geometry.geom_type == 'Polygon':
            # Densify the exterior ring.
            new_exterior = densify_line(geometry.exterior, resolution)
            
            # Densify all interior rings (holes).
            new_interiors = []
            for interior in geometry.interiors:
                new_interiors.append(densify_line(interior, resolution))
                
            return Polygon(new_exterior, new_interiors)
            
        elif geometry.geom_type == 'MultiPolygon':
            parts = [self._densify_geometry(p, resolution) for p in geometry.geoms]
            return MultiPolygon(parts)
            
        return geometry


    def _apply_densification(self):
        """Applies densification to the clean polygon and line features."""
        # Densify polygon boundaries based on `densify`.
        if not self.clean_polygons.empty:

            def get_poly_resolution(row):
                d = row.get("densify")
                if d is False or pd.isna(d):
                    return None
                if d is True:
                    return row.get("lc")
                if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
                    return d
                return None

            def _poly_densify(row):
                res = get_poly_resolution(row)
                return self._densify_geometry(row["geometry"], res) if res is not None else row["geometry"]

            self.clean_polygons["geometry"] = self.clean_polygons.apply(_poly_densify, axis=1)

        # Densify lines based on their target resolution ('lc').
        if not self.clean_lines.empty:
            # Helper to determine the target resolution for a line row
            def get_line_resolution(row):
                d = row.get('densify')
                
                # 1. Explicitly disabled (densify=False)
                if d is False:
                    return None
                
                # 2. Explicit custom resolution (e.g., densify=5.0)
                if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0:
                    return d
                
                # 3. Default behavior (True or None): use the mesh resolution (lc)
                return row.get('lc')
            def _line_densify(row):
                res = get_line_resolution(row)
                return self._densify_geometry(row['geometry'], res) if res is not None else row['geometry']

            self.clean_lines['geometry'] = self.clean_lines.apply(_line_densify, axis=1)
