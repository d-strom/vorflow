# PR #12 Canonical Release Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete review item 1 by making the upstream Vorflow repository and both maintainers visible and verifiable in the `0.1.0rc1` release candidate.

**Architecture:** Keep PEP 621 metadata in `pyproject.toml` as the source of truth. Make the archive validator check the built wheel and sdist against the same upstream URLs and maintainer identities, while static tests guard the source configuration and public documentation. This is one review item and one product-change commit; do not begin roadmap item 2 until the user reviews its evidence.

**Tech Stack:** Python 3.10–3.12, setuptools, PEP 621/639, pytest, Ruff, `build`, Twine, GitHub Actions.

**Spec:** [`ROADMAP.md`](../../../ROADMAP.md), especially “PR #12 review closeout design” and “First implementation unit: canonical release ownership.” That newer design supersedes the 2026-07-20 TestPyPI design's old single-maintainer decision.

## Global Constraints

- Keep version `0.1.0rc1`; do not tag or publish during PR review closeout.
- Keep `requires-python = ">=3.10"` and the existing Python 3.10–3.12 CI matrix.
- Package-facing repository base is `https://github.com/rhugman/vorflow`, with its `/issues` and `/blob/main/CHANGELOG.md` URLs.
- Oscar Sanchez remains the first author. Oscar Sanchez and rhugman are both maintainers; provisionally use rhugman's public commit author address `rthugman@gmail.com`, and confirm it with him before a release tag.
- Preserve the six runtime dependency floors: `numpy>=1.24`, `pandas>=1.5`, `geopandas>=0.13`, `shapely>=2.0`, `scipy>=1.10`, and `gmsh>=4.11`; add no dependencies in this item.
- Do not change mesh behavior, public runtime signatures, or `.github/workflows/testpypi.yml` in this item.
- Preserve unrelated and untracked workspace files, including the pre-existing `.pytest-*` directories. Do not remove them.
- Keep all applicable review work in PR #12; make one focused product commit for this item and pause before item 2.
- GitHub's OS/Python matrix and minimum-dependency job are the authoritative gate if local Conda imports remain impractically slow.

## Review Focus

1. A wheel containing only legacy fork URLs must be rejected, not silently accepted — `test_validate_wheel_rejects_legacy_fork_urls`.
2. A wheel containing both correct upstream URLs and an extra legacy fork URL must be rejected — `test_validate_wheel_rejects_extra_legacy_fork_url`.
3. A missing rhugman maintainer must be rejected even when Oscar remains — `test_validate_wheel_rejects_missing_co_maintainer`.
4. A misspelled rhugman maintainer email must be rejected, not treated as sufficient because the name matches — `test_validate_sdist_rejects_wrong_co_maintainer_email`.
5. Source metadata, README, and changelog must not retain fork links while the built archives use upstream links — `test_release_metadata_is_complete`, `test_public_docs_link_to_upstream`, and the existing wheel/sdist acceptance tests, followed by a real artifact build.

---

## File Structure

- `pyproject.toml` — authoritative authors, maintainers, repository URLs, and unchanged version/dependencies.
- `scripts/check_dist.py` — release-archive metadata contract for both wheel and sdist.
- `tests/test_release_metadata.py` — source-metadata and public-link regression tests; retain existing version and clean-directory smoke tests.
- `tests/test_check_dist.py` — synthetic wheel/sdist fixtures and validator regressions.
- `README.md` and `CHANGELOG.md` — package-index-facing links only; leave unrelated installation and migration copy for later review items.
- `docs/roadmap/06-pypi-publishing.md` — accurate first-author/co-maintainer status and pending email/publisher confirmation.
- `ROADMAP.md` — item-1 implementation status and local verification, with CI still identified as a gate.
- `.github/workflows/testpypi.yml` — inspect only for repository/environment identity; no edit.

### Task 1: Make release ownership canonical and testable

**Files:**
- Modify: `pyproject.toml:11-18,39-42`
- Modify: `scripts/check_dist.py:5-27,102-116`
- Modify: `tests/test_release_metadata.py:13-38`
- Modify: `tests/test_check_dist.py:13-58,82-98`
- Modify: `README.md:54,61,131,138,143`
- Modify: `CHANGELOG.md:33-34`
- Modify: `docs/roadmap/06-pypi-publishing.md:16-19,41-43,46-55`
- Modify: `ROADMAP.md:91-100,184-196`

