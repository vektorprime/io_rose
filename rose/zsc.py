# zsc.py - Rose Online ZSC Scene File Parser (Rust-Exact Match)
#
# Pure-Python module (no Blender dependencies) so it can be unit-tested
# outside Blender. Matches rose-file-readers/src/zsc.rs: unknown values
# raise instead of being coerced, so corrupt files fail loudly.
from .utils import *
from .utils import decode_string_with_fallback

from enum import IntEnum
from typing import List, Optional, NamedTuple, Dict, Any
import os
import struct

# Debug logging control - disable by default for performance
ZSC_DEBUG_LOG = False

def log_read(name, val, offset):
    """Log ZSC read operations - only when debug enabled"""
    if ZSC_DEBUG_LOG:
        print(f"[{offset:08X}] {name} -> {val}")

# === ENUMS ===

class BlendMode(IntEnum):
    NONE = 0
    CUSTOM = 1
    NORMAL = 2
    LIGHTEN = 3


class GlowType(IntEnum):
    """On-disk glow values (matches zsc.rs ZscMaterialGlow).

    0/1 mean "no glow" (stored as None); 2-5 carry the glow color.
    """
    NONE = 0
    NOTSET = 1
    SIMPLE = 2
    LIGHT = 3
    TEXTURELIGHT = 4
    ALPHA = 5


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
        self.raw: bytes = None          # original file bytes (for lossless save)
        self._section_offsets = None    # (meshes, materials, effects, objects) byte ranges
        self.load(filepath)

    def __repr__(self):
        return f"Zsc(file='{self.filepath}', meshes={len(self.meshes)}, materials={len(self.materials)}, objects={len(self.objects)})"

    def load(self, filepath: str):
        """Load and parse the ZSC file with logging."""
        zsc_size = os.path.getsize(filepath)
        try:
            with open(filepath, "rb") as f:
                self.raw = f.read()
                f.seek(0)

                section_ranges = {}
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
                    # UTF-8 first, EUC-KR fallback (matches reader.rs
                    # decode_string); chr()-per-byte mojibake otherwise.
                    return decode_string_with_fallback(b''.join(data))

                def read_str_no_null(size):
                    # Fixed-length path: same decoding, trimmed at the
                    # first null like reader.rs.
                    return decode_string_with_fallback(
                        f.read(size)).split('\x00')[0]
                
                def read_vec3():
                    return Vec3(*[read_f32() for _ in range(3)])

                def read_quat():
                    w = read_f32()
                    x = read_f32()
                    y = read_f32()
                    z = read_f32()
                    return Vec4(x, y, z, w)

                # --- Meshes ---
                mesh_count = read_u16()
                self.meshes = []
                self.materials = []
                self.effects = []
                self.objects = []
                for _ in range(mesh_count):
                    path = read_str()
                    self.meshes.append(path)
                section_ranges['meshes'] = (0, f.tell())

                # --- Materials ---
                material_count = read_u16()
                for _ in range(material_count):
                    mat = ZscMaterial()
                    mat.path = read_str()

                    mat.is_skin = bool(read_u16())
                    mat.alpha_enabled = bool(read_u16())
                    mat.two_sided = bool(read_u16())
                    alpha_test_enabled = bool(read_u16())
                    alpha_ref = read_u16() / 256.0
                    mat.z_test_enabled = bool(read_u16())
                    mat.z_write_enabled = bool(read_u16())

                    # File values are 0=Normal, 1=Lighten (zsc.rs
                    # ZscMaterialBlend); anything else is corrupt, not NONE.
                    blend_mode = read_u16()
                    if blend_mode == 0:
                        mat.blend_mode = BlendMode.NORMAL
                    elif blend_mode == 1:
                        mat.blend_mode = BlendMode.LIGHTEN
                    else:
                        raise ValueError(f"Invalid ZscMaterialBlend {blend_mode}")

                    mat.specular_enabled = bool(read_u16())
                    mat.alpha = read_f32()

                    # 0|1 = no glow, 2-5 carry glow_color (zsc.rs
                    # ZscMaterialGlow); 6+ is corrupt.
                    glow_type = read_u16()
                    if glow_type in (0, 1):
                        mat.glow = None
                    elif glow_type in (2, 3, 4, 5):
                        mat.glow = GlowType(glow_type)
                    else:
                        raise ValueError(f"Invalid ZscMaterialGlow {glow_type}")
                    mat.glow_color = read_vec3()

                    if alpha_test_enabled:
                        mat.alpha_test = alpha_ref
                    else:
                        mat.alpha_test = None

                    self.materials.append(mat)
                section_ranges['materials'] = (section_ranges['meshes'][1], f.tell())

                # --- Effects ---
                effect_count = read_u16()
                self.effects = []
                for _ in range(effect_count):
                    path = read_str()
                    self.effects.append(path)
                section_ranges['effects'] = (section_ranges['materials'][1], f.tell())

                # --- Objects ---
                object_count = read_u16()
                for _ in range(object_count):
                    # Skip 4 * 3 = 12 bytes
                    f.seek(12, 1)

                    obj = ZscObject()
                    mesh_count = read_u16()

                    if mesh_count == 0:
                        self.objects.append(obj)
                        continue

                    for _ in range(mesh_count):
                        part = ZscObjectPart()
                        part.mesh_id = read_u16()
                        part.material_id = read_u16()

                        # Parse properties
                        while True:
                            prop_id = read_u8()
                            if prop_id == 0:
                                break
                            size = read_u8()

                            if prop_id == 1:
                                part.position = read_vec3()
                            elif prop_id == 2:
                                part.rotation = read_quat()
                            elif prop_id == 3:
                                part.scale = read_vec3()
                            elif prop_id == 4:
                                f.seek(4 * 4, 1)  # skip 4 floats
                            elif prop_id == 5:
                                part.bone_index = read_u16()
                            elif prop_id == 6:
                                part.dummy_index = read_u16()
                            elif prop_id == 7:
                                parent_id = read_u16()
                                if parent_id == 0:
                                    part.parent = None
                                else:
                                    part.parent = parent_id - 1  # 1-based → 0-based
                            elif 8 <= prop_id <= 28:
                                # Reserved properties: skip (zsc.rs).
                                f.seek(size, 1)
                            elif prop_id == 29:
                                bits = read_u16()
                                shape = bits & 0b111
                                if shape == 0:
                                    part.collision_shape = None
                                elif shape in (1, 2, 3, 4):
                                    part.collision_shape = CollisionType(shape)
                                else:
                                    raise ValueError(
                                        f"Invalid ZscCollisionShape {shape}")
                                # Upper byte must be zero (zsc.rs from_bits
                                # only accepts flag bits 3-7).
                                if bits & 0xFF00:
                                    raise ValueError(
                                        f"Invalid ZscCollisionFlags {bits:#x}")
                                # Stored unshifted (bits 3-7 -> 0-4); the
                                # writer shifts back (see _write_part).
                                part.collision_flags = (bits >> 3) & 0x1F
                            elif prop_id == 30:
                                if size == 0:
                                    continue
                                path = read_str_no_null(size)
                                part.animation_path = path
                            elif prop_id == 31 or prop_id == 32:
                                f.seek(2, 1)
                            else:
                                raise ValueError(f"Invalid property_id: {prop_id}")

                        obj.parts.append(part)

                    # Effects
                    effect_count = read_u16()
                    for _ in range(effect_count):
                        eff = ZscObjectEffect()
                        eff.effect_id = read_u16()
                        eff_type = read_u16()
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
                            if prop_id == 0:
                                break
                            size = read_u8()

                            if prop_id == 1:
                                eff.position = read_vec3()
                            elif prop_id == 2:
                                eff.rotation = read_quat()
                            elif prop_id == 3:
                                eff.scale = read_vec3()
                            elif prop_id == 7:
                                parent_id = read_u16()
                                if parent_id == 0:
                                    eff.parent = None
                                else:
                                    eff.parent = parent_id - 1  # 1-based → 0-based
                            else:
                                # The reference rejects unknown effect
                                # properties instead of skipping them, so a
                                # corrupt/newer file fails loudly (zsc.rs).
                                raise ValueError(
                                    f"Invalid ZscObjectEffect property_id: {prop_id}")

                        obj.effects.append(eff)

                    # Skip 4 * 3 * 2 = 24 bytes
                    f.seek(24, 1)

                    self.objects.append(obj)

                section_ranges['objects'] = (section_ranges['effects'][1], f.tell())
                self._section_offsets = section_ranges


        except Exception as e:
            # The with-block closes f before this handler runs, so tell()
            # would raise "I/O operation on closed file" and mask the real
            # error (all strict-validation failures surfaced as that).
            try:
                offset = f.tell()
            except (ValueError, NameError):
                offset = 0
            raise RuntimeError(
                f"Failed to load ZSC file '{filepath}' at offset {offset}: {e}") from e

    # ------------------------------------------------------------------
    # Serialization
    #
    # The existing sections (meshes / materials / effects / objects) are
    # re-emitted byte-for-byte from the original file, so a save never
    # corrupts data the parser couldn't round-trip. Appended meshes,
    # materials and objects are serialized after the originals - because
    # mesh/material/object ids are positional indices, append-only
    # insertion keeps every existing id valid.
    # ------------------------------------------------------------------

    def append_mesh(self, path: str):
        self.meshes.append(path)

    def append_material(self, mat: ZscMaterial):
        self.materials.append(mat)

    def append_object(self, obj: ZscObject):
        self.objects.append(obj)

    @staticmethod
    def _write_cstr(buf, s):
        buf.extend(s.encode('utf-8'))
        buf.append(0)

    @staticmethod
    def _write_vec3(buf, v):
        buf.extend(struct.pack('<fff', v[0], v[1], v[2]))

    @staticmethod
    def _write_quat(buf, q):
        buf.extend(struct.pack('<ffff', q[3], q[0], q[1], q[2]))  # WXYZ

    @staticmethod
    def _write_prop_vec3(buf, prop_id, v):
        buf.append(prop_id)
        buf.append(12)
        Zsc._write_vec3(buf, v)

    @staticmethod
    def _write_prop_quat(buf, v):
        buf.append(2)
        buf.append(16)
        Zsc._write_quat(buf, v)

    @staticmethod
    def _write_prop_u16(buf, prop_id, v):
        buf.append(prop_id)
        buf.append(2)
        buf.extend(struct.pack('<H', v))

    def _write_part(self, buf, part: ZscObjectPart):
        buf.extend(struct.pack('<HH', part.mesh_id, part.material_id))
        Zsc._write_prop_vec3(buf, 1, part.position)
        Zsc._write_prop_quat(buf, part.rotation)
        Zsc._write_prop_vec3(buf, 3, part.scale)
        if part.bone_index is not None:
            Zsc._write_prop_u16(buf, 5, part.bone_index)
        if part.dummy_index is not None:
            Zsc._write_prop_u16(buf, 6, part.dummy_index)
        if part.parent is not None:
            Zsc._write_prop_u16(buf, 7, part.parent + 1)
        if part.collision_shape is not None:
            # collision_flags is stored unshifted (see load); the on-disk
            # layout is shape bits 0-2 | flag bits 3-7. Omitting the shift
            # corrupted every saved part (e.g. NOT_MOVEABLE -> SPHERE).
            bits = int(part.collision_shape) | ((part.collision_flags & 0x1F) << 3)
            Zsc._write_prop_u16(buf, 29, bits)
        if part.animation_path:
            data = part.animation_path.encode('utf-8')
            buf.append(30)
            buf.append(len(data))
            buf.extend(data)
        buf.append(0)  # end of properties

    def _write_effect(self, buf, eff: ZscObjectEffect):
        buf.extend(struct.pack('<HH', eff.effect_id, int(eff.effect_type)))
        Zsc._write_prop_vec3(buf, 1, eff.position)
        Zsc._write_prop_quat(buf, eff.rotation)
        Zsc._write_prop_vec3(buf, 3, eff.scale)
        if eff.parent is not None:
            Zsc._write_prop_u16(buf, 7, eff.parent + 1)
        buf.append(0)

    def _write_object(self, buf, obj: ZscObject):
        buf.extend(b'\x00' * 12)  # reserved (name buffer, skipped on read)
        buf.extend(struct.pack('<H', len(obj.parts)))
        if len(obj.parts) == 0:
            return
        for part in obj.parts:
            self._write_part(buf, part)
        buf.extend(struct.pack('<H', len(obj.effects)))
        for eff in obj.effects:
            self._write_effect(buf, eff)
        buf.extend(b'\x00' * 24)  # reserved (skipped on read)

    def _write_new_mesh_section(self, buf):
        for path in self.meshes[self._original_count('meshes'):]:
            Zsc._write_cstr(buf, path)

    def _write_new_material_section(self, buf):
        # On-disk order is 9xu16, f32 alpha, u16 glow, vec3 glow_color
        # (see load). The previous writer emitted 11xu16 (glow + padding
        # before alpha), producing files no correct parser could read.
        for mat in self.materials[self._original_count('materials'):]:
            Zsc._write_cstr(buf, mat.path)
            buf.extend(struct.pack('<HHHHHHHHH',
                                    1 if mat.is_skin else 0,
                                    1 if mat.alpha_enabled else 0,
                                    1 if mat.two_sided else 0,
                                    1 if mat.alpha_test is not None else 0,
                                    int((mat.alpha_test or 0.0) * 256.0),
                                    1 if mat.z_test_enabled else 0,
                                    1 if mat.z_write_enabled else 0,
                                    1 if mat.blend_mode == BlendMode.LIGHTEN else 0,
                                    1 if mat.specular_enabled else 0))
            buf.extend(struct.pack('<f', mat.alpha))
            buf.extend(struct.pack('<H',
                                    int(mat.glow) if mat.glow is not None else 0))
            Zsc._write_vec3(buf, mat.glow_color)

    def _write_new_effect_section(self, buf):
        for path in self.effects[self._original_count('effects'):]:
            Zsc._write_cstr(buf, path)

    def _write_new_object_section(self, buf):
        for obj in self.objects[self._original_count('objects'):]:
            self._write_object(buf, obj)

    def _original_count(self, section):
        """Number of items in a section that came from the original file."""
        if not self._section_offsets or not self.raw:
            return 0
        if section == 'meshes':
            return len(self.meshes) - self._appended('meshes')
        if section == 'materials':
            return len(self.materials) - self._appended('materials')
        if section == 'effects':
            return len(self.effects) - self._appended('effects')
        if section == 'objects':
            return len(self.objects) - self._appended('objects')
        return 0

    def _appended(self, section):
        start, end = self._section_offsets[section]
        raw_count_field = self.raw[start:start + 2]
        if len(raw_count_field) == 2:
            count = int.from_bytes(raw_count_field, 'little')
            current = {'meshes': len(self.meshes), 'materials': len(self.materials),
                       'effects': len(self.effects), 'objects': len(self.objects)}[section]
            return max(0, current - count)
        return 0

    def save(self, filepath: str):
        """Write the ZSC file: original sections re-emitted byte-for-byte,
        appended items serialized after them."""
        if not self.raw or not self._section_offsets:
            raise RuntimeError("Zsc was not loaded from a file; cannot save losslessly")

        m0, m1 = self._section_offsets['meshes']
        ma0, ma1 = self._section_offsets['materials']
        e0, e1 = self._section_offsets['effects']
        o0, o1 = self._section_offsets['objects']

        with open(filepath, "wb") as f:
            f.write(struct.pack('<H', len(self.meshes)))
            f.write(self.raw[m0 + 2:m1])
            extra = bytearray()
            self._write_new_mesh_section(extra)
            f.write(extra)

            f.write(struct.pack('<H', len(self.materials)))
            f.write(self.raw[ma0 + 2:ma1])
            extra = bytearray()
            self._write_new_material_section(extra)
            f.write(extra)

            f.write(struct.pack('<H', len(self.effects)))
            f.write(self.raw[e0 + 2:e1])
            extra = bytearray()
            self._write_new_effect_section(extra)
            f.write(extra)

            f.write(struct.pack('<H', len(self.objects)))
            f.write(self.raw[o0 + 2:o1])
            extra = bytearray()
            self._write_new_object_section(extra)
            f.write(extra)

        # Re-parse the file so raw bytes and section offsets stay in sync
        # for subsequent saves.
        self.load(filepath)
