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
   the ZON's own directory, plus the matching TIL/IFO/LIT files.

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

`src/zone_loader.rs` and `src/zone_loader/spawning/terrain.rs`:

- Block world size = `16 * grid_per_patch * grid_size` (typically 160 units;
  JDT01: grid_size = 250 cm, grid_per_patch = 0.04).
- Block origin: `offset_x = 160.0 * block_x`, `offset_y = 160.0 * (65 - block_y)`.
  **block_y = 0 is the north/top of the map (y axis inverted).**
- Entity transform: `(offset_x - 5200.0, 0.0, -offset_y + 5200.0)` (5200 cm = 52 m).
- Each block is 16x16 terrain tiles; each tile is a 4x4 quad grid -> 65x65 HIM
  samples per block.
- HIM heights are centimeters: divided by 100.0 to get meters.
- Heightmap sample spacing: 2.5 m (160 units / 64 quads).
- `get_terrain_height(x, y)`: `block_x = x / block_size`,
  `block_y = 65.0 - (y / block_size)`, then bilinear interpolation over the
  HIM (`get_clamped`).
- `get_tile_index()`: same mapping, returns `tile.layer2 + tile.offset2`.

## Coordinate transforms (Rose -> Bevy)

Rose positions are in centimeters in a Y-up (Z-up in some docs) left-handed
system; Bevy is right-handed Y-up meters:

```
position: (x, y, z) -> (x, z, -y) / 100.0
rotation: (x, y, z, w) -> (x, z, -y, w)
scale:    (x, y, z) -> (x, z, y)
```

## Consumers of terrain data

Collision, boat spawn, flight movement, footstep sounds, seasonal effects and
the game connection system all call `get_terrain_height()`/`get_tile_index()`
and rely on the 0.0/0 fallbacks for missing blocks.

## Pitfalls reference

The project keeps its own `pitfalls/zone-loading.md` in
`rose-offline-client` - consult it when debugging zone issues.
