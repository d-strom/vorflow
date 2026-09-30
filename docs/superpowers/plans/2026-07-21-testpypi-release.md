# TestPyPI Release Rehearsal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, validate, and publish `vorflow==0.1.0rc1` to TestPyPI through a manually approved GitHub Trusted Publishing workflow, then stop before real PyPI.

**Architecture:** Keep the release version and metadata in `pyproject.toml`, validate the generated wheel and source distribution with a repository script, and build the archives once in a tag-triggered GitHub job. A separate least-privilege publish job downloads those exact archives, pauses at the protected `testpypi` environment, and uploads only to TestPyPI.

**Tech Stack:** Python 3.10-3.12, setuptools 77+, pytest, Ruff, `build`, Twine, GitHub Actions, PyPI Trusted Publishing (OIDC), TestPyPI.

## Global Constraints

- Start release implementation only after `gmshflow_missing` is integrated into a clean `main`; preserve all unrelated user changes.
- Candidate version is exactly `0.1.0rc1`; reserve `0.1.0` for a future production release.
- Do not change mesh-generation behaviour or public runtime APIs.
- Do not create a real-PyPI job, environment, credential, tag, or upload.
- Oscar is the primary author and current maintainer; retain rhugman as an original author without an email in package metadata.
- Keep the existing MIT copyright notice unchanged.
- Runtime floors are `numpy>=1.24`, `pandas>=1.5`, `geopandas>=0.13`, `shapely>=2.0`, `scipy>=1.10`, and `gmsh>=4.11`; add no upper caps.
- Test the exact available floor releases `numpy==1.24.0`, `pandas==1.5.0`, `geopandas==0.13.0`, `shapely==2.0.0`, `scipy==1.10.0`, and `gmsh==4.11.1` on Python 3.10.
- TestPyPI account registration, email verification, recovery settings, and publishing approval are user-owned manual steps. Never request or store passwords, recovery codes, two-factor codes, or API tokens.
- TestPyPI is temporary rehearsal infrastructure, not a permanent release archive.

## Pre-execution Gate

Before Task 1, use `superpowers:finishing-a-development-branch` to review and integrate `gmshflow_missing`. Do not automatically stage the current unrelated files under `docs/assets/`, `docs/auto-exponential-field-math-review.md`, or `benchmarks/`.

Verify the baseline:

```powershell
git branch --show-current
git status --short
git log -1 --oneline
```

Expected after integration: branch `main`, an empty status, and `main` containing design commits `ec0964d` and `612eab8`. Create a focused release-preparation branch or worktree before Task 1.

## File Structure

**Create:**

- `CHANGELOG.md` — release-candidate history.
- `MANIFEST.in` — explicit source-distribution exclusions.
- `examples/basic_usage.py` — canonical runnable smoke example.
- `scripts/check_dist.py` — archive and built-metadata validator.
- `tests/test_check_dist.py` — focused validator tests.
- `tests/test_release_metadata.py` — static release-metadata tests.
- `.github/workflows/testpypi.yml` — tag-triggered TestPyPI build and publish workflow.

**Modify:**

- `.gitignore` — ignore `.superpowers/` visual state.
- `ROADMAP.md` — one active milestone plus completed history.
- `docs/roadmap/06-pypi-publishing.md` — candidate scope and status.
- `pyproject.toml` — version, authorship, dependencies, licence, keywords, build and release tools.
- `src/vorflow/__init__.py` — non-duplicated source fallback.
- `README.md` — PyPI install path, absolute links, canonical example, optional export note.
- `.github/workflows/python-app.yml` — minimum-dependency CI job.

---

### Task 1: Establish the Current Roadmap and Ignore Local Visual State

**Files:**

- Modify: `.gitignore`
- Modify: `ROADMAP.md`
- Modify: `docs/roadmap/06-pypi-publishing.md`

**Interfaces:**

- Consumes: approved release design in `docs/superpowers/specs/2026-07-20-testpypi-release-design.md`.
- Produces: one active roadmap objective, preserved completed history, and no tracked `.superpowers/` state.

- [ ] **Step 1: Inspect the user's existing roadmap edits before changing them**

```powershell
git diff -- ROADMAP.md docs/roadmap/06-pypi-publishing.md .gitignore
```

Expected: reviewable output; do not overwrite text that is not superseded by the approved design.

- [ ] **Step 2: Ignore the visual companion directory**

Add this exact entry beside other local development exclusions in `.gitignore`:

```gitignore
# Superpowers visual brainstorming state
.superpowers/
```

- [ ] **Step 3: Replace the stale roadmap sequencing with current/completed sections**

Preserve the existing context and decisions, but make the milestone area read:

