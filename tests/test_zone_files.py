"""Parse every ZON/HIM/TIL/IFO file in a zone directory.

Regression tests:
- All files parse without exceptions.
- The ZON texture list is trimmed at the "end" sentinel (Rust client
  behavior, spawning.rs) - no "end" entry may remain.

Exit code 0 on success, 1 on failure.
"""
import os
import sys
import traceback

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

from rose.zon import Zon
from rose.him import Him
from rose.til import Til
from rose.ifo import Ifo

ZONE_DIR = os.environ.get(
    "ROSE_TEST_ZONE",
    r"C:\Users\vicha\RustroverProjects\rose-offline-client\target\debug\3Ddata\MAPS\JUNON\JDT01",
)

ZON_FILE = os.path.join(ZONE_DIR, "JDT01.ZON")

# JDT01 reference values (update if testing a different zone)
EXPECTED_TEXTURES = 29
EXPECTED_LAST_TEXTURE = "3DData\\Terrain\\Tiles\\Junon\\JD\\S001_09.dds"


def main():
    if not os.path.isdir(ZONE_DIR):
        print(f"zone dir not found: {ZONE_DIR}")
        print("set ROSE_TEST_ZONE to a zone directory")
        return 1

    fail = 0

    # ZON + "end" sentinel
    try:
        zon = Zon(ZON_FILE)
        print(f"ZON: type={zon.zone_type} grid={zon.width}x{zon.length} "
              f"grid_size={zon.grid_size} tiles={len(zon.tiles)} textures={len(zon.textures)}")
        assert "end" not in zon.textures, "texture list still contains 'end' sentinel"
        if os.path.basename(ZONE_DIR) == "JDT01":
            assert len(zon.textures) == EXPECTED_TEXTURES, \
                f"expected {EXPECTED_TEXTURES} textures, got {len(zon.textures)}"
            assert zon.textures[-1] == EXPECTED_LAST_TEXTURE, \
                f"unexpected last texture: {zon.textures[-1]!r}"
            assert zon.grid_size == 250.0, f"unexpected grid_size {zon.grid_size}"
        print(f"  textures ok ({len(zon.textures)}), sentinel trimmed")
    except Exception as e:
        print(f"ZON FAIL: {e}")
        traceback.print_exc()
        fail += 1

    # HIM/TIL/IFO per tile
    parsed = 0
    for name in sorted(os.listdir(ZONE_DIR)):
        if not name.upper().endswith(".HIM"):
            continue
        base = name[:-4]
        try:
            him = Him(os.path.join(ZONE_DIR, name))
            til = Til(os.path.join(ZONE_DIR, base + ".TIL"))
            ifo = Ifo(os.path.join(ZONE_DIR, base + ".IFO"))
            parsed += 1
            print(f"  {name}: HIM {him.width}x{him.length} maxh={him.max_height:.1f}, "
                  f"TIL {len(til.tiles)}x{len(til.tiles[0])}, "
                  f"IFO cnst={len(ifo.cnst_objects)} deco={len(ifo.deco_objects)}")
        except Exception as e:
            print(f"  {name}: FAIL: {e}")
            traceback.print_exc()
            fail += 1

    if parsed == 0:
        print("no HIM files found")
        fail += 1

    if fail:
        print(f"\n{parsed} tiles parsed, {fail} FAILURES")
        return 1
    print(f"\nall ok: {parsed} tiles parsed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
