"""Zone exporter: save an imported .zon zone back to the game data.

Mirrors the Rust map editor's save system (src/map_editor/save/):

- Object instances (empties stamped by the importer) are matched back to
  their IFO block + object index; transforms are updated in place. Objects
  moved across block borders are relocated to the block they are now in.
- New objects (rose_is_new, no IFO index) are appended to the block's
  object list.
- Objects listed in the scene's rose_deleted_objects list are removed.
- Terrain: the ROSE_Terrain mesh is compared against the original HIM
  files; blocks with changed heights are rewritten.
- Original files are backed up to <zone_dir>/backup/<timestamp>/ before
  any overwrite. Only modified blocks are written.
"""

import json
import os
import struct
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .rose.dds import write_dds_rgba8
from .rose.him import Him
from .rose.ifo import Ifo, IfoObject, BlockType
from .rose.zon import Zon
from .rose.zsc import Zsc, ZscMaterial, ZscObject, ZscObjectPart

IFO_BLOCK = {"CNST": BlockType.CnstObject, "DECO": BlockType.DecoObject}

# The game's absolute world space (see architecture/blender-importer.md):
# block corner = 160 * block_coord - 5200 m, block size 160 m.
ZONE_CENTER = 5200.0
BLOCK_SIZE = 160.0
CM_PER_M = 100.0
HEIGHT_EPSILON_CM = 0.5  # terrain change threshold (0.5 cm)


def f32(value):
    """Quantize to f32 (the game file precision)."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def resolve_ifo_path(zone_dir, bx, by):
    """IFO path for a block, tolerating lowercase extensions on
    case-sensitive filesystems (data files use uppercase .IFO)."""
    for ext in (".IFO", ".ifo"):
        cand = os.path.join(zone_dir, f"{bx}_{by}{ext}")
        if os.path.exists(cand):
            return cand
    return os.path.join(zone_dir, f"{bx}_{by}.IFO")


def world_to_block(location):
    """(block_x, block_y) for a Blender world position, matching the game's
    block grid. Terrain and objects lie in the X/Y plane in Blender
    (Z = height); the client's north axis maps to Blender Y:
    block corner x/y = 160 * block_coord - 5200."""
    bx = int((location[0] + ZONE_CENTER) // BLOCK_SIZE)
    by = int((location[1] + ZONE_CENTER) // BLOCK_SIZE)
    return bx, by


def blender_to_ifo(obj):
    """IfoObject fields from a Blender object transform (inverse of the
    importer's conversion: position cm (x*100, -y*100, z*100), rotation
    XYZW with negated Y, scale unchanged)."""
    loc = obj.matrix_world.translation
    ifo = IfoObject()
    ifo.position.x = loc[0] * CM_PER_M
    ifo.position.y = -loc[1] * CM_PER_M
    ifo.position.z = loc[2] * CM_PER_M
    # Read the rotation from the object's actual rotation properties
    # (matrix_world can be stale in background mode, and quaternion
    # assignments on Euler-mode objects are no-ops in Blender 4.x).
    if obj.rotation_mode == 'QUATERNION':
        q = obj.rotation_quaternion
    else:
        q = obj.rotation_euler.to_quaternion()
    ifo.rotation.x = q.x
    ifo.rotation.y = -q.y
    ifo.rotation.z = q.z
    ifo.rotation.w = q.w
    s = obj.matrix_world.to_scale()
    ifo.scale.x, ifo.scale.y, ifo.scale.z = s[0], s[1], s[2]
    return ifo


def get_block_list(ifo, block_type):
    if block_type == BlockType.DecoObject:
        return ifo.deco_objects
    if block_type == BlockType.CnstObject:
        return ifo.cnst_objects
    if block_type == BlockType.AnimatedObject:
        return ifo.animated_objects
    if block_type == BlockType.CollisionObject:
        return ifo.collision_objects
    if block_type == BlockType.Warp:
        return ifo.warps
    if block_type == BlockType.EventObject:
        return ifo.event_objects
    if block_type == BlockType.Npc:
        return ifo.npcs
    if block_type == BlockType.SoundObject:
        return ifo.sound_objects
    if block_type == BlockType.EffectObject:
        return ifo.effect_objects
    if block_type == BlockType.MonsterSpawn:
        return ifo.monster_spawns
    return None


def backup_files(zone_dir, paths):
    """Copy files into <zone_dir>/backup/<timestamp>/ before overwriting."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(zone_dir, "backup", stamp)
    os.makedirs(backup_dir, exist_ok=True)
    copied = 0
    for p in paths:
        if os.path.exists(p):
            import shutil
            shutil.copy2(p, os.path.join(backup_dir, os.path.basename(p)))
            copied += 1
    return backup_dir, copied


