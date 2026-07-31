"""Headless Blender test: terrain UVs, per-pair materials, layer2 blending.

Verifies the 2026-08-01 texturing work:
- UVMap and UVMap_rot exist with patch-local coordinates,
- one material per (layer1, layer2) pair (35 for JDT01),
- two-layer materials have a MIX node fed by layer2 alpha + UVMap_rot,
- rotated patches produce rotated UVs in UVMap_rot,
- DDS textures load with an alpha channel (splat masks).

Run with the Blender executable (requires bpy):
  blender --background --factory-startup --python tests/test_blender_materials.py

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

EXPECTED_PAIR_MATERIALS = 35


def main():
    if not os.path.isfile(ZONE_FILE):
        print(f"zon file not found: {ZONE_FILE}")
        return 1

    try:
        res = bpy.ops.import_map.zon(
            filepath=ZONE_FILE,
            load_texture=True,
            load_cnst_objects=False,
            load_deco_objects=False,
        )
    except Exception as e:
        print(f"IMPORT FAILED: {e}")
        return 1
    print("RESULT:", res)

    terrain = bpy.data.objects.get("ROSE_Terrain")
    if not terrain:
        print("ROSE_Terrain missing")
        return 1
    mesh = terrain.data

    # UV layers
    uv_names = [l.name for l in mesh.uv_layers]
    print("uv layers:", uv_names)
    assert "UVMap" in uv_names and "UVMap_rot" in uv_names, "UV maps missing"

    # Per-pair materials
    mats = [m for m in mesh.materials if m and m.name.startswith("ROSE_Terrain_")]
    print("terrain materials:", len(mats))
    if os.path.basename(os.path.dirname(ZONE_FILE)) == "JDT01":
        assert len(mats) == EXPECTED_PAIR_MATERIALS, \
            f"expected {EXPECTED_PAIR_MATERIALS} pair materials, got {len(mats)}"

    # Two-layer materials: MIX node with layer2 alpha factor + UVMap_rot
    two_layer = 0
    for m in mats:
        ntypes = [n.type for n in m.node_tree.nodes]
        if "MIX" in ntypes:
            two_layer += 1
            mix = next(n for n in m.node_tree.nodes if n.type == "MIX")
            assert mix.data_type == "RGBA"
            # factor must be driven by the layer2 texture alpha
            for link in m.node_tree.links:
                if link.to_socket.name == "Factor":
                    assert link.from_socket.name == "Alpha", "mix factor not from texture alpha"
            # layer2 texture must read UVMap_rot
            attrs = [n for n in m.node_tree.nodes if n.type == "ATTRIBUTE"]
            assert any(a.attribute_name == "UVMap_rot" for a in attrs), \
                f"{m.name} lacks UVMap_rot attribute node"
    print(f"two-layer blend materials: {two_layer}")
    assert two_layer > 0, "no two-layer materials found"

    # UV sanity: face 0 is patch-local (0,0)-(0.25,0.25)
    uv = mesh.uv_layers["UVMap"].data
    uv2 = mesh.uv_layers["UVMap_rot"].data
    f0 = mesh.polygons[0]
    lo = f0.loop_start
    uvs = [tuple(uv[lo + i].uv) for i in range(4)]
    print("face0 UVs:", uvs)
    assert uvs[0] == (0.0, 0.0) and uvs[2] == (0.25, 0.25), "face0 UVs not patch-local"

    # At least one rotated patch must differ between UVMap and UVMap_rot
    rotated = 0
    for fi in range(0, len(mesh.polygons)):
        p = mesh.polygons[fi]
        u1 = tuple(uv[p.loop_start].uv)
        u2 = tuple(uv2[p.loop_start].uv)
        if abs(u1[0] - u2[0]) > 1e-6 or abs(u1[1] - u2[1]) > 1e-6:
            rotated += 1
    print(f"faces with rotated layer2 UVs: {rotated}")
    assert rotated > 0, "no rotated patches found (rotation handling broken)"

    # DDS images must have alpha (DXT3 splat masks)
    dds = [img for img in bpy.data.images if img.name.endswith(".dds")]
    print(f"dds images: {len(dds)}")
    assert dds, "no DDS textures loaded"
    assert all(img.channels == 4 for img in dds), "DDS textures lost their alpha channel"

    # sRGB mipmap bug fix: textures load as Non-Color with manual Gamma(2.2)
    # (Blender double-converts sRGB mip levels, turning minified dark texels
    # into black patches - see architecture/zone-terrain-transparency-issue.md)
    assert all(img.colorspace_settings.name == "Non-Color" for img in dds), \
        "terrain images must be Non-Color (sRGB mip darkening bug)"
    gamma_mats = 0
    for m in mats:
        if any(n.type == "GAMMA" for n in m.node_tree.nodes):
            gamma_mats += 1
    print(f"materials with Gamma(2.2): {gamma_mats}/{len(mats)}")
    assert gamma_mats == len(mats), "every terrain material needs a Gamma node"

    print("MATERIALS TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
