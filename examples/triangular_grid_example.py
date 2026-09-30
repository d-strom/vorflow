#%%
"""Triangular grid output example.

Run this file in VS Code with the Python extension. The `#%%` markers create
cell-like execution, similar to a notebook.

Some workflows want the gmsh triangular mesh itself as the deliverable (e.g. a
triangular FE/FV grid) instead of the Voronoi dual. `MeshGenerator` caches the
2D element polygons during `generate()`, so after meshing you can export:

- ``get_element_grid("triangles")`` — the plain triangular grid as a
  GeoDataFrame with zone ids assigned by element centroid,
- ``get_element_grid("all")`` / ``("quads")`` — the mixed tri/quad grid when a
  structured quad buffer is present,
- ``get_triangular_quality()`` — per-element gmsh quality metrics, joinable on
  ``element_tag``.

The Voronoi tessellation step is simply never invoked here.
"""

import matplotlib.pyplot as plt
from shapely.geometry import LineString, Polygon, box

from vorflow import ConceptualMesh, MeshGenerator
from vorflow.fields import GeometricGrowthField
from vorflow.utils import build_connectivity

#%%
# Conceptual model: a domain with a refined inner zone and a structured quad
# buffer along a fault, so the exported grid is a mixed tri/quad mesh.
field = GeometricGrowthField(growth_factor=1.2)
domain = box(0, 0, 20, 12)
inner_zone = Polygon([(6, 3), (14, 3), (14, 9), (6, 9)])
fault = LineString([(2, 6), (18, 6)])

mesh = ConceptualMesh(crs=None)
mesh.add_polygon(domain, zone_id="domain", resolution=2.0, densify=True)
mesh.add_polygon(
    inner_zone,
    zone_id="inner-zone",
    resolution=1.0,
    z_order=1,
    densify=True,
    fields=[field],
)
mesh.add_line(
    fault,
    line_id="fault",
    resolution=1.0,
    is_barrier=True,
    quad_buffer=True,
    quad_buffer_thickness=1,
    fields=[field],
)

clean_polys, clean_lines, clean_points = mesh.generate()

mesher = MeshGenerator(background_lc=2.0, verbosity=0)
mesher.generate(clean_polys, clean_lines, clean_points)

#%%
# The element grid is the deliverable: triangles (plus quads from the buffer),
# each with a zone id assigned by element centroid.
element_grid = mesher.get_element_grid()          # mixed tri/quad
triangles = mesher.get_element_grid("triangles")  # triangular-only view
quads = mesher.get_element_grid("quads")

print(f"2D elements: {len(element_grid)}")
print(f"  triangles: {len(triangles)}")
print(f"  quads:     {len(quads)}")
print("\nElements per zone:")
print(element_grid["zone_id"].value_counts().to_string())

#%%
# Per-element quality joins on element_tag. gamma is the inscribed/circumscribed
# radius ratio (1.0 = equilateral); it applies to the triangles.
quality = mesher.get_triangular_quality()
graded = element_grid.merge(quality[["element_tag", "gamma"]], on="element_tag", how="left")
print("\nTriangle gamma quality:")
print(graded.loc[graded["is_triangle"], "gamma"].describe()[["min", "mean", "50%"]])

# Element-centroid connectivity works on the element grid too (no generator
# columns needed), e.g. for FV-style face checks on the triangular grid.
connectivity = build_connectivity(element_grid, center="centroid")
print(f"\nElement-face connectivity pairs: {len(connectivity)}")

#%%
fig, axes = plt.subplots(1, 3, figsize=(18, 5), constrained_layout=True)

element_grid.plot(
    column="zone_id", ax=axes[0], categorical=True, legend=True,
    cmap="Pastel2", edgecolor="#666666", linewidth=0.3,
)
axes[0].plot(*fault.xy, color="black", linewidth=1.2, linestyle='--')
axes[0].set_title("Element grid colored by zone_id")

graded.plot(
    column="gamma", ax=axes[1], cmap="viridis", legend=True,
    edgecolor="#444444", linewidth=0.2, vmin=0.5, vmax=1.0,
    missing_kwds={"color": "#f3c97a", "label": "quads"},
)
axes[1].set_title("Triangle quality (gamma, 1.0 = equilateral)")

triangles.plot(ax=axes[2], facecolor="#d7e3f4", edgecolor="#8aa0bd", linewidth=0.3)
quads.plot(ax=axes[2], facecolor="#f3c97a", edgecolor="#9b6b1f", linewidth=0.5)
axes[2].plot(*fault.xy, color="black", linewidth=1.2, linestyle='--')
axes[2].set_title("Triangular grid with structured quad buffer")

for ax in axes:
    ax.set_aspect("equal")
    ax.axis("off")

plt.show()

# %%
