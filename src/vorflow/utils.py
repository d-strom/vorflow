from __future__ import annotations

import logging
import warnings

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, Point, Polygon, MultiPolygon


logger = logging.getLogger(__name__)


CONNECTIVITY_COLUMNS = [
    "cell_id_1",
    "cell_id_2",
    "orig_index_1",
    "orig_index_2",
    "node_id_1",
    "node_id_2",
    "center_mode",
    "center_1",
    "center_2",
    "angle",
    "ortho_error",
    "skewness",
    "connector",
    "shared_edge",
]


def _empty_connectivity(crs=None):
    return gpd.GeoDataFrame(
        columns=CONNECTIVITY_COLUMNS,
        geometry="shared_edge",
        crs=crs,
    )


def _longest_line(geom):
    """Return the longest line component from a shared-boundary geometry."""
    if geom.is_empty:
        return None
    if isinstance(geom, LineString):
        return geom if geom.length > 0 else None
    if isinstance(geom, MultiLineString):
        lines = [line for line in geom.geoms if line.length > 0]
        return max(lines, key=lambda line: line.length) if lines else None
    if hasattr(geom, "geoms"):
        lines = []
        for part in geom.geoms:
            line = _longest_line(part)
            if line is not None:
                lines.append(line)
        return max(lines, key=lambda line: line.length) if lines else None
    return None


def _representative_point_on_geometry(geom):
    """Return a point suitable for projection onto a connector line."""
    if geom.is_empty:
        return None
    if isinstance(geom, Point):
        return geom
    if isinstance(geom, LineString):
        return geom.interpolate(0.5, normalized=True)
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            point = _representative_point_on_geometry(part)
            if point is not None:
                return point
    centroid = geom.centroid
    return centroid if isinstance(centroid, Point) and not centroid.is_empty else None


def _center_point(row, center):
    if center == "generator":
        return Point(float(row["x"]), float(row["y"]))
    if center == "centroid":
        return row.geometry.centroid
    raise ValueError("center must be either 'generator' or 'centroid'.")


def build_connectivity(gdf: gpd.GeoDataFrame, center: str = "generator") -> gpd.GeoDataFrame:
    """
    Build a per-face connectivity report for a polygonal grid.

    ``center="generator"`` uses the ``x`` and ``y`` columns and reports the
    mathematical Voronoi-dual connectivity. ``center="centroid"`` uses polygon
    centroids and reports MODFLOW-facing cell-center connectivity for exported
    cells. ``angle`` is the connector-vs-shared-face angle in degrees, with an
    ideal value of 90. ``ortho_error`` is the corresponding orthogonality error,
    with an ideal value of 0. ``skewness`` is the fractional position along the
    connector where the shared face crosses, with an ideal value of 0.5.
    ``cell_id_1`` and ``cell_id_2`` are zero-based row positions in ``gdf`` and
    are therefore unique even when the GeoDataFrame index is not. ``orig_index_1``
    and ``orig_index_2`` preserve the input index for traceability. The active
    geometry is ``shared_edge``; ``connector`` stores the center-to-center line.
    """
    if center not in {"generator", "centroid"}:
        raise ValueError("center must be either 'generator' or 'centroid'.")

    required = {"geometry"}
    if center == "generator":
        required.update({"x", "y"})
    missing = required.difference(gdf.columns)
    if missing:
        raise ValueError(
            f"build_connectivity missing required columns: {sorted(missing)}; "
            f"required columns for center='{center}': {sorted(required)}"
        )

    if gdf.empty:
        return _empty_connectivity(gdf.crs)

    df = gdf.copy()
    df["__cell_id"] = np.arange(len(df), dtype=np.int64)
    df["__orig_index"] = df.index
    df = df.reset_index(drop=True)

    neighbors = gpd.sjoin(df, df, how="inner", predicate="touches")
    pairs = neighbors[neighbors.index < neighbors.index_right].copy()

    records = []
    for left_idx, pair in pairs.iterrows():
        right_idx = int(pair["index_right"])
        left = df.loc[left_idx]
        right = df.loc[right_idx]

        shared_edge = _longest_line(left.geometry.intersection(right.geometry))
        if shared_edge is None:
            continue

        center_1 = _center_point(left, center)
        center_2 = _center_point(right, center)
        if center_1.is_empty or center_2.is_empty:
            continue

        x1, y1 = float(center_1.x), float(center_1.y)
        x2, y2 = float(center_2.x), float(center_2.y)
        gx = x2 - x1
        gy = y2 - y1
        gmag = np.sqrt(gx * gx + gy * gy)
        if gmag == 0:
            continue

        coords = list(shared_edge.coords)
        if len(coords) < 2:
            continue

        ex = coords[-1][0] - coords[0][0]
        ey = coords[-1][1] - coords[0][1]
        emag = np.sqrt(ex * ex + ey * ey)
        if emag == 0:
            continue

        cos_theta = abs(gx * ex + gy * ey) / (gmag * emag)
        cos_theta = min(1.0, max(0.0, cos_theta))
        angle = float(np.degrees(np.arccos(cos_theta)))
        ortho_error = abs(90.0 - angle)

        connector = LineString([(x1, y1), (x2, y2)])
        crossing = connector.intersection(shared_edge)
        point = _representative_point_on_geometry(crossing)
        if point is None:
            point = shared_edge.interpolate(0.5, normalized=True)
        skewness = float(connector.project(point) / connector.length)

        record = {
            "cell_id_1": int(left["__cell_id"]),
            "cell_id_2": int(right["__cell_id"]),
            "orig_index_1": left["__orig_index"],
            "orig_index_2": right["__orig_index"],
            "center_mode": center,
            "center_1": center_1,
            "center_2": center_2,
            "angle": angle,
            "ortho_error": float(ortho_error),
            "skewness": skewness,
            "connector": connector,
            "shared_edge": shared_edge,
        }
        if "node_id" in df.columns:
            record["node_id_1"] = left["node_id"]
            record["node_id_2"] = right["node_id"]
        records.append(record)

    if not records:
        return _empty_connectivity(gdf.crs)

    return gpd.GeoDataFrame(records, geometry="shared_edge", crs=gdf.crs)