**Interfaces:**
- Consumes: `[project]` and `[project.urls]` in `pyproject.toml`; RFC 822 wheel `METADATA` and sdist `PKG-INFO` read by `validate_wheel(Path, str)` and `validate_sdist(Path, str)`.
- Produces: unchanged `validate_wheel`, `validate_sdist`, and `validate_dist` signatures; validated upstream project URLs and both maintainer name/email pairs in either archive kind.

- [ ] **Step 1: Confirm this worktree's baseline and file ownership.**

```powershell
git branch --show-current
git status --short
git log -1 --oneline
rg -n "oscarfasanchez/vorflow_os|rhugman/vorflow|maintainers|Maintainer-email" pyproject.toml README.md CHANGELOG.md scripts/check_dist.py tests/test_check_dist.py tests/test_release_metadata.py docs/roadmap/06-pypi-publishing.md
```

Expected: branch `fix/pr12-issues-13-16`; only pre-existing untracked `.pytest-*` directories; old fork links in the named files; no active product edits. Stop and reconcile any unexpected tracked changes.

- [ ] **Step 2: Add failing source-metadata and documentation tests.** In `tests/test_release_metadata.py`, keep the existing author-order, version, dependency, source-fallback, and clean-directory checks. Replace its one-maintainer assertion with the following two-maintainer assertion and add these tests after `test_release_metadata_is_complete`:

```python
    assert project["maintainers"] == [
        {"name": "Oscar Sanchez", "email": "oscarfasanchez@gmail.com"},
        {"name": "rhugman", "email": "rthugman@gmail.com"},
    ]
    assert project["urls"] == {
        "Repository": "https://github.com/rhugman/vorflow",
        "Issues": "https://github.com/rhugman/vorflow/issues",
        "Changelog": "https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md",
    }


def test_public_docs_link_to_upstream():
    for name in ("README.md", "CHANGELOG.md"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "https://github.com/oscarfasanchez/vorflow_os" not in content
        assert "https://github.com/rhugman/vorflow" in content
```

- [ ] **Step 3: Make the synthetic archives describe the intended release and add negative tests.** In `tests/test_check_dist.py`, replace the single `Maintainer-email` fixture line with the combined header below, replace the three fork `Project-URL` fixture lines with the upstream lines, and let `_write_wheel` accept supplied bytes:

```python
        "Maintainer-email: Oscar Sanchez <oscarfasanchez@gmail.com>, "
        "rhugman <rthugman@gmail.com>\n"
        "Project-URL: Repository, https://github.com/rhugman/vorflow\n"
        "Project-URL: Issues, https://github.com/rhugman/vorflow/issues\n"
        "Project-URL: Changelog, https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md\n"


def _write_wheel(path, metadata=None):
    dist_info = "vorflow-0.1.0rc1.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vorflow/__init__.py", "")
        archive.writestr(f"{dist_info}/licenses/LICENSE", "MIT")
        archive.writestr(
            f"{dist_info}/METADATA", _metadata() if metadata is None else metadata
        )
```

Add these cases after the two existing archive-acceptance tests; `_write_sdist` already accepts a `metadata` argument:

```python
def test_validate_wheel_rejects_legacy_fork_urls(tmp_path):
    wheel = tmp_path / "vorflow-0.1.0rc1-py3-none-any.whl"
    legacy = _metadata().replace(
        b"https://github.com/rhugman/vorflow",
        b"https://github.com/oscarfasanchez/vorflow_os",
    )
    _write_wheel(wheel, legacy)
    with pytest.raises(ValueError, match="project URLs"):
        check_dist.validate_wheel(wheel, "0.1.0rc1")


def test_validate_wheel_rejects_extra_legacy_fork_url(tmp_path):
    wheel = tmp_path / "vorflow-0.1.0rc1-py3-none-any.whl"
    mixed = _metadata().replace(
        b"\n\nSynthetic package metadata",
        b"\nProject-URL: Repository, https://github.com/oscarfasanchez/vorflow_os"
        b"\n\nSynthetic package metadata",
    )
    _write_wheel(wheel, mixed)
    with pytest.raises(ValueError, match="project URLs"):
        check_dist.validate_wheel(wheel, "0.1.0rc1")


def test_validate_wheel_rejects_missing_co_maintainer(tmp_path):
    wheel = tmp_path / "vorflow-0.1.0rc1-py3-none-any.whl"
    missing = _metadata().replace(b", rhugman <rthugman@gmail.com>", b"")
    _write_wheel(wheel, missing)
    with pytest.raises(ValueError, match="maintainer"):
        check_dist.validate_wheel(wheel, "0.1.0rc1")


def test_validate_sdist_rejects_wrong_co_maintainer_email(tmp_path):
    sdist = tmp_path / "vorflow-0.1.0rc1.tar.gz"
    wrong = _metadata().replace(
        b"rhugman <rthugman@gmail.com>", b"rhugman <wrong@example.com>"
    )
    _write_sdist(sdist, metadata=wrong)
    with pytest.raises(ValueError, match="maintainer"):
        check_dist.validate_sdist(sdist, "0.1.0rc1")
```

