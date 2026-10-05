"""Vorflow for QGIS.

Plugin interface and integration developed by David Ström.
Vorflow is an independent upstream project created by its original authors
and contributors. See ATTRIBUTION.md for third-party notices.
"""

import csv
import inspect
import json
import math
import os
import re
import traceback

from qgis.PyQt.QtCore import QDateTime
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget
)
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsCoordinateTransform,
    QgsGraduatedSymbolRenderer, QgsMapLayerProxyModel, QgsProject,
    QgsRendererCategory, QgsRendererRange, QgsSymbol, QgsVectorLayer
)
from qgis.gui import QgsMapLayerComboBox


def parse_json(text, title):
    text = text.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {title}: rad {exc.lineno}, "
            f"kolumn {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"{title} must be a JSON object.")
    return value


def call_supported(function, *args, **kwargs):
    """Pass only arguments supported by the installed Vorflow version."""
    signature = inspect.signature(function)
    parameters = signature.parameters
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )
    accepted = kwargs if accepts_kwargs else {
        key: value for key, value in kwargs.items() if key in parameters
    }
    return function(*args, **accepted)


def build_fields(kwargs):
    """Convert JSON definitions of Vorflow fields to MeshField objects."""
    kwargs = dict(kwargs)
    specifications = kwargs.pop("fields", None)
    if not specifications:
        return kwargs
    if not isinstance(specifications, list):
        raise ValueError("'fields' must be a JSON list.")

    from vorflow.fields import (
        ExponentialField, GeometricGrowthField, ThresholdField
    )
    classes = {
        "ThresholdField": ThresholdField,
        "ExponentialField": ExponentialField,
        "GeometricGrowthField": GeometricGrowthField,
    }
    fields = []
    for specification in specifications:
        if not isinstance(specification, dict):
            raise ValueError("Each entry in 'fields' must be an object.")
        specification = dict(specification)
        field_type = specification.pop("type", "")
        if field_type not in classes:
            raise ValueError(
                f"Unknown field type '{field_type}'. Allowed: "
                + ", ".join(classes)
            )
        fields.append(call_supported(classes[field_type], **specification))
    kwargs["fields"] = fields
    return kwargs


def qgis_to_shapely(qgs_geometry):
    data = bytes(qgs_geometry.asWkb())
    try:
        import shapely
        return shapely.from_wkb(data)
    except AttributeError:
        from shapely import wkb
        return wkb.loads(data)


def transformed_geometry(feature, layer):
    geometry = feature.geometry()
    if geometry is None or geometry.isEmpty():
        return None
    geometry = geometry.makeValid()
    project = QgsProject.instance()
    target_crs = project.crs()
    if layer.crs().isValid() and target_crs.isValid() and layer.crs() != target_crs:
        transform = QgsCoordinateTransform(layer.crs(), target_crs, project)
        geometry.transform(transform)
    return qgis_to_shapely(geometry)


def safe_name(value):
    text = re.sub(r"[^0-9A-Za-zÅÄÖåäö_-]+", "_", str(value)).strip("_")
    return text or "layer"


def feature_identifier(feature, field_name, prefix, layer_name):
    if field_name and field_name in feature.fields().names():
        value = feature[field_name]
        if value is not None and str(value).strip():
            return f"{safe_name(layer_name)}::{value}"
    return f"{safe_name(layer_name)}::{prefix}-{feature.id()}"



PARAMETER_HELP = {
    "resolution": (
        "Target size",
        "Desired local mesh size in the project's map units, normally metres. "
        "It is a target, not a guarantee of identical cells. The finest "
        "requirement from overlapping objects normally controls."
    ),
    "growth_factor": (
        "Growth factor",
        "Controls how quickly cell size may increase away from the object. Values close to "
        "1 give a slow, smooth transition but more cells. Example: 1.1–1.3."
    ),
    "embed": (
        "Embed geometry",
        "Attempts to make the geometry part of the mesh topology. Use this "
        "when nodes and cell edges should follow the object."
    ),
    "simplify_tolerance": (
        "Simplification tolerance",
        "Simplifies geometry before mesh generation. 0 preserves the original geometry. "
        "An excessive value may move or remove important details."
    ),
    "densify": (
        "Densify geometry",
        "Adds more vertices along geometry so the mesh can better "
        "follow its shape and the specified target-size variation."
    ),
    "snap_to_polygons": (
        "Snap to polygons",
        "Snaps lines to nearby polygon boundaries where supported by vorflow. "
        "This may improve topology at connections."
    ),
    "is_barrier": (
        "Barrier",
        "Treats the line as a barrier in the conceptual model. Use "
        "for objects across which connectivity or flow should not pass freely."
    ),
    "quad_buffer": (
        "Quad corridor",
        "Attempts to create a band of quadrilaterals along the object. Useful for "
        "streams, ditches, pipes and other elongated features."
    ),
    "quad_buffer_thickness": (
        "Number of quad rows",
        "Number of cell rows in the quad corridor. A higher value creates a wider band "
        "and more cells."
    ),
    "z_order": (
        "Priority (z-order)",
        "Controls priority where geometries overlap. Higher priority is normally used "
        "for details that should override more general zones."
    ),
    "zone_id": (
        "Zone ID",
        "Identifier for the model domain. Normally does not need to be changed."
    ),
    "field_model": (
        "Refinement model",
        "Standard uses the object's resolution and growth factor. The other "
        "options create an explicit vorflow mesh field for distance-based "
        "variation. Version 0.6 defaults to Geometric growth with edge_ratio, "
        "growth factor 1.2 and sampling 25 as a robust general starting point."
    ),
    "size_min": (
        "Minimum size",
        "Minimum target size near the object for ThresholdField or ExponentialField."
    ),
    "size_max": (
        "Maximum size",
        "Maximum target size far from the object for ThresholdField or "
        "ExponentialField."
    ),
    "dist_min": (
        "Minimum distance",
        "Within this distance, the minimum size is normally used."
    ),
    "dist_max": (
        "Maximum distance",
        "Beyond this distance, the maximum size is normally used."
    ),
    "decay_length": (
        "Decay length",
        "Characteristic distance for the exponential transition from fine to coarse."
    ),
    "sampling": (
        "Sampling",
        "Number of sample points used to describe the field. More samples "
        "may produce smoother results but increase processing time."
    ),
    "growth_model": (
        "Growth model",
        "edge_ratio gives stepwise geometric edge growth. continuous_metric "
        "gives a more continuous size function. edge_ratio is the recommended "
        "robust default."
    ),
}


def help_text(key):
    title, text = PARAMETER_HELP.get(key, (key, ""))
    return f"<b>{title}</b><br>{text}"


def make_double(value, minimum=0.0, maximum=1.0e12, decimals=4):
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setKeyboardTracking(False)
    return widget


