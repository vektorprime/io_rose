# Rose File Formats (Zone Assets)

Ground truth for these layouts is the `rose-file-readers` crate in the
`rose-offline` workspace:
`C:\Users\vicha\RustroverProjects\rose-offline\rose-file-readers\src\`.
The addon parsers live in `rose/` (`him.py`, `til.py`, `zon.py`, `ifo.py`).
All integers are little-endian. All floats are f32.

## File inventory of a zone directory (JDT01 example)

```
3DDATA/MAPS/JUNON/JDT01/
  JDT01.ZON          zone metadata (64x64 grid, textures, tiles)
  31_30.HIM/.TIL/.IFO/.MOV   one set per block, named {x}_{y}
  ...
  34_33.HIM/.TIL/.IFO/.MOV
  MINIMAP.DDS
```

The `.MOV` files are obstacle/walkability data (not used by this addon yet).

## ZON - Zone metadata

Parser: `rose/zon.py`, Rust: `zon.rs`.

Block-based structure. Header table first, then each block is parsed by
seeking to its offset:

```
-- 4       Block count (u32)
Per block header:
  -- 4     Block type (u32)
  -- 4     Block offset (u32)
```

| Value | Block | Contents (Python parser) |
|-------|-------|--------------------------|
| 0 | ZoneInfo | zone_type, width, length, grid_count, grid_size (f32, in cm), xcount, ycount, then width*length `Position` entries (used flag + x,y f32) |
| 1 | Spawns | count, then per spawn: position x, z, y (f32, z before y) + name (u8-length-prefixed) |
| 2 | Textures | count, then per texture: path (u8-length-prefixed). The list ends with a literal `"end"` sentinel entry - the Rust client stops loading at the first `"end"` (see `spawning.rs`), and the Python parser trims it. |
| 3 | Tiles | count, then per tile: layer1, layer2, offset1, offset2 (u32), blending (u32 != 0), rotation (u32), tile_type (u32) |
| 4 | Economy | name, is_underground, music path, sky path, economy rates |

Note the Rust parser skips 12 bytes at the start of ZoneInfo and reads only
`grid_per_patch` and `grid_size`; the Python parser reads the fields directly.
This is an existing discrepancy between the two implementations - the Python
parser works for the files in use (JDT01: zone_type=2, width=64, length=64,
grid_count=4, grid_size=250.0).

### Tile rotation values

| Value | Meaning |
|-------|---------|
| 0 | Unknown |
| 1 | None |
| 2 | FlipHorizontal |
| 3 | FlipVertical |
| 4 | Flip |
| 5 | Clockwise90 |
| 6 | CounterClockwise90 |

## HIM - Heightmap

Parser: `rose/him.py`, Rust: `him.rs`.

```
-- 4       Width (u32)     -> 65 for a full block
-- 4       Height (u32)    -> 65 for a full block (field called "length")
-- 8       Skip            (grid_count i32 + patch_scale f32)
-- 4*w*h   Heights (f32, row-major, values in centimeters)
```

A 65x65 HIM is a full block: 16x16 terrain tiles, each tile a 4x4 quad grid
(17x17 samples per tile, shared edge samples).

Rust accessor: `get_clamped(x, y)` clamps coordinates into `[0, w-1] x [0, h-1]`.

## TIL - Tile/texture map

Parser: `rose/til.py`, Rust: `til.rs`.

```
-- 4       Width (u32)     -> 16 for a full block
-- 4       Height (u32)    -> 16
Per tile:
  -- 3     Skip            (Python reads brush/tile_index/tile_set i8s; Rust skips)
  -- 4     Tile index (u32) -> index into the ZON Tiles block
```

Both implementations advance 7 bytes per tile, so parsing stays in sync. The
`tile` value indexes `zon.tiles[]`; the texture index is
`zon.tiles[i].layer1 + zon.tiles[i].offset1` (addon) /
`layer2 + offset2` (Rust `get_tile_index`). Whether layer1 or layer2 is the
correct base is an open discrepancy to verify visually.

## IFO - Object placement

Parser: `rose/ifo.py`, Rust: `ifo.rs`.

Block-based, same header-table pattern as ZON:

```
-- 4       Block count (u32)
Per block header:
  -- 4     Block type (u32)
  -- 4     Block offset (u32)
```

| Value | Block |
|-------|-------|
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

`IfoObject` layout:

```
-- 1+len   Object name (u8 length-prefixed)
-- 2       Warp ID (u16)
-- 2       Event ID (u16)
-- 4       Object type (u32)
-- 4       Object ID (u32)
-- 4       Minimap X (u32)
-- 4       Minimap Y (u32)
-- 16      Rotation (Quat4 f32: x, y, z, w - XYZW order)
-- 12      Position (Vec3 f32)
-- 12      Scale (Vec3 f32)
```

Quaternion orders differ per format: ZMD/ZMO/ZSC use WXYZ (w first), IFO uses
XYZW (x first).

## Related formats (brief)

- **ZSC** (scene/container): mesh paths (u16 count), materials (path + flags:
  is_skin, alpha_enabled, two_sided, alpha_test + alpha_ref/256, z_test,
  z_write, blend_mode, specular, alpha f32, glow type + color), effect paths,
  then objects with parts; parts use a property loop (`property_id u8,
  size u8, data`): 1=position, 2=rotation (WXYZ), 3=scale, 4=skip 16,
  5=bone_index, 6=dummy_index, 7=parent (0=None else id-1), 29=collision,
  30=animation_path, 0=end.
- **ZMS** (mesh): magic `ZMS0005`..`ZMS0008`. Flag bitmask: POSITION=2,
  NORMAL=4, COLOR=8, BONE_INDEX=16, BONE_WEIGHT=32, TANGENT=64, UV1..UV4 =
  128..1024. v5/6: u32 counts, per-vertex u32 ID, positions scaled by 100.0
  (must divide). v7/8: u16 counts, no vertex IDs.
- **ZMD** (skeleton): `ZMD0002`/`ZMD0003`, bones with parent index, name,
  position, rotation (WXYZ). v3 dummy bones carry rotation; v2 use identity.
- **ZMO** (animation): `ZMO0002`, fps/frames/channel count, channel types are
  a bitmask (1=Empty, 2=Position, 4=Rotation, 8=Normal, 16=Alpha, 32..256=UV1..4,
  512=Texture, 1024=Scale), optional "EZMO"/"3ZMO" extended footer with frame
  events and interpolation interval.

## String encoding

Try UTF-8 first, fall back to EUC-KR (Korean) - `decode_string_with_fallback`
in `rose/utils.py`, matching the Rust reference.
