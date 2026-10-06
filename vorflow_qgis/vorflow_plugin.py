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
import time

from qgis.PyQt.QtCore import QDateTime, QTimer
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QProgressBar, QScrollArea, QSpinBox, QStackedWidget, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget
)
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsCoordinateTransform,
    QgsGraduatedSymbolRenderer, QgsMapLayerProxyModel, QgsProject,
    QgsRendererCategory, QgsRendererRange, QgsSymbol, QgsVectorLayer,
    QgsMessageLog, Qgis
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


def _callable_parameter_info(function):
    """Return an inspectable signature and parameter names for diagnostics."""
    signature = inspect.signature(function)
    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    return signature, parameters, accepts_kwargs


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
    "smoothing_steps": (
        "Gmsh smoothing steps",
        "Number of Laplacian smoothing steps applied to the triangular Gmsh "
        "mesh. This can improve triangle shapes, but it is not Lloyd relaxation "
        "and normally does not centre Voronoi cells on their generators."
    ),
    "hex_ring": (
        "Hex ring around points",
        "Passes hex_ring=True to Blueprint.add_point(). Vorflow places six fixed "
        "nodes at the point resolution to create a regular hexagonal cell centred "
        "on the point. Vorflow may drop the ring, with a warning, if another "
        "feature is within twice the resolution or a finer size field reaches it."
    ),
    "lloyd_iterations": (
        "Weighted Lloyd iterations",
        "Moves free interior nodes towards size-weighted Voronoi-cell centroids "
        "before the grid is built. Boundary, zone-edge, point and line nodes stay "
        "fixed. A value around 20 is a useful starting point."
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
        resolution_label = (
            "Boundary resolution:" if geometry_kind == "domain" else "Resolution:"
        )
        self._add_control(
            basic_form, "resolution", resolution_label,
            make_double(defaults.get("resolution", 10.0), 0.000001),
        )
        if geometry_kind == "domain":
            self.controls["resolution"].setToolTip(
                "Target mesh size at the model-domain boundary. Away from the "
                "boundary, the mesh may grow to the Global background size."
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
        if geometry_kind == "point":
            self._add_control(
                basic_form, "hex_ring", "Hex ring:",
                self._checkbox(defaults.get("hex_ring", False)),
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
        if values.get("hex_ring"):
            parts.append("hex ring")
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

def _polygon_ring_metrics(geometry):
    """Return polygon-shape metrics used by the Voronoi-cell quality report."""
    import math
    if geometry is None or geometry.is_empty:
        return {}
    polygon = geometry
    if polygon.geom_type == "MultiPolygon":
        polygon = max(polygon.geoms, key=lambda item: item.area, default=None)
    if polygon is None or polygon.is_empty:
        return {}
    coords = list(polygon.exterior.coords)
    lengths = [
        math.hypot(x2 - x1, y2 - y1)
        for (x1, y1), (x2, y2) in zip(coords, coords[1:])
    ]
    lengths = [value for value in lengths if value > 0]
    angles = []
    for index, point in enumerate(coords[:-1]):
        previous = coords[index - 1]
        following = coords[(index + 1) % (len(coords) - 1)]
        v1 = (previous[0] - point[0], previous[1] - point[1])
        v2 = (following[0] - point[0], following[1] - point[1])
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 and n2:
            cosine = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
            angles.append(math.degrees(math.acos(cosine)))
    minx, miny, maxx, maxy = polygon.bounds
    width, height = maxx - minx, maxy - miny
    aspect = max(width, height) / min(width, height) if min(width, height) > 0 else float("nan")
    perimeter = polygon.length
    compactness = (
        4.0 * math.pi * polygon.area / (perimeter * perimeter)
        if perimeter > 0 else float("nan")
    )
    return {
        "cell_area": polygon.area,
        "cell_perimeter": perimeter,
        "cell_n_edges": len(lengths),
        "cell_min_edge": min(lengths, default=float("nan")),
        "cell_max_edge": max(lengths, default=float("nan")),
        "cell_edge_ratio": (
            min(lengths) / max(lengths) if lengths and max(lengths) > 0 else float("nan")
        ),
        "cell_aspect_ratio": aspect,
        "cell_compactness": compactness,
        "cell_min_angle": min(angles, default=float("nan")),
        "cell_max_angle": max(angles, default=float("nan")),
    }


def _shared_face_metrics(left, right):
    """Return face-normal/centroid metrics for one neighbouring cell pair."""
    import math
    face = left.boundary.intersection(right.boundary)
    if face.is_empty or face.length <= 0:
        return None
    c1, c2 = left.centroid, right.centroid
    dx, dy = c2.x - c1.x, c2.y - c1.y
    center_distance = math.hypot(dx, dy)
    if center_distance <= 0:
        return None
    midpoint = face.centroid
    # Distance from the common-face midpoint to the line joining the two cell centres.
    skew_distance = abs((midpoint.x - c1.x) * dy - (midpoint.y - c1.y) * dx) / center_distance
    # For a Voronoi dual this is close to one. This is a centroid-based
    # orthogonality diagnostic and is deliberately reported separately from
    # Vorflow's native generator-based diagnostics when available.
    # The normal to the face is obtained from the longest segment of the face.
    if face.geom_type == "MultiLineString":
        face = max(face.geoms, key=lambda item: item.length, default=None)
    coords = list(face.coords) if face is not None and hasattr(face, "coords") else []
    if len(coords) >= 2:
        fx, fy = coords[-1][0] - coords[0][0], coords[-1][1] - coords[0][1]
        flength = math.hypot(fx, fy)
        normal_alignment = abs(dx * (-fy) + dy * fx) / (center_distance * flength) if flength else float("nan")
    else:
        normal_alignment = float("nan")
    return {
        "face_length": face.length if face is not None else 0.0,
        "orthogonality": normal_alignment,
        "cvfd_skewness": 2.0 * skew_distance / center_distance,
    }


def build_voronoi_quality_grid(grid):
    """Build a cell-wise quality report for the generated Voronoi grid.

    The report is intentionally based on the exported Voronoi polygons, not on
    Gmsh/Delaunay elements.  It includes shape metrics and centroid-based
    CVFD/orthogonality diagnostics.  Native Vorflow diagnostics, if exposed by
    the installed version, can be merged by the caller in future versions.
    """
    import math
    import numpy as np
    import pandas as pd

    if grid is None or grid.empty:
        return grid
    result = grid.copy()
    shape_rows = [_polygon_ring_metrics(geometry) for geometry in result.geometry]
    shape_frame = pd.DataFrame(shape_rows, index=result.index)
    for column in shape_frame.columns:
        result[column] = shape_frame[column]

    orthogonality = [[] for _ in range(len(result))]
    skewness = [[] for _ in range(len(result))]
    neighbour_counts = [0 for _ in range(len(result))]
    geometries = list(result.geometry)
    index_by_position = {id(geometry): pos for pos, geometry in enumerate(geometries)}
    # GeoPandas' spatial index is available in normal QGIS installations.
    try:
        spatial_index = result.sindex
        query_candidates = lambda bounds: list(spatial_index.intersection(bounds))
    except Exception:
        query_candidates = lambda bounds: range(len(geometries))
    for position, geometry in enumerate(geometries):
        if geometry is None or geometry.is_empty:
            continue
        candidates = query_candidates(geometry.bounds)
        for candidate in candidates:
            if candidate <= position:
                continue
            other = geometries[candidate]
            metrics = _shared_face_metrics(geometry, other)
            if not metrics:
                continue
            neighbour_counts[position] += 1
            neighbour_counts[candidate] += 1
            for target, key in ((orthogonality, "orthogonality"), (skewness, "cvfd_skewness")):
                target[position].append(metrics[key])
                target[candidate].append(metrics[key])

    result["cell_neighbour_count"] = neighbour_counts
    result["orthogonality_min"] = [
        min(values) if values else float("nan") for values in orthogonality
    ]
    result["orthogonality_mean"] = [
        sum(values) / len(values) if values else float("nan") for values in orthogonality
    ]
    result["cvfd_skewness_max"] = [
        max(values) if values else float("nan") for values in skewness
    ]
    result["cvfd_skewness_mean"] = [
        sum(values) / len(values) if values else float("nan") for values in skewness
    ]

    high_is_good = {
        "cell_edge_ratio": result["cell_edge_ratio"],
        "cell_compactness": result["cell_compactness"],
        "orthogonality_min": result["orthogonality_min"],
    }
    quality_table = pd.DataFrame(high_is_good, index=result.index).clip(lower=0.0, upper=1.0)
    result["q_overall"] = quality_table.min(axis=1, skipna=True)
    result["q_mean"] = quality_table.mean(axis=1, skipna=True)
    result["q_worst_metric"] = quality_table.idxmin(axis=1, skipna=True)
    result["q_invalid"] = (
        ~result.geometry.is_valid
        | (pd.to_numeric(result["cell_area"], errors="coerce") <= 0)
        | (pd.to_numeric(result["cell_neighbour_count"], errors="coerce") < 1)
    ).astype(int)
    # A compactness of one is a circle; lower values indicate less compact cells.
    result["cell_centroid_x"] = result.geometry.centroid.x
    result["cell_centroid_y"] = result.geometry.centroid.y
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
        self.diagnostic_lines = []
        self.run_started = None
        self.run_timer = QTimer(self)
        self.run_timer.setInterval(250)
        self.run_timer.timeout.connect(self.update_elapsed_timer)

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
            "in the layer are merged into one domain geometry. Boundary "
            "resolution refines the domain edge; Global background size "
            "controls the general size inside the domain."
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
                "hex_ring": False,
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
        self.use_smoothing_override = QCheckBox("Override Vorflow default")
        self.use_smoothing_override.setChecked(False)
        self.use_smoothing_override.setToolTip(
            "Leave unchecked to use Vorflow's own Gmsh smoothing_steps default. "
            "This is Laplacian triangle smoothing, not Lloyd relaxation."
        )
        self.smoothing_steps = QSpinBox()
        self.smoothing_steps.setRange(0, 1000)
        self.smoothing_steps.setValue(10)
        self.smoothing_steps.setEnabled(False)
        self.smoothing_steps.setToolTip(
            PARAMETER_HELP["smoothing_steps"][1]
        )
        self.use_smoothing_override.toggled.connect(
            self.smoothing_steps.setEnabled
        )
        mesh_form.addRow("Global background size:", self.background_lc)
        mesh_form.addRow("Log level:", self.verbosity)
        mesh_form.addRow("Override Gmsh smoothing:", self.use_smoothing_override)
        mesh_form.addRow("Gmsh smoothing steps:", self.smoothing_steps)

        tess_group = QGroupBox("Voronoi tessellation")
        tess_form = QFormLayout(tess_group)
        self.clip_boundary = QCheckBox("Clip cells to the model-domain boundary")
        self.clip_boundary.setChecked(True)
        self.clip_boundary.setToolTip(
            "Removes or clips portions of Voronoi cells outside the domain."
        )
        tess_form.addRow(self.clip_boundary)

        self.use_lloyd = QCheckBox("Enable weighted Lloyd relaxation")
        self.use_lloyd.setChecked(False)
        self.use_lloyd.setToolTip(PARAMETER_HELP["lloyd_iterations"][1])
        self.lloyd_iterations = QSpinBox()
        self.lloyd_iterations.setRange(1, 1000)
        self.lloyd_iterations.setValue(20)
        self.lloyd_iterations.setEnabled(False)
        self.lloyd_iterations.setToolTip(PARAMETER_HELP["lloyd_iterations"][1])
        self.use_lloyd.toggled.connect(self.lloyd_iterations.setEnabled)
        tess_form.addRow(self.use_lloyd)
        tess_form.addRow("Lloyd iterations:", self.lloyd_iterations)

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
        output_form.addRow("Voronoi-cell quality data:", self.export_quality)
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

        diagnostics_group = QGroupBox("Run diagnostics")
        diagnostics_layout = QVBoxLayout(diagnostics_group)
        self.diagnostics = QTextBrowser()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMinimumHeight(110)
        self.diagnostics.setPlaceholderText(
            "The installed Vorflow API, hex-ring diagnostics and Lloyd report "
            "will be shown here after you start a run."
        )
        diagnostics_layout.addWidget(self.diagnostics)
        main.addWidget(diagnostics_group)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setToolTip(
            "Shows workflow phase progress. Gmsh and Vorflow currently do not "
            "provide fine-grained progress callbacks, so long internal phases "
            "are shown as indeterminate."
        )
        main.addWidget(self.progress)

        self.elapsed_time_label = QLabel("Total time: 00:00:00.0")
        self.elapsed_time_label.setToolTip(
            "Total elapsed time from clicking Generate mesh until completion or failure."
        )
        main.addWidget(self.elapsed_time_label)

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
        self.generate_button = buttons.button(QDialogButtonBox.Ok)
        self.generate_button.setText("Generate mesh")
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

    def add_diagnostic(self, text):
        """Show diagnostics in QGIS and in the dialog without hiding failures."""
        self.diagnostic_lines.append(str(text))
        if len(self.diagnostic_lines) > 80:
            self.diagnostic_lines = self.diagnostic_lines[-80:]
        if hasattr(self, "diagnostics"):
            self.diagnostics.setPlainText("\\n".join(self.diagnostic_lines))
            scrollbar = self.diagnostics.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        try:
            QgsMessageLog.logMessage(str(text), "Vorflow", Qgis.Info)
        except Exception:
            pass
        QApplication.processEvents()

    def set_status(self, text):
        self.status.setText(text)
        QApplication.processEvents()

    @staticmethod
    def format_elapsed_time(seconds):
        """Format elapsed seconds as HH:MM:SS.t."""
        seconds = max(0.0, float(seconds))
        total_seconds = int(seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, whole_seconds = divmod(remainder, 60)
        tenths = int((seconds - total_seconds) * 10)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{tenths}"

    def start_run_timer(self, started=None):
        """Start the total timer when Generate mesh is clicked."""
        self.run_started = started if started is not None else time.perf_counter()
        self.elapsed_time_label.setText("Total time: 00:00:00.0")
        self.run_timer.start()
        QApplication.processEvents()

    def update_elapsed_timer(self):
        """Refresh the displayed total time while a run is active."""
        if self.run_started is None:
            return
        elapsed = time.perf_counter() - self.run_started
        self.elapsed_time_label.setText(
            f"Total time: {self.format_elapsed_time(elapsed)}"
        )

    def stop_run_timer(self, elapsed=None):
        """Stop the timer and retain the final total duration."""
        self.run_timer.stop()
        if elapsed is None and self.run_started is not None:
            elapsed = time.perf_counter() - self.run_started
        if elapsed is not None:
            self.elapsed_time_label.setText(
                f"Total time: {self.format_elapsed_time(elapsed)}"
            )
        self.run_started = None

    def set_progress(self, value=None, text=None, busy=False):
        """Update phase progress without inventing an internal Gmsh percentage."""
        if busy:
            self.progress.setRange(0, 0)
            self.progress.setFormat(text or "Working…")
        else:
            self.progress.setRange(0, 100)
            if value is not None:
                self.progress.setValue(max(0, min(100, int(value))))
            self.progress.setFormat(f"%p% — {text}" if text else "%p%")
        if text:
            self.status.setText(text)
        self.update_elapsed_timer()
        QApplication.processEvents()

    @staticmethod
    def accepts_keyword(function, keyword):
        try:
            _, parameters, accepts_kwargs = _callable_parameter_info(function)
        except (TypeError, ValueError):
            return False
        return accepts_kwargs or keyword in parameters

    def validate_crs(self):
        crs = QgsProject.instance().crs()
        if not crs.isValid():
            raise ValueError("The project does not have a valid CRS.")
        if crs.isGeographic():
            raise ValueError(
                "Use a projected CRS, such as SWEREF 99 TM (EPSG:3006)."
            )
        return crs

    def add_domain(self, blueprint, background_lc):
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

        domain_geometry = unary_union(geometries)
        kwargs = source.merged_parameters({})
        zone_id = kwargs.pop("zone_id", "domain")

        # The domain polygon defines the mesh extent. Keep its interior at the
        # global background size instead of applying the boundary resolution
        # as a constant size field throughout the polygon.
        domain_kwargs = dict(kwargs)
        domain_kwargs["resolution"] = background_lc
        domain_kwargs.pop("fields", None)
        call_supported(
            blueprint.add_polygon, domain_geometry,
            zone_id=zone_id, **domain_kwargs
        )

        # Apply the configured domain resolution and growth field only to the
        # polygon boundary. The boundary is already part of the domain
        # topology, so this internal refinement line must not be embedded again.
        boundary_kwargs = dict(kwargs)
        boundary_kwargs["embed"] = False
        call_supported(
            blueprint.add_line, domain_geometry.boundary,
            line_id=f"{zone_id}::boundary", **boundary_kwargs
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
                if (
                    method_name == "add_point"
                    and kwargs.get("hex_ring")
                    and not self.accepts_keyword(method, "hex_ring")
                ):
                    raise RuntimeError(
                        "Hex ring is enabled, but the installed Vorflow does not "
                        "support add_point(..., hex_ring=True). Install a Vorflow "
                        "version containing Rui's point-centring change (#32)."
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
        quality_group = root_group.addGroup("Quality – Voronoi cells")
        quality_group.setExpanded(True)

        definitions = [
            ("01 Overall cell quality (worst metric)", "q_overall", "quality", True),
            ("02 Problem Voronoi cells", "q_invalid", "problems", True),
            ("03 Mean cell quality", "q_mean", "quality", False),
            ("04 Cell edge ratio min/max", "cell_edge_ratio", "ratio", False),
            ("05 Cell compactness", "cell_compactness", "quality", False),
            ("06 Cell aspect ratio", "cell_aspect_ratio", "aspect", False),
            ("07 Minimum interior angle", "cell_min_angle", "dynamic", False),
            ("08 Maximum interior angle", "cell_max_angle", "dynamic", False),
            ("09 Minimum edge length", "cell_min_edge", "dynamic", False),
            ("10 Maximum edge length", "cell_max_edge", "dynamic", False),
            ("11 Cell area", "cell_area", "dynamic", False),
            ("12 Number of neighbours", "cell_neighbour_count", "dynamic", False),
            ("13 Orthogonality (minimum)", "orthogonality_min", "quality", False),
            ("14 Orthogonality (mean)", "orthogonality_mean", "quality", False),
            ("15 CVFD skewness (maximum)", "cvfd_skewness_max", "dynamic", False),
            ("16 CVFD skewness (mean)", "cvfd_skewness_mean", "dynamic", False),
        ]
        for name, field, style, visible in definitions:
            self.quality_layer(path, name, quality_group, field, style, visible)

        raw = self.new_layer(path, "99 Voronoi quality data – all fields")
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
        started = time.perf_counter()
        self.start_run_timer(started)
        self.generate_button.setEnabled(False)
        self.diagnostic_lines = []
        self.diagnostics.clear()
        self.set_progress(2, "Checking Vorflow installation…")
        try:
            self.last_voronoi_grid = None
            self.last_voronoi_path = None
            self.generate_disv_button.setEnabled(False)
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

            self.set_progress(12, "Building conceptual model…")
            blueprint = call_supported(
                ConceptualMesh, crs=crs.authid(),
                connectivity_tolerance=connectivity
            )
            self.add_domain(blueprint, background_lc)
            self.add_sources(
                blueprint, self.polygons, "add_polygon", "zone_id", "zone"
            )
            self.add_sources(
                blueprint, self.lines, "add_line", "line_id", "line"
            )
            self.add_sources(
                blueprint, self.points, "add_point", "point_id", "point"
            )

            self.set_progress(None, "Cleaning and connecting geometries…", busy=True)
            clean_polygons, clean_lines, clean_points = blueprint.generate()
            self.set_progress(27, "Conceptual model completed.")

            self.set_progress(32, "Checking the MeshGenerator API…")
            smoothing_override = self.use_smoothing_override.isChecked()
            requested_smoothing = int(self.smoothing_steps.value())
            generator_signature, generator_parameters, generator_kwargs = (
                _callable_parameter_info(MeshGenerator)
            )
            self.add_diagnostic(
                f"MeshGenerator signature: {generator_signature}"
            )
            if smoothing_override:
                self.add_diagnostic(
                    f"Requested Gmsh smoothing_steps override: {requested_smoothing}"
                )
            else:
                self.add_diagnostic(
                    "Requested Gmsh smoothing_steps: Vorflow default "
                    "(no override supplied by QGIS plugin)."
                )

            common_mesh_kwargs = {
                "background_lc": background_lc,
                "verbosity": self.verbosity.currentData(),
            }
            smoothing_route = None
            constructor_kwargs = dict(common_mesh_kwargs)
            if smoothing_override and "smoothing_steps" in generator_parameters:
                constructor_kwargs["smoothing_steps"] = requested_smoothing
                smoothing_route = "MeshGenerator constructor"
            elif smoothing_override and generator_kwargs:
                # A **kwargs constructor accepts the value, although the
                # installed package does not expose it explicitly.
                constructor_kwargs["smoothing_steps"] = requested_smoothing
                smoothing_route = "MeshGenerator constructor (**kwargs)"
            elif not smoothing_override:
                if "smoothing_steps" in generator_parameters:
                    smoothing_route = "Vorflow default (MeshGenerator constructor)"
                elif generator_kwargs:
                    smoothing_route = "Vorflow default (MeshGenerator constructor; **kwargs)"
                else:
                    smoothing_route = "Vorflow default (parameter not exposed)"
            else:
                self.add_diagnostic(
                    "MeshGenerator does not expose smoothing_steps in its "
                    "constructor; checking generate()."
                )

            mesher = call_supported(MeshGenerator, **constructor_kwargs)
            generate_signature, generate_parameters, generate_kwargs = (
                _callable_parameter_info(mesher.generate)
            )
            self.add_diagnostic(f"mesher.generate signature: {generate_signature}")

            generate_options = {}
            if smoothing_override and smoothing_route is None:
                if "smoothing_steps" in generate_parameters:
                    generate_options["smoothing_steps"] = requested_smoothing
                    smoothing_route = "mesher.generate"
                elif generate_kwargs:
                    generate_options["smoothing_steps"] = requested_smoothing
                    smoothing_route = "mesher.generate (**kwargs)"

            if smoothing_override and smoothing_route is None:
                message = (
                    "The installed Vorflow API does not expose smoothing_steps "
                    "in MeshGenerator(...) or mesher.generate(...). "
                    "The requested Gmsh smoothing setting cannot be applied."
                )
                self.add_diagnostic("ERROR: " + message)
                raise RuntimeError(message)

            if smoothing_override:
                self.add_diagnostic(
                    f"Gmsh smoothing routing: {smoothing_route}; "
                    f"effective requested value: {requested_smoothing}"
                )
                if requested_smoothing == 0:
                    self.add_diagnostic(
                        "Gmsh Laplacian smoothing is explicitly disabled for this run (0)."
                    )
                else:
                    self.add_diagnostic(
                        f"Gmsh Laplacian smoothing override requested with "
                        f"{requested_smoothing} iteration(s)."
                    )
                status_smoothing = str(requested_smoothing)
            else:
                self.add_diagnostic(
                    f"Gmsh smoothing routing: {smoothing_route}; "
                    "effective value: Vorflow default"
                )
                self.add_diagnostic(
                    "Vorflow's own Gmsh smoothing_steps default is being used; "
                    "no value was supplied by the QGIS plugin."
                )
                status_smoothing = "Vorflow default"

            self.set_progress(
                None,
                f"Generating mesh with Gmsh "
                f"(Laplacian smoothing: {status_smoothing})…",
                busy=True,
            )
            mesher.generate(
                clean_polygons, clean_lines, clean_points, **generate_options
            )
            self.set_progress(55, "Gmsh mesh generation completed.")

            mesh_diagnostics = getattr(mesher, "diagnostics", None)
            if isinstance(mesh_diagnostics, dict):
                hex_diagnostics = mesh_diagnostics.get("hex_rings")
                if hex_diagnostics is not None:
                    self.add_diagnostic(
                        "Hex-ring diagnostics: " + repr(hex_diagnostics)
                    )
                else:
                    self.add_diagnostic(
                        "Hex-ring diagnostics were not present in mesher.diagnostics."
                    )
            else:
                self.add_diagnostic(
                    "The installed Vorflow does not expose mesher.diagnostics."
                )

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
                self.set_progress(62, f"Exporting {suffix}…")
                grid = (
                    mesher.get_element_grid()
                    if selection is None
                    else mesher.get_element_grid(selection)
                )
                path = os.path.join(output_dir, f"{prefix}_{suffix}.gpkg")
                if self.export_gdf(grid, path):
                    exported.append((path, f"Vorflow {suffix}", suffix))

            # The Voronoi grid is now the authoritative quality grid. Generate it
            # whenever either the Voronoi output or the quality report is requested.
            voronoi_grid = None
            if self.export_voronoi.isChecked() or self.export_quality.isChecked():
                lloyd_count = (
                    int(self.lloyd_iterations.value())
                    if self.use_lloyd.isChecked()
                    else 0
                )
                tess_kwargs = {
                    "clip_to_boundary": self.clip_boundary.isChecked()
                }
                if lloyd_count:
                    if not self.accepts_keyword(
                        VoronoiTessellator, "lloyd_iterations"
                    ):
                        raise RuntimeError(
                            "Weighted Lloyd relaxation is enabled, but the installed "
                            "Vorflow does not support VoronoiTessellator(..., "
                            "lloyd_iterations=N). Install a Vorflow version containing "
                            "Rui's point-centring change (#32)."
                        )
                    tess_kwargs["lloyd_iterations"] = lloyd_count
                    self.add_diagnostic(
                        f"Weighted Lloyd iterations requested: {lloyd_count}"
                    )
                    tess_status = (
                        f"Running {lloyd_count} weighted Lloyd iterations and "
                        "creating the Voronoi grid…"
                    )
                else:
                    self.add_diagnostic("Weighted Lloyd relaxation: disabled.")
                    tess_status = "Creating the Voronoi grid…"

                self.set_progress(None, tess_status, busy=True)
                tessellator = call_supported(
                    VoronoiTessellator, mesher, blueprint, **tess_kwargs
                )
                voronoi_grid = tessellator.generate()
                lloyd_report = getattr(tessellator, "lloyd_report", None)
                if lloyd_report is not None:
                    self.add_diagnostic("Lloyd report: " + repr(lloyd_report))
                elif lloyd_count:
                    self.add_diagnostic(
                        "WARNING: Lloyd was requested, but tessellator.lloyd_report "
                        "was not exposed by the installed Vorflow."
                    )
                self.set_progress(80, "Voronoi grid completed.")
                self.last_voronoi_grid = voronoi_grid.copy()
                self.last_output_directory = output_dir
                self.last_prefix = prefix
                self.mf6_model_name.setText(f"{prefix}_model")
                self.generate_disv_button.setEnabled(True)
                self.generate_disv_button.setToolTip(
                    "Create a MODFLOW 6 DISV dataset from the most recently generated grid."
                )

                if self.export_voronoi.isChecked():
                    path = os.path.join(output_dir, f"{prefix}_voronoi.gpkg")
                    if self.export_gdf(voronoi_grid, path):
                        exported.append((path, "Vorflow Voronoi", "voronoi"))
                    self.last_voronoi_path = path

                if self.export_quality.isChecked():
                    self.set_progress(86, "Calculating Voronoi-cell quality metrics…")
                    quality_grid = build_voronoi_quality_grid(voronoi_grid)
                    quality_path = os.path.join(output_dir, f"{prefix}_quality.gpkg")
                    if self.export_gdf(quality_grid, quality_path):
                        exported.append((
                            quality_path, "Vorflow Voronoi quality", "quality"
                        ))

            if not exported:
                raise RuntimeError("No outputs were created.")

            if self.add_outputs.isChecked():
                self.set_progress(94, "Building layer group and quality styles…")
                self.add_outputs_to_project(exported, quality_path, prefix)

            elapsed = time.perf_counter() - started
            self.stop_run_timer(elapsed)
            self.set_progress(100, f"Done in {elapsed:.1f} seconds.")
            QMessageBox.information(
                self, "Vorflow",
                f"Mesh generation completed in {elapsed:.1f} seconds.\n\n"
                "The quality group uses red for low/invalid quality "
                "and green for high quality.\n\n"
                + "\n".join(path for path, _, _ in exported)
            )

        except Exception as exc:
            traceback.print_exc()
            elapsed = time.perf_counter() - started
            self.stop_run_timer(elapsed)
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.setFormat("Failed")
            self.set_status(f"Processing failed after {elapsed:.1f} seconds.")
            QMessageBox.critical(self, "Vorflow – Error", str(exc))
        finally:
            if self.run_timer.isActive():
                self.stop_run_timer()
            self.generate_button.setEnabled(True)


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
