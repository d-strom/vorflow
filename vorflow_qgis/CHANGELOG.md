# Changelog

## Unreleased

- Polygon inputs control cell size by default. Opt in to "Create model
  zone" when a polygon should be embedded and assign zone IDs. Layer
  summaries show which mode is in effect.

## 0.19.1

- Fix the quality report crash when a shapefile introduces Z or M into a
  planar Voronoi grid. New QGIS inputs are converted to XY in memory;
  quality metrics also work on existing grids with extra coordinates.
- Handle shared cell boundaries that contain both a line and isolated
  points. Source layers and saved grid geometry are left unchanged.

## 0.19.0

- Merged upstream `develop` through `63d1f6e`, including exact barrier faces,
  input checks, faster point embedding and boundary fixes. Removed the
  numeric straddle-width control because it no longer controls the offset.
- Polygon snapping is opt-in. Added upstream input geometry checks with
  locations in Run diagnostics. Input layer selectors start empty.
- Resize the layer lists and diagnostics with drag handles. Diagnostics start
  collapsed and retain their contents when hidden.
- Layer summaries show the effective inherited or overridden refinement
  model. Geometric growth uses one growth factor.
- The elapsed clock updates during Gmsh, Lloyd, quality calculation and
  export, using seconds, min/s or h/min/s as appropriate.
- Keep the 100-pass quality profile; use 20 Lloyd passes for a fast trial.
  See `MESHING_REVIEW.md` for measured tradeoffs.

## 0.18.0

- Merged all upstream `develop` changes through `27712b2` (20 October 2026),
  including Vorflow 0.3.0, vectorised connectivity/DISV/Voronoi operations and
  consistent int64 generator IDs with inset boundary centring.
- Replaced the fork's older junction implementation with upstream's maintained
  algorithm. The UI now exposes `line_junction_fraction=0.5`; disabling
  normalisation explicitly passes 0. Diagnostics show junctions, snapped ends
  and trimmed overshoots. Incompatible installations fail with an update hint.
- DISV topology now comes from upstream `grid_to_disv()`. Cell centres use
  generator x/y, hanging edge vertices are inserted, and shared vertices are
  preserved. Text-file indices are converted to one-based form exactly once.
- Added `node_id` to the cell2d CSV so MODFLOW cell numbers map back to the
  original Vorflow grid. Edge-vertex tolerance defaults to upstream automatic
  scaling and no longer rounds or merges nearby coordinates.
- Pass QGIS layer names as upstream feature labels. Kept the existing QGIS
  rendering, explicit geometry-cleaning workflow and GeoPackage export.
- Added integration regressions for junction controls, DISV topology and
  file mapping, plus a Gmsh/Lloyd/DISV round trip. CI now runs on `dev` pushes.
- See `UPSTREAM_SYNC.md` for the feature assessment and source baseline.

## 0.17.0

- Replaced the duplicated basic/advanced growth controls with one effective
  size-transition control. The previous basic growth factor was ignored while
  the default explicit `GeometricGrowthField` was active.
- Changed every plugin geometric-growth default to `growth_factor=1.1` with
  `growth_model="continuous_metric"`; legacy `edge_ratio` remains selectable.
- Model-domain boundary refinement is now an explicit opt-in. When disabled,
  the domain defines extent only and no redundant boundary refinement line is
  added. When enabled, its growth settings grade a finer boundary size back to
  the global background size.
- Added controls for Gmsh algorithm, optimisation cycles, initial Delaunay
  tolerance and optional OCC shape healing.
- Added controls for boundary-cell centring. `inset_mirror` with inset 0.25 and
  corner angle 135 degrees is the new plugin default; historical `clip` remains
  selectable.
- Added Lloyd damping, stopping tolerance and size-smoothing controls, retaining
  the tested defaults 1.0, 0.001 and 5 passes.
- Added numeric geometry-densification spacing and barrier straddle width.
- Corrected the quad-corridor row range from 1–1000 to Vorflow's supported 1–2.
- Corrected log-level choices to Vorflow's actual 0–2 API.
- Line-junction tolerance now follows connectivity tolerance by default and is
  only independent when explicitly requested.
- Disabled the redundant all-elements export by default; the Voronoi grid and
  Voronoi quality report remain enabled.
- Added `DEFAULTS_AUDIT.md` documenting every reviewed default and intentionally
  unexposed low-level option.

## 0.16.0

- Added controls for Vorflow internal embedded-line junction normalisation.
- QGIS now passes `junction_tolerance` and `normalize_line_junctions` to
  `ConceptualMesh` when supported.
