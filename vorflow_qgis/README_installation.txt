VORFLOW FOR QGIS 0.17.0
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

For the matching Vorflow features on this development branch, open OSGeo4W
Shell on Windows and run:

    python -m pip install geopandas shapely scipy gmsh pyogrio
    python -m pip install --no-deps --force-reinstall "vorflow @ git+https://github.com/d-strom/vorflow.git@dev"

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


QUALITY-ORIENTED DEFAULT PROFILE IN VERSION 0.17
- Refinement model: Geometric growth (GeometricGrowthField)
- Growth model: continuous_metric
- Growth factor: 1.1 for points, lines, polygons and optional domain-boundary refinement
- Model-domain boundary refinement: disabled unless explicitly enabled
- Point hex rings: enabled
- Boundary-cell centring: inset_mirror, inset fraction 0.25
- Weighted Lloyd iterations: 100
- Lloyd damping / tolerance / size smoothing: 1.0 / 0.001 / 5
- Minimum face fraction: 0.2
- Sampling: 25

The continuous-metric interpretation uses log(growth_factor), making 1.1 about
4.7% gentler than the legacy edge-ratio interpretation. Hex rings create
regular cells around point features such as wells. Inset/mirror centring
improves typical boundary orthogonality. Lloyd improves grid-wide regularity,
and the minimum face fraction opens very short faces after Lloyd without moving
fixed well, line or boundary generators. Individual layers can override the
feature settings.

The model domain normally defines extent only. Enable "Refine model-domain
boundary" when the outer boundary itself needs a smaller target size than the
global background. Only then does the domain growth control grade that finer
boundary size into the model interior.

REFINEMENT TERMINOLOGY
- Geometric growth: GeometricGrowthField with selectable continuous_metric or legacy edge_ratio convention.
- Threshold: ThresholdField with size_min, size_max, dist_min and dist_max.
- Exponential: ExponentialField with size_min, size_max and decay_length.
- Sampling: number of samples used to represent the mesh field.
- Densification spacing: optional geometry-segment limit; 0 uses resolution.
- Barrier straddle width: optional paired-generator spacing; 0 uses Vorflow automatic sizing.

Different input layers may use different refinement models in the same mesh,
subject to support in the installed Vorflow version.

See DEFAULTS_AUDIT.md for the complete parameter review, default rationale and
the low-level options that intentionally remain automatic.

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
