# vorflow Roadmap - Porting robust capabilities from `gmshflow`

This roadmap captures the agreed plan for bringing the strongest features of the
pre-refactor `gmshflow` library into `vorflow`, while keeping `vorflow`'s current
defaults backward-compatible and adding robust paths as opt-in options.

Each milestone has a self-contained document under [`docs/roadmap/`](docs/roadmap/)
with goals, files to touch, source references, reuse notes, and verification
steps.

## Context

`gmshflow` (`../gmshflow/src/gmshflow.py`, a single module on the gmsh geo
kernel) was the version that existed before the refactor into `vorflow`.
`vorflow` (`src/vorflow/`) is modular and more robust: it runs on the gmsh OCC
kernel with `fragment` / `removeAllDuplicates` / `healShapes`, has a better field
system, and tracks generator points through the pipeline so Voronoi quality can
be computed correctly.

However, a handful of `gmshflow` capabilities are either missing from `vorflow`
or useful enough to port.

## Decisions

- Keep both the lightweight straddle path and the robust structured-quad buffer
  path. Structured buffers are opt-in and do not replace straddle.
- Keep the current Voronoi clipping behavior as the default.
- Do not add a core sliver-filtering or active-domain API for now. Larger
  mesh-envelope / smaller active-domain workflows should select or tag active
  cells externally with GeoPandas after grid generation.
- Add triangular/2D element quality, Voronoi/MODFLOW-facing connectivity reports,
  CVFD skewness, and centroid-based quality diagnostics.
- Avoid new hard dependencies. Skip `topojson`; document external preprocessing
  recipes instead.
- Leave `calculate_orthogonality` backward-compatible. New connectivity reports
  can expose both Voronoi-dual generator connectivity and MODFLOW-facing centroid
  connectivity.

## Out of Scope

- Outer-domain construction (`prepare_mesh_domain`): users can construct convex
  hulls, square extents, or buffered envelopes with Shapely/GeoPandas.
- Topology-preserving simplification with `topojson`: avoid the dependency.
- A general `merge_many_multilinestring_into_one_linestring` helper: add only a
  localized repair if structured buffers actually encounter offset-curve splits.

## Current Objective

| # | Milestone | Status | Risk | Behavior change | Doc |
|---|-----------|--------|------|-----------------|-----|
| 6 | First PyPI release and PR #12 review closeout | In progress | High | Release metadata and meshing corrections | [06-pypi-publishing.md](docs/roadmap/06-pypi-publishing.md) |

The current release scope is a `0.1.0rc1` TestPyPI rehearsal. A successful
rehearsal changes this status to **TestPyPI verified**. The milestone becomes
**Done** only after `vorflow` is published to real PyPI.

## PR #12 review closeout design

