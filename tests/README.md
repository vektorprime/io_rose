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
| test_sparse_grid.py | pure python | face/material count alignment on sparse tile grids |
| test_terrain_build.py | pure python | full terrain build + stitch faces on real data (mirrors import_map.py) |
| test_til_mapping.py | pure python | TIL 16x16 patch -> 4x4 quad mapping (`vx // 4`) |
| test_coordinates.py | pure python | terrain/object world-space alignment (block corner = 160*block - 5200 m) |
| test_texture_stats.py | pure python | two-layer texture pair statistics |
| test_dds_alpha.py | pure python | DXT3 alpha masks are straight alpha (no premultiplied black) |
| test_blender_import.py | Blender headless | full `.zon` import via the operator |
| test_blender_materials.py | Blender headless | UV maps, per-pair materials, layer2 rotation, DDS alpha, Non-Color + Gamma |

## Test data

All tests need a real zone directory (HIM/TIL/IFO/ZON files). The default is
the JDT01 zone from the Bevy client export:

```
C:\Users\vicha\RustroverProjects\rose-offline-client\target\debug\3Ddata\MAPS\JUNON\JDT01
```

Override with the `ROSE_TEST_ZONE` environment variable to test other zones.

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
```

Blender headless tests (must run with the Blender executable so `bpy` exists):

```
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/test_blender_import.py
"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe" --background --factory-startup --python tests/test_blender_materials.py
```

All scripts exit with code 0 on success, 1 on failure.

## Known data values (JDT01, update if the map data changes)

- ZON: 64x64 grid, grid_size 250.0, 131 tiles, 29 textures (after `"end"` sentinel trim)
- 16 tile files on disk: 31_30 .. 34_33 (sparse map)
- 35 distinct (layer1, layer2) texture pairs
- Rotations present: 1 (None), 2 (FlipH), 3 (FlipV), 4 (Flip)
- All DDS textures are DXT3 (4 channels, alpha = splat mask)
