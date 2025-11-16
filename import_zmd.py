from pathlib import Path
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
        default="*.zmd",
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

        self.report({'INFO'}, f"Imported {len(zmd.bones)} bones from {filename}")
        return {"FINISHED"}

    def bones_from_zmd(self, zmd, armature):
        """Create Blender bones from ZMD bone data"""
        
        # Create all bones first so parenting can be done later
        for rose_bone in zmd.bones:
            bone = armature.edit_bones.new(rose_bone.name)
            bone.use_connect = False

        # Build world transforms for each bone
        world_positions = []
        world_rotations = []
        
        for idx, rose_bone in enumerate(zmd.bones):
            pos = bmath.Vector(rose_bone.position.as_tuple())
            rot = bmath.Quaternion(rose_bone.rotation.as_tuple(w_first=True))
            
            if rose_bone.parent_id == -1:
                # Root bone - use local transform as world transform
                world_positions.append(pos)
                world_rotations.append(rot)
            else:
                # Child bone - transform by parent's world transform
                parent_pos = world_positions[rose_bone.parent_id]
                parent_rot = world_rotations[rose_bone.parent_id]
                
                # Rotate position by parent rotation, then add parent position
                world_pos = parent_pos + (parent_rot @ pos)
                world_rot = parent_rot @ rot
                
                world_positions.append(world_pos)
                world_rotations.append(world_rot)

        # Now set bone positions and parenting
        for idx, rose_bone in enumerate(zmd.bones):
            bone = armature.edit_bones[idx]
            
            world_pos = world_positions[idx]
            world_rot = world_rotations[idx]

            if rose_bone.parent_id == -1:
                # Root bone
                bone.head = world_pos
                bone.tail = world_pos + bmath.Vector((0, 0, 0.1))
                
                if self.keep_root_bone and bone.length < 0.0001:
                    bone.tail = bone.head + bmath.Vector((0, 0, 0.1))
            else:
                # Child bone
                if rose_bone.parent_id >= len(armature.edit_bones):
                    self.report({'WARNING'}, 
                        f"Invalid parent ID {rose_bone.parent_id} for bone {rose_bone.name}")
                    continue
                
                bone.parent = armature.edit_bones[rose_bone.parent_id]
                bone.head = world_pos
                
                # Set tail to point in direction of rotation
                bone.tail = world_pos + (world_rot @ bmath.Vector((0, 0.1, 0)))
                
                # Ensure minimum bone length
                if bone.length < 0.001:
                    bone.tail = bone.head + bmath.Vector((0, 0.001, 0))


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