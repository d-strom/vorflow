VORFLOW FOR QGIS 0.14.0
======================

AUTHOR
David Ström

PURPOSE
This QGIS plugin provides a graphical interface for Vorflow/Gmsh mesh
generation, Voronoi-cell quality reporting, Voronoi tessellation, and export of a minimal
MODFLOW 6 DISV dataset.

The plugin is an independent integration. Vorflow remains a separate upstream
Python dependency and is credited in ATTRIBUTION.md.

INSTALLATION
1. In QGIS, open:
   Plugins > Manage and Install Plugins > Install from ZIP.
2. Select the plugin ZIP file.
3. Install Vorflow and its Python dependencies in the Python environment used
   by QGIS.

On Windows, open OSGeo4W Shell and run:

    python -m pip install vorflow geopandas shapely scipy gmsh pyogrio

4. Restart QGIS.

MAIN WORKFLOW
1. Select a polygon layer as the model domain.
2. Add any point, line and polygon refinement layers.
3. Configure global defaults and optional overrides for individual layers.
4. Choose output products under "Output and quality".
5. Click "Generate mesh". The progress bar shows the current phase and the
   total timer measures the complete run from click to completion.
6. If a Voronoi grid was generated, open "MODFLOW 6 / DISV" and click
   "Generate DISV grid..." to create a minimal MODFLOW 6 dataset.


RECOMMENDED DEFAULT PROFILE IN VERSION 0.6
- Refinement model: Geometric growth (GeometricGrowthField)
- Growth model: edge_ratio
- Growth factor: 1.2
- Sampling: 25

This profile is preselected for the model domain and global point, line and
polygon settings. It is intended as a robust general starting point with a
controlled transition between fine and coarse cells. All settings remain
editable, and individual layers can still override or disable the global
refinement model.

REFINEMENT TERMINOLOGY
- Standard: target size/resolution and growth factor.
- Geometric growth: GeometricGrowthField.
- Threshold: ThresholdField with size_min, size_max, dist_min and dist_max.
- Exponential: ExponentialField with size_min, size_max and decay_length.
- Sampling: number of samples used to represent the mesh field.
- edge_ratio and continuous_metric retain Vorflow's parameter names.

Different input layers may use different refinement models in the same mesh,
subject to support in the installed Vorflow version.

MODFLOW 6 / DISV EXPORT
The exporter creates:

    mfsim.nam
    <model>.nam
    <model>.disv
    <model>.tdis
    <model>.ims
    <model>.ic
    <model>.npf
    <model>.oc
    <model>_vertices.csv
    <model>_cell2d.csv
    README_DISV.txt

The export is a minimal starting dataset. It does not include boundary
conditions, wells, recharge or storage packages. TOP, BOTM, starting head and
hydraulic conductivity are constant in this version. Review all geometry and
model data before simulation.

ICON
The included icon is original plugin artwork based on a Voronoi-cell motif.
It is not copied from the upstream Vorflow repository. This avoids reusing
third-party artwork without confirmed permission. If the upstream project
provides a logo under a compatible licence, icon.png can be replaced while
retaining the filename.

SOURCE CONTROL
See GITHUB_PUBLISHING.md for commands to publish this plugin to a GitHub fork.


VORONOI-CELL QUALITY REPORTING
The quality output is generated from the exported Voronoi cells, not from the
underlying Delaunay/Gmsh triangles. The report includes cell area, perimeter,
edge lengths and ratios, aspect ratio, compactness, interior angles, neighbour
count, centroid-based orthogonality, and CVFD-skewness diagnostics. The
quality GeoPackage contains one feature per Voronoi cell and the QGIS quality
group provides themed views of the principal metrics.


LLOYD SMOOTHING
The "Global mesh parameters" tab contains "Lloyd smoothing steps". This is
passed centrally to Vorflow's MeshGenerator and then to Gmsh during mesh
generation. The default is 0, which disables smoothing. Increase it to apply
internal Lloyd smoothing iterations; embedded and constrained geometry nodes
may remain constrained by the mesh geometry.


Lloyd smoothing default behaviour
---------------------------------
By default, the plugin does not pass `smoothing_steps` to Vorflow. This preserves
Vorflow's own default and avoids changing upstream behaviour. Enable
"Override Vorflow default" to pass an explicit value, including 0 to disable
smoothing for that run.


Required Vorflow support for hex rings and weighted Lloyd
----------------------------------------------------------
The plugin interface exposes Rui's point-centring API:

    blueprint.add_point(..., hex_ring=True)
    VoronoiTessellator(..., lloyd_iterations=20)

The algorithms themselves belong to the upstream ``vorflow`` package, not
to this QGIS plugin. The plugin checks the installed API and stops with a
clear error rather than silently ignoring an unsupported option.

For testing Rui's feature branch in a fresh environment:

    python -m venv vorflow-pr
    vorflow-pr\Scripts\activate
    pip install "vorflow @ git+https://github.com/rhugman/vorflow.git@feat/point-centring"

If QGIS uses a different Python interpreter, install the package into that
exact interpreter/environment. Restart QGIS after installation.

Progress reporting
------------------
The progress bar reports completed workflow phases. During Gmsh generation
and weighted Lloyd relaxation it switches to an indeterminate busy state.
The upstream API currently provides no per-element callback, so an honest
internal percentage or safe Cancel button cannot be provided by the plugin.
