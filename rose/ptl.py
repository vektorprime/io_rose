"""PTL (ROSE Online Particle) file format parser.

Mirrors rose-file-readers/src/ptl.rs. A PTL file holds one or more
particle sequences. Each sequence describes emission (life, rate, loops,
radius, gravity), rendering (texture, atlas grid, align/billboard mode,
blend factors) and a list of keyframes that drive size, color, velocity,
texture index and rotation over each particle's life.

Keyframe type ids (u32):
    1 SizeXY, 2 Timer, 3 Red, 4 Green, 5 Blue, 6 Alpha, 7 ColourRGBA,
    8 VelocityX, 9 VelocityY, 10 VelocityZ, 11 VelocityXYZ,
    12 Texture, 13 Rotation
"""

import struct

from .utils import (
    Vector3,
    decode_string_with_fallback,
    read_f32,
    read_i32,
    read_u8,
    read_u32,
    read_vector3_f32,
    write_f32,
    write_i32,
    write_u8,
    write_u32,
)


def _split_text_raw(raw):
    if not raw:
        return ""
    nul = raw.find(b"\x00")
    data = raw[:nul] if nul != -1 else raw
    return decode_string_with_fallback(data)


def _read_u32_len_bytes(f):
    length = read_u32(f)
    data = f.read(length)
    if len(data) != length:
        raise ValueError(
            f"PTL: truncated string field (want {length} bytes, got {len(data)})"
        )
    return data


def _write_u32_len_bytes(f, raw, text):
    if raw is not None and _split_text_raw(raw) == (text or ""):
        write_u32(f, len(raw))
        f.write(raw)
        return
    data = (text or "").encode("utf-8") + b"\x00"
    write_u32(f, len(data))
    f.write(data)


def _read_range_f32(f):
    lo = read_f32(f)
    hi = read_f32(f)
    return (lo, hi)


def _write_range_f32(f, rng):
    write_f32(f, rng[0])
    write_f32(f, rng[1])


class PtlKeyframe:
    TYPE_NAMES = {
        1: "SizeXY", 2: "Timer", 3: "Red", 4: "Green", 5: "Blue",
        6: "Alpha", 7: "ColourRGBA", 8: "VelocityX", 9: "VelocityY",
        10: "VelocityZ", 11: "VelocityXYZ", 12: "Texture", 13: "Rotation",
    }

    def __init__(self):
        self.keyframe_type = 0
        self.start_min = 0.0
        self.start_max = 0.0
        self.fade = False
        # data stored as a flat float list; interpretation by type:
        # 1: [sx_min, sy_min, sx_max, sy_max]
        # 2/3/4/5/6/8/9/10/12/13: [min, max]
        # 7: [r,g,b,a min..., r,g,b,a max...] (8 floats)
        # 11: [x,y,z min..., x,y,z max...] (6 floats)
        self.values = []

    @property
    def type_name(self):
        return self.TYPE_NAMES.get(self.keyframe_type, f"Unknown{self.keyframe_type}")

    @property
    def start_time(self):
        return (self.start_min, self.start_max)

    def read(self, f):
        self.keyframe_type = read_u32(f)
        self.start_min = read_f32(f)
        self.start_max = read_f32(f)
        self.fade = read_u8(f) != 0
        t = self.keyframe_type
        if t == 1:
            self.values = [read_f32(f) for _ in range(4)]
        elif t == 7:
            self.values = [read_f32(f) for _ in range(8)]
        elif t == 11:
            self.values = [read_f32(f) for _ in range(6)]
        elif t in (2, 3, 4, 5, 6, 8, 9, 10, 12, 13):
            self.values = [read_f32(f) for _ in range(2)]
        else:
            raise ValueError(f"PTL: invalid keyframe type {t}")

    def write(self, f):
        write_u32(f, self.keyframe_type)
        write_f32(f, self.start_min)
        write_f32(f, self.start_max)
        write_u8(f, 1 if self.fade else 0)
        for v in self.values:
            write_f32(f, v)


