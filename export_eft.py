"""
Blender operator for exporting ROSE Online EFT effect files.

Reads an `EFT_<name>` hierarchy created by the EFT importer (root empty
with `eft_is_effect_root`, child slot empties with `eft_slot_type` of
PARTICLE / MESH) and writes a .EFT file.

Round-trip fidelity: when the source file recorded on the root
(`eft_source_file`) still exists, it is re-read and patched in place,
so untouched skip blobs, padding, string encodings and debug-fill flag
values survive verbatim. Slot transforms are only rewritten when the
live Blender transform differs from the imported snapshot (`eft_pos` /
`eft_pitch` / `eft_yaw` / `eft_roll`); otherwise the original floats are
kept bit-for-bit.
"""

import math
from pathlib import Path

import bpy
import mathutils
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Quaternion, Vector

from .rose.eft import Eft, EftMesh, EftParticle
from .import_eft import blender_pos_to_rose_cm, blender_quat_to_rose_euler


def _get_prop(obj, key, default=None):
    try:
        value = obj.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def _slot_local_matrix(root, slot):
    """Slot transform relative to the effect root."""
    try:
        return root.matrix_world.inverted() @ slot.matrix_world
    except Exception:
        return slot.matrix_basis


def _transform_unchanged(slot, local):
    """True when the live transform still matches the import snapshot."""
    try:
        orig_pos = _get_prop(slot, "eft_pos", None)
        loc = local.translation
        if orig_pos is not None:
            if (abs(loc.x * 100.0 - orig_pos[0]) > 1e-4
                    or abs(-loc.y * 100.0 - orig_pos[1]) > 1e-4
                    or abs(loc.z * 100.0 - orig_pos[2]) > 1e-4):
                return False
        else:
            return False
        orig_euler = [_get_prop(slot, "eft_pitch", None),
                      _get_prop(slot, "eft_yaw", None),
                      _get_prop(slot, "eft_roll", None)]
        if any(v is None for v in orig_euler):
            return False
        q_live = local.to_quaternion()
        pitch, yaw, roll = blender_quat_to_rose_euler(q_live)
        return (abs(pitch - orig_euler[0]) < 1e-4
                and abs(yaw - orig_euler[1]) < 1e-4
                and abs(roll - orig_euler[2]) < 1e-4)
    except Exception:
        return False


def _write_transform(entry, slot, local):
    """Write live transform into an EFT entry; snapshot props are updated."""
    x, y, z = blender_pos_to_rose_cm(local.translation)
    pitch, yaw, roll = blender_quat_to_rose_euler(local.to_quaternion())
    entry.position.x = x
    entry.position.y = y
    entry.position.z = z
    entry.pitch = pitch
    entry.yaw = yaw
    entry.roll = roll
    try:
        slot["eft_pos"] = [float(x), float(y), float(z)]
        slot["eft_pitch"] = float(pitch)
        slot["eft_yaw"] = float(yaw)
        slot["eft_roll"] = float(roll)
    except Exception:
        pass


def _sync_skip_blob(entry, slot):
    for attr, prop in (("skip_a", "eft_skip_a"), ("skip_b", "eft_skip_b")):
        try:
            text = slot.get(prop, None)
        except Exception:
            text = None
        if text is None:
            continue
        from .rose.eft import _split_text_raw

        if _split_text_raw(getattr(entry, attr)) != str(text):
            setattr(entry, attr, str(text).encode("utf-8"))


def _sync_anim_flag(entry, new_anim_text):
    """Keep the raw use_animation flag unless path presence changed."""
    from .rose.eft import _split_text_raw

    old_text = _split_text_raw(entry.animation_file_raw)
    old_effective = old_text and old_text != "NULL" and entry.use_animation != 0
    new_effective = bool(new_anim_text and new_anim_text != "NULL")
    if bool(old_effective) == new_effective:
        return
    entry.use_animation = 1 if new_effective else 0


