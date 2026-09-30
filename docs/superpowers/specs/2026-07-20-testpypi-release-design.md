# TestPyPI Release Rehearsal Design

**Date:** 2026-07-20

**Status:** Approved design

**Target candidate:** `vorflow` version `0.1.0rc1`

**Production release reserved:** `0.1.0`

## Purpose

Prepare `vorflow` as an installable Python package and rehearse the complete
release process on TestPyPI. The rehearsal must prove that a clean user
environment can install the built wheel, import the package, run the Basic
Usage mesh-generation example, and report the expected version.

The work stops after TestPyPI verification. It does not create or execute a
real-PyPI publishing path.

## Current Repository Context

- Release-target feature work is committed on branch `gmshflow_missing` but is
  not yet integrated into `main`.
- The working tree contains uncommitted roadmap and documentation material.
- `pyproject.toml` already uses PEP 621 metadata, setuptools, and a `src/`
  layout.
- The package version is currently duplicated as `0.0.2` in
  `pyproject.toml` and the source-checkout fallback in
  `src/vorflow/__init__.py`.
- GitHub Actions currently runs Ruff and pytest across Linux, Windows, and
  macOS with Python 3.10 through 3.12.
- No package distribution has yet been built and verified in this repository.

Release preparation begins only after the intended feature work is integrated
into a clean `main`. Packaging work must remain separate from feature work so
that the release-specific changes are easy to review.

## Scope Boundary

### In scope

- Package metadata and dependency lower bounds.
- Version reporting and release-candidate versioning.
- PyPI-compatible README content and a runnable Basic Usage example.
- A changelog entry for the candidate.
- Wheel and source-distribution build verification.
- Minimum-dependency, clean-wheel, and post-upload smoke tests.
- A dedicated TestPyPI Trusted Publishing workflow.
- TestPyPI account, GitHub environment, and pending-publisher setup guidance.
- TestPyPI page and installation verification.
- Roadmap updates recording the rehearsal result.

### Out of scope

- Changes to mesh generation or other runtime behaviour.
- A real-PyPI publishing job or production repository URL.
- A `v0.1.0` production tag or real PyPI upload.
- `setuptools-scm` or another tag-derived versioning dependency.
- Documentation-site, DOI, citation, or conda-forge work.
- Removing or relocating the user's ignored local VS Code workspace file.

## Agreed Release Sequence

1. Review and integrate `gmshflow_missing` into `main` without folding
   packaging work into that integration.
2. Begin packaging changes from a clean `main`.
3. Prepare package version `0.1.0rc1`.
4. Run source tests, minimum-dependency tests, archive validation, and a fresh
   wheel-install smoke test locally. The tag-triggered GitHub workflow repeats
   the release-critical version, test, build, archive, and smoke checks.
5. Create the TestPyPI account and configure the matching Trusted Publisher
   and GitHub environment.
6. Merge the reviewed release-preparation changes into `main`.
7. Create tag `v0.1.0rc1` on the exact verified commit.
8. Let GitHub Actions build the archives, then approve the protected
   `testpypi` publishing environment.
9. Verify the TestPyPI page and install `vorflow==0.1.0rc1` in another clean
   environment.
10. Record the result and stop without publishing to real PyPI.

## Roadmap Cleanup

`ROADMAP.md` is retained as both the current work map and the historical record
of completed work. It is reorganized rather than erased:

- Milestone 6 becomes the single current objective.
- Milestones 1 through 5 remain in a compact completed-milestones section.
- Their detailed files under `docs/roadmap/` remain available as decision and
  verification history.
- The stale `Suggested Sequencing` text for already completed milestones is
  removed.
- After a successful TestPyPI rehearsal, Milestone 6 is recorded as
  `TestPyPI verified`, not `Done`.
- Milestone 6 becomes `Done` only after the separate future production release
  makes `pip install vorflow` available from real PyPI.

## Version Design

`pyproject.toml` is the only authoritative source containing the candidate
version:

```toml
version = "0.1.0rc1"
```

At runtime, `vorflow.__version__` continues to use
`importlib.metadata.version("vorflow")`. If distribution metadata is
unavailable because the source directory is being imported without an
installation, the fallback is `0+unknown`, not a duplicated release number.

The release workflow must compare the pushed tag, with its leading `v`
removed, against the built distribution version. A mismatch stops the build
before an upload is possible.

If the first candidate needs a correction after upload, the corrected package
uses `0.1.0rc2` and tag `v0.1.0rc2`. An uploaded candidate is never overwritten
or represented by a moved tag. The eventual production release remains
`0.1.0`.

## Authors, Maintainer, and Licence

Project metadata lists Oscar Sanchez as the primary current author and
maintainer. It retains rhugman as an original author without publishing
rhugman's email:

```toml
authors = [
    {name = "Oscar Sanchez", email = "oscarfasanchez@gmail.com"},
    {name = "rhugman"},
]
maintainers = [
    {name = "Oscar Sanchez", email = "oscarfasanchez@gmail.com"},
]
```

