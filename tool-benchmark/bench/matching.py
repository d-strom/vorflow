"""Matched-count mode: tune one global spec scale until NCPL hits the case target.

Cell count scales roughly as ncpl ~ scale**p with p close to -2 (a 2D count).
Each step solves for the scale that would hit the target under that power law,
re-estimating p from the last two builds (clamped to a plausible range).

A failed build does not end the search: the same step is retried at nearby
scales (``SCALE_NUDGES``), as a user would. If no build lands within tolerance,
the successful build closest to the target is returned with ``matched=False``.
Failed scales are returned as ``failures``.
"""

import math
from pathlib import Path

from .isolate import run_isolated

TOLERANCE = 0.05
MAX_BUILDS = 8
POWER_RANGE = (-3.0, -1.0)
SCALE_NUDGES = (1.02, 0.98, 1.05)   # retries of a failed step, relative to its planned scale


def match_count(tool: str, case, target: int, ws: Path, tool_kwargs: dict,
                tol: float = TOLERANCE, max_builds: int = MAX_BUILDS) -> dict:
    """Build at successive scales until |ncpl/target - 1| <= tol; return the closest build."""
    planned = scale = _first_scale(case, target)
    power, history, failures, best, nudges = -2.0, [], [], None, None
    for i in range(max_builds):
        result = run_isolated(tool, case, scale, Path(ws) / f"build_{i:02d}", tool_kwargs)
        if result["status"] != "ok":
            failures.append(scale)
            nudges = nudges or iter(SCALE_NUDGES)
            nudge = next(nudges, None)
            if nudge is None:
                break
            scale = planned * nudge
            continue
        nudges = None
        ncpl = result["grid"].ncpl
        history.append((scale, ncpl))
        miss = abs(ncpl / target - 1.0)
        if best is None or miss < best[0]:
            best = (miss, scale, result)
        if miss <= tol:
            break
        if len(history) >= 2:
            power = _fit_power(history[-2], history[-1], power)
        planned = scale = scale * (target / ncpl) ** (1.0 / power)
    if best is None:
        return {**result, "scale": scale, "history": history, "failures": failures, "matched": False}
    miss, scale, result = best
    return {**result, "scale": scale, "history": history, "failures": failures, "matched": miss <= tol}


def _first_scale(case, target: int) -> float:
    """Starting scale: 1 at the case's first target, adjusted by the ncpl ~ scale**-2 law."""
    return (case.targets[0] / target) ** 0.5


def _fit_power(prev: tuple, last: tuple, fallback: float) -> float:
    """Power p in ncpl ~ scale**p from two builds, clamped; fallback if degenerate."""
    (s0, n0), (s1, n1) = prev, last
    if s0 == s1 or n0 == n1:
        return fallback
    p = math.log(n1 / n0) / math.log(s1 / s0)
    return min(max(p, POWER_RANGE[0]), POWER_RANGE[1])
