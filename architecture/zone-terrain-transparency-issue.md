# Zone Terrain Transparency Issue

Investigation notes from 2026-08-01: user reported that terrain textures
apply but are "not blended" - parts of the textures appear black and look
like they should be semi-transparent, overlapping with other textures.

## Symptom

- Terrain imports at the correct position; assets and textures are present.
- Textures on the terrain appear "not blended": black regions where the
  user expected the detail layer to be transparent and reveal the base
  layer underneath.

## The game's actual blend

The Bevy client (`rose-offline-client`) renders terrain with TWO texture
layers per TIL patch, sampled per-quad in the shader
(`src/render/shaders/terrain_material.wgsl`):

```wgsl
let tile_layer1_id = tile_info & 0xff;          // layer1 + offset1
let tile_layer2_id = (tile_info >> 8) & 0xff;   // layer2 + offset2
let tile_rotation  = (tile_info >> 16) & 0xff;

let layer1 = textureSample(tile_array_texture[tile_layer1_id], sampler, uv1);
let layer2 = textureSample(tile_array_texture[tile_layer2_id], sampler, rotated_uv);
let terrain_color = mix(layer1, layer2, layer2.a);
```

The **alpha channel of the layer2 texture is the splat mask** - where it is
0 the base (layer1) shows; where it is 1 the detail (layer2) shows. There
is no separate mask texture, and no `blending`-flag check in the terrain
mesh code (`src/zone_loader/spawning/terrain.rs`).

## Investigation

Five independent checks were run to find the black source:

1. **DXT3 reference decode (pure Python)** - the JD terrain tiles
   (T011_01, T018_01/03, T021_05, T020_01) all contain real splat masks:
   alpha mean 0.27-0.61, 17-48% fully transparent pixels, straight
   (non-premultiplied) alpha, and essentially **zero near-black RGB
   pixels** (0.0-0.01%). The textures themselves cannot produce black.

2. **Blender's DDS alpha vs reference** - Blender 4.5 loads the DXT3 alpha
   channel bit-identically to the reference decode (same mean, min, max,
   and counts). The alpha driving the mix is correct.

3. **Material node graphs** - per-(layer1, layer2) pair materials with a
   Mix (RGBA) node: layer2 Alpha -> Factor, layer1 Color -> A, layer2
   Color -> B; layer2 sampled through the rotation-adjusted `UVMap_rot`
   attribute. Wiring is correct.

4. **Headless EEVEE renders** - rendered the imported terrain in three
   modes (normal blend / layer1-only / layer2-only). The normal mode
   differs from layer1-only on 41% of pixels, proving the mix is active.
   Black pixels on the terrain: ~0.5-0.7% (deep shadow slopes), not
   texture regions.

5. **Live viewport analysis (via Blender MCP)** - with the terrain framed,
   the user's actual session showed 0.01% black pixels in both Material
   Preview and Rendered modes. The black that remained was the viewport
   UI and the void beyond the sparse map's edge tiles.

## Conclusion

The blend works correctly in the current import; the reported black was
most likely one (or both) of:

- A **stale import** from before the UV / two-layer blend work (flat
  per-patch colors sampled at UV (0,0), which read dark).
- **Dim scene lighting**: the default scene has one weak point light; the
  Rendered-mode mean brightness measured (0.24, 0.24, 0.24), so large
  parts of the terrain read as dark/black. The game renders with a strong
  directional sun plus ambient.

## Resolution

Added a `Setup scene lighting` option (default on) to the `.zon` importers
(`import_map.py`, `import_terrain.py`):

- Creates a `ROSE_Sun` (energy 2.5, 55-degree elevation) if the scene has
  no sun light.
- Raises the world background strength to at least 0.5 so ambient light
  keeps the terrain visible in Rendered mode.

This matches the game's lit look and removes the "black terrain" reading.

## 2026-08-02 follow-up: the real root cause (sRGB mipmap darkening)

The user reported the alpha/black issue again after reimporting. A render
pipeline investigation (headless EEVEE and Cycles, pixel-level analysis)
found the true cause:

1. At **mip level 0** (full zoom) the terrain renders clean (~0.08% black
   pixels - the texture's own few dark texels).
2. At **minification** (mip levels 1+), ~10-13% of terrain pixels render
   **pure black (0,0,0)**, clustered on the face/patch grid lines.
3. This happened with DDS, PNG, and procedurally-generated images alike,
   in both EEVEE and Cycles - but a **solid white 256x256 image was clean**,
   and a flat white material was clean.
4. Setting the image colorspace to **Non-Color** removed all the black
   (black fraction dropped to exactly the void, 5.77%).

Conclusion: **Blender's sRGB mipmap pipeline double-converts the sRGB->
linear color transform on mip levels**. The textures' dark texels (sRGB
luminance ~33-60) get gamma-applied twice when minified and become pure
black. The layer2 alpha masks are unaffected - the black was the texture
RGB through broken mips, not the alpha blend.

### Fix (in both importers)

- Terrain images are loaded with `colorspace_settings.name = "Non-Color"`
  so no automatic sRGB conversion happens anywhere in the pipeline.
- Each texture node's Color output is linearized manually with a
  `Gamma(2.2)` node before entering the Mix/BSDF, keeping the correct
  linear-space lighting response.

Verified: minified render black fraction = 5.77% (the void only), mean
color warm brown (0.361, 0.204, 0.066), all 35 materials carry Gamma
nodes, all 20 images are Non-Color.

## Regression tests

- `tests/test_dds_alpha.py` - decodes DXT3 masks independently and checks
  they are straight alpha with no premultiplied-black pixels.
- `tests/test_blender_materials.py` - verifies the mix node chain
  (Factor from layer2 alpha, UVMap_rot on layer2, DDS images keep their
  alpha channel), that images are Non-Color, and that every material has
  a Gamma node.
- `tests/test_blender_import.py` - smoke test now asserts the `ROSE_Sun`
  light is created on import.

## If it recurs

- Reimport with the current addon code (reload scripts first).
- Check viewport shading mode: Material Preview uses studio lights and
  always shows the textures; Rendered mode needs the sun (or a light of
  your own).
- Any remaining black *on the terrain* should be verified by rendering
  with `tests/test_blender_materials.py`'s approach - measure, don't
  guess: the texture data, alpha, and node graphs are all verified.
