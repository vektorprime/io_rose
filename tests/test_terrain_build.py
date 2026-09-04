"""End-to-end terrain build on real zone data.

Rebuilds the full terrain mesh (vertices + main quads, no inter-tile stitch
faces - tiles abut exactly and the client spawns separate blocks) using the
same logic as import_map.py and validates that:
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
    him_files = {}
    for file in sorted(os.listdir(ZONE_DIR)):
        # Case-insensitive: data files use uppercase .HIM
        if file.upper().endswith(".HIM"):
            x, y = map(int, file.split(".")[0].split("_"))
            coords.append((x, y))
            him_files[(x, y)] = file
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
        him = Him(os.path.join(ZONE_DIR, him_files[(x, y)]))
        # Row-major [length rows][width cols] (heights[vy][vx] below).
        him.indices = list_2d(him.length, him.width)
        tiles.hims[ny][nx] = him
        tiles.indices[ny][nx] = list_2d(him.length, him.width)

    # Per-quad scale from the data (patch_scale cm per 100 quads), not a
    # hardcoded 2.5; the world origin is the fixed -5200 m ROSE offset.
    first_him = next(h for h in
                     (tiles.hims[ny][nx] for ny in range(dimy) for nx in range(dimx))
                     if h is not None)
    grid_scale = (first_him.patch_scale / 100.0) if first_him.patch_scale else 2.5
    block_size = 64.0 * grid_scale
    world_origin = -5200.0
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

    # No inter-tile stitch faces (mirrors import_map.py): tiles abut
    # exactly in world space, so stitched quads would be degenerate.

    max_vi = len(vertices) - 1
    for f in faces:
        assert all(0 <= i <= max_vi for i in f), f"face references invalid vertex: {f}"
    return len(vertices), len(faces), min(xs), max(xs), min(ys), max(ys)


def main():
    nv, nf, x0, x1, y0, y1 = build_terrain()
    print(f"all tiles present: vertices={nv} faces={nf}")
    print(f"x range [{x0:.1f}, {x1:.1f}] y range [{y0:.1f}, {y1:.1f}]")

    # JDT01 reference: 16 tiles x 65x65 verts, 16 x 64x64 quads;
    # tiles 31_30..34_33 -> x [-240, 400], y [-400, 240]
    assert nv == 16 * 65 * 65, f"expected {16 * 65 * 65} vertices, got {nv}"
    assert nf == 16 * 64 * 64, f"expected {16 * 64 * 64} faces, got {nf}"
    assert abs(x0 - (-240.0)) < 0.01, f"west edge {x0} != -240"
    assert abs(x1 - 400.0) < 0.01, f"east edge {x1} != 400"
    assert abs(y0 - (-400.0)) < 0.01, f"south edge {y0} != -400"
    assert abs(y1 - 240.0) < 0.01, f"north edge {y1} != 240"

    # Sparse map: a missing tile must not crash and must drop exactly one
    # tile of geometry (stitch faces used to make this fuzzy).
    nv2, nf2, *_ = build_terrain(force_missing=(33, 31))
    print(f"33_31 forced missing: vertices={nv2} faces={nf2}")
    assert nv2 == nv - 65 * 65, f"expected {nv - 65 * 65} vertices, got {nv2}"
    assert nf2 == nf - 64 * 64, f"expected {nf - 64 * 64} faces, got {nf2}"

    print("\nTERRAIN BUILD OK (coordinates match Rust client)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