class ExportZone(bpy.types.Operator):
    bl_idname = "rose.export_zone"
    bl_label = "Save ROSE Zone (.zon)"
    bl_description = "Write the imported zone back to the game data (IFO objects + terrain heights)"

    export_terrain: BoolProperty(
        name="Export Terrain",
        description="Write HIM files for terrain blocks whose heights changed",
        default=True,
    )

    @staticmethod
    def _scene_meta(context):
        scene = context.scene
        zone_file = scene.get("rose_zone_file")
        zone_dir = scene.get("rose_zone_dir")
        if not zone_file or not os.path.exists(zone_file):
            return None
        return zone_file, zone_dir

    def execute(self, context):
        meta = self._scene_meta(context)
        if not meta:
            self.report({'ERROR'}, "No zone imported in this scene - import a .zon file first")
            return {'CANCELLED'}
        zone_file, zone_dir = meta

        errors = []
        written = []      # files actually written
        updated = 0
        added = 0
        deleted = 0
        terrain_changed = 0

        try:
            zon = Zon(zone_file)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read ZON: {e}")
            return {'CANCELLED'}

        # ---------------------------------------------------------------
        # 1. Collect objects with round-trip metadata
        # ---------------------------------------------------------------
        by_block = {}   # (bx, by) -> list of empties
        new_objects = []
        for obj in bpy.data.objects:
            if not (obj.type == 'EMPTY' and "rose_ifo_block" in obj):
                continue
            if obj.get("rose_is_new"):
                new_objects.append(obj)
                continue
            bx = obj.get("rose_block_x")
            by = obj.get("rose_block_y")
            if bx is None or by is None:
                continue
            by_block.setdefault((int(bx), int(by)), []).append(obj)

        # Duplicated instances: when an object is copied in Blender, the
        # copy keeps the same (block, kind, index) metadata. Only the
        # first claimant keeps the original IFO record; the rest are
        # treated as new placements and appended to their block.
        # bpy.data.objects is in creation order, so the original comes
        # first and keeps its record.
        claimed = set()
        duplicate_objects = []
        for obj in bpy.data.objects:
            if not (obj.type == 'EMPTY' and "rose_ifo_block" in obj):
                continue
            if obj.get("rose_is_new"):
                continue
            key = (int(obj["rose_block_x"]), int(obj["rose_block_y"]),
                   str(obj["rose_ifo_block"]), int(obj["rose_ifo_index"]))
            if key in claimed:
                duplicate_objects.append(obj)
            else:
                claimed.add(key)
        if duplicate_objects:
            print(f"rose: {len(duplicate_objects)} duplicated instance(s) "
                  f"will be added as new placements")

        # Deletions: "CNST:bx:by:index" entries recorded by the
        # Mark-for-deletion operator.
        deleted_list = []
        if "rose_deleted_objects" in context.scene:
            try:
                deleted_list = json.loads(context.scene["rose_deleted_objects"])
            except Exception:
                deleted_list = []
        deleted_keys = set()
        for entry in deleted_list:
            try:
                kind, bx, by, idx = entry.split(":")
                deleted_keys.add((kind, int(bx), int(by), int(idx)))
            except ValueError:
                pass

        # ---------------------------------------------------------------
        # 2. Update / add / remove objects per block
        # ---------------------------------------------------------------
        block_keys = set(by_block.keys())
        for entry in deleted_list:
            try:
                kind, bx, by, idx = entry.split(":")
                block_keys.add((int(bx), int(by)))
            except ValueError:
                errors.append(f"bad deletion entry: {entry}")

        for obj in new_objects:
            bx, by = world_to_block(obj.matrix_world.translation)
            block_keys.add((bx, by))
        for obj in duplicate_objects:
            bx, by = world_to_block(obj.matrix_world.translation)
            block_keys.add((bx, by))

        ifo_cache = {}
        for (bx, by) in sorted(block_keys):
            ifo_path = resolve_ifo_path(zone_dir, bx, by)
            if ifo_path not in ifo_cache:
                if os.path.exists(ifo_path):
                    try:
                        ifo_cache[ifo_path] = Ifo(ifo_path)
                    except Exception as e:
                        errors.append(f"failed to read {os.path.basename(ifo_path)}: {e}")
                        continue
                else:
                    ifo_cache[ifo_path] = Ifo()

        # Process deletions first (indices shift). Removals are applied in
        # descending index order per block+kind so earlier indices stay
        # valid, and the stored rose_ifo_index props of the remaining
        # objects are re-indexed afterwards.
        removed_by_block = {}   # (bx, by, kind) -> sorted removed indices
        for entry in deleted_list:
            try:
                kind, bx, by, idx = entry.split(":")
                block_type = IFO_BLOCK.get(kind)
                if block_type is None:
                    errors.append(f"unknown object kind {kind}")
                    continue
                removed_by_block.setdefault((int(bx), int(by), kind), []).append(int(idx))
            except ValueError:
                continue

        for (bx, by, kind), indices in removed_by_block.items():
            block_type = IFO_BLOCK.get(kind)
            ifo_path = resolve_ifo_path(zone_dir, bx, by)
            ifo = ifo_cache.get(ifo_path)
            if ifo is None:
                continue
            lst = get_block_list(ifo, block_type)
            if lst is None:
                continue
            removed = 0
            for idx in sorted(indices, reverse=True):
                if idx < len(lst):
                    del lst[idx]
                    removed += 1
            if removed:
                deleted += removed
                written.append(ifo_path)
            # Re-index the scene objects of this block so their stored
            # indices stay in sync with the (shrunk) list.
            for obj in bpy.data.objects:
                if not (obj.type == 'EMPTY' and obj.get("rose_ifo_block") == kind and
                        obj.get("rose_block_x") == bx and obj.get("rose_block_y") == by):
                    continue
                oi = int(obj["rose_ifo_index"])
                if oi in indices:
                    continue  # deleted object - keep its key for the skip check
                shift = sum(1 for r in indices if r < oi)
                if shift:
                    obj["rose_ifo_index"] = oi - shift

        # Update existing instances
        for (bx, by), objs in by_block.items():
            ifo_path = resolve_ifo_path(zone_dir, bx, by)
            ifo = ifo_cache.get(ifo_path)
            if ifo is None:
                continue
            changed = False
            for obj in objs:
                block_type = IFO_BLOCK.get(str(obj["rose_ifo_block"]))
                if block_type is None:
                    errors.append(f"{obj.name}: unknown IFO block {obj['rose_ifo_block']}")
                    continue
                # Objects marked for deletion are handled by the deletion
                # pass above - skip them here so the update pass does not
                # trip over their (now removed) indices.
                key = (str(obj["rose_ifo_block"]), bx, by, int(obj["rose_ifo_index"]))
                if key in deleted_keys:
                    continue
                lst = get_block_list(ifo, block_type)
                idx = int(obj["rose_ifo_index"])
                if lst is None or idx >= len(lst):
                    errors.append(f"{obj.name}: index {idx} out of range for {kind_of(block_type)} in {bx}_{by}.IFO")
                    continue

                entry = lst[idx]
                if not hasattr(entry, 'object'):
                    # plain IfoObject lists (deco/cnst/animated/collision/warp)
                    ifo_obj = entry
                else:
                    ifo_obj = entry.object
                new_t = blender_to_ifo(obj)
                # Quantize to f32 so an untouched object round-trips exactly
                # (the import divides f32 cm by 100; multiplying back gives
                # sub-0.001 cm float noise that must not trigger a rewrite).
                new_pos = (f32(new_t.position.x), f32(new_t.position.y), f32(new_t.position.z))
                new_rot = (f32(new_t.rotation.x), f32(new_t.rotation.y),
                           f32(new_t.rotation.z), f32(new_t.rotation.w))
                new_scale = (f32(new_t.scale.x), f32(new_t.scale.y), f32(new_t.scale.z))
                pos_d = abs(ifo_obj.position.x - new_pos[0]) + \
                        abs(ifo_obj.position.y - new_pos[1]) + \
                        abs(ifo_obj.position.z - new_pos[2])
                # Quaternions are equivalent under sign flip (q == -q), and
                # Blender's Euler round-trip may negate the whole quaternion
                # on import - compare sign-normalized values.
                dot = (ifo_obj.rotation.x * new_rot[0] +
                       ifo_obj.rotation.y * new_rot[1] +
                       ifo_obj.rotation.z * new_rot[2] +
                       ifo_obj.rotation.w * new_rot[3])
                if dot < 0:
                    new_rot = tuple(-v for v in new_rot)
                rot_d = abs(ifo_obj.rotation.x - new_rot[0]) + \
                        abs(ifo_obj.rotation.y - new_rot[1]) + \
                        abs(ifo_obj.rotation.z - new_rot[2]) + \
                        abs(ifo_obj.rotation.w - new_rot[3])
                scale_d = abs(ifo_obj.scale.x - new_scale[0]) + \
                          abs(ifo_obj.scale.y - new_scale[1]) + \
                          abs(ifo_obj.scale.z - new_scale[2])

                # Untouched objects (incl. block-boundary float cases and
                # Blender's quaternion normalization / f32 ULP drift) stay
                # exactly as they are in the file - never rewritten. A user
                # edit moves or rotates an object by far more than these
                # thresholds (0.01 cm / 0.0003 deg / 0.001 scale).
                if pos_d <= 1e-2 and rot_d <= 1e-3 and scale_d <= 1e-4:
                    continue

                # The object was edited. If it now sits in a different
                # block, relocate it there (as a new entry).
                cur_bx, cur_by = world_to_block(obj.matrix_world.translation)
                if (cur_bx, cur_by) != (bx, by):
                    new_path = resolve_ifo_path(zone_dir, cur_bx, cur_by)
                    if new_path not in ifo_cache:
                        if os.path.exists(new_path):
                            try:
                                ifo_cache[new_path] = Ifo(new_path)
                            except Exception:
                                ifo_cache[new_path] = Ifo()
                        else:
                            ifo_cache[new_path] = Ifo()
                    target = ifo_cache[new_path]
                    tgt_lst = get_block_list(target, block_type)
                    reloc = blender_to_ifo(obj)
                    reloc.object_name = str(obj.get("rose_ifo_object_name", obj.name))
                    reloc.object_name_raw = None
                    reloc.object_id = int(obj["rose_zsc_object_id"])
                    tgt_lst.append(reloc)
                    del lst[idx]
                    # re-index the remaining objects of this block
                    for other in objs:
                        if other is obj:
                            continue
                        oi = int(other["rose_ifo_index"])
                        if oi > idx:
                            other["rose_ifo_index"] = oi - 1
                    obj["rose_block_x"] = cur_bx
                    obj["rose_block_y"] = cur_by
                    obj["rose_ifo_index"] = len(tgt_lst) - 1
                    added += 1
                    changed = True
                    continue

                ifo_obj.position.x, ifo_obj.position.y, ifo_obj.position.z = new_pos
                ifo_obj.rotation.x, ifo_obj.rotation.y = new_rot[0], new_rot[1]
                ifo_obj.rotation.z, ifo_obj.rotation.w = new_rot[2], new_rot[3]
                ifo_obj.scale.x, ifo_obj.scale.y, ifo_obj.scale.z = new_scale
                updated += 1
                changed = True
            if changed:
                written.append(ifo_path)

        # Append new objects (rose_is_new, plus duplicated instances)
        for obj in list(new_objects) + duplicate_objects:
            bx, by = world_to_block(obj.matrix_world.translation)
            ifo_path = resolve_ifo_path(zone_dir, bx, by)
            if ifo_path not in ifo_cache:
                if os.path.exists(ifo_path):
                    try:
                        ifo_cache[ifo_path] = Ifo(ifo_path)
                    except Exception:
                        ifo_cache[ifo_path] = Ifo()
                else:
                    ifo_cache[ifo_path] = Ifo()
            ifo = ifo_cache[ifo_path]
            block_type = IFO_BLOCK.get(str(obj["rose_ifo_block"]))
            if block_type is None:
                continue
            lst = get_block_list(ifo, block_type)
            new_ifo = blender_to_ifo(obj)
            new_ifo.object_name = str(obj.get("rose_ifo_object_name", obj.name))
            new_ifo.object_name_raw = None
            new_ifo.object_id = int(obj.get("rose_zsc_object_id", 0))
            lst.append(new_ifo)
            # Convert the instance into a regular tracked object so later
            # saves update it in place instead of appending again.
            obj["rose_ifo_index"] = len(lst) - 1
            obj["rose_block_x"] = bx
            obj["rose_block_y"] = by
            obj["rose_is_new"] = False
            added += 1
            written.append(ifo_path)

        # ---------------------------------------------------------------
        # 3. Terrain (HIM) export
        # ---------------------------------------------------------------
        terrain_changed = 0
        terrain_obj = next((o for o in bpy.data.objects if o.get("rose_terrain")), None)
        if terrain_obj and terrain_obj.type == 'MESH' and self.export_terrain:
            grid_scale = zon.grid_size / CM_PER_M
            mesh = terrain_obj.data
            # vertex lookup: (rounded world x, rounded world y) -> height.
            # The terrain mesh lies in the X/Y plane (Z = height) - the
            # same space objects use, so block/sample indices come from X/Y.
            verts = {}
            depsgraph = context.evaluated_depsgraph_get()
            obj_eval = terrain_obj.evaluated_get(depsgraph)
            matrix = obj_eval.matrix_world
            for v in obj_eval.data.vertices:
                w = matrix @ v.co
                key = (round(w.x, 3), round(w.y, 3))
                verts.setdefault(key, w.z)

            for name in sorted(os.listdir(zone_dir)):
                if not name.upper().endswith(".HIM"):
                    continue
                base = name[:-4]
                try:
                    bx, by = (int(p) for p in base.split("_"))
                except ValueError:
                    continue
                him_path = os.path.join(zone_dir, name)
                him = Him(him_path)
                changed = False
                for sy in range(him.length):
                    for sx in range(him.width):
                        wx = BLOCK_SIZE * bx - ZONE_CENTER + sx * grid_scale
                        wy = BLOCK_SIZE * by - ZONE_CENTER + sy * grid_scale
                        h = verts.get((round(wx, 3), round(wy, 3)))
                        if h is None:
                            continue
                        height_cm = h * CM_PER_M
                        if abs(height_cm - him.heights[sy][sx]) > HEIGHT_EPSILON_CM:
                            him.heights[sy][sx] = height_cm
                            changed = True
                if changed:
                    him.save(him_path)
                    written.append(him_path)
                    terrain_changed += 1

        # ---------------------------------------------------------------
        # 4. Backup + write
        # ---------------------------------------------------------------
        if written:
            backup_dir, copied = backup_files(zone_dir, written)
            self.report({'INFO'}, f"backed up {copied} files to {backup_dir}")
        for ifo_path in set(written):
            if ifo_path.upper().endswith(".IFO") and ifo_path in ifo_cache:
                try:
                    ifo_cache[ifo_path].save(ifo_path)
                except Exception as e:
                    errors.append(f"failed to write {os.path.basename(ifo_path)}: {e}")

        if errors:
            for e in errors[:10]:
                self.report({'WARNING'}, e)
            self.report({'ERROR'}, f"save finished with {len(errors)} warning(s)")
            return {'FINISHED'}
        self.report({'INFO'}, f"Saved zone: {updated} updated, {added} added, "
                             f"{deleted} deleted, {terrain_changed} terrain block(s)")
        return {'FINISHED'}


