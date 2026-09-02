"""
Blender operator for exporting ROSE Online ZMO animation files.

The inverse of import_zmo.py:

- ZMO rotation/position channels are ABSOLUTE local transforms (parent space)
  that REPLACE the ZMD rest transform.
- Per frame we read the evaluated pose and compute the engine-local transform
  directly:  local = parent_pose_world.inverted() @ bone_pose_world
  (with a ZMD-aligned armature rest, this equals what the ROSE engine reads).
- Positions are written in centimeters (x100), quaternions in WXYZ order with
  sign-continuity so runtime slerp takes the short path.

File layout (matches rose/zmo.py reader / rose-file-readers zmo.rs):
    "ZMO0002\0" | u32 fps | u32 num_frames | u32 channel_count
    channel_count x (u32 channel_type, u32 bone_index)
    num_frames x (per channel: POSITION vec3 | ROTATION quat WXYZ | SCALE f32)
    extended block: u16 event_count + event_count x u16 + u32 interp_interval_ms
    u32 extended_block_offset | "3ZMO"
"""

import struct
from pathlib import Path

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper

from .rose.zmd import ZMD
from .rose.zmo import ZmoChannelType


def _pack_u32(v):
    return struct.pack('<I', v)


def _pack_u16(v):
    return struct.pack('<H', v)


def _pack_f32(*v):
    return struct.pack('<' + 'f' * len(v), *v)


