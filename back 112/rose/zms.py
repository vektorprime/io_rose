from enum import IntEnum
from .utils import *

class VertexFlags(IntEnum):
    POSITION = 2      # (1 << 1)
    NORMAL = 4        # (1 << 2)
    COLOR = 8         # (1 << 3)
    BONE_WEIGHT = 16  # (1 << 4) - FIXED ORDER
    BONE_INDEX = 32   # (1 << 5) - FIXED ORDER
    TANGENT = 64      # (1 << 6)
    UV1 = 128         # (1 << 7)
    UV2 = 256         # (1 << 8)
    UV3 = 512         # (1 << 9)
    UV4 = 1024        # (1 << 10)

class Vertex:
    def __init__(self):
        self.position = Vector3()
        self.normal = Vector3()
        self.color = Color4()
        self.bone_weights = [0.0, 0.0, 0.0, 0.0]
        self.bone_indices = [0, 0, 0, 0]
        self.tangent = Vector3()
        self.uv1 = Vector2()
        self.uv2 = Vector2()
        self.uv3 = Vector2()
        self.uv4 = Vector2()

class ZMS:
    def __init__(self, filepath=None):
        self.identifier = ""
        self.version = 0
        self.flags = 0
        self.bounding_box_min = Vector3(0, 0, 0)
        self.bounding_box_max = Vector3(0, 0, 0)
        self.vertices = []
        self.indices = []
        self.bones = []
        self.materials = []
        self.strips = []
        self.pool = 0

        if filepath:
            with open(filepath, "rb") as f:
                self.read(f)

    def positions_enabled(self):
        return (self.flags & VertexFlags.POSITION) != 0

    def normals_enabled(self):
        return (self.flags & VertexFlags.NORMAL) != 0

    def colors_enabled(self):
        return (self.flags & VertexFlags.COLOR) != 0

    def bones_enabled(self):
        bone_weights = (self.flags & VertexFlags.BONE_WEIGHT) != 0
        bone_indices = (self.flags & VertexFlags.BONE_INDEX) != 0
        return (bone_weights and bone_indices)

    def tangents_enabled(self):
        return (self.flags & VertexFlags.TANGENT) != 0

    def uv1_enabled(self):
        return (self.flags & VertexFlags.UV1) != 0

    def uv2_enabled(self):
        return (self.flags & VertexFlags.UV2) != 0

    def uv3_enabled(self):
        return (self.flags & VertexFlags.UV3) != 0

    def uv4_enabled(self):
        return (self.flags & VertexFlags.UV4) != 0

    def read(self, f):
        self.identifier = read_str(f)
        
        # Determine version from identifier
        if self.identifier == "ZMS0005":
            self.version = 5
            self._read_version6(f, 5)
        elif self.identifier == "ZMS0006":
            self.version = 6
            self._read_version6(f, 6)
        elif self.identifier == "ZMS0007":
            self.version = 7
            self._read_version8(f, 7)
        elif self.identifier == "ZMS0008":
            self.version = 8
            self._read_version8(f, 8)
        else:
            raise ValueError(f"Unsupported ZMS version: {self.identifier}")

    def _read_version6(self, f, version):
        """Read ZMS version 5 or 6 format"""
        self.flags = read_u32(f)
        self.bounding_box_min = read_vector3_f32(f)
        self.bounding_box_max = read_vector3_f32(f)

        # Read bone lookup table
        bone_count = read_u32(f)
        bone_table = []
        for i in range(bone_count):
            _ = read_u32(f)  # Skip first u32 (dummy index)
            bone_table.append(read_u32(f))

        vert_count = read_u32(f)
        for i in range(vert_count):
            self.vertices.append(Vertex())

        # Read positions (scaled by 100.0 in version 5/6)
        if self.positions_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                pos = read_vector3_f32(f)
                # Divide by 100.0 to unscale
                self.vertices[i].position = Vector3(pos.x / 100.0, pos.y / 100.0, pos.z / 100.0)

        # Read normals
        if self.normals_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].normal = read_vector3_f32(f)

        # Read colors
        if self.colors_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].color = read_color4(f)

        # Read bone weights and indices
        if self.bones_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].bone_weights = read_list_f32(f, 4)
                bone_indices_raw = read_list_u32(f, 4)
                # Map through bone table
                self.vertices[i].bone_indices = [
                    bone_table[idx] if idx < len(bone_table) else 0
                    for idx in bone_indices_raw
                ]

        # Read tangents
        if self.tangents_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].tangent = read_vector3_f32(f)

        # Read UV coordinates
        if self.uv1_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].uv1 = read_vector2_f32(f)

        if self.uv2_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].uv2 = read_vector2_f32(f)

        if self.uv3_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].uv3 = read_vector2_f32(f)

        if self.uv4_enabled():
            for i in range(vert_count):
                _ = read_u32(f)  # vertex_id
                self.vertices[i].uv4 = read_vector2_f32(f)

        # Read triangle indices
        triangle_count = read_u32(f)
        for i in range(triangle_count):
            _ = read_u32(f)  # triangle_id
            idx1 = read_u32(f)
            idx2 = read_u32(f)
            idx3 = read_u32(f)
            self.indices.append(Vector3(idx1, idx2, idx3))

        # Read materials (version 6 only)
        if version >= 6:
            material_count = read_u32(f)
            for i in range(material_count):
                _ = read_u32(f)  # index
                self.materials.append(read_u32(f))

        # Populate bones list from bone_table
        self.bones = bone_table

    def _read_version8(self, f, version):
        """Read ZMS version 7 or 8 format"""
        self.flags = read_u32(f)
        self.bounding_box_min = read_vector3_f32(f)
        self.bounding_box_max = read_vector3_f32(f)

        # Read bones (direct list, no lookup table)
        bone_count = read_u16(f)
        for i in range(bone_count):
            self.bones.append(read_u16(f))

        vert_count = read_u16(f)
        for i in range(vert_count):
            self.vertices.append(Vertex())

        # Read vertex data (no vertex_id prefix in version 7/8)
        if self.positions_enabled():
            for i in range(vert_count):
                self.vertices[i].position = read_vector3_f32(f)

        if self.normals_enabled():
            for i in range(vert_count):
                self.vertices[i].normal = read_vector3_f32(f)

        if self.colors_enabled():
            for i in range(vert_count):
                self.vertices[i].color = read_color4(f)

        if self.bones_enabled():
            for i in range(vert_count):
                self.vertices[i].bone_weights = read_list_f32(f, 4)
                bone_indices_raw = read_list_u16(f, 4)
                # Map through bones list - indices are into bones array
                self.vertices[i].bone_indices = [
                    self.bones[idx] if idx < len(self.bones) else 0
                    for idx in bone_indices_raw
                ]

        if self.tangents_enabled():
            for i in range(vert_count):
                self.vertices[i].tangent = read_vector3_f32(f)

        if self.uv1_enabled():
            for i in range(vert_count):
                self.vertices[i].uv1 = read_vector2_f32(f)

        if self.uv2_enabled():
            for i in range(vert_count):
                self.vertices[i].uv2 = read_vector2_f32(f)

        if self.uv3_enabled():
            for i in range(vert_count):
                self.vertices[i].uv3 = read_vector2_f32(f)

        if self.uv4_enabled():
            for i in range(vert_count):
                self.vertices[i].uv4 = read_vector2_f32(f)

        # Read indices - flat array for version 7/8
        index_count = read_u16(f)
        indices_flat = read_list_u16(f, index_count * 3)
        for i in range(0, len(indices_flat), 3):
            self.indices.append(Vector3(indices_flat[i], indices_flat[i+1], indices_flat[i+2]))

        # Read materials
        material_count = read_u16(f)
        self.materials = read_list_u16(f, material_count)

        # Read strips
        strip_count = read_u16(f)
        self.strips = read_list_u16(f, strip_count)

        # Read pool (version 8 only)
        if version >= 8:
            self.pool = read_u16(f)