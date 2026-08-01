"""Unit tests for the pure terrain helpers in rose/utils.py.

- apply_uv_rotation: all six ZON rotations (shader match).
- patch_rotation / texture_pair: TIL patch lookups.

Exit code 0 on success, 1 on failure.
"""
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import _paths

from rose.utils import apply_uv_rotation

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    _paths.client_zone_dir(),
)


def test_uv_rotation():
    cases = [
        # rotation, input, expected (matches terrain_material.wgsl apply_rotation)
        (1, (0.25, 0.75), (0.25, 0.75)),   # None
        (2, (0.25, 0.75), (0.75, 0.75)),   # FlipHorizontal: u' = 1-u
        (3, (0.25, 0.75), (0.25, 0.25)),   # FlipVertical: v' = 1-v
        (4, (0.25, 0.75), (0.75, 0.25)),   # Flip
        (5, (0.25, 0.75), (0.75, 0.75)),   # CW90: u'=v, v'=1-u
        (6, (0.25, 0.75), (0.25, 0.25)),   # CCW90: u'=1-v, v'=u
        (0, (0.25, 0.75), (0.25, 0.75)),   # Unknown -> unchanged
        (99, (0.25, 0.75), (0.25, 0.75)),  # garbage -> unchanged
    ]
    for rot, (u, v), expected in cases:
        got = apply_uv_rotation(u, v, rot)
        assert got == expected, f"rotation {rot}: expected {expected}, got {got}"

    # Double-flip round trips (floating point tolerant)
    def approx(a, b, eps=1e-9):
        return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps

    assert approx(apply_uv_rotation(*apply_uv_rotation(0.2, 0.7, 2), 2), (0.2, 0.7))
    assert approx(apply_uv_rotation(*apply_uv_rotation(0.2, 0.7, 3), 3), (0.2, 0.7))
    assert approx(apply_uv_rotation(*apply_uv_rotation(0.2, 0.7, 4), 4), (0.2, 0.7))
    # CW90 x4 = identity
    u, v = 0.2, 0.7
    for _ in range(4):
        u, v = apply_uv_rotation(u, v, 5)
    assert approx((u, v), (0.2, 0.7))
    print("apply_uv_rotation: all cases pass")


def test_patch_helpers():
    from rose.zon import Zon
    from rose.til import Til
    from rose.utils import patch_rotation, texture_pair

    zon = Zon(os.path.join(ZONE_DIR, "JDT01.ZON"))
    til = Til(os.path.join(ZONE_DIR, "31_30.TIL"))
    n_tex = len(zon.textures)

    # Missing TIL -> safe defaults
    assert patch_rotation(None, zon, 0, 0) == 1
    assert texture_pair(None, zon, 0, 0, n_tex) is None

    # Known JDT01 31_30 patch map values (from the 2026-08-01 analysis)
    assert texture_pair(til, zon, 0, 0, n_tex) == (0, 17)
    assert texture_pair(til, zon, 4, 0, n_tex) == (0, 16)
    assert texture_pair(til, zon, 1, 0, n_tex) == (16, 16)
    assert texture_pair(til, zon, 60, 60, n_tex) == (9, 9)

    # Patch clamping: out-of-range patch indices clamp to the edge
    assert texture_pair(til, zon, 100, 100, n_tex) == texture_pair(til, zon, 15, 15, n_tex)

    # Every patch returns a canonical pair within range
    for py in range(16):
        for px in range(16):
            pair = texture_pair(til, zon, px, py, n_tex)
            assert pair is not None, f"patch ({px},{py}) returned None"
            l1, l2 = pair
            assert l1 < n_tex and l2 < n_tex, f"patch ({px},{py}) out of range: {pair}"

    print(f"patch_rotation / texture_pair: all patches of 31_30.TIL ok "
          f"({16 * 16} patches, {n_tex} textures)")
    return zon, til


def main():
    test_uv_rotation()
    test_patch_helpers()
    print("ALL HELPER TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