The existing MIT copyright notice remains intact. Packaging metadata adopts
the PEP 639 form supported by setuptools 77.0.3 and newer:

```toml
[build-system]
requires = ["setuptools>=77.0.3"]

[project]
license = "MIT"
license-files = ["LICENSE"]
```

The deprecated `License :: OSI Approved :: MIT License` classifier is removed
because the SPDX expression now declares the licence. This does not remove or
change the MIT licence itself.

References:

- [PyPA project authors and maintainers metadata](https://packaging.python.org/en/latest/specifications/declaring-project-metadata/#authors-maintainers)
- [PyPA licence and licence-file guidance](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license-and-license-files)

## Dependencies

Runtime dependencies use lower bounds and no upper caps:

```toml
dependencies = [
    "numpy>=1.24",
    "pandas>=1.5",
    "geopandas>=0.13",
    "shapely>=2.0",
    "scipy>=1.10",
    "gmsh>=4.11",
]
```

A dedicated Python 3.10 minimum-dependency check installs those exact lower
versions and runs the test suite. If an exact lower-bound combination fails
because `vorflow` uses an unavailable or incompatible API, the dependency
floor is raised to the lowest version demonstrated to pass. Tests are not
weakened to preserve an incorrect advertised floor.

## README and Example Design

The README installation section leads with the future production command:

```bash
pip install vorflow
```

Editable installation and development extras appear in a separate development
subsection. Repository links use complete `https://github.com/...` addresses
so they resolve on GitHub, TestPyPI, and PyPI. This includes links to
`examples/`, `ROADMAP.md`, `LICENSE`, and `etc/environment.yml`.

The core Basic Usage example becomes a runnable
`examples/basic_usage.py` script and succeeds after producing a non-empty
Voronoi `GeoDataFrame`. The README keeps an understandable version of that
flow and links to the complete script.

Shapefile export is documented separately from core mesh generation. The
current `GeoDataFrame.to_file(...)` call relies on a GeoPandas-supported I/O
engine such as Pyogrio or Fiona; `vorflow` itself does not import either
engine. The core smoke test therefore proves mesh construction without making
an optional geospatial file writer a hard `vorflow` dependency.

Reference: [GeoPandas `GeoDataFrame.to_file` documentation](https://geopandas.org/en/stable/docs/reference/api/geopandas.GeoDataFrame.to_file.html).

## Distribution Design

The release build produces both standard distribution formats:

- `vorflow-0.1.0rc1-py3-none-any.whl`, the ready-to-install pure-Python wheel.
- `vorflow-0.1.0rc1.tar.gz`, the source distribution containing the source and
  build instructions needed to create a wheel.

The wheel contains the `vorflow` Python package and its `.dist-info` metadata.
The source distribution contains the source package, `pyproject.toml`, README,
licence, and required build metadata. Tests may be present in the source
distribution, but `.conda/`, `benchmarks/`, general planning documentation,
and `*.code-workspace` files must not be present in either archive.

The user's ignored `src/vorflow/vorflow.code-workspace` file is not modified.
Packaging configuration prevents it from entering an archive.

## Verification Design

### Source checks

- Run Ruff against `src` and `tests`.
- Run the complete pytest suite.
- Preserve the existing Linux, Windows, and macOS coverage across Python 3.10
  through 3.12.
- Run the exact minimum-dependency combination on Python 3.10.

### Archive checks

1. Build both archives with `python -m build`.
2. Run `twine check dist/*`.
3. Inspect every archive member against required and forbidden paths.
4. Read built metadata and confirm the name, version, dependencies, Python
   requirement, licence, authors, maintainer, and project URLs.
5. Confirm that the wheel and source distribution describe the same version.

### Fresh-wheel smoke test

Create a new virtual environment with no inherited project packages, install
the built wheel, and then:

1. run `pip check`;
2. import `vorflow`;
3. assert `vorflow.__version__ == "0.1.0rc1"`;
4. run `examples/basic_usage.py` from outside the source tree;
5. confirm it produces a non-empty `GeoDataFrame`.

Running outside the source tree ensures the test imports the installed wheel
rather than accidentally importing `src/vorflow`.

### Post-upload TestPyPI smoke test

TestPyPI does not serve as a complete mirror of all runtime dependencies. In a
new environment, install the declared dependencies from the normal Python
package index first. Then install only `vorflow==0.1.0rc1` from TestPyPI with
dependency resolution disabled for that second command. Run `pip check`, the
version assertion, and the Basic Usage script again.

Also inspect the rendered TestPyPI project page, absolute links, author and
maintainer presentation, licence, dependencies, release files, and Trusted
Publishing provenance.

## Trusted Publishing Architecture

The dedicated workflow is `.github/workflows/testpypi.yml` and is triggered
only by release-candidate tags matching the intended `v*rc*` family. The build
job also performs an exact PEP 440 version comparison, so the broad GitHub tag
glob cannot authorize a mismatched version.

### Build job

- Checks out the tagged source.
- Has read-only repository permissions and no OIDC identity permission.
- Confirms the tag and package version match.
- Runs the required checks and builds both archives.
- Uploads the verified `dist/` directory as an internal GitHub artifact.

### Publish job

- Depends on successful completion of the build job.
- Downloads the build job's artifact and does not rebuild it.
- Uses the protected GitHub environment named `testpypi`.
- Receives `id-token: write` only at the job level.
- Publishes with `pypa/gh-action-pypi-publish@release/v1`.
- Sets `repository-url: https://test.pypi.org/legacy/` explicitly.

There is no production publish job, production environment, production
repository URL, or API token secret.

Reference: [PyPI Trusted Publishing with GitHub Actions](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## One-Time Account Configuration

Oscar creates and verifies a TestPyPI account. Before pushing the candidate
tag, TestPyPI receives a pending Trusted Publisher registration with exactly:

```text
Owner:       oscarfasanchez
Repository:  vorflow_os
Workflow:    testpypi.yml
Environment: testpypi
Project:     vorflow
```

GitHub receives an environment named `testpypi` with Oscar configured as its
required reviewer. The environment permits Oscar to approve a run that he
triggered. The spelling and capitalization must match the TestPyPI
registration and workflow exactly.

Trusted Publishing uses short-lived OIDC credentials rather than a stored
TestPyPI API token. The publish job cannot authenticate if its repository,
workflow filename, or environment differs from the registered identity.

Account creation is an explicit user-owned manual step because it uses Oscar's
email, password, email-verification link, recovery information, and account
security settings. The implementation work prepares the workflow and exact
publisher values, then pauses while Oscar completes those personal steps.
Passwords, recovery information, and two-factor authentication values are not
requested, stored, or handled by the implementation process.

TestPyPI is a rehearsal service rather than permanent storage. Its database may
periodically remove projects, releases, and accounts. The durable record is the
Git repository, release tag, changelog, workflow, and verification notes; the
TestPyPI project page is not treated as a backup or permanent release archive.

## Failure Handling

- A source-test, lint, minimum-dependency, tag/version, build, metadata,
  archive-content, or Twine failure stops before the publish job.
- Without environment approval, the publish job remains paused and uploads
  nothing.
- A Trusted Publisher identity mismatch is rejected by TestPyPI; it does not
  fall back to a stored credential.
- A problem discovered after upload produces the next immutable candidate,
  such as `0.1.0rc2`; the existing archive and tag are not replaced.
- If `vorflow` is unavailable in TestPyPI's separate project namespace, work
  stops for an explicit naming decision. The workflow does not silently change
  the distribution name.
- Successful TestPyPI verification records the result and stops. Production
  publishing requires a separate future design and explicit approval.

## File Responsibilities

| File | Responsibility |
|---|---|
| `pyproject.toml` | Authoritative version, package identity, dependencies, licence, build configuration, and search metadata |
| `src/vorflow/__init__.py` | Installed-version lookup and non-release source fallback |
| `README.md` | PyPI-facing installation, usage, links, and optional-export guidance |
| `CHANGELOG.md` | Candidate release notes |
| `examples/basic_usage.py` | Canonical executable core example used by smoke tests |
| `scripts/check_dist.py` | Deterministic archive-member and built-metadata validation |
| `MANIFEST.in` | Explicit source-distribution exclusions for repository-only material |
| `.github/workflows/testpypi.yml` | Build, approval, OIDC, and TestPyPI-only upload path |
| `.gitignore` | Exclusion of local `.superpowers/` visual-session state |
| `docs/roadmap/06-pypi-publishing.md` | Detailed milestone decisions and rehearsal record |
| `ROADMAP.md` | High-level milestone status without claiming a production release |

`pyproject.toml` explicitly limits setuptools package discovery to `src/` and
disables undeclared package data. `MANIFEST.in` prunes `.conda/`, `benchmarks/`,
general planning documentation, and `*.code-workspace` from the source
distribution. `scripts/check_dist.py` independently inspects both archives and
fails when required files or metadata are missing or forbidden members are
present. The release cannot proceed while any forbidden archive member
remains.

## Acceptance Criteria

- The intended feature work is integrated and the release commit on `main` is
  clean.
- Source tests and Ruff pass.
- The exact minimum-dependency test passes or the declared floors are raised
  to the demonstrated passing minimums.
- `python -m build` creates one wheel and one source distribution for
  `0.1.0rc1`.
- `twine check` passes for both archives.
- Required metadata is correct and forbidden repository-only material is
  absent from both archives.
- A clean environment installs the local wheel, passes `pip check`, reports
  `0.1.0rc1`, and runs the Basic Usage example.
- The tag is exactly `v0.1.0rc1` and points at the verified release commit.
- GitHub publishes the checked artifact to TestPyPI through the protected
  `testpypi` environment and Trusted Publishing.
- A second clean environment installs the candidate from TestPyPI and repeats
  the smoke checks.
- The TestPyPI page renders correctly with working links and expected metadata.
- `ROADMAP.md` identifies Milestone 6 as the only current objective, preserves
  Milestones 1 through 5 as completed history, and contains no obsolete
  implementation sequencing.
- No real-PyPI workflow, tag, credential, or upload is created.