```markdown
## Current Objective

| # | Milestone | Status | Risk | Behavior change | Doc |
|---|-----------|--------|------|-----------------|-----|
| 6 | First PyPI release | In progress | Low | None (packaging only) | [06-pypi-publishing.md](docs/roadmap/06-pypi-publishing.md) |

The current release scope is a `0.1.0rc1` TestPyPI rehearsal. A successful
rehearsal changes this status to **TestPyPI verified**. The milestone becomes
**Done** only after `vorflow` is published to real PyPI.

## Completed Milestones

| # | Milestone | Status | Doc |
|---|-----------|--------|-----|
| 1 | Quality metrics | Done | [01-quality-metrics.md](docs/roadmap/01-quality-metrics.md) |
| 2 | Active-domain filtering workflow | Done (example) | [02-robust-clipping.md](docs/roadmap/02-robust-clipping.md) |
| 3 | Boundary inset/mirror points | Done | [03-boundary-mirror-points.md](docs/roadmap/03-boundary-mirror-points.md) |
| 4 | Structured-quad transfinite buffer | Done | [04-structured-quad-buffer.md](docs/roadmap/04-structured-quad-buffer.md) |
| 5 | Triangular/mixed element-grid output | Done | [05-triangular-grid-output.md](docs/roadmap/05-triangular-grid-output.md) |
```

Remove `Suggested Sequencing`, because it describes already completed work. Keep the completed-capability summary and verification history.

- [ ] **Step 4: Mark the detailed publishing milestone as in progress**

Change its header metadata to:

```markdown
**Status:** in progress · **Current target:** TestPyPI `0.1.0rc1` · **Risk:** low · **Behavior change:** none (packaging/metadata only)
```

Add a scope note immediately after the goal:

```markdown
This milestone is being rehearsed on TestPyPI first. The rehearsal stops after
verifying `0.1.0rc1`; real PyPI publication remains a separate approval gate.
```

- [ ] **Step 5: Verify the roadmap no longer contains obsolete sequencing**

```powershell
rg -n "Current Objective|Completed Milestones|TestPyPI|Suggested Sequencing|Implement the remaining" ROADMAP.md docs/roadmap/06-pypi-publishing.md
git diff --check
```

Expected: current/completed headings and TestPyPI scope are found; obsolete sequencing phrases are absent; `git diff --check` is silent.

- [ ] **Step 6: Commit the roadmap cleanup**

```powershell
git add .gitignore ROADMAP.md docs/roadmap/06-pypi-publishing.md
git commit -m "docs: focus roadmap on TestPyPI release"
```

### Task 2: Make Release Metadata Explicit and Testable

**Files:**

- Create: `tests/test_release_metadata.py`
- Modify: `pyproject.toml`
- Modify: `src/vorflow/__init__.py`

**Interfaces:**

- Consumes: PEP 621/639 metadata through setuptools.
- Produces: authoritative version `0.1.0rc1` and runtime `vorflow.__version__` from installed metadata.

- [ ] **Step 1: Write failing static metadata tests**

Create `tests/test_release_metadata.py`:

```python
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_is_complete():
    with (ROOT / "pyproject.toml").open("rb") as stream:
        data = tomllib.load(stream)

    project = data["project"]
    assert data["build-system"]["requires"] == ["setuptools>=77.0.3"]
    assert project["version"] == "0.1.0rc1"
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [
        {"name": "Oscar Sanchez", "email": "oscarfasanchez@gmail.com"},
        {"name": "rhugman"},
    ]
    assert project["maintainers"] == [
        {"name": "Oscar Sanchez", "email": "oscarfasanchez@gmail.com"}
    ]
    assert project["dependencies"] == [
        "numpy>=1.24",
        "pandas>=1.5",
        "geopandas>=0.13",
        "shapely>=2.0",
        "scipy>=1.10",
        "gmsh>=4.11",
    ]
    assert "License :: OSI Approved :: MIT License" not in project["classifiers"]


def test_source_fallback_is_not_a_duplicate_release_version():
    source = (ROOT / "src" / "vorflow" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0+unknown"' in source
    assert '__version__ = "0.0.2"' not in source
```

- [ ] **Step 2: Run the tests and confirm they fail for the current metadata**

```powershell
python -m pip install "tomli>=2; python_version < '3.11'"
pytest tests/test_release_metadata.py -v
```

Expected: failures showing `setuptools>=61.0`, version `0.0.2`, legacy licence metadata, unbounded dependencies, and the duplicated fallback.

- [ ] **Step 3: Update `pyproject.toml` metadata**

Apply these exact values:

