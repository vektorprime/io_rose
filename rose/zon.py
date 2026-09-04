from .utils import *
import struct

class BlockType:
    Info = 0
    Spawns = 1
    Textures = 2
    Tiles = 3
    Economy = 4

class ZoneType:
    Grass = 0
    Mountain = 1
    MountainVillage = 2
    BoatVillage = 3
    Login = 4
    MountainGorge = 5
    Beach = 6
    JunonDungeon = 7
    LunaSnow = 8
    Birth = 9
    JunonField = 10
    LunaDungeon = 11
    EldeonField = 12
    EldeonField2 = 13
    JunonPyramids = 14

class Position:
    def __init__(self):
        self.is_used = False
        self.position = Vector2()

    def __repr__(self):
        return "Position ({},{})[{}]".format(
                self.position.x,
                self.position.y,
                "Used" if self.is_used else "Not Used")

class Spawn:
    def __init__(self):
        self.position = Vector3()
        self.name = ""
        self.name_raw = None  # raw bytes from disk (lossless round-trip)

    def __repr__(self):
        return "Spawn '{}'".format(self.name)

class Tile:
    def __init__(self): 
        self.layer1 = 0
        self.layer2 = 0
        self.offset1 = 0
        self.offset2 = 0
        self.blending = False
        self.rotation = 0
        self.tile_type = 0

