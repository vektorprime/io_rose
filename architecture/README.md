# io_rose Architecture Notes

Documentation of the Rose Online asset architecture and how this Blender
addon relates to the Rust reference implementation
(`rose-offline-client`, Bevy 0.18, workspace `rose-offline`).

## Contents

| Document | Scope |
|----------|-------|
| [rose-file-formats.md](rose-file-formats.md) | Binary layouts of ZON / HIM / TIL / IFO (ground truth: `rose-file-readers` crate). |
| [rose-offline-client-zone-loading.md](rose-offline-client-zone-loading.md) | How the Bevy client loads zones: 64x64 block grid, sparse tiles, world-space mapping, coordinate transforms. |
| [blender-importer.md](blender-importer.md) | How `import_map.py` imports `.zon` maps: pipeline, mesh generation (main quads only, no stitching), materials, pitfalls; ZMS import/export round trip (strips, material counts, normals, bounding box, skin weights). |
| [zone-exporter.md](zone-exporter.md) | The zone save feature: byte-exact writers, round-trip metadata, diff-based IFO/HIM export, backups, new-mesh flow. |

## Key takeaways from the 2026-07-31 session (sparse-tile crash)

1. A `.zon` file describes a **64x64 zone grid**, but the tiles on disk are a
   **sparse subset** (JDT01 ships only 16 of 4096 possible blocks).
2. The Rust client explicitly supports missing tiles: each block is
   `Option<Box<ZoneLoaderBlock>>`, missing blocks are skipped at spawn, and
   height/tile lookups fall back to `0.0` / `0`.
3. The Blender importer previously built inter-tile stitch faces and assumed
   every neighbor tile exists, crashing with
   `TypeError: 'NoneType' object is not subscriptable` on sparse maps.
4. Stitching was then removed entirely (`import_map.py:780-783`): faces are
   main-grid quads only, and both the generation loop and the
   material-index loop skip missing tiles (`if not tiles.hims[ty][tx]`).
   The old `has_x_neighbor` / `has_y_neighbor` / `has_xy_neighbor` guards
   no longer exist - do not re-add them.

## 2026-08-01 session (assets not on terrain)

Terrain and IFO objects now share the client's single absolute world space:
terrain block corner = `160 * block_coord - 5200` m, objects at
`(x, -y, z) / 100`, no world offset. Details in
[blender-importer.md](blender-importer.md).

## 2026-09-30 session (ZMS export: stale strips, normals)

1. Strips and material face counts are only written while the exported
   triangle list matches the imported one (counts + index CRC stored on
   import); otherwise they are dropped with an INFO report.
2. In Blender 4.5 `vertex.normal` already reflects custom normals (mean of
   the corners), but custom normals are int16-encoded and ~2.6% of client
   normals cannot be represented. The exporter averages corner normals per
   exported (vertex, uv) key, transforms them explicitly for world export,
   and writes the exact file normal (stashed in `zms_normal`) for every
   untouched vertex.
3. Degenerate triangles crashed `normals_split_custom_set`; the importers
   skip them. Details in [blender-importer.md](blender-importer.md),
   "ZMS round trip".
