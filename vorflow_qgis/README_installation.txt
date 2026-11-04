VORFLOW FOR QGIS 0.19.1
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

For the matching Vorflow build, open OSGeo4W Shell on Windows and run:

    python -m pip install --upgrade "https://github.com/rhugman/vorflow/archive/63d1f6e811b4fbfb7fdefe51c513dfc1e67fbba3.zip" pyogrio

This develop build reports 0.3.0, like the earlier PyPI release. Use the
pinned build for the new APIs. If pip skips replacing an installed 0.3.0,
repeat with --force-reinstall --no-deps once dependencies are present.

4. Restart QGIS.

MAIN WORKFLOW
1. Select a polygon layer as the model domain.
2. Add any point, line and polygon refinement layers.
3. Configure global defaults and optional overrides for individual layers.
   Optional selectors start empty. Drag the horizontal divider above the
   input list to enlarge it. Click Run diagnostics to show or hide the log;
   when open, drag its upper divider to adjust its height.
4. Choose output products under "Output and quality".
5. Click "Generate mesh". The progress bar shows the current phase and the
   total timer continues counting during calculations and exports. It shows
   seconds, min/s from one minute, and h/min/s from one hour.
6. If a Voronoi grid was generated, open "MODFLOW 6 / DISV" and click
   "Generate DISV grid..." to create a minimal MODFLOW 6 dataset.

POLYGON REFINEMENT AND MODEL ZONES
Polygon input layers control cell size by default. In overlaps, Vorflow
uses the finest applicable local size field. These polygons do not add
mesh edges or assign zone IDs.

Enable "Create model zone" in the polygon defaults or as a layer override
when the model needs a separate zone ID or a meshed polygon outline.
Zone priority controls ownership where these polygons overlap. Clip zone
inputs to the model domain before meshing. Use "Create model zone" when
requesting a polygon quad corridor.

The polygon selected under Model domain defines the outer model boundary.

QUALITY-ORIENTED DEFAULT PROFILE IN VERSION 0.19
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
- Embedded line-junction spacing fraction: 0.5 (0 disables normalisation)
- Snap to polygons: off unless an intentional connection is needed

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
- Treat as barrier: Vorflow places cell faces along the line automatically.

Geometric growth has one effective factor under Refinement model. A layer
override replaces its inherited model and all of that model's values. There
is no separate Basic factor.

SNAP TO POLYGONS
Uses vertices on the cleaned boundaries of the model domain and selected,
enabled, embedded polygons. Refinement-only polygons, unselected project
layers and generated mesh cells are not targets. A snap can insert a vertex
within Connectivity tolerance anywhere along a line and leave a short
segment or kink. Use Check input geometry before meshing to find problem
locations in Run diagnostics. See MESHING_REVIEW.md.

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

DISV geometry uses upstream grid_to_disv(): cell centres are Vorflow generator
x/y coordinates, shared vertices are preserved, and hanging vertices are
inserted into neighbouring edges. File indices are one-based. The cell2d CSV
includes node_id to map each MODFLOW cell back to the original grid.
Edge-vertex tolerance 0 uses upstream's scale-aware default; positive values
set the edge insertion tolerance without rounding or merging coordinates.

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
The plugin uses upstream's current conceptual-model controls:

- Normalise embedded line junctions (enabled by default)
- Line-junction spacing fraction (default: 0.5)

The fraction multiplies each line's numeric densification spacing or its
resolution. Vorflow snaps near-miss endpoints, trims short overshoots and
pins shared junctions before densification. Connectivity tolerance remains
the absolute touching tolerance. Field-only, barrier/straddle and quad-buffer
lines are excluded, nearby parallel lines stay separate, and polygon-boundary
anchors stay fixed. QGIS performs no geometry pre-processing for this feature.

The plugin passes line_junction_fraction=0 when normalisation is disabled.
The pinned Vorflow develop build is required. Older fork-only junction_tolerance and
normalize_line_junctions arguments have been replaced. The plugin stops with
an update instruction if the installed API is incompatible.

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

If QGIS uses a different Python interpreter, install the package into that
exact interpreter/environment. Restart QGIS after installation.

See UPSTREAM_SYNC.md for the complete upstream feature assessment.

Progress reporting
------------------
The progress bar reports completed workflow phases. During Gmsh generation
and weighted Lloyd relaxation it switches to an indeterminate busy state.
The upstream API currently provides no per-element callback, so an honest
internal percentage or safe Cancel button cannot be provided by the plugin.
