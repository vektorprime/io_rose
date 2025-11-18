from .utils import *
from enum import IntEnum

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

class IfoMonsterSpawnPoint:
    def __init__(self):
        self.object = IfoObject()
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

class IfoEventObject:
    def __init__(self):
        self.object = IfoObject()
        self.quest_trigger_name = ""
        self.script_function_name = ""

class IfoSoundObject:
    def __init__(self):
        self.object = IfoObject()
        self.sound_path = ""
        self.range = 0
        self.interval = 0

class IfoNpc:
    def __init__(self):
        self.object = IfoObject()
        self.ai_id = 0
        self.quest_file_name = ""

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
        
        if filepath:
            self.load(filepath)
    
    def read_object(self, f):
        obj = IfoObject()
        obj.object_name = read_bstr(f)
        obj.warp_id = read_u16(f)
        obj.event_id = read_u16(f)
        obj.object_type = read_u32(f)
        obj.object_id = read_u32(f)
        obj.minimap_position.x = read_u32(f)
        obj.minimap_position.y = read_u32(f)
        obj.rotation = read_quat_wxyz(f)
        obj.position = read_vector3_f32(f)
        obj.scale = read_vector3_f32(f)
        return obj
    
    def load(self, filepath):
        with open(filepath, "rb") as f:
            block_count = read_u32(f)
            
            # First pass: read block headers
            blocks = []
            for i in range(block_count):
                block_type = read_u32(f)
                block_offset = read_u32(f)
                blocks.append((block_type, block_offset))
            
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
                        obj.quest_trigger_name = read_bstr(f)
                        obj.script_function_name = read_bstr(f)
                        self.event_objects.append(obj)
                
                elif block_type == BlockType.Npc:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        npc = IfoNpc()
                        npc.object = self.read_object(f)
                        npc.ai_id = read_u32(f)
                        npc.quest_file_name = read_bstr(f)
                        self.npcs.append(npc)
                
                elif block_type == BlockType.MonsterSpawn:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        spawn = IfoMonsterSpawnPoint()
                        spawn.object = self.read_object(f)
                        _ = read_bstr(f)  # spawn_name
                        
                        basic_count = read_u32(f)
                        for _ in range(basic_count):
                            _ = read_bstr(f)  # monster_name
                            ms = IfoMonsterSpawn()
                            ms.id = read_u32(f)
                            ms.count = read_u32(f)
                            spawn.basic_spawns.append(ms)
                        
                        tactic_count = read_u32(f)
                        for _ in range(tactic_count):
                            _ = read_bstr(f)  # monster_name
                            ms = IfoMonsterSpawn()
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
                        obj.effect_path = read_bstr(f)
                        self.effect_objects.append(obj)
                
                elif block_type == BlockType.SoundObject:
                    object_count = read_u32(f)
                    for _ in range(object_count):
                        obj = IfoSoundObject()
                        obj.object = self.read_object(f)
                        obj.sound_path = read_bstr(f)
                        obj.range = read_u32(f)
                        obj.interval = read_u32(f)
                        self.sound_objects.append(obj)