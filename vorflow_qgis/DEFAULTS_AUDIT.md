# QGIS defaults and control audit

This audit covers the settings exposed by the QGIS wrapper and the matching
Vorflow 0.2.1 APIs on the `dev` branch. The selected defaults are intended as a
quality-oriented starting point for groundwater models. Model-specific spatial
resolution still has to follow the hydraulic problem; no universal cell size is
possible.

## Default profile

| Area | Plugin default | Reason |
|---|---:|---|
| Global background size | 100 map units | Neutral project-scale starting value; must be set by the modeller. |
| Point resolution | 10 | Finer starting size for wells and observation points. |
| Line resolution | 10 | Finer starting size for rivers, faults and barriers. |
| Polygon resolution | 25 | Intermediate starting size for refinement zones. |
| Geometric growth factor | 1.1 for all feature types | Gentler grading is more important to head accuracy and cell regularity than cosmetic smoothing. |
| Geometric growth model | `continuous_metric` | Uses `log(1.1)`, the continuous-metric gradient for a requested neighbouring-edge ratio. It is about 4.7% gentler than `edge_ratio` at 1.1. |
| Distance-field sampling | 25 | Moderate curve sampling without excessive field cost. |
| Point hex ring | Enabled | Produces a regular, generator-centred well cell when local clearance permits. |
| Domain-boundary refinement | Disabled | The domain normally defines extent; the background size should control its interior unless the outer boundary itself needs refinement. |
| Connectivity tolerance | 0.001 map units | Conservative projected-coordinate topology repair. |
| Junction tolerance | Same as connectivity | Prevents two nearly identical tolerances drifting apart. |
| Gmsh algorithm | 6, Frontal-Delaunay | Quality-oriented 2D triangulation. Algorithm 5 remains available for speed or difficult cases. |
| Gmsh smoothing | Vorflow default, 10 | Preserves the tested core default; the plugin only overrides it on request. |
| Gmsh optimisation cycles | 2 | Vorflow's post-mesh quality default. |
| Initial Delaunay tolerance | 1e-8 | Gmsh-compatible conservative default; increase only after an insertion failure. |
| OCC shape healing | Disabled | Healing can remove or merge small model features. It is a recovery tool, not a general quality option. |
| Boundary-cell centring | `inset_mirror` | Improves typical boundary-connection orthogonality compared with clipping boundary generators in place. |
| Boundary inset fraction | 0.25 | Inside Vorflow's tested 0.2–0.3 range. |
| Sharp-corner angle | 135 degrees | Keeps sharp corners fixed while centring smoother boundary sections. |
| Weighted Lloyd | Enabled, 100 passes | Improves visual and centroidal regularity; it remains secondary to resolution and grading. |
| Lloyd damping | 1.0 | Vorflow's tested full-shift default. |
| Lloyd stopping tolerance | 0.001 | Vorflow default; graded meshes usually run the requested number of passes. |
| Lloyd size smoothing | 5 passes | Removes Gmsh node-size jitter without noticeably flattening intended grading. |
| Minimum face fraction | Enabled, 0.2 | Opens visibly short faces created around near-cocircular generators. |
| Default exports | Voronoi grid + quality | Avoids writing the auxiliary Gmsh element grid unless explicitly requested. |

## Model-domain growth controls

Growth is not a property of the domain polygon as a whole. It only matters when
the outer model boundary requests a smaller target size than the global
background. In that case the plugin adds a field-only boundary line and the
growth model controls how quickly the fine boundary size returns to the
background size moving into the domain.

The old interface always showed these controls even when boundary resolution
equalled the background size, in which case the geometric field returned no
constraint. Version 0.17 therefore makes **Refine model-domain boundary** an
explicit opt-in. With it disabled, no internal boundary-refinement line is
created and the growth settings are disabled. Domain embedding and its
low-priority zone order are fixed internally because changing either can make
the mesh extent invalid.

## Removed or corrected controls

- The basic growth-factor control was removed. The default explicit geometric
  field overrode it, so changing the basic value did nothing.
- The separate “Standard” refinement choice was removed. It duplicated the
  geometric model with fewer controls and a different implicit default.
- Quad-buffer thickness is restricted to 1 or 2, matching Vorflow validation.
- Log levels are restricted to Vorflow's 0, 1 and 2 meanings.

## Newly exposed useful controls

- Numeric densification spacing, independent of local cell resolution.
- Explicit barrier straddle width, with 0 retaining Vorflow's automatic width.
- Gmsh algorithm, post-mesh optimisation cycles and initial insertion tolerance.
- Optional OCC healing with its tolerance and three repair switches.
- Boundary centring mode, inset fraction and corner threshold.
- Lloyd damping, convergence tolerance and density-size smoothing.

## Deliberately automatic or unexposed

- `boundary_tolerance` remains automatic because Vorflow derives it from model
  scale and node spacing; a fixed GUI default would be less portable.
- `MeshGenerator(diagnose=True)` is not exposed because its detailed in-memory
  records are not a stable user deliverable. The plugin already reports the
  actionable summaries it consumes.
- Deprecated `dist_min`/`dist_max` feature arguments are not exposed. The
  explicit Threshold model provides the supported replacement.
- Vorflow's package-level defaults remain unchanged for API compatibility. The
  QGIS plugin passes its quality-oriented profile explicitly.

## Trade-offs

The defaults favour smooth grading and grid quality, not minimum runtime or
cell count. `growth_factor=1.1`, inset boundary centring and 100 Lloyd passes can
increase build time and, especially for 1.1 grading, substantially increase the
number of cells. For rapid trial meshes, users can select algorithm 5, reduce or
disable Lloyd, or choose a steeper growth factor after checking result accuracy.

An isolated 600 × 600 single-well comparison was run with background size 60,
well size 6, growth factor 1.1 and identical Gmsh settings. `edge_ratio` produced
902 cells and a p95 adjacent-cell area ratio of 1.805. `continuous_metric`
produced 948 cells and lowered that p95 ratio to 1.736, with essentially
unchanged median compactness (0.9023 versus 0.9027). This is one synthetic case,
not a universal benchmark, but it confirms the expected trade-off behind the
plugin default: roughly 5% more cells for smoother local grading.
