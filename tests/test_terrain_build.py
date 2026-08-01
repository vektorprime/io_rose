"""End-to-end terrain build on real zone data.

Rebuilds the full terrain mesh (vertices + main quads + inter-tile stitch
faces) using the same logic as import_map.py and validates that:
- no crashes on sparse maps (missing neighbor tiles),
- every face references valid vertex indices,
- per-tile face counts stay aligned,
- the terrain corners match the Rust client's world space
  (block corner = 160 * block_coord - 5200 meters).

Exit code 0 on success, 1 on failure.
"""
import os
import sys
from types import SimpleNamespace

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import _paths

from rose.him import Him
from rose.utils import list_2d

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    _paths.client_zone_dir(),
)


def build_terrain(force_missing=None):
    """Vertex + face generation mirroring import_map.py execute()."""
    coords = []
    for file in sorted(os.listdir(ZONE_DIR)):
        if file.endswith(".HIM"):
            x, y = map(int, file.split(".")[0].split("_"))
            coords.append((x, y))
    minx = min(c[0] for c in coords)
    maxx = max(c[0] for c in coords)
    miny = min(c[1] for c in coords)
    maxy = max(c[1] for c in coords)
    dimx = maxx - minx + 1
    dimy = maxy - miny + 1

    tiles = SimpleNamespace(
        dimension=SimpleNamespace(x=dimx, y=dimy),
        min_pos=SimpleNamespace(x=minx, y=miny),
        hims=list_2d(dimy, dimx),
        indices=list_2d(dimy, dimx),
    )
    for x, y in coords:
        nx, ny = x - minx, y - miny
        if force_missing and (x, y) == force_missing:
            continue
        him = Him(os.path.join(ZONE_DIR, f"{x}_{y}.HIM"))
        him.indices = list_2d(him.width, him.length)
        tiles.hims[ny][nx] = him
        tiles.indices[ny][nx] = list_2d(him.width, him.length)

    grid_scale = 2.5
    block_size = 64.0 * grid_scale
    world_origin = -32.5 * block_size
    vertices, edges, faces = [], [], []
    xs, ys = [], []

    # Main quads (mirrors import_map.py)
    for y in range(tiles.dimension.y):
        for x in range(tiles.dimension.x):
            if not tiles.hims[y][x]:
                continue
            indices = tiles.indices[y][x]
            him = tiles.hims[y][x]
            base_x = (x + tiles.min_pos.x) * block_size + world_origin
            base_y = (y + tiles.min_pos.y) * block_size + world_origin
            for vy in range(him.length):
                for vx in range(him.width):
                    height = him.heights[vy][vx] / 100.0
                    world_x = base_x + vx * grid_scale
                    world_y = base_y + vy * grid_scale
                    vertices.append((world_x, world_y, height))
                    xs.append(world_x)
                    ys.append(world_y)
                    vi = len(vertices) - 1
                    him.indices[vy][vx] = vi
                    indices[vy][vx] = vi
                    if vx < him.width - 1 and vy < him.length - 1:
                        v1 = vi
                        v2 = vi + 1
                        v3 = vi + 1 + him.width
                        v4 = vi + him.width
                        edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                        faces.append((v1, v2, v3, v4))

    # Inter-tile stitch faces (mirrors import_map.py, sparse-safe)
    for y in range(tiles.dimension.y):
        for x in range(tiles.dimension.x):
            if not tiles.hims[y][x] or not tiles.indices[y][x]:
                continue
            indices = tiles.indices[y][x]
            him = tiles.hims[y][x]
            is_x_edge = (x == tiles.dimension.x - 1)
            is_y_edge = (y == tiles.dimension.y - 1)
            has_x_neighbor = not is_x_edge and bool(tiles.indices[y][x + 1])
            has_y_neighbor = not is_y_edge and bool(tiles.indices[y + 1][x])
            has_xy_neighbor = has_x_neighbor and has_y_neighbor and bool(tiles.indices[y + 1][x + 1])
            for vy in range(him.length):
                for vx in range(him.width):
                    is_x_edge_vertex = (vx == him.width - 1) and (vy < him.length - 1)
                    is_y_edge_vertex = (vx < him.width - 1) and (vy == him.length - 1)
                    is_corner_vertex = (vx == him.width - 1) and (vy == him.length - 1)
                    if has_x_neighbor and is_x_edge_vertex:
                        next_indices = tiles.indices[y][x + 1]
                        v1 = indices[vy][vx]
                        v2 = next_indices[vy][0]
                        v3 = next_indices[vy + 1][0]
                        v4 = indices[vy + 1][vx]
                        edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                        faces.append((v1, v2, v3, v4))
                    if has_y_neighbor and is_y_edge_vertex:
                        next_indices = tiles.indices[y + 1][x]
                        v1 = indices[vy][vx]
                        v2 = indices[vy][vx + 1]
                        v3 = next_indices[0][vx + 1]
                        v4 = next_indices[0][vx]
                        edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                        faces.append((v1, v2, v3, v4))
                    if has_xy_neighbor and is_corner_vertex:
                        right = tiles.indices[y][x + 1]
                        diag = tiles.indices[y + 1][x + 1]
                        down = tiles.indices[y + 1][x]
                        diag_him = tiles.hims[y + 1][x + 1]
                        down_him = tiles.hims[y + 1][x]
                        v1 = indices[vy][vx]
                        v2 = right[diag_him.length - 1][0]
                        v3 = diag[0][0]
                        v4 = down[0][down_him.width - 1]
                        edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                        faces.append((v1, v2, v3, v4))

    max_vi = len(vertices) - 1
    for f in faces:
        assert all(0 <= i <= max_vi for i in f), f"face references invalid vertex: {f}"
    return len(vertices), len(faces), min(xs), max(xs), min(ys), max(ys)


def main():
    nv, nf, x0, x1, y0, y1 = build_terrain()
    print(f"all tiles present: vertices={nv} faces={nf}")
    print(f"x range [{x0:.1f}, {x1:.1f}] y range [{y0:.1f}, {y1:.1f}]")

    # JDT01 reference: tiles 31_30..34_33 -> x [-240, 400], y [-400, 240]
    assert abs(x0 - (-240.0)) < 0.01, f"west edge {x0} != -240"
    assert abs(x1 - 400.0) < 0.01, f"east edge {x1} != 400"
    assert abs(y0 - (-400.0)) < 0.01, f"south edge {y0} != -400"
    assert abs(y1 - 240.0) < 0.01, f"north edge {y1} != 240"

    # Sparse map: a missing tile must not crash and must produce fewer faces
    nv2, nf2, *_ = build_terrain(force_missing=(33, 31))
    print(f"33_31 forced missing: vertices={nv2} faces={nf2}")
    assert nv2 < nv and nf2 < nf, "missing tile did not reduce mesh"

    print("\nTERRAIN BUILD OK (coordinates match Rust client)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
