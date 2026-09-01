from pathlib import Path
import shutil

import bpy
import bmesh
from bpy.props import StringProperty, BoolProperty, FloatProperty

if "bpy" in locals():
    import importlib
else:
    from .rose.zms import ZMS


def _texture_grids(image):
    w, h = image.size
    px = image.pixels[:]
    lum = [0.0] * (w * h)
    alp = [0.0] * (w * h)
    for i in range(w * h):
        lum[i] = 0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2]
        alp[i] = px[i * 4 + 3]
    blur = [0.0] * (w * h)
    for y in range(h):
        for x in range(w):
            s = 0.0
            c = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx = min(w - 1, max(0, x + dx))
                    yy = min(h - 1, max(0, y + dy))
                    s += lum[yy * w + xx]
                    c += 1
            blur[y * w + x] = s / c
    return w, h, alp, blur


def sanitize_bmesh(bm):
    # Original game meshes contain degenerate triangles and coincident
    # vertices that crash bmesh.ops.subdivide_edges
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    kill = [f for f in bm.faces
            if len(f.verts) < 3 or len(set(f.verts)) < 3]
    bmesh.ops.delete(bm, geom=kill, context='FACES')
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context='VERTS')


def original_coverage(bm, uvl, grids, alpha_threshold):
    w, h, alp, _ = grids
    total = 0
    kept = 0
    for f in bm.faces:
        us = [l[uvl].uv for l in f.loops]
        cu = sum(u.x for u in us) / len(us)
        cv = sum(u.y for u in us) / len(us)
        x = int(cu % 1.0 * w) % w
        y = int(cv % 1.0 * h) % h
        total += 1
        if alp[y * w + x] >= alpha_threshold:
            kept += 1
    return (kept / total) if total else 0.0


def enhance_bmesh(bm, uvl, grids, alpha_threshold, curvature, relief, clip=True):
    w, h, alp, blur = grids
    sanitize_bmesh(bm)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=2, use_grid_fill=True)
    for v in bm.verts:
        v.co.y += curvature * (v.co.x ** 2 + v.co.z ** 2)
    for _ in range(2):
        if len(bm.faces) * 9 <= 60000:
            bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=2, use_grid_fill=True)
        else:
            break
    if clip:
        bm.faces.ensure_lookup_table()
        kill = []
        for f in bm.faces:
            us = [l[uvl].uv for l in f.loops]
            cu = sum(u.x for u in us) / len(us)
            cv = sum(u.y for u in us) / len(us)
            x = int(cu % 1.0 * w) % w
            y = int(cv % 1.0 * h) % h
            if alp[y * w + x] < alpha_threshold:
                kill.append(f)
        bmesh.ops.delete(bm, geom=kill, context='FACES')
        boundary = [v for v in bm.verts if any(len(e.link_faces) == 1 for e in v.link_edges)]
        for _ in range(2):
            bmesh.ops.smooth_vert(bm, verts=boundary, factor=0.5,
                                  use_axis_x=True, use_axis_y=True, use_axis_z=True)
    bm.verts.index_update()
    bm.normal_update()
    relief_map = {}
    for f in bm.faces:
        for l in f.loops:
            u, v = l[uvl].uv
            x = int(u % 1.0 * w) % w
            y = int(v % 1.0 * h) % h
            li = y * w + x
            r = max(0.0, blur[li] - 0.25) ** 1.5 if alp[li] > 0.5 else 0.0
            vi = l.vert.index
            if vi in relief_map:
                relief_map[vi] = (relief_map[vi][0] + r, relief_map[vi][1] + 1)
            else:
                relief_map[vi] = (r, 1)
    for v in bm.verts:
        if v.index in relief_map:
            r, c = relief_map[v.index]
            v.co += v.normal * (r / c * relief)
    for f in bm.faces:
        f.smooth = True


