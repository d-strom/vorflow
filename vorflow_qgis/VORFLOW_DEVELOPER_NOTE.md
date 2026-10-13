# Version 0.17 integration note

The QGIS plugin uses Vorflow's upstream algorithms rather than reimplementing
them. Version 0.17 targets the integrated API on `d-strom/vorflow@dev`:

```python
blueprint.add_point(
    ...,
    fields=[GeometricGrowthField(
        growth_factor=1.1,
        growth_model="continuous_metric",
        sampling=25,
    )],
    hex_ring=True,
)
tess = VoronoiTessellator(
    mesher,
    blueprint,
    boundary_centering="inset_mirror",
    boundary_inset_fraction=0.25,
    lloyd_iterations=100,
    lloyd_damping=1.0,
    lloyd_tolerance=0.001,
    lloyd_size_smoothing=5,
    min_face_fraction=0.2,
)
grid = tess.generate()
```

It surfaces `mesher.diagnostics["hex_rings"]`, `tess.lloyd_report` and
`tess.short_face_report`. The exported GeoDataFrame is written without
dropping columns, so upstream `lloyd_shift` is retained.

The progress bar is phase-based and a separate timer measures total run time.
Gmsh, Lloyd and short-face clean-up are synchronous and do not expose granular
callbacks; those phases therefore use an indeterminate state.

# Resolved behavior and modelling priorities

Gmsh `smoothing_steps` is Laplacian smoothing of the auxiliary triangle mesh.
Weighted Lloyd is a separate `VoronoiTessellator` operation that moves free
generators towards their size-weighted cell centroids. Fixed generators at
wells, lines, zone edges and boundaries stay in place.

The wrapper defaults reflect the upstream developer's grid comparison:

- `growth_factor=1.1` with `continuous_metric` on all plugin feature types gives
  gentler, consistently interpreted grading and usually
  improves cell shape more than additional Lloyd iterations, at the cost of
  more cells;
- `hex_ring=True` centres a regular cell on point features such as wells;
- 100 weighted Lloyd passes improve grid-wide regularity;
- `min_face_fraction=0.2` opens the short faces that Lloyd can create without
  moving fixed generators.
- model-domain boundary refinement is opt-in because its growth settings have
  no effect when the boundary target is not finer than the background size;
- `boundary_centering="inset_mirror"` improves typical boundary orthogonality.

For MODFLOW results, local cell size, smooth grading and well-cell geometry are
normally more important than visual cell regularity. Short-face clean-up mainly
improves skewness metrics and local vector or particle-tracking plots. The QGIS
integration preserves Vorflow's Gmsh smoothing default unless the user
explicitly enables an override.
