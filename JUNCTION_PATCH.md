# Embedded line junctions: upstream implementation

The isolated fork patch from QGIS 0.16/0.17 has been superseded by upstream
Vorflow's maintained line-junction implementation in `blueprint.py` and
`_junctions.py`. QGIS 0.18 uses that code unchanged.

```python
blueprint = ConceptualMesh(
    crs="EPSG:3006",
    connectivity_tolerance=0.001,
    line_junction_fraction=0.5,
)
```

`line_junction_fraction` multiplies each line's vertex spacing (numeric
`densify`, otherwise `resolution`). Upstream snaps near-miss ends, trims short
overshoots and pins shared junctions before densification, clearing vertices
too close to a junction. Set the fraction to 0 to disable this step.
`connectivity_tolerance` remains the absolute touching tolerance.

The earlier fork-only `junction_tolerance` and `normalize_line_junctions`
arguments have been removed. The plugin's normalisation checkbox now passes
either the selected fraction or 0, and rejects installations missing the
current API rather than silently ignoring the controls.

Parallel lines remain separate; field-only, barrier/straddle and quad-buffer
lines are excluded. Ends and vertices on polygon boundaries remain anchored.
Vorflow records `junctions`, `snapped_ends` and `trimmed_ends` in
`blueprint.junction_diagnostics`; the plugin displays counts and full details.

See `tests/test_line_junctions.py` for upstream geometry and Gmsh regressions
and `tests/test_qgis_plugin_integration.py` for the QGIS adapter checks.
