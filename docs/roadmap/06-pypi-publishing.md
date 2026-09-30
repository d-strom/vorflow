# Milestone 6 — First PyPI release

**Status:** in progress · **Current target:** TestPyPI `0.1.0rc1` · **Risk:** low · **Behavior change:** none (packaging/metadata only)
**Back to** [ROADMAP.md](../../ROADMAP.md)

## Goal

Publish `vorflow` to PyPI so users can `pip install vorflow`. The name is
**available on PyPI** (verified 2026-07-18: the registry returns 404 for
`vorflow`).

This milestone is being rehearsed on TestPyPI first. The rehearsal stops after
verifying `0.1.0rc1`; real PyPI publication remains a separate approval gate.

## Current state (release candidate prepared locally)

- Explicit PEP 621/639 metadata for `0.1.0rc1`, with Oscar Sanchez as first
  author and Oscar and rhugman as maintainers. Rhugman's public commit author
  email is provisionally used in maintainer metadata pending his confirmation.
- MIT SPDX metadata, dependency floors, keywords, repository links, changelog,
  and absolute README links suitable for package-index rendering.
- One canonical Basic Usage script that runs against an installed wheel.
- Cross-platform CI plus a Python 3.10 job for the six exact dependency floors.
- Automated wheel/sdist content and built-metadata validation.
- A `v*rc*` GitHub Actions workflow that builds once and publishes only to
  TestPyPI through the protected `testpypi` environment and short-lived OIDC
  credentials.
- `__version__` resolved from installed metadata, with a neutral source-tree
  fallback instead of a duplicated release number.

An earlier local rehearsal passed Ruff, the full test suite, isolated archive
builds, Twine checks, archive validation, fresh-wheel installation, `pip check`,
and the Basic Usage example outside the source tree. The current PR head still
requires fresh CI and release-workflow verification.

## Completed release preparation

- [x] Integrate `gmshflow_missing` into local `main` after full verification.
- [x] Prepare and validate the `0.1.0rc1` wheel and source distribution.
- [x] Smoke-test the wheel in a fresh environment outside the repository.
- [x] Add PyPI-facing installation documentation and absolute links.
- [x] Add and test runtime dependency floors without upper caps.
- [x] Record Oscar's primary authorship and provisional co-maintainer metadata
  for rhugman.
- [x] Add the changelog, modern licence metadata, and package keywords.
- [x] Add an RC-only, TestPyPI-only Trusted Publishing workflow.

## Remaining TestPyPI rehearsal

- [ ] Address release code-review findings and integrate the focused release
  branch into `main`.
- [ ] Confirm with rhugman that `rthugman@gmail.com` is the contact address he
  wants exposed in package maintainer metadata, and verify the upstream
  `testpypi` GitHub environment and TestPyPI Trusted Publisher identity.
- [ ] Create and push the annotated `v0.1.0rc1` tag.
- [ ] Review the GitHub build and manually approve the protected `testpypi`
  deployment.
- [ ] Inspect the TestPyPI project page and install `0.1.0rc1` independently.
- [ ] Record the result as **TestPyPI verified**. Real PyPI publication remains
  a separate approval gate.

## Phase 3 — Nice-to-have (can follow in later 0.x releases)

- [ ] **Python 3.13/3.14 in the CI matrix and classifiers.** The local dev
  env is already on 3.14, so it is implicitly supported but untested in CI.
- [ ] **Docs site** (mkdocs-material + API reference on GitHub Pages or
  ReadTheDocs) and a `Documentation` URL in `[project.urls]`. The README is
  sufficient for an alpha.
- [ ] **Citation/DOI** (`CITATION.cff` + Zenodo) — worthwhile for a
  research-audience package.
- [ ] **conda-forge feedstock.** The MODFLOW/flopy user base is heavily
  conda-based. Do this after the PyPI release stabilizes, since conda-forge
  builds from the published sdist.

## Release sequence

1. Integrate the reviewed release branch into `main`.
2. Tag `v0.1.0rc1`; only the `v*rc*` TestPyPI workflow can match.
3. Let GitHub rebuild, retest, and validate one wheel and one sdist.
4. Review the build results, then manually approve the protected `testpypi`
   deployment.
5. Verify the rendered TestPyPI page and an independent installation, then stop
   before real PyPI.

## Verification

- `twine check dist/*` passes; sdist/wheel contain only the package and
  standard metadata.
- README Basic Usage example runs against the installed wheel in a clean venv.
- TestPyPI project page renders with no broken links before the real upload.
- After the rehearsal: install `vorflow==0.1.0rc1` from TestPyPI in a fresh
  environment and confirm `vorflow.__version__` matches the tag.
- Real PyPI publication remains pending a separate review and approval.
