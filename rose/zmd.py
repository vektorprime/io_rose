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
    def __init__(self, filepath=None):
        self.bones = []
        self.dummies = []

        if filepath:
            with open(filepath, "rb") as f:
                self.read(f)

    def read(self, f):
        # Read 7-character format identifier
        identifier = read_fstr(f, 7)
        print(f"ZMD Identifier: {identifier}")
        
        # Read bone data
        bone_count = read_u32(f)
        print(f"Bone count: {bone_count}")

        for i in range(bone_count):
            bone = Bone()
            bone.parent_id = read_i32(f)
            bone.name = read_str(f)
            bone.position = read_vector3_f32(f)
            bone.rotation = read_quat_wxyz(f)
            
            # Apply scaling to convert from cm to m
            bone.position = bone.position.scalar(0.01)
            
            # Force first bone to be root
            if i == 0:
                bone.parent_id = -1

            print(f"Bone {i}: {bone.name} (parent: {bone.parent_id})")
            self.bones.append(bone)
        
        # Try to read dummy objects (they may not exist in all files)
        try:
            # Check if there's more data to read
            current_pos = f.tell()
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()
            f.seek(current_pos)  # Seek back
            
            if current_pos < file_size:
                dummy_count = read_u32(f)
                print(f"Dummy count: {dummy_count}")
                
                for i in range(dummy_count):
                    dummy = Dummy()
                    dummy.name = read_str(f)
                    dummy.parent_id = read_i32(f)
                    dummy.position = read_vector3_f32(f)
                    dummy.rotation = read_quat_wxyz(f)
                    
                    # Apply scaling
                    dummy.position = dummy.position.scalar(0.01)
                    
                    print(f"Dummy {i}: {dummy.name} (parent: {dummy.parent_id})")
                    self.dummies.append(dummy)
            else:
                print("No dummy data in file")
        except Exception as e:
            print(f"Could not read dummies (file may not contain them): {e}")
            # This is not necessarily an error - older files may not have dummies