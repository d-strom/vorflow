# vorflow

Voronoi mesh generation for MODFLOW 6 using Gmsh and GeoPandas.

`vorflow` is a Python package for creating 2D unstructured Voronoi cell meshes for groundwater modeling, particularly for MODFLOW 6. It leverages the power of `Gmsh` for robust triangular meshing and `Shapely`/`GeoPandas` for geometric operations.

The process is designed to translate a conceptual model—defined by geometric features like polygons, lines, and points—into a high-quality Voronoi grid suitable for numerical simulation.

## Core Components

The library is built around three main classes that work in sequence:

1.  **`ConceptualMesh`**: A blueprinting tool to define the model domain and its features. You can add polygons (e.g., model boundary, refinement zones), lines (rivers, faults), and points (wells) and specify the desired mesh density and refinement behavior for each.

2.  **`MeshGenerator`**: This is the engine that generates a triangular mesh based on the blueprint from `ConceptualMesh`. It uses `Gmsh` as its backend to create a quality-conforming Delaunay triangulation.

3.  **`VoronoiTessellator`**: This class takes the triangular mesh from `MeshGenerator` and computes its dual: the Voronoi diagram. The result is a grid of polygonal cells. It includes logic to clip the grid to the domain boundary and enforce barrier features by cutting through cells.

## Workflow

The typical workflow follows these steps:

1.  **Define Geometry**: Create `shapely` objects for your model features (domain boundary, rivers, wells, etc.).
2.  **Create a Blueprint**: Instantiate `ConceptualMesh` and add your geometries, specifying parameters like mesh resolution, refinement distances, and feature types (e.g., barriers).
3.  **Generate Mesh**: Instantiate `MeshGenerator` and call its `generate()` method with the processed geometries from the blueprint. This produces a triangular mesh.
4.  **Tessellate to Voronoi**: Instantiate `VoronoiTessellator` with the generated mesh and the blueprint. Calling its `generate()` method produces the final `GeoDataFrame` of Voronoi cells.
5.  **Export**: The resulting `GeoDataFrame` can be easily saved to a shapefile or other formats.

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
[`etc/environment.yml`](https://github.com/rhugman/vorflow/blob/main/etc/environment.yml).

## Basic Usage

Here is a simple example of how to generate a non-empty Voronoi grid:

The complete runnable version is
[examples/basic_usage.py](https://github.com/rhugman/vorflow/blob/main/examples/basic_usage.py).

```python
from shapely.geometry import LineString, Point, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator

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
```

### Optional file export

GeoPandas writes formats such as Shapefile and GeoPackage through an I/O engine
such as Pyogrio or Fiona. Install one of those engines before calling:

```python
grid_gdf.to_file("mf6_grid.gpkg", driver="GPKG")
```

### Mesh gradation

Feature resolutions use `GeometricGrowthField` by default. Its
`growth_factor` is an upper target for neighboring characteristic edge-length
growth, not cell area growth and not an exact guarantee for every generated
neighbor pair. The default `growth_factor=1.2` uses the transparent spatial law

```text
h(d) = feature_lc + (growth_factor - 1) * d.
```

For the continuous-metric convention, pass an explicit
`GeometricGrowthField(growth_model="continuous_metric")`; this uses the gentler
gradient `log(growth_factor)`. In normal `MeshGenerator` use, the global
background field caps either result at `background_lc`.

> **Coordinate systems:** always work in a *projected* CRS (e.g. UTM or a
> national grid) so mesh sizes are in real length units (meters/feet).
> Geographic coordinates (lat/lon degrees, e.g. EPSG:4326) produce
> physically meaningless MODFLOW grids — reproject your data first with
> `GeoDataFrame.to_crs()`.

## Examples

The [examples/](https://github.com/rhugman/vorflow/tree/main/examples)
folder contains runnable scripts and notebooks
covering field-based refinement, mesh quality diagnostics, structured quad
buffers, active-domain workflows, and triangular element-grid export.

## Roadmap

See [ROADMAP.md](https://github.com/rhugman/vorflow/blob/main/ROADMAP.md)
for planned and completed milestones.

## License

MIT — see [LICENSE](https://github.com/rhugman/vorflow/blob/main/LICENSE).
