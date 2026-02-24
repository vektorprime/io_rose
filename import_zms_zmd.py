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
        
        # Import all ZMS files
        imported_count = 0
        for zms_path in zms_files:
            try:
                # Create a report function wrapper
                def report_wrapper(level, message):
                    self.report({level}, message)
                
                zms = ZMS(str(zms_path), report_func=report_wrapper)
                mesh_obj = self._create_mesh(context, zms, zms_path.stem, armature_obj)
                
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
        
        return obj
    
    def _bones_from_zmd(self, zmd, armature):
        """Create Blender bones from ZMD bone data."""
        # Create all bones first
        for rose_bone in zmd.bones:
            bone = armature.edit_bones.new(rose_bone.name)
            bone.use_connect = False
        
        # Build world transforms
        world_positions = []
        world_rotations = []
        
        for idx, rose_bone in enumerate(zmd.bones):
            pos = bmath.Vector(rose_bone.position.as_tuple())
            rot = bmath.Quaternion(rose_bone.rotation.as_tuple(w_first=True))
            
            if rose_bone.parent_id == -1:
                world_positions.append(pos)
                world_rotations.append(rot)
            else:
                parent_pos = world_positions[rose_bone.parent_id]
                parent_rot = world_rotations[rose_bone.parent_id]
                world_pos = parent_pos + (parent_rot @ pos)
                world_rot = parent_rot @ rot
                world_positions.append(world_pos)
                world_rotations.append(world_rot)
        
        # Set bone positions and parenting
        for idx, rose_bone in enumerate(zmd.bones):
            bone = armature.edit_bones[idx]
            world_pos = world_positions[idx]
            world_rot = world_rotations[idx]
            
            if rose_bone.parent_id == -1:
                bone.head = world_pos
                bone.tail = world_pos + bmath.Vector((0, 0, 0.1))
                if self.keep_root_bone and bone.length < 0.0001:
                    bone.tail = bone.head + bmath.Vector((0, 0, 0.1))
            else:
                if rose_bone.parent_id >= len(armature.edit_bones):
                    continue
                bone.parent = armature.edit_bones[rose_bone.parent_id]
                bone.head = world_pos
                bone.tail = world_pos + (world_rot @ bmath.Vector((0, 0.1, 0)))
                if bone.length < 0.001:
                    bone.tail = bone.head + bmath.Vector((0, 0.001, 0))
    
    def _create_mesh(self, context, zms, filename, armature_obj):
        """Create mesh from ZMS data and optionally link to armature."""
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
        
        # Material with texture
        mat = bpy.data.materials.new(filename)
        mat.use_nodes = True
        
        nodes = mat.node_tree.nodes
        mat_node = nodes["Principled BSDF"]
        tex_node = nodes.new(type="ShaderNodeTexImage")
        
        if self.load_texture:
            # Find the ZMS file path for texture lookup
            zms_path = Path(self.filepath).parent / f"{filename}.zms"
            for ext in self.texture_extensions:
                p = zms_path.with_suffix(ext)
                if p.is_file():
                    image = bpy.data.images.load(str(p))
                    tex_node.image = image
                    break
        
        links = mat.node_tree.links
        links.new(tex_node.outputs["Color"], mat_node.inputs["Base Color"])
        mesh.materials.append(mat)
        
        mesh.update(calc_edges=True)
        
        # Create object
        obj = bpy.data.objects.new(filename, mesh)
        
        # Create vertex groups for bones BEFORE parenting
        if len(zms.bones) > 0 and armature_obj:
            # Get bone names from armature
            bone_names = [bone.name for bone in armature_obj.data.bones]
            
            for i, bone_id in enumerate(zms.bones):
                # Create vertex group with bone name if available
                if bone_id < len(bone_names):
                    group_name = bone_names[bone_id]
                else:
                    group_name = f"zms_bone_{i}"
                
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
                        try:
                            group_index = zms.bones.index(bone_id)
                            if 0 <= group_index < len(obj.vertex_groups):
                                obj.vertex_groups[group_index].add([vi], weight, 'REPLACE')
                        except ValueError:
                            pass
        
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
