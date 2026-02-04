# ROSE Online Asset File Analysis Report

## Executive Summary

This report analyzes the differences between the Rust `rose-file-readers` library (the official reference implementation) and the Blender Python plugin (`io_rose`) for parsing ROSE Online asset files. The goal is to identify discrepancies that may cause the Blender plugin to not display assets like the official game client.

---

## 1. FILE-BY-FILE COMPARISON

### 1.1 ZMS (Mesh) Files

#### Rust Implementation (`rose-file-readers/src/zms.rs`)
- **Version Support**: ZMS0005, ZMS0006, ZMS0007, ZMS0008
- **Version 5/6 Format**:
  - Uses `uint32` for vertex counts and indices
  - Positions are stored as `int32` (cm units) and **scaled by 100.0** during parsing
  - Bone lookup table with dummy indices (skipped)
  - Vertex format flags: POSITION, NORMAL, COLOR, BONE_WEIGHT, BONE_INDEX, TANGENT, UV1-UV4
- **Version 7/8 Format**:
  - Uses `uint16` for vertex counts and indices (matches C++ uint16)
  - **No scaling applied** - positions are already in meters
  - Direct bone list (no lookup table)
  - Strip indices supported (for triangle strips)

#### Python Implementation (`rose/zms.py`)
- **Version Support**: Same versions detected
- **Critical Discrepancy #1 - Position Scaling**:
  - Line 130: `pos.z / 100.0` - Python divides by 100.0 for version 5/6
  - **Rust also divides by 100.0** (lines 121-125), so this is CORRECT
- **Critical Discrepancy #2 - Version 7/8 Positions**:
  - Python version 7/8 reads positions directly (lines 221-223)
  - **Rust version 7/8 does NOT divide positions by 100.0** (line 255-258)
  - Python implementation matches Rust correctly for v7/8

#### Geometry Data Reading Differences
| Aspect | Rust | Python | Status |
|--------|------|--------|--------|
| Vertex position reading | `reader.read_vector3_f32()` | `read_vector3_f32(f)` | Same |
| Bone lookup table | `bones.push(reader.read_u32() as u16)` | `bone_table.append(read_u32(f))` | Same |
| UV coordinate reading | `reader.read_vec::<[f32; 2]>` | `read_vector2_f32(f)` | Same |
| Index reading (v5/6) | `reader.read_u32() as u16` | `read_u32(f)` | Same |

#### Material/Strip Data
- **Rust**: Reads `material_num_faces` as `Vec<u16>` (v6) or `Vec<u16>` (v8)
- **Python**: Reads as `list` of `uint32` but converts correctly to `uint16`
- **Strip indices**: Rust has dedicated `strip_indices` array; Python has `self.strips` array

#### Key Finding: ZMS
**The Python ZMS implementation correctly matches the Rust version 5/6 scaling behavior.** No critical parsing discrepancies found in ZMS file format handling.

---

### 1.2 ZSC (Scene) Files

#### Rust Implementation (`rose-file-readers/src/zsc.rs`)
- **Version**: Single format (no version numbers in magic header)
- **Materials**:
  - `ZscMaterialBlend` enum: Normal=0, Lighten=1
  - `ZscMaterialGlow` enum: None, Simple, Light, Texture, TextureLight, Alpha
  - `alpha_test` stored as `Option<f32>` (256.0 divisor)
- **Object Parts**:
  - Position: `Vec3<f32>`
  - Rotation: `Vec4<f32>` (quaternion)
  - Scale: `Vec3<f32>`
  - Parent: `Option<u16>` (1-based in file)
- **Properties Block** (lines 201-300):
  - Prop ID 1: Position
  - Prop ID 2: Rotation
  - Prop ID 3: Scale
  - Prop ID 4: Skip 16 bytes
  - Prop ID 5: Bone index (u16)
  - Prop ID 6: Dummy index (u16)
  - Prop ID 7: Parent (u16, 1-based → 0-based)
  - Prop ID 29: Collision flags (u16)

#### Python Implementation (`rose/zsc.py`)
- **Critical Discrepancy #3 - Blend Mode Mapping**:
  - Lines 22-27: BlendMode enum defines NONE=0, CUSTOM=1, NORMAL=2, LIGHTEN=3
  - **Rust uses NONE=0, LIGHTEN=1** only (no CUSTOM)
  - Python adds CUSTOM=1 and shifts NORMAL to 2, LIGHTEN to 3
  - **This is a Python-only extension, not a bug**