class ParameterEditorWidget(QWidget):
    """Interactive Vorflow parameters, either global or layer-specific."""

    def __init__(self, geometry_kind, defaults=None, individual=False, parent=None):
        super().__init__(parent)
        self.geometry_kind = geometry_kind
        self.individual = individual
        self.controls = {}
        self.overrides = {}
        defaults = defaults or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        basic = QGroupBox("Basic settings")
        basic_form = QFormLayout(basic)
        self._add_control(
            basic_form, "resolution", "Resolution:",
            make_double(defaults.get("resolution", 10.0), 0.000001),
        )
        self._add_control(
            basic_form, "growth_factor", "Growth factor:",
            make_double(defaults.get("growth_factor", 1.2), 1.0, 1000.0, 4),
        )
        self._add_control(
            basic_form, "embed", "Embed geometry:",
            self._checkbox(defaults.get("embed", True)),
        )
        self._add_control(
            basic_form, "simplify_tolerance", "Simplification tolerance:",
            make_double(defaults.get("simplify_tolerance", 0.0), 0.0),
        )
        if geometry_kind == "domain":
            self._add_control(
                basic_form, "zone_id", "Zone ID:",
                self._lineedit(defaults.get("zone_id", "domain")),
            )
        layout.addWidget(basic)

        if geometry_kind in ("domain", "line", "polygon"):
            geometry = QGroupBox("Geometry and topology")
            geometry_form = QFormLayout(geometry)
            self._add_control(
                geometry_form, "densify", "Densify geometry:",
                self._checkbox(defaults.get("densify", True)),
            )
            self._add_control(
                geometry_form, "z_order", "Priority (z-order):",
                self._intspin(defaults.get("z_order", 0), -100000, 100000),
            )
            if geometry_kind == "line":
                self._add_control(
                    geometry_form, "snap_to_polygons",
                    "Snap to polygons:",
                    self._checkbox(defaults.get("snap_to_polygons", True)),
                )
                self._add_control(
                    geometry_form, "is_barrier", "Treat as barrier:",
                    self._checkbox(defaults.get("is_barrier", False)),
                )
            if geometry_kind in ("line", "polygon"):
                self._add_control(
                    geometry_form, "quad_buffer",
                    "Create quad corridor:",
                    self._checkbox(defaults.get("quad_buffer", False)),
                )
                self._add_control(
                    geometry_form, "quad_buffer_thickness",
                    "Number of quad rows:",
                    self._intspin(defaults.get("quad_buffer_thickness", 1), 1, 1000),
                )
            layout.addWidget(geometry)

        refinement = QGroupBox("Advanced refinement model")
        refinement_form = QFormLayout(refinement)
        self.field_model = QComboBox()
        self.field_model.addItem("Standard – resolution and growth factor", "standard")
        self.field_model.addItem("Geometric growth", "geometric")
        self.field_model.addItem("Threshold", "threshold")
        self.field_model.addItem("Exponential", "exponential")
        model = defaults.get("field_model", "geometric")
        idx = self.field_model.findData(model)
        self.field_model.setCurrentIndex(max(0, idx))

        if individual:
            model_row = QWidget()
            model_layout = QHBoxLayout(model_row)
            model_layout.setContentsMargins(0, 0, 0, 0)
            self.field_override = QCheckBox("Override")
            self.field_override.setToolTip(
                "Clear to inherit the refinement model from the geometry type's "
                "global settings."
            )
            model_layout.addWidget(self.field_override)
            model_layout.addWidget(self.field_model, 1)
            refinement_form.addRow("Refinement model:", model_row)
            self.field_override.toggled.connect(self._update_field_enabled)
        else:
            self.field_override = None
            refinement_form.addRow("Refinement model:", self.field_model)

        self.field_stack = QStackedWidget()

        standard = QLabel(
            "No additional mesh field. The object's resolution and growth factor are used."
        )
        standard.setWordWrap(True)
        self.field_stack.addWidget(standard)

        geometric = QWidget()
        gf = QFormLayout(geometric)
        self.field_growth_factor = make_double(
            defaults.get("field_growth_factor", defaults.get("growth_factor", 1.2)),
            1.0, 1000.0, 4
        )
        self.growth_model = QComboBox()
        self.growth_model.addItem("Edge ratio (edge_ratio)", "edge_ratio")
        self.growth_model.addItem("Continuous metric (continuous_metric)", "continuous_metric")
        growth_model = defaults.get("growth_model", "edge_ratio")
        growth_idx = self.growth_model.findData(growth_model)
        self.growth_model.setCurrentIndex(max(0, growth_idx))
        self.field_sampling_g = self._intspin(defaults.get("sampling", 25), 1, 100000)
        gf.addRow("Growth factor:", self.field_growth_factor)
        gf.addRow("Growth model:", self.growth_model)
        gf.addRow("Sampling:", self.field_sampling_g)
        self.field_stack.addWidget(geometric)

        threshold = QWidget()
        tf = QFormLayout(threshold)
        self.threshold_min = make_double(defaults.get("size_min", 5.0), 0.000001)
        self.threshold_max = make_double(defaults.get("size_max", 100.0), 0.000001)
        self.dist_min = make_double(defaults.get("dist_min", 10.0), 0.0)
        self.dist_max = make_double(defaults.get("dist_max", 500.0), 0.0)
        self.field_sampling_t = self._intspin(defaults.get("sampling", 25), 1, 100000)
        tf.addRow("Minimum size:", self.threshold_min)
        tf.addRow("Maximum size:", self.threshold_max)
        tf.addRow("Minimum distance:", self.dist_min)
        tf.addRow("Maximum distance:", self.dist_max)
        tf.addRow("Sampling:", self.field_sampling_t)
        self.field_stack.addWidget(threshold)

        exponential = QWidget()
        ef = QFormLayout(exponential)
        self.exp_min = make_double(defaults.get("size_min", 5.0), 0.000001)
        self.exp_max = make_double(defaults.get("size_max", 100.0), 0.000001)
        self.decay_length = make_double(defaults.get("decay_length", 150.0), 0.000001)
        self.field_sampling_e = self._intspin(defaults.get("sampling", 25), 1, 100000)
        ef.addRow("Minimum size:", self.exp_min)
        ef.addRow("Maximum size:", self.exp_max)
        ef.addRow("Decay length:", self.decay_length)
        ef.addRow("Sampling:", self.field_sampling_e)
        self.field_stack.addWidget(exponential)

        refinement_form.addRow(self.field_stack)
        layout.addWidget(refinement)
        layout.addStretch()

        self.field_model.currentIndexChanged.connect(self._update_field_page)
        self._update_field_page()
        self._update_field_enabled()

    @staticmethod
    def _checkbox(value):
        widget = QCheckBox()
        widget.setChecked(bool(value))
        return widget

    @staticmethod
    def _lineedit(value):
        return QLineEdit(str(value))

    @staticmethod
    def _intspin(value, minimum, maximum):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(int(value))
        return widget

    def _add_control(self, form, key, label, widget):
        widget.setToolTip(PARAMETER_HELP.get(key, ("", ""))[1])
        self.controls[key] = widget
        if self.individual:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            override = QCheckBox("Override")
            override.setToolTip(
                "Clear to use the global value. Select "
                "to set a layer-specific value."
            )
            self.overrides[key] = override
            row_layout.addWidget(override)
            row_layout.addWidget(widget, 1)
            override.toggled.connect(widget.setEnabled)
            widget.setEnabled(False)
            form.addRow(label, row)
        else:
            form.addRow(label, widget)

    def _update_field_page(self):
        self.field_stack.setCurrentIndex(self.field_model.currentIndex())

    def _update_field_enabled(self):
        enabled = not self.individual or self.field_override.isChecked()
        self.field_model.setEnabled(enabled)
        self.field_stack.setEnabled(enabled)

    @staticmethod
    def _widget_value(widget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
            return widget.value()
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return None

    def values(self):
        values = {}
        for key, widget in self.controls.items():
            if not self.individual or self.overrides[key].isChecked():
                values[key] = self._widget_value(widget)

        if not self.individual or self.field_override.isChecked():
            model = self.field_model.currentData()
            values["field_model"] = model
            if model == "geometric":
                values["fields"] = [{
                    "type": "GeometricGrowthField",
                    "growth_factor": self.field_growth_factor.value(),
                    "growth_model": self.growth_model.currentData(),
                    "sampling": self.field_sampling_g.value(),
                }]
            elif model == "threshold":
                if self.threshold_max.value() < self.threshold_min.value():
                    raise ValueError("Maximum size must be greater than or equal to minimum size.")
                if self.dist_max.value() < self.dist_min.value():
                    raise ValueError("Maximum distance must be greater than or equal to minimum distance.")
                values["fields"] = [{
                    "type": "ThresholdField",
                    "size_min": self.threshold_min.value(),
                    "size_max": self.threshold_max.value(),
                    "dist_min": self.dist_min.value(),
                    "dist_max": self.dist_max.value(),
                    "sampling": self.field_sampling_t.value(),
                }]
            elif model == "exponential":
                if self.exp_max.value() < self.exp_min.value():
                    raise ValueError("Maximum size must be greater than or equal to minimum size.")
                values["fields"] = [{
                    "type": "ExponentialField",
                    "size_min": self.exp_min.value(),
                    "size_max": self.exp_max.value(),
                    "decay_length": self.decay_length.value(),
                    "sampling": self.field_sampling_e.value(),
                }]
            else:
                values.pop("fields", None)
        return values

    def summary(self):
        values = self.values()
        parts = []
        if "resolution" in values:
            parts.append(f"size {values['resolution']:g}")
        if "growth_factor" in values:
            parts.append(f"growth {values['growth_factor']:g}")
        if values.get("is_barrier"):
            parts.append("barrier")
        if values.get("quad_buffer"):
            parts.append(f"quad × {values.get('quad_buffer_thickness', 1)}")
        if values.get("field_model") not in (None, "standard"):
            parts.append(str(values["field_model"]))
        return ", ".join(parts) if parts else "Inherits all global values"


class LayerSettingsDialog(QDialog):
    def __init__(self, geometry_kind, editor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Layer-specific settings")
        self.resize(640, 720)
        layout = QVBoxLayout(self)
        info = QLabel(
            "Select <b>Override</b> for parameters that should differ from the "
            "global settings. Unselected parameters are inherited."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(editor)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class LayerSourceWidget(QGroupBox):
    """A selectable QGIS layer with interactive layer-specific parameters."""

    def __init__(
        self, geometry_filter, geometry_kind, remove_callback=None,
        parent=None, direct_settings=False, defaults=None
    ):
        super().__init__("Input layer", parent)
        self.remove_callback = remove_callback
        self.geometry_kind = geometry_kind
        self.direct_settings = direct_settings
        form = QFormLayout(self)

        layer_row = QWidget()
        layer_layout = QHBoxLayout(layer_row)
        layer_layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("Use")
        self.enabled.setChecked(True)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(geometry_filter)
        self.layer_combo.setAllowEmptyLayer(True)
        browse = QPushButton("Open file…")
        browse.clicked.connect(self.open_file)
        layer_layout.addWidget(self.enabled)
        layer_layout.addWidget(self.layer_combo, 1)
        layer_layout.addWidget(browse)
        if remove_callback:
            remove = QPushButton("Remove")
            remove.clicked.connect(lambda: remove_callback(self))
            layer_layout.addWidget(remove)

        self.id_field = QComboBox()
        self.id_field.addItem("<QGIS feature-ID>", "")

        self.editor = ParameterEditorWidget(
            geometry_kind, defaults=defaults,
            individual=not direct_settings
        )
        self.settings_dialog = LayerSettingsDialog(
            geometry_kind, self.editor, self
        )
        settings_row = QWidget()
        settings_layout = QHBoxLayout(settings_row)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings = QPushButton("Settings…")
        settings.clicked.connect(self.open_settings)
        self.settings_summary = QLabel("")
        self.settings_summary.setWordWrap(True)
        settings_layout.addWidget(settings)
        settings_layout.addWidget(self.settings_summary, 1)

        form.addRow("Layer:", layer_row)
        form.addRow("ID field:", self.id_field)
        form.addRow("Controls:", settings_row)

        self.layer_combo.layerChanged.connect(self.update_fields)
        self.layer_combo.layerChanged.connect(self.update_title)
        self.update_summary()

    def open_settings(self):
        self.settings_dialog.exec_()
        self.update_summary()

    def update_summary(self):
        try:
            self.settings_summary.setText(self.editor.summary())
        except Exception:
            self.settings_summary.setText("Review settings")

    def update_title(self, layer):
        self.setTitle(layer.name() if layer else "Input layer")

    def update_fields(self, layer):
        current = self.id_field.currentData()
        self.id_field.clear()
        self.id_field.addItem("<QGIS feature-ID>", "")
        if layer:
            for field in layer.fields():
                self.id_field.addItem(field.name(), field.name())
        index = self.id_field.findData(current)
        if index >= 0:
            self.id_field.setCurrentIndex(index)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open vector layer", "",
            "Vector layers (*.shp *.gpkg *.geojson *.json *.sqlite);;"
            "All files (*.*)"
        )
        if not path:
            return
        layer = QgsVectorLayer(
            path, os.path.splitext(os.path.basename(path))[0], "ogr"
        )
        if not layer.isValid():
            QMessageBox.critical(self, "Vorflow", f"Could not open:\n{path}")
            return
        QgsProject.instance().addMapLayer(layer)
        self.layer_combo.setLayer(layer)

    def layer(self):
        return self.layer_combo.currentLayer()

    def selected_id_field(self):
        return self.id_field.currentData()

    def merged_parameters(self, global_parameters):
        parameters = dict(global_parameters)
        own = self.editor.values()
        # A layer-specific Standard selection must also be able to disable a global field.
        if own.get("field_model") == "standard":
            parameters.pop("fields", None)
        parameters.update({k: v for k, v in own.items() if k != "field_model"})
        return build_fields(parameters)


class MultiLayerInputWidget(QWidget):
    """Any number of layers with global and layer-specific GUI parameters."""

    def __init__(self, title, geometry_filter, geometry_kind, defaults, parent=None):
        super().__init__(parent)
        self.title = title
        self.geometry_filter = geometry_filter
        self.geometry_kind = geometry_kind
        self.defaults = defaults
        self.sources = []

        layout = QVBoxLayout(self)
        global_group = QGroupBox(f"Global default parameters – {title}")
        global_layout = QVBoxLayout(global_group)
        global_scroll = QScrollArea()
        global_scroll.setWidgetResizable(True)
        self.global_editor = ParameterEditorWidget(
            geometry_kind, defaults=defaults, individual=False
        )
        global_scroll.setWidget(self.global_editor)
        global_scroll.setMinimumHeight(275)
        global_layout.addWidget(global_scroll)
        layout.addWidget(global_group)

        add_button = QPushButton(f"Add {title.lower()} layer")
        add_button.clicked.connect(self.add_source)
        layout.addWidget(add_button)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.addStretch()
        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)
        self.add_source()

    def add_source(self):
        source = LayerSourceWidget(
            self.geometry_filter, self.geometry_kind,
            self.remove_source, self.container,
            direct_settings=False, defaults=self.defaults
        )
        self.sources.append(source)
        self.container_layout.insertWidget(
            self.container_layout.count() - 1, source
        )

    def remove_source(self, source):
        if source in self.sources:
            self.sources.remove(source)
            source.setParent(None)
            source.deleteLater()

    def active_sources(self):
        global_parameters = self.global_editor.values()
        global_parameters.pop("field_model", None)
        result = []
        for source in self.sources:
            if source.enabled.isChecked() and source.layer() is not None:
                result.append((
                    source, source.layer(),
                    source.merged_parameters(global_parameters)
                ))
        return result


class ParameterHelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vorflow – Parameter help")
        self.resize(760, 700)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        sections = [
            "<h1>Parameter help</h1>",
            "<p>Values use the project's map units. Use a projected "
            "CRS, such as SWEREF 99 TM (EPSG:3006), so distances are normally "
            "interpreted in metres.</p>",
            "<h2>How sizes are combined</h2>"
            "<p>Vorflow/Gmsh combines contributions from the domain, points, lines, "
            "polygons and mesh fields. Where requirements overlap, the finest "
            "size normally controls. A specified target size therefore does not mean "
            "that every cell will have exactly that size.</p>",
        ]
        for key in (
            "resolution", "growth_factor", "embed", "simplify_tolerance",
            "densify", "snap_to_polygons", "is_barrier", "quad_buffer",
            "quad_buffer_thickness", "z_order", "field_model", "size_min",
            "size_max", "dist_min", "dist_max", "decay_length", "sampling",
            "growth_model"
        ):
            title, body = PARAMETER_HELP[key]
            sections.append(f"<h3>{title}</h3><p>{body}</p>")
        sections.extend([
            "<h2>Global and layer-specific values</h2>"
            "<p>Each geometry type has global defaults. Click "
            "<b>Settings…</b> for a layer and select <b>Override</b> only "
            "for parameters that should differ.</p>",
            "<h2>Practical troubleshooting</h2><ul>"
            "<li>Test one layer at a time if the mesh becomes unexpectedly fine.</li>"
            "<li>Check overlapping refinement zones and intersecting lines.</li>"
            "<li>Use a low growth factor for a smooth transition, but expect more cells.</li>"
            "<li>Review the quality group after each run.</li></ul>",
            "<h2>About and attribution</h2>"
            "<p>This QGIS plugin was developed by <b>David Ström</b>. "
            "It integrates the independent <b>Vorflow</b> Python package and "
            "uses Vorflow terminology where applicable.</p>"
            "<p>Vorflow itself is the work of its original authors and contributors; "
            "this plugin does not claim authorship of the upstream project. "
            "For current authorship, source repository and licence information, see "
            "<a href='https://pypi.org/project/vorflow/'>the Vorflow project page on PyPI</a> "
            "and the upstream repository linked there.</p>"
            "<p>QGIS, Gmsh, MODFLOW 6, ModelMuse and FloPy are separate third-party "
            "projects and retain their respective names, licences and copyrights.</p>",
        ])
        browser.setHtml("".join(sections))
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)

