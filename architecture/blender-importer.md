# Blender Addon: Map Import Pipeline (`import_map.py`)

This addon (`io_rose`, Blender 4.5) mirrors the Rust client's zone loading:
it reads the same `.zon`/`.him`/`.til`/`.ifo` files and rebuilds the terrain
mesh in Blender.

## Addon layout

```
io_rose/
  __init__.py               operator registration, menu items
  import_map.py             .zon map import (terrain + IFO objects)
  import_terrain.py         older terrain-only variant
  import_combined_zone.py   combined zone import variant
  import_converted_terrain.py
  import_zms.py / import_zms_zmd.py / import_zmd.py / import_zmo.py / import_zsc.py
  import_eft.py             .eft effect import (slots, meshes, particle preview)
  export_zms.py             mesh export
  export_zmo.py             animation export
  export_eft.py             effect export
  export_zone.py            zone save (IFO/HIM diff + new ZMS/ZSC/DDS)
  enhance_wings.py          back-slot wing batch enhancer
  test_zon.py               ZON parser self-test
  rose/                     format parsers (pure Python, no bpy):
    him.py til.py zon.py ifo.py zsc.py zms.py zmd.py zmo.py eft.py ptl.py dds.py utils.py
  architecture/             this documentation
```

`import_combined_zone.py` / `import_converted_terrain.py` are Bevy-converter
variants (Y-up converter output, `65 - block_y` flip); `import_map.py` /
`import_terrain.py` are the direct `.zon` path (Blender Z-up, no flip).
`import_zms/zmd/zmo/zsc/eft` have one operator each, see `__init__.py` menu
table. `export_zmo.py` exists (animation export).

Parsers depend only on `struct` and can be run/tested outside Blender.

## Import pipeline (execute)

1. **Locate 3DDATA root** by walking up from the `.zon` path
   (`MAPS/{PLANET}/{ZONE}/file.zon`).
2. **Load ZSC files**: `LIST_CNST_{zone_code}.ZSC` and all
   `LIST_DECO_*.ZSC` from the planet folder (used for IFO object models).
3. **Scan tile directory** (`zon_dir`) for `*.HIM` files; each
   `{x}_{y}.HIM` is a grid coordinate. Tiles present on disk are a **sparse
   subset** of the zone's 64x64 grid (JDT01: only 31_30..34_33 = 16 tiles).
4. **Load per tile**: HIM (heightmap), TIL (tile/texture map), IFO
   (objects; failures degrade gracefully to None).
5. **Generate terrain mesh**:
   - Vertices: one per HIM sample, in **absolute world coordinates matching
     the Rust client**: `block corner = 160.0 * block_coord - 5200.0` meters
     (block_size = 64 * grid_scale, world_origin = -32.5 * block_size),
     samples spaced `grid_scale = grid_size/100` apart. No per-tile offset
     accumulation and no Y negation: the client's terrain formula already
     folds in the Y flip so it aligns with the object conversion
     `(x, -y, z)/100`.
    - Main quads per tile: `(w-1) x (l-1)` faces.
    - No inter-tile stitch faces: tiles already abut exactly in absolute
      world space (edge vertices coincide), so stitched quads would be
      degenerate zero-area faces. This matches the Rust client
      (`terrain.rs`), which spawns separate blocks without stitching
      (`import_map.py:780-783`). Tile-to-tile stride is 64 samples
      (160 m), matching the client's block size - never 65, or adjacent
      tiles drift 2.5 m apart.
6. **Materials**: the TIL patch grid (16x16 patches, each covering a 4x4
   quad area) maps to ZON tiles with **two texture layers**. The addon
   replicates the game shader exactly:
   - One material per distinct `(layer1, layer2)` pair
   (`layer1+offset1`, `layer2+offset2`).
   - Node graph: `mix(layer1, layer2, layer2.alpha)` (DXT3 alpha is the
     splat mask), layer2 sampled through a rotation-adjusted UV map.
   - `UVMap`: patch-local 0..1 coords per face corner; `UVMap_rot`: same
     coords with the patch's ZON rotation (flip H/V, 90 deg) applied.
   - Material slots are assigned per face in face-append order.
7. **Spawn IFO objects** (CNST/DECO) using the ZSC files, cached materials
   and mesh instancing.

## Face ordering contract (critical!)

Faces are main-grid quads only (no stitch faces), in tile-major order
matching the generation loop (`import_map.py:834-835`). The material-index
pass must iterate tiles in exactly the same order and skip the same missing
tiles (`if not tiles.hims[ty][tx]: continue`), or `face_idx` drifts and
polygons get wrong material slots.

## Sparse tiles: history (2026-07-31) and current behavior

