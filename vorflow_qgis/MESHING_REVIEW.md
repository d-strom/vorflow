# QGIS 0.19: snapping and Lloyd review

Baseline: `rhugman/vorflow@develop` at `63d1f6e811b4fbfb7fdefe51c513dfc1e67fbba3`
(30 October 2026), the newest upstream branch tip at integration. The fork
uses the upstream core unchanged. Upstream adds exact barrier faces, input
checks, faster point embedding and domain/hole coverage fixes. GapField,
concave-cell splitting and MFUSG export are available in the Python API;
the wrapper keeps its existing refinement and MODFLOW 6 export workflow.

## Snap to polygons

The wrapper registers the chosen model domain and enabled input rows only.
Vorflow removes field-only polygons from topology processing, resolves
overlaps among embedded polygons, then snaps lines against their cleaned
boundary vertices with Shapely and the absolute Connectivity tolerance
(project map units). The operation can insert vertices on the interior of
line segments, despite the upstream argument docstring mentioning endpoints.
It happens before Gmsh, so Voronoi cells do not exist yet. A layer in the
QGIS project, including a previous output, cannot become a target unless
explicitly selected as an embedded input. Optional selectors start empty.

In QGIS, a line with an existing vertex at (49.9, 50) passed 0.03 m below an
embedded polygon corner at (50, 50.03). At 0.06 m snap tolerance, the corner
was inserted, leaving a 0.104 m segment (the shortest original segment was
30.1 m, with densification off for this test). The upstream input check
flagged a short segment. With snapping off, with the layer only in the
project, or with the polygon selected as field-only, the line was unchanged.
Such close fixed vertices can force tiny elements and irregular cells.

Use snapping for intended connections and keep the tolerance small relative
to the local target size. Use the optional geometry check to locate short
segments, close crossings and narrow gaps. Turning off this checkbox does
not turn off domain clipping, line-junction repair, or the separate cleanup
of barrier ends. More Lloyd passes cannot move fixed line and boundary nodes.

## Lloyd

Three synthetic projected domains (987 cells with wells, 933 with lines and
zones, 936 with a concave boundary) used the same Gmsh nodes within each case.
Settings: continuous-metric growth 1.1, inset/mirror boundary centring,
size smoothing 5, short-face cleanup 0.2. We ran caps 0, 10, 20, 50 and 100.
Times below include Gmsh and tessellation, excluding QGIS export. The
orthogonality statistic is p95 error of centroid-to-centroid connections.

| Case | 20 passes | 50 passes | 100 passes | p95 error 20 / 50 / 100 |
|---|---:|---:|---:|---:|
| Wells | 1.19 s | 1.97 s | 3.35 s | 1.83° / 1.82° / 1.74° |
| Lines and zones | 1.49 s | 2.25 s | 3.28 s | 2.85° / 2.83° / 2.64° |
| Concave boundary | 1.38 s | 2.16 s | 3.34 s | 3.05° / 3.04° / 2.99° |

All runs had the same cell counts within each case, valid geometry and the
same domain coverage. Generator-based p95 orthogonality was effectively zero
for these grids. At 20 passes some centroid and grading metrics were worse
than at 100; short faces did not improve monotonically. 100 remains the
quality default. Use 20 for fast trials (55–64% less compute time here), or
compare 50 and 100 on the actual model. These are geometry measurements, not
proof of identical groundwater heads or particle paths. The earlier upstream
baseline `27712b2` gave the same reported geometry metrics at 100 passes on
these three cases; upstream's speed improvements are workload-dependent.
Use `scripts/benchmark_qgis_lloyd.py` to reproduce the comparison.

The dialog clock updates on the QGIS thread while numerical and file phases
run in a worker. The wrapper uses the upstream Gmsh setup with signal-handler
registration disabled in that worker. QGIS layer access stays on the GUI
thread; the plugin yields between input features.
