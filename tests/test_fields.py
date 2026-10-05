"""Direct unit tests for the mesh size field classes.

Each test builds a minimal gmsh model, calls the field's create(), and reads
the resulting gmsh field options back with gmsh.model.mesh.field.get* so the
refinement parameters the classes promise are actually what gmsh receives.
"""
import math

import gmsh
import pytest
from shapely import union_all
from shapely.geometry import Polygon, box

from vorflow.fields import (
    ConstantField,
    DistanceField,
    ExponentialField,
    GeometricGrowthField,
    MeshField,
    ThresholdField,
)


@pytest.fixture
def gmsh_model():
    """A tiny synchronized OCC model: one point, one line, one surface."""
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("fields_test")
    pt = gmsh.model.occ.addPoint(5, 5, 0)
    l1 = gmsh.model.occ.addPoint(0, -2, 0)
    l2 = gmsh.model.occ.addPoint(10, -2, 0)
    line = gmsh.model.occ.addLine(l1, l2)
    rect = gmsh.model.occ.addRectangle(0, 0, 0, 10, 10)
    gmsh.model.occ.synchronize()
    yield {"point": pt, "line": line, "surface": rect}
    gmsh.finalize()


def _line_tags(model):
    return {"points": [], "lines": [model["line"]], "surfaces": []}


class TestDistanceField:
    def test_creates_distance_field_with_curve_list(self, gmsh_model):
        tag = DistanceField(sampling=33).create(gmsh, _line_tags(gmsh_model))
        assert tag is not None
        assert gmsh.model.mesh.field.getType(tag) == "Distance"
        curves = gmsh.model.mesh.field.getNumbers(tag, "CurvesList")
        assert list(map(int, curves)) == [gmsh_model["line"]]
        assert gmsh.model.mesh.field.getNumber(tag, "Sampling") == 33

    def test_returns_none_for_empty_tags(self, gmsh_model):
        tag = DistanceField().create(gmsh, {"points": [], "lines": [], "surfaces": []})
        assert tag is None


class TestConstantField:
    def test_sets_vin_and_vout(self, gmsh_model):
        tag = ConstantField(size=7.5).create(gmsh, {}, background_lc=50.0)
        assert gmsh.model.mesh.field.getType(tag) == "Constant"
        assert gmsh.model.mesh.field.getNumber(tag, "VIn") == 7.5
        assert gmsh.model.mesh.field.getNumber(tag, "VOut") == 50.0


class TestThresholdField:
    def test_sets_all_threshold_options(self, gmsh_model):
        field = ThresholdField(size_min=2.0, dist_min=4.0, dist_max=40.0, size_max=25.0)
        tag = field.create(gmsh, _line_tags(gmsh_model), background_lc=100.0)
        assert gmsh.model.mesh.field.getType(tag) == "Threshold"
        assert gmsh.model.mesh.field.getNumber(tag, "SizeMin") == 2.0
        assert gmsh.model.mesh.field.getNumber(tag, "SizeMax") == 25.0
        assert gmsh.model.mesh.field.getNumber(tag, "DistMin") == 4.0
        assert gmsh.model.mesh.field.getNumber(tag, "DistMax") == 40.0

    def test_size_max_defaults_to_background(self, gmsh_model):
        field = ThresholdField(size_min=2.0, dist_min=4.0, dist_max=40.0)
        tag = field.create(gmsh, _line_tags(gmsh_model), background_lc=100.0)
        assert gmsh.model.mesh.field.getNumber(tag, "SizeMax") == 100.0

    def test_polygon_surface_gets_constant_interior_via_min(self, gmsh_model):
        tags = {"points": [], "lines": [], "surfaces": [gmsh_model["surface"]]}
        tag = ThresholdField(2.0, 4.0, 40.0).create(gmsh, tags, background_lc=100.0)
        # growth from boundary curves + spatial constant inside -> combined Min
        assert gmsh.model.mesh.field.getType(tag) == "Min"


def _fields_of_type(field_type):
    return [f for f in gmsh.model.mesh.field.list() if gmsh.model.mesh.field.getType(f) == field_type]


def _view_triangles_by_value(view):
    """{value: [triangle Polygon, ...]} from a list-based scalar triangle view."""
    data_types, counts, data = gmsh.view.getListData(view)
    assert list(data_types) == ["ST"]
    entries = list(data[0])
    by_value = {}
    for i in range(int(counts[0])):
        x0, x1, x2, y0, y1, y2, _, _, _, v0, v1, v2 = entries[12 * i:12 * i + 12]
        assert v0 == v1 == v2
        by_value.setdefault(v0, []).append(Polygon([(x0, y0), (x1, y1), (x2, y2)]))
    return by_value


