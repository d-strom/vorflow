"""MF6 verification problems with known head solutions (Tier 0).

After the vorogridgen harness (``../vorogridgen/examples/mesh_accuracy.py``):
a confined single layer, K = 1, top 0, bottom -100 (T = 100). Cells with an
edge on the domain boundary get CHD at the exact head; the error is measured
over the remaining cells as an area-weighted L2 norm and a maximum. The exact
head is evaluated at the centres MF6 is given, since that is where MF6's head
value applies.

Only cells on the *domain* boundary get CHD, so a disconnected interior face
(metrics.disconnected_faces) acts as the no-flow barrier MF6 makes of it and
shows up as head error.

Problems (the ``name`` of a case's ``mf6_problems`` entry):

* ``linear``  -- unit head drop along a direction ``GRADIENT_DEG`` from x, no
  source. A two-point flux on an orthogonal grid reproduces it to roundoff, so
  any error is a grid or centre defect. The oblique direction keeps flow from
  aligning with lattice faces, which would hide defects parallel to it.
* ``mms``     -- manufactured h = cos(a x') cos(a y'), a = pi / L, in axes
  x', y' rotated by ``GRADIENT_DEG`` about the domain centre (so the field is
  not aligned with square lattices), with the source W = -T lap(h) = 2 T a^2 h
  applied per cell as WEL. Error is driven by skewness and grading; this is
  the convergence problem.
* ``thiem``   -- steady radial flow to a well at a circle's centre,
  h = h0 - q / (2 pi T) ln(R / r). The well cell and its neighbours are
  excluded from the error (the well-cell head is Peaceman's, not h(r=0)).
  Parameters: ``well`` (feature id), ``rate`` (extraction, default 100).
* ``barrier`` -- unit head drop normal to a straight barrier line crossing the
  domain, with an HFB of hydraulic characteristic ``hydchr`` on every
  connection whose centre-to-centre segment crosses the line. Exact solution:
  series resistance, linear on each side with a jump at the line.
  Parameters: ``barrier`` (feature id), ``hydchr`` (default 1e-3).
"""

from dataclasses import dataclass, field
from pathlib import Path

import flopy
import numpy as np
import shapely

from .case import Case
from .grid import Grid, cell_polygons, to_gridprops
from .metrics import boundary_cells, face_table

TOP, BOT, K = 0.0, -100.0, 1.0
T = K * (TOP - BOT)
GRADIENT_DEG = 30.0
PROBLEMS = ("linear", "mms", "thiem", "barrier")


@dataclass
class Setup:
    """Exact head plus the stresses one problem needs on one grid."""
    h_exact: np.ndarray
    wel: list = field(default_factory=list)       # (cell, rate)
    hfb: list = field(default_factory=list)       # (cell_i, cell_j, hydchr)
    exclude: np.ndarray | None = None             # cells left out of the error


def run_problem(grid: Grid, problem: dict, case: Case, centres: str, xt3d: bool,
                ws: Path, mf6_exe: str) -> dict:
    """Solve one problem on one grid and return the error against the exact head."""
    assert problem["name"] in PROBLEMS, f"unknown MF6 problem {problem['name']!r}"
    xy = grid.centres(centres)
    faces = face_table(grid)
    bnd = boundary_cells(grid, faces, case)
    polys = cell_polygons(grid)
    area = shapely.area(polys)
    setup = SETUPS[problem["name"]](problem, case, xy, faces, polys, bnd)
    gwf, sim = _build_model(grid, centres, setup, bnd, xt3d, ws, mf6_exe)
    success, _ = sim.run_simulation(silent=True)
    if not success:
        return {"mf6_ok": False, "n_hfb": len(setup.hfb)}
    err = gwf.output.head().get_data().ravel() - setup.h_exact
    scored = ~bnd if setup.exclude is None else ~bnd & ~setup.exclude
    return {
        "mf6_ok": True,
        "l2": float(np.sqrt(np.sum(area[scored] * err[scored] ** 2) / np.sum(area[scored]))),
        "max_err": float(np.abs(err[scored]).max()),
        "n_chd": int(bnd.sum()),
        "n_hfb": len(setup.hfb),
    }


def _setup_linear(problem, case, xy, faces, polys, bnd) -> Setup:
    """Unit head drop along GRADIENT_DEG; no stresses."""
    c, s = np.cos(np.radians(GRADIENT_DEG)), np.sin(np.radians(GRADIENT_DEG))
    x0, y0, x1, y1 = case.domain.bounds
    h = ((xy[:, 0] - x0) * c + (xy[:, 1] - y0) * s) / ((x1 - x0) * c + (y1 - y0) * s)
    return Setup(h_exact=h)