```toml
[build-system]
requires = ["setuptools>=77.0.3"]
build-backend = "setuptools.build_meta"

[project]
name = "vorflow"
version = "0.1.0rc1"
description = "Voronoi mesh generation for MODFLOW 6 using Gmsh and GeoPandas"
readme = "README.md"
license = "MIT"
license-files = ["LICENSE"]
authors = [
    {name = "Oscar Sanchez", email = "oscarfasanchez@gmail.com"},
    {name = "rhugman"},
]
maintainers = [
    {name = "Oscar Sanchez", email = "oscarfasanchez@gmail.com"},
]
requires-python = ">=3.10"
keywords = ["modflow", "groundwater", "voronoi", "mesh", "gmsh"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Science/Research",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Hydrology",
]
dependencies = [
    "numpy>=1.24",
    "pandas>=1.5",
    "geopandas>=0.13",
    "shapely>=2.0",
    "scipy>=1.10",
    "gmsh>=4.11",
]
```

Add the changelog URL and release tools:

```toml
[project.urls]
Repository = "https://github.com/oscarfasanchez/vorflow_os"
Issues = "https://github.com/oscarfasanchez/vorflow_os/issues"
Changelog = "https://github.com/oscarfasanchez/vorflow_os/blob/main/CHANGELOG.md"

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-cov",
    "black",
    "ruff",
    "jupyter",
    "matplotlib",
    "build>=1.2",
    "twine>=6",
    "tomli>=2; python_version < '3.11'",
]
```

Keep the existing `examples` extra unchanged.

- [ ] **Step 4: Replace the duplicate fallback in `src/vorflow/__init__.py`**

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vorflow")
except PackageNotFoundError:
    __version__ = "0+unknown"
```

Leave the imports, public classes, logging setup, and `__all__` unchanged.

- [ ] **Step 5: Run the focused tests**

```powershell
pytest tests/test_release_metadata.py -v
```

Expected: `2 passed`.

- [ ] **Step 6: Commit the metadata change**

```powershell
git add pyproject.toml src/vorflow/__init__.py tests/test_release_metadata.py
git commit -m "build: prepare 0.1.0rc1 metadata"
```

### Task 3: Create a Canonical Basic Usage Example and PyPI-Facing Documentation

**Files:**

- Create: `examples/basic_usage.py`
- Create: `CHANGELOG.md`
- Modify: `README.md`

**Interfaces:**

- Consumes: public `ConceptualMesh`, `MeshGenerator`, and `VoronoiTessellator` APIs.
- Produces: `main()` returning a non-empty GeoDataFrame and a README that renders outside GitHub.

- [ ] **Step 1: Add a failing test for the canonical example**

Append to `tests/test_release_metadata.py`:

```python
import os
import subprocess
import sys


