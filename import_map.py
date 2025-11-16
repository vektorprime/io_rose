from .rose.him import Him
from .rose.til import Til
from .rose.zon import Zon
from .rose.zsc import Zsc

from .rose.utils import Vector2, list_2d

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

    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]

    def create_terrain_material(self, zon_path, texture_paths):
        """Create a material with the first available texture."""
        self.report({'INFO'}, f"in create_terrain_material zon_path is {zon_path}, texture_paths is {texture_paths}")
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
        self.report({'INFO'}, f"in resolve_texture_path zone_filepath is {zon_filepath}, texture_path is {texture_path}")
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
                    if file.name.lower() == texture_relative.lower():
                        self.report({'INFO'}, f"Found texture via case-insensitive match: {file}")
                        return str(file)
            except Exception:
                continue

        self.report({'WARNING'}, f"Texture not found: {texture_relative}")
        return None


    def get_map_name(self):
        # Returns the folder name for the map; example: JUNON for files that contain "JPT"
        fp = str(self.filepath).upper()
        if "JPT" in fp:
            return "JUNON"
        # fallback: try to infer from parent directory (example heuristic)
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
        #get 3Ddata path
        root_3ddata = self.get_3ddata_path()
        self.report({'INFO'}, f"root_3ddata is {root_3ddata}")
        map_name = self.get_map_name()
        self.report({'INFO'}, f"get_map_name returned {map_name}")
        town_name = self.get_town_name()
        self.report({'INFO'}, f"get_town_name returned {town_name}")
        zsc_full_path = os.path.join(root_3ddata, map_name, f"LIST_CNST_{town_name}.ZSC")
        self.report({'INFO'}, f"zsc_full_path is {zsc_full_path}")
        #zsc_path = os.path.join(os.path.dirname(self.filepath), "scene.zsc")
        if os.path.exists(zsc_full_path):
            zsc = Zsc(zsc_full_path)
            self.report({'INFO'}, f"Loaded {len(zsc.meshes)} meshes from ZSC")
            self.report({'INFO'}, f"Loaded {len(zsc.materials)} materials/textures from ZSC")
            self.report({'INFO'}, f"Loaded {len(zsc.effects)} effects from ZSC")
            self.report({'INFO'}, f"Loaded {len(zsc.objects)} objects from ZSC")
            zsc_size = os.path.getsize(zsc_full_path)
            self.report({'INFO'}, f"ZSC file size is {zsc_size}")
            #for obj in zsc.objects:
            #    self.report({'INFO'}, f"Object: {len(obj.meshes)} meshes, {len(obj.effects)} effects")

        
        self.report({'INFO'}, "in execute: Starting map import process")
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
            # Use HIM files to build tiles data
            if file.endswith(him_ext):
                # Extract coordinate of tiles system from file name
                x, y = map(int, file.split(".")[0].split("_"))

                # Get min/max pos of tile system
                tiles.min_pos.x = min(x, tiles.min_pos.x)
                tiles.min_pos.y = min(y, tiles.min_pos.y)
                tiles.max_pos.x = max(x, tiles.max_pos.x)
                tiles.max_pos.y = max(y, tiles.max_pos.y)

                tiles.count += 1
                tiles.coords.append((x, y))

        tiles.dimension.x = tiles.max_pos.x - tiles.min_pos.x + 1
        tiles.dimension.y = tiles.max_pos.y - tiles.min_pos.y + 1

        self.report({'INFO'}, f"Detected tile grid: {tiles.dimension.x} x {tiles.dimension.y}")
        self.report({'INFO'}, f"Tile range: X({tiles.min_pos.x} to {tiles.max_pos.x}), Y({tiles.min_pos.y} to {tiles.max_pos.y})")

        tiles.indices = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.hims = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.tils = list_2d(tiles.dimension.y, tiles.dimension.x)
        tiles.ifos = list_2d(tiles.dimension.y, tiles.dimension.x)

        self.report({'INFO'}, "Loading HIM/TIL files...")
        for x, y in tiles.coords:
            tile_name = "{}_{}".format(x, y)
            him_file = os.path.join(zon_dir, tile_name + him_ext)
            til_file = os.path.join(zon_dir, tile_name + til_ext)
            ifo_file = os.path.join(zon_dir, tile_name + ifo_ext)

            # Calculate relative offset for this tile
            norm_x = x - tiles.min_pos.x
            norm_y = y - tiles.min_pos.y

            try:
                him = Him(him_file)
                til = Til(til_file)

                # Stores vertex indices
                him.indices = list_2d(him.width, him.length)

                tiles.indices[norm_y][norm_x] = list_2d(him.width, him.length)
                tiles.hims[norm_y][norm_x] = him
                tiles.tils[norm_y][norm_x] = til

                self.report({'INFO'}, f"Loaded tile: {tile_name} (HIM: {him.width}x{him.length})")
            except Exception as e:
                self.report({'WARNING'}, f"Failed to load tile {tile_name}: {e}")

        tiles.offsets = list_2d(tiles.dimension.y, tiles.dimension.x)

        # Calculate tile offsets
        length, cur_length = 0, 0
        self.report({'INFO'}, "Calculating tile offsets...")
        for y in range(tiles.dimension.y):
            width = 0
            for x in range(tiles.dimension.x):
                him = tiles.hims[y][x]

                offset = Vector2(width, length)
                tiles.offsets[y][x] = offset

                width += him.width
                cur_length = him.length

            length += cur_length

        vertices = []
        edges = []
        faces = []

        self.report({'INFO'}, "Generating mesh data...")
        # Generate mesh data (vertices/edges/faces) for each tile
        for y in range(tiles.dimension.y):
            for x in range(tiles.dimension.x):
                indices = tiles.indices[y][x]
                him = tiles.hims[y][x]

                offset_x = tiles.offsets[y][x].x
                offset_y = tiles.offsets[y][x].y

                for vy in range(him.length):
                    for vx in range(him.width):
                        # Create vertices
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

        self.report({'INFO'}, "Generating inter-tile connections...")
        # Generate edges/faces between HIM tiles
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

                        if not is_x_edge:
                            if is_x_edge_vertex:
                                next_indices = tiles.indices[y][x + 1]
                                v1 = indices[vy][vx]
                                v2 = next_indices[vy][0]
                                v3 = next_indices[vy + 1][0]
                                v4 = indices[vy + 1][vx]
                                edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                                faces.append((v1, v2, v3, v4))

                        if not is_y_edge:
                            if is_y_edge_vertex:
                                next_indices = tiles.indices[y + 1][x]
                                v1 = indices[vy][vx]
                                v2 = indices[vy][vx + 1]
                                v3 = next_indices[0][vx + 1]
                                v4 = next_indices[0][vx]
                                edges += ((v1, v2), (v2, v3), (v3, v4), (v4, v1))
                                faces.append((v1, v2, v3, v4))

                        if not is_x_edge and not is_y_edge:
                            if is_corner_vertex:
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

        self.report({'INFO'}, f"Mesh generated: {len(vertices)} vertices, {len(edges)} edges, {len(faces)} faces")

        # Create mesh
        mesh = bpy.data.meshes.new("ROSE_Terrain")
        mesh.from_pydata(vertices, edges, faces)
        mesh.update()

        # Add UV layer
        mesh.uv_layers.new(name="UVMap")

        # Apply UVs to faces
        self.report({'INFO'}, "Applying UVs to mesh...")
        for loop_idx, loop in enumerate(mesh.loops):
            vi = loop.vertex_index
            u = (vertices[vi][0] % 1.0)
            v = (vertices[vi][1] % 1.0)
            mesh.uv_layers["UVMap"].data[loop_idx].uv = (u, 1 - v)

        # Create and apply material with texture
        self.report({'INFO'}, f"Attempting to load materials for {len(zon.textures)} textures...")
        if self.load_texture and zon.textures:
            self.report({'INFO'}, f"Found {len(zon.textures)} textures from ZON file")
            for texture in zon.textures:
                self.report({'INFO'}, f"Processing texture: {texture}")
                mat = self.create_terrain_material(self.filepath, [texture])
                mesh.materials.append(mat)
                self.report({'INFO'}, f"Material applied for texture: {texture}")
        elif self.load_texture:
            self.report({'WARNING'}, "No textures found in ZON file")
        else:
            self.report({'INFO'}, "Texture loading disabled by user")

        mesh.update(calc_edges=True)

        obj = bpy.data.objects.new("ROSE_Terrain", mesh)
        context.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)

        self.report({'INFO'}, "Import completed successfully!")
        return {"FINISHED"}
