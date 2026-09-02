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
  export_zms.py             mesh export
  test_zon.py               ZON parser self-test
  rose/                     format parsers (pure Python, no bpy):
    him.py til.py zon.py ifo.py zsc.py zms.py zmd.py zmo.py utils.py
  architecture/             this documentation
```

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
   - Inter-tile stitch faces: X-edge, Y-edge, and corner quads that bridge
     the shared sample edges between adjacent tiles. Tile-to-tile stride is
     64 samples (160 m), matching the client's block size - never 65, or
     adjacent tiles drift 2.5 m apart.
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

Faces are appended per tile in this order, and the material-index pass must
count them in exactly the same order:

1. Main quads: `(length-1) * (width-1)` per tile
2. X-edge stitch faces: `(length-1)` per tile (only if right neighbor exists)
3. Y-edge stitch faces: `(width-1)` per tile (only if bottom neighbor exists)
4. Corner stitch face: 1 per tile (only if all three neighbors exist)

Any change to one loop must be mirrored in the other, or `face_idx` drifts
and polygons get wrong material slots.

## Sparse tiles: the 2026-07-31 bug fix

Symptom (importing JDT01.ZON):

```
File "import_map.py", line 736, in execute
    v2 = next_indices[vy][0]
TypeError: 'NoneType' object is not subscriptable
```

Root cause: the stitching loop used `tiles.indices[y][x + 1]` and
`tiles.indices[y + 1][x]` unconditionally. On sparse maps the neighbor tile
never loaded, so its slot was `None`. The Rust client handles this by
skipping `None` blocks; the addon did not.

Fix (matching the Rust behavior):

- Compute per tile:
  `has_x_neighbor`, `has_y_neighbor`, `has_xy_neighbor` (all three neighbors
  for the corner face - the old code only checked two, which could still
  crash via the unguarded `right` access).
- The stitching loop only emits stitch faces when the neighbor exists.
- The material-index loop uses the identical predicates so face counting
  stays aligned.

Verified: 10 sparse-grid configurations (including JDT01 with tiles forced
missing) all keep face counts aligned; no crash; all face vertex indices
valid.

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
