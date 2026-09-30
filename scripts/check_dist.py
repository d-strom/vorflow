"""Validate vorflow wheel/sdist contents against the metadata in pyproject.toml.

Every expected value (name, version, dependencies, people, URLs, licence) is
read from ``pyproject.toml`` so a release bump only has to touch that file.
The checks confirm that the built archives carry exactly that metadata, that
an optional release tag agrees with the project version, and that no
development artifacts leak into either archive.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from email.utils import getaddresses
from pathlib import Path, PurePosixPath
import tarfile
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRECTORIES = {
    ".conda",
    ".github",
    "benchmarks",
    "docs",
    "tests",
    "tool-benchmark",
    "__pycache__",
}


@dataclass(frozen=True)
class ExpectedMetadata:
    """Core-metadata values implied by a pyproject ``[project]`` table."""

    name: str
    version: str
    summary: str | None
    requires_python: str | None
    requirements: frozenset[str]
    extras: frozenset[str]
    license_expression: str | None
    license_files: frozenset[str]
    author_names: frozenset[str]
    author_emails: frozenset[tuple[str, str]]
    maintainer_names: frozenset[str]
    maintainer_emails: frozenset[tuple[str, str]]
    urls: frozenset[str]


def _split_people(people: list[dict]) -> tuple[frozenset, frozenset]:
    # PEP 621: name-only entries go to Author/Maintainer, entries with an
    # email go to Author-email/Maintainer-email as "Name <email>".
    names = frozenset(p["name"] for p in people if "email" not in p)
    emails = frozenset((p.get("name", ""), p["email"]) for p in people if "email" in p)
    return names, emails


def _normalized_requirement(value: str) -> str:
    return str(Requirement(value))


def expected_from_pyproject(pyproject: Path) -> ExpectedMetadata:
    with pyproject.open("rb") as stream:
        project = tomllib.load(stream)["project"]
    author_names, author_emails = _split_people(project.get("authors", []))
    maintainer_names, maintainer_emails = _split_people(project.get("maintainers", []))
    return ExpectedMetadata(
        name=project["name"],
        version=str(Version(project["version"])),
        summary=project.get("description"),
        requires_python=project.get("requires-python"),
        requirements=frozenset(
            _normalized_requirement(r) for r in project.get("dependencies", [])
        ),
        extras=frozenset(project.get("optional-dependencies", {})),
        license_expression=project.get("license"),
        license_files=frozenset(project.get("license-files", [])),
        author_names=author_names,
        author_emails=author_emails,
        maintainer_names=maintainer_names,
        maintainer_emails=maintainer_emails,
        urls=frozenset(f"{k}, {v}" for k, v in project.get("urls", {}).items()),
    )


def version_from_tag(tag: str) -> str:
    """Return the version of a ``vX.Y.Z`` or ``vX.Y.ZrcN`` release tag.

    Release candidates go to TestPyPI and final releases to PyPI, so any other
    pre-, post-, dev- or local-release tag is rejected.
    """
    message = f"expected a release tag like v0.1.0 or v0.1.0rc1, received {tag!r}"
    if not tag.startswith("v"):
        raise ValueError(message)
    try:
        version = Version(tag[1:])
    except InvalidVersion as error:
        raise ValueError(message) from error
    candidate = version.pre is not None and version.pre[0] == "rc"
    final = version.pre is None
    if (
        not (candidate or final)
        or version.is_postrelease
        or version.is_devrelease
        or version.local is not None
        or tag != f"v{version}"
    ):
        raise ValueError(message)
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


def _dist_name(expected: ExpectedMetadata) -> str:
    # Archive filenames use the normalized name with underscores (PEP 625/427).
    return canonicalize_name(expected.name).replace("-", "_")


def _names_field(metadata, field: str) -> frozenset[str]:
    value = metadata[field]
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _emails_field(metadata, field: str) -> frozenset[tuple[str, str]]:
    return frozenset(getaddresses(metadata.get_all(field, [])))


def _check(archive_kind: str, label: str, actual, expected) -> None:
    if actual != expected:
        raise ValueError(
            f"{archive_kind} {label} {actual!r} does not match pyproject {expected!r}"
        )


def _validate_metadata(metadata, expected: ExpectedMetadata, archive_kind: str) -> None:
    name = metadata["Name"]
    _check(
        archive_kind,
        "project name",
        canonicalize_name(name or ""),
        canonicalize_name(expected.name),
    )
    _check(archive_kind, "version", metadata["Version"], expected.version)
    _check(archive_kind, "summary", metadata["Summary"], expected.summary)
    _check(
        archive_kind, "Requires-Python", metadata["Requires-Python"], expected.requires_python
    )
    requirements = [Requirement(v) for v in metadata.get_all("Requires-Dist", [])]
    runtime = frozenset(
        str(r) for r in requirements if r.marker is None or "extra" not in str(r.marker)
    )
    _check(archive_kind, "runtime requirements", runtime, expected.requirements)
    _check(
        archive_kind,
        "extras",
        frozenset(metadata.get_all("Provides-Extra", [])),
        expected.extras,
    )
    _check(
        archive_kind,
        "License-Expression",
        metadata["License-Expression"],
        expected.license_expression,
    )
    _check(
        archive_kind,
        "License-File",
        frozenset(metadata.get_all("License-File", [])),
        expected.license_files,
    )
    _check(archive_kind, "authors", _names_field(metadata, "Author"), expected.author_names)
    _check(
        archive_kind,
        "author emails",
        _emails_field(metadata, "Author-email"),
        expected.author_emails,
    )
    _check(
        archive_kind,
        "maintainers",
        _names_field(metadata, "Maintainer"),
        expected.maintainer_names,
    )
    _check(
        archive_kind,
        "maintainer emails",
        _emails_field(metadata, "Maintainer-email"),
        expected.maintainer_emails,
    )
    _check(
        archive_kind,
        "project URLs",
        frozenset(metadata.get_all("Project-URL", [])),
        expected.urls,
    )


def validate_wheel(wheel: Path, expected: ExpectedMetadata) -> None:
    prefix = f"{_dist_name(expected)}-{expected.version}-"
    if not wheel.name.startswith(prefix):
        raise ValueError(f"wheel filename {wheel.name!r} does not start with {prefix!r}")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        bad = forbidden_members(names)
        if bad:
            raise ValueError(f"wheel contains forbidden members: {bad}")
        _require_suffix(names, "vorflow/__init__.py")
        for license_file in expected.license_files:
            _require_suffix(names, f".dist-info/licenses/{license_file}")
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise ValueError("wheel has no .dist-info/METADATA")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))

    _validate_metadata(metadata, expected, "wheel")


def validate_sdist(sdist: Path, expected: ExpectedMetadata) -> None:
    root = f"{_dist_name(expected)}-{expected.version}"
    expected_filename = f"{root}.tar.gz"
    if sdist.name != expected_filename:
        raise ValueError(
            f"sdist filename {sdist.name!r} does not match {expected_filename!r}"
        )
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
        required = {
            "PKG-INFO",
            "pyproject.toml",
            "README.md",
            "src/vorflow/__init__.py",
            *expected.license_files,
        }
        for member in sorted(required):
            member_name = f"{root}/{member}"
            if member_name not in names:
                raise ValueError(f"sdist is missing {member_name}")
        metadata_file = archive.extractfile(f"{root}/PKG-INFO")
        if metadata_file is None:
            raise ValueError(f"sdist cannot read {root}/PKG-INFO")
        metadata = BytesParser(policy=default).parsebytes(metadata_file.read())

    _validate_metadata(metadata, expected, "sdist")


def validate_dist(
    directory: Path,
    pyproject: Path = ROOT / "pyproject.toml",
    tag: str | None = None,
) -> None:
    expected = expected_from_pyproject(pyproject)
    if tag is not None:
        tag_version = version_from_tag(tag)
        if tag_version != expected.version:
            raise ValueError(
                f"tag {tag!r} does not match pyproject version {expected.version!r}"
            )
    wheel = _one(directory, "*.whl")
    sdist = _one(directory, "*.tar.gz")
    validate_wheel(wheel, expected)
    validate_sdist(sdist, expected)
    print(f"Validated {wheel.name} and {sdist.name} against {pyproject}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="directory holding one wheel and one sdist")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=ROOT / "pyproject.toml",
        help="pyproject.toml to read expected metadata from",
    )
    parser.add_argument(
        "--expected-tag",
        help="release tag (e.g. v0.1.0 or v0.1.0rc1) that must match the project version",
    )
    args = parser.parse_args()
    validate_dist(args.directory, args.pyproject, args.expected_tag)


if __name__ == "__main__":
    main()
