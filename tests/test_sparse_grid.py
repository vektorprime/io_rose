"""Face/material count alignment on sparse tile grids.

The inter-tile stitching loop and the material-index loop in import_map.py /
import_terrain.py must count faces identically, otherwise material slots
drift. This mirrors the fix from the 2026-07-31 session (sparse-tile crash).

Exit code 0 on success, 1 on failure.
"""
import os
import sys
from types import SimpleNamespace

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from rose.utils import list_2d


def build(tiles, dim_x, dim_y, missing=()):
    """65x65 tile grid with the given blocks missing."""
    hims = list_2d(dim_y, dim_x)
    inds = list_2d(dim_y, dim_x)
    for y in range(dim_y):
        for x in range(dim_x):
            if (x, y) in missing:
                continue
            him = SimpleNamespace(width=65, length=65)
            hims[y][x] = him
            inds[y][x] = list_2d(65, 65, None)
    tiles.hims = hims
    tiles.indices = inds


def count_faces(tiles):
    """Mirrors import_map.py's vertex + stitching face counting."""
    total = 0
    per_tile = {}
    for y in range(tiles.dimension.y):
        for x in range(tiles.dimension.x):
            if not tiles.hims[y][x] or not tiles.indices[y][x]:
                continue
            him = tiles.hims[y][x]
            is_x_edge = (x == tiles.dimension.x - 1)
            is_y_edge = (y == tiles.dimension.y - 1)
            has_x_neighbor = not is_x_edge and bool(tiles.indices[y][x + 1])
            has_y_neighbor = not is_y_edge and bool(tiles.indices[y + 1][x])
            has_xy_neighbor = has_x_neighbor and has_y_neighbor and bool(tiles.indices[y + 1][x + 1])
            n = (him.length - 1) * (him.width - 1)
            if has_x_neighbor:
                n += him.length - 1
            if has_y_neighbor:
                n += him.width - 1
            if has_xy_neighbor:
                n += 1
            per_tile[(x, y)] = n
            total += n
    return total, per_tile


def count_materials(tiles):
    """Mirrors import_map.py's material-index face counting."""
    total = 0
    per_tile = {}
    for ty in range(int(tiles.dimension.y)):
        for tx in range(int(tiles.dimension.x)):
            if not tiles.hims[ty][tx]:
                continue
            him = tiles.hims[ty][tx]
            is_x_edge = (tx == tiles.dimension.x - 1)
            is_y_edge = (ty == tiles.dimension.y - 1)
            has_x_neighbor = not is_x_edge and bool(tiles.indices[ty][tx + 1])
            has_y_neighbor = not is_y_edge and bool(tiles.indices[ty + 1][tx])
            has_xy_neighbor = has_x_neighbor and has_y_neighbor and bool(tiles.indices[ty + 1][tx + 1])
            n = (him.length - 1) * (him.width - 1)
            if has_x_neighbor:
                n += him.length - 1
            if has_y_neighbor:
                n += him.width - 1
            if has_xy_neighbor:
                n += 1
            per_tile[(tx, ty)] = n
            total += n
    return total, per_tile


def main():
    cases = [
        ("all present (JDT01 4x4)", 4, 4, ()),
        ("missing top-right", 4, 4, ((3, 0),)),
        ("missing row", 4, 4, ((0, 2), (1, 2), (2, 2), (3, 2))),
        ("missing column", 4, 4, ((2, 0), (2, 1), (2, 2), (2, 3))),
        ("missing corner cluster", 4, 4, ((3, 3), (2, 3), (3, 2))),
        ("single tile", 1, 1, ()),
        ("2x2 with one missing", 2, 2, ((1, 1),)),
        ("empty", 4, 4, tuple((x, y) for x in range(4) for y in range(4))),
        ("center missing only", 4, 4, ((1, 1),)),
        ("random sparse", 8, 8, ((2, 5), (3, 5), (4, 5), (6, 1), (6, 2), (7, 0), (0, 7))),
    ]

    ok = True
    for name, dx, dy, missing in cases:
        tiles = SimpleNamespace(dimension=SimpleNamespace(x=dx, y=dy))
        build(tiles, dx, dy, missing)
        f_total, f_per = count_faces(tiles)
        m_total, m_per = count_materials(tiles)
        status = "OK" if (f_total == m_total and f_per == m_per) else "MISMATCH"
        if status != "OK":
            ok = False
            diff = set(f_per.items()) ^ set(m_per.items())
            print(f"{name}: faces={f_total} materials={m_total} {status} diff={diff}")
        else:
            print(f"{name}: faces={f_total} materials={m_total} {status}")

    if not ok:
        print("\nALIGNMENT FAILURES")
        return 1
    print("\nALL ALIGNED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
