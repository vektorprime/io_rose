# rose-offline-client: Zone Loading Architecture

Reference project:
`rose-offline-client` (Bevy 0.18, workspace `rose-offline`; checkouts at
`$ROSE_CLIENT_3DDATA`'s parent or `%USERPROFILE%\RustroverProjects`).

## Workspace layout

```
rose-offline/                       workspace root (sibling crates)
  rose-file-readers/                binary parsers: ZonFile, HimFile, TilFile,
                                    IfoFile, LitFile, ZmsFile, ZmdFile, ZmoFile,
                                    ZscFile, ChrFile
  rose-data-irose/                  game data from STB files (ZoneList, etc.)
rose-offline-client/                the Bevy game
  src/zone_loader/                  zone loading + spawning
```

## Zone discovery

- `rose-data-irose/src/zone_database.rs` reads `3DDATA/STB/LIST_ZONE.STB` and
  builds `ZoneListEntry { zon_file_path, zsc_cnst_path, zsc_deco_path, ... }`.
  The ZON path is STB column 1, so ZONs may live anywhere (e.g.
  `3DDATA/MAPS/JUNON/ZON/JDT01.ZON` or `3DDATA/MAPS/JUNON/JDT01/JDT01.ZON`).
- Synthetic ocean zone id 200 is hardcoded to
  `3DDATA/MAPS/OCEAN/OCEAN.ZON`.

## Loading pipeline

`src/zone_loader/loading.rs`:

1. `load_zone_direct()` - the active runtime path (dispatched by
   `zone_loader_system` in `systems.rs`). Reads bytes with
   `read_bytes_with_priority_sync`: **real filesystem first, VFS fallback**
   (map-editor saved files override VFS data).
2. Parses the ZON and both ZSC files (CNST + DECO).
3. `load_block_files_direct()` loads every `{block_x}_{block_y}.HIM` file from
   the ZON's own directory, plus the matching TIL/IFO/LIT files. The Blender
   addon (`import_map.py:684-712`) loads HIM/TIL/IFO only and ignores LIT.

### The 64x64 block grid

- Zones are a fixed 64x64 grid of blocks (block_x, block_y both in 0..64).
- Blocks are stored row-major, y-major:
  `blocks[block_x + block_y * 64]` as `Vec<Option<Box<ZoneLoaderBlock>>>`
  (4096 slots).
- `ZoneLoaderBlock` requires `him: HimFile`; `til`/`ifo`/`lit_*` are `Option<T>`.

### Sparse tiles are explicitly supported

- A missing HIM does not fail zone load: the block slot stays `None` and the
  block is skipped ("Blocks without HIM files will be skipped").
- `spawn_zone` skips `None` blocks; the terrain simply has holes.
- `get_terrain_height()` returns `0.0` and `get_tile_index()` returns `0` for
  missing blocks (players sink/fall over holes).
- The map editor's `bootstrap_default_zone_blocks()` writes all 4096 flat
  `{x}_{y}.HIM/.TIL/.IFO` scaffolds so custom zones aren't empty - confirming
  the engine renders sparse maps with holes.

## World-space mapping (terrain)

`src/zone_loader.rs` and `src/zone_loader/spawning/terrain.rs` (Bevy Y-up
target). Do not conflate with the Blender Z-up mapping below:

- Block world size = `64 * grid_size / 100` meters (== `16 * grid_per_patch
  * grid_size / 100`; JDT01: grid_size = 250 cm -> 160 m).
- Bevy block origin: `offset_x = 160.0 * block_x`,
  `offset_y = 160.0 * (65 - block_y)`.
  **In the Bevy path block_y = 0 is the north/top of the map (y axis inverted).**
- Bevy entity transform: `(offset_x - 5200.0, 0.0, -offset_y + 5200.0)`,
  all in meters (5200 m offset, not 5200 cm).
- Each block is 16x16 TIL patches; each patch is a 4x4 quad grid -> 65x65 HIM
  samples for a full JDT01 block (HIM size comes from the file header,
  `rose/him.py:27-28`, not fixed).
- HIM heights are centimeters: divided by 100.0 to get meters.
- Heightmap sample spacing: 2.5 m (160 m / 64 quads).
- `get_terrain_height(x, y)`: `block_x = x / block_size`,
  `block_y = 65.0 - (y / block_size)`, then bilinear interpolation over the
  HIM (`get_clamped`).
- `get_tile_index()`: same mapping, returns `tile.layer2 + tile.offset2`.
  The Blender terrain shader instead uses the full pair
  `(layer1+offset1, layer2+offset2)` (`rose/utils.py:398-405`).

Blender Z-up mapping (`import_map.py:742-743,753-756`,
`export_zone.py:56-63`): block corner = `160 * block_coord - 5200` m on both
axes, no `65 -` flip. Block lookup is
`(floor((X + 5200) / 160), floor((Y + 5200) / 160))`. The `65 - block_y`
flip only applies to the Bevy-converter variants
(`import_combined_zone.py:30-37`, `import_converted_terrain.py:378-386`).

## Coordinate transforms

Rose file data are centimeters, right-handed Z-up
(`rose/utils.py:314-315`: X = right, Y = forward, Z = up).

- Rose -> Bevy (right-handed Y-up meters):
```
position: (x, y, z) -> (x, z, -y) / 100.0
rotation: (x, y, z, w) -> (x, z, -y, w)
scale:    (x, y, z) -> (x, z, y)
```
- Rose -> Blender (`import_map.py`, `rose/utils.py:306-327`,
  `export_zone.py:72-85`; both Z-up, no axis swap):
```
position: (x, y, z) cm -> (x, -y, z) / 100.0 m
rotation: IFO XYZW (x, -y, z, w) <-> Blender WXYZ (w, x, y, z)
scale:    unchanged
```

## Consumers of terrain data

Collision, boat spawn, flight movement, footstep sounds, seasonal effects and
the game connection system all call `get_terrain_height()`/`get_tile_index()`
and rely on the 0.0/0 fallbacks for missing blocks.

## Pitfalls reference

The project keeps its own `pitfalls/zone-loading.md` in
`rose-offline-client` - consult it when debugging zone issues.
