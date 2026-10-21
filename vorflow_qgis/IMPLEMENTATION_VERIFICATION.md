# Implementation verification - Vorflow QGIS 0.18

Source baseline: upstream `rhugman/vorflow@develop` at `27712b2`, integrated
on `d-strom/vorflow@dev`. The core source tree is unchanged from upstream.
See `UPSTREAM_SYNC.md` for the complete feature assessment.

## Upstream API mapping

| Requested behavior | Plugin implementation |
|---|---|
| `ConceptualMesh(line_junction_fraction=0.5)` | Spacing-fraction control; disabled checkbox passes 0. Older APIs are rejected. |
| Upstream junction diagnostics | Counts and full `junctions`, `snapped_ends`, `trimmed_ends` lists. |
| `grid_to_disv()` | Native generator centres, clockwise rings and hanging-vertex insertion; adapter converts indices to one-based text files. |
| Result mapping | cell2d CSV includes original `node_id`. |
| Feature labels | QGIS layer names passed to upstream feature registration. |
| `blueprint.add_point(..., hex_ring=True)` | Point-layer global and per-layer `Hex ring` control; forwarded to `add_point`. |
| Reject unsupported `hex_ring` | Explicit signature check and actionable error. |
| `VoronoiTessellator(..., lloyd_iterations=100)` | Enabled-by-default weighted Lloyd control in the Voronoi tessellation group; forwarded to the constructor. |
| Reject unsupported Lloyd | Explicit signature check and actionable error. |
| `VoronoiTessellator(..., min_face_fraction=0.2)` | Enabled-by-default short-face clean-up control; forwarded to the constructor. |
| `GeometricGrowthField(growth_factor=1.1, growth_model="continuous_metric", sampling=25)` | Explicit default field for every geometry type; layer overrides remain available. |
| Optional domain-boundary grading | Disabled by default; when enabled, a field-only boundary line grades the selected boundary size into `background_lc`. |
| `MeshGenerator(mesh_algorithm=..., optimization_cycles=..., tolerance_initial_delaunay=...)` | Exposed with defaults 6, 2 and 1e-8. |
| `MeshGenerator(heal_shapes=..., heal_tolerance=..., heal_fix_*=...)` | Exposed as an opt-in recovery tool; disabled by default. |
| `VoronoiTessellator(boundary_centering="inset_mirror", ...)` | Exposed with quality defaults 0.25 inset and 135-degree corner threshold. |
| `lloyd_damping`, `lloyd_tolerance`, `lloyd_size_smoothing` | Exposed and forwarded with defaults 1.0, 0.001 and 5. |
| `add_line(..., straddle_width=...)` | Exposed; 0 is converted to `None` for Vorflow automatic sizing. |
| Numeric `densify` | A positive spacing is forwarded as float; 0 uses resolution through `densify=True`. |
| Reject unsupported short-face clean-up | Explicit signature check and actionable error. |
| `mesher.diagnostics["hex_rings"]` | Displayed in Run diagnostics after meshing. |
| `tess.lloyd_report` | Displayed in Run diagnostics after tessellation. |
| `tess.short_face_report` | Displayed in Run diagnostics after tessellation. |
| `grid["lloyd_shift"]` | Not removed or transformed; preserved when the upstream GeoDataFrame is exported to GeoPackage. |
| Gmsh smoothing distinction | UI and diagnostics now call it Laplacian Gmsh smoothing, not Lloyd. |
| Progress | Phase-based progress bar; Gmsh and Lloyd phases use indeterminate mode because no granular callback is available. |
| Total timer | Starts on Generate mesh and retains the final duration on completion or failure. |

## Important boundary

The QGIS plugin exposes the controls and forwards them correctly. The
`hex_ring`, boundary centring, weighted Lloyd and short-face algorithms must
exist in the installed `vorflow` package. When a selected quality feature is
missing, the plugin stops instead of silently ignoring it.
