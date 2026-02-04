from .utils import *


class Him:
    def __init__(self, filepath=None):
        self.width = 0
        self.length = 0
        
        # Two dimensional array for height data
        self.heights = [] 
        self.max_height = 0.0
        self.min_height = 0.0
        
        if filepath:
            self.load(filepath)

    def load(self, filepath):
        with open(filepath, 'rb') as f:
            self.width = read_i32(f)
            self.length = read_i32(f)
            # Skip 8 bytes (grid_count i32 + patch_scale f32)
            f.seek(8, 1)
            
            self.heights = list_2d(self.width, self.length, 0)
            for y in range(self.length):
                for x in range(self.width):
                    h = read_f32(f)
                    self.heights[y][x] = h
                    
                    if h > self.max_height:
                        self.max_height = h
                    if h < self.min_height:
                        self.min_height = h
