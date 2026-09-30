from pathlib import Path
import struct
import ast
import math

if "bpy" in locals():
    import importlib
else:
    from .rose.zms import *

import bpy
import mathutils
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ExportHelper


def _corner_normals(mesh, normal_matrix=None):
    """Per-corner (x, y, z) normals of a mesh with custom normals, else None.

    Custom normals are what the viewport shows; vertex.normal is recomputed
    from the object's own faces, so pieces split from one mesh (head/body/
    tail) would get different normals along their shared seam.
    normal_matrix (3x3 inverse transpose) is applied for world-space
    export: decoded custom normals do not survive non-uniform scale or
    mirroring through bmesh.ops.transform, so they are read from the source
    mesh and transformed here instead.
    """
    if not getattr(mesh, "has_custom_normals", False):
        return None
    corner_normals = getattr(mesh, "corner_normals", None)  # Blender 4.1+
    if corner_normals is None or len(corner_normals) != len(mesh.loops):
        return None
    flat = [0.0] * (len(mesh.loops) * 3)
    corner_normals.foreach_get("vector", flat)
    normals = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
    if normal_matrix is not None:
        normals = [tuple((normal_matrix @ mathutils.Vector(n)).normalized())
                   for n in normals]
    return normals


def _is_identity(matrix, tolerance=1e-9):
    return all(abs(matrix[i][j] - (1.0 if i == j else 0.0)) <= tolerance
               for i in range(len(matrix)) for j in range(len(matrix)))


def _file_normals(mesh):
    """Exact per-vertex file normals stashed by the importers, or None."""
    attr = mesh.attributes.get(FILE_NORMAL_ATTRIBUTE)
    if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT_VECTOR':
        return None
    flat = [0.0] * (len(mesh.vertices) * 3)
    attr.data.foreach_get("vector", flat)
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]


def _topology_change(obj, zms):
    """Why zms no longer has the imported topology, or None if it does.

    Strips (ibuf_strip) index the vertex buffer and material face counts
    (matid_numfaces) partition the triangle list, so both are only valid
    while the exported triangle list equals the imported one.
    """
    imported = (obj.get("zms_import_vertex_count"),
                obj.get("zms_import_triangle_count"))
    if None in imported:
        return ("no imported vertex/triangle counts recorded "
                "(imported by an older io_rose; re-import to keep them)")
    exported = (len(zms.vertices), len(zms.indices))
    if exported != (int(imported[0]), int(imported[1])):
        return (f"mesh now has {exported[0]} verts / {exported[1]} tris, "
                f"imported {imported[0]} / {imported[1]}")
    crc = obj.get("zms_import_index_crc")
    if crc is not None and crc != index_checksum(zms.indices):
        return ("triangle list differs from the imported one (edited faces, "
                "or winding swapped by the coordinate conversion)")
    return None


def _unchanged_file_normals(mesh, file_normals):
    """Per mesh vertex: True while its custom normals are still exactly what
    importing the stashed file normals produced.

    Blender stores custom normals as int16 offsets inside each corner's
    normal space, so they decode off by ~1e-4 rad, and ~2.6% of client
    vertices cannot be represented at all (up to 90 deg off, or zero for a
    normal perpendicular to its only face). No angle tolerance can tell
    those from edits; re-applying the stash to a copy and comparing the
    decoded corner normals can.
    """
    if len(file_normals) != len(mesh.vertices):
        return None
    loop_verts = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    probe = mesh.copy()
    try:
        probe.normals_split_custom_set([file_normals[vi] for vi in loop_verts])
        expected = [0.0] * (len(loop_verts) * 3)
        probe.corner_normals.foreach_get("vector", expected)
    finally:
        bpy.data.meshes.remove(probe)
    current = [0.0] * (len(loop_verts) * 3)
    mesh.corner_normals.foreach_get("vector", current)
    unchanged = [True] * len(mesh.vertices)
    for loop_idx, vi in enumerate(loop_verts):
        i = loop_idx * 3
        if (abs(current[i] - expected[i]) > 1e-6 or
                abs(current[i + 1] - expected[i + 1]) > 1e-6 or
                abs(current[i + 2] - expected[i + 2]) > 1e-6):
            unchanged[vi] = False
    return unchanged


def _restore_file_normals(zms, source_vertices, file_normals, unchanged,
                          convert_coordinates):
    """Write the exact stashed file normal for every exported vertex whose
    source vertex still has its imported normals (bit-exact round trip)."""
    for v, vert_idx in zip(zms.vertices, source_vertices):
        if unchanged[vert_idx]:
            fx, fy, fz = file_normals[vert_idx]
            v.normal = Vector3(fx, -fy if convert_coordinates else fy, fz)


