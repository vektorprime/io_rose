from pathlib import Path
import os

if "bpy" in locals():
    import importlib
else:
    from .rose.zsc import *
    from .rose.ifo import *
    from .rose.utils import convert_rose_position_to_blender
    from .import_zms import ImportZMS

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector, Quaternion


class ImportZSC(bpy.types.Operator, ImportHelper):
    bl_idname = "rose.import_zsc"
    bl_label = "ROSE Scene (.zsc)"
    bl_options = {"PRESET"}

    filename_ext = ".ZSC"
    filter_glob: StringProperty(default="*.ZSC", options={"HIDDEN"})
    
    load_textures: BoolProperty(
        name="Load textures",
        description="Automatically load textures for materials",
        default=True,
    )
    
    world_offset: bpy.props.FloatProperty(
        name="World Offset",
        description="World offset in meters for positioning objects (default52.0m = 5200cm)",
        default=52.0,
    )
    
    load_cnst_objects: BoolProperty(
        name="Load CNST Objects",
        description="Load construction objects from IFO file",
        default=True,
    )
    
    load_deco_objects: BoolProperty(
        name="Load DECO Objects",
        description="Load decoration objects from IFO file",
        default=True,
    )
    
    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]

    def execute(self, context):
        filepath = Path(self.filepath)
        
        # Load ZSC file
        try:
            zsc = Zsc(str(filepath))
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load ZSC file: {str(e)}")
            return {'CANCELLED'}
        
        self.report({'INFO'}, f"Loaded ZSC: {len(zsc.meshes)} meshes, {len(zsc.materials)} materials, {len(zsc.objects)} objects")
        
        # Try to find corresponding IFO file
        ifo = None
        ifo_path = filepath.with_suffix('.IFO')
        if not ifo_path.exists():
            ifo_path = filepath.with_suffix('.ifo')
        
        if ifo_path.exists():
            try:
                ifo = Ifo(str(ifo_path))
                self.report({'INFO'}, f"Loaded IFO: {len(ifo.cnst_objects)} CNST, {len(ifo.deco_objects)} DECO objects")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load IFO file: {str(e)}")
        
        # Create a collection for this scene
        collection = bpy.data.collections.new(filepath.stem)
        context.scene.collection.children.link(collection)
        
        # Cache for materials and meshes
        material_cache = {}
        mesh_cache = {}
        
        # Pre-load all materials
        for mat_idx, zsc_mat in enumerate(zsc.materials):
            material_cache[mat_idx] = self.create_material(zsc_mat, filepath.parent)
        
        # Load objects from IFO
        if ifo:
            if self.load_cnst_objects:
                for obj_inst in ifo.cnst_objects:
                    if obj_inst.object_id < len(zsc.objects):
                        self.spawn_object(
                            context, collection, zsc, obj_inst, 
                            material_cache, mesh_cache, filepath.parent
                        )
            
            if self.load_deco_objects:
                for obj_inst in ifo.deco_objects:
                    if obj_inst.object_id < len(zsc.objects):
                        self.spawn_object(
                            context, collection, zsc, obj_inst,
                            material_cache, mesh_cache, filepath.parent
                        )
        else:
            # No IFO file - just spawn all ZSC objects at origin for preview
            self.report({'INFO'}, "No IFO file found, spawning all objects at origin")
            for obj_id, zsc_obj in enumerate(zsc.objects):
                # Create a fake IfoObject
                fake_ifo_obj = type('obj', (object,), {
                    'object_id': obj_id,
                    'object_name': f"Object_{obj_id}",
                    'position': Vector3(0, 0, 0),
                    'rotation': Quat(1, 0, 0, 0),
                    'scale': Vector3(1, 1, 1)
                })()
                
                self.spawn_object(
                    context, collection, zsc, fake_ifo_obj,
                    material_cache, mesh_cache, filepath.parent
                )
        
        self.report({'INFO'}, f"Import completed!")
        return {"FINISHED"}
    
    def convert_rose_quaternion_to_blender(self, rot):
        """
        Convert Rose Online quaternion to Blender quaternion.

        Both Rose Online and Blender use Z-up coordinate systems.
        Only the Y component needs to be negated to match the position transform.

        Transform: (W, X, Y, Z) -> (W, X, -Y, Z)

        Args:
            rot: Quaternion with (w, x, y, z) attributes
            
        Returns:
            Tuple of (w, x, y, z) for Blender
        """
        return (rot.w, rot.x, -rot.y, rot.z)
    
    def spawn_object(self, context, collection, zsc, ifo_object, material_cache, mesh_cache, base_path):
        """Spawn a ZSC object instance from IFO data"""
        zsc_obj = zsc.objects[ifo_object.object_id]
        
        # Create parent empty for the object
        obj_name = getattr(ifo_object, 'object_name', f"Object_{ifo_object.object_id}")
        parent_empty = bpy.data.objects.new(obj_name, None)
        parent_empty.empty_display_type = 'PLAIN_AXES'
        parent_empty.empty_display_size = 0.5
        collection.objects.link(parent_empty)
        
        # Convert ROSE coordinates to Blender (X, -Y, Z) and scale by 1/100
        pos = ifo_object.position
        bx, by, bz = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
        # Apply configurable world offset to match terrain coordinates (only horizontal X, Y)
        parent_empty.location = (bx + self.world_offset, by + self.world_offset, bz)
        
        # Convert quaternion (W, X, Y, Z) -> Blender (W, X, -Y, Z)
        rot = ifo_object.rotation
        parent_empty.rotation_mode = 'QUATERNION'
        parent_empty.rotation_quaternion = self.convert_rose_quaternion_to_blender(rot)
        
        # Scale - no axis swap needed since both use Z-up
        parent_empty.scale = (ifo_object.scale.x, ifo_object.scale.y, ifo_object.scale.z)
        
        # Spawn all parts
        part_objects = []
        for part_idx, part in enumerate(zsc_obj.parts):
            part_obj = self.spawn_part(
                context, zsc, part, part_idx, 
                material_cache, mesh_cache, base_path, obj_name
            )
            if part_obj:
                collection.objects.link(part_obj)
                part_obj.parent = parent_empty
                part_objects.append(part_obj)
        
        return parent_empty
    
    def spawn_part(self, context, zsc, part, part_idx, material_cache, mesh_cache, base_path, obj_name):
        """Spawn a single object part (mesh instance)"""
        mesh_id = part.mesh_id
        material_id = part.material_id
        
        # Get or load mesh
        if mesh_id not in mesh_cache:
            mesh_path = zsc.meshes[mesh_id]
            mesh_cache[mesh_id] = self.load_zms_mesh(mesh_path, base_path)
        
        mesh_data = mesh_cache[mesh_id]
        if not mesh_data:
            return None
        
        # Create object instance
        part_name = f"{obj_name}_part{part_idx}"
        obj = bpy.data.objects.new(part_name, mesh_data)
        
        # Apply material
        if material_id in material_cache:
            if len(obj.data.materials) > 0:
                obj.data.materials[0] = material_cache[material_id]
            else:
                obj.data.materials.append(material_cache[material_id])
        
        # Set transform (relative to parent)
        # Note: Parts use local coordinates relative to parent, so no world offset needed
        obj.location = convert_rose_position_to_blender(part.position.x, part.position.y, part.position.z)
        obj.rotation_mode = 'QUATERNION'
        obj.rotation_quaternion = self.convert_rose_quaternion_to_blender(part.rotation)
        # Scale - no axis swap needed since both use Z-up
        obj.scale = (part.scale.x, part.scale.y, part.scale.z)
        
        return obj
    
    def load_zms_mesh(self, mesh_path, base_path):
        """Load a ZMS mesh file and return mesh data"""
        # Try to resolve the mesh path
        full_path = None
        
        # Try relative to ZSC location
        candidate = base_path / mesh_path
        if candidate.exists():
            full_path = candidate
        else:
            # Try going up to find 3DDATA root
            current = base_path
            for _ in range(10):
                if current.name.upper() == "3DDATA":
                    candidate = current / mesh_path
                    if candidate.exists():
                        full_path = candidate
                        break
                if current.parent == current:
                    break
                current = current.parent
        
        if not full_path:
            self.report({'WARNING'}, f"Mesh not found: {mesh_path}")
            return None
        
        try:
            from .rose.zms import ZMS
            zms = ZMS(str(full_path))
            
            # Create mesh data
            mesh_name = Path(mesh_path).stem
            mesh = bpy.data.meshes.new(mesh_name)
            
            # Mesh vertices are in local object space - use as-is from file
            # Coordinate transform is applied via object transform, not vertex positions
            verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
            
            # Faces
            faces = [(int(i.x), int(i.y), int(i.z)) for i in zms.indices]
            
            mesh.from_pydata(verts, [], faces)
            
            # UVs
            if zms.uv1_enabled():
                mesh.uv_layers.new(name="UVMap")
                for loop_idx, loop in enumerate(mesh.loops):
                    vi = loop.vertex_index
                    u = zms.vertices[vi].uv1.x
                    v = zms.vertices[vi].uv1.y
                    mesh.uv_layers["UVMap"].data[loop_idx].uv = (u, 1-v)
            
            mesh.update(calc_edges=True)
            return mesh
            
        except Exception as e:
            self.report({'WARNING'}, f"Failed to load mesh {mesh_path}: {str(e)}")
            return None
    
    def create_material(self, zsc_mat, base_path):
        """Create a Blender material from ZSC material data"""
        mat_name = Path(zsc_mat.path).stem
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        
        # Create nodes
        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (400, 0)
        
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        # Load texture if requested
        if self.load_textures:
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-400, 0)
            
            # Try to find texture file
            texture_path = self.resolve_texture(zsc_mat.path, base_path)
            if texture_path:
                try:
                    tex_node.image = bpy.data.images.load(str(texture_path))
                except:
                    self.report({'WARNING'}, f"Failed to load texture: {texture_path}")
            
            links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
            
            # Handle alpha
            if zsc_mat.alpha_enabled or zsc_mat.alpha != 1.0:
                mat.blend_method = 'BLEND'
                if zsc_mat.alpha != 1.0:
                    links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
                    bsdf.inputs['Alpha'].default_value = zsc_mat.alpha
        
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        
        # Material properties
        if zsc_mat.two_sided:
            mat.use_backface_culling = False
        
        return mat
    
    def resolve_texture(self, texture_path, base_path):
        """Try to find the actual texture file"""
        # Try exact path relative to ZSC
        for ext in self.texture_extensions:
            candidate = base_path / Path(texture_path).with_suffix(ext)
            if candidate.exists():
                return candidate
        
        # Try to find 3DDATA root
        current = base_path
        for _ in range(10):
            if current.name.upper() == "3DDATA":
                for ext in self.texture_extensions:
                    candidate = current / Path(texture_path).with_suffix(ext)
                    if candidate.exists():
                        return candidate
                break
            if current.parent == current:
                break
            current = current.parent
        
        return None


def menu_func_import(self, context):
    self.layout.operator(ImportZSC.bl_idname, text="ROSE Scene (.zsc)")


def register():
    bpy.utils.register_class(ImportZSC)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportZSC)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)