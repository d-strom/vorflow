import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_dist.py"
SPEC = importlib.util.spec_from_file_location("check_dist", SCRIPT)
check_dist = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["check_dist"] = check_dist  # dataclasses resolve annotations via sys.modules
SPEC.loader.exec_module(check_dist)

# A synthetic project, independent of the real release values, so these tests
# exercise the checker logic rather than restating vorflow's pyproject.
VERSION = "2.3.0rc4"
PYPROJECT = f"""
[project]
name = "vorflow"
version = "{VERSION}"
description = "Synthetic summary"
requires-python = ">=3.10"
license = "MIT"
license-files = ["LICENSE"]
authors = [
    {{name = "Ada Author", email = "ada@example.org"}},
    {{name = "Nameonly Author"}},
]
maintainers = [
    {{name = "Ada Author", email = "ada@example.org"}},
    {{name = "Nameonly Author"}},
]
dependencies = ["numpy >= 1.24", "shapely>=2.0"]

[project.optional-dependencies]
dev = ["pytest"]

[project.urls]
Repository = "https://example.org/vorflow"
"""


def _metadata(version=VERSION):
    return (
        "Metadata-Version: 2.4\n"
        "Name: vorflow\n"
        f"Version: {version}\n"
        "Summary: Synthetic summary\n"
        "Author: Nameonly Author\n"
        "Author-email: Ada Author <ada@example.org>\n"
        "Maintainer: Nameonly Author\n"
        "Maintainer-email: Ada Author <ada@example.org>\n"
        "License-Expression: MIT\n"
        "License-File: LICENSE\n"
        "Requires-Python: >=3.10\n"
        "Project-URL: Repository, https://example.org/vorflow\n"
        "Requires-Dist: numpy>=1.24\n"
        "Requires-Dist: shapely>=2.0\n"
        "Provides-Extra: dev\n"
        'Requires-Dist: pytest; extra == "dev"\n'
        "\n"
        "Synthetic package metadata for archive validation tests.\n"
    ).encode()


@pytest.fixture
def expected(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    return check_dist.expected_from_pyproject(pyproject)


def _write_sdist(path, metadata=None, root=f"vorflow-{VERSION}", extra=()):
    members = {
        f"{root}/pyproject.toml": b"",
        f"{root}/README.md": b"",
        f"{root}/LICENSE": b"MIT",
        f"{root}/src/vorflow/__init__.py": b"",
        **{f"{root}/{name}": b"" for name in extra},
    }
    if metadata is not False:
        members[f"{root}/PKG-INFO"] = _metadata() if metadata is None else metadata
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _write_wheel(path, metadata=None):
    dist_info = f"vorflow-{VERSION}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vorflow/__init__.py", "")
        archive.writestr(f"{dist_info}/licenses/LICENSE", "MIT")
        archive.writestr(
            f"{dist_info}/METADATA", _metadata() if metadata is None else metadata
        )


def _wheel_path(tmp_path):
    return tmp_path / f"vorflow-{VERSION}-py3-none-any.whl"


def _sdist_path(tmp_path):
    return tmp_path / f"vorflow-{VERSION}.tar.gz"


def test_expected_metadata_splits_people_per_pep_621(expected):
    assert expected.author_names == {"Nameonly Author"}
    assert expected.author_emails == {("Ada Author", "ada@example.org")}
    assert expected.requirements == {"numpy>=1.24", "shapely>=2.0"}


def test_real_pyproject_is_readable():
    real = check_dist.expected_from_pyproject(ROOT / "pyproject.toml")
    assert real.name == "vorflow"
    assert real.requirements


@pytest.mark.parametrize(
    ("tag", "version"),
    [("v0.1.0rc1", "0.1.0rc1"), ("v0.1.0", "0.1.0"), ("v1.2.3", "1.2.3")],
)
def test_version_from_tag_accepts_candidate_and_final_tags(tag, version):
    assert check_dist.version_from_tag(tag) == version


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0rc1",
        "vnot-rc-tag",
        "v0.1.0rc",
        "v0.1.0rc1junk",
        "v0.1.0a1",
        "v0.1.0b1",
        "v0.1.0.dev1",
        "v0.1.0.post1",
        "v0.1.0+local",
        "v0.1.0-rc1",
    ],
)
def test_version_from_tag_rejects_other_tags(tag):
    with pytest.raises(ValueError, match="expected a release tag"):
        check_dist.version_from_tag(tag)


def test_forbidden_members_are_reported():
    members = [
        "vorflow-0.1.0rc1/src/vorflow/__init__.py",
        "vorflow-0.1.0rc1/docs/private-plan.md",
        "vorflow-0.1.0rc1/src/vorflow/vorflow.code-workspace",
        "vorflow-0.1.0rc1/tests/test_pipeline.py",
    ]
    assert check_dist.forbidden_members(members) == members[1:]