def numeric_series(frame, field):
    if field not in frame.columns:
        return None
    try:
        import pandas as pd
        return pd.to_numeric(frame[field], errors="coerce")
    except Exception:
        return None


def enrich_quality_grid(frame):
    """Add easy-to-interpret, dimensionless summary metrics."""
    import numpy as np
    import pandas as pd

    result = frame.copy()
    high_is_good = [
        field for field in
        ("minSICN", "minSJ", "minSIGE", "gamma", "minIsotropy", "angleShape")
        if field in result.columns
    ]

    normalized = {}
    for field in high_is_good:
        values = pd.to_numeric(result[field], errors="coerce")
        normalized[field] = values.clip(lower=0.0, upper=1.0)

    if normalized:
        quality_table = pd.DataFrame(normalized, index=result.index)
        result["q_overall"] = quality_table.min(axis=1, skipna=True)
        result["q_mean"] = quality_table.mean(axis=1, skipna=True)
        result["q_worst_metric"] = quality_table.idxmin(axis=1, skipna=True)
    else:
        result["q_overall"] = np.nan
        result["q_mean"] = np.nan
        result["q_worst_metric"] = ""

    min_edge = numeric_series(result, "minEdge")
    max_edge = numeric_series(result, "maxEdge")
    if min_edge is not None and max_edge is not None:
        result["q_edge_ratio"] = np.where(
            max_edge > 0, (min_edge / max_edge).clip(0.0, 1.0), np.nan
        )
        result["aspect_ratio"] = np.where(
            min_edge > 0, max_edge / min_edge, np.nan
        )

    min_det = numeric_series(result, "minDetJac")
    max_det = numeric_series(result, "maxDetJac")
    if min_det is not None and max_det is not None:
        result["q_jacobian"] = np.where(
            max_det > 0, (min_det / max_det).clip(0.0, 1.0), np.nan
        )

    invalid = pd.Series(False, index=result.index)
    for field in ("minSICN", "minSJ", "minSIGE", "minDetJac"):
        values = numeric_series(result, field)
        if values is not None:
            invalid = invalid | (values <= 0)
    result["q_invalid"] = invalid.astype(int)

    try:
        result["element_area"] = result.geometry.area
    except Exception:
        pass
    return result