def _mesh_unit_positions(zms):
    """Exported positions in mesh units: v5/v6 store them * 100, and
    (co * 100) / 100 gives back the exact float32 coordinate."""
    if zms.version <= 6:
        return [(v.position.x / 100.0, v.position.y / 100.0, v.position.z / 100.0)
                for v in zms.vertices]
    return [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]


def _restore_bounding_box(obj, zms):
    """Write the imported bounding box back while the exported positions
    are exactly the imported ones (same order and float32 bits).

    1979 of 2882 client files store a box that is not the min/max of their
    positions, so the recomputed box breaks an unedited round trip. A moved
    vertex, or a world transform / mirror that moves them all, keeps the
    recomputed box.
    """
    lo, hi = obj.get("zms_import_bbox_min"), obj.get("zms_import_bbox_max")
    crc = obj.get("zms_import_position_crc")
    if None in (lo, hi, crc) or len(lo) != 3 or len(hi) != 3:
        return
    if crc != position_checksum(_mesh_unit_positions(zms)):
        return
    zms.bounding_box_min = Vector3(*lo)
    zms.bounding_box_max = Vector3(*hi)


def _file_skin(mesh):
    """Stashed per-vertex file blend weights and bone slots (4-tuples in
    file slot order), or None."""
    attrs = (mesh.attributes.get(FILE_BONE_WEIGHT_ATTRIBUTE),
             mesh.attributes.get(FILE_BONE_SLOT_ATTRIBUTE))
    if any(a is None or a.domain != 'POINT' or a.data_type != 'QUATERNION'
           for a in attrs):
        return None
    stash = []
    for attr in attrs:
        flat = [0.0] * (len(mesh.vertices) * 4)
        attr.data.foreach_get("value", flat)
        stash.append([tuple(flat[i:i + 4]) for i in range(0, len(flat), 4)])
    weights, slots = stash
    return weights, [tuple(int(s) if s.is_integer() else -1 for s in q) for q in slots]


def _unchanged_file_skin(mesh, bones, file_weights, file_slots):
    """Per mesh vertex: True while its vertex-group weights are still exactly
    what importing the stashed file weights produced.

    Both importers make one vertex group per bone table entry, in table
    order, and add every slot with weight > 0 to its bone's group (the
    first table slot holding that bone id; the exporter maps group g back
    to bones[g]). Slot order, zero-weight slots and the unnormalized weight
    bits are not in the groups, so only the stash can reproduce them.
    """
    group_of_slot = [bones.index(b) for b in bones]
    unchanged = []
    for vert, weights, slots in zip(mesh.vertices, file_weights, file_slots):
        expected = {}
        for w, s in zip(weights, slots):
            if not 0 <= s < len(bones):
                expected = None
                break
            if w > 0.0:
                expected[group_of_slot[s]] = w  # importers add with REPLACE
        unchanged.append(expected is not None and
                         sorted((g.group, g.weight) for g in vert.groups) ==
                         sorted(expected.items()))
    return unchanged


def _restore_file_skin(zms, source_vertices, file_weights, file_slots, unchanged):
    """Write the stashed file weights and slots for every exported vertex
    whose source vertex still has its imported vertex-group weights."""
    for v, vert_idx in zip(zms.vertices, source_vertices):
        if unchanged[vert_idx]:
            v.bone_weights = list(file_weights[vert_idx])
            v.bone_slots = list(file_slots[vert_idx])
            v.bone_indices = [zms.bones[s] for s in v.bone_slots]


def _bone_slots(v, bone_table):
    """Bone table slot written for each of the vertex's 4 bone ids.

    The file's own slots (v.bone_slots) are kept while they still resolve
    to v.bone_indices, since bone_table.index() returns the first slot of a
    bone listed twice. Otherwise each bone id's first slot, 0 if missing.
    """
    bone_ids = v.bone_indices[:4]
    slots = v.bone_slots
    if (slots is not None and len(slots) == len(bone_ids) and
            all(0 <= s < len(bone_table) and bone_table[s] == b
                for s, b in zip(slots, bone_ids))):
        return list(slots)
    result = []
    for bone_id in bone_ids:
        try:
            result.append(bone_table.index(bone_id))
        except ValueError:
            result.append(0)
    return result


def _mesh_has_colors(mesh):
    """True if the mesh carries vertex colors (legacy or 4.x attributes)."""
    if len(mesh.vertex_colors) > 0:
        return True
    try:
        return len(mesh.color_attributes) > 0
    except Exception:
        return False


