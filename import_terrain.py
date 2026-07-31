from .rose.him import Him
from .rose.til import Til
from .rose.zon import Zon

from .rose.utils import (
    Vector2, list_2d, apply_uv_rotation, patch_rotation, texture_pair,
)

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

    def create_terrain_materials(self, zon_path, texture_paths, texture_pairs):
        """
        Create one material per distinct (layer1, layer2) texture pair.

        Args:
            zon_path: Path to the ZON file
            texture_paths: List of texture paths from ZON Textures block
            texture_pairs: Iterable of (layer1_idx, layer2_idx) pairs

        Returns:
            (materials, pair_to_slot): list of materials and a dict mapping
            (layer1, layer2) pairs to material slots.
        """
        materials = []
        pair_to_slot = {}
        for pair in sorted(texture_pairs):
            mat = self.create_terrain_material(zon_path, texture_paths, pair[0], pair[1])
            if mat is not None:
                pair_to_slot[pair] = len(materials)
                materials.append(mat)
        return materials, pair_to_slot

    def create_terrain_material(self, zon_path, texture_paths, l1, l2):
        """Create a two-layer terrain material.

        Replicates the game shader (terrain_material.wgsl):
            final = mix(layer1, layer2, layer2.alpha)
        Layer1 samples the plain patch-local UV map; layer2 samples the
        rotation-adjusted UV map (UVMap_rot).
        """
        mat = bpy.data.materials.new(name=f"ROSE_Terrain_{l1}_{l2}")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        def load_image(idx):
            if idx >= len(texture_paths):
                return None
            resolved = self.resolve_texture_path(zon_path, texture_paths[idx])
            if not resolved or not Path(resolved).exists():
                return None
            try:
                image = bpy.data.images.get(Path(resolved).name)
                if not image:
                    image = bpy.data.images.load(resolved)
                return image
            except Exception:
                return None

        image1 = load_image(l1)
        image2 = load_image(l2) if l2 != l1 else None
        if image1 is None and image2 is None:
            bpy.data.materials.remove(mat)
            return None

        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (600, 0)
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (300, 0)

        if image2 is not None:
            tex2 = nodes.new(type='ShaderNodeTexImage')
            tex2.location = (-600, 0)
            tex2.image = image2
            uv_rot_attr = nodes.new(type='ShaderNodeAttribute')
            uv_rot_attr.attribute_name = 'UVMap_rot'
            uv_rot_attr.location = (-900, -200)
            links.new(uv_rot_attr.outputs['Vector'], tex2.inputs['Vector'])

            if image1 is not None:
                tex1 = nodes.new(type='ShaderNodeTexImage')
                tex1.location = (-900, 0)
                tex1.image = image1
                mix = nodes.new(type='ShaderNodeMix')
                mix.data_type = 'RGBA'
                mix.location = (-300, 0)
                links.new(tex2.outputs['Alpha'], mix.inputs['Factor'])
                links.new(tex1.outputs['Color'], mix.inputs['A'])
                links.new(tex2.outputs['Color'], mix.inputs['B'])
                links.new(mix.outputs['Result'], bsdf.inputs['Base Color'])
            else:
                links.new(tex2.outputs['Color'], bsdf.inputs['Base Color'])
        else:
            tex1 = nodes.new(type='ShaderNodeTexImage')
            tex1.location = (-300, 0)
            tex1.image = image1
            links.new(tex1.outputs['Color'], bsdf.inputs['Base Color'])

        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        return mat

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

            # Convert grid_size (cm) to meters. Terrain uses the same absolute
            # world space as import_map.py (block corner = 160*block - 5200 m).
            grid_scale = zon.grid_size / 100.0

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

            wm.progress_update(30)

            # Generate terrain mesh
            # Absolute world space matching the Rust client (spawning/terrain.rs):
            #   block corner = 160.0 * block_coord - 5200.0 meters (block 0..63),
            #   heightmap samples every grid_scale meters.
            block_size = 64.0 * grid_scale
            world_origin = -32.5 * block_size
            vertices = []
            edges = []
            faces = []
            # Per-face patch-local UVs: ((u,v) x4 corners, layer2 rotation)
            face_uvs = []

            for y in range(tiles.dimension.y):
                for x in range(tiles.dimension.x):
                    if not tiles.hims[y][x]:
                        continue
                        
                    indices = tiles.indices[y][x]
                    him = tiles.hims[y][x]
                    til = tiles.tils[y][x]
                    base_x = (x + tiles.min_pos.x) * block_size + world_origin
                    base_y = (y + tiles.min_pos.y) * block_size + world_origin

                    for vy in range(him.length):
                        for vx in range(him.width):
                            height = him.heights[vy][vx] / 100.0
                            
                            world_x = base_x + vx * grid_scale
                            world_y = base_y + vy * grid_scale
                            
                            vertices.append((world_x, world_y, height))
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
                                # Patch-local UVs (matching the client's uv1 = [x/4, y/4])
                                px, py = vx // 4, vy // 4
                                u0 = (vx - px * 4) / 4.0
                                v0 = (vy - py * 4) / 4.0
                                face_uvs.append((
                                    ((u0, v0),
                                     (u0 + 0.25, v0),
                                     (u0 + 0.25, v0 + 0.25),
                                     (u0, v0 + 0.25)),
                                    patch_rotation(til, zon, px, py),
                                ))

            # Generate inter-tile connections
            for y in range(tiles.dimension.y):
                for x in range(tiles.dimension.x):
                    if not tiles.hims[y][x] or not tiles.indices[y][x]:
                        continue
                        
                    indices = tiles.indices[y][x]
                    him = tiles.hims[y][x]
                    is_x_edge = (x == tiles.dimension.x - 1)
                    is_y_edge = (y == tiles.dimension.y - 1)

                    # Skip connections to neighboring tiles that don't exist on disk
                    # (zones are sparse grids; missing tiles are skipped like the Rust client)
                    has_x_neighbor = not is_x_edge and bool(tiles.indices[y][x + 1])
                    has_y_neighbor = not is_y_edge and bool(tiles.indices[y + 1][x])
                    has_xy_neighbor = has_x_neighbor and has_y_neighbor and bool(tiles.indices[y + 1][x + 1])

                    for vy in range(him.length):
                        for vx in range(him.width):
                            is_x_edge_vertex = (vx == him.width - 1) and (vy < him.length - 1)
                            is_y_edge_vertex = (vx < him.width - 1) and (vy == him.length - 1)
                            is_corner_vertex = (vx == him.width - 1) and (vy == him.length - 1)

                            if has_x_neighbor and is_x_edge_vertex:
                                next_indices = tiles.indices[y][x + 1]
                                v1 = indices[vy][vx]
                                v2 = next_indices[vy][0]
                                v3 = next_indices[vy + 1][0]
                                v4 = indices[vy + 1][vx]
                                edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                                faces.append((v1, v2, v3, v4))
                                # Edge stitch quad is degenerate in world space;
                                # give it the edge patch's UVs
                                px, py = min(vx // 4, 15), vy // 4
                                va = (vy - py * 4) / 4.0
                                vb = (vy + 1 - py * 4) / 4.0
                                face_uvs.append((
                                    ((1.0, va), (0.0, va), (0.0, vb), (1.0, vb)),
                                    patch_rotation(til, zon, px, py),
                                ))

                            if has_y_neighbor and is_y_edge_vertex:
                                next_indices = tiles.indices[y + 1][x]
                                v1 = indices[vy][vx]
                                v2 = indices[vy][vx + 1]
                                v3 = next_indices[0][vx + 1]
                                v4 = next_indices[0][vx]
                                edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                                faces.append((v1, v2, v3, v4))
                                px, py = vx // 4, min(vy // 4, 15)
                                ua = (vx - px * 4) / 4.0
                                ub = (vx + 1 - px * 4) / 4.0
                                face_uvs.append((
                                    ((ua, 1.0), (ub, 1.0), (ub, 0.0), (ua, 0.0)),
                                    patch_rotation(til, zon, px, py),
                                ))

                            if has_xy_neighbor and is_corner_vertex:
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
                                face_uvs.append((
                                    ((1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0)),
                                    patch_rotation(til, zon, 15, 15),
                                ))

            # Create terrain mesh
            mesh = bpy.data.meshes.new("ROSE_Terrain")
            mesh.from_pydata(vertices, edges, faces)
            mesh.update()

            # UV maps matching the game: uv1 = patch-local 0..1 per TIL patch,
            # UVMap_rot = same coords with the patch's layer2 rotation applied.
            uv_layer = mesh.uv_layers.new(name="UVMap")
            uv2_layer = mesh.uv_layers.new(name="UVMap_rot")
            for fi, (corners, rotation) in enumerate(face_uvs):
                poly = mesh.polygons[fi]
                for li in range(poly.loop_start, poly.loop_start + poly.loop_total):
                    corner = li - poly.loop_start
                    u, v = corners[corner]
                    uv_layer.data[li].uv = (u, v)
                    uv2_layer.data[li].uv = apply_uv_rotation(u, v, rotation)

            wm.progress_update(50)
            
            # Apply materials based on tile_index
            if self.load_texture and zon.textures:

                # Collect the distinct (layer1, layer2) texture pairs used by
                # the map's TIL patches (matching the Rust client's two-layer
                # blending; see terrain.rs and terrain_material.wgsl).
                texture_pairs = set()
                for ty in range(int(tiles.dimension.y)):
                    for tx in range(int(tiles.dimension.x)):
                        til = tiles.tils[ty][tx]
                        if not til or not til.tiles:
                            continue
                        for row in til.tiles:
                            for patch in row:
                                if patch.tile < len(zon.tiles):
                                    ztile = zon.tiles[patch.tile]
                                    l1 = ztile.layer1 + ztile.offset1
                                    l2 = ztile.layer2 + ztile.offset2
                                    if l1 >= len(zon.textures):
                                        l1 = l2
                                    if l2 >= len(zon.textures):
                                        l2 = l1
                                    texture_pairs.add((l1, l2))

                # Create materials, one per texture pair
                texture_materials, pair_to_slot = self.create_terrain_materials(
                    self.filepath, zon.textures, texture_pairs)

                if texture_materials:
                    mesh.materials.clear()
                    for mat in texture_materials:
                        mesh.materials.append(mat)
                    
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

                            # Must mirror the stitching loop: only count faces
                            # for neighbors that actually exist on disk
                            has_x_neighbor = not is_x_edge and bool(tiles.indices[ty][tx + 1])
                            has_y_neighbor = not is_y_edge and bool(tiles.indices[ty + 1][tx])
                            has_xy_neighbor = has_x_neighbor and has_y_neighbor and bool(tiles.indices[ty + 1][tx + 1])

                            def slot_for(px, py):
                                pair = texture_pair(til, zon, px, py, len(zon.textures))
                                if pair is None:
                                    return None
                                return pair_to_slot.get(pair)

                            # Main tile faces
                            # TIL is a 16x16 patch grid; each patch covers a 4x4
                            # quad area of the 64x64 heightmap grid.
                            for vy in range(him.length - 1):
                                for vx in range(him.width - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        slot = slot_for(vx // 4, vy // 4)
                                        if slot is not None:
                                            material_indices[face_idx] = slot
                                    face_idx += 1
                            
                            # Inter-tile X edge faces
                            if has_x_neighbor:
                                for vy in range(him.length - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        slot = slot_for((him.width - 1) // 4, vy // 4)
                                        if slot is not None:
                                            material_indices[face_idx] = slot
                                    face_idx += 1
                            
                            # Inter-tile Y edge faces
                            if has_y_neighbor:
                                for vx in range(him.width - 1):
                                    if face_idx < len(faces) and til and til.tiles:
                                        slot = slot_for(vx // 4, (him.length - 1) // 4)
                                        if slot is not None:
                                            material_indices[face_idx] = slot
                                    face_idx += 1
                            
                            # Corner faces
                            if has_xy_neighbor:
                                if face_idx < len(faces) and til and til.tiles:
                                    slot = slot_for((him.width - 1) // 4, (him.length - 1) // 4)
                                    if slot is not None:
                                        material_indices[face_idx] = slot
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
