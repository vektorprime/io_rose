"""Headless Blender test for ZMS export: stale strips/materials, normals,
bounding boxes and skin weights.

Run with the Blender executable (requires bpy):
  blender --background --factory-startup --python tests/test_blender_zms_export.py

- Imports are smooth shaded, stash the exact file normals, blend weights
  and bone slots, and record the imported topology, bounding box and
  position CRC; degenerate triangles are skipped instead of crashing Blender.
- Unedited import -> export is byte-identical through both importers,
  including normals Blender cannot represent, non-tight bounding boxes,
  unsorted / unnormalized weights and zero-weight slots, and for a
  deterministic sample of client files the exporter can reproduce.
  import_zms_zmd without a skeleton alongside makes the same zms_bone_{i}
  groups and weights as import_zms instead of dropping the skin.
- The recorded box and skin stash only apply while they still describe the
  mesh: a moved vertex, world transform or topology edit recomputes the
  box (in file units: v5/v6 * 100, like the positions), and edited vertex
  weights (or any topology edit) export exactly as without the stash
  (sorted, renormalized, padded with bone 0).
- Edited topology (subdivide, delete, flipped face) and objects without
  recorded import counts export empty strips / material face counts with
  an INFO report instead of the stale imported lists.
- Exported normals equal the custom (corner) normals, averaged per exported
  vertex: a synthetic sphere, a mesh split into two pieces along a seam,
  edited normals on unedited topology, and world-space + mirrored export.

Exit code 0 on success, 1 on failure.
"""
import bmesh
import bpy
import glob
import importlib.util
import math
import mathutils
import os
import shutil
import struct
import sys
import tempfile
import traceback

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths  # noqa: E402

# Load this checkout as package "io_rose" even when its directory has
# another name (git worktree): "import io_rose" would silently test the
# installed copy in addons_core instead.
_spec = importlib.util.spec_from_file_location(
    "io_rose", os.path.join(ADDON_ROOT, "__init__.py"),
    submodule_search_locations=[ADDON_ROOT])
io_rose = importlib.util.module_from_spec(_spec)
sys.modules["io_rose"] = io_rose
_spec.loader.exec_module(io_rose)
io_rose.register()

from io_rose.export_zms import ExportZMS, export_zms_mesh_object  # noqa: E402
from io_rose.rose.zms import (FILE_BONE_SLOT_ATTRIBUTE, FILE_BONE_WEIGHT_ATTRIBUTE,  # noqa: E402
                              FILE_NORMAL_ATTRIBUTE, ZMS, index_checksum,
                              position_checksum)

ROOT = _paths.client_3ddata_root()
# v7, skinned (1 bone), WARRIOR_BONE.ZMD alongside, 90 strip indices, 6 of
# 56 normals that Blender's custom normals cannot represent
STRIPS_ZMS = os.path.join(ROOT, "NPC", "NPC", "WARRIOR", "BODY03.ZMS")
# v7 static, 43 strip indices, non-unit normals (lengths ~1700)
NONUNIT_ZMS = os.path.join(ROOT, "JUNON", "HOUSE", "LHOUSE", "LHOUSE02C.ZMS")
# v8 skinned, material face counts [84, 104] (no strips)
MATERIALS_ZMS = os.path.join(ROOT, "AVATAR", "ARMS", "ARM1_03300.ZMS")
# 56 of 104 triangles degenerate; crashed normals_split_custom_set
DEGENERATE_ZMS = os.path.join(ROOT, "ITEM", "BACK", "BACK02.ZMS")
# degenerate triangles + material face counts [48, 84, 4, 4, 4] (144 tris)
DEGENERATE_MATERIALS_ZMS = os.path.join(ROOT, "PAT", "CART", "ABILITY", "CART01_ABILITY052.ZMS")
# v8 skinned, 6 bones, non-tight box, unsorted weights and weights whose
# bits renormalizing changes; ZMD alongside
SKINNED_ZMS = os.path.join(ROOT, "NPC", "ANIMAL", "WOLF2", "BODY02_1.ZMS")
# v7 skinned, zero-weight slots pointing at other bones than the pad;
# ZMD alongside
ZERO_SLOT_ZMS = os.path.join(ROOT, "NPC", "ANIMAL", "STAGBEETLE", "HEAD01_010.ZMS")
# v6: positions and bounding box stored * 100
V6_ZMS = os.path.join(ROOT, "AVATAR", "CAP", "CAP_02600.ZMS")
BOX_PROPS = ("zms_import_bbox_min", "zms_import_bbox_max", "zms_import_position_crc")
SAMPLE_STEP = 15
# Private to this process: runs from other worktrees (and scripts importing
# this module) write the same file names. main() removes it after a clean
# run and keeps it for inspection when a check fails.
TMP_DIR = tempfile.mkdtemp(prefix="io_rose_zms_export_")


