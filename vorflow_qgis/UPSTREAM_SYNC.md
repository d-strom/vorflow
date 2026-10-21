# Upstream integration: QGIS 0.18.0

Reviewed on 21 October 2026. Fork baseline: `d-strom/vorflow@dev`, `98beab2`.
Integrated target: `rhugman/vorflow@develop`,
`27712b2fc937688b66ca23f7f6d61f0c78146f94` (20 October 2026).
The current upstream main release is 0.3.0 at `07dbc07`; develop includes its
code plus the newer connectivity, vectorisation and node-ID fixes.

Every commit reachable from the target but absent from the fork baseline was
reviewed, including changes already ported in QGIS 0.15–0.17. Merge conflicts
in the core were resolved to upstream. The complete `src/vorflow` tree and
package metadata match the target exactly: the plugin uses upstream
algorithms rather than keeping a second junction implementation.

| Upstream change | QGIS assessment and integration |
|---|---|
| Line junctions (#38) | Adopted upstream code. Replaced absolute junction distance with spacing fraction, default 0.5. Disable passes 0. Updated diagnostics and compatibility checks; removed obsolete tests of fork-only private helpers, retaining upstream geometric coverage and adding adapter regressions. |
| Native DISV export (#47) | Used `grid_to_disv()` for the plugin's generated grid. Preserves generator centres and hanging edge vertices; converts zero-based arrays to one-based MODFLOW files and exports the node-ID mapping. Existing minimal simulation writer remains. |
| `to_modflowgwfdisv()` | Available in the integrated Python package. The QGIS exporter continues writing its minimal simulation without requiring optional FloPy. |
| `to_file()` | Available in the package. Existing QGIS GeoPackage export already calls the same GeoDataFrame writer with a named layer, so no duplicate export control was added. |
| `plot()`, labels and line colours (#46/#51) | Available in the package. QGIS already renders and styles its layers. Layer names now populate feature labels for upstream plotting/debugging; no Matplotlib dependency or second plot UI is needed. |
| `MeshGenerator(conceptual_mesh=...)`, `tessellator.grid` | Available in the package. The plugin keeps its explicit clean-frame workflow to report separate cleaning and Gmsh phases. Its `generate()` call does not compare the return value with `True`, so the new self return is compatible. |
| Lloyd size smoothing and short faces (#34) | Already exposed; retained plugin defaults 5 and 0.2, with 100 weighted Lloyd passes and continuous-metric growth at 1.1. Core and tests now match upstream. |
| Optimised connectivity (#60), vectorised hot paths (#62) | Adopted in full. Native DISV conversion now gives the wrapper the upstream export speedups as well as the mesh/connectivity changes. |
| int64 node IDs with inset/mirror centring (#63) | Adopted in full and exercised with the plugin's inset/mirror profile. |
| 0.3.0 metadata, documentation, examples, community files, paper and release tooling | Merged. Updated wrapper installation and API notes. Enabled tests and documentation builds on fork `dev` pushes; Pages deployment remains upstream-only because the fork has no `develop` branch. |

The target is upstream's integration branch, rather than an unmerged feature
or benchmark branch. Existing feature branches were checked against this
baseline; their merged functionality is included through develop.

The QGIS wrapper remains a separate ZIP. Updating it does not update an
already installed Vorflow Python dependency: follow `README_installation.txt`
in the Python environment used by QGIS.

## Validation

- Full suite with optional FloPy and Matplotlib: 589 passed, one distribution
  test skipped because setuptools was absent. After adding setuptools/wheel,
  that test passed separately, building and validating wheel and sdist.
  All 590 collected tests have therefore passed.
- Ruff passed for core, tests, scripts and the complete QGIS plugin.
- Separate QGIS 3.40.0 process: real dialog, synthetic polygon/line/point
  layers, near-miss junction repair, 100 Lloyd passes, inset/mirror centring,
  short-face cleanup, GeoPackage outputs, styled quality layers and DISV
  files. Result: 140 cells, 294 vertices, 21 project layers; no plugin errors.
- FloPy loaded the plugin's text simulation and confirmed generator centres,
  layer counts and shared-edge neighbours in the adapter regression.
- Plugin ZIP contents, Python compilation and ZIP integrity were checked.