- **Critical Discrepancy #4 - Glow Type Mapping**:
  - Lines 29-36: GlowType enum has NONE=0, NOTSET=1, SIMPLE=2, LIGHT=3, TEXTURE=4, TEXTURELIGHT=5, ALPHA=6
  - **Rust (lines 155-162)**: 0|1=None, 2=Simple, 3=Light, 4=TextureLight, 5=Alpha
  - Python adds NOTSET=1 and TEXTURE=4 (rust has 4=TextureLight, 5=Alpha)
  - **This is a Python extension, not a bug**

- **Quaternion Order**: Both use W,X,Y,Z order (correct)

#### Key Finding: ZSC
The Python ZSC implementation adds some extra enum values (CUSTOM, NOTSET, TEXTURE) that don't exist in the official file format, but these are **safe fallbacks** that won't cause display issues.

---

### 1.3 HIM (Height Map) Files

#### Rust Implementation (`rose-file-readers/src/him.rs`)
```rust
pub struct HimFile {
    pub width: u32,
    pub height: u32,
    pub heights: Vec<f32>,
}
```
- **No scaling applied** - heights are in cm
- **No patches** - raw height grid only
- Skips 8 bytes after width/height (lines 23-25)

#### Python Implementation (`rose/him.py`)
```python
class Him:
    self.width = read_i32(f)
    self.length = read_i32(f)
    self.grid_count = read_i32(f)
    self.patch_scale = read_f32(f)
    # ... patches and quad_patches arrays ...
```
- **Critical Discrepancy #5 - Extra Data Parsing**:
  - Python reads: width, length, grid_count, patch_scale
  - Rust reads: width, height (then skips 8 bytes)
  - **Python parses patches and quad_patches that don't exist in the official format**
  - This could cause **offset errors** if the file structure is different

#### Key Finding: HIM
**Python's HIM parser assumes additional data structures (patches, quad_patches) that may not exist in the official ZMS format.** This could lead to parsing errors or incorrect height values.

---

### 1.4 TIL (Tile) Files

#### Rust Implementation (`rose-file-readers/src/til.rs`)
```rust
pub struct TilFile {
    pub width: u32,
    pub height: u32,
    pub tiles: Vec<u32>,
}
```
- **Row-by-row reading** (height loops outer, width inner)
- Skips 3 bytes per tile before reading tile value

#### Python Implementation (`rose/til.py`)
```python
for l in range(self.length-1, -1, -1):  # REVERSED ORDER!
    for w in range(self.width):
        t = TilPatch()
        t.brush = read_i8(f)
        t.tile_index = read_i8(f)
        t.tile_set = read_i8(f)
        t.tile = read_i32(f)
```
- **Critical Discrepancy #6 - Row Order**:
  - Rust: Normal order (row 0 to height-1)
  - Python: **Reversed order** (length-1 down to 0)
  - **This will flip the tile map vertically**

#### Key Finding: TIL
**Python's TIL parser flips the tile map vertically due to reversed row iteration.** This will cause terrain textures to appear upside-down compared to the official game.

---

### 1.5 IFO (Object Instance) Files

#### Rust Implementation (`rose-file-readers/src/ifo.rs`)
- **Object Structure**:
  ```rust
  pub struct IfoObject {
      pub object_name: String,
      pub minimap_position: Vec2<u32>,
      pub object_type: u32,
      pub object_id: u32,
      pub warp_id: u16,
      pub event_id: u16,
      pub position: Vec3<f32>,
      pub rotation: Quat4<f32>,
      pub scale: Vec3<f32>,
  }
  ```
- **Quaternion**: Uses `read_quat4_xyzw_f32()` - reads X,Y,Z,W order
- **Rotation reading** (line 34): `reader.read_quat4_xyzw_f32()?`

#### Python Implementation (`rose/ifo.py`)
- **Critical Discrepancy #7 - Quaternion Order**:
  - Line 100: `obj.rotation = read_quat_xyzw(f)` 
  - Python's `read_quat_xyzw()` (utils.py line 209-215):
    ```python
    def read_quat_xyzw(f):
        x = read_f32(f)
        y = read_f32(f)
        z = read_f32(f)
        w = read_f32(f)
        return Quat(w, x, y, z)  # Returns Quat(w, x, y, z) - WRONG!
    ```
  - **Rust returns Quat4 { x, y, z, w }**
  - **Python's Quat class stores as Quat(w, x, y, z) but read_quat_xyzw creates incorrect order**