- [ ] **Step 4: Run the new regressions before implementing.**

```powershell
python -m pytest tests/test_release_metadata.py::test_release_metadata_is_complete tests/test_release_metadata.py::test_public_docs_link_to_upstream tests/test_check_dist.py -q
```

Expected: the new source and archive acceptance checks fail against fork URLs and one maintainer. If local dependency imports stall, record the exact failing command/time and use the corresponding GitHub CI run as the authoritative test after pushing; do not call an unrun test passing.

- [ ] **Step 5: Update the source metadata and distribution validator.** Keep Oscar first in `authors` and add rhugman only to `maintainers`, with his provisionally proposed address. Set the three `project.urls` values and `EXPECTED_URLS` to the exact upstream strings shown in Steps 2–3. In `scripts/check_dist.py`, import `getaddresses` and replace its single-maintainer check and URL check with:

```python
from email.utils import getaddresses

EXPECTED_MAINTAINERS = {
    ("Oscar Sanchez", "oscarfasanchez@gmail.com"),
    ("rhugman", "rthugman@gmail.com"),
}

# Inside _validate_metadata, after the author checks:
maintainers = set(getaddresses(metadata.get_all("Maintainer-email", [])))
if maintainers != EXPECTED_MAINTAINERS:
    raise ValueError(
        f"unexpected {archive_kind} maintainer metadata: {maintainers}"
    )
project_urls = set(metadata.get_all("Project-URL", []))
if not EXPECTED_URLS.issubset(project_urls) or any(
    "https://github.com/oscarfasanchez/vorflow_os" in url
    for url in project_urls
):
    raise ValueError(f"{archive_kind} has incorrect project URLs: {project_urls}")
```

The `pyproject.toml` result must retain `version = "0.1.0rc1"` and its existing dependency list; its changed sections should read:

```toml
maintainers = [
    {name = "Oscar Sanchez", email = "oscarfasanchez@gmail.com"},
    {name = "rhugman", email = "rthugman@gmail.com"},
]

[project.urls]
Repository = "https://github.com/rhugman/vorflow"
Issues = "https://github.com/rhugman/vorflow/issues"
Changelog = "https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md"
```

- [ ] **Step 6: Correct public documentation without changing installation guidance.** In `README.md` and `CHANGELOG.md`, replace every `https://github.com/oscarfasanchez/vorflow_os` prefix with `https://github.com/rhugman/vorflow`; the path suffixes and changelog tag references stay intact. In `docs/roadmap/06-pypi-publishing.md`, replace the old ownership statement and completed checkbox with:

```markdown
- Explicit PEP 621/639 metadata for `0.1.0rc1`, with Oscar Sanchez as first
  author and Oscar and rhugman as maintainers. Rhugman's public commit author
  email is provisionally used in maintainer metadata pending his confirmation.

- [x] Record Oscar's primary authorship and provisional co-maintainer metadata
  for rhugman.
```

Add under “Remaining TestPyPI rehearsal,” before the tag step:

```markdown
- [ ] Confirm with rhugman that `rthugman@gmail.com` is the contact address he
  wants exposed in package maintainer metadata, and verify the upstream
  `testpypi` GitHub environment and TestPyPI Trusted Publisher identity.
```

- [ ] **Step 7: Re-run focused tests and inspect the diff.**

