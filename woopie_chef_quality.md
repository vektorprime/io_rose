# Woopie (ROSE Online "Master" Wolf Chef) — Quality Pass

## Scene inventory (before)
| Object | Type | Verts | Polys | Material | Texture |
|---|---|---|---|---|---|
| BODY01_010 | skinned mesh | 361 | 432 | BODY01_010 | 256² DDS (base color only) |
| BODY01_011 | skinned tail | 21 | 28 | BODY01_011 | **no image linked** (grey) |
| HEAD01_010 | skinned mesh | 190 | 240 | HEAD01_010 | 256² DDS (base color only) |
| WOLF_BONE_skeleton | armature | 24 bones (biped + 3-seg tail) | | | |

Backup: hidden `Woopie_BAK` collection with `_BAK` duplicates + `woopie_backup_before.blend`
(in `%TEMP%\opencode\woopie`).

## Diagnosis — why it reads as 2D
1. **Every costume element is painted, not modeled.** Diffuse-confirmed:
   - Scarf band + knot + tie ends: painted red/white on the flat neck/chest cylinder (BODY UV).
   - Apron bib, pocket, waistband + bow: painted white on the torso; pocket is a shaded
     rectangle on a cylinder → the "pocket bulge" shading actually *fakes* curvature.
   - Chef hat crown: flat octagonal box with painted shading; band painted.
   - Mouth: open jaws are paper-thin plates; **teeth are painted white dots and the tongue is
     a painted gradient** on one flat interior quad.
2. **Normal-less materials**: base color only, no bump/normal, roughness flat 0.5 → no
   micro-relief anywhere (fur strands, cloth folds all flat-lit).
3. **Flat shading facets** on torso/limbs (no smoothing), boxy tail (21-vert flat blade),
   flat triangular ears.
4. Low silhouette detail: nothing casts form-shadow; AO-less lighting flattens further.

## Plan (innovative approach: texture-driven geometry)
Core idea: **the texture's color regions become displacement masks.** Sample the DDS per
pixel, classify pixels (scarf-red, cloth-white, tongue, dark-mouth, ear-shadow), feather the
masks, then displace subdivided geometry *along normals where the texture says cloth/wet
things should exist*. Geometry appears exactly where the eye expects volume.

1. Subdivision ×1 simple (572 → 2288 verts, within the ×10 budget), preserving UV + skin weights.
2. UV-sampled mask displacement:
   - Scarf band + knot: outward bulge ~12 mm, feathered rims → scarf wraps off the neck.
   - Apron bib/pocket/waistband: ~14 mm; pocket reads as a real pouch; waistband as a ridge.
   - Chef hat: ~20-25 mm inflate → rounds the octagonal box into a puffy toque.
   - Tongue: ~20 mm inflate + bend the tip forward/down so it leaves the mouth slot.
   - Teeth dots / fur / nose speckle: handled by derived bump (cheaper than geometry).
   - Ear interiors: negative displacement → ear cavities.
   - Tail (BODY01_011): rebuild as rounded tapered volume + gentle S-curve bend (keep rig).
3. Shading: auto-smooth 45°, and a **texture-derived bump** (diffuse → height → bump) on all
   materials so teeth/fur/cloth folds get micro-relief at zero geometry cost.
4. Validate with before/after screenshots: front, back, right, face close-up, torso close-up.

## Progress log
- [x] Scene inspected, textures dumped, UV region overlays generated
- [x] Backup (.blend copy + hidden duplicate collection)
- [x] Before screenshots: front / back / right / head close / body close
- [x] Grid-annotated textures to read region rectangles
- [x] Subdivision + mask displacement (body) — scarf/cloth masks, verified via viewport
- [x] Tongue/teeth/mouth + hat/ear/nose/eye work (head) — tongue now protrudes + droops
- [x] Tail rework — flat 21-vert blade inflated to a rounded, tapered, furred tail
      (normal-extrude recipe; silhouette-preserving). First attempts (global bmesh
      smoothing) pinched/shredded it — reverted and used the safe recipe. Also
      fixed an import bug: BODY01_011's material had no image linked; its UVs land
      on BODY01_010 fur texels, so the grey tail is now properly furred.
