from pathlib import Path, PureWindowsPath
import os
import re

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


def normalize_vfs_path(path_str):
    """Split a ZSC-stored path on both separators into OS-native parts.

    ZSC files store Windows-style backslash paths, which Path() treats as
    one component on POSIX, breaking the 3DDATA walk-up below.
    """
    parts = [p for p in re.split(r'[\\/]+', path_str) if p not in ('', '.')]
    return Path(*parts) if parts else Path('')


def existing_case_insensitive(path):
    """Return path if it exists, else a case-insensitive sibling match."""
    if path.exists():
        return path
    parent, name = path.parent, path.name.lower()
    try:
        for entry in os.listdir(parent):
            if entry.lower() == name:
                return parent / entry
    except OSError:
        pass
    return None


class ImportZSC(bpy.types.Operator, ImportHelper):
    bl_idname = "rose.import_zsc"
    bl_label = "ROSE Scene (.zsc)"
    bl_options = {"PRESET"}

    filename_ext = ".zsc"
    filter_glob: StringProperty(default="*.zsc;*.ZSC", options={"HIDDEN"})
    
    load_textures: BoolProperty(
        name="Load textures",
        description="Automatically load textures for materials",
        default=True,
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
            skipped_oob = 0
            if self.load_cnst_objects:
                for obj_inst in ifo.cnst_objects:
                    if obj_inst.object_id < len(zsc.objects):
                        self.spawn_object(
                            context, collection, zsc, obj_inst,
                            material_cache, mesh_cache, filepath.parent
                        )
                    else:
                        skipped_oob += 1
                        self.report({'WARNING'},
                            f"CNST '{obj_inst.object_name}' references missing "
                            f"ZSC object {obj_inst.object_id} - skipped")

            if self.load_deco_objects:
                for obj_inst in ifo.deco_objects:
                    if obj_inst.object_id < len(zsc.objects):
                        self.spawn_object(
                            context, collection, zsc, obj_inst,
                            material_cache, mesh_cache, filepath.parent
                        )
                    else:
                        skipped_oob += 1
                        self.report({'WARNING'},
                            f"DECO '{obj_inst.object_name}' references missing "
                            f"ZSC object {obj_inst.object_id} - skipped")
            if skipped_oob:
                self.report({'WARNING'},
                    f"Skipped {skipped_oob} IFO instances with out-of-range object ids")
            # Blocks this preview importer intentionally does not spawn
            # (they need armatures / game systems, not static meshes).
            others = {
                'NPC': len(ifo.npcs),
                'monster spawns': len(ifo.monster_spawns),
                'animated': len(ifo.animated_objects),
                'effects': len(ifo.effect_objects),
                'sounds': len(ifo.sound_objects),
                'warps': len(ifo.warps),
                'events': len(ifo.event_objects),
            }
            present = {k: v for k, v in others.items() if v}
            if present:
                self.report({'INFO'},
                    "IFO blocks not spawned by this importer: " +
                    ", ".join(f"{v} {k}" for k, v in present.items()))
        else:
            # No IFO file - just spawn all ZSC objects at origin for preview
            self.report({'INFO'}, "No IFO file found, spawning all objects at origin")
            for obj_id, zsc_obj in enumerate(zsc.objects):
                # Create a fake IfoObject (identity rotation; utils Quat
                # order is x,y,z,w, so (1,0,0,0) would be a 180-degree flip)
                fake_ifo_obj = type('obj', (object,), {
                    'object_id': obj_id,
                    'object_name': f"Object_{obj_id}",
                    'position': Vector3(0, 0, 0),
                    'rotation': Quat(0, 0, 0, 1),
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
        
        # IFO positions are absolute world cm; the converted position is
        # absolute Blender meters - no world offset is applied (offsets only
        # misalign objects vs terrain; see architecture/blender-importer.md).
        pos = ifo_object.position
        bx, by, bz = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
        parent_empty.location = (bx, by, bz)
        
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
            else:
                part_objects.append(None)

        # Second pass: sibling part parenting (part.parent indexes parts).
        # Without an armature, bone/dummy attachments cannot resolve, so
        # they are kept as custom properties instead of being dropped.
        for part_idx, part in enumerate(zsc_obj.parts):
            part_obj = part_objects[part_idx]
            if part_obj is None:
                continue
            if part.parent is not None:
                if 0 <= part.parent < len(part_objects) and part_objects[part.parent] is not None:
                    if part.parent == part_idx:
                        self.report({'WARNING'},
                            f"{obj_name}: part {part_idx} parents to itself - ignored")
                    else:
                        try:
                            part_obj.parent = part_objects[part.parent]
                        except RuntimeError:
                            self.report({'WARNING'},
                                f"{obj_name}: part {part_idx} parenting loop - kept on object root")
                else:
                    self.report({'WARNING'},
                        f"{obj_name}: part {part_idx} has invalid parent "
                        f"{part.parent} - kept on object root")

        # Spawn effects as placeholder empties so effect placement / ids
        # survive the import instead of being silently dropped.
        for eff_idx, eff in enumerate(zsc_obj.effects):
            eff_obj = bpy.data.objects.new(f"{obj_name}_effect{eff_idx}", None)
            eff_obj.empty_display_type = 'PLAIN_AXES'
            eff_obj.empty_display_size = 0.3
            eff_obj["rose_effect_id"] = eff.effect_id
            eff_obj["rose_effect_type"] = int(eff.effect_type)
            if eff.parent is not None:
                eff_obj["rose_parent_part"] = eff.parent
            collection.objects.link(eff_obj)
            if eff.parent is not None and 0 <= eff.parent < len(part_objects) \
                    and part_objects[eff.parent] is not None:
                eff_obj.parent = part_objects[eff.parent]
            else:
                eff_obj.parent = parent_empty
            pos = eff.position
            eff_obj.location = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
            eff_obj.rotation_mode = 'QUATERNION'
            eff_obj.rotation_quaternion = self.convert_rose_quaternion_to_blender(eff.rotation)
            eff_obj.scale = (eff.scale.x, eff.scale.y, eff.scale.z)

        return parent_empty
    
    def spawn_part(self, context, zsc, part, part_idx, material_cache, mesh_cache, base_path, obj_name):
        """Spawn a single object part (mesh instance)"""
        mesh_id = part.mesh_id
        material_id = part.material_id

        # Bounds checks (the client skips out-of-range parts); without them
        # one bad part aborts the whole import with an IndexError.
        if mesh_id >= len(zsc.meshes):
            self.report({'WARNING'},
                f"{obj_name}: part {part_idx} references missing mesh {mesh_id} - skipped")
            return None
        if material_id >= len(zsc.materials):
            self.report({'WARNING'},
                f"{obj_name}: part {part_idx} references missing material {material_id} - skipped")
            return None

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

        # Apply material. Materials live on the (possibly shared) mesh data,
        # so assigning a different material requires a mesh copy first -
        # otherwise this part would overwrite every part sharing the mesh.
        mat = material_cache.get(material_id)
        if mat is not None:
            if len(mesh_data.materials) and mesh_data.materials[0] != mat:
                mesh_data = mesh_data.copy()
                obj.data = mesh_data
            if len(mesh_data.materials):
                mesh_data.materials[0] = mat
            else:
                mesh_data.materials.append(mat)

        # Attachments the preview cannot resolve are kept as custom
        # properties (bone/dummy need an armature, animation a ZMO).
        if part.bone_index is not None:
            obj["rose_bone_index"] = part.bone_index
        if part.dummy_index is not None:
            obj["rose_dummy_index"] = part.dummy_index
        if part.animation_path:
            obj["rose_animation_path"] = part.animation_path
        if part.collision_shape is not None:
            obj["rose_collision_shape"] = int(part.collision_shape)
            obj["rose_collision_flags"] = part.collision_flags

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
        # Stored paths are Windows-style; normalize separators first so the
        # lookups below also work on POSIX.
        rel_path = normalize_vfs_path(mesh_path)

        def resolve(candidate):
            hit = existing_case_insensitive(candidate)
            return hit

        # Try to resolve the mesh path
        full_path = resolve(base_path / rel_path)

        if not full_path:
            # Try going up to find 3DDATA root
            current = base_path
            for _ in range(10):
                if current.name.upper() == "3DDATA":
                    full_path = resolve(current / rel_path)
                    if full_path:
                        break
                    # Stored paths may include the 3DDATA root as a prefix
                    parts = rel_path.parts
                    if parts and parts[0].upper() == "3DDATA":
                        full_path = resolve(current / Path(*parts[1:]))
                        if full_path:
                            break
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
            
            # Mesh-local vertices use the same (x, -y, z) mirror as the
            # placement transforms (see import_map.load_zms_mesh). The mirror
            # has determinant -1, so triangle winding is swapped as well.
            verts = [(v.position.x, -v.position.y, v.position.z) for v in zms.vertices]

            # Faces (winding swapped to match the mirror above)
            faces = [(int(i.x), int(i.z), int(i.y)) for i in zms.indices]
            
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
        
        # Load texture if requested. The node is only kept (and Base
        # Color only linked) when an image actually loads; otherwise the
        # material renders black with no indication of why.
        texture_loaded = False
        if self.load_textures:
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-400, 0)

            # Try to find texture file. Empty/"NULL" paths use the
            # client's specular fallback texture instead of no texture.
            texture_path = self.resolve_texture(zsc_mat.path, base_path)
            if texture_path is None and zsc_mat.path.strip().upper() in ("", "NULL"):
                texture_path = self.resolve_texture(
                    "3DDATA\\EFFECT\\ETC\\SPECULAR_SPHEREMAP.DDS", base_path)
                if texture_path is None:
                    texture_path = self.resolve_texture(
                        "ETC\\SPECULAR_SPHEREMAP.DDS", base_path)
            if texture_path:
                try:
                    tex_node.image = bpy.data.images.load(
                        str(texture_path), check_existing=True)
                    texture_loaded = True
                except Exception as e:
                    self.report({'WARNING'},
                        f"Failed to load texture {texture_path.name}: {e}")

            if texture_loaded:
                links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
            else:
                nodes.remove(tex_node)

        # Alpha mode matches the client (objects.rs): only alpha_enabled
        # enables transparency (Mask with threshold, else Blend); texture
        # alpha alone never does, and material alpha is a fallback value.
        if zsc_mat.alpha_enabled:
            if zsc_mat.alpha_test is not None:
                mat.blend_method = 'CLIP'
                mat.alpha_threshold = zsc_mat.alpha_test
            else:
                mat.blend_method = 'BLEND'
            if texture_loaded:
                links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
            else:
                bsdf.inputs['Alpha'].default_value = zsc_mat.alpha
            if hasattr(mat, 'show_transparent_back'):
                mat.show_transparent_back = False
        else:
            mat.blend_method = 'OPAQUE'

        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

        # Single-sided materials must cull backfaces (client cull_mode);
        # the old code was a no-op that left everything double-sided.
        mat.use_backface_culling = not zsc_mat.two_sided

        return mat
    
    def resolve_texture(self, texture_path, base_path):
        """Try to find the actual texture file"""
        rel = normalize_vfs_path(texture_path)

        def attempt(candidate):
            return existing_case_insensitive(candidate)

        # Try exact path relative to ZSC
        for ext in self.texture_extensions:
            hit = attempt(base_path / rel.with_suffix(ext))
            if hit:
                return hit

        # Try to find 3DDATA root
        current = base_path
        for _ in range(10):
            if current.name.upper() == "3DDATA":
                parts = rel.parts
                stripped = Path(*parts[1:]) if parts and parts[0].upper() == "3DDATA" else None
                for ext in self.texture_extensions:
                    hit = attempt(current / rel.with_suffix(ext))
                    if hit:
                        return hit
                    # Stored paths may include the 3DDATA root as a prefix
                    if stripped:
                        hit = attempt(current / stripped.with_suffix(ext))
                        if hit:
                            return hit
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