Original symptom (importing JDT01.ZON, old stitching code):

```
File "import_map.py", line 736, in execute
    v2 = next_indices[vy][0]
TypeError: 'NoneType' object is not subscriptable
```

Old root cause: the then-existing stitching loop used
`tiles.indices[y][x + 1]` and `tiles.indices[y + 1][x]` unconditionally with
`has_x_neighbor` / `has_y_neighbor` / `has_xy_neighbor` guards. On sparse
maps the neighbor tile never loaded, so its slot was `None`.

Current behavior: stitching was removed entirely (see above). Sparse maps
are handled by skipping missing tiles in both the face-generation loop and
the material-index loop. The Rust client likewise skips `None` blocks.

Verified: `tests/test_sparse_grid.py` (face/material count alignment on
sparse grids) and `tests/test_terrain_build.py` (full build, no stitch);
all face vertex indices valid.

## Assets on terrain: the 2026-08-01 coordinate fix

Symptom: `.zon` imports fine, but IFO objects (CNST/DECO) are offset from the
terrain (they stay aligned relative to each other).

Root cause: the addon placed the terrain at `+52 m` world offset with a
65-sample per-tile stride (162.5 m) and a negated Y, while objects were
placed at `(x, -y, z)/100 + 52 m`. The Rust client uses one shared absolute
space: terrain block corner = `160 * block_coord - 5200` meters and objects
at `(x, z, -y)/100` with **no extra offset** (verified against JDT01 DECO
data: e.g. an object at (-8714.8, 26820.7) cm must land on tile 31_30, whose
terrain spans x [-240, -80], y [-400, -240] - the old code put it ~292 m
away).

Fix in `import_map.py`:

- Terrain vertices: `world = block_coord * 160 - 5200 + sample * grid_scale`
  (both axes), no Y negation, no world offset property.
- Tile stride is 64 samples (160 m), matching the client; the old per-tile
  offset accumulation was removed.
- Objects: `(x/100, -y/100, z/100)`, no world offset.
- Removed the `world_offset_x/y` operator properties (now meaningless).

Verified: every object in all 16 JDT01 IFO files lands within its own tile's
terrain bounds; terrain corners match the client exactly (tile 31_30 corner
at (-240, -400) m).

## Coordinates recap

| System | Rule |
|--------|------|
| Rose file data | centimeters, Y-up |
| Rust client | `(x, y, z) -> (x, z, -y) / 100.0`, terrain block corner `160 * block - 5200` m |
| Blender addon | terrain `(160*block_x - 5200 + vx*2.5, 160*block_y - 5200 + vy*2.5, h/100)`; objects `(x, -y, z)/100` - same space, no offsets |

Both agree on Z-up; they differ in how the Y axis is folded. The addon's
convention is the one used by `import_map.py` - keep it consistent when
adding features.

## Back-slot equipment meshes: the 2026-09-01 wing orientation fix

Symptom chain while shipping a resculpted `BACK_WING12.ZMS`: wings sideways
in-game; rotated +90 in Blender -> still sideways; rotated back -> upside
down; 180 about Z -> upright but grafted to the chest; flipped depth axis ->
correct but floating; final offset -> correct.

Root facts (verified in the Rust client, not guessed):

- `spawn_model` (rose-offline-client `src/model_loader.rs`) spawns every
  ZSC part mesh with `Transform::default()` parented to a skeleton bone.
  **The part position/rotation/scale from LIST_BACK.ZSC is ignored** (the
  BACK_WING12 entry is identity anyway, so the real engine never corrected
  it either).
- The Back slot parents to dummy bone index 3 (`p_03` in MALE/FEMALE.ZMD,
  parent `b1_chest`, identity local rotation, ~on the spine).
- `zms_asset_loader.rs` rewrites mesh attributes `(x, y, z) -> (x, z, -y)`.

Net effect - the only orientation is the one baked into the file:

| File axis | In-game direction |
|-----------|-------------------|
| +X        | up (game vertical) |
| -Y        | backward (behind the character) |
| ±Z        | left / right wing pair |

(+8 deg forward lean comes from the chest bone bind pose.)

Authoring rule for equipped back-slot ZMS (wings, capes): build the mesh in
**file space** - tips toward +X, pair mirrored across Z, sweep toward -Y,
wing roots near Y = 0 tucked into the torso, and nudge the whole fan a
little further back (BACK_WING12 ended at Y in [-0.81, -0.08]) so it clears
the back. Export **verbatim**:
`export_zms_mesh_object(obj, path, version=8, apply_world_transform=False,
convert_coordinates=False)`. Never apply a "stand it up for the Blender
viewport" rotation before exporting; that rotation must stay unapplied (or
exist only as a parent/display transform), because the game applies its own
equivalent mapping at load.