def color_symbol(layer, color):
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(QColor(color))
    try:
        symbol.symbolLayer(0).setStrokeColor(QColor(90, 90, 90, 90))
        symbol.symbolLayer(0).setStrokeWidth(0.08)
    except Exception:
        pass
    return symbol


QUALITY_COLORS = [
    "#a50026", "#d73027", "#f46d43", "#fee08b", "#a6d96a", "#1a9850"
]


def apply_fixed_graduated(layer, field, breaks, labels=None, reverse=False):
    colors = list(reversed(QUALITY_COLORS)) if reverse else QUALITY_COLORS
    ranges = []
    for index in range(len(breaks) - 1):
        lower, upper = breaks[index], breaks[index + 1]
        color_index = round(index * (len(colors) - 1) / max(1, len(breaks) - 2))
        label = (
            labels[index] if labels and index < len(labels)
            else f"{lower:g} – {upper:g}"
        )
        ranges.append(QgsRendererRange(
            lower, upper, color_symbol(layer, colors[color_index]), label
        ))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
    layer.triggerRepaint()


def interpolate_color(start, end, fraction):
    return QColor(
        round(start.red() + (end.red() - start.red()) * fraction),
        round(start.green() + (end.green() - start.green()) * fraction),
        round(start.blue() + (end.blue() - start.blue()) * fraction)
    )


def apply_dynamic_graduated(layer, field, class_count=5):
    field_index = layer.fields().indexFromName(field)
    if field_index < 0:
        return
    minimum = layer.minimumValue(field_index)
    maximum = layer.maximumValue(field_index)
    try:
        minimum, maximum = float(minimum), float(maximum)
    except (TypeError, ValueError):
        return
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        return
    if maximum <= minimum:
        maximum = minimum + 1.0

    start, end = QColor("#ffffcc"), QColor("#253494")
    ranges = []
    for index in range(class_count):
        lower = minimum + (maximum - minimum) * index / class_count
        upper = minimum + (maximum - minimum) * (index + 1) / class_count
        color = interpolate_color(start, end, index / max(1, class_count - 1))
        ranges.append(QgsRendererRange(
            lower, upper, color_symbol(layer, color), f"{lower:.3g} – {upper:.3g}"
        ))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
    layer.triggerRepaint()


def apply_problem_style(layer):
    categories = [
        QgsRendererCategory(
            0, color_symbol(layer, QColor(80, 170, 80, 35)), "Valid"
        ),
        QgsRendererCategory(
            1, color_symbol(layer, QColor(220, 30, 30, 220)),
            "Inverted or invalid"
        ),
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("q_invalid", categories))
    layer.triggerRepaint()


class VorflowDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vorflow – Mesh generation and quality control")
        self.resize(1020, 850)
        self.last_voronoi_grid = None
        self.last_voronoi_path = None
        self.last_output_directory = None
        self.last_prefix = None

        main = QVBoxLayout(self)
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        information = QLabel(
            "Add any number of point, line and polygon layers. "
            "Use global defaults and configure layer-specific overrides. "
            "The initial refinement profile uses Geometric growth, edge_ratio, "
            "growth factor 1.2 and sampling 25."
        )
        information.setWordWrap(True)
        help_button = QPushButton("Parameter help…")
        help_button.clicked.connect(self.show_parameter_help)
        top_layout.addWidget(information, 1)
        top_layout.addWidget(help_button)
        main.addWidget(top_row)

        tabs = QTabWidget()
        self.tabs = tabs
        main.addWidget(tabs, 1)

        geometry_tabs = QTabWidget()
        domain_tab = QWidget()
        domain_layout = QVBoxLayout(domain_tab)
        domain_info = QLabel(
            "The model domain must be a polygon layer. All valid polygons "
            "in the layer are merged into one domain geometry."
        )
        domain_info.setWordWrap(True)
        self.domain_defaults = {
            "zone_id": "domain", "resolution": 100.0, "z_order": -1000,
            "densify": True, "embed": True, "growth_factor": 1.2,
            "simplify_tolerance": 0.0, "field_model": "geometric",
            "growth_model": "edge_ratio", "sampling": 25
        }
        self.domain_source = LayerSourceWidget(
            QgsMapLayerProxyModel.PolygonLayer, "domain", None,
            direct_settings=True, defaults=self.domain_defaults
        )
        domain_layout.addWidget(domain_info)
        domain_layout.addWidget(self.domain_source)
        domain_layout.addStretch()

        self.points = MultiLayerInputWidget(
            "Point", QgsMapLayerProxyModel.PointLayer, "point",
            {
                "resolution": 10.0, "growth_factor": 1.2,
                "embed": True, "simplify_tolerance": 0.0,
                "field_model": "geometric", "growth_model": "edge_ratio",
                "sampling": 25
            }
        )
        self.lines = MultiLayerInputWidget(
            "Line", QgsMapLayerProxyModel.LineLayer, "line",
            {
                "resolution": 10.0, "growth_factor": 1.2,
                "snap_to_polygons": True, "is_barrier": False,
                "densify": True, "simplify_tolerance": 0.0,
                "embed": True, "quad_buffer": False,
                "quad_buffer_thickness": 1, "z_order": 0,
                "field_model": "geometric", "growth_model": "edge_ratio",
                "sampling": 25
            }
        )
        self.polygons = MultiLayerInputWidget(
            "Polygon", QgsMapLayerProxyModel.PolygonLayer, "polygon",
            {
                "resolution": 25.0, "growth_factor": 1.2,
                "z_order": 1, "densify": True,
                "simplify_tolerance": 0.0, "embed": True,
                "quad_buffer": False, "quad_buffer_thickness": 1,
                "field_model": "geometric", "growth_model": "edge_ratio",
                "sampling": 25
            }
        )

        geometry_tabs.addTab(domain_tab, "Model domain")
        geometry_tabs.addTab(self.points, "Point layers")
        geometry_tabs.addTab(self.lines, "Line layers")
        geometry_tabs.addTab(self.polygons, "Polygon layers")
        tabs.addTab(geometry_tabs, "Geometry and refinement")

        mesh_tab = QWidget()
        mesh_layout = QVBoxLayout(mesh_tab)

        conceptual_group = QGroupBox("Conceptual model")
        conceptual_form = QFormLayout(conceptual_group)
        self.connectivity_tolerance = make_double(0.001, 0.0, 1.0e12, 8)
        self.connectivity_tolerance.setToolTip(
            "Tolerance used to consider geometries connected. Choose a "
            "value that is small relative to the model scale."
        )
        conceptual_form.addRow(
            "Connectivity tolerance:",
            self.connectivity_tolerance
        )

        mesh_group = QGroupBox("Gmsh / MeshGenerator")
        mesh_form = QFormLayout(mesh_group)
        self.background_lc = make_double(100.0, 0.000001, 1.0e12, 4)
        self.background_lc.setToolTip(
            "Global background size where no finer object or mesh field controls the size."
        )
        self.verbosity = QComboBox()
        for level, description in (
            (0, "0 – silent"), (1, "1 – errors"), (2, "2 – warnings"),
            (3, "3 – information"), (4, "4 – detailed"), (5, "5 – debug")
        ):
            self.verbosity.addItem(description, level)
        self.verbosity.setCurrentIndex(1)
        mesh_form.addRow("Global background size:", self.background_lc)
        mesh_form.addRow("Log level:", self.verbosity)

        tess_group = QGroupBox("Voronoi tessellation")
        tess_form = QFormLayout(tess_group)
        self.clip_boundary = QCheckBox("Clip cells to the model-domain boundary")
        self.clip_boundary.setChecked(True)
        self.clip_boundary.setToolTip(
            "Removes or clips portions of Voronoi cells outside the domain."
        )
        tess_form.addRow(self.clip_boundary)

        notes = QLabel(
            "<b>Tip:</b> The target size is not an exact cell size. "
            "Overlapping objects, intersections, geometry vertices and "
            "growth fields can cause local variation."
        )
        notes.setWordWrap(True)

        mesh_layout.addWidget(conceptual_group)
        mesh_layout.addWidget(mesh_group)
        mesh_layout.addWidget(tess_group)
        mesh_layout.addWidget(notes)
        mesh_layout.addStretch()
        tabs.addTab(mesh_tab, "Global mesh parameters")

        output_tab = QWidget()
        output_form = QFormLayout(output_tab)
        output_row = QWidget()
        output_row_layout = QHBoxLayout(output_row)
        output_row_layout.setContentsMargins(0, 0, 0, 0)
        self.output_directory = QLineEdit()
        choose_output = QPushButton("Browse…")
        choose_output.clicked.connect(self.select_output)
        output_row_layout.addWidget(self.output_directory, 1)
        output_row_layout.addWidget(choose_output)

        self.output_name = QLineEdit("vorflow_grid")
        self.export_voronoi = QCheckBox()
        self.export_voronoi.setChecked(True)
        self.export_elements = QCheckBox()
        self.export_elements.setChecked(True)
        self.export_triangles = QCheckBox()
        self.export_quads = QCheckBox()
        self.export_quality = QCheckBox()
        self.export_quality.setChecked(True)
        self.add_outputs = QCheckBox()
        self.add_outputs.setChecked(True)
        self.quality_group = QCheckBox()
        self.quality_group.setChecked(True)
        self.quality_group.setToolTip(
            "Creates multiple styled views of the same quality data."
        )

        output_form.addRow("Output directory:", output_row)
        output_form.addRow("File prefix:", self.output_name)
        output_form.addRow("Voronoi grid:", self.export_voronoi)
        output_form.addRow("All elements:", self.export_elements)
        output_form.addRow("Triangles:", self.export_triangles)
        output_form.addRow("Quadrilaterals:", self.export_quads)
        output_form.addRow("Quality data:", self.export_quality)
        output_form.addRow("Add results to QGIS:", self.add_outputs)
        output_form.addRow("Create themed quality layer group:", self.quality_group)
        tabs.addTab(output_tab, "Output and quality")

        mf6_tab = QWidget()
        mf6_layout = QVBoxLayout(mf6_tab)
        mf6_info = QLabel(
            "After a Voronoi grid has been generated, it can be converted to "
            "MODFLOW 6 DISV. The export creates a DISV file, validation tables "
            "and a minimal MODFLOW 6 simulation that can be imported and "
            "extended in applications such as ModelMuse. Boundary conditions are not included."
        )
        mf6_info.setWordWrap(True)
        mf6_layout.addWidget(mf6_info)

        mf6_grid_group = QGroupBox("DISV geometry")
        mf6_grid_form = QFormLayout(mf6_grid_group)
        self.mf6_model_name = QLineEdit("vorflow_model")
        self.mf6_vertex_tolerance = make_double(0.001, 0.000000001, 1.0e6, 9)
        self.mf6_vertex_tolerance.setToolTip(
            "Vertices closer than the tolerance receive the same vertex ID. "
            "Choose a small value in the project's length unit."
        )
        self.mf6_length_units = QComboBox()
        self.mf6_length_units.addItem("Metres", "METERS")
        self.mf6_length_units.addItem("Feet", "FEET")
        self.mf6_length_units.addItem("Centimetres", "CENTIMETERS")
        self.mf6_length_units.addItem("Unknown", "UNKNOWN")
        mf6_grid_form.addRow("Model name:", self.mf6_model_name)
        mf6_grid_form.addRow("Vertex tolerance:", self.mf6_vertex_tolerance)
        mf6_grid_form.addRow("Length unit:", self.mf6_length_units)
        mf6_layout.addWidget(mf6_grid_group)

        mf6_vertical_group = QGroupBox("Vertical discretisation – constant elevations")
        mf6_vertical_form = QFormLayout(mf6_vertical_group)
        self.mf6_nlay = QSpinBox()
        self.mf6_nlay.setRange(1, 1000)
        self.mf6_nlay.setValue(1)
        self.mf6_top = make_double(0.0, -1.0e12, 1.0e12, 4)
        self.mf6_bottom = make_double(-10.0, -1.0e12, 1.0e12, 4)
        self.mf6_start_head = make_double(0.0, -1.0e12, 1.0e12, 4)
        self.mf6_hk = make_double(1.0, 0.0, 1.0e12, 8)
        mf6_vertical_form.addRow("Number of layers:", self.mf6_nlay)
        mf6_vertical_form.addRow("Top elevation, all cells:", self.mf6_top)
        mf6_vertical_form.addRow("Bottom elevation, lowest layer:", self.mf6_bottom)
        mf6_vertical_form.addRow("Starting head:", self.mf6_start_head)
        mf6_vertical_form.addRow("Hydraulic conductivity K:", self.mf6_hk)
        mf6_layout.addWidget(mf6_vertical_group)

        self.generate_disv_button = QPushButton("Generate DISV grid…")
        self.generate_disv_button.setEnabled(False)
        self.generate_disv_button.setToolTip(
            "First generate a mesh with the Voronoi grid option enabled."
        )
        self.generate_disv_button.clicked.connect(self.generate_disv_dataset)
        mf6_layout.addWidget(self.generate_disv_button)
        mf6_note = QLabel(
            "<b>Note:</b> The minimal model has no boundary conditions and is "
            "intended as an import and starting dataset. Complete the model in "
            "ModelMuse, FloPy or another MODFLOW 6 environment before simulation."
        )
        mf6_note.setWordWrap(True)
        mf6_layout.addWidget(mf6_note)
        mf6_layout.addStretch()
        tabs.addTab(mf6_tab, "MODFLOW 6 / DISV")

        self.status = QLabel("")
        self.status.setWordWrap(True)
        main.addWidget(self.status)

        buttons_row = QWidget()
        buttons_layout = QHBoxLayout(buttons_row)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        help_bottom = QPushButton("Help")
        help_bottom.clicked.connect(self.show_parameter_help)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("Generate mesh")
        buttons.accepted.connect(self.run_model)
        buttons.rejected.connect(self.reject)
        buttons_layout.addWidget(help_bottom)
        buttons_layout.addStretch()
        buttons_layout.addWidget(buttons)
        main.addWidget(buttons_row)

    def show_parameter_help(self):
        ParameterHelpDialog(self).exec_()

    def select_output(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select output directory", self.output_directory.text()
        )
        if directory:
            self.output_directory.setText(directory)

    def set_status(self, text):
        self.status.setText(text)
        QApplication.processEvents()

    def validate_crs(self):
        crs = QgsProject.instance().crs()
        if not crs.isValid():
            raise ValueError("The project does not have a valid CRS.")
        if crs.isGeographic():
            raise ValueError(
                "Use a projected CRS, such as SWEREF 99 TM (EPSG:3006)."
            )
        return crs

    def add_domain(self, blueprint):
        from shapely.ops import unary_union
        source = self.domain_source
        layer = source.layer()
        if layer is None:
            raise ValueError("Select a model domain.")

        geometries = []
        for feature in layer.getFeatures():
            geometry = transformed_geometry(feature, layer)
            if geometry is not None and not geometry.is_empty:
                geometries.append(geometry)
        if not geometries:
            raise ValueError("The model domain contains no valid geometry.")

        kwargs = source.merged_parameters({})
        zone_id = kwargs.pop("zone_id", "domain")
        call_supported(
            blueprint.add_polygon, unary_union(geometries),
            zone_id=zone_id, **kwargs
        )

    def add_sources(self, blueprint, manager, method_name, id_argument, prefix):
        method = getattr(blueprint, method_name)
        for source, layer, base_kwargs in manager.active_sources():
            id_field = source.selected_id_field()
            for feature in layer.getFeatures():
                geometry = transformed_geometry(feature, layer)
                if geometry is None or geometry.is_empty:
                    continue
                kwargs = dict(base_kwargs)
                kwargs[id_argument] = feature_identifier(
                    feature, id_field, prefix, layer.name()
                )
                call_supported(method, geometry, **kwargs)

    def export_gdf(self, gdf, path):
        if gdf is None or gdf.empty:
            return False
        if os.path.exists(path):
            os.remove(path)
        gdf.to_file(path, layer="grid", driver="GPKG")
        return True

    def new_layer(self, path, name):
        return QgsVectorLayer(f"{path}|layername=grid", name, "ogr")

    def add_standard_result(self, path, name, group):
        layer = self.new_layer(path, name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer, False)
            group.addLayer(layer)

    def quality_layer(self, path, name, group, field=None, style=None, visible=False):
        layer = self.new_layer(path, name)
        if not layer.isValid():
            return
        if field and layer.fields().indexFromName(field) < 0:
            return

        if style == "quality":
            apply_fixed_graduated(
                layer, field,
                [-1.000001, 0.0, 0.2, 0.4, 0.6, 0.8, 1.000001],
                [
                    "≤ 0: invalid/inverted", "0–0.2: very low",
                    "0.2–0.4: low", "0.4–0.6: moderate",
                    "0.6–0.8: good", "0.8–1: very good"
                ]
            )
        elif style == "ratio":
            apply_fixed_graduated(
                layer, field,
                [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.000001],
                [
                    "0–0.2: very elongated", "0.2–0.4: elongated",
                    "0.4–0.6: moderate", "0.6–0.8: good",
                    "0.8–0.9: very good", "0.9–1: near-equilateral"
                ]
            )
        elif style == "aspect":
            apply_fixed_graduated(
                layer, field,
                [0.999999, 1.25, 1.5, 2.0, 3.0, 5.0, 1.0e30],
                [
                    "1–1.25: very good", "1.25–1.5: good",
                    "1.5–2: acceptable", "2–3: elongated",
                    "3–5: poor", "> 5: very poor"
                ],
                reverse=True
            )
        elif style == "dynamic":
            apply_dynamic_graduated(layer, field)
        elif style == "problems":
            apply_problem_style(layer)

        QgsProject.instance().addMapLayer(layer, False)
        node = group.addLayer(layer)
        node.setItemVisibilityChecked(visible)

    def add_quality_group(self, path, root_group):
        quality_group = root_group.addGroup("Quality – triangles")
        quality_group.setExpanded(True)

        definitions = [
            ("01 Overall quality (worst metric)", "q_overall", "quality", True),
            ("02 Problem triangles", "q_invalid", "problems", True),
            ("03 Mean quality", "q_mean", "quality", False),
            ("04 SICN – inverse condition", "minSICN", "quality", False),
            ("05 Scaled Jacobian", "minSJ", "quality", False),
            ("06 SIGE – gradient error", "minSIGE", "quality", False),
            ("07 Isotropy", "minIsotropy", "quality", False),
            ("08 Angle shape", "angleShape", "quality", False),
            ("09 Gamma/radius shape", "gamma", "quality", False),
            ("10 Edge ratio min/max", "q_edge_ratio", "ratio", False),
            ("11 Aspect ratio max/min", "aspect_ratio", "aspect", False),
            ("12 Jacobian stability min/max", "q_jacobian", "ratio", False),
            ("13 Minimum edge length", "minEdge", "dynamic", False),
            ("14 Maximum edge length", "maxEdge", "dynamic", False),
            ("15 Element area", "element_area", "dynamic", False),
        ]
        for name, field, style, visible in definitions:
            self.quality_layer(
                path, name, quality_group, field, style, visible
            )

        raw = self.new_layer(path, "99 Quality data – all raw fields")
        if raw.isValid():
            QgsProject.instance().addMapLayer(raw, False)
            node = quality_group.addLayer(raw)
            node.setItemVisibilityChecked(False)

    def add_outputs_to_project(self, exported, quality_path, prefix):
        root = QgsProject.instance().layerTreeRoot()
        stamp = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        run_group = root.addGroup(f"Vorflow – {prefix} – {stamp}")
        result_group = run_group.addGroup("Results")

        for path, name, kind in exported:
            if kind != "quality":
                self.add_standard_result(path, name, result_group)

        if quality_path and self.quality_group.isChecked():
            self.add_quality_group(quality_path, run_group)
        elif quality_path:
            self.add_standard_result(
                quality_path, "Vorflow quality", result_group
            )
        run_group.setExpanded(True)

    @staticmethod
    def _signed_ring_area(coordinates):
        return 0.5 * sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(
                coordinates, coordinates[1:] + coordinates[:1]
            )
        )

    def build_disv_geometry(self, grid, tolerance):
        """Build MODFLOW 6 VERTICES and CELL2D from Voronoi polygons."""
        from shapely.geometry import Polygon, MultiPolygon

        if grid is None or grid.empty:
            raise ValueError("No generated Voronoi grid is available.")

        vertices = []
        vertex_lookup = {}
        cell2d = []
        cell_rows = []

        def vertex_id(x, y):
            key = (round(float(x) / tolerance), round(float(y) / tolerance))
            if key not in vertex_lookup:
                vertex_lookup[key] = len(vertices) + 1
                vertices.append((len(vertices) + 1, float(x), float(y)))
            return vertex_lookup[key]

        for row_number, (_, row) in enumerate(grid.iterrows(), start=1):
            geometry = row.geometry
            if geometry is None or geometry.is_empty:
                raise ValueError(f"Cell {row_number} has no geometry.")
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            if isinstance(geometry, MultiPolygon):
                if len(geometry.geoms) != 1:
                    raise ValueError(
                        f"Cell {row_number} consists of multiple separate polygons. "
                        "DISV export requires one contiguous polygon per cell."
                    )
                geometry = geometry.geoms[0]
            if not isinstance(geometry, Polygon):
                raise ValueError(
                    f"Cell {row_number} has geometry type "
                    f"{geometry.geom_type}; Polygon is required."
                )
            if geometry.interiors:
                raise ValueError(
                    f"Cell {row_number} contains holes. This is not supported in CELL2D."
                )

            coordinates = [(float(x), float(y)) for x, y, *rest
                           in list(geometry.exterior.coords)[:-1]]
            if len(coordinates) < 3:
                raise ValueError(f"Cell {row_number} has fewer than three vertices.")

            # MODFLOW 6 expects vertices in clockwise order.
            if self._signed_ring_area(coordinates) > 0:
                coordinates.reverse()

            ids = []
            for x, y in coordinates:
                vid = vertex_id(x, y)
                if not ids or ids[-1] != vid:
                    ids.append(vid)
            if len(ids) > 1 and ids[0] == ids[-1]:
                ids.pop()
            if len(set(ids)) < 3:
                raise ValueError(
                    f"Cell {row_number} collapses at the selected vertex tolerance."
                )

            center = geometry.centroid
            if not geometry.covers(center):
                center = geometry.representative_point()
            cell2d.append((
                row_number, float(center.x), float(center.y), len(ids), ids
            ))
            cell_rows.append((
                row_number, float(center.x), float(center.y),
                float(geometry.area), len(ids)
            ))

        return vertices, cell2d, cell_rows

    @staticmethod
    def _write_text(path, content):
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip() + "\n")

    def write_disv_dataset(
        self, directory, model_name, vertices, cell2d, cell_rows,
        nlay, top, bottom, start_head, hk, length_units, crs_authid
    ):
        os.makedirs(directory, exist_ok=True)
        ncpl = len(cell2d)
        nvert = len(vertices)
        layer_thickness = (top - bottom) / nlay
        bottoms = [top - layer_thickness * (layer + 1)
                   for layer in range(nlay)]

        disv_lines = [
            "# Created by the Vorflow QGIS plugin",
            f"# CRS: {crs_authid}",
            "BEGIN OPTIONS",
            f"  LENGTH_UNITS {length_units}",
            "END OPTIONS",
            "",
            "BEGIN DIMENSIONS",
            f"  NLAY {nlay}",
            f"  NCPL {ncpl}",
            f"  NVERT {nvert}",
            "END DIMENSIONS",
            "",
            "BEGIN GRIDDATA",
            "  TOP",
            f"    CONSTANT {top:.12g}",
            "  BOTM LAYERED",
        ]
        disv_lines.extend(f"    CONSTANT {value:.12g}" for value in bottoms)
        disv_lines.extend([
            "  IDOMAIN LAYERED",
            *[f"    CONSTANT 1" for _ in range(nlay)],
            "END GRIDDATA",
            "",
            "BEGIN VERTICES",
        ])
        disv_lines.extend(
            f"  {vid} {x:.15g} {y:.15g}" for vid, x, y in vertices
        )
        disv_lines.extend(["END VERTICES", "", "BEGIN CELL2D"])
        for cell_id, xc, yc, count, ids in cell2d:
            disv_lines.append(
                f"  {cell_id} {xc:.15g} {yc:.15g} {count} "
                + " ".join(str(value) for value in ids)
            )
        disv_lines.append("END CELL2D")
        self._write_text(
            os.path.join(directory, f"{model_name}.disv"),
            "\n".join(disv_lines)
        )

        self._write_text(
            os.path.join(directory, "mfsim.nam"),
            f"""BEGIN OPTIONS
END OPTIONS

BEGIN TIMING
  TDIS6 {model_name}.tdis
END TIMING

BEGIN MODELS
  GWF6 {model_name}.nam {model_name}
END MODELS

BEGIN SOLUTIONGROUP 1
  IMS6 {model_name}.ims {model_name}
END SOLUTIONGROUP"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.tdis"),
            """BEGIN OPTIONS
  TIME_UNITS DAYS
END OPTIONS

BEGIN DIMENSIONS
  NPER 1
END DIMENSIONS

BEGIN PERIODDATA
  1.0 1 1.0
END PERIODDATA"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.ims"),
            """BEGIN OPTIONS
  PRINT_OPTION SUMMARY
  COMPLEXITY SIMPLE
END OPTIONS

BEGIN NONLINEAR
  OUTER_DVCLOSE 1.0e-4
  OUTER_MAXIMUM 100
END NONLINEAR

BEGIN LINEAR
  INNER_DVCLOSE 1.0e-4
  INNER_MAXIMUM 100
  LINEAR_ACCELERATION BICGSTAB
END LINEAR"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.nam"),
            f"""BEGIN OPTIONS
  SAVE_FLOWS
