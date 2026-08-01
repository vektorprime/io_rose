"""Byte-exact round-trip tests for the zone file writers.

Load -> save -> load and compare:
- Every ZON/HIM/TIL/IFO file in the zone directory must round-trip
  BYTE-IDENTICAL (this is what makes the export operator safe: saving an
  unmodified block never changes it).
- Also verifies writer vs Rust map editor layout compatibility is
  exercised via the real files.

Exit code 0 on success, 1 on failure.
"""
import os
import sys
import tempfile

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

EXTENSIONS = (".ZON", ".HIM", ".TIL", ".IFO")


def roundtrip_bytes(parser_cls, path):
    obj = parser_cls(path)
    tmp = os.path.join(tempfile.mkdtemp(), os.path.basename(path))
    obj.save(tmp)
    with open(path, "rb") as a, open(tmp, "rb") as b:
        return a.read() == b.read()


def main():
    if not os.path.isdir(ZONE_DIR):
        print(f"zone dir not found: {ZONE_DIR}")
        print("set ROSE_TEST_ZONE to a zone directory")
        return 1

    fail = 0
    checked = 0

    for name in sorted(os.listdir(ZONE_DIR)):
        if not name.upper().endswith(EXTENSIONS):
            continue
        path = os.path.join(ZONE_DIR, name)
        parser = {"ZON": Zon, "HIM": Him, "TIL": Til, "IFO": Ifo}[name[-3:].upper()]
        try:
            ok = roundtrip_bytes(parser, path)
            checked += 1
            if not ok:
                print(f"  {name}: ROUND-TRIP DIFFERS")
                fail += 1
        except Exception as e:
            print(f"  {name}: FAIL: {e}")
            fail += 1

    if checked == 0:
        print("no zone files found")
        return 1

    if fail:
        print(f"\n{checked} files checked, {fail} FAILURES")
        return 1
    print(f"\nall ok: {checked} files round-trip byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