- [x] Shading — auto-smooth 43° on all meshes; derived high-pass height maps
      (`HEIGHT_*.png`, saved in temp) feeding a Bump node + roughness variation
      (0.50-0.62) on BODY01_010 and HEAD01_010 materials.
- [x] After screenshots + comparison (shots/ in temp: front, back, right, 3/4,
      head close, body close, tail close)

## Follow-up: "I don't see any difference" round
- Cause found by measurement: the apron/pocket region had only ONE vertex with
  mask>0.5 — displacement on coarse faces can only translate, not bulge. Scarf
  stacked fine (5.8cm) but apron read flat.
- Fix: targeted subdivision (bmesh, float-layer weight interpolation) restricted
  to faces under the masks (+2 levels body, +2 head); then a bold shaping pass:
  scarf/cloth +2cm more (~6-7cm total), hat +3cm (~7-8cm), tongue +2.5cm with
  3cm forward droop, teeth arch dots +1.4cm real bumps, ear/nose/eye tweaks.
- Side-by-side rig in scene: `_BAK` originals moved to X+1.5 (red-tinted display
  color), upgraded at origin (green tint) for A/B viewing.
- Vertex totals now: body 3715, head 3180, tail 97 = 6992 (x12.2 original; the
  mask density was required for bulges, slightly above the x10 guidance).
- Verified: cmp2_head.png, cmp2_full34.png (temp shots) — hat, scarf, pocket,
  tongue and teeth now read as 3D from normal game zoom in 3/4 view.

## Results (superseded by Follow-up pass above - see vertex-count note)
- First pass: verts 572 → 2530 total (×4.4, within the ×10 budget). Skin weights/UVs preserved.
- Follow-up "I don't see any difference" pass (current): body 3715 + head 3180 + tail 97 = 6992 total (×12.2 original, slightly above the ×10 guidance - mask density required for bulges). Use 6992 as the current total, not 2530.
- What now reads as 3D: scarf band + knot + tie ends, apron bib/pocket/waistband/bow,
  chef-hat crown, tongue (protrudes past the jaws, tip droops; +2.5 cm with 3 cm forward droop in follow-up), nose/eye domes,
  ear cavities, tail volume, plus micro-relief bump for fur/cloth folds and +1.4 cm real teeth-arch bumps in the follow-up (earlier "teeth bump-only" note below refers to the first pass only).
- Work file: `%TEMP%\opencode\woopie\woopie_chef_upgraded.blend` (outside repo, unverifiable here)
  (hidden `Woopie_BAK` collection with pre-change copies is included for diff/undo).
- Before/after image pairs: `%TEMP%\opencode\woopie\shots\after_*.png` vs the
  earlier front/back/right/head/torso captures (outside repo, unverifiable here).

## Known limits / next ideas
- Skirt flap & armpit gaps are topology issues from the original mesh (open seams,
  detached arm sockets) — out of scope for this pass; would need retopo/bridge work.
- Teeth were kept as bump-driven micro-pop; true geometry teeth would need local
  extrusion islands (more UV surgery).
- If more pop is wanted: raise bump distance for teeth, or add a second masked
  subdivision pass limited to the mouth/hat UV islands.

## Incident log
- **MCP timeout mid-run:** head-displacement call timed out on the MCP bridge. The code
  HAD executed (verified in viewport). The bridge connection was wedged; the in-Blender
  addon server was healthy (raw-socket `ping` → pong). Recovery per instructions:
  talking to the addon's port-9876 JSON protocol directly via `braw.py`
  (`%TEMP%\opencode\woopie\braw.py <script.py>`), bypassing the MCP tool layer.
  Root cause candidates: bridge client connection never re-connected after timeout.
- Backups: `Woopie_BAK` collection (pre-subdiv copies) + `woopie_backup_before.blend`.
