# Voronoi grid generator benchmark

Compares vorflow with mf6Voronoi, FloPy (Triangle + `VoronoiGrid`) and
VOROGRIDGEN on shared cases. The plan, fairness rules and metric definitions
are in [docs/benchmark-plan.md](../docs/benchmark-plan.md).

This folder is outside the vorflow package and has its own environment, so the
other tools never become vorflow dependencies.

## Setup

```bash
micromamba env create -f environment.yml
```

or, on top of an existing vorflow environment:

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e .. "mf6Voronoi==0.0.38" "flopy>=3.10" pyyaml psutil
```

MF6 and Triangle go in `.bin/`, from the MODFLOW-ORG executables release:

```bash
get-modflow --subset mf6,triangle .bin
```

VOROGRIDGEN runs on Windows only: put `vorogridgen.exe` in `.bin/`. On other
platforms its adapter writes the input files (`work/<case>/vorogridgen/`) and
the result is recorded as `unavailable`; the CI Windows job fills it in.

## Run

Edit the flags in `workflow.py`'s `__main__` block, then:

```bash
python workflow.py
```

## CI

`.github/workflows/benchmark.yml` runs on pushes that touch `tool-benchmark/`
or the workflow, on pull requests that also touch `src/vorflow/`, and on
demand (`workflow_dispatch`, once the workflow is on the default branch):

- **vorogridgen** (Windows) downloads the freeware from Hydrosymple at run
  time, checks it reproduces its shipped example (6 440 cells), then runs
  `python ci.py windows`.
- **others** (Linux) builds `environment.yml` and runs `python ci.py others`.
- **report** merges both jobs' `results/rows` and `work/**/grid.pkl` and runs
  `python ci.py report`. Tables and figures are in the `benchmark-results`
  artifact.

To merge a CI run into a local checkout, download the `rows-*` artifacts into
`tool-benchmark/` and run `workflow.main(build=False)`.

## Layout

- `cases/`: one YAML per case (inline WKT geometry, sizing spec, MF6 problems).
- `bench/adapters/`: one module per tool, `build(case, scale, ws) -> Grid`.
- `bench/`: common grid form (`grid.py`), metrics as MF6 reads the grid
  (`metrics.py`), verification models (`models.py`), matched-count search
  (`matching.py`), process isolation (`isolate.py`) and figures (`plots.py`).
- `results/rows/`: one JSON file of rows per (case, tool, target), the unit of merging.
- `results/`: `metrics.csv`, `mf6_verification.csv` (compiled from the rows),
  calibration table and figures.
- `ci.py`: one entry point per CI job.
- `work/`: every build's files. `work/`, `results/`, `.venv/` and `.bin/` are
  git-ignored; CI regenerates the results.
