from .utils import *
from enum import IntEnum
import struct

class BlockType(IntEnum):
    DeprecatedMapInfo = 0
    DecoObject = 1
    Npc = 2
    CnstObject = 3
    SoundObject = 4
    EffectObject = 5
    AnimatedObject = 6
    DeprecatedWater = 7
    MonsterSpawn = 8
    WaterPlanes = 9
    Warp = 10
    CollisionObject = 11
    EventObject = 12

class IfoObject:
    def __init__(self):
        self.object_name = ""
        self.object_name_raw = None  # raw bytes from disk (lossless round-trip)
        self.minimap_position = Vector2()
        self.object_type = 0
        self.object_id = 0
        self.warp_id = 0
        self.event_id = 0
        self.position = Vector3()
        self.rotation = Quat()
        self.scale = Vector3(1.0, 1.0, 1.0)
    
    def __repr__(self):
        return f"IfoObject(name='{self.object_name}', id={self.object_id}, pos={self.position})"

class IfoMonsterSpawn:
    def __init__(self):
        self.id = 0
        self.count = 0
        self.monster_name = ""
        self.monster_name_raw = None

class IfoMonsterSpawnPoint:
    def __init__(self):
        self.object = IfoObject()
        self.spawn_name = ""
        self.spawn_name_raw = None
        self.basic_spawns = []
        self.tactic_spawns = []
        self.interval = 0
        self.limit_count = 0
        self.range = 0
        self.tactic_points = 0

class IfoEffectObject:
    def __init__(self):
        self.object = IfoObject()
        self.effect_path = ""
        self.effect_path_raw = None

class IfoEventObject:
    def __init__(self):
        self.object = IfoObject()
        self.quest_trigger_name = ""
        self.quest_trigger_name_raw = None
        self.script_function_name = ""
        self.script_function_name_raw = None

class IfoSoundObject:
    def __init__(self):
        self.object = IfoObject()
        self.sound_path = ""
        self.sound_path_raw = None
        self.range = 0
        self.interval = 0

class IfoNpc:
    def __init__(self):
        self.object = IfoObject()
        self.ai_id = 0
        self.quest_file_name = ""
        self.quest_file_name_raw = None

