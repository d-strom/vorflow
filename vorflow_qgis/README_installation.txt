VORFLOW FOR QGIS 0.16.0
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

Until the short-face feature is included in a Vorflow release, open OSGeo4W
Shell on Windows and run:

    python -m pip install geopandas shapely scipy gmsh pyogrio
    python -m pip install --no-deps --force-reinstall "vorflow @ git+https://github.com/rhugman/vorflow.git@feat/lloyd-size-smoothing"

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


RECOMMENDED DEFAULT PROFILE IN VERSION 0.15
- Refinement model: Geometric growth (GeometricGrowthField)
- Growth model: edge_ratio
- Point and line growth factor: 1.1
- Point hex rings: enabled
- Weighted Lloyd iterations: 100
- Minimum face fraction: 0.2
- Sampling: 25

The gentler 1.1 grading is preselected for point and line layers; the domain
and polygon defaults remain 1.2. Hex rings create regular cells around point
features such as wells. Lloyd improves grid-wide regularity, and the minimum
face fraction opens very short faces after Lloyd without moving fixed well,
line or boundary generators. All settings remain editable, and individual
layers can override or disable the defaults.

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


EMBEDDED LINE JUNCTION NORMALISATION
------------------------------------
Version 0.16 adds two conceptual-model controls:

- Normalise embedded line junctions (enabled by default)
- Line-junction tolerance (default: the connectivity tolerance)

The QGIS plugin passes these values to Vorflow. Vorflow then makes exact
embedded line crossings explicit as shared line vertices and connects nearby
line endpoints within the junction tolerance. Field-only lines (`embed=False`)
are not modified, and nearby parallel lines are not merged. QGIS performs no
geometry pre-processing for this feature.

The new controls require a Vorflow package that supports
`junction_tolerance` and `normalize_line_junctions`. With an older Vorflow
version, the plugin's compatibility filter safely omits the new arguments,
but junction normalisation will not be active.

GMSH SMOOTHING, WEIGHTED LLOYD AND SHORT FACES
Gmsh `smoothing_steps` performs Laplacian smoothing of the auxiliary triangle
mesh. The plugin leaves Vorflow's default unchanged unless "Override Gmsh
smoothing" is enabled.

Weighted Lloyd relaxation is a separate VoronoiTessellator operation. It moves
only free generators towards size-weighted cell centroids; wells, lines, zone
edges and boundaries remain fixed. Lloyd is mainly a shape/appearance tool.
For MODFLOW results, local resolution, gentle grading and well-cell centring
normally matter more.

Lloyd can create a small number of nearly zero-length faces where, for example,
five- and seven-sided cells meet. `min_face_fraction=0.2` moves nearby free
generators slightly until those faces open. The face remains perpendicular to
the generator connector, and fixed generators do not move.


Required Vorflow support
------------------------
The plugin interface exposes the upstream point-centring and short-face APIs:

    blueprint.add_point(..., hex_ring=True)
    tess = VoronoiTessellator(
        mesher,
        blueprint,
        lloyd_iterations=100,
        min_face_fraction=0.2,
    )
    grid = tess.generate()
    print(tess.short_face_report)

The algorithms themselves belong to the upstream ``vorflow`` package, not
to this QGIS plugin. The plugin checks the installed API and stops with a
clear error rather than silently ignoring an unsupported option.

For testing the feature branch in a fresh environment:

    python -m venv vorflow-pr
    vorflow-pr\Scripts\activate
    pip install "vorflow @ git+https://github.com/rhugman/vorflow.git@feat/lloyd-size-smoothing"

If QGIS uses a different Python interpreter, install the package into that
exact interpreter/environment. Restart QGIS after installation.

Progress reporting
------------------
The progress bar reports completed workflow phases. During Gmsh generation
and weighted Lloyd relaxation it switches to an indeterminate busy state.
The upstream API currently provides no per-element callback, so an honest
internal percentage or safe Cancel button cannot be provided by the plugin.
