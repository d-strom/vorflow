# Vorflow junction patch – installation

This archive contains two coordinated deliverables:

- `vorflow_source/`: patched Vorflow source package, version 0.2.1;
- `vorflow_qgis_0.16.0.zip`: matching QGIS plugin package.

The patch starts from the latest supplied branch:
`vorflow-feat-lloyd-size-smoothing`.

## 1. Install the patched Vorflow package

Use the Python environment that QGIS actually uses. On Windows with QGIS,
run the commands from an OSGeo4W Shell or the QGIS Python environment.

After extracting this archive:

```bash
cd vorflow_source
python -m pip install --no-deps --force-reinstall .
```

The `--no-deps` option is intentional: it prevents replacing the geospatial
stack already used by QGIS. It assumes that the current environment already
has the dependencies required by Vorflow (`numpy`, `pandas`, `geopandas`,
`shapely`, `scipy`, and `gmsh`).

Verify the package in that same environment:

```bash
python -c "import vorflow; print(vorflow.__version__); print(vorflow.__file__)"
```

The path printed by the command must point to the patched installation.

If QGIS uses a different Python interpreter than the `python` command in
your shell, use that interpreter explicitly, for example:

```bash
"C:\path\to\qgis\bin\python-qgis.bat" -m pip install --no-deps --force-reinstall .
```

Do not install the package into an unrelated system Python.

## 2. Install the matching QGIS plugin

In QGIS:

1. `Plugins` -> `Manage and Install Plugins...`
2. Select `Install from ZIP`
3. Select `vorflow_qgis_0.16.0.zip`
4. Confirm the installation.
5. Restart QGIS completely.

You do not need to install a second QGIS installation. You should install this
plugin version over the previous Vorflow plugin version. If QGIS refuses to
replace it, remove the old `vorflow_qgis` plugin from the QGIS profile and
then install the ZIP.

## 3. Configure the new controls

The plugin adds a `Conceptual model` section containing:

- `Normalise embedded line junctions` — enabled by default;
- `Line-junction tolerance` — default equals the connectivity tolerance.

Start with a projected CRS in metres and:

```text
Connectivity tolerance: 0.001
Line-junction tolerance: 0.001
Normalise embedded line junctions: enabled
Lloyd iterations: 25 or 100
Minimum face fraction: 0.2
```

Use `embed=False` for lines that are only refinement indicators. Keep
`embed=True` only when the line must be part of the mesh topology.

## 4. Verify that the feature is active

The plugin's diagnostics should contain a message like:

```text
Embedded line-junction normalisation: enabled; 1 junction(s),
1 exact pair(s) noded, 0 endpoint connection(s).
```

If it reports that the feature is omitted for compatibility, the Python
environment used by QGIS is still loading the old Vorflow package.

## 5. What the patch does

The patch runs inside Vorflow before Gmsh:

- exact embedded line crossings receive explicit shared vertices;
- nearby endpoint-to-line connections are repaired within the junction tolerance;
- field-only lines are left untouched;
- nearby parallel lines are not merged;
- no Voronoi-cell postprocessing is performed in QGIS.

The patch does not guarantee that an embedded crossing becomes one large cell:
an embedded line network is still a mesh constraint and can legitimately create
multiple cells around a junction.

## 6. Rollback

To roll back the Python package:

```bash
python -m pip uninstall vorflow
```

Then reinstall the previous pinned version or branch.

In QGIS, install the previous plugin ZIP, or remove the plugin through
`Plugins` -> `Manage and Install Plugins...`.
