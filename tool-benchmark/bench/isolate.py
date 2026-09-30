"""Run one adapter build in a fresh process: timing, peak memory, timeout.

A spawned child per build keeps Gmsh's process-global state and each tool's
imports out of the parent, makes peak RSS attributable to one build, and lets
a hung build be killed.
"""

import multiprocessing as mp
import platform
import queue
import time
import traceback
from pathlib import Path

TIMEOUT_S = 30 * 60


def run_isolated(tool: str, case, scale: float, ws: Path, tool_kwargs: dict,
                 timeout: float = TIMEOUT_S) -> dict:
    """Build one grid in a child process; return {status, grid, wall_s, peak_rss_mb, build_rss_mb, error}."""
    ctx = mp.get_context("spawn")
    results = ctx.Queue()
    proc = ctx.Process(target=_child, args=(results, tool, case, scale, ws, tool_kwargs))
    proc.start()
    try:
        result = results.get(timeout=timeout)
    except queue.Empty:                 # timed out; the child is killed below
        result = {"status": "timeout", "grid": None, "error": f"no result after {timeout} s"}
    proc.join(timeout=10)
    if proc.is_alive():
        proc.kill()
    return result


def _child(results, tool: str, case, scale: float, ws: Path, tool_kwargs: dict) -> None:
    """Child entry point: build, then report the grid or the failure."""
    from .adapters import get_adapter
    from .adapters.vorogridgen_adapter import PlatformUnavailable

    adapter = get_adapter(tool)
    baseline_mb = _current_rss_mb()     # after importing the tool
    t0 = time.perf_counter()
    try:
        grid = adapter.build(case, scale, Path(ws), **tool_kwargs)
        result = {"status": "ok", "grid": grid, "error": ""}
    except PlatformUnavailable as err:
        result = {"status": "unavailable", "grid": None, "error": str(err)}
    except Exception:                   # any tool failure is a benchmark result, not a crash
        result = {"status": "failed", "grid": None, "error": traceback.format_exc(limit=5)}
    result["wall_s"] = time.perf_counter() - t0
    result["peak_rss_mb"] = _peak_rss_mb()
    result["build_rss_mb"] = result["peak_rss_mb"] - baseline_mb
    results.put(result)


def _current_rss_mb() -> float:
    """Current memory of this process in MB."""
    import psutil
    return psutil.Process().memory_info().rss / 2**20


def _peak_rss_mb() -> float:
    """Peak memory of this process in MB (excludes external executables it ran)."""
    if platform.system() == "Windows":
        import psutil
        return psutil.Process().memory_info().peak_wset / 2**20
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if platform.system() == "Darwin" else peak / 2**10       # bytes vs KB