Note: the stock `BACK_WING12.ZMS` is *not* upright on this client - it
displays sideways, since the shipped file relies on transforms the client
does not apply. Compare against the `.bak` only for scale/attach framing,
not for orientation.

Debug recipe (file->game axis map without launching the game): load
MALE.ZMD, compute the global bind pose of dummy `p_03` using the client's
own conversions (`pos (x, z, -y)/100`, `Quat::from_xyzw(x, z, -y, w)`,
hierarchy multiply), then compose with the loader swap above.

## ZMS round trip: strips, material counts, normals, box, skin (2026-09-30)

Found during a mesh-replacement task: re-exporting an imported ZMS after a
topology edit wrote the imported strips verbatim, and normals were not the
file's. The bounding box and skin weights followed. Guarded by
`tests/test_blender_zms_export.py`.

### What the importers store

`import_zms.py`, `import_zms_zmd.py` (`_create_mesh`):

- Object props (restored by `export_zms_mesh_object`): `zms_version`,
  `zms_identifier`, `zms_bones`, `zms_pool`, `zms_strips` (ibuf_strip),
  `zms_materials` (matid_numfaces), plus the imported topology
  `zms_import_vertex_count`, `zms_import_triangle_count` and
  `zms_import_index_crc` (`rose/zms.py` `index_checksum`, CRC32 of the
  triangle index list), and the file's box `zms_import_bbox_min` /
  `zms_import_bbox_max` with `zms_import_position_crc` (`position_checksum`,
  CRC32 of the positions packed as float32, in mesh units).
- Mesh attribute `zms_normal` (POINT, FLOAT_VECTOR): the exact file
  normals, including non-unit ones (351 client files).
- Skinned files only, mesh attributes `zms_bone_weight` and `zms_bone_slot`
  (POINT, QUATERNION used as a plain float4): each vertex's 4 file blend
  weights and 4 bone table slots, in file order (`Vertex.bone_slots`, the
  raw slots the reader resolves through the table into `bone_indices`).
- Faces are smooth shaded **before** `normals_split_custom_set` (custom
  normals are stored relative to the shading-dependent corner spaces, so
  changing shading afterwards changes them). `import_eft.py` does the same.
- Degenerate triangles (a repeated vertex, e.g. `(0, 1, 1)` strip joins;
  26 client files, e.g. `ITEM/BACK/BACK02.ZMS`) are skipped
  (`rose/zms.py` `valid_triangles`). `from_pydata` keeps them as invalid
  faces with no valid edge per corner, and `normals_split_custom_set` then
  read garbage edge indices: EXCEPTION_ACCESS_VIOLATION in
  `mesh_normals_corner_custom_set`, depending on memory layout (it also
  crashed the pre-fix, flat-shaded importer). They have zero area, so
  nothing visible is lost, but those files no longer round-trip exactly.

### Strips and material face counts are topology-bound

`ibuf_strip` indexes the vertex buffer and `matid_numfaces` partitions the
triangle list (per-subset face counts; they sum to the triangle count in
all 364 client files that have them). The exporter restores both only
while the triangle list it writes has the imported vertex/triangle counts
and index CRC (`export_zms.py` `_topology_change`). Otherwise - decimate,
subdivide, re-mesh, deleted or flipped faces, the winding swap of
`convert_coordinates=True`, or an object imported before these props
existed - it writes empty lists and reports INFO with the reason. Bones and
pool are not topology-bound and are always restored. Counts alone are not
enough: a flipped face keeps both counts, and in 10 client files the
exporter's first-use vertex numbering differs from the file's, so the
verbatim strips pointed at the wrong vertices.

Callers: `export_zone.py` (`AddZoneObject`, mirrored export) therefore
never ships strips for an imported mesh; `enhance_wings.py` records the
original file's topology on its densified mesh so the stale strips/material
counts are dropped (8 verts / 11 strip indices -> 1568 verts, strips empty).

### Normals

Blender 4.5 facts (measured, not assumed):

- `MeshVertex.normal` is the normalized **mean of the vertex's corner
  normals** when the mesh has custom normals; only without custom normals
  is it the face-derived normal. `corner_normals` (4.1+) is what the
  viewport shows.
- Custom normals are stored as `custom_normal` (CORNER, INT16_2D) offsets
  inside each corner's normal space: they decode ~1e-4 rad off, and about
  2.6% of client vertices cannot be represented at all (up to 90 deg off,
  or `(0, 0, 0)` for a normal perpendicular to its only face). Flat faces
  still show custom normals; smooth faces lose slightly fewer (2.61% vs
  2.81%).
