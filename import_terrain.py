from .rose.him import Him
from .rose.til import Til
from .rose.zon import Zon

from .rose.utils import Vector2, list_2d

import os
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty
from bpy_extras.io_utils import ImportHelper


class ImportTerrain(bpy.types.Operator, ImportHelper):
    """Import ROSE terrain only (no decorations or objects)"""
    bl_idname = "import_terrain.zon"
    bl_label = "Import ROSE Terrain (.zon)"
    bl_options = {"PRESET"}

    filename_ext = ".zon"
    filter_glob: StringProperty(default="*.zon", options={"HIDDEN"})

    load_texture: BoolProperty(
        name="Load textures",
        description="Automatically detect and load textures from ZON file",
        default=True,
    )
    
    limit_tiles: BoolProperty(
        name="Limit Tiles",
        description="Only load a single tile (for testing)",
        default=False,
    )
    
    tile_x: IntProperty(
        name="Tile X",
        description="X coordinate of tile to load (if Limit Tiles enabled)",
        default=30,
    )
    
    tile_y: IntProperty(
        name="Tile Y", 
        description="Y coordinate of tile to load (if Limit Tiles enabled)",
        default=30,
    )

    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]

    def __init__(self, *args, **kwargs):
        """Initialize with caches for path resolution"""
        super().__init__(*args, **kwargs)
        self._texture_path_cache = {}
        self._3ddata_root_cache = None

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
        # Check cache first
        cache_key = (zon_filepath, texture_path)
        if cache_key in self._texture_path_cache:
            return self._texture_path_cache[cache_key]
        
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

        # Try common texture directories with direct path construction
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

        self.report({'WARNING'}, f"Texture not found: {texture_relative}")
        self._texture_path_cache[cache_key] = None
        return None

    def create_terrain_materials(self, zon_path, texture_paths):
        """
        Create a material for each texture in the ZON file.
        
        Args:
            zon_path: Path to the ZON file
            texture_paths: List of texture paths from ZON Textures block
        
        Returns:
            List of materials, one per texture
        """
        materials = []
        
        for idx, texture_path in enumerate(texture_paths):
            resolved_path = self.resolve_texture_path(zon_path, texture_path)
            
            if resolved_path and Path(resolved_path).exists():
                try:
                    # Create material
                    mat = bpy.data.materials.new(name=f"ROSE_Terrain_{idx}")
                    mat.use_nodes = True
                    nodes = mat.node_tree.nodes
                    links = mat.node_tree.links
                    
                    nodes.clear()
                    
                    # Texture node
                    tex_node = nodes.new(type='ShaderNodeTexImage')
                    tex_node.location = (-400, 0)
                    
                    # Load image
                    image = bpy.data.images.get(Path(resolved_path).name)
                    if not image:
                        image = bpy.data.images.load(resolved_path)
                    tex_node.image = image
                    
                    # Shader
                    bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
                    bsdf.location = (0, 0)
                    
                    output = nodes.new(type='ShaderNodeOutputMaterial')
                    output.location = (400, 0)
                    
                    # Connect
                    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
                    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
                    
                    materials.append(mat)
                except Exception as e:
                    materials.append(None)
            else:
                materials.append(None)
        
        return materials

    def execute(self, context):
        import time
        start_time = time.time()
        
        # Progress reporting
        wm = context.window_manager
        wm.progress_begin(0, 100)

        try:
            filepath = Path(self.filepath).resolve()
            
            # Find 3DDATA root
            root_3ddata = filepath
            while root_3ddata.name.upper() != "3DDATA" and root_3ddata.parent != root_3ddata:
                root_3ddata = root_3ddata.parent
            
            if root_3ddata.name.upper() != "3DDATA":
                return {'CANCELLED'}

            him_ext = ".HIM"
            til_ext = ".TIL"

            # Handle case-sensitive platforms
            if self.filepath.endswith(".zon"):
                him_ext = ".him"
                til_ext = ".til"

            zon = Zon(self.filepath)
            zon_dir = os.path.dirname(self.filepath)

            # Calculate grid scale and world offset
            grid_scale = zon.grid_size / 100.0
            world_offset_x = 52.0
            world_offset_y = 52.0

            tiles = SimpleNamespace()
            tiles.min_pos = Vector2(999, 999)
            tiles.max_pos = Vector2(-1, -1)
            tiles.dimension = Vector2(0, 0)
            tiles.count = 0
            tiles.coords = []

            # Scan directory for HIM files
            for file in os.listdir(zon_dir):
                if file.endswith(him_ext):
                    try:
                        x, y = map(int, file.split(".")[0].split("_"))
                        
                        if self.limit_tiles and (x != self.tile_x or y != self.tile_y):
                            continue
                        
                        tiles.min_pos.x = min(x, tiles.min_pos.x)
                        tiles.min_pos.y = min(y, tiles.min_pos.y)
                        tiles.max_pos.x = max(x, tiles.max_pos.x)
                        tiles.max_pos.y = max(y, tiles.max_pos.y)
                        tiles.count += 1
                        tiles.coords.append((x, y))
                    except:
                        continue

            if tiles.count == 0:
                return {'CANCELLED'}

            tiles.dimension.x = tiles.max_pos.x - tiles.min_pos.x + 1
            tiles.dimension.y = tiles.max_pos.y - tiles.min_pos.y + 1

            tiles.indices = list_2d(tiles.dimension.y, tiles.dimension.x)
            tiles.hims = list_2d(tiles.dimension.y, tiles.dimension.x)
            tiles.tils = list_2d(tiles.dimension.y, tiles.dimension.x)
            tiles.offsets = list_2d(tiles.dimension.y, tiles.dimension.x)

            # Load Him/Til files
            for x, y in tiles.coords:
                tile_name = "{}_{}".format(x, y)
                him_file = os.path.join(zon_dir, tile_name + him_ext)
                til_file = os.path.join(zon_dir, tile_name + til_ext)

                norm_x = x - tiles.min_pos.x
                norm_y = y - tiles.min_pos.y

                try:
                    him = Him(him_file)
                    til = Til(til_file)
                    him.indices = list_2d(him.width, him.length)

                    tiles.indices[norm_y][norm_x] = list_2d(him.width, him.length)
                    tiles.hims[norm_y][norm_x] = him
                    tiles.tils[norm_y][norm_x] = til
                except Exception as e:
                    pass

            # Calculate tile offsets
            length, cur_length = 0, 0
            for y in range(tiles.dimension.y):
                width = 0
                for x in range(tiles.dimension.x):
                    him = tiles.hims[y][x]
                    if him:
                        offset = Vector2(width, length)
                        tiles.offsets[y][x] = offset
                        width += him.width
                        cur_length = him.length
                length += cur_length

            wm.progress_update(30)

            # Generate terrain mesh
            vertices = []
            edges = []
            faces = []

            for y in range(tiles.dimension.y):
                for x in range(tiles.dimension.x):
                    if not tiles.hims[y][x]:
                        continue
                        
                    indices = tiles.indices[y][x]
                    him = tiles.hims[y][x]
                    offset_x = tiles.offsets[y][x].x
                    offset_y = tiles.offsets[y][x].y

                    for vy in range(him.length):
                        for vx in range(him.width):
                            height = him.heights[vy][vx] / 100.0
                            
                            world_x = (vx + offset_x) * grid_scale + world_offset_x
                            world_y = (vy + offset_y) * grid_scale + world_offset_y
                            
                            vertices.append((world_x, world_y, -height))
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
            for y in range(tiles.dimension.y):
                for x in range(tiles.dimension.x):
                    if not tiles.hims[y][x] or not tiles.indices[y][x]:
                        continue
                        
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
                                
                                if tiles.hims[y + 1][x + 1] and tiles.hims[y + 1][x]:
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

            wm.progress_update(50)
            
            # Apply materials based on tile_index
            if self.load_texture and zon.textures:
                texture_materials = self.create_terrain_materials(self.filepath, zon.textures)
                
                if texture_materials:
                    mesh.materials.clear()
                    for mat in texture_materials:
                        if mat:
                            mesh.materials.append(mat)
                    
                    # Pre-compute material slot for each texture index
                    texture_to_slot = {
                        tex_idx: slot_idx
                        for tex_idx in range(len(zon.textures))
                        for slot_idx, mat in enumerate(mesh.materials)
                        if mat and mat.name == f"ROSE_Terrain_{tex_idx}"
                    }
                    
                    # Pre-compute zon tile texture indices
                    zon_tile_textures = [
                        zon.tiles[i].layer1 + zon.tiles[i].offset1 if i < len(zon.tiles) else 0
                        for i in range(len(zon.tiles))
                    ]
                    
                    # Build material index array for all faces
                    material_indices = [0] * len(faces)
                    face_idx = 0
                    
                    for ty in range(int(tiles.dimension.y)):
                        for tx in range(int(tiles.dimension.x)):
                            if not tiles.hims[ty][tx]:
                                continue
                            
                            him = tiles.hims[ty][tx]
                            til = tiles.tils[ty][tx]
                            is_x_edge = (tx == tiles.dimension.x - 1)
                            is_y_edge = (ty == tiles.dimension.y - 1)
                            
                            til_width = len(til.tiles[0]) if til and til.tiles else 0
                            til_height = len(til.tiles) if til and til.tiles else 0
                            
                            # Main tile faces
                            for vy in range(him.length - 1):
                                for vx in range(him.width - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        til_x = min(vx, til_width - 1)
                                        til_y = min(vy, til_height - 1)
                                        til_patch = til.tiles[til_y][til_x]
                                        if til_patch.tile < len(zon_tile_textures):
                                            tex_idx = zon_tile_textures[til_patch.tile]
                                            if tex_idx in texture_to_slot:
                                                material_indices[face_idx] = texture_to_slot[tex_idx]
                                    face_idx += 1
                            
                            # Inter-tile X edge faces
                            if not is_x_edge:
                                for vy in range(him.length - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        til_x = min(him.width - 1, til_width - 1)
                                        til_y = min(vy, til_height - 1)
                                        til_patch = til.tiles[til_y][til_x]
                                        if til_patch.tile < len(zon_tile_textures):
                                            tex_idx = zon_tile_textures[til_patch.tile]
                                            if tex_idx in texture_to_slot:
                                                material_indices[face_idx] = texture_to_slot[tex_idx]
                                    face_idx += 1
                            
                            # Inter-tile Y edge faces
                            if not is_y_edge:
                                for vx in range(him.width - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        til_x = min(vx, til_width - 1)
                                        til_y = min(him.length - 1, til_height - 1)
                                        til_patch = til.tiles[til_y][til_x]
                                        if til_patch.tile < len(zon_tile_textures):
                                            tex_idx = zon_tile_textures[til_patch.tile]
                                            if tex_idx in texture_to_slot:
                                                material_indices[face_idx] = texture_to_slot[tex_idx]
                                    face_idx += 1
                            
                            # Corner faces
                            if not is_x_edge and not is_y_edge:
                                if tiles.hims[ty + 1][tx + 1] and tiles.hims[ty + 1][tx]:
                                    if face_idx < len(faces) and til and til.tiles:
                                        til_x = min(him.width - 1, til_width - 1)
                                        til_y = min(him.length - 1, til_height - 1)
                                        til_patch = til.tiles[til_y][til_x]
                                        if til_patch.tile < len(zon_tile_textures):
                                            tex_idx = zon_tile_textures[til_patch.tile]
                                            if tex_idx in texture_to_slot:
                                                material_indices[face_idx] = texture_to_slot[tex_idx]
                                    face_idx += 1
                    
                    # Batch assign material indices to polygons
                    for i, mat_idx in enumerate(material_indices):
                        mesh.polygons[i].material_index = mat_idx

            mesh.update(calc_edges=True)

            wm.progress_update(80)
            
            # Create terrain object at origin
            terrain_obj = bpy.data.objects.new("ROSE_Terrain", mesh)
            context.collection.objects.link(terrain_obj)

        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {str(e)}")
            return {'CANCELLED'}
        
        finally:
            wm.progress_end()
        
        elapsed = time.time() - start_time
        self.report({'INFO'}, f"Terrain import completed in {elapsed:.2f} seconds")
        return {"FINISHED"}


def menu_func_import_terrain(self, context):
    self.layout.operator(ImportTerrain.bl_idname, text="ROSE Terrain (.zon)")


def register():
    bpy.utils.register_class(ImportTerrain)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_terrain)


def unregister():
    bpy.utils.unregister_class(ImportTerrain)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_terrain)


if __name__ == "__main__":
    register()
