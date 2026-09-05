This plugin lets you use Blender 4.5 to view and modify game assets for ROSE Online (`bl_info` requires Blender 4.5+, `__init__.py:4`). Original authors: Ralph Minderhoud and Ryko (see `__init__.py:3`).

NOTE: Loading a full `.zon` (e.g. JDT01: 16 tiles x 64x64 quads = 65536 polys, `tests/test_blender_import.py:61`) is heavy - expect minutes on slower PCs. The previous "3-10 minutes" claim is workload/hardware dependent, not a benchmark.

## Import (`File > Import`, `__init__.py:73-85`)

| Menu text | Operator | Source |
|---|---|---|
| ROSE Zone (Converted Terrain + Assets) | `import_combined_zone.zon` | `import_combined_zone.py` (Bevy-converter Y-up path, `65 - block_y` flip) |
| ROSE Map (.zon) | `import_map.zon` | `import_map.py` (direct .zon, terrain + IFO objects) |
| ROSE Terrain Only (.zon) | `import_terrain.zon` | `import_terrain.py` |
| Converted ROSE Terrain (.mesh.bin) | `import_converted_terrain.mesh_bin` | `import_converted_terrain.py` |
| ROSE Scene (.zsc) | `rose.import_zsc` | `import_zsc.py` |
| ROSE Armature (.zmd) | `rose.import_zmd` | `import_zmd.py` |
| ROSE Mesh (.zms) | `rose.import_zms` | `import_zms.py` |
| ROSE Mesh with Skeleton (.zms) | `rose.import_zms_zmd` | `import_zms_zmd.py` (meshes with skeletons + textures) |
| ROSE Animation (.zmo) | `rose.import_zmo` | `import_zmo.py` |
| ROSE Effect (.eft) | `rose.import_eft` | `import_eft.py` |
| ROSE Wings Enhancer (batch) | `rose.enhance_wings` | `enhance_wings.py` |

Use `ROSE Map (.zon)` for zones/maps; `Converted Terrain` / `Combined Zone` only for Bevy-converter `.mesh.bin` output (different `65 - block_y` coordinate path, see `architecture/rose-offline-client-zone-loading.md`).

## Export (`File > Export`, `__init__.py:66-71`)

| Menu text | Operator | Source |
|---|---|---|
| ROSE Mesh (.zms) | `rose.export_zms` | `export_zms.py` |
| ROSE Animation (.zmo) | `rose.export_zmo` | `export_zmo.py` |
| ROSE Effect (.eft) | `rose.export_eft` | `export_eft.py` |
| ROSE Zone (.zon) - Save Edited Zone | `rose.export_zone` | `export_zone.py` (diff-based IFO/HIM save, see `architecture/zone-exporter.md`) |
| ROSE Object - Add Selected Mesh to Zone | `rose.add_zone_object` | `export_zone.py` |

Hidden (F3 search, not in menu): `rose.mark_zone_deleted` (`Mark Selected for Zone Deletion`, `export_zone.py:521-522`). No toggle - clearing needs manual reset of scene `rose_deleted_objects`.

Docs: `architecture/` (file formats, client loading, importer pipeline, exporter). Tests: `tests/README.md`.

You can load meshes with skeletons and textures:
<img width="1046" height="593" alt="image" src="https://github.com/user-attachments/assets/21a00c97-28ed-4567-ae71-cea4201e9ba2" />

See here for wolfie
<img width="1239" height="871" alt="image" src="https://github.com/user-attachments/assets/593a04fe-e74a-47a0-a16e-55580dcf3a54" />


And here's the zone/map:

<img width="659" height="591" alt="image" src="https://github.com/user-attachments/assets/8203f018-5c76-4175-aa0c-352206a2ed6f" />

Here is an example of me loading the JDT .zon file:

<img width="1540" height="806" alt="image" src="https://github.com/user-attachments/assets/a9741fed-5356-4ec1-bce8-4bc4060b5850" />



I copied these assets in Zant:
<img width="966" height="824" alt="image" src="https://github.com/user-attachments/assets/da61ef5f-f65d-44bb-afd7-56656ce93c55" />

Then I export it
<img width="1002" height="600" alt="image" src="https://github.com/user-attachments/assets/71ff8551-ce21-4313-bb26-d67a295a95bc" />


Confirming that the saved assets worked:
<img width="1519" height="810" alt="image" src="https://github.com/user-attachments/assets/62f2ef72-af63-4a43-8256-f735e03f12e2" />
