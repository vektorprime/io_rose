# Pitfalls & Lessons Learned

## Reference Implementation Coordinate System Differences

**Critical Lesson**: Reference implementations may use different coordinate systems than your target engine.

The Rust Bevy 0.11 reference implementation uses:
- **Bevy**: Y-up coordinate system
- **Transform**: `(x, z, -y)` swaps Y and Z for Bevy's Y-up system

Blender uses:
- **Blender**: Z-up coordinate system (same as Rose Online)
- **Transform**: `(x, -y, z)` only negates Y for forward direction

**Warning**: Do NOT blindly copy transforms from reference implementations without verifying their coordinate system matches your target engine.

## Coordinate System Transformation

**Issue**: Rose Online uses Z-up (X=right, Y=forward, Z=up). Blender also uses Z-up, so minimal transformation is needed.

**Solution**: Since both Rose Online and Blender use Z-up coordinates, only negate Y for forward direction:

| Transform | Formula |
|-----------|---------|
| Position | `(x/100, -y/100, z/100)` |
| Rotation | `(w, x, -y, z)` |
| Scale | `(sx, sy, sz)` |
| Normal | `(nx, -ny, nz)` |

Implementation locations:
- [`import_map.py:1090`](import_map.py:1090) - Map object positions
- [`import_map.py:1013`](import_map.py:1013) - IFO rotations
- [`import_map.py:1065`](import_map.py:1065) - Part rotations
- [`import_zms.py`](import_zms.py) - ZMS mesh vertices
- [`export_zms.py`](export_zms.py) - Export must apply inverse transform for round-trip fidelity

## Quaternion Order Differences

**Issue**: Different file formats use different quaternion component orders.

| Format | Order | Notes |
|--------|-------|-------|
| ZMD/ZMO/ZSC | WXYZ | w component first |
| IFO | XYZW | x component first |
| Blender | WXYZ | Internal representation |

**Solution**: Convert IFO quaternions (XYZW) to Blender format (WXYZ) using `(w, x, z, -y)`:
- The `-y` accounts for coordinate system handedness change
- [`import_map.py:1013`](import_map.py:1013), [`import_map.py:1065`](import_map.py:1065)

## Version-Dependent Parsing

**Issue**: ZMD v2 vs v3 have different dummy bone structures.

**Bug**: [`rose/zmd.py:72`](rose/zmd.py:72) attempted to read rotation field for v2 files which don't have it.

**Solution**: Always check version before reading optional fields:
```python
if version >= 3:
    rotation = read_quaternion()
```

## Root Bone Parent Handling

**Issue**: ZMD files may have root bones with parent set to own index (self-referencing).

**Bug**: [`rose/zmd.py:49`](rose/zmd.py:49) forced ALL root bones to have no parent.

**Solution**: Only convert self-referencing bones (where `parent_idx == bone_idx`) to no parent:
```python
if parent_idx != bone_idx:
    bone.parent = bones[parent_idx]
```

## Encoding Handling

**Issue**: Rose Online files may use UTF-8 or EUC-KR encoding (Korean text).

**Solution**: Try UTF-8 first, fallback to EUC-KR:
```python
try:
    data = content.decode('utf-8')
except UnicodeDecodeError:
    data = content.decode('euc-kr')
```
- [`rose/utils.py`](rose/utils.py) - `safe_decode()` function

## Hardcoded Values

**Issue**: Hardcoded values reduce flexibility and can hide bugs.

**Bugs Fixed**:
- [`import_map.py:1013`](import_map.py:1013) - Hardcoded quaternion replaced with actual IFO rotation
- [`import_map.py:1065`](import_map.py:1065) - Hardcoded quaternion replaced with actual part rotation
- World offset was hardcoded 52.0 - now configurable

**Solution**: Always use actual data from parsed files; make constants configurable when they might vary.

## Round-Trip Fidelity

**Issue**: Export must apply inverse transforms of import for correct round-trip.

**Solution**: If import uses `(x, z, -y)`, export must use `(x, -z, y)`:
- [`export_zms.py`](export_zms.py) - Apply inverse coordinate and normal transforms

## Silent Exceptions

**Issue**: Try/except blocks that silently pass can hide parsing errors.

**Solution**: Use verbose logging option to report silent exceptions during debugging:
- [`import_map.py`](import_map.py) - Added verbose logging for parsing issues

## Terrain 180° Z Rotation

**Issue**: Terrain meshes require a 180° rotation on the Z axis to orient correctly.

**Root Cause**: The Rust Bevy reference implementation inverts block Y coordinates using `(65.0 - block_y)` before calculating offsets (zone_loader.rs:808-809), then applies `-offset_y` to the Z position. This double-inversion achieves correct orientation in Bevy's Y-up system. For Blender's Z-up system, the equivalent fix is a 180° Z rotation.

**Solution**: Apply `rotation_euler = (0, 0, math.pi)` to the terrain object after creation:
- [`import_terrain.py:520`](import_terrain.py:520) - Terrain object rotation
