"""Feature-row accessors and small geometry helpers.

Shared by the Gmsh engine (``engine.py``) and the pure-geometry quad-buffer
planning (``buffer.py``). Rows are clean ``ConceptualMesh`` GeoDataFrame rows,
so optional columns may be missing or NaN; every accessor resolves those to the
same defaults the engine has always used.
"""
from __future__ import annotations

import math

import pandas as pd
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon


def is_embedded(row) -> bool:
    """True unless the row sets ``embed`` to a falsy, non-NaN value."""
    val = row.get('embed', True)
    if pd.isna(val):
        return True
    return bool(val)


def row_bool(row, column, default=False) -> bool:
    """Read a boolean-ish column (True, 'true', '1', 'yes'); NaN gives ``default``."""
    val = row.get(column, default)
    if pd.isna(val):
        return bool(default)
    return (val is True) or (str(val).lower() in ['true', '1', 'yes'])


def positive_number(value) -> float | None:
    """``value`` as a float if it is a positive number, else None."""
    if value is None or pd.isna(value):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        # Non-numeric column content means "not set".
        return None
    return value if value > 0 else None


def feature_lc(row, background_lc) -> float:
    """The row's ``lc``, falling back to ``background_lc`` then 10.0 (floored at 0.001)."""
    lc = positive_number(row.get('lc'))
    if lc is None:
        lc = positive_number(background_lc)
    return max(lc if lc is not None else 10.0, 0.001)


def polygon_parts(geom) -> list:
    """Flatten a (Multi)Polygon or collection into its Polygon parts."""
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


def line_parts(geom) -> list:
    """Flatten a (Multi)LineString or collection into its non-empty LineString parts."""
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


def sanitize_coords(coords, *, min_spacing=1e-5, require_closed=False, min_points=2) -> list:
    """Drop non-finite and near-duplicate coordinates; [] if fewer than ``min_points`` remain."""
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