- `bmesh.ops.transform` moves the encoded normals with the geometry, which
  is only right for rotation: non-uniform scale or mirroring corrupts them.
- Separate (P) keeps the decoded custom normals of both pieces.

Export (`zms_from_mesh_data`): with custom normals, each exported vertex
(one per (vertex, uv, color) key) gets the normalized mean of the corner
normals of the loops sharing that key, so a hard edge along a UV seam
survives (the per-vertex mean blurred it). World-space export reads them
from the source mesh and applies the inverse transpose of `matrix_world`.
Without custom normals `vertex.normal` is used as before. When the topology
is unchanged, every vertex whose corner normals still decode to exactly
what re-applying `zms_normal` to a copy of the mesh produces gets the exact
stashed file normal (`_unchanged_file_normals`); an angle tolerance cannot
tell Blender's 90 deg losses from edits, the re-encode comparison can.

Pieces split from one skinned mesh (head/body/tail) only share seam normals
if the pieces carry the same custom normals along the seam (Separate keeps
them, or Data Transfer from the whole mesh). A piece without custom normals
exports its own face-derived normals, which differ per piece at the seam
(22.6 deg on the test sheet); the exporter cannot recover those.

### Bounding box

1971 of 2882 client files, all v7+, store a header box that is not the
min/max of their positions (the authoring tool's box). v5/v6 store the box
`* 100` like the positions (file units, e.g. `AVATAR/CAP/CAP_02600.ZMS`:
min `(-41.97, -58.62, -39.92)`), and all 8 v6 files have a tight box in
those units. `rose/zms.py` divides only the positions by 100 and keeps the
box verbatim: the importers stash it as `zms_import_bbox_min/max`, and
rose-offline's `ZmsFile::read_bounds` reads a v5/v6 box as centimeters.

The exporter writes the imported box back while the topology is unchanged
and the positions it exports, in mesh units, still have
`zms_import_position_crc` (`_restore_bounding_box`). The CRC uses mesh
units, not file units: `(co * 100) / 100` is exact, so a v6 file keeps its
box although `* 100` rounds some positions. A moved vertex, a world export
of a moved object, the mirror of `convert_coordinates=True`, a topology
edit or an object without the props all get the box recomputed from the
exported positions, in file units like the positions it bounds (v5/v6:
from the `* 100` values). Float32 rounding is monotonic, so the written box
is exactly the min/max of the written positions. Before this fix the v5/v6
recomputed box was written in mesh units, 100x too small.

### Skin weights

The fallback (edited weights, no stash, topology edits) is unchanged: each
vertex's vertex groups sorted by weight, the top 4 divided by their sum,
missing slots padded with bone id 0, and group `g` mapped to `zms_bones[g]`.
Client files do not look like that: of 699 skinned files 310 store
unsorted weights, 428 weights whose float bits `w / total` changes, 44
zero-weight slots other than 0; and bone id 0 is not slot 0 when the table
lists it later. 514 came back with other weights or slots on an unedited
round trip (681 differed at all, most also in the box).

So while the topology is unchanged, every vertex whose vertex-group weights
are still exactly what importing the stash produced gets the stashed
weights and slots (`_unchanged_file_skin`, `_restore_file_skin`). "What
importing produced": both importers make one group per table entry in table
order and add each slot with weight > 0 to its bone's group with REPLACE,
i.e. group = first table slot holding that bone id, the same group the
fallback maps back to that bone. The comparison is exact (float32 weights
and group indices), so any weight paint, normalize, added or removed group
on a vertex sends that vertex through the fallback; the others keep the
file bytes. Since the expected groups use the exporter's own group -> bone
mapping, a restored vertex always has the influences the fallback would
write. The writer keeps a vertex's raw slots while they still resolve to
its bone ids (`_bone_slots`); only a table that lists a bone twice (no
client file does) needs that over `bones.index(bone_id)`.

`import_zms_zmd.py` names groups after ZMD joints and creates none without
a skeleton in the folder (365 skinned client files, e.g. `AVATAR/ARMS`):
those export without bone data at all (pre-existing, unchanged).

### Byte-exact round trip

An unedited import re-exports byte-identically when the file fits the
exporter's own model: 2809 of 2882 client files through `import_zms`
(807 before the box and skin stash), and the same files through
`import_zms_zmd` except the 365 skinned files without a ZMD alongside
(2444). The rest differ for pre-existing reasons: normals are always
written (30 files have none), degenerate triangles are skipped (26 files)
and vertices are renumbered in first-use order (10 files), and v5/v6
positions go through `* 100` (7 of the 8 v6 files differ in positions; box
and everything else round-trip). No file differs in normals, box, skin
weights, strips, material counts or indices.
