from .utils import *

class TilPatch:
    """
    Represents a single tile patch in the TIL file.
    
    Note: There is a discrepancy between Python and Rust implementations:
    - Python interprets first 3 bytes as: brush (i8), tile_index (i8), tile_set (i8)
    - Rust skips first 3 bytes and only reads tile (u32)
    
    Both read 7 bytes total (3 + 4), so file position stays in sync.
    The 'tile' field is the only one used for texture lookups, so this
    difference doesn't affect functionality.
    """
    def __init__(self):
        self.brush = 0       # Possibly terrain brush type (unused)
        self.tile_index = 0  # Possibly tile variation index (unused)
        self.tile_set = 0    # Possibly tileset identifier (unused)
        self.tile = 0        # Index into ZON texture array (USED)

class Til:
    """TIL file parser for Rose Online terrain tile data."""
    
    def __init__(self, filepath=None):
        self.width = 0
        self.length = 0
        self.tiles = []

        if filepath:
            self.load(filepath)

    def load(self, filepath):
        """Load TIL file from disk.
        
        Format per tile (7 bytes):
        - 3 bytes: metadata (brush, tile_index, tile_set) - interpretation varies
        - 4 bytes: tile (u32) - index into ZON texture array
        """
        with open(filepath, 'rb') as f:
            self.width = read_i32(f)
            self.length = read_i32(f)
            
            self.tiles = list_2d(self.width, self.length)

            for l in range(self.length):
                for w in range(self.width):
                    t = TilPatch()
                    # Read metadata bytes (interpretation may differ from Rust)
                    t.brush = read_i8(f)
                    t.tile_index = read_i8(f)
                    t.tile_set = read_i8(f)
                    # Read tile index (used for texture lookup)
                    t.tile = read_u32(f)  # Changed to unsigned for consistency

                    self.tiles[l][w] = t


