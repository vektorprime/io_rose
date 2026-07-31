from .rose.him import Him
from .rose.til import Til
from .rose.zon import Zon
from .rose.zsc import Zsc
from .rose.ifo import Ifo
from .rose.zms import ZMS

from .rose.utils import (
    Vector2, Vector3, list_2d, convert_rose_position_to_blender,
    apply_uv_rotation, patch_rotation, texture_pair,
)

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

    setup_lighting: BoolProperty(
        name="Setup scene lighting",
        description="Add a sun light and world ambient so the terrain is "
                    "visible in Rendered viewport mode (the game uses a "
                    "strong directional light plus ambient)",
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
    
    verbose_logging: BoolProperty(
        name="Verbose Logging",
        description="Log warnings for skipped files and errors during import",
        default=False,
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
                # Terrain tiles are sRGB-encoded but must be sampled raw:
                # Blender's sRGB mipmap pipeline darkens minified samples to
                # black (double sRGB->linear conversion on mip levels).
                # Load as Non-Color and linearize manually with Gamma nodes.
                image.colorspace_settings.name = "Non-Color"
                return image
            except Exception:
                return None

        def connect_color(tex_node, target_socket):
            """tex Color -> Gamma(2.2) -> target (manual sRGB linearization)."""
            gamma = nodes.new(type='ShaderNodeGamma')
            gamma.location = (tex_node.location.x + 250, tex_node.location.y)
            gamma.inputs['Gamma'].default_value = 2.2
            links.new(tex_node.outputs['Color'], gamma.inputs['Color'])
            links.new(gamma.outputs['Color'], target_socket)

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
                connect_color(tex1, mix.inputs['A'])
                connect_color(tex2, mix.inputs['B'])
                links.new(mix.outputs['Result'], bsdf.inputs['Base Color'])
            else:
                connect_color(tex2, bsdf.inputs['Base Color'])
        else:
            tex1 = nodes.new(type='ShaderNodeTexImage')
            tex1.location = (-300, 0)
            tex1.image = image1
            connect_color(tex1, bsdf.inputs['Base Color'])

        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        return mat

    def setup_scene_lighting(self, context):
        """Add a sun light and world ambient so the terrain is visible in
        Rendered viewport mode (the game renders with a strong directional
        light plus ambient; a bare scene leaves the terrain nearly black)."""
        import math
        scene = context.scene
        sun = None
        for obj in scene.objects:
            if obj.type == "LIGHT" and obj.data.type == "SUN":
                sun = obj
                break
        if sun is None:
            sun_data = bpy.data.lights.new("ROSE_Sun", type="SUN")
            sun_data.energy = 2.5
            sun_data.angle = math.radians(10.0)
            sun = bpy.data.objects.new("ROSE_Sun", sun_data)
            sun.rotation_euler = (math.radians(55), 0, math.radians(25))
            scene.collection.objects.link(sun)
        world = scene.world
        if world and world.use_nodes:
            bg = world.node_tree.nodes.get("Background")
            if bg and bg.inputs[1].default_value < 0.3:
                bg.inputs[1].default_value = 0.5

    def __init__(self, *args, **kwargs):
        """Initialize with caches for path resolution"""
        super().__init__(*args, **kwargs)
        self._texture_path_cache = {}
        self._mesh_path_cache = {}
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
        # instead of expensive rglob
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
        # Only search in likely texture directories, not entire 3DDATA
        search_dirs = [d for d in common_dirs if d.exists()]
        if not search_dirs:
            search_dirs = [root_3ddata]
        
        texture_name_lower = texture_name.lower()
        for search_dir in search_dirs:
            try:
                # Use iterdir for shallow search first
                for item in search_dir.iterdir():
                    if item.is_file() and item.name.lower() == texture_name_lower:
                        result = str(item)
                        self._texture_path_cache[cache_key] = result
                        return result
                    # Shallow search in subdirectories
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


    def create_terrain_texture_atlas(self, zon_path, texture_paths):
        """
        Create a texture atlas combining all terrain textures.
        Uses efficient buffer operations instead of per-pixel loops.
        """
        import numpy as np
        
        # Load all texture images
        images = []
        max_width = 0
        max_height = 0
        
        for idx, tex_path in enumerate(texture_paths):
            resolved_path = self.resolve_texture_path(zon_path, tex_path)
            if resolved_path and Path(resolved_path).exists():
                try:
                    img = bpy.data.images.load(resolved_path)
                    # Ensure we float buffer for faster access
                    img.use_half_precision = False
                    images.append(img)
                    max_width = max(max_width, img.size[0])
                    max_height = max(max_height, img.size[1])
                except Exception as e:
                    images.append(None)
            else:
                images.append(None)
        
        # Filter out None images but keep indices
        valid_images = [(i, img) for i, img in enumerate(images) if img is not None]
        
        if not valid_images:
            return None, {}
        
        # Calculate atlas dimensions (4xN grid)
        textures_per_row = 4
        num_rows = (len(valid_images) + textures_per_row - 1) // textures_per_row
        atlas_width = max_width * textures_per_row
        atlas_height = max_height * num_rows
        
        # Create atlas image with numpy for speed
        atlas_name = "ROSE_Terrain_Atlas"
        try:
            atlas = bpy.data.images.new(atlas_name, width=atlas_width, height=atlas_height, alpha=True, float_buffer=True)
        except Exception as e:
            return None, {}
        
        # Create numpy array for atlas (RGBA)
        atlas_array = np.zeros((atlas_height, atlas_width, 4), dtype=np.float32)
        
        # Build atlas info and copy textures
        atlas_info = {}
        for idx, (original_idx, img) in enumerate(valid_images):
            row = idx // textures_per_row
            col = idx % textures_per_row
            x = col * max_width
            y = row * max_height
            
            img_w, img_h = img.size[0], img.size[1]
            
            # Get pixels as numpy array (faster than Python loops)
            # Blender stores as flat RGBA array
            pixels = np.array(img.pixels[:])
            img_array = pixels.reshape((img_h, img_w, 4))
            
            # Flip Y for Blender's bottom-up coordinate system and place in atlas
            # Calculate destination coordinates (top-left origin for numpy)
            dst_y_start = (num_rows - 1 - row) * max_height
            dst_x_start = col * max_width
            
            # Place image (handling different sizes)
            atlas_array[dst_y_start:dst_y_start + img_h, dst_x_start:dst_x_start + img_w] = img_array
            
            # Store atlas region info (UV space, 0-1)
            atlas_info[original_idx] = {
                'x': x, 'y': y,
                'width': img_w, 'height': img_h,
                'u_min': x / atlas_width,
                'v_min': y / atlas_height,
                'u_max': (x + img_w) / atlas_width,
                'v_max': (y + img_h) / atlas_height
            }
        
        # Assign pixels back to Blender (flatten array)
        atlas.pixels = atlas_array.flatten().tolist()
        atlas.update()
        
        return atlas, atlas_info


    def get_map_name(self):
        """Extract map name (planet) from ZON file path.
        
        Assumes structure: .../MAPS/{PLANET}/{ZONE}/file.zon
        Examples:
        - MAPS/JUNON/JPT01/30_30.ZON -> JUNON
        - MAPS/ELDEON/EJ01/30_30.ZON -> ELDEON  
        - MAPS/LUNAR/LMT01/30_30.ZON -> LUNAR
        """
        try:
            filepath = Path(self.filepath).resolve()
            
            # Navigate up: file.zon -> ZONE -> PLANET -> MAPS
            # So parent = ZONE, grandparent = PLANET
            zone_folder = filepath.parent
            planet_folder = zone_folder.parent
            
            # Verify structure by checking if great-grandparent is MAPS
            if planet_folder.parent.name.upper() == "MAPS":
                return planet_folder.name.upper()
            
            # Fallback: search path components for known planets
            path_parts = [p.upper() for p in filepath.parts]
            known_planets = ["JUNON", "ELDEON", "LUNAR", "DEKARON", "SKAUPTUN", "ORLO"]
            
            for planet in known_planets:
                if planet in path_parts:
                    return planet
            
            # Last resort: return grandparent name
            return planet_folder.name.upper()
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
        import time
        start_time = time.time()
        timings = {}
        
        def record_time(stage_name, stage_start):
            elapsed = time.time() - stage_start
            timings[stage_name] = elapsed
            return time.time()
        
        t = start_time

        # Progress reporting to prevent Blender freeze
        wm = context.window_manager
        wm.progress_begin(0, 100)
        
        # CRITICAL: Disable viewport updates for performance
        # Store original state to restore later
        original_viewport_shade = None
        try:
            original_use_autopersist = bpy.context.preferences.view.use_auto_persist
            bpy.context.preferences.view.use_auto_persist = False
        except:
            original_use_autopersist = None
            pass
        
        # Use faster mesh creation method
        import bmesh
        
        # Batch link lists
        terrain_objects = []
        object_cache = {}  # For mesh instancing

        # Get paths based on file structure
        # Expected: 3DDATA/MAPS/JUNON/JPT01/30_30.ZON
        try:
            filepath = Path(self.filepath).resolve()
            
            # Find 3DDATA root (go up until we find it)
            root_3ddata = filepath
            while root_3ddata.name.upper() != "3DDATA" and root_3ddata.parent != root_3ddata:
                root_3ddata = root_3ddata.parent
            
            if root_3ddata.name.upper() != "3DDATA":
                return {'CANCELLED'}
                
            map_name = self.get_map_name()  # e.g., "JUNON", "ELDEON", "LUNAR"
            zone_code = self.get_zone_code()  # e.g., "JPT", "EJ", "LMT"
            
            planet_path = root_3ddata / map_name
            
            # --- Load CNST ZSC (Construction Objects) ---
            zsc_cnst = None
            
            # Try specific zone code first, then auto-discover
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
            t = record_time("Load ZSC files", t)
            
            # --- Load DECO ZSC (Decoration Objects) ---
            # Load ALL available DECO files (some planets have multiple: EJ+EZ, LP+LZ, etc.)
            zsc_deco_list = []
            
            # Try specific zone code first
            deco_candidates = [
                planet_path / f"LIST_DECO_{zone_code}.ZSC",
                planet_path / f"list_deco_{zone_code.lower()}.zsc",
                planet_path / f"LIST_DECO_{zone_code}.zsc",
                planet_path / f"list_deco_{zone_code.lower()}.ZSC",
            ]
            
            # Auto-discover all DECO files in planet folder
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
            
            wm.progress_update(10)
            
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

            # CRITICAL: Convert grid_size (cm) to meters.
            # Terrain and objects share one absolute world space (see vertex
            # generation below): block corner = 160.0 * block_coord - 5200.0 m.
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
                        
                        # If limit_tiles is enabled, only load the specified tile
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
            tiles.ifos = list_2d(tiles.dimension.y, tiles.dimension.x)

            # Load HIM/TIL/IFO files
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
                        except Exception as e:
                            if self.verbose_logging:
                                self.report({'WARNING'}, f"Failed to load IFO {ifo_file}: {str(e)}")

                    tiles.indices[norm_y][norm_x] = list_2d(him.width, him.length)
                    tiles.hims[norm_y][norm_x] = him
                    tiles.tils[norm_y][norm_x] = til
                    tiles.ifos[norm_y][norm_x] = ifo
                except Exception as e:
                    if self.verbose_logging:
                        self.report({'WARNING'}, f"Failed to load tile {tile_name}: {str(e)}")

            wm.progress_update(30)
            t = record_time("Load tile data", t)
            
            # Generate terrain mesh with PROPER WORLD COORDINATES
            # Absolute world space matching the Rust client (spawning/terrain.rs):
            #   block corner = 160.0 * block_coord - 5200.0 meters (block 0..63),
            #   heightmap samples every grid_scale meters (160.0 = 64 * grid_scale)
            # Terrain and IFO objects share this space: object pos is cm / 100.
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
                    block_x = x + tiles.min_pos.x
                    block_y = y + tiles.min_pos.y
                    base_x = block_x * block_size + world_origin
                    base_y = block_y * block_size + world_origin

                    for vy in range(him.length):
                        for vx in range(him.width):
                            # Both Rose and Blender use Z-up coordinate systems.
                            # Object conversion uses (x, -y, z) / 100, and the
                            # client terrain formula already folds in the Y flip,
                            # so terrain Y is not negated again here.
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
                    
                    # Build material index array for all faces at once
                    material_indices = [0] * len(faces)
                    face_idx = 0

                    # Single pass: compute material for each face type
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
                            # quad area of the 64x64 heightmap grid (matching the
                            # Rust client's tile_x = tilemap.width * block_x.fract()).
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
            t = record_time("Create terrain mesh", t)
            
            # Create terrain object at origin (vertices already in world space)
            terrain_obj = bpy.data.objects.new("ROSE_Terrain", mesh)
            context.collection.objects.link(terrain_obj)

            # Sun light + ambient so Rendered mode matches the game's look
            if self.setup_lighting:
                self.setup_scene_lighting(context)
            
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
            
            # First pass: collect which object IDs are actually used and pre-load materials
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
            
            
            # Pre-create materials only for used objects
            if zsc_cnst:
                used_materials = set()
                for obj_id in used_cnst_objects:
                    if obj_id < len(zsc_cnst.objects):
                        for part in zsc_cnst.objects[obj_id].parts:
                            used_materials.add(part.material_id)
                
                for mat_id in used_materials:
                    if mat_id < len(zsc_cnst.materials):
                        material_cache_cnst[mat_id] = self.create_zsc_material(zsc_cnst.materials[mat_id], root_3ddata)
            
            # Pre-load materials from ALL DECO ZSC files
            for zsc_deco in zsc_deco_list:
                used_materials = set()
                for obj_id in used_deco_objects:
                    if obj_id < len(zsc_deco.objects):
                        for part in zsc_deco.objects[obj_id].parts:
                            used_materials.add(part.material_id)
                
                for mat_id in used_materials:
                    if mat_id < len(zsc_deco.materials):
                        # Use tuple of (zsc_file_index, mat_id) as key to avoid collisions between files
                        cache_key = (id(zsc_deco), mat_id)
                        material_cache_deco[cache_key] = self.create_zsc_material(zsc_deco.materials[mat_id], root_3ddata)
            
            # Spawn objects
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
                        for obj_inst in ifo.cnst_objects:                            
                            if obj_inst.object_id >= len(zsc_cnst.objects):
                                pass
                                continue
                            
                            self.spawn_object(
                                context, cnst_collection, zsc_cnst, obj_inst,
                                material_cache_cnst, mesh_cache_cnst, root_3ddata
                            )
                            total_cnst += 1
                    
                    # Spawn DECO objects (check all loaded DECO ZSC files)
                    if self.load_deco_objects and zsc_deco_list:
                        for obj_inst in ifo.deco_objects:
                            # Find which ZSC file contains this object_id
                            target_zsc = None
                            for deco_zsc in zsc_deco_list:
                                if obj_inst.object_id < len(deco_zsc.objects):
                                    target_zsc = deco_zsc
                                    break
                            
                            if not target_zsc:
                                pass
                                continue
                            
                            # Use appropriate material cache key
                            temp_cache = {}
                            for key, mat in material_cache_deco.items():
                                if key[0] == id(target_zsc):
                                    temp_cache[key[1]] = mat
                            
                            self.spawn_object(
                                context, deco_collection, target_zsc, obj_inst,
                                temp_cache, mesh_cache_deco, root_3ddata
                            )
                            total_deco += 1
            
            t = record_time("Spawn objects", t)
            
        finally:
            pass
        
        # Print timing summary
        elapsed = time.time() - start_time
        self.report({'INFO'}, f"Import completed in {elapsed:.2f} seconds")
        return {"FINISHED"}
    
    def spawn_object(self, context, collection, zsc, ifo_object, material_cache, mesh_cache, base_path):
        """Spawn a ZSC object from IFO data with correct coordinate conversion"""
        zsc_obj = zsc.objects[ifo_object.object_id]
        
        # Create parent empty for this object instance
        obj_name = ifo_object.object_name if ifo_object.object_name else f"Object_{ifo_object.object_id}"
        parent_empty = bpy.data.objects.new(obj_name, None)
        parent_empty.empty_display_type = 'PLAIN_AXES'
        parent_empty.empty_display_size = 0.5
        collection.objects.link(parent_empty)
        
        # --- Transform Conversion (Rose -> Blender) ---
        # Both Rose and Blender use Z-up coordinate systems
        # Convert ROSE coordinates to Blender (X, -Y, Z) and scale by 1/100
        pos = ifo_object.position
        bx, by, bz = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
        # IFO positions are absolute world cm; terrain uses the same space
        # (see terrain vertex generation), so no additional offset is applied.
        parent_empty.location = (bx, by, bz)

        
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
        
        # --- Local Transform (Relative to Parent) ---
        # Both Rose and Blender use Z-up coordinate systems
        # Convert ROSE coordinates to Blender (X, -Y, Z) and scale by 1/100
        # Note: Parts use local coordinates relative to parent, so no world offset needed
        obj.location = convert_rose_position_to_blender(part.position.x, part.position.y, part.position.z)
        
        # Convert rotation from ZSC part (WXYZ in file, stored as XYZW in Vec4) to Blender
        # Both Z-up, only negate Y component: (w, x, y, z) -> (w, x, -y, z)
        from mathutils import Quaternion
        rot = part.rotation
        obj.rotation_mode = 'QUATERNION'
        obj.rotation_quaternion = Quaternion((rot.w, rot.x, -rot.y, rot.z))
        
        # Scale: no axis swap needed since both use Z-up
        obj.scale = (
            part.scale.x,
            part.scale.y,
            part.scale.z
        )
        
        return obj
    
    def load_zms_mesh(self, mesh_path, base_path):
        """Load ZMS mesh with optimized UV assignment"""
        full_path = self.resolve_mesh_path(mesh_path, base_path)
        if not full_path:
            return None
        
        try:
            zms = ZMS(str(full_path))
            mesh_name = Path(mesh_path).stem
            mesh = bpy.data.meshes.new(mesh_name)
            
            # Mesh vertices are in local object space - use as-is from file
            # Coordinate transform is applied via object transform, not vertex positions
            verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
            faces = [(int(i.x), int(i.y), int(i.z)) for i in zms.indices]
            
            mesh.from_pydata(verts, [], faces)
            
            if zms.uv1_enabled() and zms.vertices:
                uv_layer = mesh.uv_layers.new(name="UVMap")
                # Get the UV layer data for faster access
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
        except:
            pass
        
        self._mesh_path_cache[cache_key] = None
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