**Goal.** Make [PR #12](https://github.com/rhugman/vorflow/pull/12) safe to
merge and release as `0.1.0rc1` by resolving every applicable item in the
[first maintainer review](https://github.com/rhugman/vorflow/pull/12#issuecomment-5096576387)
and [second maintainer review](https://github.com/rhugman/vorflow/pull/12#issuecomment-5096770583).
This includes verified correctness, compatibility, CI, performance, and
maintainability work in the same PR. Do not split the PR. The review comments
have no inline threads, so each item's resolution is reported in the PR
conversation with a commit and verification link.

**Working rule.** Follow the ordered queue below strictly one item at a time.
For each item, confirm the claim against the then-current PR head, add a
focused regression or benchmark where applicable, make only that item's change,
run focused checks, Ruff and the full suite, and record the result here and in
the PR. Commit the item separately and pause for review before starting the
next. A claim already fixed, not reproducible, or no longer applicable receives
an evidence-backed disposition instead of a gratuitous code change. The final
PR head must pass the complete GitHub OS/Python matrix and minimum-dependency
job. CI is the release gate; do not create a release tag or publish as part of
review closeout. Preserve unrelated files and the existing public defaults
except where a review item explicitly calls for a correction.

**Review decisions.** Package-facing URLs target `rhugman/vorflow`, while
Oscar Sanchez remains the first author and Oscar and rhugman are both listed
as maintainers. A [public Vorflow commit](https://github.com/rhugman/vorflow/commit/02c82b60947ac1d8d0f8c01315ce6480b0e307dc)
uses `rthugman@gmail.com` as rhugman's author email, although his GitHub
profile does not list a contact email. Propose that address for maintainer
metadata, but obtain rhugman's confirmation before release; remove or replace
it if he prefers. The four removed `add_polygon` keywords are documented with
migration guidance rather than reintroduced before the first release candidate.
All verified review work
stays in PR #12. Existing fixes for issues #13–#16 at commit `01b2d43` are
retained; the issues stay open until merge. The TestPyPI trusted-publisher
configuration belongs to the upstream repository and must be confirmed with
its maintainer before an upstream release tag is created.

### Ordered review queue

`Next` means the first item to design and implement after this roadmap is
approved. `Queued` means not yet accepted as a code change: reproduction and
technical evaluation are part of that item's work. A repeated observation in
the two reviews appears once here.

#### Release blockers and user-visible correctness

| # | Status | Review item and acceptance evidence |
|---|--------|-------------------------------------|
| 1 | Verified; release gates pending | Canonical release ownership: upstream URLs in package metadata, README, changelog, distribution validator, tests, and release milestone; built wheel/sdist metadata and links checked. Confirm upstream TestPyPI trusted-publisher setup separately. |
| 2 | Awaiting CI and user review | `ConceptualMesh.generate()` preserves raw polygon, line, and point inputs across repeated calls; identical inputs produce identical clean frames, including `embed=False` fields. Includes the missing idempotency test. |
| 3 | Queued | Constructing `MeshGenerator` cannot change the package logger's level; per-instance verbosity still controls Gmsh and diagnostics. |
| 4 | Queued | Clipped or dropped out-of-domain lines and points produce warnings naming affected feature IDs; a boundary-epsilon case is tested. |
| 5 | Queued | Failed point or line embedding produces an actionable warning identifying source feature IDs; existing diagnostic counts remain available. |
| 6 | Queued | Orphan surfaces are attached only to geometrically plausible owners; distant orphans are warned about and excluded from recovered domain surfaces. |
| 7 | Queued | Dedup/heal coordinate remapping uses explicit distance tolerances, preserves distinct nearby survivors, and warns when a mapped feature is lost; boundary-of-bucket regressions cover both paths. |
| 8 | Queued | Structured strips touching the domain boundary do not create a mismatch between meshed surface and Voronoi clipping domain; area/sliver regression covers an edge crossing. |
| 9 | Queued | Point deduplication is symmetric under which point carries `simplify_tolerance`, retains the finest point, and documents the exact merge rule. |
| 10 | Queued | Each mesh generation resets run-specific diagnostics, so a second run cannot report stale embedding data. |
| 11 | Queued | Structured-buffer surfaces retain the matching strip/corner provenance through fragmentation; one surface cannot inherit another strip's transfinite structure. |
| 12 | Queued | Transfinite curve constraints are calculated before any are applied; failure leaves no partial constraints. Includes a failed-distribution regression. |

#### Public API and release pipeline

| # | Status | Review item and acceptance evidence |
|---|--------|-------------------------------------|
| 13 | Queued | `background_lc` fails fast with a clear error at construction when absent or invalid; no geometry work begins. Update its API documentation and changelog. |
| 14 | Queued | `add_polygon`, `add_line`, and `add_point` consistently validate feature sizes/distances and straddle width at input time; invalid values cannot silently fall back to background sizing. |
| 15 | Queued | Remove or justify the redundant engine-side quad-thickness check and the silent `10.0` fallback, without accepting invalid public input. |
| 16 | Queued | Changelog `Removed`/migration notes cover `mesh_refinement`, `border_density`, `dist_max_in`, and `dist_max_out`; `Changed` notes cover `background_lc` and the default CRS. Supported alternatives are tested. |
| 17 | Queued | `ThresholdField` validates finite positive sizes and valid distances/sampling consistently with other public fields; `size_max=0` cannot masquerade as omitted. |
| 18 | Queued | The TestPyPI smoke test derives the installed version from the release tag instead of hard-coding `0.1.0rc1`; a later RC tag is covered. |
| 19 | Queued | Declare `packaging` as a direct development dependency because the distribution checker imports it. |
| 20 | Queued | Restore CI push coverage for `develop` if it remains an upstream integration branch; verify the current upstream branch policy before changing triggers. |
| 21 | Queued | README installation text distinguishes TestPyPI rehearsal from real PyPI availability and quotes extras syntax for zsh. |
| 22 | Queued | Replace brittle exact author, build-backend, and dependency-list assertions with behavior/metadata-contract checks while retaining version and clean-directory smoke coverage. |
| 23 | Queued | Remove unreleased `AutoLinearField` and `AutoExponentialField` aliases only after confirming no repository examples or supported consumer contract depends on them. |
| 24 | Queued | Group mesh fields by the field object plus feature size, not `hash(field)`; a hash-collision regression proves distinct fields remain distinct. |
| 25 | Queued | Mesh-generation failure is logged at error/exception severity and re-raised without duplicate or hidden messages. |

#### Performance, diagnostics, and maintainability

| # | Status | Review item and acceptance evidence |
|---|--------|-------------------------------------|
| 26 | Queued | `build_connectivity` avoids per-pair pandas scalar lookups and vectorizes geometry work where equivalent; compare output and benchmark a representative larger grid. |
| 27 | Queued | Prepare or index strip footprints once before moving domain-ring vertices; preserve the original geometric result and benchmark a densified ring. |
| 28 | Queued | Vectorize `inset_mirror` boundary distances and index ring vertices once; preserve corner classification on holes and multipart domains, with a scaling benchmark. |
| 29 | Queued | Bound quad-buffer crossing refinement by local crossing width instead of the long bounding-box diagonal; test shallow-angle, near-parallel crossings. |
| 30 | Queued | Eliminate diagnostic counters that are never written (or implement their measurement); do not report unmeasured “0 conflicts.” |
| 31 | Queued | Make hidden `simplify(lc * 1.5)` in structured-buffer planning explicit, configurable, or removable based on geometry regressions; document any remaining default. |
| 32 | Queued | Align `ortho_error` docstrings for connector-vs-edge and connector-vs-normal calculations without changing the numerical convention. |
| 33 | Queued | Use warning level for large `heal_tolerance`, remove duplicated “Warning:” prefixes, and preserve actionable identifiers. |
| 34 | Queued | Make pre- and post-mesh `launch_gmsh_gui` behavior consistent or explicitly document the distinction; test the chosen condition. |
| 35 | Queued | Extract structured-buffer planning, trimming, crossing arbitration, and transfinite application from the oversized `_add_geometry` while retaining regression-equivalent behavior. |
| 36 | Queued | Extract or remove verbose DIAG blocks after preserving useful `diagnostics` data and debug behavior; no change to mesh output. |
| 37 | Queued | Remove stale `docs/superpowers/` scratch only after confirming unique release decisions are represented in this roadmap or `docs/roadmap/`. Keep the useful milestone documents. |

### Review dispositions and merge handoff

- **Action versions:** The questioned checkout, setup-python, upload-artifact,
  and download-artifact majors are published (see their respective
  [releases](https://github.com/actions/checkout/releases),
  [setup-python releases](https://github.com/actions/setup-python/releases),
  [upload-artifact releases](https://github.com/actions/upload-artifact/releases),
  and [download-artifact releases](https://github.com/actions/download-artifact/releases)).
  The [last exact-head fork run](https://github.com/oscarfasanchez/vorflow_os/actions/runs/32625192327)
  passed all 11 jobs, so this is not an unresolvable-action blocker. Check the
  published versions and final run again before merge; normalization is only
  needed if a supported runner or consistency problem is demonstrated.
- **Repeated test requests:** The idempotency and dropped-feature warning tests
  are acceptance evidence for items 2 and 4, not separate changes. The repeated
  `ThresholdField`, `inset_mirror`, and structured-buffer extraction notes are
  represented once each above.
- **PR split:** Declined for this closeout. Keep the single delivery vehicle as
  agreed, with isolated commits and verification per item.
- **Related issues/PRs:** Do not close #13–#16 before PR #12 merges. Recheck
  whether PRs #9 and #11 are fully absorbed before asking the maintainer to
  close them; do not close them merely because similar features exist here.
- **Final gate:** Run Ruff on `src tests scripts`, the full pytest suite,
  distribution build/check/smoke tests, and all GitHub matrix/minimum-dependency
  jobs on the final head. Update the PR conversation with a concise resolution
  map and evidence. Upstream TestPyPI rehearsal remains a separate, explicit
  maintainer-approved step after merge readiness.

### First implementation unit: canonical release ownership

Change package-facing repository, issue, changelog, and README links to
`https://github.com/rhugman/vorflow` without changing the `0.1.0rc1` version.
Preserve Oscar as first author; list both Oscar and rhugman as maintainers,
provisionally using rhugman's publicly committed address. Update
`scripts/check_dist.py`, its fixture tests, and release-metadata tests to
assert the canonical URLs; update the release milestone's ownership statement.
Do not alter the TestPyPI publisher identity
in code: verify its upstream repository/environment configuration and confirm
the maintainer email with rhugman before any tag is pushed. A focused metadata
test should fail against the current fork URLs and pass after the change; then
check built wheel and sdist metadata, README/changelog links, Ruff, and the full suite. Commit and report this unit
alone, then pause before item 2.

**Item 1 verification (local; PR CI pending).** Twenty focused release tests
passed in the Vorflow Conda environment with the Gmsh smoke test deselected.
`ruff check src tests scripts`, Twine, and `scripts/check_dist.py` passed for a
fresh `0.1.0rc1` wheel and sdist built with installed setuptools. The normal
isolated build could not download setuptools in the network-restricted local
sandbox, and the full pytest run produced no output after about 100 seconds
while importing local dependencies. GitHub PR CI must verify the full suite
before this item is considered resolved; the tag-triggered release workflow
will verify an isolated build at the separate release gate. The upstream
TestPyPI publisher identity and rhugman's preferred contact address still
require confirmation before tagging.

**Item 1 CI.** The [exact-head run](https://github.com/oscarfasanchez/vorflow_os/actions/runs/35480928726)
passed lint, all nine OS/Python test-matrix jobs, and minimum dependencies
(11/11). The external release gates above remain open.

### Second implementation unit: repeatable conceptual-mesh preprocessing

Run geometry cleanup on per-call feature-record copies, restoring the original
raw polygon, line, and point lists even when processing raises. Preserve the
existing clean output API. Regression tests cover unchanged raw inputs,
identical clean frames on repeated calls with a field-only polygon, and
restoration after an error. Locally, the focused conceptual-mesh file passed
(31 tests), and the full suite passed (219 tests) in the activated Vorflow
Conda environment; PR CI is pending for this item.

## Completed Milestones

| # | Milestone | Status | Doc |
|---|-----------|--------|-----|
| 1 | Quality metrics | Done | [01-quality-metrics.md](docs/roadmap/01-quality-metrics.md) |
| 2 | Active-domain filtering workflow | Done (example) | [02-robust-clipping.md](docs/roadmap/02-robust-clipping.md) |
| 3 | Boundary inset/mirror points | Done | [03-boundary-mirror-points.md](docs/roadmap/03-boundary-mirror-points.md) |
| 4 | Structured-quad transfinite buffer | Done | [04-structured-quad-buffer.md](docs/roadmap/04-structured-quad-buffer.md) |
| 5 | Triangular/mixed element-grid output | Done | [05-triangular-grid-output.md](docs/roadmap/05-triangular-grid-output.md) |

All five milestones are implemented: quality metrics and connectivity reports
(`get_triangular_quality`, `utils.build_connectivity`), the active-domain
workflow example (`examples/active_domain_quality_example.py`), opt-in boundary
inset/mirror points (`VoronoiTessellator(boundary_centering="inset_mirror")`),
opt-in structured quad buffers (`add_polygon`/`add_line` with
`quad_buffer=True`), and the element-grid exporter
(`MeshGenerator.get_element_grid()`, see
`examples/triangular_grid_example.py` and
`examples/structured_buffer_example.py`).

## Summary per Milestone

1. **Quality metrics**: add cached gmsh 2D element quality, generator/centroid
   connectivity reports, skewness, tests, and example diagnostics.
2. **Active-domain filtering workflow**: no core API. Add an example showing how
   to mesh a larger envelope, select active cells externally, and compare quality
   against direct clipping.
3. **Boundary inset/mirror points**: add an opt-in Voronoi construction mode that
   improves boundary-cell center placement and boundary connectivity quality.
4. **Structured-quad transfinite buffer**: add opt-in robust feature alignment for
   line and polygon features in the OCC pipeline, keeping straddle as the default.
5. **Triangular/mixed element-grid output**: add a separate `MeshGenerator`
   exporter for gmsh element polygons, supporting triangles now and mixed tri/quad
   meshes for structured buffers.

## Verification

- Per milestone: add focused tests under `tests/`.
- End-to-end: keep `examples/field_capabilities_example.py` for field and quality
  diagnostics, and add `examples/active_domain_quality_example.py` for the larger
  mesh-envelope workflow.
- Backward compatibility: with new options disabled, current Voronoi output and
  public APIs remain unchanged.
