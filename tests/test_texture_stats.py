"""Two-layer texture statistics for a zone.

Counts distinct (layer1, layer2) pairs and rotation usage across all TIL
files. Regression anchors: the addon creates one material per pair, so the
pair count drives material count in Blender.

Exit code 0 on success, 1 on failure.
"""
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import _paths

from rose.zon import Zon
from rose.til import Til

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    _paths.client_zone_dir(),
)

# JDT01 reference values (all rotation values 0-6, not just 1-4)
EXPECTED_PAIRS = 35
EXPECTED_ROTATIONS = {1: 2859, 2: 518, 3: 469, 4: 250}


def _find_zon():
    """JDT01.ZON by default, else the first .ZON in the zone dir."""
    cand = os.path.join(ZONE_DIR, "JDT01.ZON")
    if os.path.isfile(cand):
        return cand
    for name in sorted(os.listdir(ZONE_DIR)):
        if name.upper().endswith(".ZON"):
            return os.path.join(ZONE_DIR, name)
    raise FileNotFoundError(f"no .ZON file in {ZONE_DIR}")


def main():
    zon = Zon(_find_zon())
    pairs = {}
    rotations = {}
    total_patches = 0
    skipped_oob = 0

    for name in sorted(os.listdir(ZONE_DIR)):
        if not name.upper().endswith(".TIL"):
            continue
        til = Til(os.path.join(ZONE_DIR, name))
        for row in til.tiles:
            for p in row:
                if p.tile >= len(zon.tiles):
                    # Corrupt TIL previously hid here; count loudly instead.
                    skipped_oob += 1
                    continue
                t = zon.tiles[p.tile]
                l1 = t.layer1 + t.offset1
                l2 = t.layer2 + t.offset2
                total_patches += 1
                pairs[(l1, l2)] = pairs.get((l1, l2), 0) + 1
                rotations[t.rotation] = rotations.get(t.rotation, 0) + 1
    print(f"patches with out-of-range tile index: {skipped_oob}")
    assert skipped_oob == 0, f"{skipped_oob} patches reference missing ZON tiles"

    print(f"total patches: {total_patches}")
    print(f"distinct (l1, l2) pairs: {len(pairs)}")
    print(f"rotations: {sorted(rotations.items())}")
    same = sum(v for (l1, l2), v in pairs.items() if l1 == l2)
    print(f"patches where l1 == l2: {same}")

    print("pairs detail (top 10):")
    for (l1, l2), c in sorted(pairs.items(), key=lambda kv: -kv[1])[:10]:
        n1 = zon.textures[l1] if l1 < len(zon.textures) else "?"
        n2 = zon.textures[l2] if l2 < len(zon.textures) else "?"
        print(f"  l1={l1:2} ({n1}) l2={l2:2} ({n2}) x{c}")

    if os.path.basename(ZONE_DIR) == "JDT01":
        assert len(pairs) == EXPECTED_PAIRS, \
            f"expected {EXPECTED_PAIRS} pairs, got {len(pairs)}"
        assert rotations == EXPECTED_ROTATIONS, \
            f"rotation histogram differs: {sorted(rotations.items())} " \
            f"!= {sorted(EXPECTED_ROTATIONS.items())}"

    print("\nTEXTURE STATS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
