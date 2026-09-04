"""
Blender operator for importing ROSE Online ZMS mesh with ZMD armature combined.

This imports all ZMS meshes in the directory along with the skeleton in one step,
properly linking them so that animations (ZMO) can be applied correctly.

Coordinate System Notes:
- ROSE Online: X=right, Y=forward, Z=up (right-handed, Z-up)
- Blender: X=right, Y=forward, Z=up (right-handed, Z-up)
- Since both use the same coordinate system, we only need to scale positions (cm -> m)
"""

from pathlib import Path
import math
import bpy
import mathutils as bmath
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper

from .rose.zms import ZMS
from .rose.zmd import ZMD


class ImportZMSwithZMD(bpy.types.Operator, ImportHelper):
    """Import ROSE Online ZMS meshes with ZMD armature (linked together)"""
    bl_idname = "rose.import_zms_zmd"
    bl_label = "ROSE Mesh with Skeleton (.zms)"
    bl_options = {"PRESET", "UNDO"}
    
    filename_ext = ".zms"
    filter_glob: StringProperty(
        default="*.zms;*.ZMS",
        options={"HIDDEN"}
    )
    
    load_texture: BoolProperty(
        name="Load Textures",
        description="Automatically detect and load textures if they can be found",
        default=True,
    )
    
    keep_root_bone: BoolProperty(
        name="Keep Root Bone",
        description="Prevent Blender from automatically removing the root bone",
        default=True,
    )
    
    import_all_zms: BoolProperty(
        name="Import All ZMS in Directory",
        description="Import all ZMS files from the same directory",
        default=True,
    )
    
    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]
    skeleton_extensions = [".ZMD", ".zmd"]
    mesh_extensions = [".ZMS", ".zms"]
    
    def execute(self, context):
        filepath = Path(self.filepath)
        directory = filepath.parent
        
        # Find ZMD file first
        zmd = None
        zmd_path = None
        
        # First try exact match (same filename, different extension)
        for ext in self.skeleton_extensions:
            potential_path = filepath.with_suffix(ext)
            if potential_path.is_file():
                zmd_path = potential_path
                break
        
        # If not found, search for any .zmd file in the same directory
        if zmd_path is None:
            for ext in self.skeleton_extensions:
                matches = list(directory.glob(f"*{ext}"))
                if matches:
                    zmd_path = matches[0]
                    self.report({'INFO'}, f"Found skeleton in directory: {zmd_path.name}")
                    break
        
        if zmd_path is None:
            self.report({'WARNING'}, f"No ZMD file found in {directory}. Importing meshes without skeleton.")
        else:
            try:
                zmd = ZMD(str(zmd_path))
                self.report({'INFO'}, f"Loaded skeleton: {zmd_path.name} ({len(zmd.bones)} bones)")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load ZMD file: {str(e)}. Importing meshes without skeleton.")
                zmd = None
        
        # Create armature first if ZMD exists
        armature_obj = None
        skeleton_name = zmd_path.stem if zmd_path else "skeleton"
        if zmd:
            armature_obj = self._create_armature(context, zmd, skeleton_name)
            if armature_obj:
                # Remember where the skeleton came from so the ZMO importer can
                # convert ROSE absolute local transforms into Blender pose space
                armature_obj["zmd_path"] = str(zmd_path)
        
        # Collect all ZMS files to import
        zms_files = []
        if self.import_all_zms:
            for ext in self.mesh_extensions:
                zms_files.extend(directory.glob(f"*{ext}"))
            # Remove duplicates (case-insensitive file systems)
            seen = set()
            unique_zms = []
            for f in zms_files:
                lower = f.name.lower()
                if lower not in seen:
                    seen.add(lower)
                    unique_zms.append(f)
            zms_files = unique_zms
        else:
            zms_files = [filepath]
        
        # Sort files for consistent ordering
        zms_files.sort(key=lambda p: p.name.lower())
        
        self.report({'INFO'}, f"Found {len(zms_files)} ZMS files to import")
        
        # ZMD-ordered joint names (bones, then dummies): the engine addresses
        # skin weights and ZMO channels by these indices, which is NOT the
        # armature's collection order (Blender sorts bones into hierarchy
        # order, moving early-parented dummies up the list).
        joint_names = None
        if zmd:
            joint_names = ([b.name for b in zmd.bones]
                           + [d.name for d in zmd.dummies])

        # Import all ZMS files
        imported_count = 0
        for zms_path in zms_files:
            try:
                # Create a report function wrapper
                def report_wrapper(level, message):
                    self.report({level}, message)

                zms = ZMS(str(zms_path), report_func=report_wrapper)
                mesh_obj = self._create_mesh(context, zms, zms_path.stem,
                                             armature_obj, joint_names)
                
                # Parent mesh to armature
                if armature_obj:
                    mesh_obj.parent = armature_obj
                    # Set armature modifier
                    mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
                    mod.object = armature_obj
                
                imported_count += 1
                
            except Exception as e:
                self.report({'WARNING'}, f"Failed to import {zms_path.name}: {str(e)}")
                continue
        
        if armature_obj:
            self.report({'INFO'}, 
                f"Imported {imported_count} meshes with {len(zmd.bones)} bones")
        else:
            self.report({'INFO'}, 
                f"Imported {imported_count} meshes (no skeleton)")
        
        return {"FINISHED"}
    
    def _create_armature(self, context, zmd, filename):
        """Create armature from ZMD data."""
        armature = bpy.data.armatures.new(filename + "_skeleton")
        obj = bpy.data.objects.new(filename + "_skeleton", armature)
        
        # Link to scene collection
        context.collection.objects.link(obj)
        
        # Set as active and selected
        context.view_layer.objects.active = obj
        obj.select_set(True)
        
        # Enter edit mode to create bones
        bpy.ops.object.mode_set(mode='EDIT')
        
        try:
            self._bones_from_zmd(zmd, armature)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create bones: {str(e)}")
            bpy.ops.object.mode_set(mode='OBJECT')
            return None
        
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # Bone head/tail/roll cannot store an arbitrary rotation frame directly,
        # so after the first pass (roll=0) compute and apply the exact roll for
        # each bone so bone.matrix_local equals the ZMD composed rest transform.
        self._align_rest_to_zmd(zmd, obj)
        
        return obj
    
    def _zmd_entries(self, zmd):
        """Flattened (name, parent, pos, rot) list: bones then dummies.

        Dummy parents index into the bone array (dummies are appended after
        bones in the engine joint list), so out-of-range dummy parents fall
        back to root with a warning.
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
        Invalid bone parents fall back to root with a warning; parent cycles
        attach to root rather than failing the whole import.
        """
        entries = self._zmd_entries(zmd)
        n_bones = len(zmd.bones)
        world_positions = [bmath.Vector((0.0, 0.0, 0.0)) for _ in entries]
        world_rotations = [bmath.Quaternion((1.0, 0.0, 0.0, 0.0)) for _ in entries]

        def valid_parent(idx, parent):
            limit = n_bones if idx >= n_bones else len(entries)
            return parent is not None and 0 <= parent < limit and parent != idx

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

    def _bones_from_zmd(self, zmd, armature):
        """Create Blender bones from ZMD bone + dummy data.

        Note: a Blender bone only stores direction + roll (5 DOF) while ZMD
        gives a full 3-DOF rotation, so placing the tail along the rest Y axis
        fixes 2 of the 3.  The remaining roll is set in _align_rest_to_zmd.
        Dummies are real bones (the engine chains them after the bones and
        parents attachments/effects to them), so ZMO channels and weights
        that target dummy joints keep working.
        """
        entries, world_positions, world_rotations = self._world_transforms(zmd)

        # Create all bones first. Keep our own references: Blender re-sorts
        # edit bones into hierarchy order on mode re-entry, so integer
        # indices into armature.edit_bones are only valid within this session.
        created = []
        for (name, _parent, _pos, _rot) in entries:
            bone = armature.edit_bones.new(name)
            bone.use_connect = False
            created.append(bone)

        # Set bone positions and parenting
        for idx, (_name, parent, _pos, _rot) in enumerate(entries):
            bone = created[idx]
            world_pos = world_positions[idx]
            world_rot = world_rotations[idx]

            if parent != -1 and 0 <= parent < len(created):
                bone.parent = created[parent]
            bone.head = world_pos
            bone.tail = world_pos + (world_rot @ bmath.Vector((0, 0.1, 0)))
            if bone.length < 0.001:
                if parent == -1 and self.keep_root_bone:
                    bone.tail = bone.head + bmath.Vector((0, 0.1, 0))
                if bone.length < 0.001:
                    bone.tail = bone.head + bmath.Vector((0, 0.001, 0))

    def _align_rest_to_zmd(self, zmd, armature_obj):
        """Set bone roll so matrix_local exactly matches the ZMD rest transform.

        Blender derives a bone's rest frame from head/tail direction plus roll,
        with an implicit roll=0 base frame.  Reading back the evaluated
        matrix_local after the first pass gives that actual base frame, so the
        required roll is the signed angle around the bone's Y axis from the
        base X axis to the ZMD rest X axis. Covers bones and dummies alike.

        This alignment is critical and its failure is silent: Blender deforms
        skinned meshes with pose @ rest^-1 while the engine uses
        pose @ ZMD_bind^-1.  A mismatched rest still looks correct at rest
        (the ratio is identity for ANY rest) but corrupts every animated pose.
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
            # re-entry, so dummy bones created last but parented early have
            # moved - index-based access scrambles rolls onto wrong bones.
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

    
    def _create_mesh(self, context, zms, filename, armature_obj, joint_names=None):
        """Create mesh from ZMS data and optionally link to armature.

        joint_names is the ZMD-ordered joint list (bones, then dummies) used
        to resolve global bone ids to vertex-group names. It must NOT be
        derived from armature collection order (see above).
        """
        mesh = bpy.data.meshes.new(filename)
        
        # Vertices
        verts = []
        for v in zms.vertices:
            verts.append((v.position.x, v.position.y, v.position.z))
        
        # Normals
        normals = []
        if zms.normals_enabled():
            for v in zms.vertices:
                normals.append((v.normal.x, v.normal.y, v.normal.z))
        else:
            normals = None
        
        # Faces
        faces = []
        for i in zms.indices:
            faces.append((int(i.x), int(i.y), int(i.z)))
        
        # Create mesh
        mesh.from_pydata(verts, [], faces)
        
        # Set normals
        if normals is not None:
            loop_normals = []
            for loop in mesh.loops:
                vi = loop.vertex_index
                loop_normals.append(normals[vi])
            mesh.normals_split_custom_set(loop_normals)
        
        # UV layers
        if zms.uv1_enabled():
            mesh.uv_layers.new(name="uv1")
        if zms.uv2_enabled():
            mesh.uv_layers.new(name="uv2")
        if zms.uv3_enabled():
            mesh.uv_layers.new(name="uv3")
        if zms.uv4_enabled():
            mesh.uv_layers.new(name="uv4")
        
        for loop_idx, loop in enumerate(mesh.loops):
            vi = loop.vertex_index
            
            if zms.uv1_enabled():
                u = zms.vertices[vi].uv1.x
                v = zms.vertices[vi].uv1.y
                mesh.uv_layers["uv1"].data[loop_idx].uv = (u, 1 - v)
            
            if zms.uv2_enabled():
                u = zms.vertices[vi].uv2.x
                v = zms.vertices[vi].uv2.y
                mesh.uv_layers["uv2"].data[loop_idx].uv = (u, 1 - v)
            
            if zms.uv3_enabled():
                u = zms.vertices[vi].uv3.x
                v = zms.vertices[vi].uv3.y
                mesh.uv_layers["uv3"].data[loop_idx].uv = (u, 1 - v)
            
            if zms.uv4_enabled():
                u = zms.vertices[vi].uv4.x
                v = zms.vertices[vi].uv4.y
                mesh.uv_layers["uv4"].data[loop_idx].uv = (u, 1 - v)
        
        # Vertex colors (matches ZmsFile.color / client MESH_ATTRIBUTE_COLOR).
        # Stored as a POINT-domain color attribute so the exporter can
        # round-trip them; without this, re-export writes white instead of
        # the original tint.
        if zms.colors_enabled():
            try:
                color_attr = mesh.color_attributes.new(
                    name="Color", type='FLOAT_COLOR', domain='POINT')
                for vi, v in enumerate(zms.vertices):
                    if vi < len(color_attr.data):
                        color_attr.data[vi].color = (
                            v.color.r, v.color.g, v.color.b, v.color.a)
            except Exception as e:
                self.report({'WARNING'}, f"Could not import vertex colors: {e}")

        # Material with texture
        mat = bpy.data.materials.new(filename)
        mat.use_nodes = True

        nodes = mat.node_tree.nodes
        mat_node = nodes["Principled BSDF"]
        tex_node = nodes.new(type="ShaderNodeTexImage")

        texture_loaded = False
        if self.load_texture:
            # Find the ZMS file path for texture lookup
            zms_path = Path(self.filepath).parent / f"{filename}.zms"
            for ext in self.texture_extensions:
                p = zms_path.with_suffix(ext)
                if p.is_file():
                    try:
                        image = bpy.data.images.load(str(p), check_existing=True)
                        tex_node.image = image
                        texture_loaded = True
                        break
                    except Exception as e:
                        self.report({'WARNING'},
                            f"Could not load texture {p.name}: {e}")
                        continue

        # Only link the texture when an image actually loaded; otherwise the
        # material renders black with no indication of why.
        if texture_loaded:
            links = mat.node_tree.links
            links.new(tex_node.outputs["Color"], mat_node.inputs["Base Color"])
        else:
            nodes.remove(tex_node)
        mesh.materials.append(mat)
        
        mesh.update(calc_edges=True)
        
        # Create object
        obj = bpy.data.objects.new(filename, mesh)
        
        # Create vertex groups for bones BEFORE parenting
        if len(zms.bones) > 0 and armature_obj:
            # Resolve global bone ids through the ZMD-ordered joint list.
            # Fall back to armature order only when no ZMD names were passed
            # (armature imported from elsewhere).
            bone_names = (joint_names if joint_names is not None
                          else [bone.name for bone in armature_obj.data.bones])
            
            for i, bone_id in enumerate(zms.bones):
                # Create vertex group with bone name if available
                if bone_id < len(bone_names):
                    group_name = bone_names[bone_id]
                else:
                    group_name = f"zms_bone_{i}"
                
                if group_name not in obj.vertex_groups:
                    obj.vertex_groups.new(name=group_name)
            
            # Assign weights per vertex
            for vi, v in enumerate(zms.vertices):
                for gi in range(4):
                    try:
                        weight = v.bone_weights[gi]
                        bone_id = int(v.bone_indices[gi])
                    except (IndexError, ValueError):
                        continue
                    
                    if weight and weight > 0.0:
                        # bone_indices are GLOBAL ZMD bone ids: real files
                        # contain indices beyond len(zms.bones), so they cannot
                        # be local table slots (rose-file-readers' zms.rs
                        # bones.get(index) interpretation is wrong for these).
                        if 0 <= bone_id < len(bone_names):
                            group_name = bone_names[bone_id]
                        else:
                            continue
                        vg = obj.vertex_groups.get(group_name)
                        if vg is None:
                            vg = obj.vertex_groups.new(name=group_name)
                        vg.add([vi], weight, 'REPLACE')
        
        # Store ZMS metadata
        obj["zms_version"] = zms.version
        obj["zms_identifier"] = zms.identifier
        obj["zms_materials"] = str(zms.materials)
        obj["zms_strips"] = str(zms.strips)
        obj["zms_pool"] = zms.pool
        obj["zms_bones"] = str(zms.bones)
        
        # Link to scene
        context.collection.objects.link(obj)
        
        return obj


def menu_func_import(self, context):
    self.layout.operator(ImportZMSwithZMD.bl_idname, text="ROSE Mesh with Skeleton (.zms)")


def register():
    bpy.utils.register_class(ImportZMSwithZMD)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportZMSwithZMD)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
    register()