def kind_of(block_type):
    for k, v in IFO_BLOCK.items():
        if v == block_type:
            return k
    return str(block_type)


class MarkZoneObjectDeleted(bpy.types.Operator):
    bl_idname = "rose.mark_zone_deleted"
    bl_label = "Mark Selected for Zone Deletion"
    bl_description = "Remove the selected objects from the zone on next save"

    def execute(self, context):
        scene = context.scene
        entries = []
        if "rose_deleted_objects" in scene:
            try:
                entries = json.loads(scene["rose_deleted_objects"])
            except Exception:
                entries = []
        count = 0
        for obj in context.selected_objects:
            if not (obj.type == 'EMPTY' and "rose_ifo_block" in obj):
                continue
            if obj.get("rose_is_new"):
                # new objects are simply deleted from the scene
                continue
            kind = str(obj["rose_ifo_block"])
            bx, by = int(obj["rose_block_x"]), int(obj["rose_block_y"])
            idx = int(obj["rose_ifo_index"])
            entry = f"{kind}:{bx}:{by}:{idx}"
            if entry not in entries:
                entries.append(entry)
                count += 1
            obj.hide_set(True)
        scene["rose_deleted_objects"] = json.dumps(entries)
        self.report({'INFO'}, f"marked {count} object(s) for deletion")
        return {'FINISHED'}