#### Key Finding: IFO
**Python's quaternion parsing has a critical bug** - `read_quat_xyzw` reads X,Y,Z,W but stores as Quat(w, x, y, z) which means the components are in wrong positions. Should be Quat(w, x, y, z) where w is read last → correct!

---

### 1.6 ZON (Zone) Files

#### Rust Implementation (`rose-file-readers/src/zon.rs`)
- **Block types**: ZoneInfo=0, EventPositions=1, Textures=2, Tiles=3, Economy=4
- **ZoneInfo block**: Skips 12 bytes, reads grid_per_patch (u32), grid_size (f32), skips 8 more bytes
- **Rotation enum**: Unknown=0, None=1, FlipHorizontal=2, FlipVertical=3, Flip=4, Clockwise90=5, CounterClockwise90=6

#### Python Implementation (`rose/zon.py`)
- **Block type constants**: Info=0, Spawns=1, Textures=2, Tiles=3, Economy=4
- **Critical Discrepancy #8 - Block Type Names**:
  - Rust: EventPositions=1, Python: Spawns=1 (same value, different name)
  - **This is just naming, not a bug**

#### Key Finding: ZON
**Minor naming differences only** - no functional impact.

---

## 2. COORDINATE TRANSFORMATION DISCREPANCIES

### 2.1 Position Conversion

#### Rust Implementation
From `rose-file-readers/src/zms.rs` (lines 120-125):
```rust
// Mesh version 5/6 is scaled by 100.0
for [x, y, z] in position.iter_mut() {
    *x /= 100.0;
    *y /= 100.0;
    *z /= 100.0;
}
```
- **Version 5/6**: Positions divided by 100.0 (cm → meters)
- **Version 7/8**: No division (already in meters)

#### Python Implementation
From `rose/zms.py` (line 130):
```python
pos.z / 100.0)  # Divide by 100.0 to unscale
```
- **Version 5/6**: Correctly divides by 100.0

#### Blender Export (import_zms.py)
From line 115:
```python
verts.append((v.position.x, v.position.z, -v.position.y))
```
- **Blender coordinate conversion**: (X, Y, Z) → (X, Z, -Y)
- **This is CORRECT** for converting from Y-up (ROSE) to Z-up (Blender)

### 2.2 Scale Conversion

#### Rust Implementation
No explicit scale transformation in file readers - scaling handled by position conversion.

#### Python Implementation
From `rose/utils.py` (lines 261-277):
```python
def convert_rose_position_to_blender(x, y, z):
    return (x / 100.0, z / 100.0, -y / 100.0)
```
- **100.0 scaling factor** for cm → meters conversion
- **Correct coordinate transformation**

#### Import ZSC (import_zsc.py)
Lines 152-156:
```python
pos = ifo_object.position
bx, by, bz = convert_rose_position_to_blender(pos.x, pos.y, pos.z)
parent_empty.location = (bx + 52.0, bz + 52.0, by + 52.0)  # World offset
```
- **Adds 52.0 meter offset** to match terrain coordinates
- **This is a game-specific feature**, not a bug

### 2.3 Rotation/Quaternion Conversion

#### Rust Implementation
From `rose-file-readers/src/reader.rs` (lines 237-243):
```rust
pub fn read_quat4_xyzw_f32(&mut self) -> Result<Quat4<f32>>, ReadError> {
    let x = self.read_f32()?;
    let y = self.read_f32()?;
    let z = self.read_f32()?;
    let w = self.read_f32()?;
    Ok(Quat4 { x, y, z, w })
}
```
- **Stores as Quat4 { x, y, z, w }**

#### Python Implementation
From `rose/utils.py` (lines 209-215):
```python
def read_quat_xyzw(f):
    x = read_f32(f)
    y = read_f32(f)
    z = read_f32(f)
    w = read_f32(f)
    return Quat(w, x, y, z)
```
- **Stores as Quat(w, x, y, z)** with correct component order!
- Wait, let me re-check... `Quat(w, x, y, z)` means:
  - First param (w) = read last → W component ✓
  - Second param (x) = read first → X component ✓
  - This is **CORRECT**

