# Rust Bevy 0.11 Rose Online Reference Implementation Analysis

**Phase 2 Analysis Document**  
**Source Location:** `C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src` and `C:\Users\vicha\RustroverProjects\exjam-rose-offline-client\rose-offline-client\src`

---

## Table of Contents

1. [Project Architecture Overview](#project-architecture-overview)
2. [Binary Parsing Infrastructure](#binary-parsing-infrastructure)
3. [ZMS Format - Mesh Files](#zms-format---mesh-files)
4. [ZMD Format - Skeleton Files](#zmd-format---skeleton-files)
5. [ZMO Format - Animation Files](#zmo-format---animation-files)
6. [ZSC Format - Scene/Container Files](#zsc-format----scenecontainer-files)
7. [ZON Format - Zone Files](#zon-format---zone-files)
8. [HIM Format - Heightmap Files](#him-format---heightmap-files)
9. [TIL Format - Tile Map Files](#til-format---tile-map-files)
10. [IFO Format - Map Object Placement](#ifo-format---map-object-placement)
11. [CHR Format - Character Definition Files](#chr-format---character-definition-files)
12. [Coordinate System Transformations](#coordinate-system-transformations)
13. [Bevy 0.11 Mesh Generation](#bevy-011-mesh-generation)
14. [Key Algorithmic Approaches](#key-algorithmic-approaches)
15. [Cross-Reference Validation Points](#cross-reference-validation-points)

---

## Project Architecture Overview

### Crate Structure

The Rust implementation consists of two main components:

1. **rose-file-readers** (`rose-offline\rose-file-readers\src\`)
   - Pure Rust binary format parsing library
   - No external game engine dependencies
   - Provides `RoseFile` trait for all format readers

2. **rose-offline-client** (`exjam-rose-offline-client\rose-offline-client\src\`)
   - Bevy 0.11 game client
   - Asset loaders that convert parsed data to Bevy types
   - Rendering systems and materials

### Key Files Analyzed

| File | Purpose | Lines |
|------|---------|-------|
| [`zms.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zms.rs) | Mesh format parsing | 366 |
| [`zmd.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zmd.rs) | Skeleton format parsing | 71 |
| [`zmo.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zmo.rs) | Animation format parsing | 147 |
| [`zsc.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zsc.rs) | Scene/container parsing | 361 |
| [`zon.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zon.rs) | Zone format parsing | 141 |
| [`him.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\him.rs) | Heightmap parsing | 40 |
| [`til.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\til.rs) | Tile map parsing | 39 |
| [`ifo.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\ifo.rs) | Object placement parsing | 376 |
| [`chr.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\chr.rs) | Character definition parsing | 94 |
| [`reader.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\reader.rs) | Binary reader utilities | 345 |
| [`types.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\types.rs) | Common data types | 28 |

---

## Binary Parsing Infrastructure

### RoseFileReader - Core Binary Reader

Location: [`reader.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\reader.rs)

```rust
pub struct RoseFileReader<'a> {
    pub cursor: Cursor<&'a [u8]>,
    pub use_wide_strings: bool,
}
```

#### Primitive Reading Methods

| Method | Return Type | Byte Order | Size |
|--------|-------------|------------|------|
| `read_u8()` | `u8` | N/A | 1 |
| `read_u16()` | `u16` | Little Endian | 2 |
| `read_u32()` | `u32` | Little Endian | 4 |
| `read_u64()` | `u64` | Little Endian | 8 |
| `read_i8()` | `i8` | N/A | 1 |
| `read_i16()` | `i16` | Little Endian | 2 |
| `read_i32()` | `i32` | Little Endian | 4 |
| `read_f32()` | `f32` | Little Endian | 4 |
| `read_f64()` | `f64` | Little Endian | 8 |

#### Vector Reading Methods

```rust
// Vec2: x (f32), y (f32) = 8 bytes
pub fn read_vector2_f32(&mut self) -> Result<Vec2<f32>, ReadError>

// Vec3: x (f32), y (f32), z (f32) = 12 bytes
pub fn read_vector3_f32(&mut self) -> Result<Vec3<f32>, ReadError>

// Vec4: x (f32), y (f32), z (f32), w (f32) = 16 bytes
pub fn read_vector4_f32(&mut self) -> Result<Vec4<f32>, ReadError>

// Quaternion XYZW: x, y, z, w (all f32) = 16 bytes
pub fn read_quat4_xyzw_f32(&mut self) -> Result<Quat4<f32>, ReadError>

// Quaternion WXYZ: w, x, y, z (all f32) = 16 bytes
pub fn read_quat4_wxyz_f32(&mut self) -> Result<Quat4<f32>, ReadError>
```

#### String Reading Methods

| Method | Format | Length Encoding |
|--------|--------|-----------------|
| `read_null_terminated_string()` | Null-terminated | None (reads until `\0`) |
| `read_fixed_length_string(n)` | Fixed buffer | Parameter `n` |
| `read_u8_length_string()` | Length-prefixed | 1 byte (u8) |
| `read_u16_length_string()` | Length-prefixed | 2 bytes (u16) |
| `read_u32_length_string()` | Length-prefixed | 4 bytes (u32) |
| `read_variable_length_string()` | Variable | 1-2 bytes (bit 7 indicates second byte) |

#### String Encoding

```rust
fn decode_string(mut bytes: &[u8], use_wide_strings: bool) -> Cow<'_, str> {
    if use_wide_strings {
        // UTF-16 LE decoding
        let (decoded, _, _) = UTF_16LE.decode(bytes);
        decoded
    } else {
        // Try UTF-8 first, fall back to EUC-KR
        match str::from_utf8(bytes) {
            Ok(s) => Cow::from(s),
            Err(_) => {
                let (decoded, _, _) = EUC_KR.decode(bytes);
                decoded
            }
        }
    }
}
```

### Common Data Types

Location: [`types.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\types.rs)

```rust
#[derive(Default, Copy, Clone, Debug)]
pub struct Vec2<T> {
    pub x: T,
    pub y: T,
}

#[derive(Default, Copy, Clone, Debug)]
pub struct Vec3<T> {
    pub x: T,
    pub y: T,
    pub z: T,
}

#[derive(Default, Copy, Clone, Debug)]
pub struct Vec4<T> {
    pub x: T,
    pub y: T,
    pub z: T,
    pub w: T,
}

#[derive(Default, Copy, Clone, Debug)]
pub struct Quat4<T> {
    pub x: T,
    pub y: T,
    pub z: T,
    pub w: T,
}
```

---

## ZMS Format - Mesh Files

Location: [`zms.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zms.rs)

### Magic Header Versions

| Magic String | Version | Parser Function |
|--------------|---------|-----------------|
| `ZMS0005` | 5 | `read_version6()` |
| `ZMS0006` | 6 | `read_version6()` |
| `ZMS0007` | 7 | `read_version8()` |
| `ZMS0008` | 8 | `read_version8()` |

### Format Flags (Bitmask)

```rust
bitflags::bitflags! {
    pub struct ZmsFormatFlags: u32 {
        const POSITION = (1 << 1);    // 0x0002
        const NORMAL = (1 << 2);      // 0x0004
        const COLOR = (1 << 3);       // 0x0008
        const BONE_WEIGHT = (1 << 4); // 0x0010
        const BONE_INDEX = (1 << 5);  // 0x0020
        const TANGENT = (1 << 6);     // 0x0040
        const UV1 = (1 << 7);         // 0x0080
        const UV2 = (1 << 8);         // 0x0100
        const UV3 = (1 << 9);         // 0x0200
        const UV4 = (1 << 10);        // 0x0400
    }
}
```

### Data Structure

```rust
pub struct ZmsFile {
    pub format: ZmsFormatFlags,
    pub position: Vec<[f32; 3]>,
    pub normal: Vec<[f32; 3]>,
    pub color: Vec<[f32; 4]>,
    pub bone_weights: Vec<[f32; 4]>,
    pub bone_indices: Vec<[u16; 4]>,
    pub tangent: Vec<[f32; 3]>,
    pub uv1: Vec<[f32; 2]>,
    pub uv2: Vec<[f32; 2]>,
    pub uv3: Vec<[f32; 2]>,
    pub uv4: Vec<[f32; 2]>,
    pub indices: Vec<u16>,
    pub strip_indices: Vec<u16>,
    pub material_num_faces: Vec<u16>,
}
```

### Version 5/6 Binary Layout

```
Offset  Size    Field
------  ----    -----
0       7       Magic ("ZMS0005" or "ZMS0006" null-terminated)
7       4       Format flags (u32)
11      12      Bounding box min (Vec3 f32)
23      12      Bounding box max (Vec3 f32)
35      4       Bone count (u32)
39      8*bone  Bone table (u32 unknown + u32 bone_id per entry)
--      4       Vertex count (u32)

Per-vertex data (if flag set):
  --    4       Vertex ID (u32) - ONLY in v5/v6
  --    12      Position (Vec3 f32) - if POSITION flag
  --    12      Normal (Vec3 f32) - if NORMAL flag
  --    16      Color (Vec4 f32) - if COLOR flag
  --    16+16   Bone weights (Vec4 f32) + Bone indices (Vec4 u32) - if BONE_WEIGHT + BONE_INDEX
  --    12      Tangent (Vec3 f32) - if TANGENT flag
  --    8       UV1 (Vec2 f32) - if UV1 flag
  --    8       UV2 (Vec2 f32) - if UV2 flag
  --    8       UV3 (Vec2 f32) - if UV3 flag
  --    8       UV4 (Vec2 f32) - if UV4 flag

Index data:
--      4       Triangle count (u32)
Per-triangle:
  --    4       Vertex ID (u32)
  --    4       Index 0 (u32 -> cast to u16)
  --    4       Index 1 (u32 -> cast to u16)
  --    4       Index 2 (u32 -> cast to u16)

Material faces (v6+):
--      4       Material ID count (u32)
Per-material:
  --    4       Index (u32)
  --    4       Face count (u32 -> cast to u16)
```

**IMPORTANT:** Version 5/6 positions are scaled by 100.0 and must be divided:
```rust
// Mesh version 5/6 is scaled by 100.0
for [x, y, z] in position.iter_mut() {
    *x /= 100.0;
    *y /= 100.0;
    *z /= 100.0;
}
```

### Version 7/8 Binary Layout

```
Offset  Size    Field
------  ----    -----
0       7       Magic ("ZMS0007" or "ZMS0008" null-terminated)
7       4       Format flags (u32)
11      12      Bounding box min (Vec3 f32)
23      12      Bounding box max (Vec3 f32)
35      2       Bone count (u16)
37      2*bone  Bone table (u16 per entry)
--      2       Vertex count (u16)

Per-vertex data (if flag set) - NO Vertex ID in v7/v8:
  --    12      Position (Vec3 f32) - if POSITION flag
  --    12      Normal (Vec3 f32) - if NORMAL flag
  --    16      Color (Vec4 f32) - if COLOR flag
  --    16+8    Bone weights (Vec4 f32) + Bone indices (Vec4 u16) - if BONE_WEIGHT + BONE_INDEX
  --    12      Tangent (Vec3 f32) - if TANGENT flag
  --    8       UV1 (Vec2 f32) - if UV1 flag
  --    8       UV2 (Vec2 f32) - if UV2 flag
  --    8       UV3 (Vec2 f32) - if UV3 flag
  --    8       UV4 (Vec2 f32) - if UV4 flag

Index data:
--      2       Triangle count (u16)
--      2*3*tri Indices (u16 x 3 per triangle)

Material faces:
--      2       Material ID count (u16)
--      2*mat   Face counts (u16 per material)

Strip indices:
--      2       Strip index count (u16)
--      2*strip Strip indices (u16 each)

Version 8 only:
--      2       Pool type (u16)
```

### Bone Index Translation

Bone indices in the file are indices into a bone table, not direct bone indices:

```rust
// Version 5/6: bone table maps u32 -> u16
let bone_x = bones.get(index.x as usize).cloned()...;

// Version 7/8: bone table is direct u16 values
let bone_x = bones.get(index.x as usize).cloned()...;
```

---

## ZMD Format - Skeleton Files

Location: [`zmd.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zmd.rs)

### Magic Header Versions

| Magic String | Version |
|--------------|---------|
| `ZMD0002` | 2 |
| `ZMD0003` | 3 |

### Data Structure

```rust
pub struct ZmdFile {
    pub bones: Vec<ZmdBone>,
    pub dummy_bones: Vec<ZmdBone>,
}

pub struct ZmdBone {
    pub parent: u16,
    pub position: Vec3<f32>,
    pub rotation: Quat4<f32>,
}
```

### Binary Layout

```
Offset  Size    Field
------  ----    -----
0       7       Magic ("ZMD0002" or "ZMD0003" fixed-length)
7       4       Bone count (u32)

Per-bone:
  --    4       Parent index (u32 -> cast to u16)
  --    var     Name (null-terminated string)
  --    12      Position (Vec3 f32: x, y, z)
  --    16      Rotation (Quat4 f32: w, x, y, z - WXYZ order!)

--      4       Dummy bone count (u32)

Per-dummy-bone:
  --    var     Name (null-terminated string)
  --    4       Parent index (u32 -> cast to u16)
  --    12      Position (Vec3 f32: x, y, z)
  --    16/0    Rotation (Quat4 f32: w, x, y, z) - ONLY in v3, v2 uses identity (0,0,0,1)
```

### Key Observations

1. **Quaternion Order:** Rotations are stored in WXYZ order (w first)
2. **Parent Index:** A bone with `parent == bone_index` is a root bone
3. **Version 2 Dummy Bones:** Have no rotation data, use identity quaternion

---

## ZMO Format - Animation Files

Location: [`zmo.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zmo.rs)

### Magic Header

Only one version: `ZMO0002`

### Data Structure

```rust
pub struct ZmoFile {
    pub fps: usize,
    pub num_frames: usize,
    pub channels: Vec<(u32, ZmoChannel)>,  // (bone_index, channel_data)
    pub frame_events: Vec<u16>,
    pub total_attack_frames: usize,
    pub interpolation_interval_ms: Option<u32>,
}

pub enum ZmoChannel {
    Empty,
    Position(Vec<Vec3<f32>>),
    Rotation(Vec<Quat4<f32>>),
    Normal(Vec<Vec3<f32>>),
    Alpha(Vec<f32>),
    UV1(Vec<Vec2<f32>>),
    UV2(Vec<Vec2<f32>>),
    UV3(Vec<Vec2<f32>>),
    UV4(Vec<Vec2<f32>>),
    Texture(Vec<f32>),
    Scale(Vec<f32>),
}
```

### Channel Type Values

| Value | Channel Type |
|-------|--------------|
| 1 | Empty |
| 2 | Position |
| 4 | Rotation |
| 8 | Normal |
| 16 | Alpha |
| 32 | UV1 |
| 64 | UV2 |
| 128 | UV3 |
| 256 | UV4 |
| 512 | Texture |
| 1024 | Scale |

### Binary Layout

```
Offset  Size    Field
------  ----    -----
0       8       Magic ("ZMO0002" null-terminated)
8       4       FPS (u32)
12      4       Number of frames (u32)
16      4       Channel count (u32)

Per-channel header:
  --    4       Channel type (u32)
  --    4       Bone index (u32)

Per-frame data (for each channel, for each frame):
  --    12      Position (Vec3 f32) - if Position channel
  --    16      Rotation (Quat4 f32: w, x, y, z) - if Rotation channel
  --    12      Normal (Vec3 f32) - if Normal channel
  --    8       UV (Vec2 f32) - if UV channel
  --    4       Value (f32) - if Alpha/Texture/Scale channel

Extended footer (at end of file, -4 bytes from end):
--      4       Extended magic ("EZMO" or "3ZMO")

If extended:
  --    4       Footer position (u32, from start of file)
  --    2       Frame event count (u16)
  Per-event:
    --  2       Event ID (u16)
  
  If "3ZMO":
    --  4       Interpolation interval (u32, milliseconds)
```

### Frame Event Attack Detection

```rust
match frame_event {
    10 | 20..=28 | 56..=57 | 66..=67 => total_attack_frames += 1,
    _ => {}
}
```

---

## ZSC Format - Scene/Container Files

Location: [`zsc.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zsc.rs)

### Data Structure

```rust
pub struct ZscFile {
    pub meshes: Vec<VfsPathBuf>,
    pub materials: Vec<ZscMaterial>,
    pub effects: Vec<VfsPathBuf>,
    pub objects: Vec<ZscObject>,
}

pub struct ZscMaterial {
    pub path: VfsPathBuf,
    pub is_skin: bool,
    pub alpha_enabled: bool,
    pub two_sided: bool,
    pub alpha_test: Option<f32>,
    pub z_write_enabled: bool,
    pub z_test_enabled: bool,
    pub blend_mode: ZscMaterialBlend,
    pub specular_enabled: bool,
    pub alpha: f32,
    pub glow: Option<ZscMaterialGlow>,
}

pub struct ZscObject {
    pub parts: Vec<ZscObjectPart>,
    pub effects: Vec<ZscObjectEffect>,
}

pub struct ZscObjectPart {
    pub mesh_id: u16,
    pub material_id: u16,
    pub position: Vec3<f32>,
    pub rotation: Vec4<f32>,  // Stored as w,x,y,z
    pub scale: Vec3<f32>,
    pub bone_index: Option<u16>,
    pub dummy_index: Option<u16>,
    pub parent: Option<u16>,
    pub collision_shape: Option<ZscCollisionShape>,
    pub collision_flags: ZscCollisionFlags,
    pub animation_path: Option<VfsPathBuf>,
}
```

### Binary Layout

```
--      2       Mesh count (u16)
Per-mesh:
  --    var     Path (null-terminated string)

--      2       Material count (u16)
Per-material:
  --    var     Path (null-terminated string)
  --    2       is_skin (u16, != 0)
  --    2       alpha_enabled (u16, != 0)
  --    2       two_sided (u16, != 0)
  --    2       alpha_test_enabled (u16, != 0)
  --    2       alpha_ref (u16, / 256.0 for f32)
  --    2       z_test_enabled (u16, != 0)
  --    2       z_write_enabled (u16, != 0)
  --    2       blend_mode (u16: 0=Normal, 1=Lighten)
  --    2       specular_enabled (u16, != 0)
  --    4       alpha (f32)
  --    2       glow_type (u16)
  --    12      glow_color (Vec3 f32)

--      2       Effect count (u16)
Per-effect:
  --    var     Path (null-terminated string)

--      2       Object count (u16)
Per-object:
  --    12      Skip (3 x u32)
  --    2       Part count (u16)
  
  Per-part:
    --  2       mesh_id (u16)
    --  2       material_id (u16)
    
    Property loop:
      --  1     property_id (u8)
      --  1     size (u8)
      
      Properties:
        1: position (Vec3 f32)
        2: rotation (w, x, y, z - 4 x f32)
        3: scale (Vec3 f32)
        4: skip 16 bytes
        5: bone_index (u16)
        6: dummy_index (u16)
        7: parent (u16, 0 = None, else id - 1)
        29: collision (u16, bits 0-2 = shape, rest = flags)
        30: animation_path (fixed-length string)
        31-32: skip 2 bytes
        0: end of properties
  
  --    2       Effect count (u16)
  Per-effect:
    --  2       effect_id (u16)
    --  2       effect_type (u16: 0=Normal, 1=DayNight, 2=LightContainer)
    
    Property loop (same as part):
      1: position
      2: rotation
      3: scale
      7: parent
      0: end
  
  --    24      Skip (6 x u32)
```

### Collision Shape Values

| Bits 0-2 | Shape |
|----------|-------|
| 0 | None |
| 1 | Sphere |
| 2 | AxisAlignedBoundingBox |
| 3 | ObjectOrientedBoundingBox |
| 4 | Polygon |

### Collision Flags

```rust
bitflags::bitflags! {
    pub struct ZscCollisionFlags: u32 {
        const NOT_MOVEABLE = (1 << 3);       // 0x0008
        const NOT_PICKABLE = (1 << 4);       // 0x0010
        const HEIGHT_ONLY = (1 << 5);        // 0x0020
        const NOT_CAMERA_COLLISION = (1 << 6); // 0x0040
        const PASSTHROUGH = (1 << 7);        // 0x0080
    }
}
```

---

## ZON Format - Zone Files

Location: [`zon.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\zon.rs)

### Data Structure

```rust
pub struct ZonFile {
    pub grid_per_patch: f32,
    pub grid_size: f32,
    pub event_positions: Vec<(String, Vec3<f32>)>,
    pub tile_textures: Vec<String>,
    pub tiles: Vec<ZonTile>,
}

pub struct ZonTile {
    pub layer1: u32,
    pub layer2: u32,
    pub offset1: u32,
    pub offset2: u32,
    pub blend: bool,
    pub rotation: ZonTileRotation,
}
```

### Block-Based Structure

ZON files use a block-based structure with a header table:

```
--      4       Block count (u32)

Per-block header:
  --    4       Block type (u32)
  --    4       Block offset (u32)
```

### Block Types

| Value | Type |
|-------|------|
| 0 | ZoneInfo |
| 1 | EventPositions |
| 2 | Textures |
| 3 | Tiles |
| 4 | Economy |

### ZoneInfo Block

```
--      12      Skip
--      4       grid_per_patch (u32 as f32)
--      4       grid_size (f32)
--      8       Skip
```

### EventPositions Block

```
--      4       Object count (u32)
Per-object:
  --    12      Position (Vec3 f32)
  --    1+len   Name (u8 length-prefixed string)
```

### Textures Block

```
--      4       Texture count (u32)
Per-texture:
  --    1+len   Path (u8 length-prefixed string)
```

### Tiles Block

```
--      4       Tile count (u32)
Per-tile:
  --    4       layer1 (u32)
  --    4       layer2 (u32)
  --    4       offset1 (u32)
  --    4       offset2 (u32)
  --    4       blend (u32, != 0)
  --    4       rotation (u32 enum)
  --    4       Skip
```

### Tile Rotation Values

| Value | Rotation |
|-------|----------|
| 0 | Unknown |
| 1 | None |
| 2 | FlipHorizontal |
| 3 | FlipVertical |
| 4 | Flip |
| 5 | Clockwise90 |
| 6 | CounterClockwise90 |

---

## HIM Format - Heightmap Files

Location: [`him.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\him.rs)

### Data Structure

```rust
pub struct HimFile {
    pub width: u32,
    pub height: u32,
    pub heights: Vec<f32>,
}
```

### Binary Layout

```
--      4       Width (u32)
--      4       Height (u32)
--      8       Skip
--      4*w*h   Heights (f32 per point, row-major order)
```

### Access Method

```rust
pub fn get_clamped(&self, x: i32, y: i32) -> f32 {
    let x = i32::clamp(x, 0, self.width as i32 - 1) as usize;
    let y = i32::clamp(y, 0, self.height as i32 - 1) as usize;
    self.heights[y * self.width as usize + x]
}
```

---

## TIL Format - Tile Map Files

Location: [`til.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\til.rs)

### Data Structure

```rust
pub struct TilFile {
    pub width: u32,
    pub height: u32,
    pub tiles: Vec<u32>,
}
```

### Binary Layout

```
--      4       Width (u32)
--      4       Height (u32)
Per-tile:
  --    3       Skip
  --    4       Tile index (u32)
```

---

## IFO Format - Map Object Placement

Location: [`ifo.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\ifo.rs)

### Data Structure

```rust
pub struct IfoFile {
    pub monster_spawns: Vec<IfoMonsterSpawnPoint>,
    pub npcs: Vec<IfoNpc>,
    pub event_objects: Vec<IfoEventObject>,
    pub animated_objects: Vec<IfoObject>,
    pub collision_objects: Vec<IfoObject>,
    pub deco_objects: Vec<IfoObject>,
    pub cnst_objects: Vec<IfoObject>,
    pub effect_objects: Vec<IfoEffectObject>,
    pub sound_objects: Vec<IfoSoundObject>,
    pub water_size: f32,
    pub water_planes: Vec<(Vec3<f32>, Vec3<f32>)>,
    pub warps: Vec<IfoObject>,
}

pub struct IfoObject {
    pub object_name: String,
    pub minimap_position: Vec2<u32>,
    pub object_type: u32,
    pub object_id: u32,
    pub warp_id: u16,
    pub event_id: u16,
    pub position: Vec3<f32>,
    pub rotation: Quat4<f32>,  // XYZW order
    pub scale: Vec3<f32>,
}
```

### Block Types

| Value | Type |
|-------|------|
| 0 | DeprecatedMapInfo |
| 1 | DecoObject |
| 2 | Npc |
| 3 | CnstObject |
| 4 | SoundObject |
| 5 | EffectObject |
| 6 | AnimatedObject |
| 7 | DeprecatedWater |
| 8 | MonsterSpawn |
| 9 | WaterPlanes |
| 10 | Warp |
| 11 | CollisionObject |
| 12 | EventObject |

### IfoObject Binary Layout

```
--      1+len   Object name (u8 length-prefixed)
--      2       Warp ID (u16)
--      2       Event ID (u16)
--      4       Object type (u32)
--      4       Object ID (u32)
--      4       Minimap X (u32)
--      4       Minimap Y (u32)
--      16      Rotation (Quat4 f32: x, y, z, w - XYZW order!)
--      12      Position (Vec3 f32)
--      12      Scale (Vec3 f32)
```

---

## CHR Format - Character Definition Files

Location: [`chr.rs`](C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\chr.rs)

### Data Structure

```rust
pub struct ChrFile {
    pub skeleton_files: Vec<String>,
    pub motion_files: Vec<String>,
    pub effect_files: Vec<String>,
    pub npcs: HashMap<u16, NpcModelData>,
}

pub struct NpcModelData {
    pub name: String,
    pub skeleton_index: u16,
    pub model_ids: Vec<u16>,
    pub motion_ids: Vec<(u16, u16)>,  // (motion_id, motion_files index)
    pub effect_ids: Vec<(u16, u16)>,  // (motion_id, effect_files index)
}
```

### Binary Layout

```
--      2       Skeleton file count (u16)
Per-skeleton:
  --    var     Path (null-terminated)

--      2       Motion file count (u16)
Per-motion:
  --    var     Path (null-terminated)

--      2       Effect file count (u16)
Per-effect:
  --    var     Path (null-terminated)

--      2       Character count (u16)
Per-character (indexed by loop counter):
  --    1       Exists flag (u8, skip if 0)
  --    2       Skeleton index (u16)
  --    var     Name (null-terminated)
  --    2       Mesh count (u16)
  Per-mesh:
    --  2       Model ID (u16)
  --    2       Motion count (u16)
  Per-motion:
    --  2       Motion ID (u16)
    --  2       Motion file index (u16)
  --    2       Effect count (u16)
  Per-effect:
    --  2       Motion ID (u16)
    --  2       Effect file index (u16)
```

---

## Coordinate System Transformations

### Rose Online Coordinate System

Rose uses a left-handed coordinate system:
- **X**: East/West
- **Y**: Up/Down (height)
- **Z**: North/South

### Bevy Coordinate System

Bevy uses a right-handed coordinate system with Y-up:
- **X**: East/West
- **Y**: Up/Down
- **Z**: Forward/Backward

### Transformation Applied

The Rust implementation applies this transformation:

```rust
// Position: (x, y, z) -> (x, z, -y)
Vec3::new(position.x, position.z, -position.y) / 100.0

// Rotation: (x, y, z, w) -> (x, z, -y, w)
Quat::from_xyzw(rotation.x, rotation.z, -rotation.y, rotation.w)

// Scale: (x, y, z) -> (x, z, y)
Vec3::new(scale.x, scale.z, scale.y)
```

### Scale Factor

All positions are divided by 100.0 to convert from Rose units to Bevy meters:
```rust
/ 100.0
```

### Quaternion Order Differences

| Format | Quaternion Order |
|--------|------------------|
| ZMD (skeleton) | WXYZ (w first) |
| ZMO (animation) | WXYZ (w first) |
| IFO (objects) | XYZW (x first) |
| ZSC (parts) | WXYZ (w first) |

---

## Bevy 0.11 Mesh Generation

### ZMS to Bevy Mesh Conversion

Location: [`zms_asset_loader.rs`](C:\Users\vicha\RustroverProjects\exjam-rose-offline-client\rose-offline-client\src\zms_asset_loader.rs)

```rust
fn load<'a>(bytes: &'a [u8], load_context: &'a mut LoadContext) -> BoxedFuture<'a, Result<(), anyhow::Error>> {
    Box::pin(async move {
        let mut zms: ZmsFile = RoseFile::read(bytes.into(), &Default::default())?;
        let mut mesh = Mesh::new(PrimitiveTopology::TriangleList);
        
        // Set indices
        mesh.set_indices(Some(Indices::U16(zms.indices)));
        
        // Transform and add normals
        if !zms.normal.is_empty() {
            for vert in zms.normal.iter_mut() {
                let y = vert[1];
                vert[1] = vert[2];
                vert[2] = -y;
            }
            mesh.insert_attribute(Mesh::ATTRIBUTE_NORMAL, zms.normal);
        }
        
        // Transform and add positions
        if !zms.position.is_empty() {
            for vert in zms.position.iter_mut() {
                let y = vert[1];
                vert[1] = vert[2];
                vert[2] = -y;
            }
            mesh.insert_attribute(Mesh::ATTRIBUTE_POSITION, zms.position);
        }
        
        // Add other attributes (tangent, color, bone weights/indices, UVs)
        // ...
        
        load_context.set_default_asset(LoadedAsset::new(mesh));
        Ok(())
    })
}
```

### Bevy Vertex Attributes Mapping

| ZMS Field | Bevy Attribute |
|-----------|----------------|
| position | `Mesh::ATTRIBUTE_POSITION` |
| normal | `Mesh::ATTRIBUTE_NORMAL` |
| tangent | `Mesh::ATTRIBUTE_TANGENT` |
| color | `Mesh::ATTRIBUTE_COLOR` |
| bone_weights | `Mesh::ATTRIBUTE_JOINT_WEIGHT` |
| bone_indices | `Mesh::ATTRIBUTE_JOINT_INDEX` (as Uint16x4) |
| uv1 | `Mesh::ATTRIBUTE_UV_0` |
| uv2 | `MESH_ATTRIBUTE_UV_1` |
| uv3 | `MESH_ATTRIBUTE_UV_2` |
| uv4 | `MESH_ATTRIBUTE_UV_3` |

### Terrain Mesh Generation

Location: [`zone_loader.rs`](C:\Users\vicha\RustroverProjects\exjam-rose-offline-client\rose-offline-client\src\zone_loader.rs)

```rust
fn spawn_terrain(...) -> Entity {
    // For each tile (16x16 tiles per block)
    for tile_x in 0..16 {
        for tile_y in 0..16 {
            // Each tile is 5x5 vertices
            for y in 0..5 {
                for x in 0..5 {
                    let heightmap_x = x + tile_x * 4;
                    let heightmap_y = y + tile_y * 4;
                    let height = heightmap.get_clamped(heightmap_x, heightmap_y) / 100.0;
                    
                    // Calculate normal from neighboring heights
                    let normal = Vec3::new(
                        (height_l - height_r) / 2.0,
                        1.0,
                        (height_t - height_b) / 2.0,
                    ).normalize();
                    
                    positions.push([tile_offset_x + x as f32 * 2.5, height, tile_offset_y + y as f32 * 2.5]);
                    normals.push([normal.x, normal.y, normal.z]);
                }
            }
        }
    }
}
```

### Skeleton Spawning

```rust
fn spawn_skeleton(commands: &mut Commands, model_entity: Entity, skeleton: &ZmdFile, ...) -> SkinnedMesh {
    let mut bind_pose = Vec::with_capacity(skeleton.bones.len());
    let mut bone_entities = Vec::with_capacity(skeleton.bones.len());
    
    for bone in skeleton.bones.iter().chain(skeleton.dummy_bones.iter()) {
        // Apply coordinate transformation
        let position = Vec3::new(bone.position.x, bone.position.z, -bone.position.y) / 100.0;
        let rotation = Quat::from_xyzw(bone.rotation.x, bone.rotation.z, -bone.rotation.y, bone.rotation.w);
        
        let transform = Transform::default()
            .with_translation(position)
            .with_rotation(rotation);
        
        bind_pose.push(transform);
        bone_entities.push(commands.spawn((Visibility::default(), transform, ...)).id());
    }
    
    // Apply parent-child hierarchy
    transform_children(skeleton, &mut bind_pose, 0);
    
    // Calculate inverse bind poses
    let inverse_bind_pose: Vec<Mat4> = bind_pose.iter()
        .map(|x| x.compute_matrix().inverse())
        .collect();
    
    SkinnedMesh {
        inverse_bindposes: skinned_mesh_inverse_bindposes_assets.add(SkinnedMeshInverseBindposes::from(inverse_bind_pose)),
        joints: bone_entities,
    }
}
```

---

## Key Algorithmic Approaches

### 1. Terrain Height Interpolation

```rust
pub fn get_terrain_height(&self, x: f32, y: f32) -> f32 {
    let block_x = x / (16.0 * self.zon.grid_per_patch * self.zon.grid_size);
    let block_y = 65.0 - (y / (16.0 * self.zon.grid_per_patch * self.zon.grid_size));
    
    if let Some(heightmap) = self.blocks.get(block_index).and_then(|b| b.as_ref()).map(|b| &b.him) {
        let tile_x = (heightmap.width - 1) as f32 * block_x.fract();
        let tile_y = (heightmap.height - 1) as f32 * block_y.fract();
        
        // Bilinear interpolation
        let height_00 = heightmap.get_clamped(tile_x, tile_y);
        let height_01 = heightmap.get_clamped(tile_x, tile_y + 1);
        let height_10 = heightmap.get_clamped(tile_x + 1, tile_y);
        let height_11 = heightmap.get_clamped(tile_x + 1, tile_y + 1);
        
        let weight_x = tile_x.fract();
        let weight_y = tile_y.fract();
        
        let height_y0 = height_00 * (1.0 - weight_x) + height_10 * weight_x;
        let height_y1 = height_01 * (1.0 - weight_x) + height_11 * weight_x;
        
        height_y0 * (1.0 - weight_y) + height_y1 * weight_y
    } else {
        0.0
    }
}
```

### 2. Animation Channel Sampling

```rust
impl ZmoAsset {
    pub fn sample_translation(&self, channel_id: usize, fract: f32, current_frame: usize, next_frame: usize) -> Option<Vec3> {
        let current = self.get_translation(channel_id, current_frame);
        let next = self.get_translation(channel_id, next_frame);
        
        if let (Some(current), Some(next)) = (current, next) {
            Some(current.lerp(next, fract))  // Linear interpolation
        } else {
            None
        }
    }
    
    pub fn sample_rotation(&self, channel_id: usize, fract: f32, current_frame: usize, next_frame: usize) -> Option<Quat> {
        let current = self.get_rotation(channel_id, current_frame);
        let next = self.get_rotation(channel_id, next_frame);
        
        if let (Some(current), Some(next)) = (current, next) {
            Some(current.slerp(next, fract))  // Spherical linear interpolation
        } else {
            None
        }
    }
}
```

### 3. Bone Hierarchy Transformation

```rust
fn transform_children(skeleton: &ZmdFile, bone_transforms: &mut Vec<Transform>, bone_index: usize) {
    for (child_id, child_bone) in skeleton.bones.iter().enumerate() {
        if child_id == bone_index || child_bone.parent as usize != bone_index {
            continue;
        }
        
        // Apply parent transform to child
        bone_transforms[child_id] = bone_transforms[bone_index] * bone_transforms[child_id];
        
        // Recursively transform children
        transform_children(skeleton, bone_transforms, child_id);
    }
}
```

---

## Cross-Reference Validation Points

When comparing the Python implementation against this Rust reference, verify:

### ZMS Format

1. **Magic header detection** - Check for all 4 versions (5, 6, 7, 8)
2. **Format flag parsing** - Bit positions match exactly
3. **Version 5/6 scale factor** - Positions must be divided by 100.0
4. **Version 5/6 vertex IDs** - Each vertex has a u32 ID prefix
5. **Bone index translation** - Use bone table lookup, not direct indices
6. **Index type conversion** - v5/6 uses u32 (cast to u16), v7/8 uses u16 directly

### ZMD Format

1. **Quaternion order** - WXYZ (w first in file)
2. **Version 2 dummy bones** - No rotation data
3. **Parent index** - Root bones have parent == own index

### ZMO Format

1. **Channel type values** - Must match bitmask values
2. **Extended footer** - Check for "EZMO" or "3ZMO" at end
3. **Frame events** - Attack frame detection logic

### ZSC Format

1. **Property-based parsing** - Parts and effects use property ID loops
2. **Rotation order** - WXYZ for parts
3. **Parent indexing** - 0 = None, else id - 1

### Coordinate Transformation

1. **Position**: `(x, y, z) -> (x, z, -y) / 100.0`
2. **Rotation**: `(x, y, z, w) -> (x, z, -y, w)`
3. **Scale**: `(x, y, z) -> (x, z, y)`

### String Encoding

1. **Default**: Try UTF-8, fall back to EUC-KR
2. **Wide strings**: UTF-16 LE

---

## Summary

This Rust reference implementation provides definitive ground truth for:

1. **Binary format layouts** - Exact byte offsets and data types
2. **Version handling** - Different parsing logic for format versions
3. **Coordinate systems** - Transformation from Rose to Bevy coordinates
4. **Data structures** - Field types and ordering
5. **String encoding** - EUC-KR with UTF-8 fallback

Key implementation details that must be replicated in Python:
- Bone table lookup for ZMS bone indices
- Version-dependent scale factor (100.0 for v5/v6)
- Quaternion order differences (WXYZ vs XYZW)
- Property-based parsing for ZSC objects
- Block-based structure for ZON and IFO files
- Extended footer detection for ZMO animations