class TestFieldOnlyPolygonInterior:
    """Field-only polygon surfaces hold no domain nodes, so their interior is located by position."""

    def _field_only_tags(self, model, polygon):
        return {
            "points": [], "lines": [], "surfaces": [model["surface"]],
            "embedded_surfaces": [], "field_only_surfaces": [model["surface"]],
            "field_only_polygons": [polygon],
        }

    def test_interior_is_a_postview_not_a_surface_scoped_constant(self, gmsh_model):
        polygon = box(0, 0, 10, 10)
        tag = GeometricGrowthField().create(
            gmsh, self._field_only_tags(gmsh_model, polygon), background_lc=100.0, feature_lc=2.0
        )
        assert gmsh.model.mesh.field.getType(tag) == "Min"
        # A Constant scoped to the unfragmented surface never reaches domain nodes.
        assert _fields_of_type("Constant") == []
        (post_view,) = _fields_of_type("PostView")
        assert gmsh.model.mesh.field.getNumber(post_view, "UseClosest") == 0
        assert gmsh.model.mesh.field.getNumber(post_view, "CropNegativeValues") == 1

    def test_view_holds_size_inside_and_background_elsewhere(self, gmsh_model):
        # A polygon with a hole: the hole must take the background value.
        polygon = box(0, 0, 10, 10).difference(box(3, 3, 7, 7))
        GeometricGrowthField().create(
            gmsh, self._field_only_tags(gmsh_model, polygon), background_lc=100.0, feature_lc=2.0
        )
        (post_view,) = _fields_of_type("PostView")
        view = int(gmsh.model.mesh.field.getNumber(post_view, "ViewTag"))
        by_value = _view_triangles_by_value(view)
        assert set(by_value) == {2.0, 100.0}
        inside = union_all(by_value[2.0])
        outside = union_all(by_value[100.0])
        assert inside.symmetric_difference(polygon).area == pytest.approx(0.0, abs=1e-9)
        assert outside.intersection(polygon).area == pytest.approx(0.0, abs=1e-9)
        # Inside plus outside covers the model's bounding box, so gmsh never
        # evaluates the view outside its elements within the model.
        xmin, ymin, _, xmax, ymax, _ = gmsh.model.getBoundingBox(-1, -1)
        assert box(xmin, ymin, xmax, ymax).difference(inside.union(outside)).area == pytest.approx(
            0.0, abs=1e-9
        )

    def test_embedded_and_field_only_interiors_combine(self, gmsh_model):
        tags = self._field_only_tags(gmsh_model, box(0, 0, 10, 10))
        tags["embedded_surfaces"] = [gmsh_model["surface"]]
        ThresholdField(2.0, 4.0, 40.0).create(gmsh, tags, background_lc=100.0)
        (constant,) = _fields_of_type("Constant")
        assert list(map(int, gmsh.model.mesh.field.getNumbers(constant, "SurfacesList"))) == [
            gmsh_model["surface"]
        ]
        assert len(_fields_of_type("PostView")) == 1

    def test_field_only_surfaces_without_geometry_grow_from_boundary_only(self, gmsh_model):
        tags = self._field_only_tags(gmsh_model, None)
        tags["field_only_polygons"] = []
        tag = GeometricGrowthField().create(gmsh, tags, background_lc=100.0, feature_lc=2.0)
        assert gmsh.model.mesh.field.getType(tag) == "MathEval"
        assert _fields_of_type("Constant") == []
        assert _fields_of_type("PostView") == []


