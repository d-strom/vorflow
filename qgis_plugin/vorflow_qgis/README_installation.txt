VORFLOW FOR QGIS 0.6.0
======================

AUTHOR
David Ström

PURPOSE
This QGIS plugin provides a graphical interface for Vorflow/Gmsh mesh
generation, mesh-quality review, Voronoi tessellation, and export of a minimal
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
5. Click "Generate mesh".
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
