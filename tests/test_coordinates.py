"""Terrain/object world-space alignment on real zone data.

Regression for the 2026-08-01 coordinate fix: terrain and IFO objects must
share one absolute world space (block corner = 160 * block_coord - 5200 m,
objects at (x, -y, z) / 100 with no extra offset).

Also dumps ZON positions / spawns / IFO ranges for debugging.

Exit code 0 on success, 1 on failure.
"""
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from rose.zon import Zon
from rose.ifo import Ifo

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    r"C:\Users\vicha\RustroverProjects\rose-offline-client\target\debug\3Ddata\MAPS\JUNON\JDT01",
)

GRID_SCALE = 2.5
BLOCK_SIZE = 64.0 * GRID_SCALE
WORLD_ORIGIN = -32.5 * BLOCK_SIZE


def terrain_bounds(tx, ty):
    x0 = tx * BLOCK_SIZE + WORLD_ORIGIN
    y0 = ty * BLOCK_SIZE + WORLD_ORIGIN
    return (x0, x0 + BLOCK_SIZE, y0, y0 + BLOCK_SIZE)


def main():
    if not os.path.isdir(ZONE_DIR):
        print(f"zone dir not found: {ZONE_DIR}")
        return 1

    zon = Zon(os.path.join(ZONE_DIR, "JDT01.ZON"))
    print(f"ZON: type={zon.zone_type} grid={zon.width}x{zon.length} grid_size={zon.grid_size}")

    # Sanity dump of the ZON position grid (map-editor coordinates)
    print("\nZON positions sample:")
    for y in (30, 31):
        for x in (31, 32):
            try:
                p = zon.positions[y][x]
                print(f"  [{y}][{x}]: used={p.is_used} pos=({p.position.x:.0f}, {p.position.y:.0f})")
            except Exception:
                pass

    # The core regression: every IFO object must fall within its own tile's
    # terrain bounds (with a small tolerance).
    fail = 0
    total = 0
    for name in sorted(os.listdir(ZONE_DIR)):
        if not name.upper().endswith(".IFO"):
            continue
        tx, ty = map(int, name.split(".")[0].split("_"))
        x0, x1, y0, y1 = terrain_bounds(tx, ty)
        ifo = Ifo(os.path.join(ZONE_DIR, name))
        for obj in ifo.cnst_objects + ifo.deco_objects:
            total += 1
            ox = obj.position.x / 100.0
            oy = -obj.position.y / 100.0
            in_x = x0 - 0.5 <= ox <= x1 + 0.5
            in_y = y0 - 0.5 <= oy <= y1 + 0.5
            if not (in_x and in_y):
                fail += 1
                print(f"{name} {obj.object_name}: obj=({ox:.2f},{oy:.2f}) "
                      f"tile=({tx},{ty}) bounds x[{x0:.1f},{x1:.1f}] y[{y0:.1f},{y1:.1f}] OFF!")

    print(f"\nchecked {total} objects, {fail} outside their tile's terrain bounds")
    if fail:
        return 1
    print("ALL OBJECTS ON THEIR TILE'S TERRAIN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