def test_basic_usage_script_runs_from_a_clean_directory(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "basic_usage.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Generated " in result.stdout
    assert " Voronoi cells" in result.stdout
```

- [ ] **Step 2: Run the test and verify the missing script causes failure**

```powershell
pytest tests/test_release_metadata.py::test_basic_usage_script_runs_from_a_clean_directory -v
```

Expected: FAIL because `examples/basic_usage.py` does not exist.

- [ ] **Step 3: Create `examples/basic_usage.py`**

```python
"""Minimal end-to-end vorflow example used by release smoke tests."""

from shapely.geometry import LineString, Point, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator


def main():
    domain = box(0, 0, 200, 200)
    well_point = Point(25, 25)
    fault_line = LineString([(100, 0), (100, 150)])

    blueprint = ConceptualMesh(crs="EPSG:3857")
    blueprint.add_polygon(domain, zone_id=1)
    blueprint.add_point(
        well_point,
        point_id="Well-A",
        resolution=2,
        growth_factor=1.2,
    )
    blueprint.add_line(
        fault_line,
        line_id="Fault-1",
        resolution=1,
        is_barrier=True,
    )

    clean_polys, clean_lines, clean_pts = blueprint.generate()
    mesher = MeshGenerator(background_lc=100)
    mesher.generate(clean_polys, clean_lines, clean_pts)

    tessellator = VoronoiTessellator(mesher, blueprint, clip_to_boundary=True)
    grid_gdf = tessellator.generate()
    if grid_gdf.empty:
        raise RuntimeError("Basic Usage generated an empty Voronoi grid")
    return grid_gdf


if __name__ == "__main__":
    grid = main()
    print(f"Generated {len(grid)} Voronoi cells")
```

- [ ] **Step 4: Run the canonical example test**

```powershell
pytest tests/test_release_metadata.py::test_basic_usage_script_runs_from_a_clean_directory -v
```

Expected: PASS and subprocess output containing `Generated ... Voronoi cells`.

- [ ] **Step 5: Rewrite the README installation section**

Use this exact structure:

````markdown
## Installation

Install the latest published release:

```bash
pip install vorflow
```

`vorflow` requires Python 3.10 or newer.

### Development installation

Clone the repository and install it in editable mode:

```bash
pip install -e .[dev]
```

For plotting examples and notebooks without all development tools:

```bash
pip install -e .[examples]
```

Alternatively, create the Conda development environment from
[`etc/environment.yml`](https://github.com/oscarfasanchez/vorflow_os/blob/main/etc/environment.yml).
````

Keep the Basic Usage code aligned with `examples/basic_usage.py`, stop after the non-empty GeoDataFrame is created, and add:

````markdown
### Optional file export

GeoPandas writes formats such as Shapefile and GeoPackage through an I/O engine
such as Pyogrio or Fiona. Install one of those engines before calling:

```python
grid_gdf.to_file("mf6_grid.gpkg", driver="GPKG")
```
````

Replace repository-relative links with:

```markdown
[examples/](https://github.com/oscarfasanchez/vorflow_os/tree/main/examples)
[ROADMAP.md](https://github.com/oscarfasanchez/vorflow_os/blob/main/ROADMAP.md)
[LICENSE](https://github.com/oscarfasanchez/vorflow_os/blob/main/LICENSE)
```

- [ ] **Step 6: Create `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to `vorflow` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0rc1] - 2026-07-21

### Added

- Voronoi and triangular/mixed-element grid generation for MODFLOW 6 workflows.
- Mesh-quality and connectivity diagnostics.
- Optional boundary inset/mirror points and structured quad buffers.
- Explicit mesh-size growth fields and runnable examples.
- Cross-platform tests and TestPyPI release automation.

### Changed

- Prepared project metadata, installation documentation, and dependency floors
  for the first public release candidate.

[Unreleased]: https://github.com/oscarfasanchez/vorflow_os/compare/v0.1.0rc1...HEAD
[0.1.0rc1]: https://github.com/oscarfasanchez/vorflow_os/tree/v0.1.0rc1
```

- [ ] **Step 7: Check links and documentation whitespace**

```powershell
rg -n "\]\((examples/|ROADMAP.md|LICENSE|etc/environment.yml)\)" README.md
git diff --check
```

Expected: the relative-link search returns no matches; whitespace check is silent.

- [ ] **Step 8: Commit the example and documentation**

```powershell
git add README.md CHANGELOG.md examples/basic_usage.py tests/test_release_metadata.py
git commit -m "docs: add release candidate usage and changelog"
```

### Task 4: Enforce Distribution Contents and Built Metadata

**Files:**

- Create: `MANIFEST.in`
- Create: `scripts/check_dist.py`
- Create: `tests/test_check_dist.py`
- Modify: `pyproject.toml`

**Interfaces:**

- Consumes: a directory containing one wheel and one `.tar.gz` source distribution.
- Produces: exit code 0 for valid `vorflow` archives; a descriptive exception and nonzero exit for forbidden contents or incorrect metadata.

- [ ] **Step 1: Write failing validator unit tests**

Create `tests/test_check_dist.py`:

```python
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_dist.py"
SPEC = importlib.util.spec_from_file_location("check_dist", SCRIPT)
check_dist = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_dist)


def test_version_from_tag():
    assert check_dist.version_from_tag("v0.1.0rc1") == "0.1.0rc1"


def test_version_from_tag_rejects_production_tag():
    try:
        check_dist.version_from_tag("v0.1.0")
    except ValueError as error:
        assert "release-candidate" in str(error)
    else:
        raise AssertionError("production tag was accepted")


def test_forbidden_members_are_reported():
    members = [
        "vorflow-0.1.0rc1/src/vorflow/__init__.py",
        "vorflow-0.1.0rc1/docs/private-plan.md",
        "vorflow-0.1.0rc1/src/vorflow/vorflow.code-workspace",
    ]
    assert check_dist.forbidden_members(members) == [members[1], members[2]]
```

- [ ] **Step 2: Run the tests and confirm import failure**

```powershell
pytest tests/test_check_dist.py -v
```

Expected: collection fails because `scripts/check_dist.py` does not exist.

- [ ] **Step 3: Create `scripts/check_dist.py`**

```python
"""Validate vorflow wheel/sdist contents and release metadata."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


FORBIDDEN_DIRECTORIES = {".conda", "benchmarks", "docs", "__pycache__"}
EXPECTED_REQUIREMENTS = {
    "numpy>=1.24",
    "pandas>=1.5",
    "geopandas>=0.13",
    "shapely>=2.0",
    "scipy>=1.10",
    "gmsh>=4.11",
}


def version_from_tag(tag: str) -> str:
    if not tag.startswith("v") or "rc" not in tag:
        raise ValueError(f"expected a release-candidate tag, received {tag!r}")
    return tag[1:]


def forbidden_members(names: list[str]) -> list[str]:
    result = []
    for name in names:
        parts = PurePosixPath(name).parts
        if (
            any(part in FORBIDDEN_DIRECTORIES for part in parts)
            or name.endswith(".code-workspace")
            or name.endswith((".pyc", ".pyo"))
        ):
            result.append(name)
    return result


def _one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"expected one {pattern} in {directory}, found {matches}")
    return matches[0]


def _require_suffix(names: list[str], suffix: str) -> None:
    if not any(name.endswith(suffix) for name in names):
        raise ValueError(f"archive is missing required path ending in {suffix!r}")


def _normalized_requirement(value: str) -> str:
    return value.replace(" ", "")


def validate_wheel(wheel: Path, expected_version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        bad = forbidden_members(names)
        if bad:
            raise ValueError(f"wheel contains forbidden members: {bad}")
        _require_suffix(names, "vorflow/__init__.py")
        _require_suffix(names, ".dist-info/licenses/LICENSE")
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise ValueError("wheel has no .dist-info/METADATA")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))

    if metadata["Name"].lower().replace("_", "-") != "vorflow":
        raise ValueError(f"unexpected project name: {metadata['Name']}")
    if metadata["Version"] != expected_version:
        raise ValueError(
            f"wheel version {metadata['Version']} does not match {expected_version}"
        )
    if metadata["Requires-Python"] != ">=3.10":
        raise ValueError(f"unexpected Requires-Python: {metadata['Requires-Python']}")
    requirements = {
        _normalized_requirement(value)
        for value in metadata.get_all("Requires-Dist", [])
        if "extra==" not in _normalized_requirement(value)
    }
    if requirements != EXPECTED_REQUIREMENTS:
        raise ValueError(f"unexpected runtime requirements: {requirements}")
    if metadata["License-Expression"] != "MIT":
        raise ValueError("wheel does not declare the MIT SPDX expression")
    if "LICENSE" not in metadata.get_all("License-File", []):
        raise ValueError("wheel metadata does not declare LICENSE")
    if "Oscar Sanchez" not in (metadata["Author-email"] or ""):
        raise ValueError("primary author is missing from wheel metadata")
    if "rhugman" not in (metadata["Author"] or ""):
        raise ValueError("original author is missing from wheel metadata")
    if "Oscar Sanchez" not in (metadata["Maintainer-email"] or ""):
        raise ValueError("maintainer is missing from wheel metadata")
    project_urls = set(metadata.get_all("Project-URL", []))
    expected_urls = {
        "Repository, https://github.com/oscarfasanchez/vorflow_os",
        "Issues, https://github.com/oscarfasanchez/vorflow_os/issues",
        "Changelog, https://github.com/oscarfasanchez/vorflow_os/blob/main/CHANGELOG.md",
    }
    if not expected_urls.issubset(project_urls):
        raise ValueError(f"wheel is missing project URLs: {expected_urls - project_urls}")


def validate_sdist(sdist: Path, expected_version: str) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
    bad = forbidden_members(names)
    if bad:
        raise ValueError(f"sdist contains forbidden members: {bad}")
    root = f"vorflow-{expected_version}/"
    for required in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "src/vorflow/__init__.py",
    ):
        if root + required not in names:
            raise ValueError(f"sdist is missing {root + required}")


def validate_dist(directory: Path, expected_version: str) -> None:
    wheel = _one(directory, "*.whl")
    sdist = _one(directory, "*.tar.gz")
    validate_wheel(wheel, expected_version)
    validate_sdist(sdist, expected_version)
    print(f"Validated {wheel.name} and {sdist.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    version_group = parser.add_mutually_exclusive_group(required=True)
    version_group.add_argument("--expected-version")
    version_group.add_argument("--expected-tag")
    args = parser.parse_args()
    expected = (
        version_from_tag(args.expected_tag)
        if args.expected_tag
        else args.expected_version
    )
    validate_dist(args.directory, expected)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run validator unit tests**

```powershell
pytest tests/test_check_dist.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Make setuptools discovery and source exclusions explicit**

Add to `pyproject.toml`:

```toml
[tool.setuptools]
package-dir = {"" = "src"}
include-package-data = false

[tool.setuptools.packages.find]
where = ["src"]
```

Create `MANIFEST.in`:

```text
prune .conda
prune benchmarks
prune docs
global-exclude *.code-workspace
global-exclude __pycache__
global-exclude *.py[cod]
```

- [ ] **Step 6: Run focused tests and commit**

```powershell
pytest tests/test_check_dist.py tests/test_release_metadata.py -v
git add MANIFEST.in pyproject.toml scripts/check_dist.py tests/test_check_dist.py
git commit -m "build: validate release archives"
```

Expected: focused tests pass and the commit contains only packaging-boundary files.

### Task 5: Prove the Advertised Minimum Dependencies in CI

**Files:**

- Modify: `.github/workflows/python-app.yml`

**Interfaces:**

- Consumes: exact floor releases on Python 3.10.
- Produces: a CI result proving or disproving the dependency floors.

- [ ] **Step 1: Add the minimum-dependencies job**

Append under `jobs:` at the same indentation as `lint` and `test`:

```yaml
  minimum-dependencies:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.10"
          cache: pip
      - name: Install Gmsh system library
        run: sudo apt-get update && sudo apt-get install --yes libglu1-mesa
      - name: Install minimum runtime dependencies
        run: >-
          python -m pip install
          numpy==1.24.0
          pandas==1.5.0
          geopandas==0.13.0
          shapely==2.0.0
          scipy==1.10.0
          gmsh==4.11.1
      - name: Install vorflow development tools
        run: python -m pip install --no-deps -e . && python -m pip install pytest pytest-cov tomli
      - name: Verify installed dependency versions
        run: >-
          python -c "import geopandas, gmsh, numpy, pandas, scipy, shapely;
          assert numpy.__version__ == '1.24.0';
          assert pandas.__version__ == '1.5.0';
          assert geopandas.__version__ == '0.13.0';
          assert shapely.__version__ == '2.0.0';
          assert scipy.__version__ == '1.10.0';
          assert gmsh.__version__ == '4.11.1'"
      - name: Run tests at dependency floors
        run: pytest --cov=vorflow --cov-report=term-missing
```

- [ ] **Step 2: Verify the existing nine-job matrix is unchanged**

```powershell
rg -n "ubuntu-latest|windows-latest|macos-latest|3.10|3.11|3.12|minimum-dependencies" .github/workflows/python-app.yml
git diff --check
```

Expected: all three operating systems and Python versions remain, plus the new minimum job.

- [ ] **Step 3: Run the local suite before relying on CI**

```powershell
ruff check src tests scripts
pytest -v
```

Expected: Ruff and pytest pass. If the remote minimum job later fails because an advertised floor is incompatible, stop and raise that floor to the lowest demonstrated passing release before continuing.

- [ ] **Step 4: Commit the CI check**

```powershell
git add .github/workflows/python-app.yml
git commit -m "ci: test minimum supported dependencies"
```

### Task 6: Add the TestPyPI-Only Trusted Publishing Workflow

**Files:**

- Create: `.github/workflows/testpypi.yml`

**Interfaces:**

- Consumes: tag `v0.1.0rc1`, verified source, protected environment `testpypi`.
- Produces: one internal artifact named `python-package-distributions` and a TestPyPI upload using short-lived OIDC credentials.

- [ ] **Step 1: Create `.github/workflows/testpypi.yml`**

```yaml
name: Publish release candidate to TestPyPI

on:
  push:
    tags:
      - "v*rc*"

permissions:
  contents: read

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
      - name: Install Gmsh system library
        run: sudo apt-get update && sudo apt-get install --yes libglu1-mesa
      - name: Install build and test tools
        run: python -m pip install -e ".[dev]"
      - name: Run Ruff
        run: ruff check src tests scripts
      - name: Run pytest
        run: pytest --cov=vorflow --cov-report=term-missing
      - name: Build wheel and source distribution
        run: python -m build
      - name: Check package metadata rendering
        run: python -m twine check dist/*
      - name: Validate tag, metadata, and archive contents
        env:
          RELEASE_TAG: ${{ github.ref_name }}
        run: python scripts/check_dist.py dist --expected-tag "$RELEASE_TAG"
      - name: Smoke-test the built wheel
        run: |
          python -m venv "$RUNNER_TEMP/vorflow-smoke"
          "$RUNNER_TEMP/vorflow-smoke/bin/python" -m pip install dist/*.whl
          "$RUNNER_TEMP/vorflow-smoke/bin/python" -m pip check
          "$RUNNER_TEMP/vorflow-smoke/bin/python" -c "import vorflow; assert vorflow.__version__ == '0.1.0rc1'"
          cd "$RUNNER_TEMP"
          "$RUNNER_TEMP/vorflow-smoke/bin/python" "$GITHUB_WORKSPACE/examples/basic_usage.py"
      - uses: actions/upload-artifact@v7
        with:
          name: python-package-distributions
          path: dist/
          if-no-files-found: error

  publish-testpypi:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: testpypi
      url: https://test.pypi.org/p/vorflow
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v8
        with:
          name: python-package-distributions
          path: dist/
      - name: Publish package distributions to TestPyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          repository-url: https://test.pypi.org/legacy/
```

- [ ] **Step 2: Verify there is no production publishing path**

```powershell
rg -n "test.pypi.org|pypi.org|id-token|environment|api-token|password" .github/workflows/testpypi.yml
```

Expected: TestPyPI URLs and `id-token: write` are present; `https://upload.pypi.org`, API tokens, and passwords are absent.

- [ ] **Step 3: Run local equivalents of the build job**

```powershell
python -m pip install -e ".[dev]"
ruff check src tests scripts
pytest -v
python -m build
python -m twine check dist/*
python scripts/check_dist.py dist --expected-version 0.1.0rc1
```

Expected: every command succeeds; the validator prints both archive filenames.

- [ ] **Step 4: Commit the workflow**

```powershell
git add .github/workflows/testpypi.yml
git commit -m "ci: add trusted TestPyPI publishing"
```

### Task 7: Perform the Complete Local Release Rehearsal

**Files:**

- Verify only; do not modify source files unless a check reveals a defect.

**Interfaces:**

- Consumes: committed release-preparation branch.
- Produces: clean local evidence that the exact wheel installs and runs outside the source tree.

- [ ] **Step 1: Run source verification**

```powershell
ruff check src tests scripts
pytest --cov=vorflow --cov-report=term-missing
git diff --check
git status --short
```

Expected: checks pass; status contains no uncommitted release files.

- [ ] **Step 2: Build into a new explicit temporary directory**

```powershell
$releaseDist = Join-Path $env:TEMP 'vorflow-0.1.0rc1-dist'
if (Test-Path -LiteralPath $releaseDist) { throw "$releaseDist already exists; inspect it before choosing a new empty directory" }
New-Item -ItemType Directory -Path $releaseDist | Out-Null
python -m build --outdir $releaseDist
python -m twine check "$releaseDist\*"
python scripts/check_dist.py $releaseDist --expected-version 0.1.0rc1
```

Expected: exactly one wheel and one source distribution validate successfully.

- [ ] **Step 3: Create a fresh wheel-only environment**

```powershell
$smokeEnv = Join-Path $env:TEMP 'vorflow-0.1.0rc1-smoke'
if (Test-Path -LiteralPath $smokeEnv) { throw "$smokeEnv already exists; inspect it before choosing a new empty directory" }
python -m venv $smokeEnv
$smokePython = Join-Path $smokeEnv 'Scripts\python.exe'
$releaseWheel = Get-ChildItem -LiteralPath $releaseDist -Filter '*.whl' | Select-Object -ExpandProperty FullName
& $smokePython -m pip install $releaseWheel
& $smokePython -m pip check
& $smokePython -c "import vorflow; assert vorflow.__version__ == '0.1.0rc1'; print(vorflow.__version__)"
```

Expected: dependency installation succeeds, `pip check` reports no broken requirements, and version output is `0.1.0rc1`.

- [ ] **Step 4: Run Basic Usage outside the source tree**

```powershell
$examplePath = (Resolve-Path 'examples/basic_usage.py').Path
Push-Location $env:TEMP
try { & $smokePython $examplePath } finally { Pop-Location }
```

Expected: `Generated <positive number> Voronoi cells`.

- [ ] **Step 5: Review the branch before external configuration**

```powershell
git log --oneline main..HEAD
git diff --stat main...HEAD
git status --short
```

Expected: focused release commits and an empty status.

### Task 8: User Configures TestPyPI and the GitHub Approval Environment

**Files:**

- External configuration only; no passwords, tokens, or recovery values enter the repository.

**Interfaces:**

- Consumes: committed `.github/workflows/testpypi.yml` and Oscar's accounts.
- Produces: matching GitHub environment and pending TestPyPI Trusted Publisher.

- [ ] **Step 1: User creates and verifies the TestPyPI account**

Open `https://test.pypi.org/account/register/`. Oscar chooses the credentials, verifies the email, and stores recovery information privately. Codex waits and provides navigation guidance only.

- [ ] **Step 2: User creates the protected GitHub environment**

In repository settings, create environment `testpypi`, add Oscar as required reviewer, and allow Oscar to approve a run he triggered. Do not add API-token secrets.

- [ ] **Step 3: User registers the pending Trusted Publisher**

At `https://test.pypi.org/manage/account/publishing/`, enter exactly:

```text
PyPI project name: vorflow
Owner: oscarfasanchez
Repository: vorflow_os
Workflow name: testpypi.yml
Environment name: testpypi
```

- [ ] **Step 4: Confirm the configuration without exposing secrets**

Expected: TestPyPI lists the pending publisher with all five exact values; GitHub lists environment `testpypi` with a required reviewer; neither service contains a TestPyPI API token for this workflow.

### Task 9: Integrate, Tag, and Trigger the Candidate Build

**Files:**

- Git branch and tag state; no new source edit.

**Interfaces:**

- Consumes: passing release-preparation branch and completed Task 8.
- Produces: immutable annotated tag `v0.1.0rc1` on clean `main` and a paused TestPyPI deployment.

- [ ] **Step 1: Request code review and integrate the release branch**

Use `superpowers:requesting-code-review`, address findings, rerun Task 7, then use `superpowers:finishing-a-development-branch`. Choose the user's preferred PR or local merge route; do not tag a feature branch.

- [ ] **Step 2: Verify the exact tag target**

```powershell
git switch main
git status --short
git log -1 --oneline
git tag --list v0.1.0rc1
```

Expected: branch `main`, empty status, reviewed release commit at HEAD, and no existing `v0.1.0rc1` tag.

- [ ] **Step 3: Obtain explicit user approval for the external tag push**

Explain that pushing the tag starts GitHub Actions but cannot upload until the protected `testpypi` environment is manually approved.

- [ ] **Step 4: Create and push the annotated candidate tag**

```powershell
git tag -a v0.1.0rc1 -m "Release vorflow 0.1.0rc1"
git push origin v0.1.0rc1
```

Expected: GitHub starts `Publish release candidate to TestPyPI`; the build job runs first.

- [ ] **Step 5: Review the build before approval**

Confirm Ruff, pytest, build, Twine, archive validation, and fresh-wheel smoke steps all passed. Downloading or rebuilding a different artifact is not permitted.

- [ ] **Step 6: User approves the `testpypi` environment**

Oscar reviews the pending deployment and clicks approval. Expected: the publish job receives a short-lived OIDC credential and uploads the previously built wheel and source distribution to TestPyPI.

### Task 10: Verify TestPyPI and Record the Rehearsal

**Files:**

- Modify: `ROADMAP.md`
- Modify: `docs/roadmap/06-pypi-publishing.md`

**Interfaces:**

- Consumes: TestPyPI project `vorflow` release `0.1.0rc1`.
- Produces: an independent installation result and roadmap status `TestPyPI verified`.

- [ ] **Step 1: Inspect the TestPyPI project page**

Open `https://test.pypi.org/project/vorflow/0.1.0rc1/` and verify the README, absolute links, authors, maintainer, MIT licence, Python requirement, dependencies, wheel, source distribution, and Trusted Publishing provenance.

- [ ] **Step 2: Install dependencies from real PyPI in a second clean environment**

```powershell
$testPypiEnv = Join-Path $env:TEMP 'vorflow-0.1.0rc1-testpypi'
if (Test-Path -LiteralPath $testPypiEnv) { throw "$testPypiEnv already exists; inspect it before choosing a new empty directory" }
python -m venv $testPypiEnv
$testPypiPython = Join-Path $testPypiEnv 'Scripts\python.exe'
& $testPypiPython -m pip install "numpy>=1.24" "pandas>=1.5" "geopandas>=0.13" "shapely>=2.0" "scipy>=1.10" "gmsh>=4.11"
```

Expected: dependencies come from normal PyPI successfully.

- [ ] **Step 3: Install only vorflow from TestPyPI**

```powershell
& $testPypiPython -m pip install --index-url https://test.pypi.org/simple/ --no-deps vorflow==0.1.0rc1
& $testPypiPython -m pip check
& $testPypiPython -c "import vorflow; assert vorflow.__version__ == '0.1.0rc1'; print(vorflow.__version__)"
```

Expected: `vorflow` comes from TestPyPI, `pip check` passes, and version output is `0.1.0rc1`.

- [ ] **Step 4: Run Basic Usage against the TestPyPI installation**

```powershell
$examplePath = (Resolve-Path 'examples/basic_usage.py').Path
Push-Location $env:TEMP
try { & $testPypiPython $examplePath } finally { Pop-Location }
```

Expected: `Generated <positive number> Voronoi cells`.

- [ ] **Step 5: Record the verified status without claiming production publication**

Change Milestone 6 in `ROADMAP.md` from `In progress` to `TestPyPI verified`. Add this section to `docs/roadmap/06-pypi-publishing.md`:

```markdown
## TestPyPI rehearsal result

- Candidate: `vorflow==0.1.0rc1`
- Tag: `v0.1.0rc1`
- Test index: <https://test.pypi.org/project/vorflow/0.1.0rc1/>
- Wheel and source distribution passed Twine and archive-content validation.
- A clean environment installed the TestPyPI candidate, passed `pip check`,
  reported the expected version, and ran the Basic Usage example.
- Real PyPI publication remains pending and requires separate approval.
```

- [ ] **Step 6: Run the final documentation and repository checks**

```powershell
rg -n "TestPyPI verified|0.1.0rc1|Real PyPI publication remains pending" ROADMAP.md docs/roadmap/06-pypi-publishing.md
git diff --check
git status --short
```

Expected: the rehearsal is recorded accurately, production remains pending, and only the two roadmap files are modified.

- [ ] **Step 7: Commit the rehearsal record**

```powershell
git add ROADMAP.md docs/roadmap/06-pypi-publishing.md
git commit -m "docs: record TestPyPI release rehearsal"
```

- [ ] **Step 8: Stop before production**

Do not create `v0.1.0`, a production environment, a real-PyPI Trusted Publisher, or a production upload workflow. Report the TestPyPI result and the remaining production decision to the user.