def test_validate_wheel_accepts_matching_metadata(tmp_path, expected):
    wheel = _wheel_path(tmp_path)
    _write_wheel(wheel)
    check_dist.validate_wheel(wheel, expected)


def test_validate_sdist_accepts_matching_metadata(tmp_path, expected):
    sdist = _sdist_path(tmp_path)
    _write_sdist(sdist)
    check_dist.validate_sdist(sdist, expected)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            b"https://example.org/vorflow",
            b"https://github.com/oscarfasanchez/vorflow_os",
            "project URLs",
        ),
        (
            b"Project-URL: Repository, https://example.org/vorflow\n",
            b"Project-URL: Repository, https://example.org/vorflow\n"
            b"Project-URL: Fork, https://github.com/oscarfasanchez/vorflow_os\n",
            "project URLs",
        ),
        (b"Maintainer: Nameonly Author\n", b"", "maintainers"),
        (b"Maintainer-email: Ada Author <ada@example.org>", b"Maintainer-email: Ada Author <wrong@example.org>", "maintainer emails"),
        (b"Requires-Dist: shapely>=2.0\n", b"", "runtime requirements"),
        (b"numpy>=1.24", b"numpy>=1.20", "runtime requirements"),
        (b"Requires-Python: >=3.10", b"Requires-Python: >=3.9", "Requires-Python"),
        (b"License-Expression: MIT", b"License-Expression: BSD-3-Clause", "License-Expression"),
        (b"Provides-Extra: dev\n", b"", "extras"),
    ],
)
def test_validate_wheel_rejects_metadata_drift(tmp_path, expected, old, new, match):
    drifted = _metadata()
    assert old in drifted
    wheel = _wheel_path(tmp_path)
    _write_wheel(wheel, drifted.replace(old, new))
    with pytest.raises(ValueError, match=match):
        check_dist.validate_wheel(wheel, expected)


def test_validate_sdist_rejects_missing_pkg_info(tmp_path, expected):
    sdist = _sdist_path(tmp_path)
    _write_sdist(sdist, metadata=False)
    with pytest.raises(ValueError, match="PKG-INFO"):
        check_dist.validate_sdist(sdist, expected)


def test_validate_sdist_rejects_dev_artifacts(tmp_path, expected):
    sdist = _sdist_path(tmp_path)
    _write_sdist(sdist, extra=["tests/test_pipeline.py"])
    with pytest.raises(ValueError, match="forbidden"):
        check_dist.validate_sdist(sdist, expected)


def test_validate_sdist_rejects_mismatched_metadata_version(tmp_path, expected):
    sdist = _sdist_path(tmp_path)
    _write_sdist(sdist, metadata=_metadata(version="2.3.0"))
    with pytest.raises(ValueError, match="version"):
        check_dist.validate_sdist(sdist, expected)


def test_validate_sdist_rejects_wrong_filename(tmp_path, expected):
    sdist = tmp_path / f"renamed-{VERSION}.tar.gz"
    _write_sdist(sdist)
    with pytest.raises(ValueError, match="filename"):
        check_dist.validate_sdist(sdist, expected)


def test_validate_dist_rejects_tag_that_disagrees_with_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    with pytest.raises(ValueError, match="does not match pyproject version"):
        check_dist.validate_dist(tmp_path, pyproject, tag="v2.3.0rc5")


def test_validate_dist_rejects_duplicate_wheels(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT, encoding="utf-8")
    (tmp_path / "one.whl").touch()
    (tmp_path / "two.whl").touch()
    _write_sdist(_sdist_path(tmp_path))
    with pytest.raises(ValueError, match=r"expected one \*\.whl"):
        check_dist.validate_dist(tmp_path, pyproject)


@pytest.mark.slow
def test_built_archives_match_real_pyproject(tmp_path):
    """Build vorflow itself and check its archives against pyproject.toml."""
    pytest.importorskip("build")
    setuptools = pytest.importorskip("setuptools")
    from packaging.version import Version

    if Version(setuptools.__version__) < Version("77.0.3"):
        pytest.skip("--no-isolation build needs setuptools>=77.0.3")
    # Build from a copy so the --no-isolation build never writes build/ or
    # *.egg-info into the checkout.
    source = tmp_path / "source"
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info", "*.py[cod]")
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"):
        shutil.copy2(ROOT / name, source / name)
    for name in ("src", "tests"):
        shutil.copytree(ROOT / name, source / name, ignore=ignore)
    out = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(out), str(source)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    check_dist.validate_dist(out, ROOT / "pyproject.toml")
