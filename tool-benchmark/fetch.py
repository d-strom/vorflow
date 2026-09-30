"""Fetch the other tools' example inputs, which can't be committed, into data/.

* mf6Voronoi test cases (``tests/data/<case>/shp``): MIT code, but the data
  have no licence, so they are downloaded at a pinned commit
  (docs/benchmark-plan.md, Licensing and data).
* VOROGRIDGEN's shipped example: the freeware must not be redistributed, so the
  zip is downloaded from Hydrosymple (or reused from ``.bin/vorogridgen_dist``,
  where the CI job unpacks it) and its BLN files are converted to a GeoPackage.

``ensure()`` downloads only what is missing; ``workflow.main`` calls it, so a
fresh checkout or CI runner fetches on first use. Run ``python fetch.py`` to
fetch by hand.
"""

import io
import os
import urllib.request
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import shapely
from shapely.geometry import LineString, MultiLineString, Point, Polygon

HERE = Path(__file__).resolve().parent
DATA_D = HERE / "data"
BIN_D = HERE / ".bin"

MF6VORONOI_SHA = "38a29849f9e69ca2343ee1dd191e9d766b5a078f"   # 2026-09-17
MF6VORONOI_RAW = f"https://raw.githubusercontent.com/hatarilabs/mf6Voronoi/{MF6VORONOI_SHA}/tests/data"
# Shapefile layers per case, as tests/json/meshCasesNormal.json names them.
MF6VORONOI_CASES = {
    "c2_riverAquifer": ("ModelLimit1", "ModelGHB1", "ModelRiver2", "ModelWell2"),
    "c7_trenchExcavation": ("modelAoi", "compoundGhb", "pumpingWells", "trenchExcavationDissolved"),
}
SHAPEFILE_PARTS = ("shp", "shx", "dbf", "prj")

VOROGRIDGEN_URL = "https://hydrosymple.com/?sdm_process_download=1&download_id=7123"
# Hydrosymple answers 403 to urllib's default "Python-urllib" agent.
USER_AGENT = "vorflow-benchmark (+https://github.com/rhugman/vorflow)"
VOROGRIDGEN_EXAMPLE = "vorogridgen_example/example.gpkg"


def ensure(data_d: Path = DATA_D) -> None:
    """Download every missing input."""
    for case, layers in MF6VORONOI_CASES.items():
        for layer in layers:
            for ext in SHAPEFILE_PARTS:
                target = data_d / "mf6voronoi" / case / f"{layer}.{ext}"
                if not target.exists():
                    _download(f"{MF6VORONOI_RAW}/{case}/shp/{layer}.{ext}", target)
    if not (data_d / VOROGRIDGEN_EXAMPLE).exists():
        convert_vorogridgen_example(_vorogridgen_example_files(), data_d / VOROGRIDGEN_EXAMPLE)


def _download(url: str, target: Path) -> None:
    """Write one URL to target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open(url, timeout=120) as response:
        target.write_bytes(response.read())
    print(f"fetched {os.path.relpath(target, HERE)}")


def _open(url: str, timeout: float):
    """urlopen with the benchmark's User-Agent."""
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def _vorogridgen_example_files() -> dict:
    """Example files by name: from .bin/vorogridgen_dist if unpacked there, else from the zip."""
    unpacked = BIN_D / "vorogridgen_dist" / "example"
    if unpacked.is_dir():
        return {p.name: p.read_text() for p in unpacked.iterdir() if p.is_file()}
    with _open(VOROGRIDGEN_URL, timeout=300) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    return {Path(name).name: archive.read(name).decode()
            for name in archive.namelist() if "/example/" in f"/{name}" and not name.endswith("/")}


def convert_vorogridgen_example(files: dict, target: Path) -> None:
    """Write the example's geometry and spacings as GeoPackage layers.

    Layers: ``domain`` (outer boundary with both holes), ``boundary_fine`` (the
    outer-boundary segments whose two vertices have a spacing below the
    maximum, and each hole ring, with an ``h`` column), ``line1``, ``line2``, ``points`` (with ``h``),
    ``poly1``, ``poly2``. The sizes in cases/f6_vorogridgen_example.yml are the
    example's spacings.
    """
    outer = _read_bln(files["outer_boundary.bln"])
    holes = [_read_bln(files[f"inner_boundary_{i}.bln"]) for i in (1, 2)]
    h_outer = max(r[2] for r in outer)
    # Outer-boundary segments whose two vertices both have a finer spacing.
    fine = {}
    for a, b in zip(outer, outer[1:] + outer[:1]):
        if a[2] < h_outer and b[2] < h_outer:
            fine.setdefault(max(a[2], b[2]), []).append(LineString([a[:2], b[:2]]))
    rows = [{"id": f"outer_{h:g}", "h": h, "geometry": shapely.line_merge(MultiLineString(parts))}
            for h, parts in sorted(fine.items())]
    for i, ring in enumerate(holes):
        rows.append({"id": f"hole{i + 1}", "h": ring[0][2], "geometry": LineString([r[:2] for r in ring])})
    layers = {
        "domain": gpd.GeoDataFrame(geometry=[Polygon([r[:2] for r in outer], [[r[:2] for r in h] for h in holes])]),
        "boundary_fine": gpd.GeoDataFrame(rows),
        "points": gpd.GeoDataFrame({"h": [r[2] for r in _read_bln(files["points.dat"])]},
                                   geometry=[Point(r[:2]) for r in _read_bln(files["points.dat"])]),
    }
    for name in ("line1", "line2"):
        rows_l = _read_bln(files[f"{name}.bln"])
        layers[name] = gpd.GeoDataFrame({"h": [rows_l[0][2]]}, geometry=[LineString([r[:2] for r in rows_l])])
    for name in ("poly1", "poly2"):
        layers[name] = gpd.GeoDataFrame(geometry=[Polygon([r[:2] for r in _read_bln(files[f"{name}.bln"])])])
    target.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="'crs' was not provided")   # the example has no .prj
        for name, gdf in layers.items():
            gdf.to_file(target, layer=name, driver="GPKG")
    print(f"converted the VOROGRIDGEN example to {os.path.relpath(target, HERE)}")


def _read_bln(text: str) -> list:
    """(x, y, spacing) rows of a BLN file; spacing 0 where the file has none."""
    rows = []
    for line in text.splitlines()[1:]:
        tokens = line.replace(",", " ").split()
        if len(tokens) >= 2:
            rows.append((float(tokens[0]), float(tokens[1]), float(tokens[2]) if len(tokens) > 2 else 0.0))
    return rows


if __name__ == "__main__":
    ensure()
