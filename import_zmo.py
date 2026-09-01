"""
Blender operator for importing ROSE Online ZMO animation files.

ZMO files contain skeletal animation data that can be applied to armatures
imported from ZMD files.
"""

from pathlib import Path
import bpy
import mathutils
from mathutils import Quaternion, Vector, Matrix
from bpy.props import StringProperty, IntProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper

from .rose.zmo import ZMO, ZmoChannelType, ZmoPositionChannel, ZmoRotationChannel, ZmoScaleChannel
from .rose.zmd import ZMD


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

    zmd_filepath: StringProperty(
        name="Source Armature (.zmd)",
        description="ZMD file the target armature was imported from. Required to convert "
                    "ZMO absolute local bone transforms into Blender pose space. "
                    "Auto-detected from the armature if imported with the combined importer",
        subtype='FILE_PATH',
        default="",
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
        
        # ZMO rotation/position channels are absolute local transforms in
        # parent space that REPLACE the ZMD rest transform. To convert them
        # into Blender pose-space keyframes we need the source ZMD.
        zmd = self._load_source_zmd(armature_obj)

        # Apply animation to armature
        self._apply_animation(armature_obj, action, zmo, zmd)
        
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

    def _load_source_zmd(self, armature_obj):
        """Locate the ZMD file the target armature was imported from."""
        candidates = []
        if self.zmd_filepath:
            candidates.append(Path(bpy.path.abspath(self.zmd_filepath)))
        stored = armature_obj.get("zmd_path")
        if stored:
            candidates.append(Path(stored))

        for path in candidates:
            if path.is_file():
                try:
                    zmd = ZMD(str(path))
                    self.report({'INFO'},
                        f"ZMO: using source armature {path.name} ({len(zmd.bones)} bones)")
                    return zmd
                except Exception as e:
                    self.report({'WARNING'}, f"ZMO: failed to load ZMD {path.name}: {e}")
        return None

    def _compute_world_matrices(self, zmd, bone_channels, frame_idx):
        """Compute per-bone world transforms for one frame, ROSE-style.

        local[i] = T(position) @ Q(rotation) @ S(scale) where each component
        comes from the ZMO channel when present (positions in cm, scaled to
        scene units) or falls back to the ZMD rest transform.
        world[i] = world[parent] @ local[i].
        """
        num_bones = len(zmd.bones)
        world = [None] * num_bones

        def local(i):
            bone = zmd.bones[i]
            pos = Vector(bone.position.as_tuple())
            quat = Quaternion(bone.rotation.as_tuple(w_first=True))
            scale = 1.0
            chs = bone_channels.get(i)
            if chs:
                pch = chs.get(ZmoChannelType.POSITION)
                if pch is not None and frame_idx < len(pch.values):
                    v = pch.values[frame_idx]
                    pos = Vector((v.x, v.y, v.z)) * self.scale_factor
                rch = chs.get(ZmoChannelType.ROTATION)
                if rch is not None and frame_idx < len(rch.values):
                    q = rch.values[frame_idx]
                    quat = Quaternion((q.w, q.x, q.y, q.z))
                sch = chs.get(ZmoChannelType.SCALE)
                if sch is not None and frame_idx < len(sch.values):
                    scale = sch.values[frame_idx]
            return (Matrix.Translation(pos)
                    @ quat.normalized().to_matrix().to_4x4()
                    @ Matrix.Diagonal((scale, scale, scale, 1.0)))

        locals_arr = [None] * num_bones

        def resolve(i, visiting):
            if world[i] is not None:
                return world[i]
            if i in visiting:
                return local(i)  # break cycles
            visiting.add(i)
            loc_mat = local(i)
            locals_arr[i] = loc_mat
            parent = zmd.bones[i].parent_id
            if 0 <= parent < num_bones and parent != i:
                world[i] = resolve(parent, visiting) @ loc_mat
            else:
                world[i] = loc_mat
            return world[i]

        worlds = [resolve(i, set()) for i in range(num_bones)]
        return worlds, locals_arr

    def _apply_animation_matrix(self, armature_obj, action, zmo, zmd):
        """Keyframe the armature by composing ROSE bone transforms per frame.

        The ROSE engine treats ZMO channels as absolute local transforms that
        REPLACE the ZMD rest transform. Blender's pose recursion is
            pose[i] = pose[parent] @ bone[parent].matrix_local.inverted()
                      @ bone[i].matrix_local @ matrix_basis
        so the per-frame basis that realizes a target ROSE local transform L is:
            matrix_basis = bone.matrix_local.inverted()
                           @ parent.matrix_local @ L
        which is then decomposed into rotation_quaternion / location / scale
        keyframes.
        """
        bone_names = [bone.name for bone in armature_obj.data.bones]
        num_bones = min(len(zmd.bones), len(bone_names))
        if len(zmd.bones) > len(bone_names):
            self.report({'WARNING'},
                f"ZMO: ZMD has {len(zmd.bones)} bones but armature has "
                f"{len(bone_names)} - extra bones skipped")

        bone_channels = zmo.get_bone_channels()
        relevant = {ZmoChannelType.POSITION, ZmoChannelType.ROTATION, ZmoChannelType.SCALE}

        for pose_bone in armature_obj.pose.bones:
            pose_bone.rotation_mode = 'QUATERNION'

        def get_fcurve(data_path, index):
            fcurve = action.fcurves.find(data_path, index=index)
            if fcurve is None:
                fcurve = action.fcurves.new(data_path, index=index)
            return fcurve

        def key_vector(data_path, values, frame):
            for axis, value in enumerate(values):
                get_fcurve(data_path, axis).keyframe_points.insert(frame, value)

        prev_quats = {}
        for frame_idx in range(zmo.num_frames):
            frame = self.start_frame + frame_idx
            world, locals_arr = self._compute_world_matrices(zmd, bone_channels, frame_idx)

            for i in range(num_bones):
                chs = bone_channels.get(i)
                if not chs or not (chs.keys() & relevant):
                    continue

                bone_name = bone_names[i]
                pose_bone = armature_obj.pose.bones.get(bone_name)
                if pose_bone is None:
                    continue

                bone = armature_obj.data.bones[bone_name]
                if bone.parent is not None and zmd.bones[i].parent_id >= 0 \
                        and bone.parent.name != bone_names[zmd.bones[i].parent_id]:
                    self.report({'WARNING'},
                        f"ZMO: parent of {bone_name} differs between armature "
                        f"({bone.parent.name}) and ZMD ({bone_names[zmd.bones[i].parent_id]})")
                parent_rest = (bone.parent.matrix_local if bone.parent is not None
                               else Matrix.Identity(4))
                basis = bone.matrix_local.inverted() @ parent_rest @ locals_arr[i]
                loc, quat, scale = basis.decompose()

                # Keep quaternion hemisphere continuous to avoid interpolation spins
                prev = prev_quats.get(bone_name)
                if prev is not None and Vector(quat).dot(Vector(prev)) < 0.0:
                    quat = -quat
                prev_quats[bone_name] = quat.copy()

                key_vector(f'pose.bones["{bone_name}"].location', loc, frame)
                key_vector(f'pose.bones["{bone_name}"].rotation_quaternion', quat, frame)
                key_vector(f'pose.bones["{bone_name}"].scale', scale, frame)

    def _apply_animation(self, armature_obj, action, zmo, zmd=None):
        """Apply ZMO animation data to armature.

        Preferred path: ZMD-aware matrix composition (see _apply_animation_matrix).
        The legacy raw-keyframe path below is only used as a fallback when no
        source ZMD is available, and assumes the ZMO rotations happen to be
        deltas relative to the rest pose (usually false -> broken poses).
        """
        if zmd is not None:
            self._apply_animation_matrix(armature_obj, action, zmo, zmd)
            return

        self.report({'WARNING'},
            "ZMO: no source ZMD found - using legacy raw keyframes. Import the "
            "mesh via 'ROSE Mesh with Skeleton' or set 'Source Armature (.zmd)' "
            "for correct pose-space mapping.")
        bone_names = [bone.name for bone in armature_obj.data.bones]
        bone_channels = zmo.get_bone_channels()
        
        # Debug logging
        print(f"=== ZMO Animation Debug ===")
        print(f"Armature: {armature_obj.name}")
        print(f"Number of bones in armature: {len(bone_names)}")
        print(f"Number of bone channels in ZMO: {len(bone_channels)}")
        
        # Ensure all pose bones use quaternion rotation mode
        for pose_bone in armature_obj.pose.bones:
            pose_bone.rotation_mode = 'QUATERNION'
        
        for bone_index, channels in bone_channels.items():
            if bone_index >= len(bone_names):
                print(f"WARNING: bone_index {bone_index} >= len(bone_names) {len(bone_names)}")
                continue
            
            bone_name = bone_names[bone_index]
            
            # Debug: Log first frame data for each bone
            pos_channel = channels.get(ZmoChannelType.POSITION)
            rot_channel = channels.get(ZmoChannelType.ROTATION)
            
            if rot_channel and isinstance(rot_channel, ZmoRotationChannel):
                first_rot = rot_channel.values[0] if rot_channel.values else None
                if first_rot:
                    print(f"Bone {bone_index} ({bone_name}): first rotation = ({first_rot.w:.4f}, {first_rot.x:.4f}, {first_rot.y:.4f}, {first_rot.z:.4f})")
            
            if pos_channel and isinstance(pos_channel, ZmoPositionChannel):
                first_pos = pos_channel.values[0] if pos_channel.values else None
                if first_pos:
                    print(f"Bone {bone_index} ({bone_name}): first position = ({first_pos.x:.4f}, {first_pos.y:.4f}, {first_pos.z:.4f})")
            
            if pos_channel and isinstance(pos_channel, ZmoPositionChannel):
                self._apply_position_channel(action, bone_name, pos_channel)
            
            if rot_channel and isinstance(rot_channel, ZmoRotationChannel):
                self._apply_rotation_channel(action, bone_name, rot_channel)
            
            scale_channel = channels.get(ZmoChannelType.SCALE)
            if scale_channel and isinstance(scale_channel, ZmoScaleChannel):
                self._apply_scale_channel(action, bone_name, scale_channel)
        
        print(f"=== End ZMO Animation Debug ===")
    
    def _apply_position_channel(self, action, bone_name, channel):
        """Apply position keyframes to a bone.
        
        NO coordinate transformation - use raw ROSE coordinates to match the skeleton.
        Scale factor is applied (default 0.01 for cm to m).
        """
        data_path = f'pose.bones["{bone_name}"].location'
        
        fcurves = []
        for i in range(3):
            fcurve = action.fcurves.find(data_path, index=i)
            if not fcurve:
                fcurve = action.fcurves.new(data_path, index=i)
            fcurves.append(fcurve)
        
        for frame_idx, pos in enumerate(channel.values):
            frame = self.start_frame + frame_idx
            
            # Use raw ROSE coordinates (no transform) to match skeleton
            x = pos.x * self.scale_factor
            y = pos.y * self.scale_factor
            z = pos.z * self.scale_factor
            
            fcurves[0].keyframe_points.insert(frame, x)
            fcurves[1].keyframe_points.insert(frame, y)
            fcurves[2].keyframe_points.insert(frame, z)
    
    def _apply_rotation_channel(self, action, bone_name, channel):
        """Apply rotation keyframes to a bone.
        
        NO coordinate transformation - use raw ROSE coordinates to match the skeleton.
        """
        data_path = f'pose.bones["{bone_name}"].rotation_quaternion'
        
        fcurves = []
        for i in range(4):
            fcurve = action.fcurves.find(data_path, index=i)
            if not fcurve:
                fcurve = action.fcurves.new(data_path, index=i)
            fcurves.append(fcurve)
        
        for frame_idx, quat in enumerate(channel.values):
            frame = self.start_frame + frame_idx
            
            # Use raw ROSE quaternion (no transform) to match skeleton
            # mathutils Quaternion expects (w, x, y, z)
            w = quat.w
            x = quat.x
            y = quat.y
            z = quat.z
            
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