class Zon:
    def __init__(self, filepath=None):
        self.zone_type = None
        self.width = 0
        self.length = 0
        self.grid_count = 0
        self.grid_size = 0.0
        self.xcount = 0
        self.ycount = 0
        self.start_position = Vector2()
        self.positions = []
        self.spawns = []
        self.textures = []
        self.textures_raw = []  # raw bytes per entry, parallel to textures
        self.tiles = []
        self._block_order = []

        self.name = ""
        self.name_raw = None
        self.is_underground = False
        self.background_music_path = ""
        self.background_music_path_raw = None
        self.sky_path = ""
        self.sky_path_raw = None
        self.economy_check_rate = 50
        self.population_base = 100
        self.population_growth_rate = 10
        self.metal_consumption = 50
        self.stone_consumption = 50
        self.wood_consumption = 50
        self.leather_consumption = 50
        self.cloth_consumption = 50
        self.alchemy_consumption = 50
        self.chemical_consumption = 50
        self.industrial_consumption = 50
        self.medicine_consumption = 50
        self.food_consumption = 50

        if filepath:
            self.load(filepath)
    
    def __repr__(self):
        return "{} zone".format(self.zone_type)

    def load(self, filepath):
        with open(filepath, "rb") as f:
            block_count = read_u32(f)

            # First pass: read all block headers
            blocks = []
            for i in range(block_count):
                block_type = read_u32(f)
                block_offset = read_u32(f)
                blocks.append((block_type, block_offset))

            self._block_order = [t for t, _ in blocks]
            
            # Second pass: parse each block
            for block_type, block_offset in blocks:
                f.seek(block_offset)

                if block_type == BlockType.Info:
                    self.zone_type = read_i32(f)
                    self.width = read_i32(f)
                    self.length = read_i32(f)
                    self.grid_count = read_i32(f)
                    self.grid_size = read_f32(f)

                    # Read xcount and ycount (NOT the same as width/length:
                    # JDT01 has width=64, length=64 but xcount=32, ycount=32)
                    self.xcount = read_i32(f)
                    self.ycount = read_i32(f)
                    
                    # Row-major [length rows][width cols], matching the
                    # TIL/HIM convention (til.rs / him.rs).
                    self.positions = list_2d(self.length, self.width)
                    for y in range(self.length):
                        for x in range(self.width):
                            p = Position()
                            p.is_used = read_bool(f)
                            p.position.x = read_f32(f)
                            p.position.y = read_f32(f)
                            self.positions[y][x] = p

                elif block_type == BlockType.Spawns:
                    spawn_count = read_u32(f)

                    for j in range(spawn_count):
                        s = Spawn()
                        # Plain sequential vector (zon.rs
                        # read_vector3_f32); the old code swapped Y/Z.
                        s.position.x = read_f32(f)
                        s.position.y = read_f32(f)
                        s.position.z = read_f32(f)
                        s.name, s.name_raw = read_bstr_raw(f)

                        self.spawns.append(s)

                elif block_type == BlockType.Textures:
                    texture_count = read_u32(f)

                    for j in range(texture_count):
                        tex, raw = read_bstr_raw(f)
                        self.textures.append(tex)
                        self.textures_raw.append(raw)

                    # The texture list is terminated by an "end" sentinel
                    # (the Rust client stops loading at the first "end" entry).
                    if "end" in self.textures:
                        self.textures = self.textures[:self.textures.index("end")]

                elif block_type == BlockType.Tiles:
                    tile_count = read_u32(f)

                    for j in range(tile_count):
                        t = Tile()
                        t.layer1 = read_u32(f)
                        t.layer2 = read_u32(f)
                        t.offset1 = read_u32(f)
                        t.offset2 = read_u32(f)
                        t.blending = (read_u32(f) != 0)
                        t.rotation = read_u32(f)
                        # 0-6 are the valid ZonTileRotation values (zon.rs);
                        # anything else is corrupt, not a new rotation.
                        if t.rotation > 6:
                            raise ValueError(
                                f"Invalid tile rotation {t.rotation}")
                        t.tile_type = read_u32(f)

                        self.tiles.append(t)

                elif block_type == BlockType.Economy:
                    self.name, self.name_raw = read_bstr_raw(f)
                    self.is_underground = (read_i32(f) != 0)
                    self.background_music_path, self.background_music_path_raw = read_bstr_raw(f)
                    self.sky_path, self.sky_path_raw = read_bstr_raw(f)
                    self.economy_check_rate = read_i32(f)
                    self.population_base = read_i32(f)
                    self.population_growth_rate = read_i32(f)
                    self.metal_consumption = read_i32(f)
                    self.stone_consumption = read_i32(f)
                    self.wood_consumption = read_i32(f)
                    self.leather_consumption = read_i32(f)
                    self.cloth_consumption = read_i32(f)
                    self.alchemy_consumption = read_i32(f)
                    self.chemical_consumption = read_i32(f)
                    self.industrial_consumption = read_i32(f)
                    self.medicine_consumption = read_i32(f)
                    self.food_consumption = read_i32(f)

    # ------------------------------------------------------------------
    # Serialization (mirrors the parsed layout exactly; the texture list
    # is terminated with the literal "end" sentinel the client expects)
    # ------------------------------------------------------------------

    def _block_has_data(self, block_type):
        if block_type == BlockType.Info:
            return True
        if block_type == BlockType.Spawns:
            return len(self.spawns) > 0
        if block_type == BlockType.Textures:
            return len(self.textures) > 0
        if block_type == BlockType.Tiles:
            return len(self.tiles) > 0
        if block_type == BlockType.Economy:
            return True
        return False

    @staticmethod
    def _str_bytes(raw, s):
        """Bytes for a string: original raw bytes when loaded from disk
        (lossless, preserves EUC-KR), otherwise UTF-8."""
        if raw is not None:
            return raw
        return s.encode("utf-8") if isinstance(s, str) else bytes(s)

    def _serialize_block(self, block_type, buf):
        if block_type == BlockType.Info:
            buf.extend(struct.pack("<iiii", self.zone_type, self.width,
                                   self.length, self.grid_count))
            buf.extend(struct.pack("<f", self.grid_size))
            buf.extend(struct.pack("<ii", self.xcount, self.ycount))
            for y in range(self.length):
                for x in range(self.width):
                    p = self.positions[y][x]
                    buf.append(1 if p.is_used else 0)
                    buf.extend(struct.pack("<ff", p.position.x, p.position.y))
        elif block_type == BlockType.Spawns:
            buf.extend(struct.pack("<I", len(self.spawns)))
            for s in self.spawns:
                buf.extend(struct.pack("<fff", s.position.x, s.position.y,
                                       s.position.z))
                data = Zon._str_bytes(s.name_raw, s.name)
                buf.append(len(data))
                buf.extend(data)
        elif block_type == BlockType.Textures:
            buf.extend(struct.pack("<I", len(self.textures) + 1))
            for i, tex in enumerate(self.textures):
                data = Zon._str_bytes(
                    self.textures_raw[i] if i < len(self.textures_raw) else None,
                    tex)
                buf.append(len(data))
                buf.extend(data)
            buf.append(3)  # "end" sentinel
            buf.extend(b"end")
        elif block_type == BlockType.Tiles:
            buf.extend(struct.pack("<I", len(self.tiles)))
            for t in self.tiles:
                buf.extend(struct.pack("<IIII", t.layer1, t.layer2,
                                       t.offset1, t.offset2))
                buf.extend(struct.pack("<I", 1 if t.blending else 0))
                buf.extend(struct.pack("<I", t.rotation))
                buf.extend(struct.pack("<I", t.tile_type))
        elif block_type == BlockType.Economy:
            data = Zon._str_bytes(self.name_raw, self.name)
            buf.append(len(data))
            buf.extend(data)
            buf.extend(struct.pack("<i", 1 if self.is_underground else 0))
            for raw, path in ((self.background_music_path_raw, self.background_music_path),
                              (self.sky_path_raw, self.sky_path)):
                data = Zon._str_bytes(raw, path)
                buf.append(len(data))
                buf.extend(data)
            for v in (self.economy_check_rate, self.population_base,
                      self.population_growth_rate, self.metal_consumption,
                      self.stone_consumption, self.wood_consumption,
                      self.leather_consumption, self.cloth_consumption,
                      self.alchemy_consumption, self.chemical_consumption,
                      self.industrial_consumption, self.medicine_consumption,
                      self.food_consumption):
                buf.extend(struct.pack("<i", v))

    def save(self, filepath):
        """Serialize the ZON file: block count + header table, then block
        data. All blocks present in the original file are kept (including
        empty ones); new blocks are appended at the end."""
        block_order = list(self._block_order)
        if not block_order:
            block_order = [t for t in (BlockType.Info, BlockType.Spawns,
                                       BlockType.Textures, BlockType.Tiles,
                                       BlockType.Economy)
                           if self._block_has_data(t)]
        for t in (BlockType.Info, BlockType.Spawns, BlockType.Textures,
                  BlockType.Tiles, BlockType.Economy):
            if t not in block_order and self._block_has_data(t):
                block_order.append(t)

        header_size = 4 + 8 * len(block_order)
        blocks = []
        offset = header_size
        for t in block_order:
            buf = bytearray()
            self._serialize_block(t, buf)
            blocks.append((t, offset, buf))
            offset += len(buf)

        with open(filepath, "wb") as f:
            f.write(struct.pack("<I", len(block_order)))
            for t, off, _ in blocks:
                f.write(struct.pack("<II", t, off))
            for _, _, buf in blocks:
                f.write(buf)