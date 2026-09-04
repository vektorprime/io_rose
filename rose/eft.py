"""EFT (ROSE Online Effect) file format parser.

Mirrors rose-file-readers/src/eft.rs. An EFT file is a container that
references particle systems (.PTL), meshes (.ZMS in EFFECTMESH),
transform animations (.ZMO in MOTION) and per-vertex mesh animations
(.ZMO in EFFECTMESH), plus an optional sound.

Binary layout (all little-endian):
- u32 header_skip_len + bytes (opaque name blob, usually empty)
- u32 use_sound_file, u32-len string sound_file, u32 sound_repeat_count
- u32 num_particles, then per particle entry:
    u32 skip_a_len + bytes, u32 skip_b_len + bytes, 4 bytes pad,
    u32-len string particle_file (.PTL),
    u32 use_animation_file, u32-len string animation_file (.ZMO),
    u32 animation_repeat_count, 4 bytes pad,
    f32 x,y,z position (centimeters), f32 pitch, yaw, roll (degrees),
    4 bytes pad, u32 start_delay (ms), u32 is_linked
- u32 num_meshes, then per mesh entry:
    u32 skip_a_len + bytes, u32 skip_b_len + bytes, 4 bytes pad,
    u32-len string mesh_file (.ZMS),
    u32-len string mesh_animation_file (.ZMO, per-vertex morph),
    u32-len string mesh_texture_file (.DDS),
    8xu32 blend flags (alpha_enabled, two_sided, alpha_test_enabled,
      depth_test_enabled, depth_write_enabled, src_blend, dst_blend, blend_op),
    u32 use_animation_file, u32-len string animation_file (.ZMO transform),
    u32 animation_repeat_count, 4 bytes pad,
    f32 x,y,z, f32 pitch, yaw, roll, 4 bytes pad,
    u32 start_delay, u32 repeat_count, u32 is_linked

Notes:
- Several u32 flag fields contain uninitialized 0xCD debug fill in
  shipped files (e.g. use_animation_file = 0xCDCDCD00). The effective
  path logic below matches the Rust client: a path is only used when
  its flag is non-zero (where a flag exists) AND the text is not empty
  and not "NULL".
- String fields on disk usually include a trailing NUL inside the
  counted length. Raw bytes are preserved per field so an unedited file
  saves back byte-identically.
"""

from .utils import (
    Vector3,
    decode_string_with_fallback,
    read_f32,
    read_u32,
    read_vector3_f32,
    write_f32,
    write_u32,
)


def _is_null_path(text):
    return not text or text == "NULL"


def _split_text_raw(raw):
    """Decode raw string bytes the way the Rust reader does (trim at NUL)."""
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
            f"EFT: truncated string field (want {length} bytes, got {len(data)})"
        )
    return data


def _write_u32_len_bytes(f, raw, text):
    """Write a u32-length string field.

    When the text still matches the original raw bytes, the raw bytes are
    written verbatim (lossless round-trip incl. trailing NUL). Otherwise the
    new text is encoded as UTF-8 with a trailing NUL.
    """
    if raw is not None and _split_text_raw(raw) == (text or ""):
        write_u32(f, len(raw))
        f.write(raw)
        return
    data = (text or "").encode("utf-8") + b"\x00"
    write_u32(f, len(data))
    f.write(data)


def _read_skip_blob(f):
    length = read_u32(f)
    data = f.read(length)
    if len(data) != length:
        raise ValueError(
            f"EFT: truncated skip blob (want {length} bytes, got {len(data)})"
        )
    return data


def _write_skip_blob(f, raw, text=None):
    if raw is not None and (text is None or _split_text_raw(raw) == text):
        write_u32(f, len(raw))
        f.write(raw)
        return
    data = (text or "").encode("utf-8") if text else b""
    write_u32(f, len(data))
    f.write(data)


def _read_pad(f):
    data = f.read(4)
    if len(data) != 4:
        raise ValueError("EFT: truncated padding")
    return data