def check(condition, message):
    if not condition:
        print(f"FAIL: {message}")
        return False
    print(f"ok: {message}")
    return True


def reset_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials):
        for block in list(coll):
            coll.remove(block)


def import_zms(path):
    reset_scene()
    bpy.ops.rose.import_zms(filepath=path, load_texture=False)
    return next(o for o in bpy.data.objects if o.type == 'MESH')


def import_zms_zmd(path):
    reset_scene()
    bpy.ops.rose.import_zms_zmd(filepath=path, load_texture=False, import_all_zms=False)
    return next(o for o in bpy.data.objects if o.type == 'MESH')


def has_zmd(path):
    """True if import_zms_zmd finds a skeleton for this file."""
    return bool(glob.glob(os.path.join(os.path.dirname(path), "*.ZMD")))


def vertex_groups(obj):
    """Group names in index order and each vertex's (group, weight) pairs."""
    return ([g.name for g in obj.vertex_groups],
            [sorted((g.group, g.weight) for g in v.groups) for v in obj.data.vertices])


def export(obj, name, **kwargs):
    """Export obj; returns (path, parsed ZMS, report messages)."""
    path = os.path.join(TMP_DIR, name)
    messages = []
    kwargs.setdefault("apply_world_transform", False)
    err = export_zms_mesh_object(obj, path, report=lambda level, msg: messages.append(msg),
                                 **kwargs)
    if err:
        raise RuntimeError(f"export of {obj.name} failed: {err}")
    return path, ZMS(path, report_func=lambda *a: None), messages


