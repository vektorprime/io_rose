# Rose Online Map Importer for Blender 4.5

This project is a **Blender 4.5 Python plugin** that imports **Rose Online** game maps into Blender.  
Its primary goal is to parse Rose Online map-related file formats and reconstruct terrain and map data inside Blender for visualization and editing.

---

## Supported Rose Online Map Assets

The plugin works with the following Rose Online file types and directory structure:

### ZON (Map Definition) Files
- **Directory**
C:\Users\vicha\Downloads\rose129\client\3Ddata\MAPS
- **Example ZON file**
C:\Users\vicha\Downloads\rose129\client\3Ddata\MAPS\JUNON\JDT01

ZON files define high-level map layout and reference terrain tiles and related assets.

---

### Terrain Tile Files
- **Base tiles directory**
C:\Users\vicha\Downloads\rose129\client\3Ddata\TERRAIN\TILES
- **Example tiles for JDT01**
C:\Users\vicha\Downloads\rose129\client\3Ddata\TERRAIN\TILES\JUNON\JD

These files contain terrain geometry and data referenced by ZON map definitions.

---

## Reference Implementation (Rust)

A **Rust workspace** is available as a reference for parsing and understanding Rose Online file formats:

- **Local path**
C:\Users\vicha\RustroverProjects\rose-offline

This project provides real-world implementations and data structures for Rose Online asset formats and can be used as authoritative guidance when implementing parsing logic in Python.

---

## Blender 4.5 References

### Blender Developer Documentation
- [blender developer docs](https://developer.blender.org/docs/features/)

Use this for:
- Blender Python API behavior
- Data structures (meshes, objects, scenes)
- Version-specific changes in Blender 4.5

### Blender 4.5 Source Code (GitHub)
- [Blender 4.5 source github page](https://github.com/blender/blender/tree/blender-v4.5-release)

Useful for:
- Verifying API behavior
- Understanding internal mesh/object handling
- Cross-referencing undocumented features

---

## Project Scope Summary

- **Input**: Rose Online ZON and terrain tile files
- **Processing**: Parse and reconstruct map and terrain data
- **Output**: Blender 4.5 meshes and scene objects
- **Languages**: Python (Blender plugin), Rust (reference implementation)

This document serves as the **authoritative context** for LLM-assisted development on this project.
