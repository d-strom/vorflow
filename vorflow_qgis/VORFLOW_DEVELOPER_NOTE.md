# Question for Vorflow developers: where should Lloyd smoothing occur?

The current `smoothing_steps` parameter is applied by `MeshGenerator`, before
`VoronoiTessellator` constructs the Voronoi cells. We would appreciate
clarification of the intended role of this parameter:

- Is the purpose to improve the quality/regularity of the auxiliary
  Delaunay/Gmsh triangulation used as the dual structure?
- Or is the intended user-visible effect to regularise the final Voronoi cells?

From a modelling perspective, it may be useful to investigate an optional
post-meshing Lloyd/CVT step that moves Voronoi generator points (while
respecting the domain boundary, embedded lines/points, refinement field and
any constrained generators) and then rebuilds the Voronoi tessellation.
That would target the final cells more directly.

However, this is not necessarily a drop-in replacement: moving generators
after Gmsh meshing could break feature conformity, refinement intentions,
boundary constraints, topology/connectivity assumptions, or the relationship
between the Delaunay mesh and its Voronoi dual. It may also require a
constrained CVT algorithm rather than ordinary Lloyd iterations.

Could you confirm:

1. What exact Gmsh operation `smoothing_steps` controls?
2. Why it belongs on `MeshGenerator` rather than `VoronoiTessellator`?
3. Whether the current default value is deliberately part of Vorflow's
   backwards-compatible behaviour?
4. Whether a separate opt-in post-tessellation CVT/Lloyd operation is
   technically and conceptually supported?
5. Which points/cells are allowed to move, and how should refinement and
   embedded features be preserved?

The QGIS integration now preserves Vorflow's default by not passing
`smoothing_steps` unless the user explicitly enables an override.
