"""Direct unit tests for the mesh size field classes.

Each test builds a minimal gmsh model, calls the field's create(), and reads
the resulting gmsh field options back with gmsh.model.mesh.field.get* so the
refinement parameters the classes promise are actually what gmsh receives.
"""
import math

import gmsh
import pytest

from vorflow.fields import (
    AutoExponentialField,
    AutoLinearField,
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


class TestDeprecatedAutomaticFields:
    @pytest.mark.parametrize("field_class", [AutoExponentialField, AutoLinearField])
    def test_deprecated_names_warn_and_delegate(self, gmsh_model, field_class):
        with pytest.warns(DeprecationWarning, match="GeometricGrowthField"):
            field = field_class(growth_factor=1.2)
        assert isinstance(field, GeometricGrowthField)
        tag = field.create(
            gmsh, _line_tags(gmsh_model), background_lc=100.0, feature_lc=2.0
        )
        assert gmsh.model.mesh.field.getString(tag, "F") == "2.0 + 0.2 * F1"


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
