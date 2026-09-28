"""VOROGRIDGEN adapter: case -> BLN files + vg.in -> vorogridgen.exe -> DISV.

The Fortran binaries run on Windows only. On other platforms ``build`` writes
the inputs and raises ``PlatformUnavailable``; the workflow records that as a
result, and the ``vorogridgen`` job of .github/workflows/benchmark.yml
produces the grids.

Spec translation (manual section 3.3): the per-vertex spacing of every
boundary, line and point is its ``h_f``; the outer and inner boundaries get
``h_max``; refinement polygons get ``max_centroid_separation = h_f``;
``poly_growth_rate = g``, ``max_centroid_separation = h_max``. Control values
not in the spec are those of the example shipped with the program.
VOROGRIDGEN has no barriers, so barrier lines are ordinary inner lines.

If the Lloyd iterations fail, the build is retried with a smaller
``lloyd_fac``, the remedy the program's own error message and documentation
give ("Conceptual error ... consider reducing the LLOYD_FAC", "Missing
triangle local neighbour"). The value used is recorded as ``info["lloyd_fac"]``.

Written centres are cell centroids; the program does not write its seeds.
"""

import platform
import subprocess
import time
from pathlib import Path

from ..case import Case, line_parts
from ..grid import Grid, read_disv

TOOL = "vorogridgen"
MAX_CELLS = 2_000_000
RUN_TIMEOUT_S = 15 * 60
EXAMPLE_CONTROL = {"safety": 0, "max_lloyd": 30, "eps_lloyd": "1.0d-4", "nsdim": 21}
LLOYD_FACS = (0.2, 0.1, 0.05)   # example value first, then the documented remedy
LLOYD_ERRORS = ("consider reducing the LLOYD_FAC", "Missing triangle local neighbour")


class PlatformUnavailable(RuntimeError):
    """The VOROGRIDGEN binaries cannot run on this platform."""


def build(case: Case, scale: float, ws: Path, exe: str | None = None) -> Grid:
    """Write VOROGRIDGEN inputs and, on Windows, run it and read the DISV."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    write_inputs(case, scale, ws, LLOYD_FACS[0])
    if platform.system() != "Windows" or exe is None:
        raise PlatformUnavailable(f"VOROGRIDGEN needs Windows and its exe; inputs written to {ws}")
    t0 = time.perf_counter()
    for lloyd_fac in LLOYD_FACS:
        write_inputs(case, scale, ws, lloyd_fac)
        ok, output = _run(exe, ws, lloyd_fac)
        if ok or not any(e in output for e in LLOYD_ERRORS):
            break
    t1 = time.perf_counter()
    assert ok, f"vorogridgen failed at lloyd_fac={lloyd_fac}; last output: {output.strip()[-500:]}"
    grid = read_disv(ws / "model.disv", TOOL)
    grid.timings = {"mesh_s": t1 - t0, "export_s": time.perf_counter() - t1}
    grid.info["lloyd_fac"] = lloyd_fac
    return grid


def _run(exe: str, ws: Path, lloyd_fac: float) -> tuple:
    """Run vorogridgen once, logging to vorogridgen_lloyd<fac>.log; return (ok, output)."""
    disv = ws / "model.disv"
    disv.unlink(missing_ok=True)
    run = subprocess.run([exe, "vg.in"], cwd=ws, stdin=subprocess.DEVNULL, capture_output=True,
                         text=True, timeout=RUN_TIMEOUT_S)
    output = run.stdout + run.stderr
    (ws / f"vorogridgen_lloyd{lloyd_fac}.log").write_text(output)
    return run.returncode == 0 and disv.exists(), output


def write_inputs(case: Case, scale: float, ws: Path, lloyd_fac: float = LLOYD_FACS[0]) -> Path:
    """Write the BLN files and vg.in for one case; return the vg.in path."""
    h_max = case.h_max * scale
    blocks = [_boundary_block("OUTER_BOUNDARY", ws / "outer_boundary.bln", case.domain.exterior.coords, h_max)]
    for i, ring in enumerate(case.domain.interiors):
        blocks.append(_boundary_block("INNER_BOUNDARY", ws / f"inner_boundary_{i}.bln", ring.coords, h_max))
    points = case.features_of("point")
    if points:
        path = ws / "points.dat"
        _write_bln(path, [(*p.geometry.coords[0], p.h * scale) for p in points])
        blocks.append(f"START INNER_POINTS\n  bln_file={path.name}\nEND INNER_POINTS\n")
    for f in case.features_of("line"):
        for i, part in enumerate(line_parts(f.geometry)):
            path = ws / f"line_{f.id}_{i}.bln"
            _write_bln(path, [(x, y, f.h * scale) for x, y in part.coords])
            blocks.append(f"START INNER_LINE\n  numlines=1\n  bln_file={path.name}\nEND INNER_LINE\n")
    for f in case.features_of("polygon"):
        path = ws / f"poly_{f.id}.bln"
        _write_bln(path, list(f.geometry.exterior.coords))
        blocks.append(f"START INNER_POLYGON\n  bln_file={path.name}\n"
                      f"  max_centroid_separation={f.h * scale:.6g}\nEND INNER_POLYGON\n")
    blocks.append("START MF6\n  xorigin=0.0\n  yorigin=0.0\n  mf6_basename=model\n  nlay 1\nEND MF6\n")
    blocks.append(_control_block(case.growth, h_max, lloyd_fac))
    vg_in = ws / "vg.in"
    vg_in.write_text(f"# {case.id} (tool-benchmark/cases), scale={scale:.6g}\n\n" + "\n".join(blocks))
    return vg_in


def _boundary_block(name: str, path: Path, coords, spacing: float) -> str:
    """A closed-boundary block with a uniform per-vertex spacing."""
    _write_bln(path, [(x, y, spacing) for x, y in coords])
    return f"START {name}\n  bln_file={path.name}\nEND {name}\n"


def _control_block(growth: float, h_max: float, lloyd_fac: float) -> str:
    """CONTROL block; END_CONTROL is spelled as in the shipped example."""
    lines = [f"  poly_growth_rate={growth:.6g}", f"  max_centroid_separation={h_max:.6g}",
             "  out_file_base=grid", f"  max_cells={MAX_CELLS}"]
    lines += [f"  {k}={v}" for k, v in EXAMPLE_CONTROL.items()] + [f"  lloyd_fac={lloyd_fac}"]
    return "START CONTROL\n" + "\n".join(lines) + "\nEND_CONTROL\n"


def _write_bln(path: Path, rows: list) -> None:
    """BLN: a count line, then one 'x, y [spacing]' row per vertex."""
    body = "\n".join(f"{r[0]:.8f}, {r[1]:.8f}" + (f"   {r[2]:.6g}" if len(r) > 2 else "") for r in rows)
    path.write_text(f"{len(rows)},1\n{body}\n")
