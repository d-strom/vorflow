import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_dist.py"
SPEC = importlib.util.spec_from_file_location("check_dist", SCRIPT)
check_dist = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_dist)


def _metadata(version="0.1.0rc1"):
    requirements = "\n".join(
        f"Requires-Dist: {requirement}"
        for requirement in (
            "numpy>=1.24",
            "pandas>=1.5",
            "geopandas>=0.13",
            "shapely>=2.0",
            "scipy>=1.10",
            "gmsh>=4.11",
        )
    )
    return (
        "Metadata-Version: 2.4\n"
        "Name: vorflow\n"
        f"Version: {version}\n"
        "Author: rhugman\n"
        "Author-email: Oscar Sanchez <oscarfasanchez@gmail.com>\n"
        "Maintainer-email: Oscar Sanchez <oscarfasanchez@gmail.com>, "
        "rhugman <rthugman@gmail.com>\n"
        "License-Expression: MIT\n"
        "License-File: LICENSE\n"
        "Requires-Python: >=3.10\n"
        "Project-URL: Repository, https://github.com/rhugman/vorflow\n"
        "Project-URL: Issues, https://github.com/rhugman/vorflow/issues\n"
        "Project-URL: Changelog, https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md\n"
        f"{requirements}\n"
        "\n"
        "Synthetic package metadata for archive validation tests.\n"
    ).encode()


def _write_sdist(path, metadata=_metadata(), root="vorflow-0.1.0rc1"):
    members = {
        f"{root}/pyproject.toml": b"",
        f"{root}/README.md": b"",
        f"{root}/LICENSE": b"MIT",
        f"{root}/src/vorflow/__init__.py": b"",
    }
    if metadata is not None:
        members[f"{root}/PKG-INFO"] = metadata
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _write_wheel(path, metadata=None):
    dist_info = "vorflow-0.1.0rc1.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("vorflow/__init__.py", "")
        archive.writestr(f"{dist_info}/licenses/LICENSE", "MIT")
        archive.writestr(
            f"{dist_info}/METADATA", _metadata() if metadata is None else metadata
        )


def test_version_from_tag():
    assert check_dist.version_from_tag("v0.1.0rc1") == "0.1.0rc1"


def test_version_from_tag_rejects_production_tag():
    try:
        check_dist.version_from_tag("v0.1.0")
    except ValueError as error:
        assert "release-candidate" in str(error)
    else:
        raise AssertionError("production tag was accepted")


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0rc1",
        "vnot-rc-tag",
        "v0.1.0rc",
        "v0.1.0rc1junk",
    ],
)
def test_version_from_tag_rejects_malformed_candidate_tags(tag):
    with pytest.raises(ValueError, match="release-candidate"):
        check_dist.version_from_tag(tag)


def test_forbidden_members_are_reported():
    members = [
        "vorflow-0.1.0rc1/src/vorflow/__init__.py",
        "vorflow-0.1.0rc1/docs/private-plan.md",
        "vorflow-0.1.0rc1/src/vorflow/vorflow.code-workspace",
    ]
    assert check_dist.forbidden_members(members) == [members[1], members[2]]


def test_validate_wheel_accepts_complete_metadata(tmp_path):
    wheel = tmp_path / "vorflow-0.1.0rc1-py3-none-any.whl"
    _write_wheel(wheel)

    check_dist.validate_wheel(wheel, "0.1.0rc1")


def test_validate_sdist_accepts_complete_metadata(tmp_path):
    sdist = tmp_path / "vorflow-0.1.0rc1.tar.gz"
    _write_sdist(sdist)

    check_dist.validate_sdist(sdist, "0.1.0rc1")


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
    legacy_urls = b"\n".join(
        (
            b"Project-URL: Repository, https://github.com/oscarfasanchez/vorflow_os",
            b"Project-URL: Issues, https://github.com/oscarfasanchez/vorflow_os/issues",
            b"Project-URL: Changelog, https://github.com/oscarfasanchez/vorflow_os/blob/main/CHANGELOG.md",
        )
    )
    mixed = _metadata().replace(
        b"\n\nSynthetic package metadata",
        b"\n" + legacy_urls + b"\n\nSynthetic package metadata",
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


def test_validate_sdist_rejects_missing_pkg_info(tmp_path):
    sdist = tmp_path / "vorflow-0.1.0rc1.tar.gz"
    _write_sdist(sdist, metadata=None)

    with pytest.raises(ValueError, match="PKG-INFO"):
        check_dist.validate_sdist(sdist, "0.1.0rc1")


def test_validate_sdist_rejects_mismatched_metadata_version(tmp_path):
    sdist = tmp_path / "vorflow-0.1.0rc1.tar.gz"
    _write_sdist(sdist, metadata=_metadata(version="0.1.0"))

    with pytest.raises(ValueError, match="does not match"):
        check_dist.validate_sdist(sdist, "0.1.0rc1")


def test_validate_sdist_rejects_wrong_filename(tmp_path):
    sdist = tmp_path / "renamed-0.1.0rc1.tar.gz"
    _write_sdist(sdist)

    with pytest.raises(ValueError, match="filename"):
        check_dist.validate_sdist(sdist, "0.1.0rc1")


def test_validate_dist_rejects_duplicate_wheels(tmp_path):
    (tmp_path / "one.whl").touch()
    (tmp_path / "two.whl").touch()
    (tmp_path / "vorflow-0.1.0rc1.tar.gz").touch()

    with pytest.raises(ValueError, match=r"expected one \*\.whl"):
        check_dist.validate_dist(tmp_path, "0.1.0rc1")
