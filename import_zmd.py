from pathlib import Path
import math
import bpy
import mathutils as bmath
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper

from .rose.zmd import ZMD


class ImportZMD(bpy.types.Operator, ImportHelper):
    """Import ROSE Online ZMD armature file"""
    bl_idname = "rose.import_zmd"
    bl_label = "ROSE Armature (.zmd)"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".zmd"
    filter_glob: StringProperty(
        default="*.zmd;*.ZMD",
        options={"HIDDEN"}
    )

    find_animations: BoolProperty(
        name="Find Animations",
        description=(
            "Recursively load any animations (ZMOs) from current "
            "directory with this armature"
        ),
        default=True,
    )

    keep_root_bone: BoolProperty(
        name="Keep Root bone",
        description=(
            "Prevent Blender from automatically removing the root bone"
        ),
        default=True,
    )

    animation_extensions = [".ZMO", ".zmo"]

    def execute(self, context):
        filepath = Path(self.filepath)
        filename = filepath.stem
        
        try:
            zmd = ZMD(str(filepath))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load ZMD file: {str(e)}")
            return {"CANCELLED"}

        # Create armature and object
        armature = bpy.data.armatures.new(filename)
        obj = bpy.data.objects.new(filename, armature)

        # Link to scene collection
        context.collection.objects.link(obj)
        
        # Set as active and selected
        context.view_layer.objects.active = obj
        obj.select_set(True)

        # Enter edit mode to create bones
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            self.bones_from_zmd(zmd, armature)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create bones: {str(e)}")
            bpy.ops.object.mode_set(mode='OBJECT')
            return {"CANCELLED"}

        bpy.ops.object.mode_set(mode='OBJECT')

        # Bone head/tail/roll cannot store an arbitrary rotation frame
        # directly, so solve the roll per bone so matrix_local matches the
        # ZMD rest transform (Blender deforms with pose @ rest^-1 while the
        # engine uses pose @ bind^-1; a mismatched rest corrupts animation).
        self._align_rest_to_zmd(zmd, obj)

        self.report({'INFO'}, f"Imported {len(zmd.bones)} bones "
                              f"({len(zmd.dummies)} dummies) from {filename}")
        return {"FINISHED"}

    def _zmd_entries(self, zmd):
        """Flattened (name, parent, pos, rot) list: bones then dummies.

        Dummy parents index into the bone array (dummies are appended after
        bones in the engine joint list), so parent indices < len(bones)
        resolve to bones while anything else is treated as root.
        """
        entries = []
        for rose_bone in zmd.bones:
            entries.append((rose_bone.name, rose_bone.parent_id,
                            bmath.Vector(rose_bone.position.as_tuple()),
                            bmath.Quaternion(rose_bone.rotation.as_tuple(w_first=True))))
        n_bones = len(zmd.bones)
        for dummy in zmd.dummies:
            parent = dummy.parent_id
            if parent < 0 or parent >= n_bones:
                self.report({'WARNING'},
                    f"Invalid parent ID {dummy.parent_id} for dummy {dummy.name}; "
                    f"attaching to root")
                parent = -1
            entries.append((dummy.name, parent,
                            bmath.Vector(dummy.position.as_tuple()),
                            bmath.Quaternion(dummy.rotation.as_tuple(w_first=True))))
        return entries

    def _world_transforms(self, zmd):
        """Compose world transforms, independent of bone order in the file.

        The ZMD format imposes no parent-before-child ordering, so parents
        are resolved iteratively instead of assuming parent_id < idx.
        Invalid bone parents fall back to root with a warning.
        """
        entries = self._zmd_entries(zmd)
        n_bones = len(zmd.bones)
        world_positions = [bmath.Vector((0.0, 0.0, 0.0)) for _ in entries]
        world_rotations = [bmath.Quaternion((1.0, 0.0, 0.0, 0.0)) for _ in entries]

        def valid_parent(idx, parent):
            # Bones reference bones; dummies reference bones only.
            limit = n_bones if idx >= n_bones else len(entries)
            return parent is not None and 0 <= parent < limit and parent != idx

        # Roots first, then iteratively resolve children whose parent is done.
        # A full pass with no progress means a cycle; attach the remainder
        # to the root rather than failing the whole import.
        resolved = set()
        for idx, (_name, parent, pos, rot) in enumerate(entries):
            if parent == -1 or not valid_parent(idx, parent):
                if parent != -1 and parent is not None:
                    self.report({'WARNING'},
                        f"Invalid parent ID {parent} for bone {_name}; "
                        f"attaching to root")
                world_positions[idx] = pos
                world_rotations[idx] = rot
                resolved.add(idx)

        remaining = set(range(len(entries))) - resolved
        while remaining:
            progress = False
            for idx in list(remaining):
                _name, parent, pos, rot = entries[idx]
                if valid_parent(idx, parent) and parent in resolved:
                    world_positions[idx] = world_positions[parent] + (world_rotations[parent] @ pos)
                    world_rotations[idx] = world_rotations[parent] @ rot
                    resolved.add(idx)
                    remaining.discard(idx)
                    progress = True
            if not progress:
                for idx in list(remaining):
                    _name, _parent, pos, rot = entries[idx]
                    self.report({'WARNING'},
                        f"Parent cycle involving bone {_name}; attaching to root")
                    world_positions[idx] = pos
                    world_rotations[idx] = rot
                    resolved.add(idx)
                    remaining.discard(idx)

        return entries, world_positions, world_rotations

    def bones_from_zmd(self, zmd, armature):
        """Create Blender bones from ZMD bone + dummy data"""

        entries, world_positions, world_rotations = self._world_transforms(zmd)

        # Create all bones first so parenting can be done later.
        # Keep our own references: Blender re-sorts edit bones into hierarchy
        # order on mode re-entry, so integer indices are only valid here.
        created = []
        for (name, _parent, _pos, _rot) in entries:
            bone = armature.edit_bones.new(name)
            bone.use_connect = False
            created.append(bone)

        # Now set bone positions and parenting
        for idx, (name, parent, _pos, _rot) in enumerate(entries):
            bone = created[idx]

            world_pos = world_positions[idx]
            world_rot = world_rotations[idx]

            if parent != -1 and 0 <= parent < len(created):
                bone.parent = created[parent]

            # Set tail to point in direction of rotation
            bone.head = world_pos
            bone.tail = world_pos + (world_rot @ bmath.Vector((0, 0.1, 0)))

            # Ensure minimum bone length
            if bone.length < 0.001:
                if parent == -1 and self.keep_root_bone:
                    bone.tail = bone.head + bmath.Vector((0, 0, 0.1))
                    if bone.length < 0.0001:
                        bone.tail = bone.head + bmath.Vector((0, 0.001, 0))
                else:
                    bone.tail = bone.head + bmath.Vector((0, 0.001, 0))

    def _align_rest_to_zmd(self, zmd, armature_obj):
        """Set bone roll so matrix_local matches the ZMD rest transform.

        A Blender bone only stores direction + roll (5 DOF) while ZMD gives
        a full rotation, so after the first pass (roll=0) the required roll
        is the signed angle around the bone Y axis from the base X axis to
        the ZMD rest X axis. Covers bones and dummies alike.
        """
        entries, world_positions, world_rotations = self._world_transforms(zmd)

        bones = armature_obj.data.bones
        rolls = {}
        for idx, (name, parent, _pos, _rot) in enumerate(entries):
            bone = bones.get(name)
            if bone is None:
                continue
            if parent == -1 or parent < 0 or parent >= len(entries):
                parent_local = bmath.Matrix.Identity(4)
            else:
                pb = bones.get(entries[parent][0])
                parent_local = pb.matrix_local if pb else bmath.Matrix.Identity(4)
            # Desired local rotation (ZMD), and actual local frame at roll=0
            desired = parent_local.to_3x3().inverted() @ world_rotations[idx].to_matrix()
            actual = parent_local.to_3x3().inverted() @ bone.matrix_local.to_3x3()
            d = actual.col[1].normalized()
            x0 = actual.col[0].normalized()
            xt = (desired @ bmath.Vector((1, 0, 0))).normalized()
            xt = xt - xt.dot(d) * d
            if xt.length < 1e-6:
                continue
            xt.normalize()
            rolls[name] = math.atan2(x0.cross(xt).dot(d), x0.dot(xt))

        if not rolls:
            return
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            # Look up by name, never by creation index: Blender re-sorts edit
            # bones into hierarchy (parent-before-child) order on mode
            # re-entry, so index-based access scrambles rolls onto wrong bones
            # once dummies (created last, parented early) are in the mix.
            edit_bones = armature_obj.data.edit_bones
            for name, roll in rolls.items():
                edit_bone = edit_bones.get(name)
                if edit_bone is None:
                    self.report({'WARNING'},
                        f"Bone '{name}' missing when applying rest roll")
                    continue
                edit_bone.roll = roll
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')


def menu_func_import(self, context):
    self.layout.operator(ImportZMD.bl_idname, text="ROSE Armature (.zmd)")


def register():
    bpy.utils.register_class(ImportZMD)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportZMD)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
    register()