def boundary_connectivity_report(
    grid_gdf: gpd.GeoDataFrame,
    domain_geom,
    *,
    center: str = "centroid",
    tolerance: float | None = None,
) -> gpd.GeoDataFrame:
    """
    Connectivity report restricted to pairs touching the domain boundary.

    Runs :func:`build_connectivity` on ``grid_gdf`` and keeps only the rows
    where at least one of the two cells touches the boundary of
    ``domain_geom``. Useful to compare the boundary-cell angle/orthogonality
    distribution between ``boundary_centering="clip"`` and ``"inset_mirror"``
    runs of the tessellator. ``center="centroid"`` is the meaningful mode for
    that comparison: with ``center="generator"`` Voronoi faces are exact
    perpendicular bisectors and always report 90 degrees.

    ``tolerance`` defaults to a domain-bbox-scaled value matching the
    tessellator's boundary-node classification.
    """
    if domain_geom is None or domain_geom.is_empty:
        raise ValueError("boundary_connectivity_report requires a non-empty domain geometry.")
    if tolerance is None:
        minx, miny, maxx, maxy = domain_geom.bounds
        tolerance = max(maxx - minx, maxy - miny, 1.0) * 1e-8
    elif tolerance < 0:
        raise ValueError("tolerance must be non-negative when provided.")

    connectivity = build_connectivity(grid_gdf, center=center)
    if connectivity.empty:
        return connectivity

    boundary = domain_geom.boundary
    distances = grid_gdf.geometry.reset_index(drop=True).distance(boundary)
    boundary_cells = set(np.flatnonzero(distances.to_numpy() <= tolerance))

    mask = connectivity["cell_id_1"].isin(boundary_cells) | connectivity["cell_id_2"].isin(
        boundary_cells
    )
    return connectivity[mask].reset_index(drop=True)


