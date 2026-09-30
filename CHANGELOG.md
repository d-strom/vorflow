# Changelog

All notable changes to `vorflow` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0rc1]

### Fixed

- Kept conceptual-mesh inputs intact across repeated preprocessing calls.
- Made overlapping-zone tie-breaking deterministic.
- Honored the `snap_to_polygons=False` opt-out for line features.
- Preserved integer cell IDs when splitting cells along barrier lines.
- Kept quality reports usable with Gmsh 4.11 by retaining unsupported metrics as `NaN`.
- Restored Shapely 2.0 resampling plus stable lint and minimum-dependency CI.

### Added

- Voronoi and triangular/mixed-element grid generation for MODFLOW 6 workflows.
- Mesh-quality and connectivity diagnostics.
- Optional boundary inset/mirror points and structured quad buffers.
- Explicit mesh-size growth fields and runnable examples.
- Cross-platform tests and TestPyPI release automation.

### Changed

- Prepared project metadata, installation documentation, and dependency floors
  for the first public release candidate.

[Unreleased]: https://github.com/rhugman/vorflow/compare/v0.1.0rc1...HEAD
[0.1.0rc1]: https://github.com/rhugman/vorflow/tree/v0.1.0rc1