END OPTIONS

BEGIN PACKAGES
  DISV6 {model_name}.disv disv
  IC6 {model_name}.ic ic
  NPF6 {model_name}.npf npf
  OC6 {model_name}.oc oc
END PACKAGES"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.ic"),
            f"""BEGIN GRIDDATA
  STRT
    CONSTANT {start_head:.12g}
END GRIDDATA"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.npf"),
            f"""BEGIN OPTIONS
  SAVE_SPECIFIC_DISCHARGE
END OPTIONS

BEGIN GRIDDATA
  ICELLTYPE
    CONSTANT 1
  K
    CONSTANT {hk:.12g}
END GRIDDATA"""
        )
        self._write_text(
            os.path.join(directory, f"{model_name}.oc"),
            f"""BEGIN OPTIONS
  BUDGET FILEOUT {model_name}.cbc
  HEAD FILEOUT {model_name}.hds
END OPTIONS

BEGIN PERIOD 1
  SAVE HEAD ALL
  SAVE BUDGET ALL
END PERIOD"""
        )

        with open(
            os.path.join(directory, f"{model_name}_vertices.csv"),
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["iv", "x", "y"])
            writer.writerows(vertices)

        with open(
            os.path.join(directory, f"{model_name}_cell2d.csv"),
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["icell2d", "xc", "yc", "area", "nvert"])
            writer.writerows(cell_rows)

        self._write_text(
            os.path.join(directory, "README_DISV.txt"),
            f"""MODFLOW 6 DISV export from the Vorflow QGIS plugin