class PtlSequence:
    # align_type: 0 full billboard, 1 none, 2 Y-axis (mirrors effect_loader.rs)
    # update_coords: 0 world, 1 local position, 2 local
    def __init__(self):
        self.name = ""
        self.name_raw = b""
        self.life = (0.0, 0.0)
        self.emit_rate = (0.0, 0.0)
        self.num_loops = 0
        self.spawn_dir_min = Vector3()
        self.spawn_dir_max = Vector3()
        self.emit_radius_min = Vector3()
        self.emit_radius_max = Vector3()
        self.gravity_min = Vector3()
        self.gravity_max = Vector3()
        self.texture_path = ""
        self.texture_path_raw = b""
        self.num_particles = 0
        self.align_type = 0
        self.update_coords = 0
        self.texture_atlas_cols = 0
        self.texture_atlas_rows = 0
        self.sprite_type = 0
        self.dst_blend_mode = 0
        self.src_blend_mode = 0
        self.blend_op = 0
        self.keyframes = []

    def read(self, f):
        self.name_raw = _read_u32_len_bytes(f)
        self.name = _split_text_raw(self.name_raw)
        self.life = _read_range_f32(f)
        self.emit_rate = _read_range_f32(f)
        self.num_loops = read_i32(f)
        self.spawn_dir_min = read_vector3_f32(f)
        self.spawn_dir_max = read_vector3_f32(f)
        self.emit_radius_min = read_vector3_f32(f)
        self.emit_radius_max = read_vector3_f32(f)
        self.gravity_min = read_vector3_f32(f)
        self.gravity_max = read_vector3_f32(f)
        self.texture_path_raw = _read_u32_len_bytes(f)
        self.texture_path = _split_text_raw(self.texture_path_raw)
        self.num_particles = read_i32(f)
        self.align_type = read_u32(f)
        self.update_coords = read_u32(f)
        self.texture_atlas_cols = read_u32(f)
        self.texture_atlas_rows = read_u32(f)
        self.sprite_type = read_u32(f)
        self.dst_blend_mode = read_u32(f)
        self.src_blend_mode = read_u32(f)
        self.blend_op = read_u32(f)
        num_keyframes = read_u32(f)
        if num_keyframes > 100000:
            raise ValueError(f"PTL: unreasonable keyframe count {num_keyframes}")
        self.keyframes = []
        for _ in range(num_keyframes):
            kf = PtlKeyframe()
            kf.read(f)
            self.keyframes.append(kf)

    def write(self, f):
        _write_u32_len_bytes(f, self.name_raw, self.name)
        _write_range_f32(f, self.life)
        _write_range_f32(f, self.emit_rate)
        write_i32(f, self.num_loops)
        for v in (self.spawn_dir_min, self.spawn_dir_max,
                  self.emit_radius_min, self.emit_radius_max,
                  self.gravity_min, self.gravity_max):
            write_f32(f, v.x)
            write_f32(f, v.y)
            write_f32(f, v.z)
        _write_u32_len_bytes(f, self.texture_path_raw, self.texture_path)
        write_i32(f, self.num_particles)
        write_u32(f, self.align_type)
        write_u32(f, self.update_coords)
        write_u32(f, self.texture_atlas_cols)
        write_u32(f, self.texture_atlas_rows)
        write_u32(f, self.sprite_type)
        write_u32(f, self.dst_blend_mode)
        write_u32(f, self.src_blend_mode)
        write_u32(f, self.blend_op)
        write_u32(f, len(self.keyframes))
        for kf in self.keyframes:
            kf.write(f)

    # -- convenience accessors for the Blender importer -------------------
    def _avg(self, rng):
        return (rng[0] + rng[1]) * 0.5

    @property
    def avg_life(self):
        return self._avg(self.life)

    @property
    def avg_emit_rate(self):
        return self._avg(self.emit_rate)

    def keyframes_of_type(self, type_id):
        return [kf for kf in self.keyframes if kf.keyframe_type == type_id]

    def avg_first_value(self, type_id, index=0, default=0.0):
        """Average of values[index] over keyframes of the given type.

        For ranged pairs (min, max) pass index 0 and 1 separately.
        """
        kfs = self.keyframes_of_type(type_id)
        if not kfs:
            return default
        vals = [kf.values[index] for kf in kfs if len(kf.values) > index]
        if not vals:
            return default
        return sum(vals) / len(vals)


class Ptl:
    """ROSE Online particle file (.PTL)."""

    def __init__(self, filepath=None, report_func=None):
        self.sequences = []
        self.report_func = report_func
        if filepath:
            with open(filepath, "rb") as f:
                self.read(f)

    def report(self, level, message):
        if self.report_func:
            self.report_func(level, message)

    def read(self, f):
        num_sequences = read_u32(f)
        if num_sequences > 10000:
            raise ValueError(f"PTL: unreasonable sequence count {num_sequences}")
        self.sequences = []
        for _ in range(num_sequences):
            seq = PtlSequence()
            seq.read(f)
            self.sequences.append(seq)
        # Rust's ptl.rs clamps max(start) >= min(start); mirror it so the
        # importer never sees an inverted range.
        for seq in self.sequences:
            for kf in seq.keyframes:
                if kf.start_max < kf.start_min:
                    kf.start_max = kf.start_min

    def write(self, f):
        write_u32(f, len(self.sequences))
        for seq in self.sequences:
            seq.write(f)

    def save(self, filepath):
        with open(filepath, "wb") as f:
            self.write(f)
