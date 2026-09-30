"""Render the README banner (docs/images/vorflow-banner-network.png).

A logo-style image with no text. The rounded tile *is* the model domain,
meshed by vorflow, and the grid is drawn as thin cell outlines so refinement
reads as denser lines. On top, in one accent colour:

- a stream network: a meandering main stem plus tributaries that end exactly
  on it,
- wells as refinement points,
- a circular zone refined inside and graded away from its outline,
- a hole cut through part of that zone.

Every polygon vertex becomes a mesh node and each segment is split into a
whole number of edges, so each outline and line is resampled at slightly under
its own cell size (VERTEX_SPACING); a vertex spacing that does not match the
local target leaves a band of finer cells along the outline. The tile has its
own background, so the image works on light and dark pages.

    PYTHONPATH=src python docs/make_splash.py
"""

from io import BytesIO
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from PIL import Image
from shapely.geometry import LineString, Point, Polygon, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator, set_verbosity

OUT_PNG = Path(__file__).resolve().parent / "images" / "vorflow-banner-network.png"
BACKGROUND = "#0c1230"
GRID_COLOR = "#2f5fd0"
FEATURE_COLOR = "#8ee8f4"
WELL_COLOR = "#effdff"
# Cell-to-cell size growth away from every feature (vorflow default 1.2).
GROWTH_FACTOR = 1.1
# Gmsh smoothing passes and optimisation cycles (MeshGenerator defaults 10, 2).
SMOOTHING_STEPS = 40
OPTIMIZATION_CYCLES = 4
# Vertex spacing as a fraction of the local cell size. Slightly under 1 keeps
# each segment a single mesh edge; at exactly 1, floating-point noise in the
# size field can split every segment in two and double the node density.
VERTEX_SPACING = 0.9

# Tile size (m), background lc, main stem (x range and
# y(x) = y0 + slope * x + amp * sin(x / wavelength)), tributaries as (source
# point, x on the main stem where they join), stream sizes, wells ((x, y), lc),
# a circular zone (centre, radius, interior lc), a hole (centre, radius) cut
# from the domain and the circle, and output size in pixels.
BANNER = {
    "size": (2000, 1000),
    "background_lc": 110.0,
    "main": {"x": (-60, 2060), "y0": 600, "slope": -0.12, "amp": 60, "wavelength": 140},
    "tributaries": [((500, 1060), 650), ((1250, 1060), 1400), ((1760, -60), 1820), ((-60, 150), 300)],
    "main_lc": 8.0,
    "tributary_lc": 10.0,
    "wells": [((520, 330), 3.0), ((930, 800), 3.0), ((1640, 720), 3.0)],
    "circle": ((1100, 240), 150, 10.0),
    "hole": ((1230, 140), 85),
    "pixels": (2400, 1200),
}


def resample(poly: Polygon, spacing: float) -> Polygon:
    """Polygon with its exterior re-drawn through evenly spaced vertices."""
    ring = poly.exterior
    n = max(8, int(round(ring.length / spacing)))
    return Polygon([ring.interpolate(d).coords[0] for d in np.linspace(0, ring.length, n, endpoint=False)])


def rounded_rect(width: float, height: float, radius: float, spacing: float) -> Polygon:
    """Rectangle with circular corners, lower-left corner at the origin."""
    smooth = box(radius, radius, width - radius, height - radius).buffer(radius, quad_segs=64)
    return resample(smooth, spacing)


def disc(centre, radius: float, spacing: float) -> Polygon:
    """Circle with vertices spaced to match the local mesh size."""
    return resample(Point(centre).buffer(radius, quad_segs=64), spacing)


def resample_line(line: LineString, spacing: float) -> LineString:
    """Line re-drawn through evenly spaced vertices."""
    stations = np.linspace(0, line.length, max(2, int(round(line.length / spacing)) + 1))
    return LineString([line.interpolate(d).coords[0] for d in stations])


def bezier(points, spacing: float, n: int = 2000) -> LineString:
    """Cubic Bezier curve through four control points, with vertices every ``spacing``."""
    p = np.asarray(points, dtype=float)
    t = np.linspace(0, 1, n)[:, None]
    xy = (1 - t) ** 3 * p[0] + 3 * (1 - t) ** 2 * t * p[1] + 3 * (1 - t) * t**2 * p[2] + t**3 * p[3]
    return resample_line(LineString(xy), spacing)


def main_y(main: dict, x: float) -> float:
    """Main-stem y at x (before resampling), used to place tributary junctions."""
    return main["y0"] + main["slope"] * x + main["amp"] * np.sin(x / main["wavelength"])


def meander_line(x, y0: float, slope: float, amp: float, wavelength: float, spacing: float) -> LineString:
    """Meandering polyline over the x range, y = y0 + slope*x + amp*sin(x/wavelength)."""
    xs = np.linspace(*x, 4000)
    curve = LineString(np.c_[xs, y0 + slope * xs + amp * np.sin(xs / wavelength)])
    return resample_line(curve, spacing)


def tributary(source, junction: Point, spacing: float, bend: float = 0.25) -> LineString:
    """Gently curved line from ``source`` that ends exactly on ``junction``."""
    p0, p3 = np.asarray(source, dtype=float), np.array([junction.x, junction.y])
    normal = np.array([-(p3 - p0)[1], (p3 - p0)[0]]) * bend
    ctrl = [p0, p0 + (p3 - p0) / 3 + normal, p0 + 2 * (p3 - p0) / 3 - normal, p3]
    return bezier(ctrl, spacing=spacing)


