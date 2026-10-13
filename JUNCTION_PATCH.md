# Embedded line-junction patch

## What this patch adds

This patch adds one deliberately isolated feature to `vorflow`:

> Before Gmsh receives the conceptual model, exact intersections between
> embedded lines are made explicit as shared line vertices. Nearby
> endpoint-to-line connections can also be snapped within a separate junction
> tolerance.

The feature is implemented as one clearly marked block in
`src/vorflow/blueprint.py`, plus one clearly marked call site in
`ConceptualMesh._enforce_connectivity()`.

The patch does **not**:

- merge Voronoi cells after tessellation;
- merge all generators that happen to be close;
- merge nearby parallel lines;
- modify field-only lines (`embed=False`);
- replace the existing barrier/straddle logic;
- change Lloyd or short-face algorithms;
- perform geometry repair in QGIS.

## New API

```python
blueprint = ConceptualMesh(
    crs="EPSG:3006",
    connectivity_tolerance=0.001,
    junction_tolerance=0.001,
    normalize_line_junctions=True,
)
```

Both new arguments are optional and backward-compatible:

- `junction_tolerance=None` means: reuse `connectivity_tolerance`.
- `normalize_line_junctions=True` is the default.
- `blueprint.generate(junction_tolerance=...)` can override the value for one run.

After `generate()`, a small diagnostic dictionary is available:

```python
print(blueprint.junction_diagnostics)
```

It reports:

- whether the feature was enabled;
- the number of detected junctions;
- exact embedded line pairs that were explicitly noded;
- nearby endpoint connections;
- the affected line IDs and coordinates.

## Deliberate algorithmic scope

The patch has intentionally conservative scope.

### Exact intersections

Two embedded `LineString` features that cross are both given an
explicit vertex at the intersection. This makes the network topology visible
to downstream meshing and keeps the two feature records separate.

### Tolerance-based repair

A tolerance-based repair is only attempted when at least one endpoint of one
line is within `junction_tolerance` of the other line. This avoids treating
two long, nearby, parallel lines as one junction.

### Collinear overlaps

Collinear overlapping line features are not merged into one feature. Their
overlap endpoints may be made explicit, but attribution remains separate.

## QGIS integration

The matching QGIS plugin patch is version `0.16.0`. It adds:

- `Normalise embedded line junctions` (enabled by default);
- `Line-junction tolerance`.

QGIS passes these values to `ConceptualMesh`. QGIS itself does not split or
repair the lines.

The plugin uses its existing `call_supported()` compatibility helper. Therefore,
the patched plugin can be installed before or after the patched library, but
the new feature is active only when the patched Vorflow package is installed.

## Installation from the supplied patch ZIP

1. Unzip the delivered patch archive.
2. Install the library into the same Python environment used by QGIS:

   ```bash
   python -m pip install --no-deps --force-reinstall .
   ```

   Run this from the unpacked `vorflow-feat-lloyd-size-smoothing-junction-patch`
   directory.

3. In QGIS, install the included plugin ZIP through:

   `Plugins -> Manage and Install Plugins -> Install from ZIP`

4. Restart QGIS completely.

5. In the plugin, leave `Normalise embedded line junctions` enabled and start
   with `Line-junction tolerance = 0.001` in a metre-based projected CRS.

6. Compare the plugin diagnostic output for:
   - `0` junctions (if the input lines do not intersect);
   - one junction for a simple crossing;
   - four incident line branches at a four-way junction.

## Recommended initial settings

For projected data in metres:

```text
Connectivity tolerance:       0.001 to 0.01 m
Line-junction tolerance:      same as connectivity initially
Normalise embedded junctions: enabled
Lloyd iterations:             25 or 100
Lloyd size smoothing:         5
Minimum face fraction:        0.2
```

If the lines are only refinement indicators and do not have to be mesh
constraints, use `embed=False`; this remains the most effective way to avoid
constraint-driven cells at a crossing.

## Limitations and follow-up work

This patch makes the input line network explicit. It does not promise that
every embedded line crossing becomes one large, visually perfect Voronoi cell.
An embedded network is still a mesh constraint and may legitimately create
several cells around a junction.

A later, separately reviewable patch could add an explicit `junction_mode`
(e.g. one shared junction generator versus preserving all constraint
vertices). That is intentionally not included here because the hydraulic and
topological meaning of those modes must be agreed before changing cell
topology.