class AddZoneObject(bpy.types.Operator):
    bl_idname = "rose.add_zone_object"
    bl_label = "Add Selected Mesh as Zone Object"
    bl_description = "Export the selected mesh to the game data (ZMS + ZSC entry) and place it in the zone"

    object_type: EnumProperty(
        name="Object Type",
        items=[('DECO', "Deco Object", "Added to the DECO object list"),
               ('CNST', "Construction Object", "Added to the CNST object list")],
        default='DECO',
    )

    def execute(self, context):
        scene = context.scene
        zone_file = scene.get("rose_zone_file")
        zone_dir = scene.get("rose_zone_dir")
        root_3ddata = scene.get("rose_3ddata_root")
        if not zone_file or not root_3ddata:
            self.report({'ERROR'}, "No zone imported in this scene - import a .zon file first")
            return {'CANCELLED'}

        mesh_obj = context.active_object
        if mesh_obj is None or mesh_obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object")
            return {'CANCELLED'}

        from .export_zms import export_zms_mesh_object

        name = mesh_obj.name.replace(" ", "_")
        rel_mesh_path = f"3Ddata/MODELS/ZMS/{name}.zms"
        abs_mesh_path = os.path.join(root_3ddata, "MODELS", "ZMS", f"{name}.zms")
        os.makedirs(os.path.dirname(abs_mesh_path), exist_ok=True)
        # Mirror into ROSE-local space (x, -y, z): the inverse of the zone
        # importers' mirrored mesh loading, so the game reads back the same
        # bytes the importers consumed.
        error = export_zms_mesh_object(mesh_obj, abs_mesh_path,
                                       convert_coordinates=True,
                                       report=self.report)
        if error:
            self.report({'ERROR'}, f"ZMS export failed: {error}")
            return {'CANCELLED'}

        # Choose the target ZSC
        if self.object_type == 'CNST':
            zsc_path = scene.get("rose_cnst_zsc_path")
            if not zsc_path:
                self.report({'ERROR'}, "No CNST ZSC path recorded - reimport the zone")
                return {'CANCELLED'}
        else:
            deco_paths = scene.get("rose_deco_zsc_paths") or []
            if not deco_paths:
                self.report({'ERROR'}, "No DECO ZSC paths recorded - reimport the zone")
                return {'CANCELLED'}
            zsc_path = deco_paths[0]

        zsc = Zsc(zsc_path)

        # Material + texture
        material_id = 0
        tex_rel = ""
        if mesh_obj.data.materials and mesh_obj.data.materials[0]:
            img = None
            for node in mesh_obj.data.materials[0].node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    img = node.image
                    break
            if img:
                tex_rel = f"3Ddata/Terrain/Tiles/{os.path.basename(os.path.dirname(zsc_path))}/{name}.dds"
                tex_abs = os.path.join(root_3ddata, "Terrain", "Tiles",
                                       os.path.basename(os.path.dirname(zsc_path)),
                                       f"{name}.dds")
                os.makedirs(os.path.dirname(tex_abs), exist_ok=True)
                w, h = img.size
                pixels = bytearray()
                for py in range(h):
                    for px in range(w):
                        r, g, b, a = img.pixels[(py * w + px) * 4:(py * w + px) * 4 + 4]
                        pixels.extend((bytes([int(r * 255), int(g * 255),
                                              int(b * 255), int(a * 255)])))
                write_dds_rgba8(tex_abs, w, h, bytes(pixels))
                mat = ZscMaterial()
                mat.path = tex_rel
                mat.alpha_enabled = False
                zsc.append_material(mat)
                material_id = len(zsc.materials) - 1

        # Append mesh + object
        zsc.append_mesh(rel_mesh_path)
        part = ZscObjectPart()
        part.mesh_id = len(zsc.meshes) - 1
        part.material_id = material_id
        zsc_obj = ZscObject()
        zsc_obj.parts.append(part)
        zsc.append_object(zsc_obj)
        new_object_id = len(zsc.objects) - 1

        # Backup + save ZSC
        backup_files(os.path.dirname(zsc_path), [zsc_path])
        zsc.save(zsc_path)

        # Parent the mesh under a new empty carrying the round-trip metadata
        empty = bpy.data.objects.new(mesh_obj.name + "_ROSE", None)
        empty.empty_display_type = 'PLAIN_AXES'
        empty.empty_display_size = 0.5
        empty.location = (0, 0, 0)
        empty["rose_ifo_block"] = self.object_type
        empty["rose_is_new"] = True
        empty["rose_zsc_object_id"] = new_object_id
        empty["rose_zsc_path"] = zsc_path
        empty["rose_ifo_object_name"] = mesh_obj.name
        context.collection.objects.link(empty)
        mesh_obj.parent = empty

        self.report({'INFO'}, f"Added zone object '{name}' (ZSC object {new_object_id} in "
                             f"{os.path.basename(zsc_path)}). Move the empty to place it; "
                             f"then Save Zone.")
        return {'FINISHED'}


def menu_func_export_zone(self, context):
    self.layout.operator(ExportZone.bl_idname, text="ROSE Zone (.zon) - Save Edited Zone")
    self.layout.operator(AddZoneObject.bl_idname, text="ROSE Object - Add Selected Mesh to Zone")
