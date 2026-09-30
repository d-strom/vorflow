import os
from pathlib import Path
import subprocess
import sys

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
        {"name": "Oscar Sanchez", "email": "oscarfasanchez@gmail.com"},
        {"name": "rhugman", "email": "rthugman@gmail.com"},
    ]
    assert project["urls"] == {
        "Repository": "https://github.com/rhugman/vorflow",
        "Issues": "https://github.com/rhugman/vorflow/issues",
        "Changelog": "https://github.com/rhugman/vorflow/blob/main/CHANGELOG.md",
    }
    assert project["dependencies"] == [
        "numpy>=1.24",
        "pandas>=1.5",
        "geopandas>=0.13",
        "shapely>=2.0",
        "scipy>=1.10",
        "gmsh>=4.11",
    ]
    assert "License :: OSI Approved :: MIT License" not in project["classifiers"]


def test_public_docs_link_to_upstream():
    for name in ("README.md", "CHANGELOG.md"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert "https://github.com/oscarfasanchez/vorflow_os" not in content
        assert "https://github.com/rhugman/vorflow" in content


def test_source_fallback_is_not_a_duplicate_release_version():
    source = (ROOT / "src" / "vorflow" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert '__version__ = "0+unknown"' in source
    assert '__version__ = "0.0.2"' not in source


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