def _loop_color(mesh, loop_idx, vert_idx):
    """RGBA tuple for a mesh loop, or None if the mesh has no colors.

    Prefers legacy vertex colors (per-loop), then 4.x color attributes
    (per-loop for CORNER domain, per-vertex for POINT domain).
    """
    if len(mesh.vertex_colors) > 0:
        layer = mesh.vertex_colors[0]
        if loop_idx < len(layer.data):
            color = layer.data[loop_idx].color
            return (color[0], color[1], color[2],
                    color[3] if len(color) > 3 else 1.0)
        return None
    try:
        attrs = mesh.color_attributes
    except Exception:
        return None
    if len(attrs) == 0:
        return None
    attr = attrs[0]
    try:
        if attr.domain == 'CORNER':
            if loop_idx < len(attr.data):
                color = attr.data[loop_idx].color
            else:
                return None
        else:  # POINT and other per-vertex domains
            if vert_idx < len(attr.data):
                color = attr.data[vert_idx].color
            else:
                return None
    except Exception:
        return None
    return (color[0], color[1], color[2],
            color[3] if len(color) > 3 else 1.0)


def export_zms_mesh_object(obj, filepath, version=8, export_normals=True,
                           export_colors=True, export_uv=True, report=None,
                           apply_world_transform=True, convert_coordinates=False):
    """Export a Blender mesh object to a ZMS file (shared by the manual
    export operator and the zone exporter).

    Args:
        obj: the Blender mesh object to export
        filepath: destination .ZMS path
        version: ZMS version (5-8)
        report: optional callable(level, message) for progress messages
        convert_coordinates: apply the Blender -> ROSE (x, -y, z) mirror.
            Defaults to False so export is the exact inverse of import
            (importers read vertices verbatim). When True, triangle winding
            is swapped to compensate the mirror determinant (-1) so faces
            and normals stay consistent.

    Returns:
        None on success, or an error string on failure.
    """
    if report is None:
        report = lambda level, msg: None
    # report callbacks take a string level ('INFO'/'ERROR'); wrap into the
    # set form bpy operator reports expect.
    raw_report = report
    report = lambda level, msg: raw_report({level}, msg)

    # The operator's enum property passes a string identifier
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 8

    filepath = Path(filepath)

    if obj is None or obj.type != 'MESH':
        return "No mesh object selected"

    mesh = obj.data

    # Try to detect version from imported metadata
    if "zms_version" in obj:
        try:
            version = int(obj["zms_version"])
        except Exception:
            pass

    # C++ uses uint16 for num_verts, num_faces in memory
    # Version 5/6 file format uses uint32 for counts
    # Version 7/8 file format uses uint16 for counts (matches C++ memory)
    # Version 9 uses u32 counts/indices for large meshes
    count_limit = 0xFFFFFFFF if version >= 9 else 65535
    if len(mesh.vertices) > count_limit:
        return f"Mesh has {len(mesh.vertices)} vertices (max {count_limit} for v{version})."

    mesh.calc_loop_triangles()
    if len(mesh.loop_triangles) > count_limit:
        return f"Mesh has {len(mesh.loop_triangles)} triangles (max {count_limit} for v{version})."

    # Custom normals come from the source mesh (see _corner_normals); the
    # bmesh round trip below keeps loop order, so indices still line up.
    normal_matrix = None
    if apply_world_transform:
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    corner_normals = _corner_normals(mesh, normal_matrix) if export_normals else None

    # Apply all transformations before export (only for world-space meshes;
    # imported ROSE meshes are kept in local space for a faithful round trip)
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    if apply_world_transform:
        bmesh.ops.transform(bm, matrix=obj.matrix_world, verts=bm.verts)

    # Create a temporary mesh with transformations applied
    temp_mesh = bpy.data.meshes.new("temp_export")
    bm.to_mesh(temp_mesh)
    bm.free()
    temp_mesh.calc_loop_triangles()

    # Restore ZMS metadata from the original object (if available)
    orig_materials = None
    orig_strips = None
    orig_pool = None
    orig_bones = None

    if "zms_materials" in obj:
        try:
            orig_materials = ast.literal_eval(obj["zms_materials"])
        except Exception:
            orig_materials = None

    if "zms_strips" in obj:
        try:
            orig_strips = ast.literal_eval(obj["zms_strips"])
        except Exception:
            orig_strips = None

    if "zms_pool" in obj:
        try:
            orig_pool = obj["zms_pool"]
        except Exception:
            orig_pool = None

    if "zms_bones" in obj:
        try:
            orig_bones = ast.literal_eval(obj["zms_bones"])
        except Exception:
            orig_bones = None

    # Create ZMS from mesh. The operator class cannot be instantiated
    # (bpy_struct), so the methods are called with None as self - they only
    # use getattr-based defaults and the explicit parameters.
    source_vertices = []
    zms = ExportZMS.zms_from_mesh_data(None, temp_mesh, obj, orig_bones, version,
                                       export_normals=export_normals,
                                       export_colors=export_colors,
                                       export_uv=export_uv,
                                       convert_coordinates=convert_coordinates,
                                       report=report,
                                       corner_normals=corner_normals,
                                       source_vertices=source_vertices)

    # Clean up temp mesh
    bpy.data.meshes.remove(temp_mesh)

    if zms is None:
        return "ZMS creation failed"

    # Apply restored metadata. Strips, material face counts and the exact
    # file normals, bounding box and skin weights belong to the imported
    # triangle list: after a topology edit (decimate, subdivide, re-mesh)
    # they would describe the old layout.
    topology_change = _topology_change(obj, zms)
    if topology_change is None:
        if orig_materials is not None:
            zms.materials = orig_materials
        if orig_strips is not None:
            zms.strips = orig_strips
        # Exact file normals only where the object adds no rotation/shear
        # (world export of a moved or uniformly scaled object is fine)
        file_normals = None
        if zms.normals_enabled() and (normal_matrix is None or
                                      _is_identity(normal_matrix.normalized())):
            file_normals = _file_normals(mesh)
        unchanged = (_unchanged_file_normals(mesh, file_normals)
                     if file_normals is not None else None)
        if unchanged is not None:
            _restore_file_normals(zms, source_vertices, file_normals,
                                  unchanged, convert_coordinates)
        _restore_bounding_box(obj, zms)
        # File slot order, zero-weight slots and unnormalized weights for
        # vertices whose vertex-group weights are untouched
        skin = _file_skin(mesh) if zms.bones_enabled() and zms.bones else None
        if skin is not None:
            _restore_file_skin(zms, source_vertices, *skin,
                               _unchanged_file_skin(mesh, zms.bones, *skin))
    elif orig_materials or orig_strips:
        report('INFO', f"{obj.name}: {topology_change}; writing empty "
                       f"triangle strips and material face counts")
    if orig_pool is not None:
        zms.pool = orig_pool
    if orig_bones is not None:
        zms.bones = orig_bones

    # Final validation - C++ uses uint16 for everything in memory (u32 for v9)
    if len(zms.vertices) > count_limit:
        return f"After processing: {len(zms.vertices)} vertices (max {count_limit}). Mesh has UV seams that split vertices."

    # Validate indices don't exceed vertex count
    max_idx = 0
    for idx in zms.indices:
        max_idx = max(max_idx, int(idx.x), int(idx.y), int(idx.z))
    if max_idx >= len(zms.vertices):
        return f"Face indices reference vertices that don't exist! Max index: {max_idx}, Vertex count: {len(zms.vertices)}"

    try:
        with open(str(filepath), "wb") as f:
            ExportZMS.write_zms(f, zms)
    except Exception as e:
        return f"Failed to write ZMS file: {str(e)}"

    report('INFO', f"Exported {filepath.name} (v{zms.version}, {len(zms.vertices)} verts, {len(zms.indices)} tris)")
    return None


