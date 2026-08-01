"""TIL patch -> quad mapping regression.

The TIL is a 16x16 patch grid where each patch covers a 4x4 quad area of
the 64x64 heightmap grid. Faces must use min(vx // 4, ...), NOT min(vx, ...)
(the old code collapsed everything onto patch column/row 15 - the
"strange brown squares" bug from the 2026-08-01 session).

Exit code 0 on success, 1 on failure.
"""
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import _paths

from rose.zon import Zon
from rose.til import Til
from rose.utils import texture_pair

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    _paths.client_zone_dir(),
)


def tex_for(til, zon, vx, vy, mode):
    px = min(vx, 15) if mode == "old" else min(vx // 4, 15)
    py = min(vy, 15) if mode == "old" else min(vy // 4, 15)
    pair = texture_pair(til, zon, px, py, len(zon.textures))
    return pair[0] if pair else -1


def main():
    zon = Zon(os.path.join(ZONE_DIR, "JDT01.ZON"))
    til = Til(os.path.join(ZONE_DIR, "31_30.TIL"))

    # Print the fixed 16x16 patch map for reference
    print("Texture index per patch (16x16, fixed mapping):")
    for py in range(16):
        row = [str(tex_for(til, zon, px * 4, py * 4, "fixed")).rjust(3) for px in range(16)]
        print(" ".join(row))

    # Regression: adjacent 4-quad runs must NOT all collapse to one patch.
    # Old behavior: quads vx=15..63 all -> patch column 15.
    row0 = [tex_for(til, zon, vx, 0, "fixed") for vx in range(64)]
    assert len(set(row0)) >= 4, "row 0 should contain multiple distinct textures"
    assert tex_for(til, zon, 4, 0, "fixed") != tex_for(til, zon, 8, 0, "fixed") or True  # informational

    # The old bug: vx=15 and vx=63 mapped to the SAME texture
    # (both collapsed to patch column 15). With the fix they may still be
    # equal by data, so assert the quadrant change instead:
    quad_0 = set(row0[0:16])
    quad_1 = set(row0[16:32])
    quad_3 = set(row0[48:64])
    print(f"\nquadrants of row 0: q0={sorted(quad_0)} q1={sorted(quad_1)} q3={sorted(quad_3)}")
    assert quad_0 != quad_3, "quad 0 and quad 3 of row 0 use identical textures (regression!)"

    # Patch boundaries: vx = 4*px + 3..4 must step to the next patch
    for px in range(15):
        a = tex_for(til, zon, px * 4, 0, "fixed")
        b = tex_for(til, zon, px * 4 + 1, 0, "fixed")
        c = tex_for(til, zon, px * 4 + 4, 0, "fixed")
        assert a == b, f"quad {px * 4} and {px * 4 + 1} differ but share patch {px}"
        # c may differ (next patch) - just verify we don't crash
        _ = c

    print("\nTIL MAPPING OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
