"""
Import ROSE Online zone with converted terrain (.mesh.bin files) and assets from ZON file.

This plugin combines the functionality of import_map.py (for loading ZON metadata and objects)
and import_converted_terrain.py (for loading converted .mesh.bin terrain blocks).

When use_new_terrain is enabled in the Rust game client, it loads:
- block_X_Y.mesh.bin - Pre-converted mesh data with positions, normals, UVs, tangents, indices
- block_X_Y_albedo.png - Baked albedo texture for each block
- block_X_Y_normal.png - Generated normal map for each block

The ZON file provides zone metadata and the directory structure determines which blocks to load.

Binary mesh format (from rose-zone-converter):
- [u32 vertex_count]
- [positions: [f32; 3] * vertex_count]
- [normals: [f32; 3] * vertex_count]
- [uvs: [f32; 2] * vertex_count]
- [u32 has_tangents] (1 or 0)
- [tangents: [f32; 4] * vertex_count] (if has_tangents == 1)
- [u32 index_count]
- [indices: u32 * index_count]

Coordinate Systems:
- ROSE Online: Right-handed, Z-up, X=right, Y=forward, Z=up, units in centimeters
- Bevy (rose-zone-converter): Right-handed, Y-up, X=horizontal, Y=height, Z=depth, units in meters  
- Blender: Right-handed, Z-up, X=horizontal, Y=depth, Z=height, units in meters

Conversion from Bevy to Blender:
  blender_x = bevy_x - world_offset_x
  blender_y = bevy_z - world_offset_y  # Bevy Z (depth) becomes Blender Y
  blender_z = bevy_y                   # Bevy Y (height) becomes Blender Z

Block positioning (from Rust zone_loader.rs lines 3317-3318, 3424):
  offset_x = 160.0 * block_x
  offset_y = 160.0 * (65.0 - block_y)
  Transform::from_xyz(offset_x - 5200.0, 0.0, -offset_y + 5200.0)  # In centimeters
"""

from pathlib import Path
import struct
import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper

# Import ROSE file parsers from the existing rose module
from .rose.zon import Zon
from .rose.him import Him
from .rose.til import Til
from .rose.ifo import Ifo
from .rose.zsc import Zsc
from .rose.utils import Vector2, Vector3, list_2d, convert_rose_position_to_blender


