"""Validate vorflow wheel/sdist contents and release metadata."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
from email.utils import getaddresses
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

from packaging.version import InvalidVersion, Version


FORBIDDEN_DIRECTORIES = {".conda", "benchmarks", "docs", "__pycache__"}
EXPECTED_REQUIREMENTS = {
    "numpy>=1.24",
    "pandas>=1.5",
    "geopandas>=0.13",
    "shapely>=2.0",
    "scipy>=1.10",
    "gmsh>=4.11",
}
EXPECTED_URLS = {
    "Repository, https://github.com/rhugman/vorflow",
    "Issues, https://github.com/rhugman/vorflow/issues",
    "Changelog, https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md",
}
EXPECTED_MAINTAINERS = {
    ("Oscar Sanchez", "oscarfasanchez@gmail.com"),
    ("rhugman", "rthugman@gmail.com"),
}


def version_from_tag(tag: str) -> str:
    if not tag.startswith("v"):
        raise ValueError(f"expected a release-candidate tag, received {tag!r}")
    value = tag[1:]
    try:
        version = Version(value)
    except InvalidVersion as error:
        raise ValueError(
            f"expected a release-candidate tag, received {tag!r}"
        ) from error
    if version.pre is None or version.pre[0] != "rc" or tag != f"v{version}":
        raise ValueError(f"expected a release-candidate tag, received {tag!r}")
    return str(version)


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


def _validate_metadata(metadata, expected_version: str, archive_kind: str) -> None:
    project_name = metadata["Name"]
    if project_name is None or project_name.lower().replace("_", "-") != "vorflow":
        raise ValueError(f"unexpected {archive_kind} project name: {project_name}")
    if metadata["Version"] != expected_version:
        raise ValueError(
            f"{archive_kind} version {metadata['Version']} does not match "
            f"{expected_version}"
        )
    if metadata["Requires-Python"] != ">=3.10":
        raise ValueError(
            f"unexpected {archive_kind} Requires-Python: "
            f"{metadata['Requires-Python']}"
        )
    requirements = {
        _normalized_requirement(value)
        for value in metadata.get_all("Requires-Dist", [])
        if "extra==" not in _normalized_requirement(value)
    }
    if requirements != EXPECTED_REQUIREMENTS:
        raise ValueError(
            f"unexpected {archive_kind} runtime requirements: {requirements}"
        )
    if metadata["License-Expression"] != "MIT":
        raise ValueError(
            f"{archive_kind} does not declare the MIT SPDX expression"
        )
    if "LICENSE" not in metadata.get_all("License-File", []):
        raise ValueError(f"{archive_kind} metadata does not declare LICENSE")
    if "Oscar Sanchez" not in (metadata["Author-email"] or ""):
        raise ValueError(f"primary author is missing from {archive_kind} metadata")
    if "rhugman" not in (metadata["Author"] or ""):
        raise ValueError(f"original author is missing from {archive_kind} metadata")
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
        raise ValueError(
            f"{archive_kind} has incorrect project URLs: {project_urls}"
        )


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

    _validate_metadata(metadata, expected_version, "wheel")


def validate_sdist(sdist: Path, expected_version: str) -> None:
    expected_filename = f"vorflow-{expected_version}.tar.gz"
    if sdist.name != expected_filename:
        raise ValueError(
            f"sdist filename {sdist.name!r} does not match {expected_filename!r}"
        )
    root = f"vorflow-{expected_version}"
    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        bad = forbidden_members(names)
        if bad:
            raise ValueError(f"sdist contains forbidden members: {bad}")
        roots = {
            PurePosixPath(name).parts[0]
            for name in names
            if PurePosixPath(name).parts
        }
        if roots != {root}:
            raise ValueError(f"sdist has unexpected top-level paths: {sorted(roots)}")
        for required in (
            "PKG-INFO",
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "src/vorflow/__init__.py",
        ):
            member_name = f"{root}/{required}"
            if member_name not in names:
                raise ValueError(f"sdist is missing {member_name}")
        metadata_file = archive.extractfile(f"{root}/PKG-INFO")
        if metadata_file is None:
            raise ValueError(f"sdist cannot read {root}/PKG-INFO")
        metadata = BytesParser(policy=default).parsebytes(metadata_file.read())

    _validate_metadata(metadata, expected_version, "sdist")


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