class Ifo:
    def __init__(self, filepath=None):
        self.monster_spawns = []
        self.npcs = []
        self.event_objects = []
        self.animated_objects = []
        self.collision_objects = []
        self.deco_objects = []
        self.cnst_objects = []
        self.effect_objects = []
        self.sound_objects = []
        self.water_size = 0.0
        self.water_planes = []
        self.warps = []
        self._block_order = []
        # Raw original file bytes + byte spans of unparsed block types,
        # re-emitted verbatim on save (DeprecatedMapInfo/DeprecatedWater)
        self.raw = None
        self._raw_blocks = {}
        # Trailing bytes after the last block (unreferenced by the header
        # table, e.g. extra spawn-path data) - preserved verbatim.
        self._tail = b""

        if filepath:
            self.load(filepath)
    
    def read_object(self, f):
        obj = IfoObject()
        obj.object_name, obj.object_name_raw = read_bstr_raw(f)
        obj.warp_id = read_u16(f)
        obj.event_id = read_u16(f)
        obj.object_type = read_u32(f)
        obj.object_id = read_u32(f)
        obj.minimap_position.x = read_u32(f)
        obj.minimap_position.y = read_u32(f)
        obj.rotation = read_quat_xyzw(f)
        obj.position = read_vector3_f32(f)
        obj.scale = read_vector3_f32(f)
        return obj
    
    def load(self, filepath):
        with open(filepath, "rb") as f:
            self.raw = f.read()
            f.seek(0)
            block_count = read_u32(f)
            
            # First pass: read block headers
            blocks = []
            for i in range(block_count):
                block_type = read_u32(f)
                block_offset = read_u32(f)
                blocks.append((block_type, block_offset))
            
            self._block_order = [t for t, _ in blocks]

            # Byte spans of every block, so unparsed types can be re-emitted
            for i, (block_type, block_offset) in enumerate(blocks):
                if i + 1 < len(blocks):
                    end = blocks[i + 1][1]
                else:
                    end = len(self.raw)
                self._raw_blocks[block_type] = self.raw[block_offset:end]
            
            # Second pass: parse blocks
            for block_type, block_offset in blocks:
                f.seek(block_offset)
                
                if block_type == BlockType.AnimatedObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        self.animated_objects.append(self.read_object(f))
                
                elif block_type == BlockType.CollisionObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        self.collision_objects.append(self.read_object(f))
                
                elif block_type == BlockType.CnstObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        self.cnst_objects.append(self.read_object(f))
                
                elif block_type == BlockType.DecoObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        self.deco_objects.append(self.read_object(f))
                
                elif block_type == BlockType.EventObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        obj = IfoEventObject()
                        obj.object = self.read_object(f)
                        obj.quest_trigger_name, obj.quest_trigger_name_raw = read_bstr_raw(f)
                        obj.script_function_name, obj.script_function_name_raw = read_bstr_raw(f)
                        self.event_objects.append(obj)
                
                elif block_type == BlockType.Npc:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        npc = IfoNpc()
                        npc.object = self.read_object(f)
                        npc.ai_id = read_u32(f)
                        npc.quest_file_name, npc.quest_file_name_raw = read_bstr_raw(f)
                        self.npcs.append(npc)
                
                elif block_type == BlockType.MonsterSpawn:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        spawn = IfoMonsterSpawnPoint()
                        spawn.object = self.read_object(f)
                        spawn.spawn_name, spawn.spawn_name_raw = read_bstr_raw(f)
                        
                        basic_count = read_u32(f)
                        for _ in range(basic_count):
                            ms = IfoMonsterSpawn()
                            ms.monster_name, ms.monster_name_raw = read_bstr_raw(f)
                            ms.id = read_u32(f)
                            ms.count = read_u32(f)
                            spawn.basic_spawns.append(ms)
                        
                        tactic_count = read_u32(f)
                        for _ in range(tactic_count):
                            ms = IfoMonsterSpawn()
                            ms.monster_name, ms.monster_name_raw = read_bstr_raw(f)
                            ms.id = read_u32(f)
                            ms.count = read_u32(f)
                            spawn.tactic_spawns.append(ms)
                        
                        spawn.interval = read_u32(f)
                        spawn.limit_count = read_u32(f)
                        spawn.range = read_u32(f)
                        spawn.tactic_points = read_u32(f)
                        self.monster_spawns.append(spawn)
                
                elif block_type == BlockType.WaterPlanes:
                    self.water_size = read_f32(f)
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        start = read_vector3_f32(f)
                        end = read_vector3_f32(f)
                        self.water_planes.append((start, end))
                
                elif block_type == BlockType.Warp:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        self.warps.append(self.read_object(f))
                
                elif block_type == BlockType.EffectObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        obj = IfoEffectObject()
                        obj.object = self.read_object(f)
                        obj.effect_path, obj.effect_path_raw = read_bstr_raw(f)
                        self.effect_objects.append(obj)
                
                elif block_type == BlockType.SoundObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        obj = IfoSoundObject()
                        obj.object = self.read_object(f)
                        obj.sound_path, obj.sound_path_raw = read_bstr_raw(f)
                        obj.range = read_u32(f)
                        obj.interval = read_u32(f)
                        self.sound_objects.append(obj)

            # Bytes after the last block that no header references (kept for
            # lossless round-trips; the parser just stops at EOF). If the
            # last block is an unparsed type, its raw data already covers
            # everything to EOF, so there is no separate tail.
            if blocks and blocks[-1][0] in self._parsed_block_types():
                self._tail = self.raw[f.tell():]

    def total_objects(self):
        """Total number of objects across all blocks."""
        return (len(self.deco_objects) + len(self.cnst_objects) +
                len(self.event_objects) + len(self.warps) +
                len(self.sound_objects) + len(self.effect_objects) +
                len(self.animated_objects) + len(self.collision_objects) +
                len(self.npcs) + len(self.monster_spawns))

    # ------------------------------------------------------------------
    # Serialization (byte-compatible with the Rust map editor's
    # IfoWriter in src/map_editor/save/ifo_export.rs)
    # ------------------------------------------------------------------

    @staticmethod
    def _bstr_bytes(raw, s):
        """Bytes to write for a string: the original raw bytes when the
        object was loaded from disk (lossless, preserves EUC-KR), otherwise
        a UTF-8 encoding of the current value."""
        if raw is not None:
            return raw
        return s.encode("utf-8") if isinstance(s, str) else bytes(s)

    @staticmethod
    def _write_bstr(buf, s, raw=None):
        data = Ifo._bstr_bytes(raw, s)
        buf.append(len(data))
        buf.extend(data)

    @staticmethod
    def _write_object(buf, obj):
        Ifo._write_bstr(buf, obj.object_name, obj.object_name_raw)
        buf.extend(struct.pack("<HHIIII", obj.warp_id, obj.event_id,
                               obj.object_type, obj.object_id,
                               int(obj.minimap_position.x), int(obj.minimap_position.y)))
        buf.extend(struct.pack("<ffff", obj.rotation.x, obj.rotation.y,
                               obj.rotation.z, obj.rotation.w))
        buf.extend(struct.pack("<fff", obj.position.x, obj.position.y, obj.position.z))
        buf.extend(struct.pack("<fff", obj.scale.x, obj.scale.y, obj.scale.z))

    def _serialize_block(self, block_type, buf):
        # Unparsed block types are re-emitted verbatim from the original file
        if block_type not in self._parsed_block_types():
            raw = self._raw_blocks.get(block_type)
            if raw is not None:
                buf.extend(raw)
            return len(buf)

        if block_type == BlockType.DecoObject:
            objs = self.deco_objects
        elif block_type == BlockType.CnstObject:
            objs = self.cnst_objects
        elif block_type == BlockType.AnimatedObject:
            objs = self.animated_objects
        elif block_type == BlockType.CollisionObject:
            objs = self.collision_objects
        elif block_type == BlockType.Warp:
            objs = self.warps
        elif block_type == BlockType.EventObject:
            objs = self.event_objects
        elif block_type == BlockType.Npc:
            objs = self.npcs
        elif block_type == BlockType.SoundObject:
            objs = self.sound_objects
        elif block_type == BlockType.EffectObject:
            objs = self.effect_objects
        elif block_type == BlockType.MonsterSpawn:
            objs = self.monster_spawns
        elif block_type == BlockType.WaterPlanes:
            # water_size f32 comes before the plane count
            buf.extend(struct.pack("<f", self.water_size))
            buf.extend(struct.pack("<I", len(self.water_planes)))
            for start_v, end_v in self.water_planes:
                buf.extend(struct.pack("<fff", start_v.x, start_v.y, start_v.z))
                buf.extend(struct.pack("<fff", end_v.x, end_v.y, end_v.z))
            return len(buf)
        else:
            return 0

        buf.extend(struct.pack("<I", len(objs)))

        for obj in objs:
            if block_type == BlockType.EventObject:
                self._write_object(buf, obj.object)
                self._write_bstr(buf, obj.quest_trigger_name, obj.quest_trigger_name_raw)
                self._write_bstr(buf, obj.script_function_name, obj.script_function_name_raw)
            elif block_type == BlockType.Npc:
                self._write_object(buf, obj.object)
                buf.extend(struct.pack("<I", obj.ai_id))
                self._write_bstr(buf, obj.quest_file_name, obj.quest_file_name_raw)
            elif block_type == BlockType.SoundObject:
                self._write_object(buf, obj.object)
                self._write_bstr(buf, obj.sound_path, obj.sound_path_raw)
                buf.extend(struct.pack("<II", obj.range, obj.interval))
            elif block_type == BlockType.EffectObject:
                self._write_object(buf, obj.object)
                self._write_bstr(buf, obj.effect_path, obj.effect_path_raw)
            elif block_type == BlockType.MonsterSpawn:
                self._write_object(buf, obj.object)
                self._write_bstr(buf, obj.spawn_name, obj.spawn_name_raw)
                buf.extend(struct.pack("<I", len(obj.basic_spawns)))
                for ms in obj.basic_spawns:
                    self._write_bstr(buf, ms.monster_name, ms.monster_name_raw)
                    buf.extend(struct.pack("<II", ms.id, ms.count))
                buf.extend(struct.pack("<I", len(obj.tactic_spawns)))
                for ms in obj.tactic_spawns:
                    self._write_bstr(buf, ms.monster_name, ms.monster_name_raw)
                    buf.extend(struct.pack("<II", ms.id, ms.count))
                buf.extend(struct.pack("<IIII", obj.interval, obj.limit_count,
                                       obj.range, obj.tactic_points))
            else:
                self._write_object(buf, obj)

        return len(buf)

    def save(self, filepath):
        """Serialize the IFO file: block count + header table, then block
        data. All blocks present in the original file are kept (including
        empty ones), and new block types appended at the end."""
        block_order = list(self._block_order)
        if not block_order:
            block_order = [t for t in BlockType if self._block_has_data(t)]
        for t in BlockType:
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
            f.write(self._tail)

    @staticmethod
    def _parsed_block_types():
        return {BlockType.DecoObject, BlockType.Npc, BlockType.CnstObject,
                BlockType.SoundObject, BlockType.EffectObject,
                BlockType.AnimatedObject, BlockType.MonsterSpawn,
                BlockType.WaterPlanes, BlockType.Warp,
                BlockType.CollisionObject, BlockType.EventObject}

    def _block_has_data(self, block_type):
        if block_type == BlockType.DecoObject:
            return len(self.deco_objects) > 0
        if block_type == BlockType.CnstObject:
            return len(self.cnst_objects) > 0
        if block_type == BlockType.AnimatedObject:
            return len(self.animated_objects) > 0
        if block_type == BlockType.CollisionObject:
            return len(self.collision_objects) > 0
        if block_type == BlockType.Warp:
            return len(self.warps) > 0
        if block_type == BlockType.EventObject:
            return len(self.event_objects) > 0
        if block_type == BlockType.Npc:
            return len(self.npcs) > 0
        if block_type == BlockType.SoundObject:
            return len(self.sound_objects) > 0
        if block_type == BlockType.EffectObject:
            return len(self.effect_objects) > 0
        if block_type == BlockType.MonsterSpawn:
            return len(self.monster_spawns) > 0
        if block_type == BlockType.WaterPlanes:
            return len(self.water_planes) > 0 or self.water_size > 0.0
        return False