```powershell
python -m pytest tests/test_release_metadata.py tests/test_check_dist.py -q
git diff --check
git diff -- pyproject.toml scripts/check_dist.py tests/test_release_metadata.py tests/test_check_dist.py README.md CHANGELOG.md docs/roadmap/06-pypi-publishing.md
```

Expected: focused tests pass, no whitespace errors, no unrelated edits, and no remaining fork link in the public-facing files.

- [ ] **Step 8: Verify the actual build artifacts in a fresh temporary output directory.**

```powershell
$artifactDir = Join-Path $env:TEMP ("vorflow-pr12-item1-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $artifactDir | Out-Null
python -m build --outdir $artifactDir
$wheelPath = (Get-ChildItem -LiteralPath $artifactDir -Filter *.whl).FullName
$sdistPath = (Get-ChildItem -LiteralPath $artifactDir -Filter *.tar.gz).FullName
python -m twine check $wheelPath $sdistPath
python scripts/check_dist.py $artifactDir --expected-version 0.1.0rc1
```

Expected: exactly one wheel and one sdist, both with upstream URLs, both maintainer pairs, and unchanged `0.1.0rc1`. Keep the temporary artifacts for diagnosis until the checks pass; do not delete unrelated `dist/` contents.

- [ ] **Step 9: Run repository-wide local checks.**

```powershell
ruff check src tests scripts
python -m pytest -q
rg -n "oscarfasanchez/vorflow_os" pyproject.toml README.md CHANGELOG.md scripts/check_dist.py tests/test_check_dist.py tests/test_release_metadata.py
```

Expected: Ruff and pytest pass; the final search prints no matches (its exit status 1 means no matches). If a local environment cannot complete, record the limitation accurately and require the corresponding CI results before marking the item resolved.

- [ ] **Step 10: Check publisher identity without changing it.** Read `.github/workflows/testpypi.yml` and, when authorized GitHub access is available, inspect the upstream `testpypi` environment with:

```powershell
gh api repos/rhugman/vorflow/environments/testpypi
```

Expected: workflow uses `environment: testpypi` and TestPyPI's legacy endpoint. The upstream maintainer must confirm the TestPyPI pending-publisher owner/repository/workflow/environment match and approve rhugman's contact email before any tag; if these settings are private or inaccessible, record them as pending rather than claiming verification. Do not tag or publish.

- [ ] **Step 11: Record the local result in the roadmap and commit only this item.** After the checks above, change item 1's status from `Next` to `Awaiting CI and user review` and append the following under “First implementation unit” if every listed local check passed. If a check did not complete, replace that clause with the exact limitation instead of claiming success. Keep item 2 queued. Stage only the named files:

```markdown
**Item 1 verification (local; PR CI pending).** The focused release-metadata
and distribution-validator tests, `ruff check src tests scripts`, the full
pytest suite, `twine check`, and `scripts/check_dist.py` passed for a fresh
`0.1.0rc1` wheel and sdist. The upstream TestPyPI publisher identity and
rhugman's preferred contact address still require confirmation before tagging.
```

```powershell
git add -- pyproject.toml scripts/check_dist.py tests/test_release_metadata.py tests/test_check_dist.py README.md CHANGELOG.md docs/roadmap/06-pypi-publishing.md ROADMAP.md
git diff --cached --check
git diff --cached --stat
git commit -m "build: canonicalize release ownership metadata"
```

Expected: one focused product commit, with no `.pytest-*` directories or unrelated files staged.

- [ ] **Step 12: Deliver and gate the item, then pause.** Confirm the local branch remains a fast-forward of `origin/main`; push `HEAD:main` only if it does, so PR #12 updates. Wait for the PR's Linux/Windows/macOS Python matrix and minimum-dependency job at the pushed SHA. If any job fails, diagnose and fix this item before proceeding. Report the item-1 commit, local artifact/test evidence, and CI run links in the PR conversation; keep #13–#16 open and do not start review item 2 until the user reviews this unit.

```powershell
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git push origin HEAD:main
gh pr checks 12 --repo rhugman/vorflow --watch
```

Expected: `git merge-base --is-ancestor` exits 0 before the push; all required PR checks pass at the new head. If the branch is not a fast-forward, stop and reconcile without force-pushing.