class ExportEFT(bpy.types.Operator, ExportHelper):
    """Export ROSE Online effect file (.eft)"""

    bl_idname = "rose.export_eft"
    bl_label = "Export ROSE Effect (.eft)"
    bl_options = {"PRESET"}

    filename_ext = ".eft"
    filter_glob: StringProperty(default="*.eft", options={"HIDDEN"})

    effect_root: StringProperty(
        name="Effect Root",
        description="Name of the EFT_ root empty (leave empty to auto-detect from selection)",
        default="",
    )

    # -- operator --------------------------------------------------------
    def execute(self, context):
        root = self._find_root(context)
        if root is None:
            self.report({"ERROR"},
                        "No EFT effect root found. Select the EFT_ empty "
                        "or one of its slots.")
            return {"CANCELLED"}

        source = _get_prop(root, "eft_source_file", "")
        eft = None
        if source and Path(source).is_file():
            try:
                eft = Eft(str(source))
            except Exception as e:
                self.report({"WARNING"},
                            f"Could not re-read source {source}: {e}")
                eft = None
        if eft is None:
            eft = Eft()
            eft.use_sound = 1 if _get_prop(root, "eft_sound_file", "") else 0

        particle_slots = []
        mesh_slots = []
        for child in root.children:
            stype = _get_prop(child, "eft_slot_type", "")
            if stype == "PARTICLE":
                particle_slots.append(child)
            elif stype == "MESH":
                mesh_slots.append(child)
        particle_slots.sort(key=lambda o: int(_get_prop(o, "eft_slot_index", 0)))
        mesh_slots.sort(key=lambda o: int(_get_prop(o, "eft_slot_index", 0)))

        eft.particles = self._sync_particles(particle_slots, eft.particles)
        eft.meshes = self._sync_meshes(mesh_slots, eft.meshes)

        # Sound block.
        new_sound = str(_get_prop(root, "eft_sound_file", eft.sound_file))
        if new_sound != eft.sound_file:
            eft.sound_file = new_sound
            eft.sound_file_raw = None
        try:
            eft.sound_repeat_count = int(_get_prop(
                root, "eft_sound_repeat", eft.sound_repeat_count))
        except (TypeError, ValueError):
            pass
        old_sound_raw = eft.sound_file_raw
        if old_sound_raw is not None:
            from .rose.eft import _split_text_raw

            old_text = _split_text_raw(old_sound_raw)
            if old_text != eft.sound_file:
                eft.use_sound = 1 if (eft.sound_file and eft.sound_file != "NULL") else 0
        else:
            eft.use_sound = 1 if (eft.sound_file and eft.sound_file != "NULL") else 0

        try:
            with open(self.filepath, "wb") as f:
                eft.write(f)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to write EFT file: {e}")
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"Exported {Path(self.filepath).name} "
                    f"({len(eft.particles)} particle(s), {len(eft.meshes)} mesh(es))")
        return {"FINISHED"}

    # -- root lookup -----------------------------------------------------
    def _find_root(self, context):
        if self.effect_root:
            obj = bpy.data.objects.get(self.effect_root)
            if obj is not None and _get_prop(obj, "eft_is_effect_root", False):
                return obj
        candidates = []
        active = context.view_layer.objects.active if context.view_layer else None
        if active is not None:
            candidates.append(active)
        try:
            candidates.extend(list(context.selected_objects))
        except Exception:
            pass
        for obj in candidates:
            if obj is None:
                continue
            if _get_prop(obj, "eft_is_effect_root", False):
                return obj
            parent = obj.parent
            while parent is not None:
                if _get_prop(parent, "eft_is_effect_root", False):
                    return parent
                parent = parent.parent
        for obj in bpy.data.objects:
            if _get_prop(obj, "eft_is_effect_root", False):
                return obj
        return None

    # -- slot sync -------------------------------------------------------
    def _sync_particles(self, slots, old_entries):
        entries = []
        for i, slot in enumerate(slots):
            entry = old_entries[i] if i < len(old_entries) else EftParticle()
            self._sync_particle_entry(slot, entry)
            entries.append(entry)
        return entries

    def _sync_meshes(self, slots, old_entries):
        entries = []
        for i, slot in enumerate(slots):
            entry = old_entries[i] if i < len(old_entries) else EftMesh()
            self._sync_mesh_entry(slot, entry)
            entries.append(entry)
        return entries

    def _sync_particle_entry(self, slot, entry):
        root = slot.parent
        local = _slot_local_matrix(root, slot) if root else slot.matrix_basis
        if not _transform_unchanged(slot, local):
            _write_transform(entry, slot, local)
        _sync_skip_blob(entry, slot)

        new_ptl = str(_get_prop(slot, "eft_particle_file", entry.particle_path()))
        if new_ptl != entry.particle_path():
            entry.particle_file = new_ptl
            entry.particle_file_raw = None
        new_anim = str(_get_prop(slot, "eft_anim_file", entry.animation_path()))
        if new_anim != entry.animation_path():
            _sync_anim_flag(entry, new_anim)
            entry.animation_file = new_anim
            entry.animation_file_raw = None
        try:
            entry.animation_repeat_count = int(_get_prop(
                slot, "eft_anim_repeat", entry.animation_repeat_count))
            entry.start_delay = int(_get_prop(
                slot, "eft_start_delay_ms", entry.start_delay))
        except (TypeError, ValueError):
            pass
        entry.is_linked = bool(_get_prop(slot, "eft_is_linked", entry.is_linked))

    def _sync_mesh_entry(self, slot, entry):
        root = slot.parent
        local = _slot_local_matrix(root, slot) if root else slot.matrix_basis
        if not _transform_unchanged(slot, local):
            _write_transform(entry, slot, local)
        _sync_skip_blob(entry, slot)

        # NOTE: path props hold *effective* paths ("" when the file stores
        # "NULL"), so compare against the effective-path helpers. Comparing
        # against the raw text would clobber "NULL" with "" on every save.
        new_mesh = str(_get_prop(slot, "eft_mesh_file", entry.mesh_file))
        if new_mesh != entry.mesh_file:
            entry.mesh_file = new_mesh
            entry.mesh_file_raw = None
        new_morph = str(_get_prop(slot, "eft_mesh_anim_file",
                                 entry.mesh_animation_path()))
        if new_morph != entry.mesh_animation_path():
            entry.mesh_animation_file = new_morph
            entry.mesh_animation_file_raw = None
        new_tex = str(_get_prop(slot, "eft_mesh_texture", entry.texture_path()))
        if new_tex != entry.texture_path():
            entry.mesh_texture_file = new_tex
            entry.mesh_texture_file_raw = None
        # Transform-anim flag follows path presence (mesh morph has no flag).
        new_anim = str(_get_prop(slot, "eft_anim_file", entry.animation_path()))
        if new_anim != entry.animation_path():
            _sync_anim_flag(entry, new_anim)
            entry.animation_file = new_anim
            entry.animation_file_raw = None

        for prop, attr in (("eft_alpha_enabled", "alpha_enabled"),
                           ("eft_two_sided", "two_sided"),
                           ("eft_alpha_test", "alpha_test_enabled"),
                           ("eft_depth_test", "depth_test_enabled"),
                           ("eft_depth_write", "depth_write_enabled"),
                           ("eft_is_linked", "is_linked")):
            setattr(entry, attr, bool(_get_prop(slot, prop, getattr(entry, attr))))
        for prop, attr in (("eft_src_blend", "src_blend_factor"),
                           ("eft_dst_blend", "dst_blend_factor"),
                           ("eft_blend_op", "blend_op"),
                           ("eft_anim_repeat", "animation_repeat_count"),
                           ("eft_start_delay_ms", "start_delay"),
                           ("eft_repeat_count", "repeat_count")):
            try:
                setattr(entry, attr, int(_get_prop(slot, prop, getattr(entry, attr))))
            except (TypeError, ValueError):
                pass


def menu_func_export(self, context):
    self.layout.operator(ExportEFT.bl_idname, text="ROSE Effect (.eft)")


def register():
    bpy.utils.register_class(ExportEFT)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.utils.unregister_class(ExportEFT)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)


if __name__ == "__main__":
    register()
