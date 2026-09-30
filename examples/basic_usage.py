"""Minimal end-to-end vorflow example used by release smoke tests."""

from shapely.geometry import LineString, Point, box

from vorflow import ConceptualMesh, MeshGenerator, VoronoiTessellator


def main():
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
    return grid_gdf


if __name__ == "__main__":
    grid = main()
    print(f"Generated {len(grid)} Voronoi cells")
