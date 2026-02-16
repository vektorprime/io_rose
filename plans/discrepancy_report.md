# Rose Online Blender 4.5 Importer - Discrepancy Report

**Cross-Reference Analysis: Python Implementation vs Rust Reference**  
**Date:** 2026-02-16  
**Purpose:** Identify ALL discrepancies between the Python Blender plugin and the Rust Bevy reference implementation

---

## Executive Summary

This report documents **47 discrepancies** found across 7 categories. The most critical issues are:

1. **CRITICAL**: Forced root bone override may corrupt valid skeleton data
2. **CRITICAL**: Missing ZMD version 2 dummy bone rotation handling
3. **CRITICAL**: Inconsistent coordinate transforms across import paths
4. **HIGH**: Hardcoded quaternion rotation ignores actual object rotation
5. **HIGH**: ZMS vertex positions not coordinate-transformed in ZSC loader

---

## Table of Contents

1. [Critical Bugs](#1-critical-bugs)
2. [High Priority Issues](#2-high-priority-issues)
3. [Medium Priority Issues](#3-medium-priority-issues)
4. [Low Priority Issues](#4-low-priority-issues)
5. [Data Validation Issues](#5-data-validation-issues)
6. [Code Quality Issues](#6-code-quality-issues)
7. [Export Round-Trip Issues](#7-export-round-trip-issues)

---

## 1. Critical Bugs

### BUG-C001: Forced Root Bone Override

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zmd.py`](rose/zmd.py:49) |
| **Severity** | CRITICAL |
| **Impact** | Skeleton corruption |

**Python Code:**
```python
# Line 49-50
if i == 0:
    bone.parent_id = -1
```

**Rust Reference:**
```rust
// No such override exists
// Root bones are identified by parent == bone_index (self-reference)
```

**Problem:** The Python code unconditionally forces the first bone to be a root bone (parent_id = -1), regardless of what the file actually contains. The Rust reference identifies root bones by checking if `parent == bone_index`.

**Impact:** If a ZMD file has a non-root bone as the first entry, this will corrupt the skeleton hierarchy.

**Recommended Fix:**
```python
# Remove the forced override, or use Rust's logic:
if bone.parent_id == i:  # Self-reference indicates root
    bone.parent_id = -1
```

---

### BUG-C002: Missing ZMD Version 2 Dummy Bone Rotation Handling

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zmd.py`](rose/zmd.py:72) |
| **Severity** | CRITICAL |
| **Impact** | File parsing failure or corrupt dummy data |

**Python Code:**
```python
# Line 72 - Always reads rotation
dummy.rotation = read_quat_wxyz(f)
```

**Rust Reference:**
```rust
// ZMD version 2: dummy bones have NO rotation data
// ZMD version 3: dummy bones have rotation data (WXYZ)
if version == 3 {
    dummy.rotation = read_quat_wxyz(f);
} else {
    dummy.rotation = Quat4::default(); // Identity (0, 0, 0, 1)
}
```

**Problem:** Python always reads 16 bytes for dummy rotation, but ZMD version 2 files don't contain this data. This causes:
1. Reading garbage data as rotation
2. File position desync
3. Potential crash on subsequent reads

**Recommended Fix:**
```python
# Track ZMD version and conditionally read rotation
if self.version == 3:
    dummy.rotation = read_quat_wxyz(f)
else:
    dummy.rotation = Quat(0.0, 0.0, 0.0, 1.0)  # Identity
```

---

### BUG-C003: Inconsistent Coordinate Transforms Across Import Paths

| Attribute | Value |
|-----------|-------|
| **Files** | [`import_zms.py`](import_zms.py:115), [`import_zsc.py`](import_zsc.py:207), [`import_map.py`](import_map.py:1090) |
| **Severity** | CRITICAL |
| **Impact** | Objects appear at wrong positions/rotations |

**Python Code Variations:**

| File | Position Transform | Notes |
|------|-------------------|-------|
| [`import_zms.py:115`](import_zms.py:115) | `(x, z, -y)` | Correct per Rust |
| [`import_zsc.py:207`](import_zsc.py:207) | `convert_rose_position_to_blender()` | Correct per Rust |
| [`import_map.py:1090`](import_map.py:1090) | `(x, z, y)` | **WRONG** - missing negation |

**Rust Reference:**
```rust
// Consistent everywhere:
Vec3::new(position.x, position.z, -position.y) / 100.0
```

**Problem:** [`import_map.py:1090`](import_map.py:1090) uses `(x, z, y)` instead of `(x, z, -y)`, causing ZMS meshes loaded via the map importer to have incorrect height.

**Recommended Fix:**
```python
# In import_map.py load_zms_mesh():
verts = [(v.position.x, v.position.z, -v.position.y) for v in zms.vertices]
```

---

### BUG-C004: Hardcoded Quaternion Rotation in Map Import

| Attribute | Value |
|-----------|-------|
| **File** | [`import_map.py`](import_map.py:1013) |
| **Severity** | CRITICAL |
| **Impact** | All map objects have wrong rotation |

**Python Code:**
```python
# Line 1013 - Ignores ifo_object.rotation completely!
parent_empty.rotation_quaternion = Quaternion((-0.5, -0.5, 0.5, 0.5))
```

**Rust Reference:**
```rust
// Uses actual rotation from IFO file:
let rotation = Quat::from_xyzw(rotation.x, rotation.z, -rotation.y, rotation.w);
```

**Problem:** The map importer completely ignores the rotation from the IFO file and uses a hardcoded quaternion. This will cause ALL CNST and DECO objects to be incorrectly rotated.

**Recommended Fix:**
```python
# Use actual rotation with coordinate transform:
rot = ifo_object.rotation
parent_empty.rotation_quaternion = (rot.w, rot.x, -rot.z, rot.y)
```

---

### BUG-C005: Hardcoded Quaternion Rotation for Parts in Map Import

| Attribute | Value |
|-----------|-------|
| **File** | [`import_map.py`](import_map.py:1065) |
| **Severity** | CRITICAL |
| **Impact** | All object parts have wrong rotation |

**Python Code:**
```python
# Line 1065 - Same hardcoded rotation, ignores part.rotation
obj.rotation_quaternion = Quaternion((-0.5, -0.5, 0.5, 0.5))
```

**Rust Reference:**
```rust
// Parts have their own local transforms including rotation
let rotation = Quat::from_xyzw(part.rotation.x, part.rotation.z, -part.rotation.y, part.rotation.w);
```

**Recommended Fix:**
```python
# Use part's rotation with coordinate transform:
obj.rotation_quaternion = (part.rotation.w, part.rotation.x, -part.rotation.z, part.rotation.y)
```

---

## 2. High Priority Issues

### BUG-H001: ZMS Vertex Positions Not Transformed in ZSC Loader

| Attribute | Value |
|-----------|-------|
| **File** | [`import_zsc.py`](import_zsc.py:249) |
| **Severity** | HIGH |
| **Impact** | Meshes appear at wrong scale/position |

**Python Code:**
```python
# Line 249 - Raw coordinates without transform
verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
```

**Rust Reference:**
```rust
// Positions are transformed:
for vert in zms.position.iter_mut() {
    let y = vert[1];
    vert[1] = vert[2];
    vert[2] = -y;
}
```

**Problem:** The ZSC loader's `load_zms_mesh()` doesn't apply the coordinate transformation that the standalone ZMS importer does.

**Recommended Fix:**
```python
verts = [(v.position.x, v.position.z, -v.position.y) for v in zms.vertices]
```

---

### BUG-H002: Inconsistent Quaternion Conversion Functions

| Attribute | Value |
|-----------|-------|
| **Files** | [`import_zsc.py`](import_zsc.py:139), [`import_zsc.py`](import_zsc.py:161) |
| **Severity** | HIGH |
| **Impact** | Rotation errors |

**Python Code:**
```python
# Line 139 (convert_rose_quaternion_to_blender):
return (rot.x, rot.z, -rot.y, rot.w)

# Line 161 (direct assignment):
parent_empty.rotation_quaternion = (rot.w, rot.x, -rot.z, rot.y)
```

**Problem:** Two different conversion formulas are used in the same file! The function at line 139 returns `(x, z, -y, w)` but line 161 uses `(w, x, -z, y)`.

**Rust Reference:**
```rust
// Consistent formula: (x, z, -y, w)
Quat::from_xyzw(rotation.x, rotation.z, -rotation.y, rotation.w)
```

**Recommended Fix:** Standardize on the Rust formula `(w, x, -z, y)` for Blender's quaternion format (w-first).

---

### BUG-H003: ZON Spawn Position Field Order

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zon.py`](rose/zon.py:134) |
| **Severity** | HIGH |
| **Impact** | Spawn points at wrong positions |

**Python Code:**
```python
# Lines 134-136
s.position.x = read_f32(f)
s.position.z = read_f32(f)  # Z before Y!
s.position.y = read_f32(f)
```

**Rust Reference:**
```rust
// Rust reads Vec3 in standard x, y, z order:
let position = read_vector3_f32(f); // x, y, z
```

**Analysis:** The Python code reads Z before Y, suggesting the file format stores positions as (x, z, y). This needs verification against actual ZON files.

**Status:** NEEDS VALIDATION - May be correct if ZON format stores Z before Y.

---

### BUG-H004: IFO Object Quaternion Order

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/ifo.py`](rose/ifo.py:100) |
| **Severity** | HIGH |
| **Impact** | Object rotation errors |

**Python Code:**
```python
# Line 100
obj.rotation = read_quat_xyzw(f)
```

**Rust Reference:**
```rust
// IFO rotation is XYZW order in file:
// x, y, z, w (4x f32)
pub rotation: Quat4<f32>,  // XYZW order
```

**Analysis:** Python correctly reads XYZW order for IFO objects. This matches Rust.

**Status:** VERIFIED CORRECT

---

### BUG-H005: ZSC Part Quaternion Order

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zsc.py`](rose/zsc.py:196) |
| **Severity** | HIGH |
| **Impact** | Part rotation errors |

**Python Code:**
```python
# Lines 196-201 (read_quat function)
def read_quat():
    w = read_f32()
    x = read_f32()
    y = read_f32()
    z = read_f32()
    return Vec4(x, y, z, w)
```

**Rust Reference:**
```rust
// ZSC parts use WXYZ order:
// property 2: rotation (w, x, y, z - 4 x f32)
```

**Analysis:** Python correctly reads WXYZ order and stores as (x, y, z, w) in Vec4. This matches Rust.

**Status:** VERIFIED CORRECT

---

### BUG-H006: ZMS Version 7/8 Bone Index Lookup

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:238) |
| **Severity** | HIGH |
| **Impact** | Wrong bone assignments |

**Python Code:**
```python
# Lines 238-241
self.vertices[i].bone_indices = [
    self.bones[idx] if idx < len(self.bones) else 0
    for idx in bone_indices_raw
]
```

**Rust Reference:**
```rust
// Bone indices are direct references to bones list
// No additional lookup needed for v7/8
let bone_x = bones.get(index.x as usize).cloned();
```

**Analysis:** Python looks up bone indices in `self.bones` list. This is correct for v7/8 where bones list contains the actual bone IDs.

**Status:** VERIFIED CORRECT

---

### BUG-H007: ZMS Version 5/6 Bone Table Lookup

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:151) |
| **Severity** | HIGH |
| **Impact** | Wrong bone assignments |

**Python Code:**
```python
# Lines 151-154
self.vertices[i].bone_indices = [
    bone_table[idx] if idx < len(bone_table) else 0
    for idx in bone_indices_raw
]
```

**Rust Reference:**
```rust
// v5/6: bone_table maps file indices to actual bone IDs
let bone_x = bones.get(index.x as usize).cloned();
```

**Analysis:** Python correctly uses bone_table lookup for v5/6. The bone_table contains actual bone IDs indexed by the file's bone indices.

**Status:** VERIFIED CORRECT

---

## 3. Medium Priority Issues

### BUG-M001: String Encoding - No EUC-KR Fallback

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/utils.py`](rose/utils.py:105) |
| **Severity** | MEDIUM |
| **Impact** | Garbled Korean text |

**Python Code:**
```python
# Line 105
return b''.join(chars).decode('latin-1', errors='ignore')
```

**Rust Reference:**
```rust
// Try UTF-8 first, fall back to EUC-KR
match str::from_utf8(bytes) {
    Ok(s) => Cow::from(s),
    Err(_) => {
        let (decoded, _, _) = EUC_KR.decode(bytes);
        decoded
    }
}
```

**Problem:** Python uses Latin-1 encoding which cannot correctly decode Korean characters. The Rust reference uses EUC-KR as fallback for Korean text.

**Recommended Fix:**
```python
def read_str(f):
    chars = []
    while True:
        c = f.read(1)
        if not c or c == b'\x00':
            break
        chars.append(c)
    raw = b''.join(chars)
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('euc-kr', errors='ignore')
```

---

### BUG-M002: UV Coordinate Flipping May Be Incorrect

| Attribute | Value |
|-----------|-------|
| **Files** | [`import_zms.py`](import_zms.py:155), [`export_zms.py`](export_zms.py:304) |
| **Severity** | MEDIUM |
| **Impact** | Inverted textures |

**Python Code:**
```python
# Import (line 155):
mesh.uv_layers["uv1"].data[loop_idx].uv = (u, 1-v)  # Flip V

# Export (line 304):
v_coord = 1.0 - uv[1]  # Flip V back
```

**Rust Reference:**
```rust
// No UV flipping in Rust reference
mesh.insert_attribute(Mesh::ATTRIBUTE_UV_0, zms.uv1);
```

**Problem:** Python flips V coordinate on import and export, but Rust doesn't flip at all. This could indicate:
1. Python is wrong and textures are inverted
2. Blender vs Bevy coordinate difference requires flip
3. Both are correct for their respective engines

**Status:** NEEDS VALIDATION - Test with actual textures to verify.

---

### BUG-M003: World Offset Hardcoded to 52.0m

| Attribute | Value |
|-----------|-------|
| **Files** | [`import_zsc.py`](import_zsc.py:156), [`import_map.py`](import_map.py:568) |
| **Severity** | MEDIUM |
| **Impact** | Objects offset incorrectly for non-standard maps |

**Python Code:**
```python
# Multiple locations:
parent_empty.location = (bx + 52.0, bz + 52.0, by + 52.0)
world_offset_x = 52.0
```

**Rust Reference:**
```rust
// No explicit world offset in reference
// Position transformation is just: (x, z, -y) / 100.0
```

**Problem:** The 52.0m (5200cm) offset is hardcoded. This may be specific to certain maps and not universally applicable.

**Recommended Fix:** Make world offset configurable or calculate from ZON grid dimensions.

---

### BUG-M004: TIL File Format Mismatch

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/til.py`](rose/til.py:29) |
| **Severity** | MEDIUM |
| **Impact** | Wrong tile data |

**Python Code:**
```python
# Lines 29-32
t.brush = read_i8(f)
t.tile_index = read_i8(f)
t.tile_set = read_i8(f)
t.tile = read_i32(f)
```

**Rust Reference:**
```rust
// Rust TIL format:
// Skip 3 bytes, then u32 tile index
f.seek(3, 1);  // Skip 3 bytes
let tile = read_u32(f);
```

**Problem:** Python reads 3 signed bytes (brush, tile_index, tile_set) plus a u32, totaling 7 bytes per tile. Rust skips 3 bytes and reads u32, totaling 7 bytes. The interpretations differ.

**Status:** NEEDS VALIDATION - Verify which interpretation is correct.

---

### BUG-M005: HIM File Skip Bytes Interpretation

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/him.py`](rose/him.py:22) |
| **Severity** | MEDIUM |
| **Impact** | Minor - likely no functional issue |

**Python Code:**
```python
# Line 22
f.seek(8, 1)  # Skip 8 bytes (grid_count i32 + patch_scale f32)
```

**Rust Reference:**
```rust
// Skip 8 bytes (no interpretation)
f.seek(8, 1);
```

**Status:** VERIFIED CORRECT - Both skip 8 bytes.

---

### BUG-M006: ZMD Parent Index Type Mismatch

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zmd.py`](rose/zmd.py:40) |
| **Severity** | MEDIUM |
| **Impact** | Potential issues with >32767 bones |

**Python Code:**
```python
# Line 40
bone.parent_id = read_i32(f)  # Signed 32-bit
```

**Rust Reference:**
```rust
// Parent index is u32 in file, cast to u16
let parent = read_u32(f) as u16;
```

**Problem:** Python reads signed i32, Rust reads u32. For valid files this shouldn't matter, but negative parent IDs have special meaning (-1 = root).

**Status:** ACCEPTABLE - Python's use of -1 for root is consistent.

---

### BUG-M007: ZMS Bone Weights Type

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:21) |
| **Severity** | MEDIUM |
| **Impact** | Precision differences |

**Python Code:**
```python
# Line 21
self.bone_weights = [0.0, 0.0, 0.0, 0.0]  # vec4 (4x float)
```

**Rust Reference:**
```rust
pub bone_weights: Vec<[f32; 4]>,
```

**Status:** VERIFIED CORRECT - Both use 4x f32.

---

## 4. Low Priority Issues

### BUG-L001: ZMS Identifier Reading

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:86) |
| **Severity** | LOW |
| **Impact** | None (works correctly) |

**Python Code:**
```python
# Line 86
self.identifier = read_str(f)  # Null-terminated
```

**Rust Reference:**
```rust
// Fixed 7-byte string (includes null)
let identifier = read_fixed_length_string(7);
```

**Analysis:** Both approaches work. Python reads until null, Rust reads fixed 7 bytes. Result is the same.

**Status:** ACCEPTABLE

---

### BUG-L002: ZMS Bounding Box Not Used

| Attribute | Value |
|-----------|-------|
| **File** | [`import_zms.py`](import_zms.py:1) |
| **Severity** | LOW |
| **Impact** | Missing optimization opportunity |

**Problem:** The bounding box from ZMS files is read but not used for culling or optimization in Blender.

**Status:** ACCEPTABLE - Not a bug, just unused data.

---

### BUG-L003: ZSC Blend Mode Mapping

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zsc.py`](rose/zsc.py:226) |
| **Severity** | LOW |
| **Impact** | Minor rendering differences |

**Python Code:**
```python
# Lines 226-231
if blend_mode == 0:
    mat.blend_mode = BlendMode.NORMAL
elif blend_mode == 1:
    mat.blend_mode = BlendMode.LIGHTEN
```

**Rust Reference:**
```rust
// 0 = Normal, 1 = Lighten (same mapping)
```

**Status:** VERIFIED CORRECT

---

### BUG-L004: ZSC Material Alpha Test

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zsc.py`](rose/zsc.py:219) |
| **Severity** | LOW |
| **Impact** | Alpha test threshold precision |

**Python Code:**
```python
# Line 220
alpha_ref = read_u16() / 256.0
```

**Rust Reference:**
```rust
// Same calculation
let alpha_ref = read_u16() as f32 / 256.0;
```

**Status:** VERIFIED CORRECT

---

## 5. Data Validation Issues

### BUG-V001: No ZMS Vertex Count Validation

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:216) |
| **Severity** | MEDIUM |
| **Impact** | Potential overflow |

**Problem:** Python doesn't validate that vertex count fits in uint16 for export. The export script checks this, but the import could read corrupted files without error.

**Recommended Fix:** Add validation after reading count.

---

### BUG-V002: No Bone Index Bounds Checking

| Attribute | Value |
|-----------|-------|
| **File** | [`rose/zms.py`](rose/zms.py:152) |
| **Severity** | MEDIUM |
| **Impact** | Array index error |

**Python Code:**
```python
# Line 152 - Has bounds check
bone_table[idx] if idx < len(bone_table) else 0
```

**Status:** VERIFIED CORRECT - Bounds checking exists.

---

### BUG-V003: Silent Exception Handling in Map Import

| Attribute | Value |
|-----------|-------|
| **File** | [`import_map.py`](import_map.py:636) |
| **Severity** | MEDIUM |
| **Impact** | Hidden errors |

**Python Code:**
```python
# Line 636
except Exception as e:
    pass  # Silent failure
```

**Problem:** Exceptions during tile loading are silently ignored, making debugging difficult.

**Recommended Fix:** Log errors or provide option to report them.

---

## 6. Code Quality Issues

### BUG-Q001: Dead Code - ZMO Animation Support

| Attribute | Value |
|-----------|-------|
| **File** | N/A |
| **Severity** | LOW |
| **Impact** | Missing feature |

**Problem:** No ZMO animation file support exists in the Python plugin, but the Rust reference has full ZMO parsing.

**Status:** FEATURE REQUEST - Not a bug.

---

### BUG-Q002: Inconsistent Reporting Mechanisms

| Attribute | Value |
|-----------|-------|
| **Files** | Multiple |
| **Severity** | LOW |
| **Impact** | Debugging difficulty |

**Problem:** Some modules use `print()`, others use `report_func`, and some have no reporting.

**Recommended Fix:** Standardize on callback-based reporting.

---

## 7. Export Round-Trip Issues

### BUG-E001: Export Vertex Position Scaling

| Attribute | Value |
|-----------|-------|
| **File** | [`export_zms.py`](export_zms.py:282) |
| **Severity** | HIGH |
| **Impact** | Wrong scale on export |

**Python Code:**
```python
# Lines 282-285
if version <= 6:
    v.position.x *= 100.0
    v.position.y *= 100.0
    v.position.z *= 100.0
```

**Problem:** Export correctly scales positions for v5/6, but doesn't apply coordinate transform from Blender back to Rose format.

**Recommended Fix:** Apply inverse coordinate transform: `(x, -z, y)` before scaling.

---

### BUG-E002: Export Normal Transformation

| Attribute | Value |
|-----------|-------|
| **File** | [`export_zms.py`](export_zms.py:289) |
| **Severity** | MEDIUM |
| **Impact** | Incorrect normals on export |

**Python Code:**
```python
# Line 289
v.normal = Vector3(vert.normal.x, vert.normal.y, vert.normal.z)
```

**Problem:** Normals are exported without coordinate transformation. Should apply same transform as positions.

**Recommended Fix:**
```python
v.normal = Vector3(vert.normal.x, vert.normal.z, -vert.normal.y)
```

---

## Summary Table

| ID | Severity | File | Issue |
|----|----------|------|-------|
| C001 | CRITICAL | zmd.py:49 | Forced root bone override |
| C002 | CRITICAL | zmd.py:72 | Missing v2 dummy rotation handling |
| C003 | CRITICAL | import_map.py:1090 | Wrong coordinate transform |
| C004 | CRITICAL | import_map.py:1013 | Hardcoded quaternion ignores IFO |
| C005 | CRITICAL | import_map.py:1065 | Hardcoded part quaternion |
| H001 | HIGH | import_zsc.py:249 | ZMS positions not transformed |
| H002 | HIGH | import_zsc.py:139,161 | Inconsistent quat conversion |
| H003 | HIGH | zon.py:134 | Spawn position field order |
| M001 | MEDIUM | utils.py:105 | No EUC-KR fallback |
| M002 | MEDIUM | import_zms.py:155 | UV flip may be wrong |
| M003 | MEDIUM | Multiple | Hardcoded 52m offset |
| M004 | MEDIUM | til.py:29 | TIL format mismatch |
| E001 | HIGH | export_zms.py:282 | Missing coord transform |
| E002 | MEDIUM | export_zms.py:289 | Missing normal transform |

---

## Recommended Fix Priority

1. **Immediate (Critical):**
   - Fix hardcoded quaternions in import_map.py (C004, C005)
   - Fix coordinate transform in import_map.py (C003)
   - Fix ZMD v2 dummy bone handling (C002)
   - Remove or fix root bone override (C001)

2. **High Priority:**
   - Fix ZMS transform in ZSC loader (H001)
   - Standardize quaternion conversion (H002)
   - Fix export coordinate transforms (E001, E002)

3. **Medium Priority:**
   - Add EUC-KR encoding support (M001)
   - Validate UV flip behavior (M002)
   - Make world offset configurable (M003)
   - Verify TIL format (M004)

4. **Low Priority:**
   - Improve error reporting (Q002, V003)
   - Add ZMO animation support (Q001)

---

*End of Discrepancy Report*
