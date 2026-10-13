# Embedded line-junction normalisation

## What this feature adds

Vorflow normalises embedded line networks before Gmsh receives the conceptual
model:

> Before Gmsh receives the conceptual model, exact intersections between
> embedded lines are made explicit as shared line vertices. Nearby
> endpoint-to-line connections can also be snapped within a separate junction
> tolerance.

The feature is implemented as one clearly marked block in
`src/vorflow/blueprint.py`, plus one clearly marked call site in
`ConceptualMesh._enforce_connectivity()`.

The feature does **not**:

- merge Voronoi cells after tessellation;
- merge all generators that happen to be close;
- merge nearby parallel lines;
- modify field-only lines (`embed=False`);
- replace the existing barrier/straddle logic;
- change Lloyd or short-face algorithms.

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

The feature has intentionally conservative scope.

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

## Recommended initial settings

For projected data in metres:

```text
Connectivity tolerance:       0.001 to 0.01 m
Line-junction tolerance:      same as connectivity initially
Normalise embedded junctions: enabled
```

If the lines are only refinement indicators and do not have to be mesh
constraints, use `embed=False`; this remains the most effective way to avoid
constraint-driven cells at a crossing.

## Limitations and follow-up work

This feature makes the input line network explicit. It does not promise that
every embedded line crossing becomes one large, visually perfect Voronoi cell.
An embedded network is still a mesh constraint and may legitimately create
several cells around a junction.

A future, separately reviewable change could add an explicit `junction_mode`
(e.g. one shared junction generator versus preserving all constraint
vertices). That is intentionally not included here because the hydraulic and
topological meaning of those modes must be agreed before changing cell
topology.
