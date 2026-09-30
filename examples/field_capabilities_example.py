#%%
"""Field capabilities example (pseudo-notebook).

Run this file in VS Code with the Python extension. The `#%%` markers create
cell-like execution, similar to a notebook.

This example duplicates polygon and line geometries to exercise multiple mesh
field types:
- Implicit threshold via `dist_min`/`dist_max`
- Explicit `ThresholdField`
- `ExponentialField`
- `AutoLinearField`
- `AutoExponentialField`
- Field-only polygon via `embed=False`
- Barrier/straddle line to validate point-pair representation
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

from shapely.affinity import translate
from shapely.geometry import LineString, Point, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator
from vorflow.fields import (
    AutoExponentialField,
    AutoLinearField,
    ThresholdField,
)
from vorflow.utils import calculate_mesh_quality, summarize_quality

#%%
# Geometry
domain = box(0, 0, 400, 200)
# Base refinement polygon (simplified set)
poly_base = box(40, 40, 70, 70)

# Create a 2x3 layout (upper/lower × left/center/right)
ul = translate(poly_base, xoff=0, yoff=80)
uc = translate(poly_base, xoff=120, yoff=80)
ur = translate(poly_base, xoff=240, yoff=80)
ll = translate(poly_base, xoff=0, yoff=0)
lc = translate(poly_base, xoff=120, yoff=0)
lr = translate(poly_base, xoff=240, yoff=0)

polys = {
    "upper-left": ul,
    "upper-center": uc,
    "upper-right": ur,
    "lower-left": ll,
    "lower-center": lc,
    "lower-right": lr,
}

# A gentle sine-wave river
xs = np.linspace(-10, 430, 45)
river_y = 140 + 18 * np.sin(0.06 * xs)
river_base = LineString(list(zip(xs, river_y)))
rivers = {
    "river-center-up": translate(river_base, xoff=0, yoff=0),
    "river-center-down": translate(river_base, xoff=0, yoff=-60),
}

# Points (simplified)
points = {
    "pt-lower-left": Point(25, 25),
    "pt-lower-center": Point(185, 25),
    "pt-lower-right": Point(325, 25),
}
#%%
# Fields used below

auto_linear = AutoLinearField(growth_factor=1.1)
auto_exp = AutoExponentialField(growth_factor=1.1)
threshold = ThresholdField(size_min=5.0, dist_min=5.0, dist_max=50.0, size_max=20.0)





#%%
# 1) Setup Blueprint + 2) Mesh Generation + 3) Voronoi Conversion

background_lc = 100
feature_lc = 10  # representative base feature length for features

blueprint = ConceptualMesh(crs="EPSG:3857")
blueprint.add_polygon(domain, zone_id="domain")  # color: black (domain boundary)

# Polygons (IDs are location-based)
blueprint.add_polygon(
    polys["upper-left"],
    zone_id="upper-left",
    resolution=feature_lc/5,
    z_order=10,
    dist_min=feature_lc/2,#using the implicit threshold approach here (instead of an explicit ThresholdField) to validate both code paths
    dist_max=background_lc * 5.0,
)

blueprint.add_polygon(
    polys["upper-center"],
    zone_id="upper-center",
    resolution=feature_lc/5,
    z_order=10,
    fields=[auto_exp],
    embed=False,  # field-only polygon 
)

blueprint.add_polygon(
    polys["upper-right"],
    zone_id="upper-right",
    resolution=feature_lc/5,
    z_order=10,
    fields=[auto_linear],
)


blueprint.add_line(
    polys["lower-left"].boundary,
    line_id='lower-left',# zone_id="lower-left",
    resolution=feature_lc/5,
    # z_order=5,
    dist_min=feature_lc/2,
    dist_max=background_lc * 1.5,
    fields=[auto_linear],
    embed=False,  # field-only boundary line
)

blueprint.add_polygon(
    polys["lower-center"],
    zone_id="lower-center",
    resolution=feature_lc/5,
    z_order=5,
    fields=[auto_exp],
)

blueprint.add_polygon(
    polys["lower-right"],
    zone_id="lower-right",
    resolution=feature_lc/5,
    z_order=5,
    fields=[threshold],
    embed=False,  # field-only polygon
)

blueprint.add_line(
    rivers["river-center-up"],
    line_id="river-center-up",
    resolution=feature_lc/2,
    is_barrier=False,
    fields=[threshold],
)

blueprint.add_line(
    rivers["river-center-down"],
    line_id="river-center-down",
    resolution=feature_lc/4,
    is_barrier=False,
    fields=[auto_exp],
    embed=False,  # field-only line
)

# Points (IDs are location-based)
# Colors: pt-lower-left -> 'tab:red', pt-lower-center -> 'tab:purple'
blueprint.add_point(
    points["pt-lower-left"],
    point_id="pt-lower-left",
    resolution=feature_lc/5,
    dist_min=feature_lc/5,
    dist_max=background_lc * 1.5,
)
blueprint.add_point(
    points["pt-lower-center"],
    point_id="pt-lower-center",
    resolution=feature_lc/5,
    fields=[auto_exp],
    embed=False,  # field-only point
)

blueprint.add_point(
    points["pt-lower-right"],
    point_id="pt-lower-right",
    resolution=feature_lc/5,
    fields=[auto_linear],
    embed=False,  # field-only point
)

clean_polys, clean_lines, clean_pts = blueprint.generate()

mesher = MeshGenerator(background_lc=background_lc, verbosity=0)
mesher.generate(clean_polys, clean_lines, clean_pts, launch_gmsh_gui=False)

tessellator = VoronoiTessellator(mesher, blueprint, clip_to_boundary=True)
grid_gdf = tessellator.generate()


#%%
# 4) Visual sanity-check

fig, ax = plt.subplots(1, 1, figsize=(14, 7))
ax.set_aspect("equal")

# Domain
ax.plot(*domain.exterior.xy, color="black", lw=1)

# Polygons
plot_polys = polys
poly_colors = {
    "upper-left": "tab:orange",
    "upper-center": "tab:green",
    "upper-right": "tab:blue",
    "lower-left": "tab:purple",
    "lower-center": "tab:brown",
    "lower-right": "tab:pink",
}
field_only = {
    "upper-center": "polygon",
    "lower-left": "boundary line",
    "lower-right": "polygon",
}
for name, poly in plot_polys.items():
    field_only_kind = field_only.get(name)
    label = f"{name} (field-only {field_only_kind})" if field_only_kind else name
    ax.plot(
        *poly.exterior.xy,
        lw=1,
        ls=":" if field_only_kind else "--",
        color=poly_colors.get(name),
        label=label,
    )

line_colors = {
    "fault-left": "tab:red",
    "fault-center": "tab:purple",
    "fault-right": "tab:blue",
    "river-center-up": "tab:cyan",
    "river-center-down": "tab:cyan",
}

ax.plot(
    *rivers["river-center-up"].xy,
    lw=1,
    color=line_colors["river-center-up"],
    label="river-center-up",
)
ax.plot(
    *rivers["river-center-down"].xy,
    lw=1,
    color=line_colors["river-center-down"],
    label="river-center-down (field-only)",
)

# Points
point_colors = {
    "pt-lower-left": "tab:red",
    "pt-lower-center": "tab:purple",
    "pt-lower-right": "tab:blue",
}
for name, pt in points.items():
    ax.scatter(pt.x, pt.y, s=25, marker="x", color=point_colors.get(name), label=name)

grid_gdf.plot(ax=ax, alpha=0.35, edgecolor="k", linewidth=0.15)
ax.legend(loc="upper right", fontsize=7, ncol=2)
fig.tight_layout()
plt.show()


#%%
# Quick check: smaller cells => smaller polygon areas (proxy for refinement)

grid_gdf2 = grid_gdf.copy()
grid_gdf2["area"] = grid_gdf2.geometry.area

fig, ax = plt.subplots(1, 1, figsize=(14, 6))
ax.set_aspect("equal")
ax.plot(*domain.exterior.xy, color="black", lw=1)
grid_gdf2.plot(ax=ax, column="area", cmap="viridis", legend=True, linewidth=0.0)
ax.set_title("Voronoi cell area (proxy for refinement)")
fig.tight_layout()
plt.show()

# %%
quality_gdf = calculate_mesh_quality(grid_gdf, calc_ortho=True)
summarize_quality(quality_gdf)

# Plot Orthogonality Error
fig, ax = plt.subplots(figsize=(10, 8))
quality_gdf.plot(column='ortho_error', ax=ax, legend=True, cmap='Reds', vmin=0, vmax=1)
plt.title("Orthogonality Error (Degrees)")
plt.show()
# %%
