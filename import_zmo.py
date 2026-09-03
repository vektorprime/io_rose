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

from .rose.zmo import ZMO, ZmoChannelType
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
        if zmo.fps > 0:
            context.scene.render.fps = zmo.fps
        else:
            self.report({'WARNING'},
                f"ZMO has invalid FPS {zmo.fps}; keeping scene frame rate")
        context.scene.frame_start = self.start_frame
        context.scene.frame_end = self.start_frame + zmo.num_frames - 1

        # ZMO rotation/position channels are absolute local transforms in
        # parent space that REPLACE the ZMD rest transform. To convert them
        # into Blender pose-space keyframes we need the source ZMD.
        zmd = self._load_source_zmd(armature_obj)

        # F-Curves must be written into the channelbag of this armature's
        # slot (Blender 4.4+ slotted actions); legacy action.fcurves is only
        # a proxy for the first slot.
        slot = self._get_or_create_slot(action, armature_obj)
        fcurve_store = self._fcurve_store(action, slot)

        # Apply animation to armature
        self._apply_animation(armature_obj, action, fcurve_store, zmo, zmd)

        # Assign action to armature. On Blender 4.4+ the slot must be
        # assigned explicitly: assigning an action alone may leave the
        # data-block in a non-animated state.
        if not armature_obj.animation_data:
            armature_obj.animation_data_create()
        armature_obj.animation_data.action = action
        if slot is not None:
            try:
                armature_obj.animation_data.action_slot = slot
            except (AttributeError, TypeError, RuntimeError) as e:
                self.report({'WARNING'},
                    f"ZMO: could not assign action slot: {e}")

        # Stash frame events + interpolation interval on the action so the
        # ZMO exporter can round-trip them (they drive combat timing).
        try:
            action["zmo_frame_events"] = list(zmo.frame_events)
            action["zmo_interp_ms"] = (int(zmo.interpolation_interval_ms)
                                       if zmo.interpolation_interval_ms is not None
                                       else 500)
        except (TypeError, ValueError, RuntimeError) as e:
            self.report({'WARNING'},
                f"ZMO: could not store frame events on action: {e}")

        self.report({'INFO'}, f"Imported {filename} ({zmo.num_frames} frames @ {zmo.fps} FPS)")
        return {"FINISHED"}

    def _get_or_create_slot(self, action, armature_obj):
        """Return an OBJECT slot for the armature, creating it if needed.

        Returns None on Blender versions without slotted actions (< 4.4),
        in which case callers fall back to the legacy action.fcurves API.
        """
        try:
            slots = action.slots
        except AttributeError:
            return None
        for existing in slots:
            if existing.name == armature_obj.name:
                return existing
        try:
            return slots.new(id_type='OBJECT', name=armature_obj.name)
        except (TypeError, RuntimeError) as e:
            self.report({'WARNING'}, f"ZMO: could not create action slot: {e}")
            return None

    def _fcurve_store(self, action, slot):
        """F-Curve collection writing into the action's channelbag for slot.

        Uses the layered API on Blender 4.4+ (single layer + keyframe
        strip), falling back to the legacy action.fcurves proxy.
        """
        if slot is not None:
            try:
                layers = action.layers
                layer = layers[0] if len(layers) else layers.new("ROSE")
                strips = layer.strips
                strip = strips[0] if len(strips) else strips.new(type='KEYFRAME')
                return strip.channelbag(slot, ensure=True).fcurves
            except (AttributeError, TypeError, RuntimeError, IndexError) as e:
                self.report({'WARNING'},
                    f"ZMO: layered action API unavailable ({e}); using legacy F-Curves")
        return action.fcurves
    
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

    def _rest_entries(self, zmd):
        """Flattened rest data over the engine joint list (bones + dummies).

        The client chains dummy joints after the bones
        (model_loader.rs joints), so ZMO channel indices address this
        combined list. Positions are meters (parser-scaled), rotations
        Blender-order quaternions. Parents always index real bones; anything
        else falls back to root with a one-time warning (validated here, not
        per frame).
        """
        names = [b.name for b in zmd.bones] + [d.name for d in zmd.dummies]
        n_bones = len(zmd.bones)
        joints = list(zmd.bones) + list(zmd.dummies)
        parents, positions, quats = [], [], []
        for idx, joint in enumerate(joints):
            parent = joint.parent_id
            kind = 'dummy' if idx >= n_bones else 'bone'
            if parent is None or not (0 <= parent < n_bones) or parent == idx:
                if parent != -1:
                    self.report({'WARNING'},
                        f"ZMO: invalid parent ID {parent} for {kind} "
                        f"'{names[idx]}'; treating as root")
                parent = -1
            parents.append(parent)
            positions.append(Vector(joint.position.as_tuple()))
            quats.append(Quaternion(joint.rotation.as_tuple(w_first=True)))
        return names, parents, positions, quats

    def _pseudo_zmd_from_armature(self, armature_obj):
        """Synthesize ZMD-like rest data from the armature's own rest pose.

        Used when no source ZMD file is available. The armature rest
        (matrix_local, after rest alignment) already equals the ZMD rest, so
        the same matrix composition applies. Parent-space locals are derived
        from the world-space rest matrices; positions are meters.
        """
        from types import SimpleNamespace
        from .rose.utils import Vector3 as RVector3, Quat as RQuat
        arm_bones = list(armature_obj.data.bones)
        index = {b.name: i for i, b in enumerate(arm_bones)}
        pseudo = []
        for bone in arm_bones:
            if bone.parent is not None and bone.parent.name in index:
                parent = arm_bones[index[bone.parent.name]]
                local = parent.matrix_local.inverted() @ bone.matrix_local
                parent_id = index[bone.parent.name]
            else:
                local = bone.matrix_local.copy()
                parent_id = -1
            t = local.translation
            q = local.to_quaternion()
            pseudo.append(SimpleNamespace(
                name=bone.name, parent_id=parent_id,
                position=RVector3(t.x, t.y, t.z),
                rotation=RQuat(q.x, q.y, q.z, q.w)))
        return SimpleNamespace(bones=pseudo, dummies=[])

    def _compute_world_matrices(self, rest, bone_channels, frame_idx):
        """Compute per-joint world transforms for one frame, ROSE-style.

        local[i] = T(position) @ Q(rotation) @ S(scale) where each component
        comes from the ZMO channel when present (positions in cm, scaled to
        scene units) or falls back to the rest transform.
        world[i] = world[parent] @ local[i].
        """
        _names, parents, rest_pos, rest_quat = rest
        num_joints = len(_names)
        world = [None] * num_joints

        def local(i):
            pos = rest_pos[i]
            quat = rest_quat[i]
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

        locals_arr = [None] * num_joints

        def resolve(i, visiting):
            if world[i] is not None:
                return world[i]
            if i in visiting:
                return local(i)  # break cycles
            visiting.add(i)
            loc_mat = local(i)
            locals_arr[i] = loc_mat
            parent = parents[i]
            if 0 <= parent < num_joints and parent != i:
                world[i] = resolve(parent, visiting) @ loc_mat
            else:
                world[i] = loc_mat
            return world[i]

        worlds = [resolve(i, set()) for i in range(num_joints)]
        return worlds, locals_arr

    def _apply_animation_matrix(self, armature_obj, action, fcurve_store, zmo, zmd):
        """Keyframe the armature by composing ROSE joint transforms per frame.

        The ROSE engine treats ZMO channels as absolute local transforms that
        REPLACE the rest transform. Blender's pose recursion is
            pose[i] = pose[parent] @ bone[parent].matrix_local.inverted()
                      @ bone[i].matrix_local @ matrix_basis
        so the per-frame basis that realizes a target ROSE local transform L is:
            matrix_basis = bone.matrix_local.inverted()
                           @ parent.matrix_local @ L
        which is then decomposed into rotation_quaternion / location / scale
        keyframes. Joints are matched to armature bones by name over the
        bones+dummies chain.
        """
        joint_names, parents, _rp, _rq = self._rest_entries(zmd)
        rest = (joint_names, parents, _rp, _rq)
        num_joints = len(joint_names)
        arm_bones = armature_obj.data.bones
        if len(arm_bones) < num_joints:
            self.report({'WARNING'},
                f"ZMO: source has {num_joints} joints but armature has "
                f"{len(arm_bones)} bones - extra joints skipped")

        bone_channels = zmo.get_bone_channels()
        relevant = {ZmoChannelType.POSITION, ZmoChannelType.ROTATION, ZmoChannelType.SCALE}

        for pose_bone in armature_obj.pose.bones:
            pose_bone.rotation_mode = 'QUATERNION'

        # Static per-joint data (rest frames + parent checks); only the local
        # transform varies per frame. Parent mismatches are reported once.
        static = []
        missing = []
        for i in range(num_joints):
            chs = bone_channels.get(i)
            if not chs or not (chs.keys() & relevant):
                static.append(None)
                continue
            bone_name = joint_names[i]
            bone = arm_bones.get(bone_name)
            if bone is None:
                missing.append(bone_name)
                static.append(None)
                continue
            if parents[i] >= 0:
                expected_parent = joint_names[parents[i]]
                actual_parent = bone.parent.name if bone.parent is not None else None
                if actual_parent != expected_parent:
                    self.report({'WARNING'},
                        f"ZMO: parent of {bone_name} differs between armature "
                        f"({actual_parent}) and source ({expected_parent})")
            parent_rest = (bone.parent.matrix_local if bone.parent is not None
                           else Matrix.Identity(4))
            static.append((bone_name, bone, parent_rest))
        for bone_name in missing:
            self.report({'WARNING'},
                f"ZMO: joint '{bone_name}' not found in armature - channel skipped")

        def get_fcurve(data_path, index):
            fcurve = fcurve_store.find(data_path, index=index)
            if fcurve is None:
                fcurve = fcurve_store.new(data_path, index=index)
            return fcurve

        def key_vector(data_path, values, frame):
            # The engine interpolates linearly (lerp/slerp) between integer
            # frames; Blender keyframes default to BEZIER, which overshoots.
            for axis, value in enumerate(values):
                fcurve = get_fcurve(data_path, axis)
                key = fcurve.keyframe_points.insert(frame, value)
                key.interpolation = 'LINEAR'

        prev_quats = {}
        for frame_idx in range(zmo.num_frames):
            frame = self.start_frame + frame_idx
            _world, locals_arr = self._compute_world_matrices(rest, bone_channels, frame_idx)

            for i in range(num_joints):
                entry = static[i]
                if entry is None:
                    continue
                bone_name, bone, parent_rest = entry

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

    def _apply_animation(self, armature_obj, action, fcurve_store, zmo, zmd=None):
        """Apply ZMO animation data to armature via matrix composition.

        When no source ZMD is available, rest frames are derived from the
        armature itself (its aligned rest equals the ZMD rest), so poses stay
        correct instead of falling back to raw absolute values as deltas.
        """
        if zmd is None:
            self.report({'INFO'},
                "ZMO: no source ZMD found - deriving rest frames from the "
                "armature. For best results import the mesh via "
                "'ROSE Mesh with Skeleton' or set 'Source Armature (.zmd)'.")
            zmd = self._pseudo_zmd_from_armature(armature_obj)
        self._apply_animation_matrix(armature_obj, action, fcurve_store, zmo, zmd)


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
