# zsc.py - Rose Online ZSC Scene File Parser (Rust-Exact Match)
from .utils import *

from enum import IntEnum
from typing import List, Optional, NamedTuple, Dict, Any
import bpy
import os
from bpy.props import StringProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper

# === ENUMS ===

class BlendMode(IntEnum):
    NONE = 0
    CUSTOM = 1
    NORMAL = 2
    LIGHTEN = 3


class GlowType(IntEnum):
    NONE = 0
    NOTSET = 1
    SIMPLE = 2
    LIGHT = 3
    TEXTURE = 4
    TEXTURELIGHT = 5
    ALPHA = 6


class CollisionType(IntEnum):
    NONE = 0
    SPHERE = 1
    AXISALIGNEDBOUNDINGBOX = 2
    ORIENTEDBOUNDINGBOX = 3
    POLYGON = 4


class EffectType(IntEnum):
    NORMAL = 0
    DAYNIGHT = 1
    LIGHTCONTAINER = 2
    UNKNOWN = 3  # fallback for invalid


# === CLASSES ===

class Vec3(NamedTuple):
    x: float
    y: float
    z: float

    @classmethod
    def from_bytes(cls, f):
        return cls(*read_vector3_f32(f))


class Vec4(NamedTuple):
    x: float
    y: float
    z: float
    w: float

    @classmethod
    def from_bytes(cls, f):
        w = read_f32(f)
        x = read_f32(f)
        y = read_f32(f)
        z = read_f32(f)
        return cls(x, y, z, w)


class ZscMaterial:
    def __init__(self):
        self.path: str = ""
        self.is_skin: bool = False
        self.alpha_enabled: bool = False
        self.two_sided: bool = False
        self.alpha_test: Optional[float] = None
        self.z_write_enabled: bool = False
        self.z_test_enabled: bool = False
        self.blend_mode: BlendMode = BlendMode.NONE
        self.specular_enabled: bool = False
        self.alpha: float = 1.0
        self.glow: Optional[GlowType] = None
        self.glow_color: Vec3 = Vec3(1.0, 1.0, 1.0)

    def __repr__(self):
        return f"Material(path='{self.path}', alpha={self.alpha}, glow={self.glow})"


class ZscObjectPart:
    def __init__(self):
        self.mesh_id: int = 0
        self.material_id: int = 0
        self.position: Vec3 = Vec3(0.0, 0.0, 0.0)
        self.rotation: Vec4 = Vec4(0.0, 0.0, 0.0, 1.0)
        self.scale: Vec3 = Vec3(1.0, 1.0, 1.0)
        self.bone_index: Optional[int] = None
        self.dummy_index: Optional[int] = None
        self.parent: Optional[int] = None  # 1-based → 0-based
        self.collision_shape: Optional[CollisionType] = None
        self.collision_flags: int = 0  # Raw u16 bits
        self.animation_path: Optional[str] = None

    def __repr__(self):
        return f"Part(mesh={self.mesh_id}, mat={self.material_id}, parent={self.parent})"


class ZscObjectEffect:
    def __init__(self):
        self.effect_id: int = 0
        self.effect_type: EffectType = EffectType.NORMAL
        self.position: Vec3 = Vec3(0.0, 0.0, 0.0)
        self.rotation: Vec4 = Vec4(0.0, 0.0, 0.0, 1.0)
        self.scale: Vec3 = Vec3(1.0, 1.0, 1.0)
        self.parent: Optional[int] = None  # 1-based → 0-based

    def __repr__(self):
        return f"Effect(id={self.effect_id}, type={self.effect_type}, parent={self.parent})"


class ZscObject:
    def __init__(self):
        self.parts: List[ZscObjectPart] = []
        self.effects: List[ZscObjectEffect] = []

    def __repr__(self):
        return f"Object(parts={len(self.parts)}, effects={len(self.effects)})"