class ExportZMO(bpy.types.Operator, ExportHelper):
    """Export the active armature's action as a ROSE Online ZMO animation"""
    bl_idname = "rose.export_zmo"
    bl_label = "ROSE Animation (.zmo)"
    bl_options = {"PRESET"}

    filename_ext = ".zmo"
    filter_glob: StringProperty(
        default="*.zmo",
        options={"HIDDEN"}
    )

    zmd_filepath: StringProperty(
        name="Source Armature (.zmd)",
        description="ZMD the armature was imported from (used for bone indices); "
                    "defaults to the armature's stored zmd_path",
        subtype='FILE_PATH',
        default="",
    )

    use_action_range: BoolProperty(
        name="Use Action Range",
        description="Export the action's frame range instead of the scene range",
        default=True,
    )

    def execute(self, context):
        arm = context.active_object
        if not arm or arm.type != 'ARMATURE':
            self.report({'ERROR'}, "No active armature")
            return {'CANCELLED'}
        if not arm.animation_data or not arm.animation_data.action:
            self.report({'ERROR'}, "Active armature has no active action")
            return {'CANCELLED'}

        action = arm.animation_data.action
        zmd_path = self.zmd_filepath or arm.get('zmd_path')
        if not zmd_path or not Path(bpy.path.abspath(zmd_path)).exists():
            self.report({'ERROR'}, "ZMD not found - set 'Source Armature (.zmd)' "
                                   "or import the skeleton with 'ROSE Mesh with Skeleton'")
            return {'CANCELLED'}

        zmd = ZMD(str(bpy.path.abspath(zmd_path)))
        bone_index_by_name = {b.name: i for i, b in enumerate(zmd.bones)}

        if self.use_action_range:
            start, end = int(round(action.frame_range[0])), int(round(action.frame_range[1]))
        else:
            start, end = context.scene.frame_start, context.scene.frame_end
        if end < start:
            self.report({'ERROR'}, "Invalid frame range")
            return {'CANCELLED'}
        num_frames = end - start + 1
        fps = context.scene.render.fps

        # Bones touched by the action (any of loc / rot / scale fcurves)
        animated = []
        for fc in action.fcurves:
            dp = fc.data_path
            if dp.startswith('pose.bones'):
                name = dp.split('"')[1]
                if name not in animated:
                    animated.append(name)
        missing = [n for n in animated if n not in bone_index_by_name]
        for n in missing:
            self.report({'WARNING'}, f"Bone '{n}' not in ZMD - channel skipped")
        animated = [n for n in animated if n in bone_index_by_name]
        animated.sort(key=lambda n: bone_index_by_name[n])
        if not animated:
            self.report({'ERROR'}, "Action has no pose bone channels")
            return {'CANCELLED'}

        # Sample every frame
        pos_data = {n: [] for n in animated}
        rot_data = {n: [] for n in animated}
        scale_data = {n: [] for n in animated}
        frame_bak = context.scene.frame_current
        try:
            for f in range(start, end + 1):
                context.scene.frame_set(f)
                for pb in arm.pose.bones:
                    if pb.name not in pos_data:
                        continue
                    parent = pb.parent
                    if parent is not None:
                        local = parent.matrix.inverted() @ pb.matrix
                    else:
                        local = pb.matrix.copy()
                    loc, quat, scale = local.decompose()
                    pos_data[pb.name].append((loc.x * 100.0, loc.y * 100.0, loc.z * 100.0))
                    rot_data[pb.name].append(quat.normalized())
                    scale_data[pb.name].append(scale)
        finally:
            context.scene.frame_set(frame_bak)

        # Sign-continuity for quaternions (runtime slerp picks short path)
        for name in animated:
            prev = None
            for i, q in enumerate(rot_data[name]):
                if prev is not None and q.dot(prev) < 0.0:
                    q = (-q.w, -q.x, -q.y, -q.z)
                    rot_data[name][i] = q
                prev = q

        # Emit scale channels only where scale actually animates
        scale_animated = []
        for name in animated:
            vals = scale_data[name]
            if any(abs(s.x - 1.0) > 1e-4 or abs(s.y - 1.0) > 1e-4 or abs(s.z - 1.0) > 1e-4
                   for s in vals):
                if not all((max(s) - min(s)) < 1e-3 for s in vals):
                    self.report({'WARNING'},
                                f"Bone '{name}' has non-uniform scale; ZMO stores uniform scale - "
                                f"exported as per-axis average")
                scale_animated.append(name)

        # Channel table: POSITION+ROTATION per animated bone (+ SCALE where needed)
        channels = []
        for name in animated:
            idx = bone_index_by_name[name]
            channels.append((int(ZmoChannelType.POSITION), idx, name))
            channels.append((int(ZmoChannelType.ROTATION), idx, name))
        for name in scale_animated:
            channels.append((int(ZmoChannelType.SCALE), bone_index_by_name[name], name))

        out = bytearray()
        out += b"ZMO0002\x00"
        out += _pack_u32(fps)
        out += _pack_u32(num_frames)
        out += _pack_u32(len(channels))
        for ctype, idx, _ in channels:
            out += _pack_u32(ctype)
            out += _pack_u32(idx)
        for _f in range(num_frames):
            fi = _f  # index into sampled data lists
            for ctype, idx, name in channels:
                if ctype == ZmoChannelType.POSITION:
                    out += _pack_f32(*pos_data[name][fi])
                elif ctype == ZmoChannelType.ROTATION:
                    w, x, y, z = rot_data[name][fi]
                    out += _pack_f32(w, x, y, z)
                elif ctype == ZmoChannelType.SCALE:
                    s = scale_data[name][fi]
                    out += _pack_f32((s.x + s.y + s.z) / 3.0)

        # Extended block (3ZMO): one neutral frame event per frame + interval
        ext_offset = len(out)
        out += _pack_u16(num_frames)
        for _ in range(num_frames):
            out += _pack_u16(0)
        out += _pack_u32(500)
        out += _pack_u32(ext_offset)
        out += b"3ZMO"

        try:
            with open(self.filepath, 'wb') as f:
                f.write(out)
        except OSError as e:
            self.report({'ERROR'}, f"Failed to write {self.filepath}: {e}")
            return {'CANCELLED'}

        self.report({'INFO'},
                    f"Exported '{action.name}' -> {Path(self.filepath).name} "
                    f"({num_frames} frames @ {fps} FPS, {len(channels)} channels)")
        return {'FINISHED'}


def menu_func_export(self, context):
    self.layout.operator(ExportZMO.bl_idname, text="ROSE Animation (.zmo)")