class ImportCombinedZone(bpy.types.Operator, ImportHelper):
    """Import ROSE zone with converted terrain (.mesh.bin) and assets from ZON file"""
    bl_idname = "import_combined_zone.zon"
    bl_label = "Import ROSE Zone (Converted Terrain + Assets)"
    bl_options = {"PRESET"}

    filename_ext = ".zon"
    # Show both .zon and .mesh.bin files in browser, but only allow selecting .zon
    filter_glob: StringProperty(default="*.zon;*.mesh.bin", options={"HIDDEN"})

    # Terrain import options
    load_terrain: BoolProperty(
        name="Load Converted Terrain",
        description="Load converted terrain blocks (.mesh.bin files)",
        default=True,
    )

    load_normal_map: BoolProperty(
        name="Load Normal Maps",
        description="Load the generated normal maps for terrain if available",
        default=True,
    )

    merge_terrain_blocks: BoolProperty(
        name="Merge Terrain Blocks",
        description="Merge all terrain blocks into a single mesh (faster rendering)",
        default=False,
    )

    # Object import options  
    load_cnst_objects: BoolProperty(
        name="Load CNST Objects",
        description="Load construction objects from IFO files",
        default=True,
    )

    load_deco_objects: BoolProperty(
        name="Load DECO Objects",
        description="Load decoration objects from IFO files",
        default=True,
    )

    load_texture: BoolProperty(
        name="Load Textures",
        description="Automatically detect and load textures",
        default=True,
    )

    # World positioning options
    world_offset_x: FloatProperty(
        name="World Offset X",
        description="World offset in meters for X axis (default: 52.0m = 5200cm)",
        default=52.0,
    )

    world_offset_y: FloatProperty(
        name="World Offset Y", 
        description="World offset in meters for Y axis (default: 52.0m = 5200cm)",
        default=52.0,
    )

    # Debug options
    verbose_logging: BoolProperty(
        name="Verbose Logging",
        description="Log detailed information during import",
        default=False,
    )

    def __init__(self, *args, **kwargs):
        """Initialize with caches for path resolution"""
        super().__init__(*args, **kwargs)
        self._texture_path_cache = {}
        self._mesh_path_cache = {}
        self._3ddata_root_cache = None

    # =========================================================================
    # PATH RESOLUTION HELPERS
    # =========================================================================

    def _get_3ddata_root(self, zon_filepath):
        """Get 3DDATA root directory with caching."""
        if self._3ddata_root_cache is not None:
            return self._3ddata_root_cache
            
        zon_path = Path(zon_filepath).resolve()
        current = zon_path.parent
        max_depth = 10
        depth = 0

        while depth < max_depth:
            if current.name.upper() == "3DDATA":
                self._3ddata_root_cache = current
                return current
            current = current.parent
            depth += 1
        
        return None

    def resolve_texture_path(self, zon_filepath, texture_path):
        """Resolve texture path from ZON to actual file path with caching."""
        cache_key = (zon_filepath, texture_path)
        if cache_key in self._texture_path_cache:
            return self._texture_path_cache[cache_key]
        
        import os
        zon_path = Path(zon_filepath).resolve()
        root_3ddata = self._get_3ddata_root(zon_filepath)

        if not root_3ddata:
            self._texture_path_cache[cache_key] = None
            return None

        # Normalize path separators
        texture_relative = texture_path.replace('\\', os.sep)
        texture_name = Path(texture_relative).name

        # Try exact path first (fastest)
        full_path = root_3ddata / texture_relative
        if full_path.exists():
            result = str(full_path)
            self._texture_path_cache[cache_key] = result
            return result

        # Try parent of 3DDATA
        full_path = root_3ddata.parent / texture_relative
        if full_path.exists():
            result = str(full_path)
            self._texture_path_cache[cache_key] = result
            return result

        # Try common texture directories
        common_dirs = [
            root_3ddata / "MAPS",
            root_3ddata / "MAPS" / "JUNON",
            root_3ddata / "MAPS" / "ELDEON",
            root_3ddata / "MAPS" / "LUNAR",
        ]
        
        # Try to extract planet name from texture path
        path_parts = texture_relative.upper().split(os.sep)
        if "JUNON" in path_parts:
            common_dirs.append(root_3ddata / "MAPS" / "JUNON")
        if "ELDEON" in path_parts:
            common_dirs.append(root_3ddata / "MAPS" / "ELDEON")
        if "LUNAR" in path_parts:
            common_dirs.append(root_3ddata / "MAPS" / "LUNAR")
        
        # Direct file existence check in common directories
        for base_dir in common_dirs:
            if base_dir.exists():
                candidate = base_dir / texture_name
                if candidate.exists():
                    result = str(candidate)
                    self._texture_path_cache[cache_key] = result
                    return result

        # Last resort: case-insensitive search with limited scope
        search_dirs = [d for d in common_dirs if d.exists()]
        if not search_dirs:
            search_dirs = [root_3ddata]
        
        texture_name_lower = texture_name.lower()
        for search_dir in search_dirs:
            try:
                for item in search_dir.iterdir():
                    if item.is_file() and item.name.lower() == texture_name_lower:
                        result = str(item)
                        self._texture_path_cache[cache_key] = result
                        return result
                    if item.is_dir():
                        for subitem in item.iterdir():
                            if subitem.is_file() and subitem.name.lower() == texture_name_lower:
                                result = str(subitem)
                                self._texture_path_cache[cache_key] = result
                                return result
            except Exception:
                continue

        if self.verbose_logging:
            self.report({'WARNING'}, f"Texture not found: {texture_relative}")
        self._texture_path_cache[cache_key] = None
        return None

    def resolve_mesh_path(self, mesh_path, base_path):
        """Resolve mesh path with caching"""
        cache_key = (str(base_path), mesh_path)
        if cache_key in self._mesh_path_cache:
            return self._mesh_path_cache[cache_key]
        
        candidate = base_path / mesh_path
        if candidate.exists():
            self._mesh_path_cache[cache_key] = candidate
            return candidate
        
        # Try case-insensitive search
        try:
            mesh_name_lower = Path(mesh_path).name.lower()
            for file in base_path.rglob('*'):
                if file.is_file() and file.name.lower() == mesh_name_lower:
                    self._mesh_path_cache[cache_key] = file
                    return file
        except Exception:
            pass
        
        self._mesh_path_cache[cache_key] = None
        return None

    # =========================================================================
    # TERRAIN MESH LOADING (from .mesh.bin files)
    # =========================================================================

    def load_mesh_binary(self, filepath):
        """
        Load the binary mesh file from rose-zone-converter output.
        
        Returns:
            dict with 'positions', 'normals', 'uvs', 'tangents' (optional), 'indices'
        """
        mesh_data = {}
        
        with open(filepath, 'rb') as f:
            # Read vertex count (u32)
            vertex_count = struct.unpack('<I', f.read(4))[0]
            
            # Read positions (f32 x 3 per vertex) - Bevy Y-up format
            positions = []
            for _ in range(vertex_count):
                x, y, z = struct.unpack('<fff', f.read(12))
                positions.append((x, y, z))
            
            # Read normals (f32 x 3 per vertex)
            normals = []
            for _ in range(vertex_count):
                x, y, z = struct.unpack('<fff', f.read(12))
                normals.append((x, y, z))
            
            # Read UVs (f32 x 2 per vertex)
            uvs = []
            for _ in range(vertex_count):
                u, v = struct.unpack('<ff', f.read(8))
                # UVs are used as-is from the converter
                # Both Bevy and Blender use same UV convention: (0,0)=bottom-left
                uvs.append((u, v))
            
            # Read has_tangents flag (u32)
            has_tangents = struct.unpack('<I', f.read(4))[0]
            
            # Read tangents if present (f32 x 4 per vertex)
            tangents = None
            if has_tangents:
                tangents = []
                for _ in range(vertex_count):
                    x, y, z, w = struct.unpack('<ffff', f.read(16))
                    tangents.append((x, y, z, w))
            
            # Read index count (u32)
            index_count = struct.unpack('<I', f.read(4))[0]
            
            # Read indices (u32 per index)
            indices = []
            for _ in range(index_count):
                idx = struct.unpack('<I', f.read(4))[0]
                indices.append(idx)
        
        mesh_data['positions'] = positions
        mesh_data['normals'] = normals
        mesh_data['uvs'] = uvs
        mesh_data['tangents'] = tangents
        mesh_data['indices'] = indices
        mesh_data['vertex_count'] = vertex_count
        mesh_data['index_count'] = index_count
        
        return mesh_data

    # =========================================================================
    # MATERIAL CREATION
    # =========================================================================

    def create_terrain_material(self, block_name, albedo_path, normal_path=None):
        """
        Create a PBR material with albedo and optional normal map for terrain.
        
        Args:
            block_name: Name for the material
            albedo_path: Path to the albedo texture
            normal_path: Optional path to the normal map
            
        Returns:
            Blender material or None if creation failed
        """
        try:
            mat = bpy.data.materials.new(name=f"ROSE_Terrain_{block_name}")
            mat.use_nodes = True
            
            # CRITICAL: Terrain must be OPAQUE to match Rust implementation
            # The baked albedo is fully opaque - transparency was handled during baking
            mat.blend_method = 'OPAQUE'
            
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            
            nodes.clear()
            
            # Albedo texture node
            albedo_node = nodes.new(type='ShaderNodeTexImage')
            albedo_node.location = (-600, 100)
            albedo_image = bpy.data.images.load(albedo_path)
            albedo_node.image = albedo_image
            
            # Principled BSDF
            bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
            bsdf.location = (0, 0)
            # Set default PBR values
            if 'Roughness' in bsdf.inputs:
                bsdf.inputs['Roughness'].default_value = 0.8
            elif len(bsdf.inputs) > 9:
                bsdf.inputs[9].default_value = 0.8
            if 'Metallic' in bsdf.inputs:
                bsdf.inputs['Metallic'].default_value = 0.0
            elif len(bsdf.inputs) > 10:
                bsdf.inputs[10].default_value = 0.0
            
            # Material Output
            output = nodes.new(type='ShaderNodeOutputMaterial')
            output.location = (400, 0)
            
            # Connect albedo color to base color
            links.new(albedo_node.outputs["Color"], bsdf.inputs["Base Color"])
            links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
            
            # Add normal map if available
            if normal_path and self.load_normal_map and Path(normal_path).exists():
                normal_node = nodes.new(type='ShaderNodeTexImage')
                normal_node.location = (-600, -100)
                normal_image = bpy.data.images.load(normal_path)
                
                # CRITICAL: Normal maps must use Non-Color color space
                normal_image.colorspace_settings.name = 'Non-Color'
                
                normal_node.image = normal_image
                
                normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                normal_map_node.location = (-200, -100)
                
                # Blender 4.x uses "Color" input for normal map
                links.new(normal_node.outputs["Color"], normal_map_node.inputs["Color"])
                links.new(normal_map_node.outputs["Normal"], bsdf.inputs["Normal"])
            
            return mat
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create terrain material: {str(e)}")
            return None

    def create_object_material(self, zsc_mat, base_path):
        """Create material from ZSC material data for objects"""
        import os
        mat_name = Path(zsc_mat.path).stem
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()
        
        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (400, 0)
        
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        if self.load_texture:
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-400, 0)
            
            texture_path = self.resolve_mesh_path(zsc_mat.path, base_path)
            if texture_path:
                try:
                    tex_node.image = bpy.data.images.load(str(texture_path))
                except Exception:
                    pass
            
            links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
            
            if zsc_mat.alpha_enabled or zsc_mat.alpha != 1.0:
                mat.blend_method = 'BLEND'
                if zsc_mat.alpha != 1.0:
                    links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
                    bsdf.inputs['Alpha'].default_value = zsc_mat.alpha
        
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        
        if zsc_mat.two_sided:
            mat.use_backface_culling = False
        
        return mat

    # =========================================================================
    # MESH CREATION FROM BINARY DATA
    # =========================================================================

    def create_mesh_from_binary_data(self, mesh_data, block_name, context):
        """
        Create a Blender mesh from the parsed binary mesh data.
        
        Args:
            mesh_data: Dictionary with positions, normals, uvs, tangents, indices
            block_name: Name for the mesh object
            context: Blender context
            
        Returns:
            Blender mesh object (without world positioning - that's handled separately)
        """
        # Create mesh and object
        mesh = bpy.data.meshes.new(name=f"ROSE_Terrain_{block_name}")
        obj = bpy.data.objects.new(f"ROSE_Terrain_{block_name}", mesh)
        
        # Convert positions from Bevy Y-up to Blender Z-up
        # Bevy: X=horizontal, Y=height, Z=depth
        # Blender: X=horizontal, Y=depth, Z=height
        blender_positions = []
        for x, y, z in mesh_data['positions']:
            blender_x = x  # Keep X as-is (will apply world offset via object location)
            blender_y = z  # Bevy Z (depth) becomes Blender Y
            blender_z = y  # Bevy Y (height) becomes Blender Z
            blender_positions.append((blender_x, blender_y, blender_z))
        
        # Convert normals: swap Y and Z for coordinate system change
        blender_normals = []
        for x, y, z in mesh_data['normals']:
            blender_normals.append((x, z, y))
        
        # Create vertices with triangles
        indices = mesh_data['indices']
        triangles = []
        for i in range(0, len(indices), 3):
            if i + 2 < len(indices):
                triangles.append((indices[i], indices[i+1], indices[i+2]))
        
        mesh.from_pydata(blender_positions, [], triangles)
        
        # Add UV layer
        if mesh_data['uvs']:
            uv_layer = mesh.uv_layers.new(name="UVMap")
            for loop_idx in range(len(mesh.loops)):
                vertex_idx = mesh.loops[loop_idx].vertex_index
                if vertex_idx < len(mesh_data['uvs']):
                    uv_layer.data[loop_idx].uv = mesh_data['uvs'][vertex_idx]
        
        # Add tangent data if available (Blender 4.x uses attributes)
        if mesh_data['tangents']:
            if "Tangent" not in mesh.attributes:
                mesh.attributes.new("Tangent", 'FLOAT_VECTOR', 'POINT')
            tangent_attr = mesh.attributes["Tangent"]
            for i, tangent in enumerate(mesh_data['tangents']):
                if i < len(tangent_attr.data):
                    tangent_attr.data[i].vector = tangent[:3]  # Use XYZ, ignore W
        
        return obj

    # =========================================================================
    # TERRAIN IMPORT (from .mesh.bin files)
    # =========================================================================

    def import_terrain_blocks(self, context, zone_path, block_files):
        """
        Import terrain blocks from .mesh.bin files.
        
        Args:
            context: Blender context
            zone_path: Path to the zone directory containing mesh files
            block_files: List of Path objects pointing to .mesh.bin files
            
        Returns:
            Collection containing all imported terrain blocks
        """
        wm = context.window_manager
        
        # Create collection for terrain blocks
        terrain_collection = bpy.data.collections.get("ROSE_Terrain_Blocks")
        if not terrain_collection:
            terrain_collection = bpy.data.collections.new("ROSE_Terrain_Blocks")
            context.scene.collection.children.link(terrain_collection)
        
        created_objects = []
        total_files = len(block_files)
        
        if self.merge_terrain_blocks:
            # Merge all blocks into single mesh
            all_positions = []
            all_normals = []
            all_uvs = []
            all_tangents = []
            all_indices = []
            vertex_offset = 0
            
            first_albedo = None
            first_normal = None
            
            for idx, block_file in enumerate(block_files):
                wm.progress_update(20 + (idx * 60 // total_files))
                
                if not block_file.exists():
                    continue
                
                # Load mesh data
                mesh_data = self.load_mesh_binary(str(block_file))
                
                # Store texture paths from first block
                if first_albedo is None:
                    stem = block_file.stem
                    if stem.endswith('.mesh'):
                        stem = stem[:-5]
                    first_albedo = block_file.with_name(f"{stem}_albedo.png")
                    first_normal = block_file.with_name(f"{stem}_normal.png")
                
                # Parse block coordinates from filename (e.g., "block_31_30.mesh.bin")
                block_name = block_file.stem
                if block_name.endswith('.mesh'):
                    block_name = block_name[:-5]
                
                parts = block_name.split('_')
                offset_x, offset_y = 0.0, 0.0
                if len(parts) >= 3 and parts[0] == 'block':
                    try:
                        block_x = int(parts[1])
                        block_y = int(parts[2])
                        
                        # Match Rust implementation exactly (zone_loader.rs lines 3317-3318, 3424)
                        # offset in centimeters, then convert to meters
                        rust_offset_x = 160.0 * block_x  # cm
                        rust_offset_y = 160.0 * (65.0 - block_y)  # cm
                        
                        # Apply world centering: Transform::from_xyz(offset_x - 5200, 0, -offset_y + 5200)
                        # Convert to meters and apply coordinate system conversion
                        offset_x = (rust_offset_x - 5200.0) / 100.0  # cm to m
                        offset_y = (-rust_offset_y + 5200.0) / 100.0  # cm to m, inverted Y
                        
                    except ValueError:
                        if self.verbose_logging:
                            self.report({'WARNING'}, f"Could not parse block coordinates from {block_name}")
                
                # Apply offset to positions (Bevy Z-up to Blender Z-up conversion)
                for x, y, z in mesh_data['positions']:
                    blender_x = x + offset_x  # X stays same
                    blender_y = z + offset_y  # Bevy Z becomes Blender Y
                    blender_z = y  # Bevy Y (height) becomes Blender Z
                    all_positions.append((blender_x, blender_y, blender_z))
                
                # Transform normals: swap Y and Z
                for x, y, z in mesh_data['normals']:
                    all_normals.append((x, z, y))
                
                all_uvs.extend(mesh_data['uvs'])
                
                if mesh_data['tangents']:
                    all_tangents.extend(mesh_data['tangents'])
                
                # Offset indices
                offset_indices = [i + vertex_offset for i in mesh_data['indices']]
                all_indices.extend(offset_indices)
                
                vertex_offset += mesh_data['vertex_count']
            
            # Create merged mesh
            wm.progress_update(80)
            merged_data = {
                'positions': all_positions,
                'normals': all_normals,
                'uvs': all_uvs,
                'tangents': all_tangents if all_tangents else None,
                'indices': all_indices,
                'vertex_count': vertex_offset,
                'index_count': len(all_indices)
            }
            
            obj = self.create_mesh_from_binary_data(merged_data, "Merged_Terrain", context)
            terrain_collection.objects.link(obj)
            created_objects.append(obj)
            
            # Create material using first block's textures
            wm.progress_update(90)
            if first_albedo and first_albedo.exists():
                mat = self.create_terrain_material("Merged_Terrain", str(first_albedo), 
                                                   str(first_normal) if first_normal else None)
                if mat:
                    obj.data.materials.append(mat)
                    for poly in obj.data.polygons:
                        poly.material_index = 0
                    if self.verbose_logging:
                        self.report({'INFO'}, f"Material assigned to merged terrain")
            elif first_albedo:
                self.report({'ERROR'}, f"Albedo not found: {first_albedo}")
        
        else:
            # Import each block as separate mesh with proper positioning
            for idx, filepath in enumerate(block_files):
                wm.progress_update(20 + (idx * 60 // total_files))
                
                if not filepath.exists():
                    continue
                
                # Parse block coordinates from filename
                stem = filepath.stem
                if stem.endswith('.mesh'):
                    stem = stem[:-5]
                
                parts = stem.split('_')
                block_x, block_y = 0, 0
                if len(parts) >= 3 and parts[0] == 'block':
                    try:
                        block_x = int(parts[1])
                        block_y = int(parts[2])
                    except ValueError:
                        if self.verbose_logging:
                            self.report({'WARNING'}, f"Could not parse coordinates from {stem}")
                
                # Calculate world position matching Rust implementation (zone_loader.rs lines 3317-3318, 3424)
                # Rust: offset_x = 160.0 * block_x, offset_y = 160.0 * (65.0 - block_y) in cm
                # Transform::from_xyz(offset_x - 5200.0, 0.0, -offset_y + 5200.0)
                rust_offset_x = 160.0 * block_x
                rust_offset_y = 160.0 * (65.0 - block_y)
                
                # Convert to meters: divide by 100 (cm to m)
                # Rust uses cm, Blender uses meters
                world_pos_x = rust_offset_x / 100.0 - 52.0  # offset in meters minus centering
                world_pos_y = -rust_offset_y / 100.0 + 52.0  # Inverted Y axis, converted to meters
                
                if self.verbose_logging:
                    self.report({'INFO'}, f"Block {stem}: World position ({world_pos_x:.1f}, {world_pos_y:.1f})")
                
                # Load mesh data
                mesh_data = self.load_mesh_binary(str(filepath))
                
                albedo_path = filepath.with_name(f"{stem}_albedo.png")
                normal_path = filepath.with_name(f"{stem}_normal.png")
                
                if not albedo_path.exists():
                    self.report({'ERROR'}, f"Albedo texture not found for block {stem}: {albedo_path}")
                    continue
                
                # Create mesh object (without world offset - handled by object location)
                obj = self.create_mesh_from_binary_data(mesh_data, stem, context)
                
                # Position the block in world space (matching Rust implementation)
                obj.location = (world_pos_x, world_pos_y, 0.0)
                
                terrain_collection.objects.link(obj)
                created_objects.append(obj)
                
                # Create and assign unique material per block
                mat = self.create_terrain_material(stem, str(albedo_path), str(normal_path))
                if mat:
                    obj.data.materials.append(mat)
                    for poly in obj.data.polygons:
                        poly.material_index = 0
                    if self.verbose_logging:
                        self.report({'INFO'}, f"Block {stem}: Material '{mat.name}' assigned")
                else:
                    self.report({'ERROR'}, f"Failed to create material for block {stem}")
        
        wm.progress_update(95)
        
        return terrain_collection, created_objects

    # =========================================================================
    # OBJECT IMPORT (from IFO/ZSC files)
    # =========================================================================

    def spawn_object(self, context, collection, zsc, ifo_object, material_cache, mesh_cache, base_path):
        """Spawn a ZSC object from IFO data with correct coordinate conversion"""
        zsc_obj = zsc.objects[ifo_object.object_id]
        
        # Create parent empty for this object instance
        obj_name = ifo_object.object_name if ifo_object.object_name else f"Object_{ifo_object.object_id}"
        parent_empty = bpy.data.objects.new(obj_name, None)
        parent_empty.empty_display_type = 'PLAIN_AXES'
        parent_empty.empty_display_size = 0.5
        collection.objects.link(parent_empty)
        
        # Convert ROSE coordinates to Blender (X, -Y, Z) and scale by 1/100
        pos = ifo_object.position
        bx, by, bz = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
        
        # Apply tile offset (passed via base_path which is actually a tuple: (tile_world_x, tile_world_y))
        # If base_path is not a tuple, use the default world offsets for backward compatibility
        if isinstance(base_path, tuple) and len(base_path) >= 2:
            tile_offset_x, tile_offset_y = base_path[0], base_path[1]
            parent_empty.location = (bx + tile_offset_x, by + tile_offset_y, bz)
        else:
            # Fallback to default world offsets
            parent_empty.location = (bx + self.world_offset_x, by + self.world_offset_y, bz)

        # Convert rotation from IFO (XYZW order) to Blender (WXYZ order)
        # Both Z-up, only negate Y component: (w, x, y, z) -> (w, x, -y, z)
        from mathutils import Quaternion
        rot = ifo_object.rotation
        parent_empty.rotation_quaternion = Quaternion((rot.w, rot.x, -rot.y, rot.z))
        
        # Scale: no axis swap needed since both use Z-up
        parent_empty.scale = (ifo_object.scale.x, ifo_object.scale.y, ifo_object.scale.z)
        
        # Spawn all component parts (meshes) of this object
        for part_idx, part in enumerate(zsc_obj.parts):
            part_obj = self.spawn_part(
                context, zsc, part, part_idx,
                material_cache, mesh_cache, base_path, obj_name
            )
            if part_obj:
                collection.objects.link(part_obj)
                part_obj.parent = parent_empty
        
        return parent_empty

    def spawn_part(self, context, zsc, part, part_idx, material_cache, mesh_cache, base_path, obj_name):
        """Spawn a single ZSC part (mesh instance) with local transform"""
        from .rose.zms import ZMS
        from mathutils import Quaternion
        
        mesh_id = part.mesh_id
        material_id = part.material_id
        
        # Retrieve or load the ZMS mesh data
        if mesh_id not in mesh_cache:
            mesh_path = zsc.meshes[mesh_id]
            mesh_cache[mesh_id] = self.load_zms_mesh(mesh_path, base_path)
        
        mesh_data = mesh_cache[mesh_id]
        if not mesh_data:
            return None
        
        # Create the Blender object
        part_name = f"{obj_name}_part{part_idx}"
        obj = bpy.data.objects.new(part_name, mesh_data)
        
        # Apply material from cache
        if material_id in material_cache:
            if len(obj.data.materials) > 0:
                obj.data.materials[0] = material_cache[material_id]
            else:
                obj.data.materials.append(material_cache[material_id])
        
        # Local transform (relative to parent) - no world offset needed
        obj.location = convert_rose_position_to_blender(part.position.x, part.position.y, part.position.z)
        
        # Convert rotation from ZSC part (WXYZ in file, stored as XYZW in Vec4) to Blender
        rot = part.rotation
        obj.rotation_mode = 'QUATERNION'
        obj.rotation_quaternion = Quaternion((rot.w, rot.x, -rot.y, rot.z))
        
        # Scale: no axis swap needed since both use Z-up
        obj.scale = (part.scale.x, part.scale.y, part.scale.z)
        
        return obj

    def load_zms_mesh(self, mesh_path, base_path):
        """Load ZMS mesh with optimized UV assignment"""
        from .rose.zms import ZMS
        
        full_path = self.resolve_mesh_path(mesh_path, base_path)
        if not full_path:
            return None
        
        try:
            zms = ZMS(str(full_path))
            mesh_name = Path(mesh_path).stem
            mesh = bpy.data.meshes.new(mesh_name)
            
            # Mesh vertices are in local object space - use as-is from file
            verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
            faces = [(int(i.x), int(i.y), int(i.z)) for i in zms.indices]
            
            mesh.from_pydata(verts, [], faces)
            
            if zms.uv1_enabled() and zms.vertices:
                uv_layer = mesh.uv_layers.new(name="UVMap")
                uv_data = uv_layer.data
                vertices = zms.vertices
                
                # Batch process UVs by polygon for better cache locality
                for poly in mesh.polygons:
                    for loop_idx in range(poly.loop_start, poly.loop_start + poly.loop_total):
                        vi = mesh.loops[loop_idx].vertex_index
                        v = vertices[vi].uv1
                        uv_data[loop_idx].uv = (v.x, 1.0 - v.y)
            
            mesh.update(calc_edges=True)
            return mesh
        except Exception as e:
            if self.verbose_logging:
                self.report({'WARNING'}, f"Failed to load ZMS mesh {mesh_path}: {str(e)}")
            return None

    # =========================================================================
    # MAIN IMPORT EXECUTION
    # =========================================================================

    def execute(self, context):
        import time
        start_time = time.time()
        
        wm = context.window_manager
        wm.progress_begin(0, 100)
        
        try:
            # Disable viewport updates for performance
            original_use_autopersist = None
            try:
                original_use_autopersist = bpy.context.preferences.view.use_auto_persist
                bpy.context.preferences.view.use_auto_persist = False
            except Exception:
                pass
            
            filepath = Path(self.filepath).resolve()
            
            # Find 3DDATA root
            root_3ddata = filepath
            while root_3ddata.name.upper() != "3DDATA" and root_3ddata.parent != root_3ddata:
                root_3ddata = root_3ddata.parent
            
            if root_3ddata.name.upper() != "3DDATA":
                self.report({'ERROR'}, "Could not find 3DDATA root directory")
                return {'CANCELLED'}
            
            zone_path = filepath.parent  # Directory containing the ZON file
            
            wm.progress_update(10)
            
            # =========================================================================
            # LOAD TERRAIN BLOCKS (.mesh.bin files)
            # =========================================================================
            if self.load_terrain:
                # Find all .mesh.bin files in the zone directory
                block_files = sorted(zone_path.glob("block_*.mesh.bin"))
                
                if block_files:
                    if self.verbose_logging:
                        self.report({'INFO'}, f"Found {len(block_files)} terrain block files")
                    
                    terrain_collection, created_objects = self.import_terrain_blocks(
                        context, zone_path, block_files
                    )
                else:
                    self.report({'WARNING'}, "No .mesh.bin files found in zone directory")
            
            wm.progress_update(50)
            
            # =========================================================================
            # LOAD ZON FILE AND OBJECTS (if needed)
            # =========================================================================
            if self.load_cnst_objects or self.load_deco_objects:
                try:
                    zon = Zon(str(filepath))
                    
                    # Extract zone code from folder name (e.g., "JPT01" -> "JPT", "JD01" -> "JD")
                    zone_folder = filepath.parent.name.upper()  # e.g., "JPT01"
                    zone_code = ""
                    for char in zone_folder:
                        if char.isalpha():
                            zone_code += char
                        else:
                            break
                    
                    map_name = filepath.parent.parent.name.upper()  # e.g., "JUNON"
                    planet_path = root_3ddata / "MAPS" / map_name
                    
                    # Load CNST ZSC - try specific zone code first, then auto-discover
                    zsc_cnst = None
                    cnst_candidates = [
                        planet_path / f"LIST_CNST_{zone_code}.ZSC",
                        planet_path / f"list_cnst_{zone_code.lower()}.zsc",
                        planet_path / f"LIST_CNST_{zone_code}.zsc",
                        planet_path / f"list_cnst_{zone_code.lower()}.ZSC",
                    ]
                    
                    # Auto-discover any CNST files if specific not found
                    if planet_path.exists():
                        discovered_cnst = list(planet_path.glob("LIST_CNST_*.[Zz][Ss][Cc]")) + \
                                        list(planet_path.glob("list_cnst_*.[Zz][Ss][Cc]"))
                        for cf in discovered_cnst:
                            if cf not in cnst_candidates:
                                cnst_candidates.append(cf)
                    
                    for candidate in cnst_candidates:
                        if candidate.exists():
                            try:
                                zsc_cnst = Zsc(str(candidate))
                                break
                            except Exception as e:
                                if self.verbose_logging:
                                    self.report({'WARNING'}, f"Failed to load CNST ZSC {candidate}: {str(e)}")
                    
                    # Load DECO ZSC files - try specific zone code first, then auto-discover all
                    zsc_deco_list = []
                    deco_candidates = [
                        planet_path / f"LIST_DECO_{zone_code}.ZSC",
                        planet_path / f"list_deco_{zone_code.lower()}.zsc",
                        planet_path / f"LIST_DECO_{zone_code}.zsc",
                        planet_path / f"list_deco_{zone_code.lower()}.ZSC",
                    ]
                    
                    # Auto-discover all DECO files in planet folder (some planets have multiple: EJ+EZ, LP+LZ)
                    if planet_path.exists():
                        discovered_deco = list(planet_path.glob("LIST_DECO_*.[Zz][Ss][Cc]")) + \
                                        list(planet_path.glob("list_deco_*.[Zz][Ss][Cc]"))
                        for df in discovered_deco:
                            if df not in deco_candidates:
                                deco_candidates.append(df)
                    
                    for candidate in deco_candidates:
                        if candidate.exists():
                            try:
                                deco_zsc = Zsc(str(candidate))
                                zsc_deco_list.append(deco_zsc)
                            except Exception as e:
                                if self.verbose_logging:
                                    self.report({'WARNING'}, f"Failed to load DECO ZSC {candidate}: {str(e)}")
                    
                    # Create collections for objects
                    cnst_collection = bpy.data.collections.get("CNST_Objects")
                    if not cnst_collection:
                        cnst_collection = bpy.data.collections.new("CNST_Objects")
                        context.scene.collection.children.link(cnst_collection)
                    
                    deco_collection = bpy.data.collections.get("DECO_Objects")
                    if not deco_collection:
                        deco_collection = bpy.data.collections.new("DECO_Objects")
                        context.scene.collection.children.link(deco_collection)
                    
                    # Pre-create material caches
                    material_cache_cnst = {}
                    material_cache_deco = {}
                    mesh_cache_cnst = {}
                    mesh_cache_deco = {}
                    
                    if zsc_cnst:
                        for mat_id in range(len(zsc_cnst.materials)):
                            material_cache_cnst[mat_id] = self.create_object_material(
                                zsc_cnst.materials[mat_id], root_3ddata
                            )
                    
                    for zsc_deco in zsc_deco_list:
                        for mat_id in range(len(zsc_deco.materials)):
                            cache_key = (id(zsc_deco), mat_id)
                            material_cache_deco[cache_key] = self.create_object_material(
                                zsc_deco.materials[mat_id], root_3ddata
                            )
                    
                    # Spawn objects from IFO files in the zone directory
                    ifo_files = sorted(zone_path.glob("*.IFO")) + sorted(zone_path.glob("*.ifo"))
                    
                    for ifo_file in ifo_files:
                        try:
                            ifo = Ifo(str(ifo_file))
                            
                            # Spawn CNST objects
                            if self.load_cnst_objects and zsc_cnst:
                                for obj_inst in ifo.cnst_objects:
                                    if obj_inst.object_id < len(zsc_cnst.objects):
                                        self.spawn_object(
                                            context, cnst_collection, zsc_cnst, obj_inst,
                                            material_cache_cnst, mesh_cache_cnst, root_3ddata
                                        )
                            
                            # Spawn DECO objects
                            if self.load_deco_objects and zsc_deco_list:
                                for obj_inst in ifo.deco_objects:
                                    target_zsc = None
                                    for deco_zsc in zsc_deco_list:
                                        if obj_inst.object_id < len(deco_zsc.objects):
                                            target_zsc = deco_zsc
                                            break
                                    
                                    if target_zsc:
                                        temp_cache = {}
                                        for key, mat in material_cache_deco.items():
                                            if key[0] == id(target_zsc):
                                                temp_cache[key[1]] = mat
                                        
                                        self.spawn_object(
                                            context, deco_collection, target_zsc, obj_inst,
                                            temp_cache, mesh_cache_deco, root_3ddata
                                        )
                        
                        except Exception as e:
                            if self.verbose_logging:
                                self.report({'WARNING'}, f"Failed to load IFO {ifo_file}: {str(e)}")
                
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to load ZON file: {str(e)}")
            
            wm.progress_update(100)
            
            # Restore viewport updates
            try:
                if original_use_autopersist is not None:
                    bpy.context.preferences.view.use_auto_persist = original_use_autopersist
            except Exception:
                pass
            
            elapsed = time.time() - start_time
            self.report({'INFO'}, f"Import completed in {elapsed:.2f} seconds")
            
            return {'FINISHED'}
            
        except Exception as e:
            import traceback
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            if self.verbose_logging:
                self.report({'ERROR'}, traceback.format_exc())
            return {'CANCELLED'}


def menu_func(self, context):
    """Add menu entry for the combined importer"""
    self.layout.operator(ImportCombinedZone.bl_idname, text="ROSE Zone (Converted Terrain + Assets)")


def register():
    bpy.utils.register_class(ImportCombinedZone)


def unregister():
    bpy.utils.unregister_class(ImportCombinedZone)


if __name__ == "__main__":
    register()