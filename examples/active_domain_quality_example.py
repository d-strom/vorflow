#%%
"""Active-domain quality example (pseudo-notebook).

This example shows a workflow for getting healthier cells around a model's active
area without adding active-domain logic to `VoronoiTessellator`.

Two grids are compared:
- Baseline: the mesh boundary is the active model boundary, so boundary cells are
  clipped directly at the active edge.
- Larger envelope: the mesh is generated on a larger square envelope, then active
  cells are selected externally with GeoPandas.

The larger-envelope approach keeps active cells uncut near the active boundary,
which often improves MODFLOW-facing centroid connectivity metrics.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd

from shapely.geometry import box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator
from vorflow.utils import build_connectivity, calculate_mesh_quality

#%%
# 1) Geometry

active_domain = box(0, 0, 300, 180)
mesh_envelope = box(-60, -60, 360, 240)
cell_size = 18.0


def generate_voronoi(boundary, *, zone_id):
    conceptual = ConceptualMesh(crs="EPSG:3857")
    conceptual.add_polygon(boundary, zone_id=zone_id, resolution=cell_size, densify=True)
    clean_polys, clean_lines, clean_points = conceptual.generate()

    mesher = MeshGenerator(
        background_lc=cell_size,
        verbosity=0,
        smoothing_steps=5,
        optimization_cycles=1,
    )
    mesher.generate(clean_polys, clean_lines, clean_points, launch_gmsh_gui=False)

    tessellator = VoronoiTessellator(mesher, conceptual, clip_to_boundary=True)
    return tessellator.generate()


def tag_active_cells(grid, active_geom):
    tagged = grid.copy()
    overlap_area = tagged.geometry.intersection(active_geom).area
    tagged["active_overlap_ratio"] = overlap_area / tagged.geometry.area
    tagged["is_active_by_overlap"] = tagged["active_overlap_ratio"] >= 0.5
    tagged["is_active_by_centroid"] = tagged.geometry.centroid.within(active_geom)
    return tagged


def quality_with_centroid_connectivity(grid):
    connectivity = build_connectivity(grid.reset_index(drop=True), center="centroid")
    quality = calculate_mesh_quality(
        grid.reset_index(drop=True),
        calc_ortho=True,
        calc_skewness=True,
        connectivity=connectivity,
    )
    return quality, connectivity


def summarize_metrics(label, quality):
    metrics = ["area", "compactness", "drift_ratio", "ortho_error", "skewness"]
    summary = quality[metrics].describe(percentiles=[0.05, 0.5, 0.95]).T
    summary = summary[["min", "5%", "50%", "95%", "max"]]
    print(f"\n{label}")
    print(summary)
    return summary


#%%
# 2) Generate comparable grids

baseline_grid = generate_voronoi(active_domain, zone_id="active-domain")
envelope_grid = generate_voronoi(mesh_envelope, zone_id="mesh-envelope")

baseline_quality, baseline_connectivity = quality_with_centroid_connectivity(baseline_grid)

envelope_tagged = tag_active_cells(envelope_grid, active_domain)
active_grid = envelope_tagged[envelope_tagged["is_active_by_centroid"]].copy()
active_quality, active_connectivity = quality_with_centroid_connectivity(active_grid)
active_quality["active_overlap_ratio"] = active_grid["active_overlap_ratio"].to_numpy()

print("\nActive-cell selection from larger envelope")
print(f"  Full envelope cells: {len(envelope_tagged)}")
print(f"  Active by centroid: {int(envelope_tagged['is_active_by_centroid'].sum())}")
print(f"  Active by >=50% overlap: {int(envelope_tagged['is_active_by_overlap'].sum())}")

baseline_summary = summarize_metrics("Baseline: clipped at active boundary", baseline_quality)
active_summary = summarize_metrics("Larger envelope: active cells selected externally", active_quality)

comparison = pd.concat(
    {
        "baseline_clipped": baseline_summary[["50%", "95%", "max"]],
        "envelope_active": active_summary[["50%", "95%", "max"]],
    },
    axis=1,
)
print("\nQuality comparison")
print(comparison)

#%%
# 3) Plot grid geometry and active selection

fig, axes = plt.subplots(1, 2, figsize=(14, 6))

axes[0].set_aspect("equal")
baseline_quality.plot(
    column="ortho_error",
    ax=axes[0],
    cmap="Reds",
    legend=True,
    vmin=0,
    vmax=max(1.0, baseline_quality["ortho_error"].quantile(0.95)),
    edgecolor="0.75",
    linewidth=0.2,
)
axes[0].plot(*active_domain.exterior.xy, color="black", lw=1.2)
axes[0].set_title("Baseline: cells clipped to active boundary")
axes[0].set_axis_off()

axes[1].set_aspect("equal")
envelope_tagged.plot(ax=axes[1], color="0.9", edgecolor="white", linewidth=0.15)
active_grid.plot(ax=axes[1], color="tab:blue", alpha=0.55, edgecolor="0.3", linewidth=0.2)
axes[1].plot(*mesh_envelope.exterior.xy, color="0.35", lw=1.0, ls="--")
axes[1].plot(*active_domain.exterior.xy, color="black", lw=1.2)
axes[1].set_title("Larger envelope: active cells selected after meshing")
axes[1].set_axis_off()

fig.tight_layout()
plt.show()

#%%
# 4) Plot MODFLOW-facing connectivity diagnostics

fig, axes = plt.subplots(1, 2, figsize=(14, 6))

for ax, grid, connectivity, title in [
    (
        axes[0],
        baseline_quality,
        baseline_connectivity,
        "Baseline centroid connectivity",
    ),
    (
        axes[1],
        active_quality,
        active_connectivity,
        "Envelope-selected active-cell connectivity",
    ),
]:
    ax.set_aspect("equal")
    grid.plot(ax=ax, color="white", edgecolor="0.85", linewidth=0.2)
    connectivity.set_geometry("connector").plot(
        ax=ax,
        color="0.35",
        linewidth=0.25,
        alpha=0.25,
    )
    connectivity.plot(
        column="ortho_error",
        ax=ax,
        cmap="Reds",
        legend=True,
        linewidth=1.0,
        vmin=0,
        vmax=max(1.0, baseline_quality["ortho_error"].quantile(0.95)),
    )
    ax.plot(*active_domain.exterior.xy, color="black", lw=1.0)
    ax.set_title(title)
    ax.set_axis_off()

fig.tight_layout()
plt.show()

#%%
# 5) Compare quality distributions

fig, axes = plt.subplots(2, 2, figsize=(12, 8))
plot_metrics = [
    ("compactness", "Compactness"),
    ("drift_ratio", "Generator drift ratio"),
    ("ortho_error", "Centroid orthogonality error"),
    ("skewness", "Centroid skewness error"),
]

for ax, (metric, title) in zip(axes.ravel(), plot_metrics):
    ax.boxplot(
        [baseline_quality[metric].dropna(), active_quality[metric].dropna()],
        tick_labels=["clipped", "envelope active"],
        showfliers=False,
    )
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.25)

fig.tight_layout()
plt.show()

#%%
