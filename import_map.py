from .rose.him import Him
from .rose.til import Til
from .rose.zon import Zon
from .rose.zsc import Zsc
from .rose.ifo import Ifo
from .rose.zms import ZMS

from .rose.utils import Vector2, Vector3, list_2d

import os
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper


class ImportMap(bpy.types.Operator, ImportHelper):
    bl_idname = "import_map.zon"
    bl_label = "Import ROSE map (.zon)"
    bl_options = {"PRESET"}

    filename_ext = ".zon"
    filter_glob: StringProperty(default="*.zon", options={"HIDDEN"})

    load_texture: BoolProperty(
        name="Load textures",
        description="Automatically detect and load textures from ZON file",
        default=True,
    )
    
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
    
    limit_tiles: BoolProperty(
        name="Limit Tiles",
        description="Only load a single tile (for testing)",
        default=False,
    )
    
    tile_x: bpy.props.IntProperty(
        name="Tile X",
        description="X coordinate of tile to load (if Limit Tiles enabled)",
        default=30,
    )
    
    tile_y: bpy.props.IntProperty(
        name="Tile Y", 
        description="Y coordinate of tile to load (if Limit Tiles enabled)",
        default=30,
    )

    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]

    def get_zone_code(self):
        """Extract zone code from the ZON file path.
        
        Examples:
        - MAPS/JUNON/JPT01/30_30.ZON -> JPT
        - MAPS/JUNON/JD01/30_30.ZON -> JD
        - MAPS/JUNON/JDT01/30_30.ZON -> JDT
        - MAPS/JUNON/JG03/30_30.ZON -> JG
        - MAPS/JUNON/JZ01_1/30_30.ZON -> JZ
        """
        try:
            filepath = Path(self.filepath).resolve()
            # Get the immediate parent folder (e.g., "JPT01", "JD01", "JG03")
            zone_folder = filepath.parent.name.upper()
            
            # Extract letters only from the start (remove numbers and underscores)
            # JPT01 -> JPT, JD01 -> JD, JZ01_1 -> JZ
            zone_code = ""
            for char in zone_folder:
                if char.isalpha():
                    zone_code += char
                else:
                    break
            
            return zone_code if zone_code else "UNKNOWN"
        except Exception:
            return "UNKNOWN"
    
    def create_terrain_material(self, zon_path, texture_paths):
        """Create a material with the first available texture."""
        mat = bpy.data.materials.new(name="ROSE_Terrain_Material")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        nodes.clear()

        # Create texture node
        tex_node = nodes.new(type='ShaderNodeTexImage')
        tex_node.location = (0, 0)

        # Resolve texture
        texture_path = None
        for path in texture_paths:
            resolved = self.resolve_texture_path(zon_path, path)
            if resolved:
                texture_path = resolved
                break

        if texture_path:
            tex_node.image = bpy.data.images.load(texture_path)
            self.report({'INFO'}, f"Loaded texture: {texture_path}")
        else:
            self.report({'WARNING'}, "No texture loaded for material.")
            tex_node.image = None

        # Create Principled BSDF
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (300, 0)

        # Create Material Output
        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (600, 0)

        # Link nodes
        links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

        return mat

    def resolve_texture_path(self, zon_filepath, texture_path):
        """Resolve texture path from ZON to actual file path."""
        zon_path = Path(zon_filepath).resolve()
        zon_dir = zon_path.parent

        # Normalize path separators
        texture_relative = texture_path.replace('\\', os.sep)

        # Try to find 3DDATA root
        current = zon_dir
        max_depth = 10
        depth = 0
        root_3ddata = None

        while depth < max_depth:
            if current.name.upper() == "3DDATA":
                root_3ddata = current
                break
            current = current.parent
            depth += 1

        if not root_3ddata:
            self.report({'WARNING'}, "Could not find 3DDATA root. Texture resolution failed.")
            return None

        # Try exact path
        full_path = root_3ddata / texture_relative
        if full_path.exists():
            return str(full_path)

        # Try parent of 3DDATA
        full_path = root_3ddata.parent / texture_relative
        if full_path.exists():
            return str(full_path)

        # Try case-insensitive search
        for path in [root_3ddata, root_3ddata.parent]:
            try:
                for file in path.rglob('*'):
                    if file.name.lower() == Path(texture_relative).name.lower():
                        self.report({'INFO'}, f"Found texture via case-insensitive match: {file}")
                        return str(file)
            except Exception:
                continue

        self.report({'WARNING'}, f"Texture not found: {texture_relative}")
        return None

    def get_map_name(self):
        # Returns the folder name for the map; example: JUNON for files that contain "JPT"
        fp = str(self.filepath).upper()
        if "J" in fp:
            return "JUNON"
        # fallback: try to infer from parent directory
        try:
            return Path(self.filepath).parent.name.upper()
        except Exception:
            return "UNKNOWN"
            
    def get_town_name(self):
        # Return town name, e.g. "JPT" for Junon maps.
        fp = str(self.filepath).upper()
        if "JPT" in fp:
            return "JPT"
        # fallback: try parent folder name
        try:
            return Path(self.filepath).parent.name.upper()
        except Exception:
            return "UNKNOWN"

    def get_3ddata_path(self):
        filepath = Path(self.filepath).resolve()
        return filepath.parent.parent.parent.parent

    def execute(self, context):
        # Get paths based on file structure
        # Expected: 3DDATA/MAPS/JUNON/JPT01/30_30.ZON
        filepath = Path(self.filepath).resolve()
        
        # Find 3DDATA root (go up until we find it)
        root_3ddata = filepath
        while root_3ddata.name.upper() != "3DDATA" and root_3ddata.parent != root_3ddata:
            root_3ddata = root_3ddata.parent
        
        if root_3ddata.name.upper() != "3DDATA":
            self.report({'ERROR'}, "Could not find 3DDATA root directory")
            return {'CANCELLED'}
        
        map_name = self.get_map_name()  # e.g., "JUNON"
        zone_code = self.get_zone_code()  # e.g., "JPT", "JD", "JG"
        
        self.report({'INFO'}, f"3DDATA root: {root_3ddata}")
        self.report({'INFO'}, f"Map name: {map_name}")
        self.report({'INFO'}, f"Zone code: {zone_code}")
        
        # Load ZSC files: 3DDATA/JUNON/LIST_CNST_JPT.ZSC
        zsc_cnst_path = root_3ddata / map_name / f"LIST_CNST_{zone_code}.ZSC"
        zsc_deco_path = root_3ddata / map_name / f"LIST_DECO_{zone_code}.ZSC"
        
        zsc_cnst = None
        zsc_deco = None
        
        if zsc_cnst_path.exists():
            try:
                zsc_cnst = Zsc(str(zsc_cnst_path))
                self.report({'INFO'}, f"Loaded CNST ZSC: {len(zsc_cnst.meshes)} meshes, {len(zsc_cnst.objects)} objects")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load CNST ZSC: {e}")
        else:
            self.report({'WARNING'}, f"CNST ZSC not found: {zsc_cnst_path}")
        
        if zsc_deco_path.exists():
            try:
                zsc_deco = Zsc(str(zsc_deco_path))
                self.report({'INFO'}, f"Loaded DECO ZSC: {len(zsc_deco.meshes)} meshes, {len(zsc_deco.objects)} objects")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load DECO ZSC: {e}")
        else:
            self.report({'WARNING'}, f"DECO ZSC not found: {zsc_deco_path}")
        
        self.report({'INFO'}, "Starting map import process")
        him_ext = ".HIM"
        til_ext = ".TIL"
        ifo_ext = ".IFO"

        # In case user is on case-sensitive platform and using lowercase ext
        if self.filepath.endswith(".zon"):
            him_ext = ".him"
            til_ext = ".til"
            ifo_ext = ".ifo"

        zon = Zon(self.filepath)
        zon_dir = os.path.dirname(self.filepath)

        self.report({'INFO'}, f"Loaded ZON file: {self.filepath}")
        self.report({'INFO'}, f"Zone dimensions: {zon.width} x {zon.length}")
        self.report({'INFO'}, f"Found {len(zon.textures)} textures in ZON file")

        tiles = SimpleNamespace()
        tiles.min_pos = Vector2(999, 999)
        tiles.max_pos = Vector2(-1, -1)
        tiles.dimension = Vector2(0, 0)
        tiles.count = 0
        tiles.coords = []

        self.report({'INFO'}, "Scanning directory for HIM files...")
        for file in os.listdir(zon_dir):
            if file.endswith(him_ext):
                x, y = map(int, file.split(".")[0].split("_"))
                
                # If limit_tiles is enabled, only load the specified tile
                if self.limit_tiles and (x != self.tile_x or y != self.tile_y):
                    continue
                
                tiles.min_pos.x = min(x, tiles.min_pos.x)
                tiles.min_pos.y = min(y, tiles.min_pos.y)
                tiles.max_pos.x = max(x, tiles.max_pos.x)
                tiles.max_pos.y = max(y, tiles.max_pos.y)
                tiles.count += 1
                tiles.coords.append((x, y))

        tiles.dimension.x = tiles.max_pos.x - tiles.min_pos.x + 1
        tiles.dimension.y = tiles.max_pos.y - tiles.min_pos.y + 1

        self.report({'INFO'}, f"Detected tile grid: {tiles.dimension.x} x {tiles.dimension.y}")

        tiles.indices = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.hims = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.tils = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.ifos = list_2d(tiles.dimension.y, tiles.dimension.x)

        self.report({'INFO'}, "Loading HIM/TIL/IFO files...")
        for x, y in tiles.coords:
            tile_name = "{}_{}".format(x, y)
            him_file = os.path.join(zon_dir, tile_name + him_ext)
            til_file = os.path.join(zon_dir, tile_name + til_ext)
            ifo_file = os.path.join(zon_dir, tile_name + ifo_ext)

            norm_x = x - tiles.min_pos.x
            norm_y = y - tiles.min_pos.y

            try:
                him = Him(him_file)
                til = Til(til_file)
                him.indices = list_2d(him.width, him.length)
                
                # Load IFO if exists
                ifo = None
                if os.path.exists(ifo_file):
                    try:
                        ifo = Ifo(ifo_file)
                        self.report({'INFO'}, f"Loaded IFO {tile_name}: {len(ifo.cnst_objects)} CNST, {len(ifo.deco_objects)} DECO")
                    except Exception as e:
                        self.report({'WARNING'}, f"Failed to load IFO {tile_name}: {e}")

                tiles.indices[norm_y][norm_x] = list_2d(him.width, him.length)
                tiles.hims[norm_y][norm_x] = him
                tiles.tils[norm_y][norm_x] = til
                tiles.ifos[norm_y][norm_x] = ifo

                self.report({'INFO'}, f"Loaded tile: {tile_name}")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load tile {tile_name}: {e}")

        tiles.offsets = list_2d(tiles.dimension.y, tiles.dimension.x)

        # Calculate tile offsets
        length, cur_length = 0, 0
        for y in range(tiles.dimension.y):
            width = 0
            for x in range(tiles.dimension.x):
                him = tiles.hims[y][x]
                offset = Vector2(width, length)
                tiles.offsets[y][x] = offset
                width += him.width
                cur_length = him.length
            length += cur_length

        # Generate terrain mesh
        vertices = []
        edges = []
        faces = []

        self.report({'INFO'}, "Generating terrain mesh...")
        for y in range(tiles.dimension.y):
            for x in range(tiles.dimension.x):
                indices = tiles.indices[y][x]
                him = tiles.hims[y][x]
                offset_x = tiles.offsets[y][x].x
                offset_y = tiles.offsets[y][x].y

                for vy in range(him.length):
                    for vx in range(him.width):
                        vz = him.heights[vy][vx] / him.patch_scale
                        vertices.append((vx + offset_x, vy + offset_y, vz))
                        vi = len(vertices) - 1
                        him.indices[vy][vx] = vi
                        indices[vy][vx] = vi

                        if vx < him.width - 1 and vy < him.length - 1:
                            v1 = vi
                            v2 = vi + 1
                            v3 = vi + 1 + him.width
                            v4 = vi + him.width
                            edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                            faces.append((v1, v2, v3, v4))

        # Generate inter-tile connections
        self.report({'INFO'}, "Generating inter-tile connections...")
        for y in range(tiles.dimension.y):
            for x in range(tiles.dimension.x):
                indices = tiles.indices[y][x]
                him = tiles.hims[y][x]
                is_x_edge = (x == tiles.dimension.x - 1)
                is_y_edge = (y == tiles.dimension.y - 1)

                for vy in range(him.length):
                    for vx in range(him.width):
                        is_x_edge_vertex = (vx == him.width - 1) and (vy < him.length - 1)
                        is_y_edge_vertex = (vx < him.width - 1) and (vy == him.length - 1)
                        is_corner_vertex = (vx == him.width - 1) and (vy == him.length - 1)

                        if not is_x_edge and is_x_edge_vertex:
                            next_indices = tiles.indices[y][x + 1]
                            v1 = indices[vy][vx]
                            v2 = next_indices[vy][0]
                            v3 = next_indices[vy + 1][0]
                            v4 = indices[vy + 1][vx]
                            edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                            faces.append((v1, v2, v3, v4))

                        if not is_y_edge and is_y_edge_vertex:
                            next_indices = tiles.indices[y + 1][x]
                            v1 = indices[vy][vx]
                            v2 = indices[vy][vx + 1]
                            v3 = next_indices[0][vx + 1]
                            v4 = next_indices[0][vx]
                            edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                            faces.append((v1, v2, v3, v4))

                        if not is_x_edge and not is_y_edge and is_corner_vertex:
                            right = tiles.indices[y][x + 1]
                            diag = tiles.indices[y + 1][x + 1]
                            down = tiles.indices[y + 1][x]
                            diag_him = tiles.hims[y + 1][x + 1]
                            down_him = tiles.hims[y + 1][x]

                            v1 = indices[vy][vx]
                            v2 = right[diag_him.length - 1][0]
                            v3 = diag[0][0]
                            v4 = down[0][down_him.width - 1]
                            edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                            faces.append((v1, v2, v3, v4))

        # Create terrain mesh
        mesh = bpy.data.meshes.new("ROSE_Terrain")
        mesh.from_pydata(vertices, edges, faces)
        mesh.update()

        # Add UVs
        mesh.uv_layers.new(name="UVMap")
        for loop_idx, loop in enumerate(mesh.loops):
            vi = loop.vertex_index
            u = (vertices[vi][0] % 1.0)
            v = (vertices[vi][1] % 1.0)
            mesh.uv_layers["UVMap"].data[loop_idx].uv = (u, 1 - v)

        # Apply material
        if self.load_texture and zon.textures:
            mat = self.create_terrain_material(self.filepath, [zon.textures[0]])
            mesh.materials.append(mat)

        mesh.update(calc_edges=True)

        # Create terrain object
        terrain_obj = bpy.data.objects.new("ROSE_Terrain", mesh)
        context.collection.objects.link(terrain_obj)
        
        # Create collections for objects
        cnst_collection = bpy.data.collections.new("CNST_Objects")
        deco_collection = bpy.data.collections.new("DECO_Objects")
        context.scene.collection.children.link(cnst_collection)
        context.scene.collection.children.link(deco_collection)
        
        # Spawn objects from IFO files (only for loaded tiles)
        material_cache_cnst = {}
        material_cache_deco = {}
        mesh_cache_cnst = {}
        mesh_cache_deco = {}
        
        # First pass: collect which object IDs are actually used
        used_cnst_objects = set()
        used_deco_objects = set()
        
        for y in range(tiles.dimension.y):
            for x in range(tiles.dimension.x):
                if tiles.hims[y][x] is None:
                    continue
                    
                ifo = tiles.ifos[y][x]
                if not ifo:
                    continue
                
                if self.load_cnst_objects:
                    for obj_inst in ifo.cnst_objects:
                        used_cnst_objects.add(obj_inst.object_id)
                
                if self.load_deco_objects:
                    for obj_inst in ifo.deco_objects:
                        used_deco_objects.add(obj_inst.object_id)
        
        self.report({'INFO'}, f"Found {len(used_cnst_objects)} unique CNST objects, {len(used_deco_objects)} unique DECO objects referenced in IFO files")
        
        # Pre-create materials only for used objects
        if zsc_cnst:
            # Collect which materials are used by referenced objects
            used_materials = set()
            for obj_id in used_cnst_objects:
                if obj_id < len(zsc_cnst.objects):
                    for part in zsc_cnst.objects[obj_id].parts:
                        used_materials.add(part.material_id)
            
            self.report({'INFO'}, f"Loading {len(used_materials)} CNST materials")
            for mat_id in used_materials:
                if mat_id < len(zsc_cnst.materials):
                    material_cache_cnst[mat_id] = self.create_zsc_material(zsc_cnst.materials[mat_id], root_3ddata)
        
        if zsc_deco:
            # Collect which materials are used by referenced objects
            used_materials = set()
            for obj_id in used_deco_objects:
                if obj_id < len(zsc_deco.objects):
                    for part in zsc_deco.objects[obj_id].parts:
                        used_materials.add(part.material_id)
            
            self.report({'INFO'}, f"Loading {len(used_materials)} DECO materials")
            for mat_id in used_materials:
                if mat_id < len(zsc_deco.materials):
                    material_cache_deco[mat_id] = self.create_zsc_material(zsc_deco.materials[mat_id], root_3ddata)
        
        # Spawn objects (only from tiles that were actually loaded)
        total_cnst = 0
        total_deco = 0
        
        for y in range(tiles.dimension.y):
            for x in range(tiles.dimension.x):
                # Skip if this tile wasn't loaded
                if tiles.hims[y][x] is None:
                    continue
                    
                ifo = tiles.ifos[y][x]
                if not ifo:
                    continue
                
                # Spawn CNST objects
                if self.load_cnst_objects and zsc_cnst:
                    spawned_this_tile = 0
                    for obj_inst in ifo.cnst_objects:
                        if obj_inst.object_id >= len(zsc_cnst.objects):
                            self.report({'WARNING'}, f"Invalid CNST object_id {obj_inst.object_id} in tile ({x},{y}), max is {len(zsc_cnst.objects)-1}")
                            continue
                        
                        self.spawn_object(
                            context, cnst_collection, zsc_cnst, obj_inst,
                            material_cache_cnst, mesh_cache_cnst, root_3ddata
                        )
                        total_cnst += 1
                        spawned_this_tile += 1
                    
                    if spawned_this_tile > 0:
                        self.report({'INFO'}, f"Tile ({x},{y}): Spawned {spawned_this_tile} CNST objects")
                
                # Spawn DECO objects
                if self.load_deco_objects and zsc_deco:
                    spawned_this_tile = 0
                    for obj_inst in ifo.deco_objects:
                        if obj_inst.object_id >= len(zsc_deco.objects):
                            self.report({'WARNING'}, f"Invalid DECO object_id {obj_inst.object_id} in tile ({x},{y}), max is {len(zsc_deco.objects)-1}")
                            continue
                        
                        self.spawn_object(
                            context, deco_collection, zsc_deco, obj_inst,
                            material_cache_deco, mesh_cache_deco, root_3ddata
                        )
                        total_deco += 1
                        spawned_this_tile += 1
                    
                    if spawned_this_tile > 0:
                        self.report({'INFO'}, f"Tile ({x},{y}): Spawned {spawned_this_tile} DECO objects")
        
        self.report({'INFO'}, f"Spawned {total_cnst} CNST objects, {total_deco} DECO objects")
        self.report({'INFO'}, "Import completed successfully!")
        return {"FINISHED"}
    
    def spawn_object(self, context, collection, zsc, ifo_object, material_cache, mesh_cache, base_path):
        """Spawn a ZSC object from IFO data"""
        zsc_obj = zsc.objects[ifo_object.object_id]
        
        # Create parent empty
        obj_name = ifo_object.object_name if ifo_object.object_name else f"Object_{ifo_object.object_id}"
        parent_empty = bpy.data.objects.new(obj_name, None)
        parent_empty.empty_display_type = 'PLAIN_AXES'
        parent_empty.empty_display_size = 0.5
        collection.objects.link(parent_empty)
        
        # Transform from ROSE to Blender coordinate systems
        # The ZMS meshes are already in correct orientation, we just need to place them correctly
        # Based on Rust: Vec3::new(x, z, -y) / 100.0 + Vec3::new(5200.0, 0.0, -5200.0)
        # This means: x stays x, z becomes height, -y becomes depth
        # For Blender Z-up terrain: x is x, y is y, z is height
        # So: ROSE(x,y,z) -> Blender(x, -y, z) with offsets
        pos = ifo_object.position
        parent_empty.location = (
            (pos.x / 100.0) + 52.0,        # X + zone_offset (5200cm = 52m)
            (-pos.y / 100.0) - 52.0,       # -Y + zone_offset (becomes Blender Y)
            pos.z / 100.0                   # Z (height, no offset for height)
        )
        
        # Quaternion - keep as-is since meshes are already correct
        rot = ifo_object.rotation
        parent_empty.rotation_mode = 'QUATERNION'
        parent_empty.rotation_quaternion = (rot.w, rot.x, rot.y, rot.z)
        
        # Scale - keep as-is
        parent_empty.scale = (ifo_object.scale.x, ifo_object.scale.y, ifo_object.scale.z)
        
        # Spawn parts
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
        """Spawn a single object part"""
        mesh_id = part.mesh_id
        material_id = part.material_id
        
        # Get or load mesh
        if mesh_id not in mesh_cache:
            mesh_path = zsc.meshes[mesh_id]
            mesh_cache[mesh_id] = self.load_zms_mesh(mesh_path, base_path)
        
        mesh_data = mesh_cache[mesh_id]
        if not mesh_data:
            return None
        
        # Create object
        part_name = f"{obj_name}_part{part_idx}"
        obj = bpy.data.objects.new(part_name, mesh_data)
        
        # Apply material
        if material_id in material_cache:
            if len(obj.data.materials) > 0:
                obj.data.materials[0] = material_cache[material_id]
            else:
                obj.data.materials.append(material_cache[material_id])
        
        # Transform (relative to parent) - keep as-is like import_zms.py
        obj.location = (part.position.x / 100.0, part.position.y / 100.0, part.position.z / 100.0)
        obj.rotation_mode = 'QUATERNION'
        obj.rotation_quaternion = (part.rotation.w, part.rotation.x, part.rotation.y, part.rotation.z)
        obj.scale = (part.scale.x, part.scale.y, part.scale.z)
        
        return obj
    
    def load_zms_mesh(self, mesh_path, base_path):
        """Load ZMS mesh"""
        full_path = self.resolve_mesh_path(mesh_path, base_path)
        if not full_path:
            return None
        
        try:
            zms = ZMS(str(full_path))
            mesh_name = Path(mesh_path).stem
            mesh = bpy.data.meshes.new(mesh_name)
            
            verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
            faces = [(int(i.x), int(i.y), int(i.z)) for i in zms.indices]
            
            mesh.from_pydata(verts, [], faces)
            
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
            self.report({'WARNING'}, f"Failed to load mesh {mesh_path}: {e}")
            return None
    
    def resolve_mesh_path(self, mesh_path, base_path):
        """Resolve mesh path"""
        candidate = base_path / mesh_path
        if candidate.exists():
            return candidate
        
        # Try case-insensitive
        try:
            for file in base_path.rglob('*'):
                if file.name.lower() == Path(mesh_path).name.lower():
                    return file
        except:
            pass
        
        return None
    
    def create_zsc_material(self, zsc_mat, base_path):
        """Create material from ZSC material data"""
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
                except:
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