def _setup_mms(problem, case, xy, faces, polys, bnd) -> Setup:
    """Manufactured cos*cos head with its source applied as WEL on non-CHD cells."""
    x0, y0, x1, y1 = case.domain.bounds
    a = np.pi / max(x1 - x0, y1 - y0)
    c, s = np.cos(np.radians(GRADIENT_DEG)), np.sin(np.radians(GRADIENT_DEG))
    dx, dy = xy[:, 0] - 0.5 * (x0 + x1), xy[:, 1] - 0.5 * (y0 + y1)
    h = np.cos(a * (c * dx + s * dy)) * np.cos(a * (c * dy - s * dx))
    rate = 2.0 * T * a * a * h * shapely.area(polys)       # source W times cell area
    wel = [(int(c), float(rate[c])) for c in np.flatnonzero(~bnd)]
    return Setup(h_exact=h, wel=wel)


def _setup_thiem(problem, case, xy, faces, polys, bnd) -> Setup:
    """Steady radial flow to a pumped well at the domain centre."""
    well = case.feature(problem["well"]).geometry
    rate = float(problem.get("rate", 100.0))
    radius = case.domain.exterior.distance(well)
    r = np.hypot(xy[:, 0] - well.x, xy[:, 1] - well.y)
    well_cell = _cell_containing(polys, well)
    exclude = np.zeros(len(xy), dtype=bool)
    exclude[well_cell] = True
    exclude[faces["cj"][faces["ci"] == well_cell]] = True
    exclude[faces["ci"][faces["cj"] == well_cell]] = True
    with np.errstate(divide="ignore"):
        h = -rate / (2.0 * np.pi * T) * np.log(radius / r)
    h[exclude] = 0.0                     # never scored; keeps the CHD/IC values finite
    return Setup(h_exact=h, wel=[(well_cell, -rate)], exclude=exclude)


def _cell_containing(polys: list, point) -> int:
    """The cell a modeller would put the well in: the first one covering the point."""
    hits = np.flatnonzero(shapely.intersects_xy(polys, point.x, point.y))
    assert len(hits) > 0, f"no cell contains the well at {point}"
    return int(hits[0])


def _setup_barrier(problem, case, xy, faces, polys, bnd) -> Setup:
    """Unit head drop normal to a straight barrier, HFB on every crossing connection."""
    line = case.feature(problem["barrier"]).geometry
    hydchr = float(problem.get("hydchr", 1e-3))
    p0, p1 = np.asarray(line.coords[0]), np.asarray(line.coords[-1])
    normal = np.array([p1[1] - p0[1], p0[0] - p1[0]]) / np.hypot(*(p1 - p0))
    normal = normal if normal[0] >= 0 else -normal
    s = (xy - p0) @ normal
    s_dom = (np.asarray(case.domain.exterior.coords) - p0) @ normal
    resistance = 1.0 / (hydchr * (TOP - BOT))            # across the barrier, per unit width
    q = 1.0 / ((s_dom.max() - s_dom.min()) / T + resistance)
    h = 1.0 - q * (s - s_dom.min()) / T - np.where(s > 0, q * resistance, 0.0)
    crossing = np.sign(s[faces["ci"]]) != np.sign(s[faces["cj"]])
    hfb = [(int(i), int(j), hydchr) for i, j in zip(faces["ci"][crossing], faces["cj"][crossing])]
    return Setup(h_exact=h, hfb=hfb)


SETUPS = {"linear": _setup_linear, "mms": _setup_mms, "thiem": _setup_thiem, "barrier": _setup_barrier}


def _build_model(grid: Grid, centres: str, setup: Setup, bnd: np.ndarray,
                 xt3d: bool, ws: Path, mf6_exe: str) -> tuple:
    """Write the steady single-layer model; return (gwf, sim)."""
    sim = flopy.mf6.MFSimulation(sim_name="bench", sim_ws=str(ws), exe_name=mf6_exe)
    flopy.mf6.ModflowTdis(sim, nper=1, perioddata=[(1.0, 1, 1.0)])
    flopy.mf6.ModflowIms(sim, complexity="SIMPLE", inner_dvclose=1e-10, outer_dvclose=1e-10,
                         inner_maximum=500, linear_acceleration="BICGSTAB")
    gwf = flopy.mf6.ModflowGwf(sim, modelname="bench")
    flopy.mf6.ModflowGwfdisv(gwf, nlay=1, top=TOP, botm=BOT, **to_gridprops(grid, centres))
    flopy.mf6.ModflowGwfnpf(gwf, icelltype=0, k=K, xt3doptions=xt3d)
    flopy.mf6.ModflowGwfic(gwf, strt=float(np.mean(setup.h_exact[bnd])))
    chd = [[(0, int(c)), float(setup.h_exact[c])] for c in np.flatnonzero(bnd)]
    flopy.mf6.ModflowGwfchd(gwf, stress_period_data=chd)
    if setup.wel:
        flopy.mf6.ModflowGwfwel(gwf, stress_period_data=[[(0, c), q] for c, q in setup.wel])
    if setup.hfb:
        flopy.mf6.ModflowGwfhfb(gwf, stress_period_data=[[(0, i), (0, j), c] for i, j, c in setup.hfb])
    flopy.mf6.ModflowGwfoc(gwf, head_filerecord="bench.hds", saverecord=[("HEAD", "LAST")])
    sim.write_simulation(silent=True)
    return gwf, sim
