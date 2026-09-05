# io_rose Tests

Regression tests for the ROSE Online Blender addon. These were created
during the 2026-07-31..08-01 sessions (sparse-tile crash, coordinate
alignment, terrain texturing) and must be kept - never delete them, they
guard against regressions.

## Layout

| Script | Type | Guards against |
|--------|------|----------------|
| test_zone_files.py | pure python | ZON/HIM/TIL/IFO parsing; `"end"` texture sentinel |
| test_zone_roundtrip.py | pure python | every ZON/HIM/TIL/IFO file saves back byte-identically (the zone exporter's safety net) |
| test_helpers.py | pure python | UV rotation, TIL patch rotation, texture pair logic |
| test_sparse_grid.py | pure python | face/material count alignment on sparse tile grids (main quads only, no stitch) |
| test_terrain_build.py | pure python | full terrain build, main quads only, no stitch faces, on real data (mirrors import_map.py) |
| test_til_mapping.py | pure python | TIL 16x16 patch -> 4x4 quad mapping (`vx // 4`) |
| test_coordinates.py | pure python | terrain/object world-space alignment (block corner = 160*block - 5200 m) |
| test_texture_stats.py | pure python | two-layer texture pair statistics |
| test_dds_alpha.py | pure python | DXT3 alpha masks are straight alpha (no premultiplied black) |
| test_blender_import.py | Blender headless | full `.zon` import via the operator |
| test_blender_materials.py | Blender headless | UV maps, per-pair materials, layer2 rotation, DDS alpha, Non-Color + Gamma |
| test_eft_roundtrip.py | pure python | every EFT/PTL file saves back byte-identically; effective-path rules |
| test_blender_eft.py | Blender headless | `.eft` import (slots, meshes, particle preview, TRAJ baking) + export round-trip |

## Test data

All tests need a real zone directory (HIM/TIL/IFO/ZON files). The client
3Ddata root is resolved by `tests/_paths.py` from the `ROSE_CLIENT_3DDATA`
environment variable, falling back to the default checkout under the
current user's home:

```
%USERPROFILE%\RustroverProjects\rose-offline-client\target\debug\3Ddata
```

Override with `ROSE_TEST_ZONE` (zone dir) / `ROSE_TEST_TEXTURES` (tile
textures) to test other data.

## Running

Pure-python tests (no Blender needed):

```
python tests/test_zone_files.py
python tests/test_zone_roundtrip.py
python tests/test_helpers.py
python tests/test_sparse_grid.py
python tests/test_terrain_build.py
python tests/test_til_mapping.py
python tests/test_coordinates.py
python tests/test_texture_stats.py
python tests/test_dds_alpha.py
python tests/test_eft_roundtrip.py
```

Blender headless tests (must run with the Blender executable so `bpy` exists):

```
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/test_blender_import.py
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/test_blender_materials.py
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/test_blender_eft.py
```

All scripts exit with code 0 on success, 1 on failure.

## Known data values (JDT01, update if the map data changes; only asserted values are enforced)

- ZON: 64x64 grid (printed, not asserted), grid_size 250.0 (asserted), 131 tiles (unasserted, update if data changes), 29 textures after `"end"` sentinel trim (asserted, `test_zone_files.py:30`)
- Tile files on disk are a sparse subset (e.g. 16 files `31_30..34_33` on the tested checkout - comment-only, `test_terrain_build.py:118`, not asserted)
- 35 distinct (layer1, layer2) texture pairs (asserted, `test_texture_stats.py:26-27`)
- Rotations present: 1 (None), 2 (FlipH), 3 (FlipV), 4 (Flip) (asserted counts `{1:2859,2:518,3:469,4:250}`, `test_blender_materials.py:70-71`)
- All DDS textures are DXT3 (asserted, `test_dds_alpha.py:37`)
- Round-trip covers all ZON/HIM/TIL/IFO files found (no fixed 49-file count asserted)

Pure-python tests must run from the addon root (`python tests/test_*.py`) so `import _paths` resolves; only some scripts add the tests dir to `sys.path` themselves. Blender tests need `bpy` plus `ROSE_CLIENT_3DDATA` data (`tests/_paths.py:11-31`).
