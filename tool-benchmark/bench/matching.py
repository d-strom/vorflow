"""Matched-count mode: tune one global spec scale until NCPL hits the case target.

Cell count scales roughly as ncpl ~ scale**p with p close to -2 (a 2D count).
Each step solves for the scale that would hit the target under that power law,
re-estimating p from the last two builds (clamped to a plausible range).
"""

import math
from pathlib import Path

from .isolate import run_isolated

TOLERANCE = 0.05
MAX_BUILDS = 8
POWER_RANGE = (-3.0, -1.0)


def match_count(tool: str, case, target: int, ws: Path, tool_kwargs: dict,
                tol: float = TOLERANCE, max_builds: int = MAX_BUILDS) -> dict:
    """Build at successive scales until |ncpl/target - 1| <= tol; return the last build."""
    scale, power, history = _first_scale(case, target), -2.0, []
    for i in range(max_builds):
        result = run_isolated(tool, case, scale, Path(ws) / f"build_{i:02d}", tool_kwargs)
        if result["status"] != "ok":
            return {**result, "scale": scale, "history": history, "matched": False}
        ncpl = result["grid"].ncpl
        history.append((scale, ncpl))
        if abs(ncpl / target - 1.0) <= tol:
            return {**result, "scale": scale, "history": history, "matched": True}
        if len(history) >= 2:
            power = _fit_power(history[-2], history[-1], power)
        scale *= (target / ncpl) ** (1.0 / power)
    return {**result, "scale": history[-1][0], "history": history, "matched": False}


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