- Added run diagnostics reporting exact line crossings noded and nearby
  endpoints connected.
- No QGIS-side line splitting or Voronoi post-processing is performed.

## 0.15.0

- Updated the recommended defaults from the Vorflow developer's grid-quality
  review: `growth_factor=1.1` for points and lines, `hex_ring=True` for point
  layers, and 100 weighted Lloyd passes.
- Added an enabled-by-default `min_face_fraction=0.2` control for removing very
  short Voronoi faces while keeping wells, lines and boundary generators fixed.
- Added `short_face_report` to Run diagnostics and a clear compatibility error
  when the installed Vorflow package does not expose the new API.
- Integrated the upstream Lloyd size-smoothing and short-face-removal changes
  from `rhugman/vorflow` feature branch `feat/lloyd-size-smoothing`.

## 0.14.0

- Added a separate total elapsed-time display below the progress bar.
- Timing starts immediately when **Generate mesh** is clicked.
- The final duration remains visible after successful completion.
- Failed runs also retain and report their elapsed duration.
- The timer uses `time.perf_counter()` so the measured duration includes all
  conceptual-model, Gmsh, Lloyd/Voronoi, quality, export and QGIS loading steps.


## 0.13.0

Corrected implementation based on the 0.11 plugin codebase.

- Added an opt-in `hex_ring` control for point layers and passes
  `hex_ring=True` to `ConceptualMesh.add_point`.
- Added weighted Lloyd relaxation through
  `VoronoiTessellator(..., lloyd_iterations=N)`.
- Reports `mesher.diagnostics["hex_rings"]` and `tessellator.lloyd_report`
  in the diagnostics panel when supplied by Vorflow.
- Preserves Vorflow's `lloyd_shift` output column in exported Voronoi data.
- Corrected the previous wording: Gmsh `smoothing_steps` is Laplacian
  triangle smoothing and is not Lloyd relaxation.
- Added a phase progress bar. Long Gmsh and Lloyd phases are shown as
  indeterminate because the current upstream API does not expose granular
  progress callbacks.
- Fails clearly if the installed Vorflow does not expose the requested
  `hex_ring` or `lloyd_iterations` APIs instead of silently ignoring them.


## 0.11.0

- Changed Lloyd smoothing to use Vorflow's own default by default.
- Added an `Override Vorflow default` control; an explicit value is only passed when enabled.
- Diagnostics now distinguish Vorflow default mode from an explicit QGIS override.


## 0.10.0

- Added run diagnostics showing the installed `MeshGenerator` and `generate()` signatures.
- Added explicit Lloyd routing: constructor or `mesher.generate()`.
- No longer silently ignores `smoothing_steps`; the run now fails with a clear error if the installed Vorflow API does not expose it.
- Added QGIS message-log output and an in-dialog diagnostics panel showing the requested/effective route and value.


## 0.9.0

- Added a central global `Lloyd smoothing steps` control under
  `Global mesh parameters`.
- Passed `smoothing_steps` to Vorflow's `MeshGenerator`; `0` is the plugin
  default and disables Lloyd smoothing.
- Kept compatibility with older Vorflow versions through the existing
  supported-argument filtering.

## 0.8.0

- Replaced the triangle/Delaunay quality output with a quality grid based on
  the generated Voronoi cells.
- Added cell-wise shape, compactness, edge, angle, neighbour, orthogonality
  and centroid-based CVFD-skewness diagnostics.
- The quality report and themed QGIS layers now describe Voronoi cells, which
  are the grid cells exported for MODFLOW 6 DISV.


## 0.7.0

- Changed model-domain resolution to refine only the domain boundary; the
  global background size now controls the general mesh size inside the domain.

## 0.6.0

- Preconfigured Geometric growth as the default refinement model.
- Preconfigured `edge_ratio` as the robust default growth model.
- Set the default growth factor to 1.2 and sampling to 25.
- Applied the recommended profile to the domain and global point, line and
  polygon settings.
- Kept Standard, Threshold, Exponential, Continuous metric and all
  layer-specific overrides available.
- Added interface and documentation notes describing the recommended profile.

## 0.5.0

- Changed plugin author/developer metadata to David Ström.
- Translated the interface, parameter help, status messages and generated
  DISV readme to English.
- Aligned refinement labels with Vorflow class and parameter terminology.
- Added an original Voronoi-themed QGIS toolbar icon.
- Added attribution and third-party notices.
- Added GitHub fork publishing instructions.
- Retained mesh generation, quality styling, Voronoi generation and minimal
  MODFLOW 6 DISV export from version 0.4.0.
