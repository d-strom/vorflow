# Changelog

All notable changes to `vorflow` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `VoronoiTessellator(boundary_inset_fraction=...)` now defaults to 0.25
  instead of 0.5. At 0.5, `boundary_centering="inset_mirror"` moved boundary
  nodes past the point where their cells are centred on Gmsh meshes and made
  centroid-to-centroid boundary orthogonality worse than `"clip"` (median
  ortho_error 12.0 vs 6.4 degrees on a 200 x 200 box at `background_lc=20`).
  At 0.25 the median is 1.4 degrees there, and 1.3 vs 6.9 degrees on the
  comprehensive demo model. Pass `boundary_inset_fraction=0.5` for the old
  behaviour.

### Fixed

- Cells that straddle a barrier now get a mirror generator across the line
  instead of a centroid-centred fragment, so every face stays a Voronoi
  bisector and MODFLOW 6 connections stay orthogonal. On a fault crossed by a
  river (benchmark case F4) the linear-head L2 error drops from 3e-5 to 1e-10.
  The primary cell keeps its `node_id` and `x`/`y`; mirrors get fresh IDs and
  their count is `VoronoiTessellator.n_barrier_mirrors`. A mirror of a
  boundary node near a barrier end can lie just outside the domain. Nodes on
  the line itself still fall back to the post-hoc split.
- Barrier splits no longer leave zero-length edges where the line passes
  through (or within roundoff of) a cell vertex.
- `heal_shapes=True` no longer hangs Gmsh on lines that meet near a thin
  sliver polygon (`cleaning_limitations_demo`, Problem 4). After healing,
  curves and surfaces are matched to the nearest survivor within half their
  own extent, one-to-one, instead of keeping a surviving tag number that
  healing had reused for a different piece. Pieces shorter than the 1e-4
  tolerance no longer take a neighbour's or another line's entity, and pieces
  that healing deleted are pruned. A line fragment whose endpoint lies on a
  surface boundary without being a vertex of that surface (healing can
  duplicate a hole's edges instead of sharing them) is no longer embedded; a
  warning names it and `diagnostics['embedding']['nonconforming_skip']`
  counts it.

## [0.1.0rc1]

### Fixed

- Kept conceptual-mesh inputs intact across repeated preprocessing calls.
- Made overlapping-zone tie-breaking deterministic.
- Honored the `snap_to_polygons=False` opt-out for line features.
- Preserved integer cell IDs when splitting cells along barrier lines.
- Kept quality reports usable with Gmsh 4.11 by retaining unsupported metrics as `NaN`.
- Restored Shapely 2.0 resampling plus stable lint and minimum-dependency CI.
- Kept barrier straddle points separate from point features with the same
  index; a point's size field no longer leaks onto an unrelated barrier.
- Enforced barriers wherever cells actually straddle them, including quad
  buffers with `quad_buffer_thickness=2` and cells at barrier ends.
- Field-only (`embed=False`) polygons no longer assign zones or change the
  clip domain, in both the Voronoi grid and the element grid.
- Kept `node_id` unique when clipping splits a cell into several parts.
- Cells whose generator sits on a slanted domain edge get the nearest zone
  instead of no `zone_id` (about 3% of cells on a simple pentagon domain).
- Polygon simplification no longer opens gaps along edges shared with
  neighbouring polygons.
- Point deduplication no longer depends on which point of a close pair has
  `simplify_tolerance`, and clean points keep their insertion order.
- Face skewness now reports the standard CVFD measure; the generator-mode
  value was always zero.
- `MeshGenerator(verbosity=...)` no longer changes the package-wide log
  level; the setting applies only while `generate()` runs.
- Custom `MeshField` subclasses with unhashable attributes can be grouped.
- With `heal_shapes=True`, surfaces or curves with identical bounding boxes
  (e.g. two triangles tiling a square) no longer swap feature ownership, which
  gave one zone's refinement to its neighbour. Entities are matched across
  `removeAllDuplicates`/`healShapes` by location within a tolerance, so near
  coincident points also resolve to the nearest survivor.
- Inset-mirror boundary centering skips nodes whose mirror ghost would land
  inside the domain, and is about 20x faster on large meshes.

### Added

- Voronoi and triangular/mixed-element grid generation for MODFLOW 6 workflows.
- Mesh-quality and connectivity diagnostics.
- Optional boundary inset/mirror points and structured quad buffers.
- Explicit mesh-size growth fields and runnable examples.
- Cross-platform tests and TestPyPI release automation.
- `vorflow.set_verbosity(level, console=False)` routes messages to the
  application's logging configuration instead of vorflow's console handler.

### Changed

- Prepared project metadata, installation documentation, and dependency floors
  for the first public release candidate.
- Features finer than `background_lc` now grade outward with a
  `GeometricGrowthField` (growth factor 1.2) by default. Models that relied on
  the old implicit sizing will generally get more, better-graded cells.
- Progress output uses the `vorflow` logger (stderr) instead of `print()`.
- The mesh-size field setup's index-matching lines are `[DIAG]` output
  (verbosity 2) instead of printing at the default verbosity.
- `ConceptualMesh(crs=...)` defaults to `None` instead of `"EPSG:4326"`;
  geographic CRSs trigger a warning.
- `MeshGenerator.generate()` raises before starting Gmsh if `background_lc`
  is missing or not positive.
- `get_element_grid()` builds element polygons on first use instead of in
  every `generate()` call.
- `quad_buffer=True` requires `embed=True`.
- Barrier straddle offsets use a tangent probe proportional to line length,
  which can move barrier nodes by floating-point amounts.
- Python 3.10 is now the minimum; matplotlib is optional (`examples` extra).

### Deprecated

- `add_polygon(mesh_refinement=...)` (no effect), `dist_max_out` (use
  `dist_max`), `border_density` (use `densify`; the border grading is kept),
  and `dist_max_in`.
- `dist_min`/`dist_max` on features; use `growth_factor` or explicit `fields`.

[Unreleased]: https://github.com/rhugman/vorflow/compare/v0.1.0rc1...HEAD
[0.1.0rc1]: https://github.com/rhugman/vorflow/tree/v0.1.0rc1