class EftParticle:
    def __init__(self):
        self.skip_a = b""
        self.skip_b = b""
        self.pad1 = b"\x00\x00\x00\x00"
        self.particle_file = ""
        self.particle_file_raw = b""
        self.use_animation = 0
        self.animation_file = ""
        self.animation_file_raw = b""
        self.animation_repeat_count = 0
        self.pad2 = b"\x00\x00\x00\x00"
        self.position = Vector3()
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0
        self.pad3 = b"\x00\x00\x00\x00"
        self.start_delay = 0
        self.is_linked = False

    @property
    def skip_a_text(self):
        return _split_text_raw(self.skip_a)

    @property
    def skip_b_text(self):
        return _split_text_raw(self.skip_b)

    def particle_path(self):
        """Effective .PTL path ("" when unused). No flag gates this field."""
        if _is_null_path(self.particle_file):
            return ""
        return self.particle_file

    def animation_path(self):
        """Effective transform .ZMO path, mirroring the Rust client."""
        if self.use_animation == 0:
            return ""
        if _is_null_path(self.animation_file):
            return ""
        return self.animation_file

    def read(self, f):
        self.skip_a = _read_skip_blob(f)
        self.skip_b = _read_skip_blob(f)
        self.pad1 = _read_pad(f)
        self.particle_file_raw = _read_u32_len_bytes(f)
        self.particle_file = _split_text_raw(self.particle_file_raw)
        self.use_animation = read_u32(f)
        self.animation_file_raw = _read_u32_len_bytes(f)
        self.animation_file = _split_text_raw(self.animation_file_raw)
        self.animation_repeat_count = read_u32(f)
        self.pad2 = _read_pad(f)
        self.position = read_vector3_f32(f)
        self.pitch = read_f32(f)
        self.yaw = read_f32(f)
        self.roll = read_f32(f)
        self.pad3 = _read_pad(f)
        self.start_delay = read_u32(f)
        self.is_linked = read_u32(f) != 0

    def write(self, f):
        _write_skip_blob(f, self.skip_a)
        _write_skip_blob(f, self.skip_b)
        f.write(self.pad1)
        _write_u32_len_bytes(f, self.particle_file_raw, self.particle_file)
        write_u32(f, self.use_animation)
        _write_u32_len_bytes(f, self.animation_file_raw, self.animation_file)
        write_u32(f, self.animation_repeat_count)
        f.write(self.pad2)
        write_f32(f, self.position.x)
        write_f32(f, self.position.y)
        write_f32(f, self.position.z)
        write_f32(f, self.pitch)
        write_f32(f, self.yaw)
        write_f32(f, self.roll)
        f.write(self.pad3)
        write_u32(f, self.start_delay)
        write_u32(f, 1 if self.is_linked else 0)