class Zsc:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.meshes: List[str] = []
        self.materials: List[ZscMaterial] = []
        self.effects: List[str] = []
        self.objects: List[ZscObject] = []
        self.load(filepath)

    def __repr__(self):
        return f"Zsc(file='{self.filepath}', meshes={len(self.meshes)}, materials={len(self.materials)}, objects={len(self.objects)})"

    def load(self, filepath: str):
        """Load and parse the ZSC file with logging."""
        zsc_size = os.path.getsize(filepath)
        try:
            with open(filepath, "rb") as f:
                def read_u32():
                    val = f.read(4)
                    if len(val) != 4:
                        raise EOFError("Unexpected EOF")
                    return int.from_bytes(val, 'little')

                def read_u16():
                    val = f.read(2)
                    if len(val) != 2:
                        raise EOFError("Unexpected EOF")
                    return int.from_bytes(val, 'little')

                def read_u8():
                    val = f.read(1)
                    if len(val) != 1:
                        raise EOFError("Unexpected EOF")
                    return int.from_bytes(val, 'little')

                def read_f32():
                    val = f.read(4)
                    if len(val) != 4:
                        raise EOFError("Unexpected EOF")
                    return struct.unpack('<f', val)[0]

                def read_str():
                    data = []
                    while True:
                        b = f.read(1)
                        if not b or b == b'\x00':
                            break
                        data.append(b)
                    return ''.join([chr(b[0]) for b in data])

                def read_str_no_null(size):
                    return f.read(size).decode('utf-8', errors='ignore')
                
                def read_vec3():
                    return Vec3(*[read_f32() for _ in range(3)])

                def read_quat():
                    w = read_f32()
                    x = read_f32()
                    y = read_f32()
                    z = read_f32()
                    return Vec4(x, y, z, w)

                def log_read(name, val):
                    offset = f.tell()
                    print(f"[{offset:08X}] {name} -> {val}")

                # --- Meshes ---
                mesh_count = read_u16()
                log_read("mesh_count", mesh_count)
                self.meshes = []
                for _ in range(mesh_count):
                    path = read_str()
                    log_read("mesh_path", path)
                    self.meshes.append(path)

                # --- Materials ---
                material_count = read_u16()
                log_read("material_count", material_count)
                for _ in range(material_count):
                    mat = ZscMaterial()
                    mat.path = read_str()
                    log_read("material_path", mat.path)

                    mat.is_skin = bool(read_u16())
                    log_read("is_skin", mat.is_skin)
                    mat.alpha_enabled = bool(read_u16())
                    log_read("alpha_enabled", mat.alpha_enabled)
                    mat.two_sided = bool(read_u16())
                    log_read("two_sided", mat.two_sided)
                    alpha_test_enabled = bool(read_u16())
                    log_read("alpha_test_enabled", alpha_test_enabled)
                    alpha_ref = read_u16() / 256.0
                    log_read("alpha_ref", alpha_ref)
                    mat.z_test_enabled = bool(read_u16())
                    log_read("z_test_enabled", mat.z_test_enabled)
                    mat.z_write_enabled = bool(read_u16())
                    log_read("z_write_enabled", mat.z_write_enabled)

                    blend_mode = read_u16()
                    log_read("blend_mode", blend_mode)
                    mat.blend_mode = BlendMode(blend_mode) if blend_mode in [0, 1, 2, 3] else BlendMode.NONE
                    if blend_mode == 0:
                        mat.blend_mode = BlendMode.NORMAL
                    elif blend_mode == 1:
                        mat.blend_mode = BlendMode.LIGHTEN
                    else:
                        mat.blend_mode = BlendMode.NONE  # fallback

                    mat.specular_enabled = bool(read_u16())
                    log_read("specular_enabled", mat.specular_enabled)
                    mat.alpha = read_f32()
                    log_read("alpha", mat.alpha)

                    glow_type = read_u16()
                    log_read("glow_type", glow_type)
                    mat.glow = GlowType(glow_type) if glow_type in [0, 1, 2, 3, 4, 5, 6] else None
                    mat.glow_color = read_vec3()
                    log_read("glow_color", mat.glow_color)

                    if alpha_test_enabled:
                        mat.alpha_test = alpha_ref
                    else:
                        mat.alpha_test = None

                    self.materials.append(mat)

                # --- Effects ---
                effect_count = read_u16()
                log_read("effect_count", effect_count)
                self.effects = []
                for _ in range(effect_count):
                    path = read_str()
                    log_read("effect_path", path)
                    self.effects.append(path)

                # --- Objects ---
                object_count = read_u16()
                log_read("object_count", object_count)
                for _ in range(object_count):
                    # Skip 4 * 3 = 12 bytes
                    f.seek(12, 1)
                    log_read("skip_12_bytes", "skipped")

                    obj = ZscObject()
                    mesh_count = read_u16()
                    log_read("mesh_count", mesh_count)

                    if mesh_count == 0:
                        self.objects.append(obj)
                        continue

                    for _ in range(mesh_count):
                        part = ZscObjectPart()
                        part.mesh_id = read_u16()
                        log_read("mesh_id", part.mesh_id)
                        part.material_id = read_u16()
                        log_read("material_id", part.material_id)

                        # Parse properties
                        while True:
                            #If there's a property id, we need to read that data based on the size in the next byte
                            prop_id = read_u8()
                            log_read("prop_id", prop_id)
                            if prop_id == 0:
                                break
                            size = read_u8()
                            log_read("prop_size", size)

                            if prop_id == 1:
                                part.position = read_vec3()
                                log_read("position", part.position)
                            elif prop_id == 2:
                                part.rotation = read_quat()
                                log_read("rotation", part.rotation)
                            elif prop_id == 3:
                                part.scale = read_vec3()
                                log_read("scale", part.scale)
                            elif prop_id == 4:
                                f.seek(4 * 4, 1)  # skip 4 floats
                                log_read("prop_id is 4, skipping 16", "")
                            elif prop_id == 5:
                                part.bone_index = read_u16()
                                log_read("bone_index", part.bone_index)
                            elif prop_id == 6:
                                part.dummy_index = read_u16()
                                log_read("dummy_index", part.dummy_index)
                            elif prop_id == 7:
                                parent_id = read_u16()
                                log_read("parent_id", parent_id)
                                if parent_id == 0:
                                    part.parent = None
                                else:
                                    part.parent = parent_id - 1  # 1-based → 0-based
                            elif prop_id == 29:
                                bits = read_u16()
                                log_read("collision_bits", bits)
                                shape = bits & 0b111
                                flags = bits >> 3
                                part.collision_shape = CollisionType(shape) if shape in [1, 2, 3, 4] else None
                                part.collision_flags = flags
                            elif prop_id == 30:
                                if size == 0:
                                    print("prop size is 0 and prop id is 30, continuing")
                                    continue
                                path = read_str_no_null(size)
                                log_read("animation_path", path)
                                part.animation_path = path
                            elif prop_id == 31 or prop_id == 32:
                                f.seek(2, 1)
                                log_read(f"skipped 2 bytes for prop_id 31 or 32", "skipped")
                            else:
                                raise ValueError(f"Invalid property_id: {prop_id}")

                        obj.parts.append(part)

                    # Effects
                    effect_count = read_u16()
                    log_read("effect_count", effect_count)
                    for _ in range(effect_count):
                        eff = ZscObjectEffect()
                        eff.effect_id = read_u16()
                        log_read("effect_id", eff.effect_id)
                        eff_type = read_u16()
                        log_read("effect_type", eff_type)
                        if eff_type == 0:
                            eff.effect_type = EffectType.NORMAL
                        elif eff_type == 1:
                            eff.effect_type = EffectType.DAYNIGHT
                        elif eff_type == 2:
                            eff.effect_type = EffectType.LIGHTCONTAINER
                        else:
                            eff.effect_type = EffectType.UNKNOWN

                        while True:
                            prop_id = read_u8()
                            log_read("effect_prop_id", prop_id)
                            if prop_id == 0:
                                break
                            size = read_u8()
                            log_read("effect_prop_size", size)

                            if prop_id == 1:
                                eff.position = read_vec3()
                                log_read("effect_position", eff.position)
                            elif prop_id == 2:
                                eff.rotation = read_quat()
                                log_read("effect_rotation", eff.rotation)
                            elif prop_id == 3:
                                eff.scale = read_vec3()
                                log_read("effect_scale", eff.scale)
                            elif prop_id == 7:
                                parent_id = read_u16()
                                log_read("effect_parent_id", parent_id)
                                if parent_id == 0:
                                    eff.parent = None
                                else:
                                    eff.parent = parent_id - 1  # 1-based → 0-based
                            else:
                                #raise ValueError(f"Invalid effect property_id: {prop_id}")
                                #skip ahead size for now ( BYTE[flag_size] data)
                                f.seek(size, 1)

                        obj.effects.append(eff)

                    # Skip 4 * 3 * 2 = 24 bytes
                    f.seek(24, 1)
                    log_read("skip_24_bytes", "skipped")

                    self.objects.append(obj)
                    print("finished object")
                print("finished object")
            print("finished reading file")


        except Exception as e:
            offset = f.tell() if 'f' in locals() else 0
            raise RuntimeError(f"Failed to load ZSC file '{filepath}' at offset {offset}: {e}")