class EnhanceWings(bpy.types.Operator):
    bl_idname = "rose.enhance_wings"
    bl_label = "Enhance ROSE Wings (silhouette geo + relief + export)"
    bl_description = ("Batch-process wing ZMS files: densify, clip geometry to the "
                      "texture silhouette, add curvature and feather relief, then "
                      "export back over the original (with backup)")
    bl_options = {"PRESET"}

    directory: StringProperty(
        name="Directory",
        description="Folder containing the wing .ZMS/.DDS pairs",
        default="",
    )
    name_filter: StringProperty(
        name="Name filter",
        description="Only process files whose upper-cased name contains this",
        default="WING",
    )
    alpha_threshold: FloatProperty(
        name="Alpha threshold",
        description="Faces whose center texture alpha is below this are removed",
        default=0.4, min=0.0, max=1.0,
    )
    curvature: FloatProperty(
        name="Curvature",
        description="Cup curvature factor applied as y += k*(x^2+z^2)",
        default=0.17, min=0.0, max=1.0,
    )
    relief: FloatProperty(
        name="Feather relief",
        description="Displacement amplitude for texture-driven feather relief",
        default=0.05, min=0.0, max=0.5,
    )
    min_coverage: FloatProperty(
        name="Min coverage",
        description="If fewer than this fraction of original faces sample opaque "
                    "texture, silhouette clipping is skipped for that mesh",
        default=0.5, min=0.0, max=1.0,
    )
    skip_processed: BoolProperty(
        name="Skip processed",
        description="Skip files that already look enhanced (vertex count > 1000)",
        default=True,
    )
    backup: BoolProperty(
        name="Backup originals",
        description="Copy originals to a backup_originals subfolder before overwriting",
        default=True,
    )

    def execute(self, context):
        from .export_zms import export_zms_mesh_object
        from .rose.zms import ZMS

        directory = Path(self.directory)
        if not directory.is_dir():
            self.report({'ERROR'}, f"Not a directory: {directory}")
            return {'CANCELLED'}

        zms_files = sorted(
            p for p in directory.glob("*.ZMS")
            if self.name_filter.upper() in p.name.upper()
        )
        if not zms_files:
            self.report({'ERROR'}, f"No *{self.name_filter}*.ZMS files in {directory}")
            return {'CANCELLED'}

        done, skipped, failed = [], [], []
        loaded_images = []
        for zms_path in zms_files:
            try:
                z = ZMS(str(zms_path))
            except Exception as e:
                failed.append((zms_path.name, f"parse error: {e}"))
                continue
            if self.skip_processed and len(z.vertices) > 1000:
                skipped.append((zms_path.name, "already enhanced"))
                continue
            if not z.uv1_enabled():
                skipped.append((zms_path.name, "no UV1"))
                continue

            tex_path = None
            for ext in (".DDS", ".dds", ".PNG", ".png"):
                cand = zms_path.with_suffix(ext)
                if cand.exists():
                    tex_path = cand
                    break
            if tex_path is None:
                skipped.append((zms_path.name, "no texture"))
                continue

            img = bpy.data.images.load(str(tex_path))
            loaded_images.append(img)
            grids = _texture_grids(img)

            mesh = bpy.data.meshes.new(zms_path.stem)
            verts = [(v.position.x, v.position.y, v.position.z) for v in z.vertices]
            faces = [(int(i.x), int(i.y), int(i.z)) for i in z.indices]
            mesh.from_pydata(verts, [], faces)
            uvl = mesh.uv_layers.new(name="UVMap")
            for loop_idx, loop in enumerate(mesh.loops):
                uv = z.vertices[loop.vertex_index].uv1
                uvl.data[loop_idx].uv = (uv.x, 1.0 - uv.y)
            mesh.update(calc_edges=True)

            bm = bmesh.new()
            bm.from_mesh(mesh)
            bm_uvl = bm.loops.layers.uv.active
            coverage = original_coverage(bm, bm_uvl, grids, self.alpha_threshold)
            clip = coverage >= self.min_coverage
            if not clip:
                self.report({'WARNING'}, f"{zms_path.name}: alpha coverage "
                            f"{coverage:.2f} below threshold, skipping silhouette clip")
            enhance_bmesh(bm, bm_uvl, grids,
                          self.alpha_threshold, self.curvature, self.relief,
                          clip=clip)
            bm.to_mesh(mesh)
            bm.free()
            mesh.update()

            # Keep the temp object OUT of the scene: linking dense meshes
            # triggers viewport/EEVEE updates that have crashed Blender
            obj = bpy.data.objects.new(zms_path.stem, mesh)

            if self.backup:
                backup_dir = directory / "backup_originals"
                backup_dir.mkdir(exist_ok=True)
                shutil.copy2(zms_path, backup_dir / zms_path.name)

            err = export_zms_mesh_object(
                obj, str(zms_path), version=z.version,
                apply_world_transform=False, convert_coordinates=False,
                report=self.report,
            )
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            if err:
                failed.append((zms_path.name, err))
                continue
            done.append(zms_path.name)

        for img in loaded_images:
            if img.users == 0:
                bpy.data.images.remove(img)

        self.report({'INFO'}, f"Enhanced {len(done)}: {', '.join(done)}")
        if skipped:
            self.report({'WARNING'}, f"Skipped {len(skipped)}: " +
                        "; ".join(f"{n} ({r})" for n, r in skipped))
        if failed:
            self.report({'ERROR'}, f"Failed {len(failed)}: " +
                        "; ".join(f"{n} ({r})" for n, r in failed))
        return {'FINISHED'}