class EftMesh:
    def __init__(self):
        self.skip_a = b""
        self.skip_b = b""
        self.pad1 = b"\x00\x00\x00\x00"
        self.mesh_file = ""
        self.mesh_file_raw = b""
        self.mesh_animation_file = ""
        self.mesh_animation_file_raw = b""
        self.mesh_texture_file = ""
        self.mesh_texture_file_raw = b""
        self.alpha_enabled = False
        self.two_sided = False
        self.alpha_test_enabled = False
        self.depth_test_enabled = False
        self.depth_write_enabled = False
        self.src_blend_factor = 0
        self.dst_blend_factor = 0
        self.blend_op = 0
        self.use_animation = 0
        self.animation_file = ""
        self.animation_file_raw = b""
        self.animation_repeat_count = 0
        self.pad2 = b"\x00\x00\x00\x00"
        self.position = Vector3()
        self.pitch = 0.0
        self.yaw = 0.0
        self.roll = 0.0
        self.pad3 = b"\x00\x00\x00\x00"
        self.start_delay = 0
        self.repeat_count = 0
        self.is_linked = False

    @property
    def skip_a_text(self):
        return _split_text_raw(self.skip_a)

    @property
    def skip_b_text(self):
        return _split_text_raw(self.skip_b)

    def mesh_animation_path(self):
        """Effective per-vertex .ZMO path (no flag field, NULL-gated only)."""
        if _is_null_path(self.mesh_animation_file):
            return ""
        return self.mesh_animation_file

    def texture_path(self):
        if _is_null_path(self.mesh_texture_file):
            return ""
        return self.mesh_texture_file

    def animation_path(self):
        if self.use_animation == 0:
            return ""
        if _is_null_path(self.animation_file):
            return ""
        return self.animation_file

    def read(self, f):
        self.skip_a = _read_skip_blob(f)
        self.skip_b = _read_skip_blob(f)
        self.pad1 = _read_pad(f)
        self.mesh_file_raw = _read_u32_len_bytes(f)
        self.mesh_file = _split_text_raw(self.mesh_file_raw)
        self.mesh_animation_file_raw = _read_u32_len_bytes(f)
        self.mesh_animation_file = _split_text_raw(self.mesh_animation_file_raw)
        self.mesh_texture_file_raw = _read_u32_len_bytes(f)
        self.mesh_texture_file = _split_text_raw(self.mesh_texture_file_raw)
        self.alpha_enabled = read_u32(f) != 0
        self.two_sided = read_u32(f) != 0
        self.alpha_test_enabled = read_u32(f) != 0
        self.depth_test_enabled = read_u32(f) != 0
        self.depth_write_enabled = read_u32(f) != 0
        self.src_blend_factor = read_u32(f)
        self.dst_blend_factor = read_u32(f)
        self.blend_op = read_u32(f)
        self.use_animation = read_u32(f)
        self.animation_file_raw = _read_u32_len_bytes(f)
        self.animation_file = _split_text_raw(self.animation_file_raw)
        self.animation_repeat_count = read_u32(f)
        self.pad2 = _read_pad(f)
        self.position = read_vector3_f32(f)
        self.pitch = read_f32(f)
        self.yaw = read_f32(f)
        self.roll = read_f32(f)
        self.pad3 = _read_pad(f)
        self.start_delay = read_u32(f)
        self.repeat_count = read_u32(f)
        self.is_linked = read_u32(f) != 0

    def write(self, f):
        _write_skip_blob(f, self.skip_a)
        _write_skip_blob(f, self.skip_b)
        f.write(self.pad1)
        _write_u32_len_bytes(f, self.mesh_file_raw, self.mesh_file)
        _write_u32_len_bytes(
            f, self.mesh_animation_file_raw, self.mesh_animation_file
        )
        _write_u32_len_bytes(f, self.mesh_texture_file_raw, self.mesh_texture_file)
        write_u32(f, 1 if self.alpha_enabled else 0)
        write_u32(f, 1 if self.two_sided else 0)
        write_u32(f, 1 if self.alpha_test_enabled else 0)
        write_u32(f, 1 if self.depth_test_enabled else 0)
        write_u32(f, 1 if self.depth_write_enabled else 0)
        write_u32(f, self.src_blend_factor)
        write_u32(f, self.dst_blend_factor)
        write_u32(f, self.blend_op)
        write_u32(f, self.use_animation)
        _write_u32_len_bytes(f, self.animation_file_raw, self.animation_file)
        write_u32(f, self.animation_repeat_count)
        f.write(self.pad2)
        write_f32(f, self.position.x)
        write_f32(f, self.position.y)
        write_f32(f, self.position.z)
        write_f32(f, self.pitch)
        write_f32(f, self.yaw)
        write_f32(f, self.roll)
        f.write(self.pad3)
        write_u32(f, self.start_delay)
        write_u32(f, self.repeat_count)
        write_u32(f, 1 if self.is_linked else 0)


class Eft:
    """ROSE Online effect file (.EFT)."""

    def __init__(self, filepath=None, report_func=None):
        self.header_skip = b""
        self.use_sound = 0
        self.sound_file = ""
        self.sound_file_raw = b""
        self.sound_repeat_count = 0
        self.particles = []
        self.meshes = []
        self.report_func = report_func
        if filepath:
            with open(filepath, "rb") as f:
                self.read(f)

    def report(self, level, message):
        if self.report_func:
            self.report_func(level, message)

    def sound_path(self):
        if self.use_sound == 0:
            return ""
        if _is_null_path(self.sound_file):
            return ""
        return self.sound_file

    def read(self, f):
        self.header_skip = _read_skip_blob(f)
        self.use_sound = read_u32(f)
        self.sound_file_raw = _read_u32_len_bytes(f)
        self.sound_file = _split_text_raw(self.sound_file_raw)
        self.sound_repeat_count = read_u32(f)
        num_particles = read_u32(f)
        if num_particles > 10000:
            raise ValueError(f"EFT: unreasonable particle count {num_particles}")
        self.particles = []
        for _ in range(num_particles):
            entry = EftParticle()
            entry.read(f)
            self.particles.append(entry)
        num_meshes = read_u32(f)
        if num_meshes > 10000:
            raise ValueError(f"EFT: unreasonable mesh count {num_meshes}")
        self.meshes = []
        for _ in range(num_meshes):
            entry = EftMesh()
            entry.read(f)
            self.meshes.append(entry)

    def write(self, f):
        _write_skip_blob(f, self.header_skip)
        write_u32(f, self.use_sound)
        _write_u32_len_bytes(f, self.sound_file_raw, self.sound_file)
        write_u32(f, self.sound_repeat_count)
        write_u32(f, len(self.particles))
        for entry in self.particles:
            entry.write(f)
        write_u32(f, len(self.meshes))
        for entry in self.meshes:
            entry.write(f)

    def save(self, filepath):
        with open(filepath, "wb") as f:
            self.write(f)