class TestExponentialField:
    def test_matheval_embeds_decay_and_sizes(self, gmsh_model):
        field = ExponentialField(size_min=1.5, decay_length=30.0, size_max=20.0)
        tag = field.create(gmsh, _line_tags(gmsh_model), background_lc=100.0)
        assert gmsh.model.mesh.field.getType(tag) == "MathEval"
        expr = gmsh.model.mesh.field.getString(tag, "F")
        assert "30.0" in expr and "1.5" in expr and "20.0" in expr

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"size_min": 0.0, "decay_length": 30.0}, "size_min"),
            ({"size_min": math.nan, "decay_length": 30.0}, "size_min"),
            ({"size_min": 1.0, "decay_length": 0.0}, "decay_length"),
            ({"size_min": 1.0, "decay_length": math.inf}, "decay_length"),
            (
                {"size_min": 2.0, "decay_length": 30.0, "size_max": 1.0},
                "size_max",
            ),
        ],
    )
    def test_rejects_invalid_constructor_values(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            ExponentialField(**kwargs)

    def test_rejects_background_smaller_than_size_min(self, gmsh_model):
        field = ExponentialField(size_min=2.0, decay_length=30.0)
        with pytest.raises(ValueError, match="background_lc"):
            field.create(gmsh, _line_tags(gmsh_model), background_lc=1.0)


class TestGeometricGrowthField:
    def test_is_exported_from_package_root(self):
        import vorflow

        assert vorflow.GeometricGrowthField is GeometricGrowthField

    def test_edge_ratio_expression_is_explicitly_linear(self, gmsh_model):
        tag = GeometricGrowthField(growth_factor=1.2).create(
            gmsh, _line_tags(gmsh_model), background_lc=100.0, feature_lc=2.0
        )
        assert gmsh.model.mesh.field.getType(tag) == "MathEval"
        expr = gmsh.model.mesh.field.getString(tag, "F")
        assert expr == "2.0 + 0.2 * F1"
        assert "Log" not in expr
        assert "^" not in expr

    def test_continuous_metric_expression_uses_log_gradient(self, gmsh_model):
        tag = GeometricGrowthField(
            growth_factor=1.2, growth_model="continuous_metric"
        ).create(
            gmsh, _line_tags(gmsh_model), background_lc=100.0, feature_lc=2.0
        )
        expr = gmsh.model.mesh.field.getString(tag, "F")
        assert expr == "2.0 + 0.182321556793955 * F1"

    def test_defaults_are_shared_and_transparent(self):
        field = GeometricGrowthField()
        assert field.growth_factor == 1.2
        assert field.growth_model == "edge_ratio"
        assert field.sampling == 20

    def test_constructor_sampling_reaches_distance_field(self, gmsh_model):
        tag = GeometricGrowthField(sampling=33).create(
            gmsh, _line_tags(gmsh_model), background_lc=100.0, feature_lc=2.0
        )
        assert gmsh.model.mesh.field.getNumber(tag - 1, "Sampling") == 33

    def test_create_sampling_override_is_retained(self, gmsh_model):
        tag = GeometricGrowthField(sampling=20).create(
            gmsh,
            _line_tags(gmsh_model),
            background_lc=100.0,
            feature_lc=2.0,
            sampling=41,
        )
        assert gmsh.model.mesh.field.getNumber(tag - 1, "Sampling") == 41

    def test_returns_none_without_feature_lc(self, gmsh_model):
        tag = GeometricGrowthField().create(gmsh, _line_tags(gmsh_model), 100.0)
        assert tag is None

    def test_returns_none_when_feature_not_finer_than_background(self, gmsh_model):
        tag = GeometricGrowthField().create(
            gmsh, _line_tags(gmsh_model), background_lc=2.0, feature_lc=5.0
        )
        assert tag is None

    @pytest.mark.parametrize("growth_factor", [1.0, 0.9, math.nan, math.inf, -math.inf])
    def test_rejects_invalid_growth_factor(self, growth_factor):
        with pytest.raises(ValueError, match="growth_factor"):
            GeometricGrowthField(growth_factor=growth_factor)

    @pytest.mark.parametrize("growth_model", ["triangle_centroids", None, []])
    def test_rejects_unknown_growth_model(self, growth_model):
        with pytest.raises(ValueError, match="growth_model"):
            GeometricGrowthField(growth_model=growth_model)

    @pytest.mark.parametrize("sampling", [0, -1, 1.5, True])
    def test_rejects_invalid_sampling(self, sampling):
        with pytest.raises(ValueError, match="sampling"):
            GeometricGrowthField(sampling=sampling)

    @pytest.mark.parametrize("feature_lc", [0.0, -1.0, math.nan, math.inf])
    def test_rejects_invalid_feature_size(self, gmsh_model, feature_lc):
        with pytest.raises(ValueError, match="feature_lc"):
            GeometricGrowthField().create(
                gmsh,
                _line_tags(gmsh_model),
                background_lc=100.0,
                feature_lc=feature_lc,
            )

    @pytest.mark.parametrize("background_lc", [0.0, -1.0, math.nan, math.inf])
    def test_rejects_invalid_background_size(self, gmsh_model, background_lc):
        with pytest.raises(ValueError, match="background_lc"):
            GeometricGrowthField().create(
                gmsh,
                _line_tags(gmsh_model),
                background_lc=background_lc,
                feature_lc=2.0,
            )

    def test_growth_model_and_sampling_participate_in_grouping(self):
        base = GeometricGrowthField()
        assert base != GeometricGrowthField(growth_model="continuous_metric")
        assert base != GeometricGrowthField(sampling=21)


class TestFieldEqualityGrouping:
    """__eq__/__hash__ let the engine group identical field specs."""

    def test_equal_parameters_hash_and_compare_equal(self):
        a = ThresholdField(2.0, 4.0, 40.0, 25.0)
        b = ThresholdField(2.0, 4.0, 40.0, 25.0)
        assert a == b
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_different_parameters_or_types_differ(self):
        a = ThresholdField(2.0, 4.0, 40.0)
        b = ThresholdField(3.0, 4.0, 40.0)
        c = ExponentialField(2.0, 4.0)
        assert a != b
        assert a != c

    def test_base_class_create_is_abstract(self):
        with pytest.raises(NotImplementedError):
            MeshField().create(gmsh, {}, 100.0)


def test_field_with_unhashable_attribute_can_be_grouped():
    class ListField(ThresholdField):
        def __init__(self):
            super().__init__(size_min=1.0, dist_min=0.0, dist_max=5.0)
            self.tags = [1, 2]

    assert hash(ListField()) == hash(ListField())
    assert ListField() == ListField()
