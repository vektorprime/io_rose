"""Headless Blender smoke test: run the full .zon import operator.

Run with the Blender executable (requires bpy):
  blender --background --factory-startup --python tests/test_blender_import.py

Exit code 0 on success, 1 on failure.
"""
import bpy
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ADDON_ROOT))

import io_rose
io_rose.register()

ZONE_FILE = os.path.join(
    os.environ.get(
        "ROSE_TEST_ZONE",
        r"C:\Users\vicha\RustroverProjects\rose-offline-client\target\debug\3Ddata\MAPS\JUNON\JDT01",
    ),
    "JDT01.ZON",
)


def main():
    if not os.path.isfile(ZONE_FILE):
        print(f"zon file not found: {ZONE_FILE}")
        return 1

    try:
        res = bpy.ops.import_map.zon(
            filepath=ZONE_FILE,
            load_texture=True,
            load_cnst_objects=True,
            load_deco_objects=True,
        )
    except Exception as e:
        print(f"IMPORT FAILED: {e}")
        return 1

    print("RESULT:", res)
    if res != {"FINISHED"}:
        print("import did not finish")
        return 1

    terrain = bpy.data.objects.get("ROSE_Terrain")
    if not terrain:
        print("ROSE_Terrain object missing")
        return 1

    meshes = len(bpy.data.meshes)
    objects = len(bpy.data.objects)
    faces = len(terrain.data.polygons)
    print(f"meshes: {meshes} objects: {objects} terrain faces: {faces}")

    assert meshes > 100, "expected ZSC part meshes (CNST/DECO)"
    assert objects > 1000, "expected spawned IFO objects"
    assert faces > 60000, "expected ~67081 terrain faces"
    assert len(terrain.data.uv_layers) >= 2, "expected UVMap + UVMap_rot"
    assert len(terrain.data.materials) > 10, "expected per-pair terrain materials"

    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