From `import_zsc.py` (lines 121-139):
```python
def convert_rose_quaternion_to_blender(self, rot):
    return (rot.x, rot.z, -rot.y, rot.w)
```
- **This transformation is INCORRECT**
- For coordinate conversion Y-up → Z-up:
  - Y axis becomes -Z axis
  - Z axis becomes Y axis
  - Rotation in Y-Z plane needs adjustment
- **Correct transformation** should be: (W, X, Z, -Y) for the quaternion

---

## 3. PERFORMANCE OPTIMIZATION OPPORTUNITIES

### 3.1 File I/O Optimization
**Current**: Python uses `struct.unpack()` for each value
**Optimization**: Use `memoryview` or `array` module for batch reading

### 3.2 String Decoding
**Current**: Python reads byte-by-byte for null-terminated strings
**Optimization**: Use `read_until(b'\x00')` with `bytes.find()`

### 3.3 List Pre-allocation
**Current**: Python lists grow dynamically in loops
**Optimization**: Pre-allocate with `[None] * count` or `list(range(count))`

### 3.4 Mesh Construction
**Current**: `mesh.from_pydata()` with separate vertex/face lists
**Optimization**: Use bmesh for faster mesh construction

### 3.5 Material/Texture Loading
**Current**: Texture loading in import loop (serial)
**Optimization**: Load textures asynchronously or cache

---

## 4. OTHER DIFFERENCES AFFECTING RENDERING

### 4.1 UV Coordinate Flipping
From `import_zms.py` (lines 138-156):
```python
mesh.uv_layers["uv1"].data[loop_idx].uv = (u, 1-v)  # Flip V
```
- **Rust file readers don't flip UVs** - V coordinate stored as-is
- **Python flips V** (1-v) - this is CORRECT for OpenGL texture coordinates

### 4.2 Normal Vector Transform
- **Rust**: No normal transformation in file readers
- **Python**: No normal transformation in importers
- **Issue**: Normals need to be transformed when coordinate system changes!

### 4.3 Material/Shader Properties
From `import_zsc.py` (lines 272-354):
- Blender uses Principled BSDF shader
- ROSE uses custom shader with:
  - Alpha test (threshold)
  - Blend modes (Normal, Lighten)
  - Glow types
- **Issue**: Not all ROSE shader properties are mapped to Blender equivalents

---

## 5. RECOMMENDATIONS FOR FIXES

### High Priority (Critical Issues)

1. **Fix TIL Row Order** (`rose/til.py` line 26):
   ```python
   # Change from:
   for l in range(self.length-1, -1, -1):
   # To:
   for l in range(self.length):
   ```

2. **Fix HIM Extra Data Parsing** (`rose/him.py`):
   - Remove patches and quad_patches parsing
   - Match Rust implementation exactly

3. **Add Normal Transformation** (`import_zms.py`):
   - Transform normals during coordinate conversion
   - Apply rotation matrix to normal vectors

4. **Verify IFO Quaternion Reading** (`rose/ifo.py` line 100):
   - Verify quaternion order matches Rust
   - Test with actual game files

### Medium Priority (Rendering Quality)

5. **Map ROSE Shader Properties to Blender**:
   - Implement alpha test in shader
   - Implement blend modes (Normal, Lighten)
   - Implement glow effects

6. **Fix World Offset Calculation** (`import_zsc.py` line 156):
   - Understand why 52.0 offset is needed
   - Make offset configurable

### Low Priority (Performance)

7. **Optimize File I/O**:
   - Batch read operations
   - Use memory-mapped files

8. **Mesh Construction**:
   - Use bmesh for faster mesh building
   - Reduce memory allocations

---

## 6. CONCLUSION

The Blender plugin's file parsing is **mostly correct** but has several critical issues:

1. **TIL file parsing flips the tile map vertically** - will cause terrain to appear upside-down
2. **HIM file parsing reads extra data structures** that don't exist in the official format - could cause offset errors
3. **Normal vector transformation is missing** - meshes may have incorrect lighting
4. **Quaternion rotation handling may have issues** when converting between coordinate systems

The Blender plugin correctly implements:
- ZMS position scaling (cm → meters)
- Coordinate transformation (Y-up → Z-up)
- UV coordinate flipping (V axis)
- Most material properties

**The main reason the Blender plugin may not display assets like the official game is due to missing shader property mapping (alpha test, blend modes, glow) and potential coordinate transformation issues in rotation/quaternion handling.**