class ExportZMS(bpy.types.Operator, ExportHelper):
    bl_idname = "rose.export_zms"
    bl_label = "Export ROSE Mesh (.zms)"
    bl_options = {"PRESET"}

    filename_ext = ".ZMS"
    filter_glob: StringProperty(default="*.ZMS", options={"HIDDEN"})

    export_version: EnumProperty(
        name="ZMS Version",
        description="Choose ZMS file version to export",
        items=[
            ('8', "Version 8 (ZMS0008)", "Modern format, recommended"),
            ('9', "Version 9 (ZMS0009)", "Large-mesh format (u32 counts/indices)"),
            ('7', "Version 7 (ZMS0007)", "Version 7 format"),
            ('6', "Version 6 (ZMS0006)", "Legacy format with materials"),
            ('5', "Version 5 (ZMS0005)", "Oldest format"),
        ],
        default='8',
    )
    
    export_normals: BoolProperty(
        name="Export Normals",
        description="Export vertex normals",
        default=True,
    )
    
    export_colors: BoolProperty(
        name="Export Vertex Colors",
        description="Export vertex colors if available",
        default=True,
    )
    
    export_uv: BoolProperty(
        name="Export UV Coordinates",
        description="Export UV coordinates",
        default=True,
    )

    def execute(self, context):
        error = export_zms_mesh_object(
            context.active_object,
            self.filepath,
            version=self.export_version,
            export_normals=self.export_normals,
            export_colors=self.export_colors,
            export_uv=self.export_uv,
            report=self.report,
            apply_world_transform=False,
            convert_coordinates=False,
        )
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {"FINISHED"}
    
    def zms_from_mesh_data(self, mesh, obj=None, orig_bones=None, version=8,
                           export_normals=None, export_colors=None, export_uv=None,
                           convert_coordinates=False, report=None,
                           corner_normals=None, source_vertices=None):
        """Extract ZMS data from mesh data.

        corner_normals: optional per-loop normals (see _corner_normals);
            derived from `mesh` when omitted. When present, each exported
            vertex gets the average over the loops that share its
            (vertex, uv, color) key; otherwise vertex.normal is used.
        source_vertices: optional list, filled with the mesh vertex index of
            every exported vertex.
        """
        # Create a report function wrapper
        if report is None:
            report = lambda level, message: (self.report({level}, message)
                                             if hasattr(self, 'report') else None)
        if export_normals is None:
            export_normals = getattr(self, 'export_normals', True)
        if export_colors is None:
            export_colors = getattr(self, 'export_colors', True)
        if export_uv is None:
            export_uv = getattr(self, 'export_uv', True)
        zms = ZMS(report_func=report)
        zms.version = version
        
        if version == 5:
            zms.identifier = "ZMS0005"
        elif version == 6:
            zms.identifier = "ZMS0006"
        elif version == 7:
            zms.identifier = "ZMS0007"
        elif version == 9:
            zms.identifier = "ZMS0009"
        else:
            zms.identifier = "ZMS0008"

        # std::vector<uint16> bone_indices
        if orig_bones is not None:
            zms.bones = list(orig_bones)
        else:
            zms.bones = []

        # Calculate flags (int vertex_format)
        zms.flags = VertexFlags.POSITION

        if export_normals and len(mesh.vertices) > 0:
            zms.flags |= VertexFlags.NORMAL

        if export_colors and _mesh_has_colors(mesh):
            zms.flags |= VertexFlags.COLOR

        if export_uv:
            if len(mesh.uv_layers) >= 1 and len(mesh.uv_layers[0].data) > 0:
                zms.flags |= VertexFlags.UV1
            if len(mesh.uv_layers) >= 2 and len(mesh.uv_layers[1].data) > 0:
                zms.flags |= VertexFlags.UV2
            if len(mesh.uv_layers) >= 3 and len(mesh.uv_layers[2].data) > 0:
                zms.flags |= VertexFlags.UV3
            if len(mesh.uv_layers) >= 4 and len(mesh.uv_layers[3].data) > 0:
                zms.flags |= VertexFlags.UV4

        # Detect bone/weight presence
        has_weights = False
        if obj is not None and hasattr(obj, "data"):
            try:
                has_weights = any(len(v.groups) > 0 for v in obj.data.vertices)
            except Exception:
                has_weights = False

        if has_weights:
            zms.flags |= VertexFlags.BONE_WEIGHT
            zms.flags |= VertexFlags.BONE_INDEX

        if zms.normals_enabled() and (corner_normals is None or
                                      len(corner_normals) != len(mesh.loops)):
            corner_normals = _corner_normals(mesh)

        # Split vertices by unique UV coordinates
        vertex_map = {}
        if source_vertices is None:
            source_vertices = []
        # Exported vertex of every loop, for the per-key normal average
        loop_vertex = [-1] * len(mesh.loops)

        # Process each triangle
        for tri in mesh.loop_triangles:
            tri_indices = []
            
            for loop_idx in tri.loops:
                loop = mesh.loops[loop_idx]
                vert_idx = loop.vertex_index
                vert = mesh.vertices[vert_idx]
                
                # Build a key with vertex index and UV coordinates
                uv_key = [vert_idx]
                
                for uv_idx in range(4):
                    if uv_idx < len(mesh.uv_layers) and len(mesh.uv_layers[uv_idx].data) > loop_idx:
                        uv = mesh.uv_layers[uv_idx].data[loop_idx].uv
                        uv_key.extend([round(uv[0], 6), round(uv[1], 6)])
                
                if export_colors and zms.colors_enabled():
                    loop_c = _loop_color(mesh, loop_idx, vert_idx)
                    if loop_c is not None:
                        uv_key.extend([round(c, 6) for c in loop_c])
                
                key = tuple(uv_key)
                
                # CRITICAL CHECK: Ensure we don't exceed uint16 max for indices.
                # Version 9 uses u32 counts/indices for large meshes, so the
                # uint16 cap applies only below v9 (the caller already gates
                # the pre-split mesh size via count_limit).
                if version < 9 and len(zms.vertices) >= 65535:
                    report('ERROR', f"Vertex count would exceed 65,535 after UV splitting. Current: {len(zms.vertices)}. Reduce subdivision or use fewer UV seams.")
                    return None
                
                if key not in vertex_map:
                    v = Vertex()
                    if convert_coordinates:
                        # vec3 position - apply Blender → Rose coordinate transform
                        # Both use Z-up, inverse of import transform (x, -y, z) -> (x, -y, z)
                        v.position = Vector3(vert.co.x, -vert.co.y, vert.co.z)
                    else:
                        # Round-trip: importers read vertices verbatim
                        v.position = Vector3(vert.co.x, vert.co.y, vert.co.z)
                    
                    # Scale positions for version 5/6 (stored *100 in file)
                    if version <= 6:
                        v.position.x *= 100.0
                        v.position.y *= 100.0
                        v.position.z *= 100.0
                    
                    # vec3 normal: set after all triangles (per-key average)

                    # zz_color (4x float)
                    if zms.colors_enabled():
                        loop_c = _loop_color(mesh, loop_idx, vert_idx)
                        if loop_c is not None:
                            v.color = Color4(loop_c[0], loop_c[1], loop_c[2],
                                           loop_c[3])
                        else:
                            v.color = Color4(1.0, 1.0, 1.0, 1.0)
                    
                    # Set UV coordinates (flip V) - vec2
                    for uv_idx in range(4):
                        if uv_idx < len(mesh.uv_layers) and len(mesh.uv_layers[uv_idx].data) > loop_idx:
                            uv = mesh.uv_layers[uv_idx].data[loop_idx].uv
                            v_coord = 1.0 - uv[1]  # Flip V coordinate
                            
                            if uv_idx == 0:
                                v.uv1 = Vector2(uv[0], v_coord)
                            elif uv_idx == 1:
                                v.uv2 = Vector2(uv[0], v_coord)
                            elif uv_idx == 2:
                                v.uv3 = Vector2(uv[0], v_coord)
                            elif uv_idx == 3:
                                v.uv4 = Vector2(uv[0], v_coord)

                    # Bone weights (vec4 - 4x float) and indices (vec4 stored as uint16/uint32 depending on version)
                    if zms.bones_enabled() and obj is not None:
                        groups = []
                        try:
                            orig_v = obj.data.vertices[vert_idx]
                            groups = [(g.group, g.weight) for g in orig_v.groups]
                        except Exception:
                            groups = []

                        groups.sort(key=lambda x: x[1], reverse=True)
                        top = groups[:4]
                        total = sum(w for _, w in top) or 1.0

                        weights = [w / total for _, w in top] + [0.0] * (4 - len(top))
                        group_indices = [int(gi) for gi, _ in top] + [0] * (4 - len(top))

                        # Convert group indices to bone IDs using zms.bones (uint16 values)
                        bone_ids = []
                        for gi in group_indices:
                            if 0 <= gi < len(zms.bones):
                                bone_ids.append(zms.bones[gi])
                            else:
                                bone_ids.append(0)

                        v.bone_weights = weights[:4]
                        v.bone_indices = bone_ids[:4]

                    new_idx = len(zms.vertices)
                    zms.vertices.append(v)
                    source_vertices.append(vert_idx)
                    vertex_map[key] = new_idx

                tri_indices.append(vertex_map[key])
                loop_vertex[loop_idx] = vertex_map[key]

            # usvec3 - 3x uint16 indices per face.
            # The (x, -y, z) coordinate mirror has determinant -1, so when
            # convert_coordinates is on the winding is swapped to keep
            # faces and normals consistent instead of inverted.
            if len(tri_indices) == 3:
                if convert_coordinates:
                    zms.indices.append(Vector3(tri_indices[0], tri_indices[2], tri_indices[1]))
                else:
                    zms.indices.append(Vector3(tri_indices[0], tri_indices[1], tri_indices[2]))

        # vec3 normals. Custom (corner) normals win over vertex.normal: they
        # are what the viewport shows, and they stay identical across pieces
        # split from one mesh. Loops sharing an exported key are averaged
        # (each loop once, even when an n-gon's triangulation reuses it).
        if zms.normals_enabled():
            sums = None
            if corner_normals is not None:
                sums = [[0.0, 0.0, 0.0] for _ in zms.vertices]
                for loop_idx, new_idx in enumerate(loop_vertex):
                    if new_idx < 0:
                        continue
                    n = corner_normals[loop_idx]
                    s = sums[new_idx]
                    s[0] += n[0]
                    s[1] += n[1]
                    s[2] += n[2]
            for new_idx, v in enumerate(zms.vertices):
                n = None
                if sums is not None:
                    s = sums[new_idx]
                    length = math.sqrt(s[0] * s[0] + s[1] * s[1] + s[2] * s[2])
                    if length > 1e-12:
                        n = (s[0] / length, s[1] / length, s[2] / length)
                if n is None:
                    n = mesh.vertices[source_vertices[new_idx]].normal
                if convert_coordinates:
                    # Blender → Rose coordinate transform, inverse of the
                    # import (x, -y, z) mirror (both Z-up)
                    v.normal = Vector3(n[0], -n[1], n[2])
                else:
                    v.normal = Vector3(n[0], n[1], n[2])

        # Calculate bounding box (vec3 pmin, pmax)
        if len(zms.vertices) > 0:
            # Get positions (v5/v6 are already scaled, so divided back)
            positions = _mesh_unit_positions(zms)

            min_x = min(p[0] for p in positions)
            min_y = min(p[1] for p in positions)
            min_z = min(p[2] for p in positions)
            max_x = max(p[0] for p in positions)
            max_y = max(p[1] for p in positions)
            max_z = max(p[2] for p in positions)
            
            zms.bounding_box_min = Vector3(min_x, min_y, min_z)
            zms.bounding_box_max = Vector3(max_x, max_y, max_z)
        
        return zms

    @staticmethod
    def write_zms(f, zms):
        version = zms.version
        
        # Write identifier (null-terminated string)
        f.write(zms.identifier.encode('ascii') + b'\x00')
        
        # Write flags (uint32 in file, but int vertex_format in C++)
        f.write(struct.pack("<I", zms.flags))
        
        # Write bounding box (vec3 - 3x float)
        ExportZMS.write_vector3_f32(f, zms.bounding_box_min)
        ExportZMS.write_vector3_f32(f, zms.bounding_box_max)
        
        if version <= 6:
            ExportZMS._write_version6(f, zms, version)
        else:
            ExportZMS._write_version8(f, zms, version)
    
    @staticmethod
    def _write_version6(f, zms, version):
        """Write ZMS version 5 or 6 format
        
        File format uses uint32 for counts/indices
        C++ memory uses uint16 for counts
        """
        # std::vector<uint16> bone_indices - but stored as uint32 in file
        bone_table = zms.bones if zms.bones else []
        
        # Write bone count (uint32 in file)
        f.write(struct.pack("<I", len(bone_table)))
        for i, bone in enumerate(bone_table):
            f.write(struct.pack("<I", i))  # dummy index (uint32)
            f.write(struct.pack("<I", bone))  # bone index (uint32 in file, uint16 in C++)
        
        # Write vertex count (uint32 in file, uint16 num_verts in C++)
        vert_count = len(zms.vertices)
        f.write(struct.pack("<I", vert_count))
        
        # Write vertex data (each with vertex_id prefix as uint32)
        if zms.positions_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))  # vertex_id (uint32)
                ExportZMS.write_vector3_f32(f, v.position)  # vec3 (3x float)
        
        if zms.normals_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector3_f32(f, v.normal)  # vec3
        
        if zms.colors_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_color4(f, v.color)  # zz_color (4x float)
        
        if zms.bones_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                # vec4 blend_weight (4x float)
                for w in v.bone_weights[:4]:
                    f.write(struct.pack("<f", w))
                # vec4 blend_index (stored as uint32 in file, indices into bone_table)
                for idx in _bone_slots(v, bone_table):
                    f.write(struct.pack("<I", idx))
        
        if zms.tangents_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector3_f32(f, v.tangent)  # vec3
        
        if zms.uv1_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector2_f32(f, v.uv1)  # vec2
        
        if zms.uv2_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector2_f32(f, v.uv2)
        
        if zms.uv3_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector2_f32(f, v.uv3)
        
        if zms.uv4_enabled():
            for i, v in enumerate(zms.vertices):
                f.write(struct.pack("<I", i))
                ExportZMS.write_vector2_f32(f, v.uv4)
        
        # Write triangle indices (usvec3 stored as uint32 in file, uint16 in C++)
        f.write(struct.pack("<I", len(zms.indices)))  # uint32 num_faces in file
        for i, idx in enumerate(zms.indices):
            f.write(struct.pack("<I", i))  # triangle_id (uint32)
            f.write(struct.pack("<I", int(idx.x)))  # uint32 in file
            f.write(struct.pack("<I", int(idx.y)))
            f.write(struct.pack("<I", int(idx.z)))
        
        # Write materials (version 6 only) - uint16 matid_numfaces in C++, uint32 in file
        if version >= 6:
            f.write(struct.pack("<I", len(zms.materials)))  # uint32 in file
            for i, mat in enumerate(zms.materials):
                f.write(struct.pack("<I", i))  # index (uint32)
                f.write(struct.pack("<I", mat))  # uint32 in file (uint16 in C++)
    
    @staticmethod
    def _write_version8(f, zms, version):
        """Write ZMS version 7, 8 or 9 format

        File format matches C++ memory: uint16 for counts and indices,
        except version 9 which uses u32 vertex/triangle counts and u32
        indices for large meshes.
        """
        large = version >= 9
        # Write bone count and bones (uint16 - std::vector<uint16>)
        f.write(struct.pack("<H", len(zms.bones)))  # uint16 num_bones
        for bone in zms.bones:
            f.write(struct.pack("<H", bone))  # uint16 bone_indices[i]

        # Write vertex count (uint16 num_verts, u32 for v9 large meshes)
        vert_count = len(zms.vertices)
        if large:
            f.write(struct.pack("<I", vert_count))
        else:
            f.write(struct.pack("<H", vert_count))
        
        # Write vertex data (no vertex_id prefix)
        if zms.positions_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector3_f32(f, v.position)  # vec3
        
        if zms.normals_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector3_f32(f, v.normal)  # vec3
        
        if zms.colors_enabled():
            for v in zms.vertices:
                ExportZMS.write_color4(f, v.color)  # zz_color (4x float)
        
        if zms.bones_enabled():
            for v in zms.vertices:
                # vec4 blend_weight (4x float)
                for w in v.bone_weights[:4]:
                    f.write(struct.pack("<f", w))
                # vec4 blend_index (stored as uint16 in file, indices into bones list)
                for idx in _bone_slots(v, zms.bones):
                    f.write(struct.pack("<H", idx))  # uint16
        
        if zms.tangents_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector3_f32(f, v.tangent)  # vec3
        
        if zms.uv1_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector2_f32(f, v.uv1)  # vec2
        
        if zms.uv2_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector2_f32(f, v.uv2)
        
        if zms.uv3_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector2_f32(f, v.uv3)
        
        if zms.uv4_enabled():
            for v in zms.vertices:
                ExportZMS.write_vector2_f32(f, v.uv4)
        
        # Write indices (flat array) - usvec3 = 3x uint16 (u32 for v9)
        if large:
            f.write(struct.pack("<I", len(zms.indices)))  # u32 num_faces
            for idx in zms.indices:
                f.write(struct.pack("<I", int(idx.x)))  # u32
                f.write(struct.pack("<I", int(idx.y)))
                f.write(struct.pack("<I", int(idx.z)))
        else:
            f.write(struct.pack("<H", len(zms.indices)))  # uint16 num_faces
            for idx in zms.indices:
                f.write(struct.pack("<H", int(idx.x)))  # uint16
                f.write(struct.pack("<H", int(idx.y)))
                f.write(struct.pack("<H", int(idx.z)))
        
        # Write materials (uint16 matid_numfaces array)
        f.write(struct.pack("<H", len(zms.materials)))  # uint16 num_matids
        for mat in zms.materials:
            f.write(struct.pack("<H", mat))  # uint16
        
        # Write strips (uint16 ibuf_strip array)
        f.write(struct.pack("<H", len(zms.strips)))  # uint16 count
        for strip in zms.strips:
            f.write(struct.pack("<H", strip))  # uint16
        
        # Write pool (version 8 only)
        if version >= 8:
            f.write(struct.pack("<H", zms.pool))  # uint16
    
    @staticmethod
    def write_vector2_f32(f, vec):
        f.write(struct.pack("<f", vec.x))
        f.write(struct.pack("<f", vec.y))
    
    @staticmethod
    def write_vector3_f32(f, vec):
        f.write(struct.pack("<f", vec.x))
        f.write(struct.pack("<f", vec.y))
        f.write(struct.pack("<f", vec.z))
    
    @staticmethod
    def write_color4(f, color):
        f.write(struct.pack("<f", color.r))
        f.write(struct.pack("<f", color.g))
        f.write(struct.pack("<f", color.b))
        f.write(struct.pack("<f", color.a))