def build_blueprint(cfg: dict, growth_factor: float = GROWTH_FACTOR):
    """Rounded tile with a stream network, wells, a refined circular zone and a hole."""
    width, height = cfg["size"]
    lc = cfg["background_lc"]
    tile = rounded_rect(width, height, radius=0.2 * min(width, height), spacing=0.5 * lc)
    main = meander_line(spacing=VERTEX_SPACING * cfg["main_lc"], **cfg["main"])
    tributaries = [
        tributary(source, main.interpolate(main.project(Point(x_join, main_y(cfg["main"], x_join)))),
                  spacing=VERTEX_SPACING * cfg["tributary_lc"])
        for source, x_join in cfg["tributaries"]
    ]
    centre, radius, circle_lc = cfg["circle"]
    circle = disc(centre, radius, spacing=VERTEX_SPACING * circle_lc)
    hole_centre, hole_radius = cfg["hole"]
    hole = disc(hole_centre, hole_radius, spacing=VERTEX_SPACING * circle_lc)
    domain = Polygon(tile.exterior.coords, [hole.exterior.coords])
    circle_zone = circle.difference(hole)

    bp = ConceptualMesh()
    bp.add_polygon(domain, zone_id="domain", resolution=lc)
    bp.add_polygon(circle_zone, zone_id="circle", resolution=circle_lc, z_order=1, growth_factor=growth_factor)
    # Field-only rim keeps the hole edge at the circle's size where it runs
    # outside the circle, so the rim vertices grade out instead of pinning a
    # ring of fine cells.
    bp.add_line(hole.exterior, line_id="hole-rim", resolution=circle_lc, embed=False, growth_factor=growth_factor)
    bp.add_line(main, line_id="main", resolution=cfg["main_lc"], growth_factor=growth_factor)
    for i, trib in enumerate(tributaries):
        bp.add_line(trib, line_id=f"tributary-{i}", resolution=cfg["tributary_lc"], growth_factor=growth_factor)
    for i, (xy, well_lc) in enumerate(cfg["wells"]):
        bp.add_point(Point(xy), point_id=f"well-{i}", resolution=well_lc, growth_factor=growth_factor)
    lines = ([(main, 1.5)] + [(trib, 1.0) for trib in tributaries]
             + [(circle_zone.exterior, 1.0), (hole.exterior, 1.0)])
    features = {"lines": lines, "points": [xy for xy, _ in cfg["wells"]]}
    return bp, tile, features


def build_grid(bp: ConceptualMesh, background_lc: float, smoothing_steps: int = SMOOTHING_STEPS,
               optimization_cycles: int = OPTIMIZATION_CYCLES):
    """Run the vorflow pipeline and return the Voronoi grid."""
    polys, lines, points = bp.generate()
    mesher = MeshGenerator(
        background_lc=background_lc,
        verbosity=0,
        smoothing_steps=smoothing_steps,
        optimization_cycles=optimization_cycles,
    )
    mesher.generate(polys, lines, points)
    return VoronoiTessellator(mesher, bp, clip_to_boundary=True).generate()


def polygon_patch(poly: Polygon, **kwargs) -> PathPatch:
    """Matplotlib patch for a shapely polygon's exterior."""
    return PathPatch(MplPath(np.asarray(poly.exterior.coords)[:, :2]), **kwargs)


def render(grid, tile: Polygon, features: dict, out_png: Path, pixels,
           background: str = BACKGROUND, grid_color: str = GRID_COLOR,
           feature_color: str = FEATURE_COLOR, well_color: str = WELL_COLOR, dpi: int = 200) -> None:
    """Draw cell outlines on the tile, with feature lines (geometry, width) and wells on top."""
    edges = [np.asarray(ring.coords)[:, :2] for ring in grid.geometry.exterior]
    fig = plt.figure(figsize=(pixels[0] / dpi, pixels[1] / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.add_patch(polygon_patch(tile, facecolor=background, edgecolor="none"))
    ax.add_collection(LineCollection(edges, colors=grid_color, linewidths=0.35, alpha=0.9))
    for line, width in features["lines"]:
        clipped = line.intersection(tile)
        for part in getattr(clipped, "geoms", [clipped]):
            if not part.is_empty:
                ax.plot(*part.xy, color=feature_color, lw=width, solid_capstyle="round", zorder=3)
    wx, wy = zip(*features["points"])
    ax.scatter(wx, wy, s=30, color=well_color, edgecolors=background, linewidths=1.2, zorder=4)
    xmin, ymin, xmax, ymax = tile.bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_axis_off()
    save_quantized(fig, out_png, dpi)


def save_quantized(fig, out_png: Path, dpi: int) -> None:
    """Save a figure as a 256-colour PNG with a transparent background."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    buffer = BytesIO()
    fig.savefig(buffer, dpi=dpi, transparent=True)
    plt.close(fig)
    # A 256-colour palette holds the few flat colours plus anti-aliasing and
    # cuts the file size several-fold.
    Image.open(buffer).quantize(colors=256, method=Image.Quantize.FASTOCTREE).save(out_png, optimize=True)


def main(out_png: Path = OUT_PNG, cfg: dict = BANNER) -> None:
    set_verbosity(0)
    bp, tile, features = build_blueprint(cfg)
    grid = build_grid(bp, cfg["background_lc"])
    render(grid, tile, features, out_png, cfg["pixels"])
    print(f"{len(grid)} cells -> {out_png}")


if __name__ == "__main__":
    main()
