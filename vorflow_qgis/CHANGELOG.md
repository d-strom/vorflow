# Changelog

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
