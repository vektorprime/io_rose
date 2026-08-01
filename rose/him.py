from .utils import *


class Him:
    def __init__(self, filepath=None):
        self.width = 0
        self.length = 0
        
        # Two dimensional array for height data
        self.heights = [] 
        self.max_height = 0.0
        self.min_height = 0.0

        # Reserved header fields (grid_count / patch_scale), preserved on save
        self.grid_count = 0
        self.patch_scale = 0.0

        # Trailing data after the height block (e.g. the legacy "Quad\0"
        # quadtree footer). Not read by the game client, preserved verbatim.
        self._tail = b""
        
        if filepath:
            self.load(filepath)

    def load(self, filepath):
        with open(filepath, 'rb') as f:
            self.width = read_i32(f)
            self.length = read_i32(f)
            self.grid_count = read_i32(f)
            self.patch_scale = read_f32(f)
            
            self.heights = list_2d(self.width, self.length, 0)
            for y in range(self.length):
                for x in range(self.width):
                    h = read_f32(f)
                    self.heights[y][x] = h
                    
                    if h > self.max_height:
                        self.max_height = h
                    if h < self.min_height:
                        self.min_height = h

            self._tail = f.read()

    def save(self, filepath):
        """Write the HIM file: width + length + grid_count + patch_scale +
        per-sample f32 heights in cm, then the preserved footer (if any).

        The header layout matches the Rust map editor's write_him_file()
        (src/map_editor/coords.rs) when grid_count=0/patch_scale=0; the
        original values are kept when saving a file that was loaded, so
        round-trips stay byte-identical."""
        with open(filepath, 'wb') as f:
            write_i32(f, self.width)
            write_i32(f, self.length)
            write_i32(f, self.grid_count)
            write_f32(f, self.patch_scale)
            for y in range(self.length):
                for x in range(self.width):
                    write_f32(f, self.heights[y][x])
            f.write(self._tail)