def _validate_connectivity_report(
    connectivity: gpd.GeoDataFrame,
    n_cells: int,
    *,
    require_ortho: bool = False,
    require_skewness: bool = False,
) -> None:
    required = {"cell_id_1", "cell_id_2"}
    if require_ortho:
        required.add("ortho_error")
    if require_skewness:
        required.add("skewness")

    missing = required.difference(connectivity.columns)
    if missing:
        raise ValueError(
            f"connectivity missing required columns: {sorted(missing)}; "
            f"required columns for requested metrics: {sorted(required)}"
        )

    if connectivity.empty:
        return

    cell_ids = pd.concat(
        [connectivity["cell_id_1"], connectivity["cell_id_2"]],
        ignore_index=True,
    )
    numeric_ids = pd.to_numeric(cell_ids, errors="coerce")
    if numeric_ids.isna().any():
        raise ValueError("connectivity cell_id_1/cell_id_2 must be integer row positions.")

    int_ids = numeric_ids.astype(np.int64)
    if not np.array_equal(numeric_ids.to_numpy(), int_ids.to_numpy()):
        raise ValueError("connectivity cell_id_1/cell_id_2 must be integer row positions.")

    if (int_ids < 0).any() or (int_ids >= n_cells).any():
        raise ValueError(
            "connectivity cell_id_1/cell_id_2 values must align to zero-based "
            "row positions in the input GeoDataFrame."
        )


def _max_pair_metric_by_cell(connectivity: gpd.GeoDataFrame, metric: pd.Series, n_cells: int) -> np.ndarray:
    values = np.zeros(n_cells, dtype=float)
    if connectivity.empty:
        return values

    s1 = metric.groupby(connectivity["cell_id_1"]).max()
    s2 = metric.groupby(connectivity["cell_id_2"]).max()
    combined = pd.concat([s1, s2], axis=1).max(axis=1)
    for cell_id, value in combined.items():
        values[int(cell_id)] = float(value)
    return values

def calculate_orthogonality(gdf: gpd.GeoDataFrame) -> pd.Series:
    """
    Calculates the maximum orthogonality error for each cell in a Voronoi grid.

    Orthogonality is a critical measure of grid quality for finite volume methods.
    It is the angle between two vectors:
    1. The vector connecting the generator points of two adjacent cells (G).
    2. The normal vector of their shared edge (N).

    In a perfect Delaunay-Voronoi dual grid, this angle is 0 degrees. This
    function calculates the deviation from this ideal for every internal edge.

    Args:
        gdf (gpd.GeoDataFrame): The Voronoi grid, which must contain 'x' and 'y'
            columns corresponding to the generator point coordinates.

    Returns:
        pd.Series: A series containing the maximum orthogonality error (in degrees)
            for each cell. Cells with no valid neighbors will have an error of 0.
    """
    if 'x' not in gdf.columns or 'y' not in gdf.columns:
        return pd.Series(np.nan, index=gdf.index)

    # Ensure the index is unique for reliable mapping.
    df = gdf.copy()
    if not df.index.is_unique:
        df = df.reset_index(drop=True)
    
    # 1. Identify all neighboring cell pairs using a spatial join.
    # The 'touches' predicate finds all polygons that share a boundary.
    neighbors = gpd.sjoin(df, df, how='inner', predicate='touches')
    
    # Filter out self-matches and duplicates to process each pair only once.
    pairs = neighbors[neighbors.index < neighbors.index_right].copy()
    
    if pairs.empty:
        return pd.Series(0.0, index=gdf.index)

    # 2. Set up vectorized calculations for the generator-to-generator vectors.
    g1_x = df.loc[pairs.index, 'x'].values
    g1_y = df.loc[pairs.index, 'y'].values
    g2_x = df.loc[pairs.index_right, 'x'].values
    g2_y = df.loc[pairs.index_right, 'y'].values
    
    # Vector G (from generator 1 to generator 2).
    Gx = g2_x - g1_x
    Gy = g2_y - g1_y
    
    # 3. Iterate through pairs to compute the normal of the shared edge.
    # This part is not easily vectorized in GeoPandas.
    errors = []
    
    geoms1 = df.loc[pairs.index, 'geometry'].values
    geoms2 = df.loc[pairs.index_right, 'geometry'].values
    
    for i in range(len(pairs)):
        poly1 = geoms1[i]
        poly2 = geoms2[i]
        
        # The intersection of two adjacent polygons is their shared edge.
        inter = poly1.intersection(poly2)
        
        # Skip if the intersection is not a line (e.g., a single point).
        if inter.is_empty or inter.geom_type not in ['LineString', 'MultiLineString']:
            errors.append(np.nan)
            continue
            
        if inter.geom_type == 'MultiLineString':
            # If they touch at multiple places, use the longest shared segment.
            if not inter.geoms:
                errors.append(np.nan)
                continue
            edge = max(inter.geoms, key=lambda x: x.length)
        else:
            edge = inter
            
        # Get the vector for the edge itself.
        coords = list(edge.coords)
        if len(coords) < 2:
            errors.append(np.nan)
            continue
            
        Ex = coords[-1][0] - coords[0][0]
        Ey = coords[-1][1] - coords[0][1]
        
        # The 2D normal vector is (-Ey, Ex).
        Nx, Ny = -Ey, Ex
        
        # Calculate the cosine of the angle between the generator vector (G)
        # and the edge normal vector (N) using the dot product.
        dot = Gx[i]*Nx + Gy[i]*Ny
        mag_g = np.sqrt(Gx[i]**2 + Gy[i]**2)
        mag_n = np.sqrt(Nx**2 + Ny**2)
        
        if mag_g == 0 or mag_n == 0:
            errors.append(np.nan)
            continue
            
        cos_theta = abs(dot) / (mag_g * mag_n)
        
        # Clamp to handle potential floating point inaccuracies.
        cos_theta = min(1.0, max(0.0, cos_theta))
        
        # The orthogonality error is the angle whose cosine we just found.
        angle_rad = np.arccos(cos_theta)
        angle_deg = np.degrees(angle_rad)
        
        errors.append(angle_deg)

    pairs['ortho_error'] = errors
    
    # 4. Aggregate the errors. Each cell's error is the maximum error from
    # all of its edges.
    s1 = pairs['ortho_error'].groupby(pairs.index).max()
    s2 = pairs['ortho_error'].groupby(pairs.index_right).max()
    
    # Combine the errors (since each edge belongs to two cells) and reindex
    # to match the original GeoDataFrame.
    combined = pd.concat([s1, s2], axis=1).max(axis=1)
    final_series = combined.reindex(gdf.index).fillna(0.0)
    
    return final_series

