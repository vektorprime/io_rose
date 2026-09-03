from .utils import *


class Bone:
    def __init__(self):
        self.parent_id = -1
        self.name = ""
        self.position = Vector3(0.0, 0.0, 0.0)
        self.rotation = Quat(0.0, 0.0, 0.0, 0.0)


class Dummy:
    def __init__(self):
        self.name = ""
        self.parent_id = -1
        self.position = Vector3(0.0, 0.0, 0.0)
        self.rotation = Quat(0.0, 0.0, 0.0, 0.0)


class ZMD:
    def __init__(self, filepath=None, report_func=None):
        self.bones = []
        self.dummies = []
        self.version = 0
        self.identifier = ""
        self.report_func = report_func  # Optional callback for reporting

        if filepath:
            with open(filepath, "rb") as f:
                self.read(f)

    def report(self, level, message):
        """Helper method to report messages either via callback or print"""
        if self.report_func:
            self.report_func(level, message)
        else:
            # Fallback to print if no report function provided
            print(f"[{level}] {message}")

    def read(self, f):
        # Read 7-character format identifier (e.g., "ZMD0002", "ZMD0003").
        # Only versions 2 and 3 exist; anything else (including corrupt
        # headers) is rejected instead of being misparsed as v2.
        # Matches rose-file-readers/src/zmd.rs read().
        identifier = read_fstr(f, 7)
        self.identifier = identifier

        if identifier == "ZMD0002":
            self.version = 2
        elif identifier == "ZMD0003":
            self.version = 3
        else:
            raise ValueError(f"Invalid ZMD magic header: {identifier!r} "
                             f"(expected 'ZMD0002' or 'ZMD0003')")

        # Read bone data
        bone_count = read_u32(f)

        for i in range(bone_count):
            bone = Bone()
            bone.parent_id = read_i32(f)
            bone.name = read_str(f)
            bone.position = read_vector3_f32(f)
            bone.rotation = read_quat_wxyz(f)

            # Apply scaling to convert from cm to m
            bone.position = bone.position.scalar(0.01)

            # Handle root bone identification per Rust reference:
            # Root bones are identified by parent == bone_index (self-reference)
            # Convert self-reference to -1 for consistency
            if bone.parent_id == i:
                bone.parent_id = -1

            self.bones.append(bone)

        # Read dummy objects. Well-formed files always store a dummy count
        # (possibly zero) after the bones; only a file that ends exactly at
        # this boundary is treated as having no dummies. Any other read
        # failure propagates instead of yielding a half-filled skeleton.
        current_pos = f.tell()
        f.seek(0, 2)  # Seek to end
        file_size = f.tell()
        f.seek(current_pos)  # Seek back

        if current_pos >= file_size:
            return

        dummy_count = read_u32(f)

        for i in range(dummy_count):
            dummy = Dummy()
            dummy.name = read_str(f)
            dummy.parent_id = read_i32(f)
            dummy.position = read_vector3_f32(f)

            # ZMD version 3+ has rotation data for dummies
            # ZMD version 2 has NO rotation data - use identity quaternion
            if self.version >= 3:
                dummy.rotation = read_quat_wxyz(f)
            else:
                dummy.rotation = Quat(0.0, 0.0, 0.0, 1.0)  # Identity

            # Apply scaling
            dummy.position = dummy.position.scalar(0.01)

            self.dummies.append(dummy)