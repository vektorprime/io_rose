"""
Blender operator for importing ROSE Online ZMO animation files.

ZMO files contain skeletal animation data that can be applied to armatures
imported from ZMD files.
"""

from pathlib import Path
import bpy
import mathutils
from bpy.props import StringProperty, IntProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper

from .rose.zmo import ZMO, ZmoChannelType, ZmoPositionChannel, ZmoRotationChannel, ZmoScaleChannel


class ImportZMO(bpy.types.Operator, ImportHelper):
    """Import ROSE Online ZMO animation file"""
    bl_idname = "rose.import_zmo"
    bl_label = "ROSE Animation (.zmo)"
    bl_options = {"PRESET", "UNDO"}
    
    filename_ext = ".zmo"
    filter_glob: StringProperty(
        default="*.zmo;*.ZMO",
        options={"HIDDEN"}
    )
    
    target_armature: StringProperty(
        name="Target Armature",
        description="Name of the armature object to apply animation to (leave empty to auto-detect)",
        default="",
    )
    
    scale_factor: FloatProperty(
        name="Scale Factor",
        description="Scale factor for position keyframes (ROSE uses centimeters, default 0.01 converts to meters)",
        default=0.01,
        min=0.0001,
        max=100.0,
    )
    
    start_frame: IntProperty(
        name="Start Frame",
        description="Frame number to start the animation at",
        default=1,
        min=1,
    )
    
    def execute(self, context):
        filepath = Path(self.filepath)
        filename = filepath.stem
        
        # Create a report function wrapper
        def report_wrapper(level, message):
            self.report({level}, message)
        
        # Load ZMO file
        try:
            zmo = ZMO(str(filepath), report_func=report_wrapper)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load ZMO file: {str(e)}")
            return {'CANCELLED'}
        
        self.report({'INFO'}, f"ZMO: {zmo.fps} FPS, {zmo.num_frames} frames, {len(zmo.channels)} channels")
        
        # Find or create target armature
        armature_obj = self._get_target_armature(context)
        if armature_obj is None:
            self.report({'ERROR'}, "No armature found. Please select an armature or specify a target.")
            return {'CANCELLED'}
        
        # Create animation action
        action = bpy.data.actions.new(name=filename)
        action.use_fake_user = True
        
        # Set frame rate
        context.scene.render.fps = zmo.fps
        context.scene.frame_start = self.start_frame
        context.scene.frame_end = self.start_frame + zmo.num_frames - 1
        
        # Apply animation to armature
        self._apply_animation(armature_obj, action, zmo)
        
        # Assign action to armature
        if not armature_obj.animation_data:
            armature_obj.animation_data_create()
        armature_obj.animation_data.action = action
        
        self.report({'INFO'}, f"Imported {filename} ({zmo.num_frames} frames @ {zmo.fps} FPS)")
        return {"FINISHED"}
    
    def _get_target_armature(self, context):
        """Get the target armature object."""
        if self.target_armature:
            obj = bpy.data.objects.get(self.target_armature)
            if obj and obj.type == 'ARMATURE':
                return obj
        
        active = context.active_object
        if active and active.type == 'ARMATURE':
            return active
        
        for obj in context.selected_objects:
            if obj.type == 'ARMATURE':
                return obj
        
        for obj in context.scene.objects:
            if obj.type == 'ARMATURE':
                return obj
        
        return None
    
    def _apply_animation(self, armature_obj, action, zmo):
        """Apply ZMO animation data to armature."""
        bone_names = [bone.name for bone in armature_obj.data.bones]
        bone_channels = zmo.get_bone_channels()
        
        for bone_index, channels in bone_channels.items():
            if bone_index >= len(bone_names):
                continue
            
            bone_name = bone_names[bone_index]
            
            pos_channel = channels.get(ZmoChannelType.POSITION)
            if pos_channel and isinstance(pos_channel, ZmoPositionChannel):
                self._apply_position_channel(action, bone_name, pos_channel)
            
            rot_channel = channels.get(ZmoChannelType.ROTATION)
            if rot_channel and isinstance(rot_channel, ZmoRotationChannel):
                self._apply_rotation_channel(action, bone_name, rot_channel)
            
            scale_channel = channels.get(ZmoChannelType.SCALE)
            if scale_channel and isinstance(scale_channel, ZmoScaleChannel):
                self._apply_scale_channel(action, bone_name, scale_channel)
    
    def _apply_position_channel(self, action, bone_name, channel):
        """Apply position keyframes to a bone."""
        data_path = f'pose.bones["{bone_name}"].location'
        
        fcurves = []
        for i in range(3):
            fcurve = action.fcurves.find(data_path, index=i)
            if not fcurve:
                fcurve = action.fcurves.new(data_path, index=i)
            fcurves.append(fcurve)
        
        for frame_idx, pos in enumerate(channel.values):
            frame = self.start_frame + frame_idx
            
            # Scale positions from cm to m
            x = pos.x * self.scale_factor
            y = pos.y * self.scale_factor
            z = pos.z * self.scale_factor
            
            fcurves[0].keyframe_points.insert(frame, x)
            fcurves[1].keyframe_points.insert(frame, y)
            fcurves[2].keyframe_points.insert(frame, z)
    
    def _apply_rotation_channel(self, action, bone_name, channel):
        """Apply rotation keyframes to a bone."""
        data_path = f'pose.bones["{bone_name}"].rotation_quaternion'
        
        fcurves = []
        for i in range(4):
            fcurve = action.fcurves.find(data_path, index=i)
            if not fcurve:
                fcurve = action.fcurves.new(data_path, index=i)
            fcurves.append(fcurve)
        
        for frame_idx, quat in enumerate(channel.values):
            frame = self.start_frame + frame_idx
            
            # Use quaternion directly from ZMO (WXYZ order)
            w, x, y, z = quat.w, quat.x, quat.y, quat.z
            
            # Normalize
            length = (w*w + x*x + y*y + z*z) ** 0.5
            if length > 0:
                w /= length
                x /= length
                y /= length
                z /= length
            
            fcurves[0].keyframe_points.insert(frame, w)
            fcurves[1].keyframe_points.insert(frame, x)
            fcurves[2].keyframe_points.insert(frame, y)
            fcurves[3].keyframe_points.insert(frame, z)
    
    def _apply_scale_channel(self, action, bone_name, channel):
        """Apply scale keyframes to a bone."""
        data_path = f'pose.bones["{bone_name}"].scale'
        
        fcurves = []
        for i in range(3):
            fcurve = action.fcurves.find(data_path, index=i)
            if not fcurve:
                fcurve = action.fcurves.new(data_path, index=i)
            fcurves.append(fcurve)
        
        for frame_idx, scale in enumerate(channel.values):
            frame = self.start_frame + frame_idx
            
            fcurves[0].keyframe_points.insert(frame, scale)
            fcurves[1].keyframe_points.insert(frame, scale)
            fcurves[2].keyframe_points.insert(frame, scale)


def menu_func_import(self, context):
    self.layout.operator(ImportZMO.bl_idname, text="ROSE Animation (.zmo)")


def register():
    bpy.utils.register_class(ImportZMO)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportZMO)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
    register()