Model: {model_name}
CRS: {crs_authid}
Number of layers: {nlay}
Cells per layer (NCPL): {ncpl}
Unique vertices (NVERT): {nvert}
Top elevation: {top}
Lowest bottom elevation: {bottom}

Open or import mfsim.nam in an application that supports MODFLOW 6 DISV.

The dataset contains DISV, TDIS, IMS, IC, NPF and OC, but no
boundary conditions, wells, recharge or storage package. It is therefore
an import and starting dataset, not a calibrated or
necessarily solvable groundwater model.

All cells have IDOMAIN = 1. TOP, BOTM, starting head and K are constant.
For multiple layers, layer boundaries are evenly distributed between TOP and the lowest BOTM.
"""
        )

    def generate_disv_dataset(self):
        try:
            if self.last_voronoi_grid is None:
                raise ValueError(
                    "First generate a mesh with the Voronoi grid option enabled."
                )
            top = self.mf6_top.value()
            bottom = self.mf6_bottom.value()
            if bottom >= top:
                raise ValueError("The lowest bottom elevation must be below the top elevation.")
            if self.mf6_hk.value() <= 0:
                raise ValueError("Hydraulic conductivity must be greater than 0.")

            default_parent = self.last_output_directory or ""
            model_name = safe_name(
                self.mf6_model_name.text().strip() or "vorflow_model"
            )
            default_directory = os.path.join(default_parent, f"{model_name}_mf6")
            directory = QFileDialog.getExistingDirectory(
                self, "Select directory for the MODFLOW 6 dataset", default_directory
            )
            if not directory:
                return
            # If the user selects the output directory, create a dedicated model directory.
            if os.path.abspath(directory) == os.path.abspath(default_parent):
                directory = default_directory

            self.set_status("Validating Voronoi cells and building DISV topology…")
            vertices, cell2d, cell_rows = self.build_disv_geometry(
                self.last_voronoi_grid, self.mf6_vertex_tolerance.value()
            )
            self.set_status("Writing MODFLOW 6 files…")
            self.write_disv_dataset(
                directory=directory,
                model_name=model_name,
                vertices=vertices,
                cell2d=cell2d,
                cell_rows=cell_rows,
                nlay=self.mf6_nlay.value(),
                top=top,
                bottom=bottom,
                start_head=self.mf6_start_head.value(),
                hk=self.mf6_hk.value(),
                length_units=self.mf6_length_units.currentData(),
                crs_authid=QgsProject.instance().crs().authid(),
            )
            self.set_status("DISV export completed.")
            QMessageBox.information(
                self, "MODFLOW 6 / DISV",
                "The DISV dataset has been created. Import mfsim.nam into a "
                "DISV-compatible MODFLOW 6 environment.\n\n"
                f"Directory: {directory}\n"
                f"Cells: {len(cell2d)}\n"
                f"Vertices: {len(vertices)}"
            )
        except Exception as exc:
            traceback.print_exc()
            self.set_status("DISV export failed.")
            QMessageBox.critical(self, "MODFLOW 6 / DISV – Error", str(exc))

    def run_model(self):
        try:
            self.last_voronoi_grid = None
            self.last_voronoi_path = None
            self.generate_disv_button.setEnabled(False)
            self.set_status("Checking Vorflow…")
            try:
                from vorflow import (
                    ConceptualMesh, MeshGenerator, VoronoiTessellator
                )
            except ImportError as exc:
                raise RuntimeError(
                    "The vorflow Python package is missing from the QGIS Python environment. "
                    "See README_installation.txt in the ZIP package."
                ) from exc

            crs = self.validate_crs()
            output_dir = self.output_directory.text().strip()
            if not output_dir:
                raise ValueError("Select an output directory.")
            os.makedirs(output_dir, exist_ok=True)

            prefix = safe_name(self.output_name.text().strip() or "vorflow_grid")
            background_lc = self.background_lc.value()
            connectivity = self.connectivity_tolerance.value()

            self.set_status("Building conceptual model…")
            blueprint = call_supported(
                ConceptualMesh, crs=crs.authid(),
                connectivity_tolerance=connectivity
            )
            self.add_domain(blueprint)
            self.add_sources(
                blueprint, self.polygons, "add_polygon", "zone_id", "zone"
            )
            self.add_sources(
                blueprint, self.lines, "add_line", "line_id", "line"
            )
            self.add_sources(
                blueprint, self.points, "add_point", "point_id", "point"
            )

            self.set_status("Cleaning and connecting geometries…")
            clean_polygons, clean_lines, clean_points = blueprint.generate()

            self.set_status("Generating mesh with Gmsh…")
            mesh_kwargs = {
                "background_lc": background_lc,
                "verbosity": self.verbosity.currentData(),
            }
            mesher = call_supported(MeshGenerator, **mesh_kwargs)
            mesher.generate(clean_polygons, clean_lines, clean_points)

            exported = []
            quality_path = None
            requests = []
            if self.export_elements.isChecked():
                requests.append(("elements", None))
            if self.export_triangles.isChecked():
                requests.append(("triangles", "triangles"))
            if self.export_quads.isChecked():
                requests.append(("quads", "quads"))

            for suffix, selection in requests:
                self.set_status(f"Exporting {suffix}…")
                grid = (
                    mesher.get_element_grid()
                    if selection is None
                    else mesher.get_element_grid(selection)
                )
                path = os.path.join(output_dir, f"{prefix}_{suffix}.gpkg")
                if self.export_gdf(grid, path):
                    exported.append((path, f"Vorflow {suffix}", suffix))

            if self.export_quality.isChecked():
                self.set_status("Calculating and exporting quality metrics…")
                quality = mesher.get_triangular_quality()
                triangles = mesher.get_element_grid("triangles")
                if (
                    quality is not None and not quality.empty
                    and triangles is not None and not triangles.empty
                ):
                    quality_grid = triangles.merge(
                        quality, on="element_tag", how="left",
                        suffixes=("", "_quality")
                    )
                    quality_grid = enrich_quality_grid(quality_grid)
                    quality_path = os.path.join(
                        output_dir, f"{prefix}_quality.gpkg"
                    )
                    if self.export_gdf(quality_grid, quality_path):
                        exported.append((
                            quality_path, "Vorflow quality", "quality"
                        ))

            if self.export_voronoi.isChecked():
                self.set_status("Creating Voronoi grid…")
                tess_kwargs = {
                    "clip_to_boundary": self.clip_boundary.isChecked()
                }
                tessellator = call_supported(
                    VoronoiTessellator, mesher, blueprint, **tess_kwargs
                )
                grid = tessellator.generate()
                path = os.path.join(output_dir, f"{prefix}_voronoi.gpkg")
                if self.export_gdf(grid, path):
                    exported.append((path, "Vorflow Voronoi", "voronoi"))
                    self.last_voronoi_grid = grid.copy()
                    self.last_voronoi_path = path
                    self.last_output_directory = output_dir
                    self.last_prefix = prefix
                    self.mf6_model_name.setText(f"{prefix}_model")
                    self.generate_disv_button.setEnabled(True)
                    self.generate_disv_button.setToolTip(
                        "Create a MODFLOW 6 DISV dataset from the most recently generated grid."
                    )

            if not exported:
                raise RuntimeError("No outputs were created.")

            if self.add_outputs.isChecked():
                self.set_status("Building layer group and quality styles…")
                self.add_outputs_to_project(exported, quality_path, prefix)

            self.set_status("Done.")
            QMessageBox.information(
                self, "Vorflow",
                "Mesh generation completed.\n\n"
                "The quality group uses red for low/invalid quality "
                "and green for high quality.\n\n"
                + "\n".join(path for path, _, _ in exported)
            )

        except Exception as exc:
            traceback.print_exc()
            self.set_status("Processing failed.")
            QMessageBox.critical(self, "Vorflow – Error", str(exc))


class VorflowPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(
            QIcon(icon_path), "Vorflow – Generate mesh", self.iface.mainWindow()
        )
        self.action.triggered.connect(self.show_dialog)
        self.iface.addPluginToVectorMenu("Vorflow", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginVectorMenu("Vorflow", self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()

    def show_dialog(self):
        self.dialog = VorflowDialog(self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