def same_bytes(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def box(z):
    return z.bounding_box_min.as_tuple(), z.bounding_box_max.as_tuple()


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def tight_box(z):
    """Min/max of the positions in file units, the box's units: v5/v6 store
    both * 100 but are read with the positions / 100, and f32(p * 100) gives
    back the stored float."""
    pos = [v.position.as_tuple() for v in z.vertices]
    if z.version <= 6:
        pos = [tuple(f32(c * 100.0) for c in p) for p in pos]
    return (tuple(min(p[k] for p in pos) for k in range(3)),
            tuple(max(p[k] for p in pos) for k in range(3)))


def skin(v):
    """A parsed vertex's blend weights and raw file slots, bit-for-bit."""
    return tuple(v.bone_weights), tuple(v.bone_slots or ())


def skin_stash(mesh):
    """The importers' (weights, slots) stash as 4-tuples per vertex."""
    stash = []
    for name in (FILE_BONE_WEIGHT_ATTRIBUTE, FILE_BONE_SLOT_ATTRIBUTE):
        attr = mesh.attributes.get(name)
        if attr is None:
            return None
        flat = [0.0] * (len(mesh.vertices) * 4)
        attr.data.foreach_get("value", flat)
        stash.append([tuple(flat[i:i + 4]) for i in range(0, len(flat), 4)])
    return stash


def without_stash(obj):
    """Drop the recorded box and skin stash: the exporter then behaves as it
    did before they existed (recomputed box; weights sorted by weight,
    renormalized and padded with bone 0)."""
    for key in BOX_PROPS:
        if key in obj:
            del obj[key]
    for name in (FILE_BONE_WEIGHT_ATTRIBUTE, FILE_BONE_SLOT_ATTRIBUTE):
        attr = obj.data.attributes.get(name)
        if attr is not None:
            obj.data.attributes.remove(attr)


def vec(v):
    return mathutils.Vector((v.x, v.y, v.z))


def edit_bmesh(obj, fn):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    fn(bm)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def subdivide_first_edge(bm):
    bmesh.ops.subdivide_edges(bm, edges=[bm.edges[0]], cuts=1, use_grid_fill=False)


def expected_normals(mesh, normal_matrix=None):
    """The exporter contract: one exported vertex per (vertex, uv) key in
    first-use order over the loop triangles, whose normal is the normalized
    mean of the corner normals of the loops sharing that key."""
    mesh.calc_loop_triangles()
    uv_layers = list(mesh.uv_layers)
    slot, sums, counted = {}, [], set()
    for tri in mesh.loop_triangles:
        for li in tri.loops:
            key = (mesh.loops[li].vertex_index,) + tuple(
                round(c, 6) for layer in uv_layers for c in layer.data[li].uv)
            if key not in slot:
                slot[key] = len(sums)
                sums.append(mathutils.Vector())
            if li not in counted:
                counted.add(li)
                n = mathutils.Vector(mesh.corner_normals[li].vector)
                if normal_matrix is not None:
                    n = (normal_matrix @ n).normalized()
                sums[slot[key]] += n
    return [s.normalized() for s in sums]


def max_normal_error(zms, expected, mirror=False):
    if len(zms.vertices) != len(expected):
        return math.inf
    worst = 0.0
    for v, n in zip(zms.vertices, expected):
        want = mathutils.Vector((n.x, -n.y, n.z)) if mirror else n
        worst = max(worst, (vec(v.normal) - want).length)
    return worst


def exporter_can_reproduce(z):
    """Files an unedited round trip can write back bit-for-bit, given the
    exporter's own limits (not what this test guards): it always writes
    normals, drops tangents, renumbers vertices in first-use order and
    rescales v5/v6 positions by 100. Skinned files and non-tight bounding
    boxes are included (the importers stash them)."""
    if (z.version < 7 or not z.normals_enabled() or z.tangents_enabled()
            or not z.vertices):
        return False
    order, seen = [], set()
    for idx in z.indices:
        tri = (int(idx.x), int(idx.y), int(idx.z))
        if len(set(tri)) < 3:
            return False
        for k in tri:
            if k not in seen:
                seen.add(k)
                order.append(k)
    return order == list(range(len(z.vertices)))


def test_import_state():
    ok = True
    src = ZMS(STRIPS_ZMS, report_func=lambda *a: None)
    obj = import_zms(STRIPS_ZMS)
    mesh = obj.data
    ok &= check(all(p.use_smooth for p in mesh.polygons), "import: faces smooth shaded")
    attr = mesh.attributes.get(FILE_NORMAL_ATTRIBUTE)
    ok &= check(attr is not None and attr.domain == 'POINT', "import: file normals stashed")
    if attr is not None:
        stash = [tuple(d.vector) for d in attr.data]
        want = [(v.normal.x, v.normal.y, v.normal.z) for v in src.vertices]
        ok &= check(stash == want, "import: stashed normals equal file normals bit-for-bit")
    ok &= check((obj.get("zms_import_vertex_count"), obj.get("zms_import_triangle_count"),
                 obj.get("zms_import_index_crc")) ==
                (len(src.vertices), len(src.indices), index_checksum(src.indices)),
                "import: vertex/triangle counts and index CRC recorded")
    unrepresentable = 0
    for vi, n in enumerate(expected_normals(mesh)):
        if vec(src.vertices[vi].normal).normalized().angle(n, math.pi) > 1e-3:
            unrepresentable += 1
    ok &= check(unrepresentable > 0,
                f"fixture has normals Blender cannot store exactly ({unrepresentable})")

    obj = import_zms_zmd(STRIPS_ZMS)
    ok &= check(all(p.use_smooth for p in obj.data.polygons)
                and FILE_NORMAL_ATTRIBUTE in obj.data.attributes
                and obj.get("zms_import_index_crc") == index_checksum(src.indices),
                "import with skeleton: smooth, normals stashed, topology recorded")
    ok &= check(obj.parent is not None and obj.parent.type == 'ARMATURE',
                "import with skeleton: parented to the ZMD armature")

    src = ZMS(SKINNED_ZMS, report_func=lambda *a: None)
    for label, importer in (("import_zms", import_zms), ("import_zms_zmd", import_zms_zmd)):
        obj = importer(SKINNED_ZMS)
        ok &= check((tuple(obj["zms_import_bbox_min"]), tuple(obj["zms_import_bbox_max"])) == box(src)
                    and obj["zms_import_position_crc"] == position_checksum(
                        v.position.as_tuple() for v in src.vertices),
                    f"{label}: file bounding box and position CRC recorded")
        ok &= check(skin_stash(obj.data) == [[tuple(v.bone_weights) for v in src.vertices],
                                             [tuple(map(float, v.bone_slots)) for v in src.vertices]],
                    f"{label}: file blend weights and bone slots stashed bit-for-bit")
    ok &= check(skin_stash(import_zms(NONUNIT_ZMS).data) is None, "static mesh: no skin stash")

    # No skeleton alongside: import_zms_zmd keeps the skin in import_zms's
    # zms_bone_{i} table-slot groups instead of dropping it
    src = ZMS(MATERIALS_ZMS, report_func=lambda *a: None)
    obj = import_zms_zmd(MATERIALS_ZMS)
    ok &= check(src.bones_enabled() and not has_zmd(MATERIALS_ZMS) and obj.parent is None,
                "fixture: skinned file without a skeleton alongside")
    names, weights = groups = vertex_groups(obj)
    ok &= check(names == [f"zms_bone_{i}" for i in range(len(src.bones))] and any(weights),
                "import_zms_zmd without skeleton: one zms_bone_{i} group per bone table entry")
    ok &= check(groups == vertex_groups(import_zms(MATERIALS_ZMS)),
                "import_zms_zmd without skeleton: same groups and weights as import_zms")

    # Would crash Blender (EXCEPTION_ACCESS_VIOLATION) before the fix
    src = ZMS(DEGENERATE_ZMS, report_func=lambda *a: None)
    obj = import_zms(DEGENERATE_ZMS)
    valid = sum(1 for i in src.indices if len({int(i.x), int(i.y), int(i.z)}) == 3)
    ok &= check(len(obj.data.polygons) == valid < len(src.indices),
                f"degenerate triangles skipped ({len(src.indices)} -> {valid})")
    ok &= check(all(p.use_smooth for p in obj.data.polygons), "degenerate file: smooth shaded")
    return ok


def test_unedited_round_trip():
    ok = True
    for label, importer, path in (("import_zms", import_zms, STRIPS_ZMS),
                                  ("import_zms_zmd", import_zms_zmd, STRIPS_ZMS),
                                  ("non-unit normals", import_zms, NONUNIT_ZMS),
                                  ("skinned", import_zms, SKINNED_ZMS),
                                  ("skinned import_zms_zmd", import_zms_zmd, SKINNED_ZMS),
                                  ("zero-weight slots", import_zms, ZERO_SLOT_ZMS),
                                  ("zero-weight slots import_zms_zmd", import_zms_zmd, ZERO_SLOT_ZMS),
                                  ("material face counts", import_zms, MATERIALS_ZMS),
                                  ("skinned import_zms_zmd without skeleton", import_zms_zmd,
                                   MATERIALS_ZMS)):
        obj = importer(path)
        if importer is import_zms_zmd and has_zmd(path):
            ok &= check(obj.parent is not None and obj.parent.type == 'ARMATURE' and
                        not any(g.name.startswith("zms_bone_") for g in obj.vertex_groups),
                        f"{label}: skeleton found, weights in joint-named groups")
        elif importer is import_zms_zmd:
            ok &= check(obj.parent is None and len(obj.vertex_groups) > 0 and
                        all(g.name == f"zms_bone_{g.index}" for g in obj.vertex_groups),
                        f"{label}: no skeleton, weights in zms_bone_{{i}} groups")
        out, zms, messages = export(obj, "roundtrip.zms")
        ok &= check(same_bytes(path, out),
                    f"unedited {label} round trip byte-identical ({os.path.basename(path)})")
        ok &= check(not any("empty" in m for m in messages), f"{label}: no strip-drop report")
    src = ZMS(MATERIALS_ZMS, report_func=lambda *a: None)
    ok &= check(src.materials == [84, 104], "fixture has material face counts")

    tested, failed, skinned, loose_box, with_zmd, without_zmd = 0, [], 0, 0, 0, 0
    files = sorted(glob.glob(os.path.join(ROOT, "**", "*.ZMS"), recursive=True))
    for path in files[::SAMPLE_STEP]:
        try:
            src = ZMS(path, report_func=lambda *a: None)
        except Exception:
            continue
        if not exporter_can_reproduce(src):
            continue
        tested += 1
        skinned += src.bones_enabled()
        loose_box += box(src) != tight_box(src)
        importers = [import_zms]
        # Skinned files also through import_zms_zmd: joint-named groups with
        # a skeleton alongside, zms_bone_{i} groups without one
        if src.bones_enabled():
            importers.append(import_zms_zmd)
            if has_zmd(path):
                with_zmd += 1
            else:
                without_zmd += 1
        for importer in importers:
            out, _, _ = export(importer(path), "sample.zms")
            if not same_bytes(path, out):
                failed.append(f"{os.path.relpath(path, ROOT)} ({importer.__name__})")
    ok &= check(tested >= 150 and skinned >= 30 and loose_box >= 100 and with_zmd >= 15
                and without_zmd >= 20 and not failed,
                f"sampled client files round-trip byte-identically "
                f"({tested - len(failed)}/{tested}; {skinned} skinned, also through "
                f"import_zms_zmd: {with_zmd} with a skeleton, {without_zmd} without; "
                f"{loose_box} non-tight boxes; failed: {failed[:5]})")
    return ok


def test_edited_topology():
    ok = True
    src = ZMS(STRIPS_ZMS, report_func=lambda *a: None)
    edits = (
        ("subdivided edge", subdivide_first_edge, "imported"),
        ("deleted face", lambda bm: bmesh.ops.delete(
            bm, geom=[bm.faces[0]], context='FACES_ONLY'), "imported"),
        ("flipped face (same counts)", lambda bm: bmesh.ops.reverse_faces(
            bm, faces=[bm.faces[0]]), "triangle list differs"),
    )
    for label, edit, reason in edits:
        obj = import_zms(STRIPS_ZMS)
        edit_bmesh(obj, edit)
        _, zms, messages = export(obj, "edited.zms")
        ok &= check(zms.strips == [] and zms.materials == [],
                    f"{label}: stale strips not written ({len(src.strips)} -> 0)")
        ok &= check(any("empty triangle strips" in m and reason in m for m in messages),
                    f"{label}: INFO report explains the dropped strips")
        ok &= check(zms.bones == src.bones and zms.pool == src.pool,
                    f"{label}: bone table and pool still restored")
        ok &= check(max_normal_error(zms, expected_normals(obj.data)) < 1e-5,
                    f"{label}: normals are the edited mesh's corner normals")

    obj = import_zms(MATERIALS_ZMS)
    edit_bmesh(obj, subdivide_first_edge)
    _, zms, _ = export(obj, "edited_materials.zms")
    ok &= check(zms.materials == [], "edited mesh: stale material face counts not written")

    obj = import_zms(STRIPS_ZMS)
    for key in ("zms_import_vertex_count", "zms_import_triangle_count", "zms_import_index_crc"):
        del obj[key]
    _, zms, messages = export(obj, "legacy.zms")
    ok &= check(zms.strips == [] and any("older io_rose" in m for m in messages),
                "object without import counts (older import): strips dropped with report")

    src = ZMS(DEGENERATE_MATERIALS_ZMS, report_func=lambda *a: None)
    _, zms, messages = export(import_zms(DEGENERATE_MATERIALS_ZMS), "degenerate.zms")
    ok &= check(src.materials and zms.materials == [] and any("empty" in m for m in messages),
                "degenerate triangles skipped on import: stale material counts dropped")
    return ok


def test_bounding_box():
    ok = True
    src = ZMS(SKINNED_ZMS, report_func=lambda *a: None)
    ok &= check(box(src) != tight_box(src), "fixture: file box is not the min/max of its positions")
    _, zms, _ = export(import_zms(SKINNED_ZMS), "box.zms")
    ok &= check(box(zms) == box(src), "unedited: the file's bounding box is written back")

    def moved_vertex(obj):
        obj.data.vertices[0].co.x += 0.25

    def moved_object(obj):
        obj.location = (3.0, -2.0, 1.0)
        bpy.context.view_layer.update()

    def legacy(obj):
        for key in BOX_PROPS:
            del obj[key]

    for label, edit, kwargs in (
            ("moved vertex", moved_vertex, {}),
            ("world export of a moved object", moved_object, {"apply_world_transform": True}),
            ("subdivided edge", lambda obj: edit_bmesh(obj, subdivide_first_edge), {}),
            ("mirrored export", lambda obj: None,
             {"apply_world_transform": True, "convert_coordinates": True}),
            ("object without a recorded box (older import)", legacy, {})):
        obj = import_zms(SKINNED_ZMS)
        edit(obj)
        _, zms, _ = export(obj, "box_edited.zms", **kwargs)
        ok &= check(box(zms) == tight_box(zms) != box(src),
                    f"{label}: box recomputed from the exported positions")

    # v5/v6 store positions and the box * 100; the check runs on the mesh's
    # own coordinates, so the float rounding of * 100 does not defeat it
    src = ZMS(V6_ZMS, report_func=lambda *a: None)
    ok &= check(src.version == 6 and box(src) == tight_box(src),
                "v6 fixture: file box is the min/max of its positions in file units (* 100)")
    _, zms, _ = export(import_zms(V6_ZMS), "box_v6.zms")
    ok &= check(box(zms) == box(src), "v6: the file's bounding box is written back")

    # A recomputed v5/v6 box is in file units like the positions it bounds
    # (it used to be written in mesh units, 100x too small)
    def moved_max_x_vertex(obj):
        max(obj.data.vertices, key=lambda v: v.co.x).co.x += 0.25

    (lo, hi) = box(src)
    for label, edit, kwargs, want in (
            ("moved vertex", moved_max_x_vertex, {},
             (lo, (hi[0] + 25.0, hi[1], hi[2]))),
            ("world export of a moved object", moved_object, {"apply_world_transform": True},
             ((lo[0] + 300.0, lo[1] - 200.0, lo[2] + 100.0),
              (hi[0] + 300.0, hi[1] - 200.0, hi[2] + 100.0))),
            ("mirrored export", lambda obj: None,
             {"apply_world_transform": True, "convert_coordinates": True},
             ((lo[0], -hi[1], lo[2]), (hi[0], -lo[1], hi[2]))),
            ("object without a recorded box (older import)", legacy, {}, (lo, hi))):
        obj = import_zms(V6_ZMS)
        edit(obj)
        _, zms, _ = export(obj, "box_v6_edited.zms", **kwargs)
        err = max(abs(a - b) for got, exp in zip(box(zms), want) for a, b in zip(got, exp))
        ok &= check(box(zms) == tight_box(zms) and err < 1e-3,
                    f"v6 {label}: box recomputed in file units (max error {err:.2g})")
    return ok


def test_skin_weights():
    ok = True
    # Fixtures: the pre-stash exporter rewrites these vertices
    for path, what in ((SKINNED_ZMS, "weight order / renormalized bits"),
                       (ZERO_SLOT_ZMS, "zero-weight slots")):
        src = ZMS(path, report_func=lambda *a: None)
        obj = import_zms(path)
        without_stash(obj)
        _, zms, _ = export(obj, "skin_fallback.zms")
        rewritten = [i for i, v in enumerate(src.vertices) if skin(zms.vertices[i]) != skin(v)]
        zero_slots = [i for i in rewritten
                      if [w for w in src.vertices[i].bone_weights if w > 0] ==
                      [w for w in zms.vertices[i].bone_weights if w > 0]]
        ok &= check(rewritten and (path != ZERO_SLOT_ZMS or zero_slots),
                    f"fixture: without the stash {len(rewritten)} of {len(src.vertices)} vertices "
                    f"of {os.path.basename(path)} change ({what})")

    # Edited weights on some vertices: those export exactly as without the
    # stash, the untouched ones keep the file's slots and weight bits
    edited = {0, 5, 9}
    for label, importer, path in (("import_zms", import_zms, SKINNED_ZMS),
                                  ("import_zms_zmd", import_zms_zmd, SKINNED_ZMS),
                                  ("import_zms_zmd without skeleton", import_zms_zmd,
                                   MATERIALS_ZMS)):
        src = ZMS(path, report_func=lambda *a: None)
        obj = importer(path)
        for vi in edited:
            g = obj.data.vertices[vi].groups[0]
            obj.vertex_groups[g.group].add([vi], 0.25 if g.weight != 0.25 else 0.5, 'REPLACE')
        _, zms, _ = export(obj, "skin_edited.zms")
        without_stash(obj)
        _, fallback, _ = export(obj, "skin_edited_fallback.zms")
        ok &= check(all(skin(zms.vertices[i]) == skin(fallback.vertices[i]) for i in edited),
                    f"{label}: edited vertices export sorted, renormalized weights as before")
        ok &= check(all(skin(zms.vertices[i]) == skin(v)
                        for i, v in enumerate(src.vertices) if i not in edited),
                    f"{label}: untouched vertices keep the file's slots and weight bits")
        ok &= check(box(zms) == box(src), f"{label}: weight edits keep the file's box")

    # Topology edits (and the mirrored zone export) ignore the stash
    for label, edit, kwargs in (
            ("subdivided edge", lambda obj: edit_bmesh(obj, subdivide_first_edge), {}),
            ("deleted face", lambda obj: edit_bmesh(obj, lambda bm: bmesh.ops.delete(
                bm, geom=[bm.faces[0]], context='FACES_ONLY')), {}),
            ("mirrored export", lambda obj: None,
             {"apply_world_transform": True, "convert_coordinates": True})):
        obj = import_zms(SKINNED_ZMS)
        edit(obj)
        out, _, _ = export(obj, "skin_topology.zms", **kwargs)
        without_stash(obj)
        fallback, _, _ = export(obj, "skin_topology_fallback.zms", **kwargs)
        ok &= check(same_bytes(out, fallback),
                    f"{label}: output identical to the exporter without the stash")
    return ok


def test_duplicate_bone_table():
    """No client file lists a bone twice, but the slots must survive it:
    bone_table.index() alone would move them to the bone's first slot."""
    ok = True
    z = ZMS(SKINNED_ZMS, report_func=lambda *a: None)
    dup = len(z.bones)
    z.bones.append(z.bones[0])
    moved = 0
    for v in z.vertices:
        for k in range(4):
            if moved < 5 and v.bone_slots[k] == 0 and v.bone_weights[k] > 0:
                v.bone_slots[k] = dup
                moved += 1
    path = os.path.join(TMP_DIR, "dup_table.zms")
    with open(path, "wb") as f:
        ExportZMS.write_zms(f, z)
    written = ZMS(path, report_func=lambda *a: None)
    ok &= check(moved == 5 and sum(s == dup for v in written.vertices for s in v.bone_slots) == 5,
                "writer keeps the file slots of a bone listed twice")
    out, _, _ = export(import_zms(path), "dup_table_roundtrip.zms")
    ok &= check(same_bytes(path, out), "duplicate bone table: unedited round trip byte-identical")
    return ok


def make_mesh_object(name, verts, faces, normal_field):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.shade_smooth()
    uv = mesh.uv_layers.new(name="uv1")
    for loop in mesh.loops:
        co = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (co.x * 0.5 + 0.5, co.y * 0.5 + 0.5)
    auto = [v.normal.copy() for v in mesh.vertices]
    mesh.normals_split_custom_set(
        [normal_field(mesh.vertices[l.vertex_index].co) for l in mesh.loops])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj, auto


def sheet(x0, x1, nx=6, ny=8):
    """Curved sheet over [x0, x1] x [-1, 1] (curvature across x = 0)."""
    verts, faces = [], []
    for j in range(ny + 1):
        for i in range(nx + 1):
            x = x0 + (x1 - x0) * i / nx
            y = -1.0 + 2.0 * j / ny
            verts.append((x, y, 0.6 * x * x + 0.2 * y * y))
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            faces.append((a, a + 1, a + nx + 2, a + nx + 1))
    return verts, faces


def field(co):
    """Normal field shared by both pieces (as Data Transfer from the whole
    mesh would set it); deliberately different from the face normals."""
    return mathutils.Vector((0.3 * math.sin(3.0 * co.y) - 0.5 * co.x,
                             0.25 * co.x - 0.2 * co.y, 1.0)).normalized()


def test_normals():
    ok = True
    reset_scene()
    verts, faces = sheet(-1.0, 1.0)
    obj, auto = make_mesh_object("Sheet", verts, faces, field)
    _, zms, _ = export(obj, "sheet.zms")
    expected = expected_normals(obj.data)
    ok &= check(len(zms.vertices) == len(expected) and
                max_normal_error(zms, expected) < 1e-5,
                "custom normals exported (not face-derived normals)")
    ok &= check(max(a.angle(field(v.co)) for a, v in zip(auto, obj.data.vertices)) > 0.2,
                "fixture: custom normals differ from the face-derived normals")

    # Hard normal edge along a UV seam: each exported copy of a seam vertex
    # (one per UV) keeps its own side's normal. Blender 4.5's vertex.normal
    # is the mean over all corners, which blurs the edge.
    reset_scene()
    mesh = bpy.data.meshes.new("HardEdge")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0)],
                     [], [(0, 1, 4, 3), (1, 2, 5, 4)])
    mesh.shade_smooth()
    uv = mesh.uv_layers.new(name="uv1")
    side = (mathutils.Vector((-0.5, 0.0, 1.0)).normalized(),
            mathutils.Vector((0.5, 0.0, 1.0)).normalized())
    loop_normals = [None] * len(mesh.loops)
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            co = mesh.vertices[mesh.loops[li].vertex_index].co
            uv.data[li].uv = (co.x * 0.3 + 0.1 * poly.index, co.y)
            loop_normals[li] = side[poly.index]
    mesh.normals_split_custom_set(loop_normals)
    obj = bpy.data.objects.new("HardEdge", mesh)
    bpy.context.collection.objects.link(obj)
    _, zms, _ = export(obj, "hard_edge.zms")
    seam_normals = [vec(v.normal) for v in zms.vertices if abs(v.position.x - 1.0) < 1e-6]
    ok &= check(len(seam_normals) == 4 and
                sum(1 for n in seam_normals if n.angle(side[0]) < 1e-3) == 2 and
                sum(1 for n in seam_normals if n.angle(side[1]) < 1e-3) == 2 and
                max_normal_error(zms, expected_normals(mesh)) < 1e-5,
                "hard edge on a UV seam: normals averaged per (vertex, uv), not per vertex")

    # One mesh split into two pieces along x = 0 (head/body) with the same
    # custom normals on both sides: the seam vertices must get the same
    # normal in both files (the per-piece face normals would not).
    reset_scene()
    seams, auto_gap = {}, 0.0
    auto_at = {}
    for name, (x0, x1) in (("Left", (-1.0, 0.0)), ("Right", (0.0, 1.0))):
        verts, faces = sheet(x0, x1, nx=3)
        obj, auto = make_mesh_object(name, verts, faces, field)
        _, zms, _ = export(obj, f"{name}.zms")
        for v in zms.vertices:
            if abs(v.position.x) < 1e-6:
                seams.setdefault((round(v.position.y, 5)), []).append(vec(v.normal))
        for v, n in zip(obj.data.vertices, auto):
            if abs(v.co.x) < 1e-6:
                auto_at.setdefault(round(v.co.y, 5), []).append(n)
    pairs = [lst for lst in seams.values() if len(lst) == 2]
    gap = max(a.angle(b) for a, b in pairs) if pairs else math.pi
    auto_gap = max(a.angle(b) for a, b in (lst for lst in auto_at.values() if len(lst) == 2))
    ok &= check(len(pairs) == 9 and gap < 1e-3,
                f"split pieces: seam normals agree (max {math.degrees(gap):.4f} deg)")
    ok &= check(auto_gap > 0.05,
                f"fixture: per-piece vertex normals would disagree ({math.degrees(auto_gap):.1f} deg)")

    # Edited normals on unedited topology: edited vertices export the new
    # corner normals, untouched ones the exact file normals, strips kept.
    src = ZMS(STRIPS_ZMS, report_func=lambda *a: None)
    obj = import_zms(STRIPS_ZMS)
    mesh = obj.data
    file_normals = [vec(v.normal) for v in src.vertices]
    edited = set(range(10))
    mesh.normals_split_custom_set([
        (0.0, 0.0, 1.0) if l.vertex_index in edited else file_normals[l.vertex_index]
        for l in mesh.loops])
    _, zms, _ = export(obj, "edited_normals.zms")
    expected = expected_normals(mesh)
    ok &= check(max((vec(zms.vertices[i].normal) - expected[i]).length for i in edited) < 1e-5,
                "edited normals exported as the new corner normals")
    ok &= check(all(zms.vertices[i].normal.as_tuple() == src.vertices[i].normal.as_tuple()
                    for i in range(len(src.vertices)) if i not in edited),
                "untouched vertices keep their exact file normals")
    ok &= check(zms.strips == src.strips, "normal edits alone keep the strips")

    # Moving the object (world export, translation only) keeps exact normals
    obj = import_zms(STRIPS_ZMS)
    obj.location = (3.0, -2.0, 1.0)
    bpy.context.view_layer.update()
    _, zms, _ = export(obj, "moved.zms", apply_world_transform=True)
    ok &= check([v.normal.as_tuple() for v in zms.vertices] == [v.normal.as_tuple() for v in src.vertices],
                "translation-only world export writes the exact file normals")

    # Zone-exporter path: world transform (rotation + non-uniform scale)
    # and the (x, -y, z) mirror. Custom normals must follow the inverse
    # transpose, not bmesh.ops.transform (which corrupts them).
    reset_scene()
    verts, faces = sheet(-1.0, 1.0)
    obj, _ = make_mesh_object("World", verts, faces, field)
    obj.location = (5.0, 1.0, -2.0)
    obj.rotation_euler = (math.radians(20), 0.0, math.radians(30))
    obj.scale = (1.0, 2.0, 0.5)
    bpy.context.view_layer.update()
    normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
    _, zms, _ = export(obj, "world.zms", apply_world_transform=True, convert_coordinates=True)
    err = max_normal_error(zms, expected_normals(obj.data, normal_matrix), mirror=True)
    ok &= check(err < 1e-5, f"world-space + mirrored export transforms custom normals ({err:.2e})")

    obj = import_zms(STRIPS_ZMS)
    _, zms, messages = export(obj, "zone.zms", apply_world_transform=True,
                              convert_coordinates=True)
    ok &= check(zms.strips == [] and any("winding" in m for m in messages),
                "mirrored export drops strips (winding swapped)")
    return ok


def main():
    ok = False
    try:
        ok = run_tests()
    finally:
        if ok or not os.listdir(TMP_DIR):
            shutil.rmtree(TMP_DIR, ignore_errors=True)
        else:
            print(f"exported files kept in {TMP_DIR}")
    return 0 if ok else 1


def run_tests():
    for path in (STRIPS_ZMS, NONUNIT_ZMS, MATERIALS_ZMS, DEGENERATE_ZMS, DEGENERATE_MATERIALS_ZMS,
                 SKINNED_ZMS, ZERO_SLOT_ZMS, V6_ZMS):
        if not os.path.isfile(path):
            print(f"test file not found: {path}")
            return False
    ok = True
    for test in (test_import_state, test_unedited_round_trip, test_edited_topology,
                 test_bounding_box, test_skin_weights, test_duplicate_bone_table,
                 test_normals):
        # Blender's --python exits 0 on an uncaught exception
        try:
            ok &= test()
        except Exception:
            traceback.print_exc()
            ok = check(False, f"{test.__name__} raised")
    print("BLENDER ZMS EXPORT TEST " + ("OK" if ok else "FAILED"))
    return ok


if __name__ == "__main__":
    sys.exit(main())
