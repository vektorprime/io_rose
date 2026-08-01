# io_rose Architecture Notes

Documentation of the Rose Online asset architecture and how this Blender
addon relates to the Rust reference implementation
(`rose-offline-client`, Bevy 0.18, workspace `rose-offline`).

## Contents

| Document | Scope |
|----------|-------|
| [rose-file-formats.md](rose-file-formats.md) | Binary layouts of ZON / HIM / TIL / IFO (ground truth: `rose-file-readers` crate). |
| [rose-offline-client-zone-loading.md](rose-offline-client-zone-loading.md) | How the Bevy client loads zones: 64x64 block grid, sparse tiles, world-space mapping, coordinate transforms. |
| [blender-importer.md](blender-importer.md) | How `import_map.py` imports `.zon` maps: pipeline, mesh generation, inter-tile stitching, materials, pitfalls. |
| [zone-terrain-transparency-issue.md](zone-terrain-transparency-issue.md) | Investigation of the "black unblended textures" report: DXT3 alpha masks, blend verification, scene lighting. |
| [zone-exporter.md](zone-exporter.md) | The zone save feature: byte-exact writers, round-trip metadata, diff-based IFO/HIM export, backups, new-mesh flow. |

## Key takeaways from the 2026-07-31 session (sparse-tile crash)

1. A `.zon` file describes a **64x64 zone grid**, but the tiles on disk are a
   **sparse subset** (JDT01 ships only 16 of 4096 possible blocks).
2. The Rust client explicitly supports missing tiles: each block is
   `Option<Box<ZoneLoaderBlock>>`, missing blocks are skipped at spawn, and
   height/tile lookups fall back to `0.0` / `0`.
3. The Blender importer previously assumed every neighbor tile exists when
   stitching inter-tile faces, crashing with
   `TypeError: 'NoneType' object is not subscriptable` on sparse maps.
4. The fix (in `import_map.py`) guards every neighbor access with
   `has_x_neighbor` / `has_y_neighbor` / `has_xy_neighbor` in **both** the
   face-stitching loop and the material-index loop, keeping face counts aligned.

## 2026-08-01 session (assets not on terrain)

Terrain and IFO objects now share the client's single absolute world space:
terrain block corner = `160 * block_coord - 5200` m, objects at
`(x, -y, z) / 100`, no world offset. Details in
[blender-importer.md](blender-importer.md).
