from __future__ import annotations

import logging
import math
import operator
import warnings

logger = logging.getLogger(__name__)


DEFAULT_GROWTH_FACTOR = 1.2
_GROWTH_MODELS = {"edge_ratio", "continuous_metric"}


def _positive_finite(value, name):
    """Return ``value`` as a positive finite float or raise a clear error."""
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number. Got {value!r}.") from exc
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be a positive finite number. Got {value!r}.")
    return value


def _positive_integer(value, name):
    """Return ``value`` as a positive integer without silently truncating it."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer. Got {value!r}.")
    try:
        value = operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a positive integer. Got {value!r}.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer. Got {value!r}.")
    return int(value)


def _growth_gradient(growth_factor, growth_model):
    """Convert an adjacent-size growth factor to a spatial size gradient."""
    try:
        growth_factor = float(growth_factor)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"growth_factor must be a finite number greater than 1.0. Got {growth_factor!r}."
        ) from exc
    if not math.isfinite(growth_factor) or growth_factor <= 1.0:
        raise ValueError(
            f"growth_factor must be a finite number greater than 1.0. Got {growth_factor!r}."
        )
    if not isinstance(growth_model, str) or growth_model not in _GROWTH_MODELS:
        choices = ", ".join(sorted(_GROWTH_MODELS))
        raise ValueError(f"growth_model must be one of: {choices}. Got {growth_model!r}.")
    if growth_model == "edge_ratio":
        return growth_factor, growth_factor - 1.0
    return growth_factor, math.log(growth_factor)


class MeshField:
    """
    Base class for all mesh size fields.
    """
    def create(self, gmsh_api, tags_dict, background_lc, feature_lc=None):
        """
        Creates the Gmsh field(s) and returns the field ID.
        
        Args:
            gmsh_api: The gmsh module.
            tags_dict (dict): Dictionary of tags {'points': [], 'lines': [], 'surfaces': []}.
            background_lc (float): Global background mesh size.
            feature_lc (float, optional): The target resolution of the specific feature group.
        """
        raise NotImplementedError("Subclasses must implement create()")

    def __eq__(self, other):
        """Equality check for grouping."""
        return isinstance(other, self.__class__) and self.__dict__ == other.__dict__

    def __hash__(self):
        """Hash for dictionary keys."""
        # Create a tuple of sorted item pairs to ensure consistent hashing
        return hash((self.__class__.__name__, tuple(sorted(self.__dict__.items()))))


class DistanceField(MeshField):
    """Creates a Gmsh Distance field from points/lines/surfaces tags.

    Internal — not part of the public API. This is a raw building block
    (distance-to-feature, not a mesh size) combined by the size fields via
    _distance_tags_for_growth(). Its create() signature differs from
    MeshField's, so it cannot be passed as a user field via ``fields=``.
    """

    def __init__(self, include_surfaces=True, sampling=20):
        self.include_surfaces = bool(include_surfaces)
        self.sampling = int(sampling)

    def create(self, gmsh_api, tags_dict):
        f_dist = gmsh_api.model.mesh.field.add("Distance")

        has_entities = False
        if tags_dict.get('points'):
            gmsh_api.model.mesh.field.setNumbers(f_dist, "PointsList", tags_dict['points'])
            has_entities = True
        if tags_dict.get('lines'):
            gmsh_api.model.mesh.field.setNumbers(f_dist, "CurvesList", tags_dict['lines'])
            gmsh_api.model.mesh.field.setNumber(f_dist, 'Sampling', self.sampling)
            has_entities = True
        if self.include_surfaces and tags_dict.get('surfaces'):#TODO check if loops needed
            gmsh_api.model.mesh.field.setNumbers(f_dist, "SurfacesList", tags_dict['surfaces'])
            gmsh_api.model.mesh.field.setNumber(f_dist, 'Sampling', self.sampling)
            has_entities = True

        if not has_entities:
            gmsh_api.model.mesh.field.remove(f_dist)
            return None

        return f_dist


def _surface_boundary_curves(gmsh_api, surface_tags):
    """Return boundary curve tags for the given surface tags."""
    curves = []
    seen = set()
    for tag in surface_tags:
        try:
            boundary = gmsh_api.model.getBoundary(
                [(2, int(tag))],
                combined=False,
                oriented=False,
                recursive=False,
            )
        except Exception:
            boundary = []

        for dim, curve_tag in boundary:
            if int(dim) != 1:
                continue
            curve_tag = int(curve_tag)
            if curve_tag not in seen:
                seen.add(curve_tag)
                curves.append(curve_tag)
    return curves


def _polygon_surface_tags(tags_dict):
    embedded_surfaces = tags_dict.get("embedded_surfaces", None)
    if embedded_surfaces is None:
        embedded_surfaces = tags_dict.get("surfaces", [])

    field_only_surfaces = tags_dict.get("field_only_surfaces", [])
    seen = set()
    surface_tags = []
    for tag in list(embedded_surfaces) + list(field_only_surfaces):
        tag = int(tag)
        if tag not in seen:
            seen.add(tag)
            surface_tags.append(tag)
    return surface_tags


def _distance_tags_for_growth(gmsh_api, tags_dict, sampling):
    """Build tags for distance growth while keeping polygon interiors flat."""
    polygon_surfaces = _polygon_surface_tags(tags_dict)
    boundary_curves = _surface_boundary_curves(gmsh_api, polygon_surfaces)

    growth_tags = {
        "points": list(tags_dict.get("points", [])),
        "lines": list(tags_dict.get("lines", [])) + boundary_curves,
        "surfaces": [],
    }

    # If no boundary curves could be recovered, fall back to the old surface
    # distance behavior instead of dropping the field.
    if polygon_surfaces and not boundary_curves:
        logger.warning(
            "Warning: could not recover boundary curves for a polygon size "
            "field; falling back to surface-distance growth."
        )
        growth_tags["surfaces"].extend(polygon_surfaces)

    return DistanceField(include_surfaces=True, sampling=sampling).create(
        gmsh_api, growth_tags
    )


def _polygon_surface_constant(gmsh_api, surface_tags, size, background_lc):
    if not surface_tags:
        return None

    const = gmsh_api.model.mesh.field.add("Constant")
    gmsh_api.model.mesh.field.setNumber(const, "VIn", float(size))
    gmsh_api.model.mesh.field.setNumber(const, "VOut", float(background_lc))
    # Field-only polygon surfaces are not domain partitions, but Gmsh can
    # still evaluate a spatial constant field inside their geometry.
    gmsh_api.model.mesh.field.setNumbers(
        const, "SurfacesList", [float(t) for t in surface_tags]
    )
    return const


def _combine_with_polygon_surface_constant(
    gmsh_api, growth_field, tags_dict, size, background_lc
):
    constant = _polygon_surface_constant(
        gmsh_api, _polygon_surface_tags(tags_dict), size, background_lc
    )
    if constant is None:
        return growth_field
    if growth_field is None:
        return constant

    f_min = gmsh_api.model.mesh.field.add("Min")
    gmsh_api.model.mesh.field.setNumbers(
        f_min, "FieldsList", [float(growth_field), float(constant)]
    )
    return f_min

# --- Manual Fields ---

class ConstantField(MeshField):
    """Internal — not part of the public API.

    Used by the engine to set the global background size (which users control
    through ``background_lc``). Not useful as a per-feature field: create()
    ignores ``tags_dict``, so it cannot scope a size to a feature. Polygon
    interior constants are handled by the internal
    _polygon_surface_constant() helper because they need SurfacesList scoping
    and are combined with a growth field.
    """

    def __init__(self, size):
        self.size = float(size)

    def create(self, gmsh_api, tags_dict, background_lc, feature_lc=None):
        const = gmsh_api.model.mesh.field.add("Constant")
        gmsh_api.model.mesh.field.setNumber(const, "VIn", self.size)
        gmsh_api.model.mesh.field.setNumber(const, "VOut", background_lc)
        return const


class ThresholdField(MeshField):
    def __init__(self, size_min, dist_min, dist_max, size_max=None, sampling=20):
        self.size_min = float(size_min)
        self.dist_min = float(dist_min)
        self.dist_max = float(dist_max)
        self.size_max = float(size_max) if size_max is not None else None
        self.sampling = int(sampling)
    def create(self, gmsh_api, tags_dict, background_lc, feature_lc=None):
        # 1. Distance field for growth away from features. For polygon
        # surfaces, use boundary curves for growth and add a spatial constant
        # field below so the polygon interior remains flat.
        f_dist = _distance_tags_for_growth(gmsh_api, tags_dict, self.sampling)
        if f_dist is None:
            return _combine_with_polygon_surface_constant(
                gmsh_api, None, tags_dict, self.size_min, background_lc
            )

        # 2. Threshold Field
        f_thresh = gmsh_api.model.mesh.field.add("Threshold")
        gmsh_api.model.mesh.field.setNumber(f_thresh, "InField", f_dist)
        gmsh_api.model.mesh.field.setNumber(f_thresh, "SizeMin", self.size_min)
        gmsh_api.model.mesh.field.setNumber(f_thresh, "SizeMax", self.size_max if self.size_max else background_lc)
        gmsh_api.model.mesh.field.setNumber(f_thresh, "DistMin", self.dist_min)
        gmsh_api.model.mesh.field.setNumber(f_thresh, "DistMax", self.dist_max)

        return _combine_with_polygon_surface_constant(
            gmsh_api, f_thresh, tags_dict, self.size_min, background_lc
        )

class ExponentialField(MeshField):
    """Exponential transition controlled by a physical decay length.

    The field is ``H - (H - size_min) * exp(-d / decay_length)``. Its maximum
    gradient occurs at the feature and equals ``(H - size_min) / decay_length``.
    To keep that gradient below an edge-ratio bound ``r - 1``, choose
    ``decay_length >= (H - size_min) / (r - 1)``.
    """

    def __init__(self, size_min, decay_length, size_max=None, sampling=20):
        self.size_min = _positive_finite(size_min, "size_min")
        self.decay_length = _positive_finite(decay_length, "decay_length")
        self.size_max = (
            _positive_finite(size_max, "size_max") if size_max is not None else None
        )
        if self.size_max is not None and self.size_max < self.size_min:
            raise ValueError("size_max must be greater than or equal to size_min.")
        self.sampling = _positive_integer(sampling, "sampling")

    def create(self, gmsh_api, tags_dict, background_lc, feature_lc=None):
        background_lc = _positive_finite(background_lc, "background_lc")
        s_max = self.size_max if self.size_max is not None else background_lc
        if s_max < self.size_min:
            raise ValueError(
                "background_lc must be greater than or equal to size_min when size_max is omitted."
            )

        f_dist = _distance_tags_for_growth(gmsh_api, tags_dict, self.sampling)
        if f_dist is None:
            return _combine_with_polygon_surface_constant(
                gmsh_api, None, tags_dict, self.size_min, background_lc
            )

        f_math = gmsh_api.model.mesh.field.add("MathEval")
        expr = f"{s_max} - ({s_max} - {self.size_min}) * Exp(-F{f_dist} / {self.decay_length})"
        gmsh_api.model.mesh.field.setString(f_math, "F", expr)
        return _combine_with_polygon_surface_constant(
            gmsh_api, f_math, tags_dict, self.size_min, background_lc
        )

# --- Automatic growth fields ---


class GeometricGrowthField(MeshField):
    """Grade target edge length away from a feature with a bounded gradient.

    ``growth_factor`` describes geometric characteristic-length growth by
    element index. The Gmsh field is deliberately linear in physical distance:

    - ``edge_ratio`` uses gradient ``growth_factor - 1``;
    - ``continuous_metric`` uses gradient ``log(growth_factor)``.

    Normal ``MeshGenerator`` use caps the result at ``background_lc`` through
    the engine's global ``Min`` field. A direct low-level call to ``create()``
    returns the uncapped growth field.
    """

    def __init__(
        self,
        growth_factor=DEFAULT_GROWTH_FACTOR,
        growth_model="edge_ratio",
        sampling=20,
    ):
        self.growth_factor, self.gradient = _growth_gradient(
            growth_factor, growth_model
        )
        self.growth_model = growth_model
        self.sampling = _positive_integer(sampling, "sampling")

    def create(
        self,
        gmsh_api,
        tags_dict,
        background_lc,
        feature_lc=None,
        sampling=None,
    ):
        if feature_lc is None:
            return None

        cs = _positive_finite(feature_lc, "feature_lc")
        cs_dom = _positive_finite(background_lc, "background_lc")
        if cs >= cs_dom:
            return None

        distance_sampling = (
            self.sampling
            if sampling is None
            else _positive_integer(sampling, "sampling")
        )

        f_dist = _distance_tags_for_growth(gmsh_api, tags_dict, distance_sampling)
        if f_dist is None:
            return _combine_with_polygon_surface_constant(
                gmsh_api, None, tags_dict, cs, background_lc
            )

        f_math = gmsh_api.model.mesh.field.add("MathEval")
        gradient = format(self.gradient, ".15g")
        expr = f"{cs} + {gradient} * F{f_dist}"

        gmsh_api.model.mesh.field.setString(f_math, "F", expr)
        return _combine_with_polygon_surface_constant(
            gmsh_api, f_math, tags_dict, cs, background_lc
        )


class AutoExponentialField(GeometricGrowthField):
    """Deprecated compatibility name for :class:`GeometricGrowthField`."""

    def __init__(
        self,
        growth_factor=DEFAULT_GROWTH_FACTOR,
        growth_model="edge_ratio",
        sampling=20,
    ):
        warnings.warn(
            "AutoExponentialField is deprecated; use GeometricGrowthField instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(growth_factor, growth_model, sampling)


class AutoLinearField(GeometricGrowthField):
    """Deprecated compatibility name for :class:`GeometricGrowthField`."""

    def __init__(
        self,
        growth_factor=DEFAULT_GROWTH_FACTOR,
        sampling=20,
        growth_model="edge_ratio",
    ):
        warnings.warn(
            "AutoLinearField is deprecated; use GeometricGrowthField instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(growth_factor, growth_model, sampling)