def calculate_mesh_quality(
    gdf: gpd.GeoDataFrame,
    calc_ortho: bool = False,
    calc_skewness: bool = False,
    connectivity: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """
    Calculates a suite of geometric quality metrics for a Voronoi grid.

    Args:
        gdf (gpd.GeoDataFrame): A GeoDataFrame containing the Voronoi cells.
            It is expected to have 'x' and 'y' columns for the generator points.
        calc_ortho (bool): If True, the orthogonality error will be calculated.
            This is a more expensive calculation and is disabled by default.
        calc_skewness (bool): If True, the maximum per-cell skewness error
            ``abs(pair_skewness - 0.5)`` will be calculated. Disabled by default.
        connectivity (GeoDataFrame, optional): A precomputed report from
            ``build_connectivity(gdf, center=...)``. It must include
            ``cell_id_1`` and ``cell_id_2`` as zero-based row positions in
            ``gdf`` plus ``ortho_error`` and/or ``skewness`` for the requested
            metrics. When provided, it is reused for per-cell aggregation.

    Returns:
        gpd.GeoDataFrame: The input GeoDataFrame with added columns for each
            quality metric (e.g., 'area', 'compactness', 'drift_ratio').
    """
    df = gdf.copy()
    
    # 1. Basic geometric properties.
    df['area'] = df.geometry.area
    df['perimeter'] = df.geometry.length
    
    # 2. Compactness (Isoperimetric Quotient): A measure of how "circular" a
    # polygon is. A perfect circle has a compactness of 1.0.
    df['compactness'] = (4 * np.pi * df['area']) / (df['perimeter'] ** 2)
    
    # 3. Convexity (Solidity): The ratio of the cell's area to the area of its
    # convex hull. A perfectly convex polygon has a convexity of 1.0.
    df['convexity'] = df['area'] / df.geometry.convex_hull.area
    
    connectivity_report = None
    if connectivity is not None:
        connectivity_report = connectivity
        _validate_connectivity_report(
            connectivity_report,
            len(df),
            require_ortho=calc_ortho,
            require_skewness=calc_skewness,
        )
    elif calc_skewness and 'x' in df.columns and 'y' in df.columns:
        connectivity_report = build_connectivity(df, center="generator")

    # 4. Generator-based drift metrics (require generator point coordinates).
    if 'x' in df.columns and 'y' in df.columns:
        centroids = df.geometry.centroid
        dx = df['x'] - centroids.x
        dy = df['y'] - centroids.y
        # Distance between the cell's generator and its geometric centroid.
        df['centroid_dist'] = np.sqrt(dx*dx + dy*dy)
        # A non-dimensional measure of the generator/centroid drift.
        df['drift_ratio'] = df['centroid_dist'] / np.sqrt(df['area'])

    if calc_ortho:
        if connectivity_report is not None and 'ortho_error' in connectivity_report.columns:
            df['ortho_error'] = _max_pair_metric_by_cell(
                connectivity_report,
                connectivity_report['ortho_error'],
                len(df),
            )
        elif 'x' in df.columns and 'y' in df.columns:
            df['ortho_error'] = calculate_orthogonality(df)

    if calc_skewness:
        if connectivity_report is None or connectivity_report.empty:
            df['skewness'] = 0.0
        else:
            skew_error = (connectivity_report['skewness'] - 0.5).abs()
            df['skewness'] = _max_pair_metric_by_cell(
                connectivity_report,
                skew_error,
                len(df),
            )
        
    return df

def summarize_quality(gdf: gpd.GeoDataFrame):
    """
    Prints a summary report of mesh quality metrics, separating statistics
    for internal cells versus boundary cells.
    """
    # Ensure the required quality metrics have been calculated.
    if 'compactness' not in gdf.columns:
        gdf = calculate_mesh_quality(gdf, calc_ortho=False)
        
    logger.info("\n--- Mesh Quality Report ---")
    logger.info(f"Total Cells: {len(gdf)}")
    
    # 1. Distinguish between internal and boundary cells.
    # A simple heuristic is that for a boundary cell, its generator point
    # will lie on or very close to the cell's own geometric boundary.
    is_boundary = np.zeros(len(gdf), dtype=bool)
    if 'x' in gdf.columns and 'y' in gdf.columns:
        gens = gpd.GeoSeries(gpd.points_from_xy(gdf.x, gdf.y), index=gdf.index,crs=gdf.crs)
        dists = gdf.geometry.boundary.distance(gens)
        
        # Use a small tolerance relative to the cell size.
        tols = np.sqrt(gdf['area']) * 0.01
        is_boundary = dists < tols

    internal_df = gdf[~is_boundary]
    boundary_df = gdf[is_boundary]
    
    logger.info(f"  - Internal Cells: {len(internal_df)}")
    logger.info(f"  - Boundary Cells: {len(boundary_df)}")

    metrics = ['area', 'compactness', 'convexity']
    if 'drift_ratio' in gdf.columns:
        metrics.append('drift_ratio')
    if 'ortho_error' in gdf.columns:
        metrics.append('ortho_error')
    if 'skewness' in gdf.columns:
        metrics.append('skewness')
        
    logger.info("\n-- Internal Cells Statistics --")
    if not internal_df.empty:
        stats_in = internal_df[metrics].describe(percentiles=[0.05, 0.5, 0.95])
        logger.info(stats_in.T[['min', '5%', '50%', '95%', 'max']].to_string())
    else:
        logger.info("No internal cells.")

    logger.info("\n-- Boundary Cells Statistics --")
    if not boundary_df.empty:
        stats_bnd = boundary_df[metrics].describe(percentiles=[0.05, 0.5, 0.95])
        logger.info(stats_bnd.T[['min', '5%', '50%', '95%', 'max']].to_string())
    else:
        logger.info("No boundary cells.")
    
    # Provide some diagnostic warnings based on common quality thresholds.
    logger.info("\n--- Diagnostics ---")
    
    # Internal cells should be high quality.
    if not internal_df.empty:
        slivers = len(internal_df[internal_df['compactness'] < 0.6])
        if slivers > 0:
            logger.info(f"[WARNING] {slivers} INTERNAL cells have low compactness (< 0.6).")
            
        if 'drift_ratio' in internal_df.columns:
            high_drift = len(internal_df[internal_df['drift_ratio'] > 0.25])
            if high_drift > 0:
                logger.info(f"[WARNING] {high_drift} INTERNAL cells have high drift (> 0.25).")
            else:
                logger.info("[OK] Internal drift is excellent.")

    # Boundary cells have different geometric norms.
    if not boundary_df.empty:
        # Boundary cells are naturally less compact.
        bad_bnd = len(boundary_df[boundary_df['compactness'] < 0.4])
        if bad_bnd > 0:
            logger.info(f"[WARNING] {bad_bnd} BOUNDARY cells are potential slivers (< 0.4).")
            
        # Boundary cells also have a naturally higher drift.
        if 'drift_ratio' in boundary_df.columns:
            high_drift_bnd = len(boundary_df[boundary_df['drift_ratio'] > 0.45])
            if high_drift_bnd > 0:
                logger.info(f"[WARNING] {high_drift_bnd} BOUNDARY cells have excessive drift (> 0.45).")
            else:
                logger.info("[OK] Boundary drift is within geometric norms (~0.34).")


def check_geometry_resolution(gdf):
    """
    Analyzes the vertex spacing (resolution) of geometries in a GeoDataFrame.
    Returns a summary of min, max, and mean segment lengths.
    """
    all_lengths = []

    for geom in gdf.geometry:
        # Handle different geometry types
        if geom.geom_type == 'Polygon':
            geoms = [geom]
        elif geom.geom_type == 'MultiPolygon':
            geoms = geom.geoms
        elif geom.geom_type == 'LineString':
            geoms = [geom]
        else:
            continue

        for g in geoms:
            # For polygons, check exterior and interiors
            if g.geom_type == 'Polygon':
                coords = list(g.exterior.coords)
                for interior in g.interiors:
                    coords.extend(list(interior.coords))
            else:
                coords = list(g.coords)

            # Calculate distances between consecutive points
            if len(coords) > 1:
                points = np.array(coords)
                # Vectorized distance calculation
                diffs = np.diff(points, axis=0)
                dists = np.sqrt((diffs**2).sum(axis=1))
                all_lengths.extend(dists)

    if not all_lengths:
        warnings.warn(
            "check_geometry_resolution: no valid segments found in the "
            "GeoDataFrame; returning empty statistics.",
            stacklevel=2,
        )
        return {
            "min": float("nan"),
            "max": float("nan"),
            "mean": float("nan"),
            "median": float("nan"),
            "count": 0,
        }

    all_lengths = np.array(all_lengths)
    return {
        "min": all_lengths.min(),
        "max": all_lengths.max(),
        "mean": all_lengths.mean(),
        "median": np.median(all_lengths),
        "count": len(all_lengths)
    }


def resample_geometry(geom, target_spacing):
    """
    Resamples a geometry so that vertices are evenly spaced at `target_spacing`.
    This is useful for creating uniform boundaries for meshing.
    """
    if geom.is_empty:
        return geom

    if geom.geom_type == 'LineString':
        length = geom.length
        # Calculate number of segments needed
        num_segments = max(int(np.ceil(length / target_spacing)), 1)
        # Generate distances along the line
        distances = np.linspace(0, length, num_segments + 1)
        # Interpolate points at these distances
        points = [geom.interpolate(d).coords[0] for d in distances]
        return LineString(points)
    
    elif geom.geom_type == 'Polygon':
        # Resample exterior ring
        ext_len = geom.exterior.length
        num_ext = max(int(np.ceil(ext_len / target_spacing)), 3) # Min 3 pts for polygon
        ext_dists = np.linspace(0, ext_len, num_ext + 1)
        # Note: interpolate(0) and interpolate(length) are the same for rings
        ext_points = [geom.exterior.interpolate(d).coords[0] for d in ext_dists]
        
        # Resample interior rings (holes)
        interiors = []
        for interior in geom.interiors:
            int_len = interior.length
            num_int = max(int(np.ceil(int_len / target_spacing)), 3)
            int_dists = np.linspace(0, int_len, num_int + 1)
            int_points = [interior.interpolate(d).coords[0] for d in int_dists]
            interiors.append(int_points)
            
        return Polygon(ext_points, interiors)
        
    elif geom.geom_type == 'MultiPolygon':
        parts = [resample_geometry(p, target_spacing) for p in geom.geoms]
        return MultiPolygon(parts)
        
    return geom
