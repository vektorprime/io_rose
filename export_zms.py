from pathlib import Path
import struct
import ast

if "bpy" in locals():
    import importlib
else:
    from .rose.zms import *

import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ExportHelper


def export_zms_mesh_object(obj, filepath, version=8, export_normals=True,
                           export_colors=True, export_uv=True, report=None,
                           apply_world_transform=True, convert_coordinates=True):
    """Export a Blender mesh object to a ZMS file (shared by the manual
    export operator and the zone exporter).

    Args:
        obj: the Blender mesh object to export
        filepath: destination .ZMS path
        version: ZMS version (5-8)
        report: optional callable(level, message) for progress messages

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
    if len(mesh.vertices) > 65535:
        return f"Mesh has {len(mesh.vertices)} vertices. C++ uses uint16 (max 65,535)."

    mesh.calc_loop_triangles()
    if len(mesh.loop_triangles) > 65535:
        return f"Mesh has {len(mesh.loop_triangles)} triangles. C++ uses uint16 (max 65,535)."

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
    zms = ExportZMS.zms_from_mesh_data(None, temp_mesh, obj, orig_bones, version,
                                       export_normals=export_normals,
                                       export_colors=export_colors,
                                       export_uv=export_uv,
                                       convert_coordinates=convert_coordinates,
                                       report=report)

    # Clean up temp mesh
    bpy.data.meshes.remove(temp_mesh)

    if zms is None:
        return "ZMS creation failed"

    # Apply restored metadata
    if orig_materials is not None:
        zms.materials = orig_materials
    if orig_strips is not None:
        zms.strips = orig_strips
    if orig_pool is not None:
        zms.pool = orig_pool
    if orig_bones is not None:
        zms.bones = orig_bones

    # Final validation - C++ uses uint16 for everything in memory
    if len(zms.vertices) > 65535:
        return f"After processing: {len(zms.vertices)} vertices (max 65,535). Mesh has UV seams that split vertices."

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
                           convert_coordinates=True, report=None):
        """Extract ZMS data from mesh data"""
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

        if export_colors and len(mesh.vertex_colors) > 0:
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

        # Split vertices by unique UV coordinates
        vertex_map = {}

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
                
                if export_colors and len(mesh.vertex_colors) > 0:
                    color_layer = mesh.vertex_colors[0]
                    if loop_idx < len(color_layer.data):
                        color = color_layer.data[loop_idx].color
                        uv_key.extend([round(c, 6) for c in color])
                
                key = tuple(uv_key)
                
                # CRITICAL CHECK: Ensure we don't exceed uint16 max for indices
                if len(zms.vertices) >= 65535:
                    report({'ERROR'}, f"Vertex count would exceed 65,535 after UV splitting. Current: {len(zms.vertices)}. Reduce subdivision or use fewer UV seams.")
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
                    
                    if zms.normals_enabled():
                        if convert_coordinates:
                            # vec3 normal - apply Blender → Rose coordinate transform
                            # Both use Z-up, inverse of import transform (x, -y, z) -> (x, -y, z)
                            v.normal = Vector3(vert.normal.x, -vert.normal.y, vert.normal.z)
                        else:
                            v.normal = Vector3(vert.normal.x, vert.normal.y, vert.normal.z)
                    
                    # zz_color (4x float)
                    if zms.colors_enabled():
                        if len(mesh.vertex_colors) > 0 and loop_idx < len(mesh.vertex_colors[0].data):
                            color = mesh.vertex_colors[0].data[loop_idx].color
                            v.color = Color4(color[0], color[1], color[2], 
                                           color[3] if len(color) > 3 else 1.0)
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
                    vertex_map[key] = new_idx
                
                tri_indices.append(vertex_map[key])
            
            # usvec3 - 3x uint16 indices per face
            if len(tri_indices) == 3:
                zms.indices.append(Vector3(tri_indices[0], tri_indices[1], tri_indices[2]))
        
        # Calculate bounding box (vec3 pmin, pmax)
        if len(zms.vertices) > 0:
            # Get positions (accounting for scaling)
            positions = []
            for v in zms.vertices:
                if version <= 6:
                    # Already scaled, so divide back for bounding box calculation
                    positions.append((v.position.x / 100.0, v.position.y / 100.0, v.position.z / 100.0))
                else:
                    positions.append((v.position.x, v.position.y, v.position.z))
            
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
                for bone_id in v.bone_indices[:4]:
                    try:
                        idx = bone_table.index(bone_id)
                    except ValueError:
                        idx = 0
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
        """Write ZMS version 7 or 8 format
        
        File format matches C++ memory: uint16 for counts and indices
        """
        # Write bone count and bones (uint16 - std::vector<uint16>)
        f.write(struct.pack("<H", len(zms.bones)))  # uint16 num_bones
        for bone in zms.bones:
            f.write(struct.pack("<H", bone))  # uint16 bone_indices[i]
        
        # Write vertex count (uint16 num_verts)
        vert_count = len(zms.vertices)
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
                for bone_id in v.bone_indices[:4]:
                    try:
                        idx = zms.bones.index(bone_id)
                    except ValueError:
                        idx = 0
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
        
        # Write indices (flat array) - usvec3 = 3x uint16
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