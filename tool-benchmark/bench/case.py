"""Tool-neutral benchmark case: geometry, sizing spec and MF6 problems.

A case is one YAML file in ``tool-benchmark/cases``. Geometry is inline WKT (small
synthetic cases), or ``domain: {circle: [x, y, r], segments: n}`` for a circle,
and every feature is clipped to the domain on load, so all tools see exactly
the same inputs.

``target_ncpl`` is one cell count or a list (a convergence sweep); the first
target is the one plotted. ``mf6_problems`` lists problem names, or
``{name: ..., <parameter>: ...}`` mappings for problems that take parameters.

Sizing spec (see docs/benchmark-plan.md): a background size ``h_max``, a target
size ``h`` per feature and one cell-to-cell growth ratio ``growth``. Geometric
growth of successive cells by ``g`` means the size grows *linearly* with
distance d from a feature:

    h(x) = min(h_max, min_f(h_f + (g - 1) * d_f(x)))

``scale`` multiplies every length in the spec (the matched-count knob); the
growth ratio is never scaled.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
import yaml
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, Point, Polygon

FEATURE_KINDS = ("point", "line", "polygon")


@dataclass
class Feature:
    id: str
    kind: str
    geometry: object
    h: float
    barrier: bool = False


@dataclass
class Case:
    id: str
    tier: int
    description: str
    source: str
    crs: str
    domain: Polygon
    h_max: float
    growth: float
    targets: tuple
    features: list = field(default_factory=list)
    mf6_problems: list = field(default_factory=list)

    def features_of(self, kind: str) -> list:
        """Features of one kind, in file order."""
        assert kind in FEATURE_KINDS, f"unknown feature kind {kind!r}"
        return [f for f in self.features if f.kind == kind]

    def feature(self, feature_id: str) -> Feature:
        """The feature with this id."""
        matches = [f for f in self.features if f.id == feature_id]
        assert len(matches) == 1, f"{self.id}: expected one feature {feature_id!r}, found {len(matches)}"
        return matches[0]


def load_case(path: Path) -> Case:
    """Read a case YAML file and clip its features to the domain."""
    spec = yaml.safe_load(Path(path).read_text())
    domain = _load_domain(spec["domain"])
    assert isinstance(domain, Polygon) and domain.is_valid, f"{path}: domain must be one valid Polygon"
    assert spec["growth"] > 1.0, f"{path}: growth must be > 1"
    features = [_load_feature(item, domain) for item in spec.get("features", [])]
    targets = spec["target_ncpl"]
    targets = tuple(int(t) for t in (targets if isinstance(targets, list) else [targets]))
    return Case(
        id=spec["id"],
        tier=spec["tier"],
        description=spec["description"].strip(),
        source=spec["source"],
        crs=spec["crs"],
        domain=domain,
        h_max=float(spec["h_max"]),
        growth=float(spec["growth"]),
        targets=targets,
        features=[f for f in features if f is not None],
        mf6_problems=[_load_problem(item) for item in spec.get("mf6_problems", [])],
    )


def _load_domain(item) -> Polygon:
    """Domain polygon from WKT or a {circle: [x, y, r], segments: n} mapping."""
    if isinstance(item, str):
        return wkt.loads(item)
    x, y, r = item["circle"]
    n = int(item.get("segments", 128))
    assert n % 4 == 0, "circle segments must be a multiple of 4"
    return Point(x, y).buffer(r, quad_segs=n // 4)


def _load_problem(item) -> dict:
    """An MF6 problem entry as {'name': ..., parameters...}."""
    return {"name": item} if isinstance(item, str) else dict(item)


def _load_feature(item: dict, domain: Polygon) -> Feature | None:
    """Parse one feature entry; None if it falls entirely outside the domain."""
    kind = item["kind"]
    assert kind in FEATURE_KINDS, f"feature {item['id']}: unknown kind {kind!r}"
    geom = wkt.loads(item["wkt"]).intersection(domain)
    if geom.is_empty:
        return None
    if kind == "line":
        geom = shapely.line_merge(geom) if not isinstance(geom, LineString) else geom
        assert isinstance(geom, (LineString, MultiLineString)), f"feature {item['id']}: clipped to {geom.geom_type}"
    if kind == "point":
        assert isinstance(geom, Point), f"feature {item['id']}: points must be single Points"
    return Feature(
        id=str(item["id"]),
        kind=kind,
        geometry=geom,
        h=float(item["h"]),
        barrier=bool(item.get("barrier", False)),
    )


def line_parts(geom) -> list:
    """Single LineStrings of a (Multi)LineString."""
    return list(geom.geoms) if isinstance(geom, MultiLineString) else [geom]


def size_field(case: Case, xy: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Target cell size of the spec at points xy (N, 2)."""
    pts = shapely.points(np.asarray(xy, dtype=float))
    h = np.full(len(pts), case.h_max * scale)
    for feature in case.features:
        d = shapely.distance(feature.geometry, pts)
        h = np.minimum(h, feature.h * scale + (case.growth - 1.0) * d)
    return h
