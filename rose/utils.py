import struct

# Basic data type classes
class Vector2:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y
    
    def __repr__(self):
        return f"Vector2({self.x}, {self.y})"

class Vector3:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z
    
    def __repr__(self):
        return f"Vector3({self.x}, {self.y}, {self.z})"
    
    def as_tuple(self):
        """Return vector as tuple"""
        return (self.x, self.y, self.z)
    
    def scalar(self, s):
        """Multiply vector by scalar and return new vector"""
        return Vector3(self.x * s, self.y * s, self.z * s)

class Color4:
    def __init__(self, r=1.0, g=1.0, b=1.0, a=1.0):
        self.r = r
        self.g = g
        self.b = b
        self.a = a
    
    def __repr__(self):
        return f"Color4({self.r}, {self.g}, {self.b}, {self.a})"

class Quat:
    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0):
        self.w = w
        self.x = x
        self.y = y
        self.z = z
    
    def as_tuple(self, w_first=False):
        """Return quaternion as tuple
        
        Args:
            w_first: If True, return (W,X,Y,Z), else return (X,Y,Z,W)
        """
        if w_first:
            return (self.w, self.x, self.y, self.z)
        else:
            return (self.x, self.y, self.z, self.w)
    
    def __repr__(self):
        return f"Quat({self.w}, {self.x}, {self.y}, {self.z})"

# Read functions for signed integers
def read_i8(f):
    """Read signed 8-bit integer"""
    return struct.unpack("<b", f.read(1))[0]

def read_i16(f):
    """Read signed 16-bit integer"""
    return struct.unpack("<h", f.read(2))[0]

def read_i32(f):
    """Read signed 32-bit integer"""
    return struct.unpack("<i", f.read(4))[0]

# Read functions for unsigned integers
def read_u8(f):
    """Read unsigned 8-bit integer (byte)"""
    return struct.unpack("<B", f.read(1))[0]

def read_u16(f):
    """Read unsigned 16-bit integer"""
    return struct.unpack("<H", f.read(2))[0]

def read_u32(f):
    """Read unsigned 32-bit integer"""
    return struct.unpack("<I", f.read(4))[0]

# Read functions for floats
def read_f32(f):
    """Read 32-bit float"""
    return struct.unpack("<f", f.read(4))[0]

# Read functions for boolean
def read_bool(f):
    """Read boolean (1 byte, 0=False, non-zero=True)"""
    return struct.unpack("<?", f.read(1))[0]

# Read functions for strings
def read_str(f):
    """Read null-terminated string"""
    chars = []
    while True:
        c = f.read(1)
        if not c or c == b'\x00':
            break
        chars.append(c)
    return b''.join(chars).decode('latin-1', errors='ignore')

def read_fstr(f, length):
    """Read a fixed-length string of specified length"""
    data = f.read(length)
    return data.decode('latin-1', errors='ignore').rstrip('\x00')

def read_bstr(f):
    """Read BSTR (length-prefixed string)
    Format: BYTE length, followed by that many characters
    """
    length = read_u8(f)
    if length == 0:
        return ""
    data = f.read(length)
    return data.decode('latin-1', errors='ignore')

# Read functions for lists
def read_list_i16(f, count):
    """Read a list of signed 16-bit integers"""
    return [read_i16(f) for _ in range(count)]

def read_list_u16(f, count):
    """Read a list of unsigned 16-bit integers"""
    return [read_u16(f) for _ in range(count)]

def read_list_u32(f, count):
    """Read a list of unsigned 32-bit integers"""
    return [read_u32(f) for _ in range(count)]

def read_list_f32(f, count):
    """Read a list of 32-bit floats"""
    return [read_f32(f) for _ in range(count)]

# Read functions for vectors
def read_vector2_f32(f):
    """Read a 2D vector of floats"""
    x = read_f32(f)
    y = read_f32(f)
    return Vector2(x, y)

def read_vector3_f32(f):
    """Read a 3D vector of floats"""
    x = read_f32(f)
    y = read_f32(f)
    z = read_f32(f)
    return Vector3(x, y, z)

def read_vector3_i16(f):
    """Read a 3D vector of signed 16-bit integers"""
    x = read_i16(f)
    y = read_i16(f)
    z = read_i16(f)
    return Vector3(x, y, z)

def read_vector3_u16(f):
    """Read a 3D vector of unsigned 16-bit integers"""
    x = read_u16(f)
    y = read_u16(f)
    z = read_u16(f)
    return Vector3(x, y, z)

def read_vector4_f32(f):
    """Read a 4D vector of floats"""
    x = read_f32(f)
    y = read_f32(f)
    z = read_f32(f)
    w = read_f32(f)
    return (x, y, z, w)

def read_vector4_u16(f):
    """Read a 4D vector of unsigned 16-bit integers"""
    x = read_u16(f)
    y = read_u16(f)
    z = read_u16(f)
    w = read_u16(f)
    return (x, y, z, w)

def read_vector4_u32(f):
    """Read a 4D vector of unsigned 32-bit integers"""
    x = read_u32(f)
    y = read_u32(f)
    z = read_u32(f)
    w = read_u32(f)
    return (x, y, z, w)

# Read functions for colors
def read_color4(f):
    """Read a 4-component color (RGBA)"""
    r = read_f32(f)
    g = read_f32(f)
    b = read_f32(f)
    a = read_f32(f)
    return Color4(r, g, b, a)

# Read functions for quaternions
def read_quat_wxyz(f):
    """Read quaternion in W,X,Y,Z order"""
    w = read_f32(f)
    x = read_f32(f)
    y = read_f32(f)
    z = read_f32(f)
    return Quat(w, x, y, z)

# Write functions (if needed for other formats)
def write_i8(f, value):
    """Write signed 8-bit integer"""
    f.write(struct.pack("<b", value))

def write_u8(f, value):
    """Write unsigned 8-bit integer"""
    f.write(struct.pack("<B", value))

def write_i16(f, value):
    """Write signed 16-bit integer"""
    f.write(struct.pack("<h", value))

def write_u16(f, value):
    """Write unsigned 16-bit integer"""
    f.write(struct.pack("<H", value))

def write_i32(f, value):
    """Write signed 32-bit integer"""
    f.write(struct.pack("<i", value))

def write_u32(f, value):
    """Write unsigned 32-bit integer"""
    f.write(struct.pack("<I", value))

def write_f32(f, value):
    """Write 32-bit float"""
    f.write(struct.pack("<f", value))

def write_bool(f, value):
    """Write boolean (1 byte)"""
    f.write(struct.pack("<?", value))

def list_2d(width, height, default=None):
    """Create a 2D list with given dimensions
    
    Args:
        width: Number of rows
        height: Number of columns
        default: Default value for each element (default: None)
    """
    return [[default for _ in range(height)] for _ in range(width)]