# Zone Exporter: Save Edited Zones Back to the Game

Covers `export_zone.py` and the round-trip machinery in `rose/`
(`ifo.py`, `zon.py`, `him.py`, `til.py`, `zsc.py`, `dds.py`).

## Goal

Edit an imported zone in Blender (move/rotate/scale objects, sculpt
terrain, delete objects, add new meshes) and write the changes back to
the game data so the client loads them. The client reads real files
first (VFS fallback second), so writing into the zone's own directory
is picked up immediately.

## What gets written

| Change in Blender | File written | Writer |
|---|---|---|
| Object moved/rotated/scaled | `{bx}_{by}.IFO` (only its block) | `rose/ifo.py` `save()` |
| Object deleted (marked) | `{bx}_{by}.IFO` | same |
| Object relocated across blocks | old + new `{bx}_{by}.IFO` | same |
| Terrain sculpted | `{bx}_{by}.HIM` (only changed blocks) | `rose/him.py` `save()` |
| New mesh added as zone object | `.ZMS` + ZSC append + optional `.DDS` | `export_zms.py` / `rose/zsc.py` / `rose/dds.py` |

The ZON file itself is never rewritten by this exporter (spawns,
economy, texture palette are out of scope for now).

## Safety model

- **Byte-exact writers**: every parser in `rose/` can re-serialize the
  file byte-for-byte (`tests/test_zone_roundtrip.py` verifies 49 files).
  Unparsed IFO block types (DeprecatedMapInfo, DeprecatedWater) and
  trailing data (e.g. the `"Quad\0"` HIM footer, orphan spawn-path data
  after the last IFO block) are preserved verbatim. Strings read with
  the EUC-KR fallback keep their raw bytes so Korean names round-trip
  losslessly.
- **Diff-based**: the exporter re-reads the original IFO from disk,
  compares each object's current transform against the file (f32
  quantized, sign-normalized quaternions), and only rewrites blocks
  where something actually changed. A no-op save changes nothing.
- **Backup**: every file about to be overwritten is copied to
  `<zone_dir>/backup/<timestamp>/` first.
- **Ground truth**: the Rust map editor's save system
  (`rose-offline-client/src/map_editor/save/`) defined the IFO
  serialization layout; HIM/TIL writers match `coords.rs` exactly.

## Workflow

1. **Import** a zone (`File > Import > ROSE Map (.zon)`). The importer
   stamps every object empty with round-trip metadata:
   - `rose_block_x`, `rose_block_y` - which IFO file it came from
   - `rose_ifo_block` (`CNST`/`DECO`), `rose_ifo_index` - position in
     that block's object list
   - `rose_zsc_object_id`, `rose_zsc_path` - the ZSC definition
   - scene-level `rose_zone_file`, `rose_zone_dir`, `rose_3ddata_root`,
     `rose_cnst_zsc_path`, `rose_deco_zsc_paths`
   - the terrain mesh gets `rose_terrain = True`
2. **Edit**:
   - Move/rotate/scale the object empties (any collection).
   - Duplicate objects (Shift-D etc.): the copy keeps the original's
     metadata, so the exporter detects the double claim and appends the
     copy as a **new placement** (the first claimant keeps the original
     IFO record).
   - Sculpt the `ROSE_Terrain` mesh with Blender tools.
   - Delete objects: select them and run
     `File > Export > Mark Selected for Zone Deletion` (they are hidden;
     re-run to un-mark by clearing the scene's `rose_deleted_objects`).
   - Add a new object: select a mesh, run
     `File > Export > ROSE Object - Add Selected Mesh to Zone`
     (exports the mesh as `.ZMS`, appends a ZSC entry + optional
     texture `.DDS`, creates a placement empty at the origin; move the
     empty where you want it).
3. **Save** (`File > Export > ROSE Zone (.zon) - Save Edited Zone`).
   The report lists updated/added/deleted objects and rewritten
   terrain blocks. Reload the zone in the game to see changes.

## Coordinate model

Terrain and objects share one absolute world space in Blender:

- The terrain mesh lies in the **X/Y plane** with height in Z.
  Block corner = `160 * block_coord - 5200` m; the block for any world
  position is `(floor((X + 5200) / 160), floor((Y + 5200) / 160))`.
- IFO positions are centimeters: Blender `(X, Y, Z)` ->
  IFO `(X * 100, -Y * 100, Z * 100)` cm.
- IFO rotations are XYZW quaternions; Blender WXYZ with negated Y:
  Blender `(w, x, y, z)` -> IFO `(x, -y, z, w)`.
- Terrain heights: HIM stores f32 cm; the mesh stores m (Z = cm / 100).

## Import pitfalls discovered

- Setting `rotation_quaternion` on an object still in Euler
  `rotation_mode` is a **no-op** in Blender 4.x (the property stores the
  value but the transform ignores it). The importer now sets
  `rotation_mode = 'QUATERNION'` first; the exporter reads rotations
  from the mode-appropriate property (not `matrix_world`, which can be
  stale in background mode).
- Blender normalizes quaternions on assignment and Euler round-trips
  may negate them (`q` == `-q`). The exporter compares rotations
  sign-normalized and tolerates f32 ULP drift so untouched objects
  never trigger rewrites.

## Not yet implemented

- ZON block editing (spawns, economy) - the `save()` writer exists in
  `rose/zon.py`, no operator/UI yet.
- TIL tile reassignment (writer exists), MOV walkability, ZMD/ZMO
  animation export, DXT-compressed DDS (uncompressed RGBA8 is written;
  the client's loader accepts both).
- Editing non-DECO/CNST IFO blocks in the UI (NPCs, warps, monster
  spawns parse and round-trip but have no Blender representation).
