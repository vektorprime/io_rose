"""
Blender operator for importing ROSE Online EFT effect files.

An EFT file references particle systems (.PTL), meshes (.ZMS in
EFFECTMESH), transform animations (.ZMO in MOTION), per-vertex mesh
animations (.ZMO in EFFECTMESH) and an optional sound. This importer
mirrors the game client's effect_loader.rs:

- Positions (centimeters) map to Blender as (x, -y, z) / 100.
- Rotations (pitch/yaw/roll degrees) map through the same mirror:
  Rose quaternion Qz(yaw) * Qx(pitch) * Qy(-roll) becomes the Blender
  quaternion (w, x, -y, z).
- A path is only effective when its flag is set (where a flag exists)
  and the text is not empty / "NULL".
- repeat_count / animation_repeat_count of 0 means infinite repeat.
- start_delay is milliseconds.

Viewport representation:
- One `EFT_<name>` root empty with one child empty per particle/mesh
  slot carrying the live transform. Moving/rotating a slot edits the
  effect (the exporter reads the live transform back).
- Mesh slots load the real .ZMS mesh (same layout as the ZMS importer)
  with a material using the EFT's texture override and blend flags.
- Particle slots create one emitter mesh per PTL sequence, shaped by the
  motion (small sphere for radial sprays, oriented triangle for directed
  jets, disc for static puffs), with a legacy particle system using the
  calibrated mapping: normal_factor is initial m/s along the emitter
  normal, and the gravity weight carries the PTL gravity so Blender scene
  gravity never leaks in (zero-gravity effects hang still as in game).
- Each sequence renders as a pool of real camera-facing textured quads
  (one per particle). Legacy OBJECT duplis were tried first but proven
  pixel-identical whether the source is aimed or zeroed: duplis ignore
  the source object's rotation, so they can never billboard. Instead the
  legacy particle system runs invisible (render NONE) as the motion
  source, and a frame-change/render_pre sync copies evaluated particle
  positions onto real quads, aims them at the scene camera (FULL /
  Y-axis / FIXED per align_type, toggleable per effect root via
  eft_live_billboard) and pokes each quad's own AgeNorm material value.
  Each quad clones the sequence material, which evaluates color, alpha
  and texture-atlas keyframes from that AgeNorm value, so every particle
  animates with its own timing. Additive
  sequences use a Transparent+Emission mix driven by texture luminance
  (their textures carry no alpha; black adds no light, exactly like the
  game). The full PTL data is also stored
  as custom properties (and as a JSON text block) so nothing is lost.
  Preview in Material Preview / Rendered shading to see textures.
- Transform .ZMO animations (MOTION) are baked to an action on a
  `TRAJ` child of the slot, so the slot keeps the editable EFT base
  transform. Note the game *replaces* the base with the ZMO once the
  anim starts, while the preview *composes* them; they match exactly
  for the common case of an identity base transform.
- Per-vertex mesh morph .ZMOs are stored as properties (baking them to
  shape keys is left for a later version).
"""

import json
import math
from pathlib import Path

import bpy
import mathutils
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Quaternion, Vector

from .rose.eft import Eft
from .rose.ptl import Ptl
from .rose.zms import ZMS
from .rose.zmo import ZMO


# ---------------------------------------------------------------------------
# Asset resolution ("beside EFT + 3Ddata hunt")
# ---------------------------------------------------------------------------

def _split_vfs(vfs_path):
    """Split a VFS path like '3DDATA\\EFFECT\\PARTICLES\\x.ptl' into parts."""
    text = (vfs_path or "").replace("\\", "/")
    return [p for p in text.split("/") if p not in ("", ".")]


def _find_3ddata_root(start_dir):
    """Walk up from start_dir looking for a directory named 3Ddata."""
    current = Path(start_dir)
    for _ in range(8):
        if current.name.upper() == "3DDATA" and current.is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def resolve_vfs_path(eft_filepath, vfs_path):
    """Resolve a stored VFS path to a real file, or None.

    Search order:
    1. <3Ddata root>/<remainder after 3DDATA> (root found by walking up
       from the EFT file).
    2. Same directory as the EFT + basename.
    3. EFFECTMESH / PARTICLES / MOTION / TEXTURE subdirectories of the
       EFT directory + basename (and PARTICLES/TEXTURE for textures).
    """
    if not vfs_path or vfs_path == "NULL":
        return None
    eft_dir = Path(eft_filepath).parent
    parts = _split_vfs(vfs_path)
    candidates = []
    root = _find_3ddata_root(eft_dir)
    if root is not None:
        remainder = parts[1:] if parts and parts[0].upper() == "3DDATA" else parts
        if remainder:
            candidates.append(root.joinpath(*remainder))
    basename = parts[-1] if parts else vfs_path
    candidates.append(eft_dir / basename)
    for sub in ("EFFECTMESH", "PARTICLES", "MOTION", "TEXTURE",
                "EFFECTMESH/DEFEND_01", "PARTICLES/TEXTURE"):
        candidates.append(eft_dir / sub / basename)
    # Case-insensitive fallback (VFS lookups are uppercase-normalized).
    for cand in candidates:
        if cand.is_file():
            return cand
        if cand.parent.is_dir():
            lower = cand.name.lower()
            for child in cand.parent.iterdir():
                if child.name.lower() == lower and child.is_file():
                    return child
    return None


# ---------------------------------------------------------------------------
# Transform conversion (mirrors effect_loader.rs through the Y mirror)
# ---------------------------------------------------------------------------

def rose_pos_to_blender(x, y, z):
    return (x / 100.0, -y / 100.0, z / 100.0)


def blender_pos_to_rose_cm(location):
    return (location[0] * 100.0, -location[1] * 100.0, location[2] * 100.0)


def rose_euler_to_blender_quat(pitch_deg, yaw_deg, roll_deg):
    """Rose pitch/yaw/roll (degrees) -> Blender quaternion.

    The client composes yaw about up, pitch about right, roll about
    forward; through the (x, -y, z) mirror that is Qz(yaw) * Qx(pitch) *
    Qy(-roll) in Blender space, then (w, x, -y, z).
    """
    q = (Quaternion((0.0, 0.0, 1.0), math.radians(yaw_deg))
         @ Quaternion((1.0, 0.0, 0.0), math.radians(pitch_deg))
         @ Quaternion((0.0, 1.0, 0.0), math.radians(-roll_deg)))
    return Quaternion((q.w, q.x, -q.y, q.z))


def blender_quat_to_rose_euler(q):
    """Inverse of rose_euler_to_blender_quat: Blender quat -> (pitch, yaw, roll)."""
    qr = Quaternion((q.w, q.x, -q.y, q.z))
    e = qr.to_euler("ZXY")
    return (math.degrees(e.x), math.degrees(e.z), -math.degrees(e.y))


def set_slot_transform(obj, x, y, z, pitch, yaw, roll):
    obj.location = rose_pos_to_blender(x, y, z)
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rose_euler_to_blender_quat(pitch, yaw, roll)


# ---------------------------------------------------------------------------
# Blend helpers (decode tables mirror effect_loader.rs)
# ---------------------------------------------------------------------------

_BLEND_OP_NAMES = {1: "Add", 2: "Subtract", 3: "ReverseSubtract",
                   4: "Min", 5: "Max"}
_BLEND_FACTOR_NAMES = {1: "Zero", 2: "One", 3: "Src", 4: "OneMinusSrc",
                       5: "SrcAlpha", 6: "OneMinusSrcAlpha", 7: "DstAlpha",
                       8: "OneMinusDstAlpha", 9: "Dst", 10: "OneMinusDst",
                       11: "SrcAlphaSaturated"}


def _set_prop(obj, key, value):
    try:
        obj[key] = value
    except Exception:
        obj[key] = str(value)


def _move_content_under(slot, traj):
    """Reparent mesh/emitter content of a slot under its TRAJ child."""
    try:
        children = list(slot.children)
    except Exception:
        return
    for child in children:
        if child is traj:
            continue
        try:
            kind = child.get("eft_slot_type", "")
        except Exception:
            kind = ""
        if kind in ("MESH_GEOMETRY", "PARTICLE_EMITTER"):
            child.parent = traj


def _image_has_alpha(image):
    """True when a texture carries a meaningful alpha channel.

    Fire/glow sprites ship alpha=1 everywhere and rely on additive
    blending instead; those must use luminance-as-alpha to render.
    """
    if image is None:
        return False
    try:
        px = image.pixels[:]
    except Exception:
        return False
    if len(px) < 4:
        return False
    try:
        return min(px[3::4]) < 0.99
    except Exception:
        return False


def _eft_root_of(obj):
    """Nearest ancestor (or self) tagged as an EFT effect root."""
    seen = 0
    node = obj
    while node is not None and seen < 8:
        try:
            if node.get("eft_is_effect_root"):
                return node
        except Exception:
            return None
        node = node.parent
        seen += 1
    return None


def _live_world_matrix(obj):
    """World matrix composed from live location/rotation/scale properties.

    Object.matrix_world can lag authored transforms when read inside
    depsgraph handlers (proven: a camera moved just before a frame change
    still reports its old matrix). Composing from the properties is always
    current. Assumes identity parent inverses, which holds for everything
    the importer creates.
    """
    from mathutils import Matrix, Vector

    chain = []
    node = obj
    seen = 0
    while node is not None and seen < 16:
        chain.append(node)
        try:
            node = node.parent
        except Exception:
            break
        seen += 1
    mat = Matrix.Identity(4)
    for node in reversed(chain):
        try:
            loc = node.location
            mode = getattr(node, "rotation_mode", "XYZ")
            if mode == "QUATERNION":
                rot = node.rotation_quaternion.to_matrix().to_4x4()
            elif mode == "AXIS_ANGLE":
                rot = Matrix.Rotation(node.rotation_angle, 4,
                                      Vector(node.rotation_axis))
            else:
                rot = node.rotation_euler.to_matrix().to_4x4()
            scl = node.scale
        except Exception:
            continue
        mat = (mat @ Matrix.Translation(loc) @ rot
               @ Matrix.Diagonal(Vector((scl[0], scl[1], scl[2], 1.0))))
    return mat


def _find_camera(scene):
    """Scene camera, falling back to any camera object."""
    try:
        cam = scene.camera
        if cam is not None and cam.type == "CAMERA":
            return cam
        for obj in scene.objects:
            if obj.type == "CAMERA":
                return obj
    except Exception:
        pass
    return None


def _quad_basis(loc, mode, cam_world):
    """World-space 3x3 basis aiming a quad's +Z at the camera.

    FULL copies the camera rotation, Y yaws toward the camera around
    world up, anything else (FIXED) returns None to keep the quad's
    current orientation.
    """
    from mathutils import Matrix, Vector

    if cam_world is None or mode == "FIXED":
        return None
    if mode == "Y":
        to_cam = cam_world.translation - loc
        to_cam.y = 0.0
        if to_cam.length < 1e-6:
            return None
        z_axis = to_cam.normalized()
        x_axis = Vector((0.0, 1.0, 0.0)).cross(z_axis)
        if x_axis.length < 1e-6:
            return None
        x_axis.normalize()
        y_axis = z_axis.cross(x_axis).normalized()
        return Matrix((x_axis, y_axis, z_axis)).transposed()
    return cam_world.to_3x3()


def _sync_particle_quads(scene):
    """Copy evaluated legacy particles onto the real preview quads.

    For every PARTICLE_EMITTER with an eft_quad_pool, alive evaluated
    particles are mapped in order onto pool quads: world position from
    the particle, camera-facing aim, scale from the sequence base size
    times the particle size, and per-quad AgeNorm material value from
    age/lifetime. Surplus quads are hidden. Runs from frame-change and
    render_pre handlers (plain base-data writes, which stick there).
    Returns the number of quads placed.
    """
    import bpy as _bpy
    from mathutils import Matrix, Vector

    if scene is None:
        return 0
    cam = _find_camera(scene)
    try:
        cam_world = _live_world_matrix(cam) if cam is not None else None
    except Exception:
        cam_world = None
    try:
        deps = _bpy.context.evaluated_depsgraph_get()
    except Exception:
        deps = None
    try:
        objects = list(scene.objects)
    except Exception:
        return 0
    placed = 0
    for emitter in objects:
        try:
            if emitter.get("eft_slot_type") != "PARTICLE_EMITTER":
                continue
            pool = emitter.get("eft_quad_pool")
            if not pool:
                continue
            root = _eft_root_of(emitter)
            if root is not None and not root.get("eft_live_billboard", True):
                continue
            mode = emitter.get("eft_billboard", "FULL")
            try:
                size_m = max(float(emitter.get("eft_quad_size", 1.0)), 1e-6)
            except Exception:
                size_m = 1.0
            alive = []
            try:
                eo = deps.objects.get(emitter.name) if deps is not None else None
                if eo is not None:
                    for ps in eo.particle_systems:
                        for p in ps.particles:
                            try:
                                if p.alive_state == "ALIVE":
                                    alive.append(p)
                            except Exception:
                                continue
            except Exception:
                pass
            for i, qname in enumerate(list(pool)):
                try:
                    quad = _bpy.data.objects.get(qname)
                except Exception:
                    quad = None
                if quad is None:
                    continue
                if i >= len(alive):
                    try:
                        quad.hide_viewport = True
                        quad.hide_render = True
                    except Exception:
                        pass
                    continue
                p = alive[i]
                try:
                    loc = Vector(p.location)
                except Exception:
                    continue
                # Legacy Particle has no .age in 4.x: derive it from the
                # frame clock minus birth_time (both in frames).
                try:
                    birth = float(p.birth_time)
                    plife = float(p.lifetime or 0.0)
                    now = float(scene.frame_current)
                    if plife > 1e-6:
                        norm = max(0.0, min((now - birth) / plife, 1.0))
                    else:
                        norm = None
                except Exception:
                    norm = None
                try:
                    # Particle.size is already the final instance scale
                    # (it incorporates the settings particle size), so it
                    # must not be multiplied by the base size again.
                    psize = float(getattr(p, "size", 0.0) or 0.0)
                except Exception:
                    psize = 0.0
                s = max(psize if psize > 0.0 else size_m, 1e-6)
                try:
                    basis = _quad_basis(loc, mode, cam_world)
                    mat = Matrix.Translation(loc)
                    if basis is not None:
                        mat = mat @ basis.to_4x4()
                    else:
                        _, keep_q, _ = _live_world_matrix(quad).decompose()
                        mat = mat @ keep_q.to_matrix().to_4x4()
                    quad.matrix_world = (
                        mat @ Matrix.Diagonal((s, s, s, 1.0)))
                    quad.hide_viewport = False
                    quad.hide_render = False
                except Exception:
                    continue
                try:
                    if norm is not None:
                        m = quad.active_material
                        node = (m.node_tree.nodes.get("AgeNorm")
                                if m is not None else None)
                        if node is not None:
                            node.outputs[0].default_value = norm
                except Exception:
                    pass
                placed += 1
        except Exception:
            continue
    return placed


@persistent
def _eft_quad_sync_frame(scene, depsgraph=None):
    """frame_change_post: keep preview quads on their particles."""
    try:
        _sync_particle_quads(scene)
    except Exception as e:
        print(f"[io_rose] quad sync failed: {e}")


@persistent
def _eft_quad_sync_render(scene, depsgraph=None):
    """render_pre: place preview quads for the rendered frame."""
    try:
        _sync_particle_quads(scene)
    except Exception as e:
        print(f"[io_rose] quad render sync failed: {e}")


def register_quad_sync():
    try:
        import bpy as _bpy

        post = _bpy.app.handlers.frame_change_post
        if not any(getattr(h, "__name__", "") == "_eft_quad_sync_frame"
                   for h in post):
            post.append(_eft_quad_sync_frame)
        pre = _bpy.app.handlers.render_pre
        if not any(getattr(h, "__name__", "") == "_eft_quad_sync_render"
                   for h in pre):
            pre.append(_eft_quad_sync_render)
    except Exception as e:
        print(f"[io_rose] could not register quad sync: {e}")


def unregister_quad_sync():
    try:
        import bpy as _bpy

        post = _bpy.app.handlers.frame_change_post
        for h in [h for h in post
                  if getattr(h, "__name__", "") == "_eft_quad_sync_frame"]:
            post.remove(h)
        pre = _bpy.app.handlers.render_pre
        for h in [h for h in pre
                  if getattr(h, "__name__", "") == "_eft_quad_sync_render"]:
            pre.remove(h)
    except Exception:
        pass


class ImportEFT(bpy.types.Operator, ImportHelper):
    """Import ROSE Online effect file (.eft)"""

    bl_idname = "rose.import_eft"
    bl_label = "ROSE Effect (.eft)"
    bl_options = {"PRESET", "UNDO"}

    filename_ext = ".eft"
    filter_glob: StringProperty(
        default="*.eft;*.EFT",
        options={"HIDDEN"},
    )

    load_meshes: BoolProperty(
        name="Load Meshes",
        description="Load referenced .ZMS effect meshes",
        default=True,
    )
    load_textures: BoolProperty(
        name="Load Textures",
        description="Load .DDS textures for meshes and particles when found",
        default=True,
    )
    load_particles: BoolProperty(
        name="Load Particles",
        description="Load referenced .PTL particle files",
        default=True,
    )
    create_particle_systems: BoolProperty(
        name="Particle Preview",
        description="Create Blender particle systems approximating each PTL sequence",
        default=True,
    )
    apply_animations: BoolProperty(
        name="Bake Transform Animations",
        description="Bake MOTION .ZMO transform animations to object actions",
        default=True,
    )

    texture_extensions = [".DDS", ".dds", ".PNG", ".png"]

    # -- operator --------------------------------------------------------
    def execute(self, context):
        filepath = Path(self.filepath)

        def report_wrapper(level, message):
            self.report({level}, message)

        try:
            eft = Eft(str(filepath), report_func=report_wrapper)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to load EFT file: {e}")
            return {"CANCELLED"}

        fps = context.scene.render.fps if context.scene.render.fps > 0 else 30
        root_name = f"EFT_{filepath.stem}"
        collection = bpy.data.collections.new(root_name)
        context.scene.collection.children.link(collection)

        root = bpy.data.objects.new(root_name, None)
        collection.objects.link(root)
        _set_prop(root, "eft_is_effect_root", True)
        _set_prop(root, "eft_source_file", str(filepath))
        _set_prop(root, "eft_sound_file", eft.sound_file)
        _set_prop(root, "eft_sound_repeat", int(eft.sound_repeat_count))
        _set_prop(root, "eft_use_sound", int(eft.use_sound))
        _set_prop(root, "eft_sound_effective", eft.sound_path())
        _set_prop(root, "eft_live_billboard", True)

        self.report({"INFO"},
                    f"EFT {filepath.name}: {len(eft.particles)} particle(s), "
                    f"{len(eft.meshes)} mesh(es), sound={eft.sound_path()!r}")

        frame_end = 1
        for i, entry in enumerate(eft.particles):
            end = self._import_particle_slot(
                context, collection, root, eft, i, entry, str(filepath), fps)
            frame_end = max(frame_end, end)
        for i, entry in enumerate(eft.meshes):
            end = self._import_mesh_slot(
                context, collection, root, eft, i, entry, str(filepath), fps)
            frame_end = max(frame_end, end)

        if frame_end > context.scene.frame_end:
            context.scene.frame_end = frame_end
        context.view_layer.objects.active = root
        self.report({"INFO"},
                    "Particle preview uses textured camera-facing quads - "
                    "switch to Material Preview or Rendered shading.")
        return {"FINISHED"}

    # -- particle slots --------------------------------------------------
    def _import_particle_slot(self, context, collection, root, eft,
                              index, entry, eft_path, fps):
        base = Path(entry.particle_path()).stem or f"particle_{index:02d}"
        slot = bpy.data.objects.new(f"PTL_{index:02d}_{base}", None)
        collection.objects.link(slot)
        slot.parent = root
        set_slot_transform(slot, entry.position.x, entry.position.y,
                           entry.position.z, entry.pitch, entry.yaw, entry.roll)
        _set_prop(slot, "eft_slot_type", "PARTICLE")
        _set_prop(slot, "eft_slot_index", index)
        _set_prop(slot, "eft_particle_file", entry.particle_path())
        _set_prop(slot, "eft_anim_file", entry.animation_path())
        _set_prop(slot, "eft_anim_repeat", int(entry.animation_repeat_count))
        _set_prop(slot, "eft_use_anim_raw", int(entry.use_animation))
        _set_prop(slot, "eft_start_delay_ms", int(entry.start_delay))
        _set_prop(slot, "eft_is_linked", bool(entry.is_linked))
        _set_prop(slot, "eft_pitch", float(entry.pitch))
        _set_prop(slot, "eft_yaw", float(entry.yaw))
        _set_prop(slot, "eft_roll", float(entry.roll))
        _set_prop(slot, "eft_pos", [float(entry.position.x),
                                   float(entry.position.y),
                                   float(entry.position.z)])
        _set_prop(slot, "eft_skip_a", entry.skip_a_text)
        _set_prop(slot, "eft_skip_b", entry.skip_b_text)

        frame_end = 1
        ptl_path = resolve_vfs_path(eft_path, entry.particle_path()) if self.load_particles else None
        _set_prop(slot, "eft_ptl_resolved", str(ptl_path) if ptl_path else "")
        ptl = None
        if entry.particle_path() and self.load_particles:
            if ptl_path is None:
                self.report({"WARNING"},
                            f"PTL not found: {entry.particle_path()}")
            else:
                try:
                    ptl = Ptl(str(ptl_path))
                    _set_prop(slot, "eft_ptl_sequences", len(ptl.sequences))
                except Exception as e:
                    self.report({"WARNING"},
                                f"Failed to load PTL {ptl_path.name}: {e}")
        if ptl is not None:
            self._store_ptl_data(slot, ptl)
            if self.create_particle_systems:
                for si, seq in enumerate(ptl.sequences):
                    self._add_sequence_preview(
                        context, collection, slot, index, base, si,
                        seq, entry, ptl_path, fps)
                    seq_frames = self._sequence_frame_end(seq, entry, fps)
                    frame_end = max(frame_end, seq_frames)

        anim_path = resolve_vfs_path(eft_path, entry.animation_path())
        _set_prop(slot, "eft_anim_resolved", str(anim_path) if anim_path else "")
        if entry.animation_path() and anim_path is None:
            self.report({"WARNING"},
                        f"Transform ZMO not found: {entry.animation_path()}")
        if self.apply_animations and entry.animation_path() and anim_path is not None:
            traj = self._make_traj_child(collection, slot)
            _move_content_under(slot, traj)
            end = self._bake_transform_anim(
                traj, anim_path, entry.animation_repeat_count,
                entry.start_delay, fps)
            frame_end = max(frame_end, end)
        return frame_end

    def _store_ptl_data(self, slot, ptl):
        """Store the full PTL definition: summary props + JSON text block."""
        summary = []
        for seq in ptl.sequences:
            summary.append({
                "name": seq.name,
                "life": list(seq.life),
                "emit_rate": list(seq.emit_rate),
                "num_loops": seq.num_loops,
                "emit_radius_min": [seq.emit_radius_min.x, seq.emit_radius_min.y, seq.emit_radius_min.z],
                "emit_radius_max": [seq.emit_radius_max.x, seq.emit_radius_max.y, seq.emit_radius_max.z],
                "gravity_min": [seq.gravity_min.x, seq.gravity_min.y, seq.gravity_min.z],
                "gravity_max": [seq.gravity_max.x, seq.gravity_max.y, seq.gravity_max.z],
                "texture": seq.texture_path,
                "num_particles": seq.num_particles,
                "align_type": seq.align_type,
                "update_coords": seq.update_coords,
                "atlas": [seq.texture_atlas_cols, seq.texture_atlas_rows],
                "blend": [seq.dst_blend_mode, seq.src_blend_mode, seq.blend_op],
                "keyframes": [
                    {"type": kf.keyframe_type, "name": kf.type_name,
                     "start": [kf.start_min, kf.start_max],
                     "fade": kf.fade, "values": list(kf.values)}
                    for kf in seq.keyframes
                ],
            })
        text_name = f"{slot.name}_PTL.json"
        text = bpy.data.texts.new(text_name)
        text.write(json.dumps(summary, indent=1))
        _set_prop(slot, "eft_ptl_json", text_name)

    def _make_traj_child(self, collection, slot):
        """Create the TRAJ empty that carries a baked transform animation."""
        traj = bpy.data.objects.new(f"{slot.name}_TRAJ", None)
        collection.objects.link(traj)
        traj.parent = slot
        traj.rotation_mode = "QUATERNION"
        _set_prop(traj, "eft_slot_type", "TRAJ")
        _set_prop(traj, "eft_owner", slot.name)
        return traj

    @staticmethod
    def _seq_label(seq):
        """Filesystem-safe short name (PTL names arrive quoted: '"star01"')."""
        cleaned = "".join(
            c if (c.isalnum() or c in "_-") else "_"
            for c in seq.name.strip().strip('"').strip("'"))
        return cleaned.strip("_") or "seq"

    @staticmethod
    def _axis_range(seq, xyz_type, min_idx, max_idx, solo_type):
        lo = seq.avg_first_value(
            xyz_type, min_idx, seq.avg_first_value(solo_type, 0, 0.0))
        hi = seq.avg_first_value(
            xyz_type, max_idx, seq.avg_first_value(solo_type, 1, lo))
        return lo, hi

    @classmethod
    def _velocity_stats(cls, seq):
        """Average velocity + spread in Rose cm/s: (vec3, speed, spread)."""
        axes = []
        spread = 0.0
        for xyz_t, mn, mx, solo in ((11, 0, 3, 8), (11, 1, 4, 9),
                                   (11, 2, 5, 10)):
            lo, hi = cls._axis_range(seq, xyz_t, mn, mx, solo)
            axes.append((lo + hi) * 0.5)
            spread += abs(hi - lo)
        vx, vy, vz = axes
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        return (vx, vy, vz), speed, spread / 3.0

    @staticmethod
    def _size_stats(seq):
        """Average billboard size (m) + randomization factor from SizeXY."""
        sx0 = seq.avg_first_value(1, 0, 10.0)
        sy0 = seq.avg_first_value(1, 1, sx0)
        sx1 = seq.avg_first_value(1, 2, sx0)
        sy1 = seq.avg_first_value(1, 3, sy0)
        mx = max(sx0, sy0, sx1, sy1)
        mn = min(sx0, sy0, sx1, sy1)
        size_m = max(0.001, (sx0 + sy0 + sx1 + sy1) * 0.25 / 100.0)
        rnd = max(0.0, min((mx - mn) / mx if mx > 0 else 0.0, 1.0))
        return size_m, rnd

    @staticmethod
    def _gravity_blender(seq):
        """Average gravity mapped to Blender m/s^2: (gx, -gy, gz) / 100."""
        gx = (seq.gravity_min.x + seq.gravity_max.x) * 0.5 / 100.0
        gy = (seq.gravity_min.y + seq.gravity_max.y) * 0.5 / 100.0
        gz = (seq.gravity_min.z + seq.gravity_max.z) * 0.5 / 100.0
        return (gx, -gy, gz)

    @staticmethod
    def _base_rgba(seq):
        """Fallback color: midpoint of ColourRGBA / channel averages."""
        r = (seq.avg_first_value(7, 0, seq.avg_first_value(3, 0, 1.0))
             + seq.avg_first_value(7, 4, seq.avg_first_value(3, 1, 1.0))) * 0.5
        g = (seq.avg_first_value(7, 1, seq.avg_first_value(4, 0, 1.0))
             + seq.avg_first_value(7, 5, seq.avg_first_value(4, 1, 1.0))) * 0.5
        b = (seq.avg_first_value(7, 2, seq.avg_first_value(5, 0, 1.0))
             + seq.avg_first_value(7, 6, seq.avg_first_value(5, 1, 1.0))) * 0.5
        a = (seq.avg_first_value(7, 3, seq.avg_first_value(6, 0, 1.0))
             + seq.avg_first_value(7, 7, seq.avg_first_value(6, 1, 1.0))) * 0.5
        return (max(0.0, min(r, 1.0)), max(0.0, min(g, 1.0)),
                max(0.0, min(b, 1.0)), max(0.0, min(a, 1.0)))

    @staticmethod
    def _color_stops(seq, life_sec, base):
        """(pos01, rgba) control points from ColourRGBA(7)/Alpha(6) keyframes.

        Positions are keyframe start times normalized by the sequence life.
        """
        stops = [(0.0, list(base))]
        if life_sec <= 0:
            return stops
        rgb_at = {0.0: list(base[:3])}
        for kf in seq.keyframes_of_type(7):
            if len(kf.values) < 8:
                continue
            t = max(0.0, min((kf.start_min + kf.start_max) * 0.5 / life_sec, 1.0))
            rgba = [(kf.values[i] + kf.values[i + 4]) * 0.5 for i in range(4)]
            stops.append((t, [max(0.0, min(c, 1.0)) for c in rgba]))
            rgb_at[t] = [max(0.0, min(c, 1.0)) for c in rgba[:3]]
        for kf in seq.keyframes_of_type(6):
            if len(kf.values) < 2:
                continue
            t = max(0.0, min((kf.start_min + kf.start_max) * 0.5 / life_sec, 1.0))
            a = max(0.0, min((kf.values[0] + kf.values[1]) * 0.5, 1.0))
            best = base[:3]
            for kt in sorted(rgb_at):
                if kt <= t + 1e-6:
                    best = rgb_at[kt]
            stops.append((t, [best[0], best[1], best[2], a]))
        stops.sort(key=lambda s: s[0])
        merged = []
        for t, rgba in stops:
            if merged and abs(t - merged[-1][0]) < 1e-4:
                merged[-1] = (t, rgba)
            else:
                merged.append((t, rgba))
        return merged

    @staticmethod
    def _texture_cells(seq, life_sec, cells):
        """(t0, v0, t1, v1) atlas-cell animation from Texture(12) keyframes."""
        pts = []
        for kf in seq.keyframes_of_type(12):
            if len(kf.values) < 2:
                continue
            t = (kf.start_min + kf.start_max) * 0.5
            v = (kf.values[0] + kf.values[1]) * 0.5
            pts.append((t, v))
        if not pts:
            return None
        pts.sort()
        t0, v0 = pts[0]
        t1, v1 = pts[-1]
        if life_sec > 0:
            t0 = max(0.0, min(t0, life_sec))
            t1 = max(0.0, min(t1, life_sec))
        v0 = max(0.0, min(v0, cells - 1))
        v1 = max(0.0, min(v1, cells - 1))
        return (t0, v0, t1, v1)

    def _make_emitter(self, collection, slot, name, kind, vel_blender):
        """Build an emitter mesh suited to the sequence motion.

        kind is "radial" (small UV sphere, FACE emit gives outward normals
        so normal_factor sprays in all directions), "disc" (flat disc for
        static puffs with an emit radius) or "jet" (tiny triangle whose
        face normal is the spray direction, so normal_factor becomes the
        jet velocity). Falls back to a tiny up-facing triangle.
        """
        import mathutils as _bmath

        speed = vel_blender.length
        verts, faces = [(0.0, 0.0, 0.0)], []

        def _tri(normal):
            n = _bmath.Vector(normal)
            if n.length < 1e-9:
                n = _bmath.Vector((0.0, 0.0, 1.0))
            n.normalize()
            u = n.orthogonal().normalized() * 0.001
            w = n.cross(u).normalized() * 0.001
            a = u + w
            b = -u + w
            c = -w
            pts = [a, b, c]
            if (b - a).cross(c - a).dot(n) < 0:
                pts = [a, c, b]
            return [tuple(p) for p in pts], [(0, 1, 2)]

        if kind == "radial":
            import math
            r = 0.005
            verts, faces = [], []
            rings = 3
            for ri in range(rings + 1):
                phi = math.pi * ri / rings
                for si in range(8):
                    theta = 2.0 * math.pi * si / 8
                    verts.append((
                        r * math.sin(phi) * math.cos(theta),
                        r * math.sin(phi) * math.sin(theta),
                        r * math.cos(phi)))
            for ri in range(rings):
                for si in range(8):
                    a = ri * 8 + si
                    b = ri * 8 + (si + 1) % 8
                    c = (ri + 1) * 8 + si
                    d = (ri + 1) * 8 + (si + 1) % 8
                    faces.append((a, c, b))
                    faces.append((b, c, d))
        elif kind == "disc":
            import math
            r = 0.01
            verts = [(0.0, 0.0, 0.0)]
            for si in range(8):
                theta = 2.0 * math.pi * si / 8
                verts.append((r * math.cos(theta), r * math.sin(theta), 0.0))
            faces = [(0, 1 + si, 1 + (si + 1) % 8) for si in range(8)]
        else:
            n = tuple(vel_blender.normalized()) if speed > 1e-9 else (0.0, 0.0, 1.0)
            verts, faces = _tri(n)

        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        mesh.update(calc_edges=True)
        emitter = bpy.data.objects.new(name, mesh)
        collection.objects.link(emitter)
        emitter.parent = slot
        emitter.display_type = "WIRE"
        _set_prop(emitter, "eft_slot_type", "PARTICLE_EMITTER")
        return emitter

    def _sequence_frame_end(self, seq, entry, fps):
        start = 1 + int(entry.start_delay / 1000.0 * fps)
        life_frames = max(1, int(seq.avg_life * fps))
        rate = seq.avg_emit_rate if seq.avg_emit_rate > 0 else 1.0
        emit_frames = int(seq.num_particles / rate * fps) if seq.num_particles > 0 else life_frames
        loops = seq.num_loops if seq.num_loops > 0 else 1
        return start + emit_frames * loops + life_frames

    def _add_sequence_preview(self, context, collection, slot, index, base,
                               seq_index, seq, entry, ptl_path, fps):
        """One emitter + quad pool + invisible particle system per sequence."""
        from mathutils import Vector

        label = self._seq_label(seq)
        stem = f"PTL_{index:02d}_{base}_{seq_index:02d}_{label}"
        life_sec = max(0.04, seq.avg_life)

        # Motion analysis (Rose cm/s -> Blender m/s, (x, -y, z)).
        (vx, vy, vz), speed_cm, spread_cm = self._velocity_stats(seq)
        vel_bl = Vector((vx / 100.0, -vy / 100.0, vz / 100.0))
        er_min, er_max = seq.emit_radius_min, seq.emit_radius_max
        radius_m = max(
            (abs(er_min.x) + abs(er_max.x)
             + abs(er_min.y) + abs(er_max.y)
             + abs(er_min.z) + abs(er_max.z)) / 6.0 / 100.0, 0.0)
        if spread_cm > max(speed_cm, 25.0):
            kind = "radial"
            emit_speed = max(speed_cm, spread_cm) / 100.0
            rand_factor = max(0.0, min(spread_cm / max(emit_speed * 100.0, 1e-6), 1.0))
        elif speed_cm > 1.0:
            kind = "jet"
            emit_speed = speed_cm / 100.0
            rand_factor = max(0.0, min(spread_cm / max(speed_cm, 1e-6), 1.0))
        elif radius_m > 0.005:
            kind = "disc"
            emit_speed = 0.0
            rand_factor = 0.0
        else:
            kind = "jet"
            emit_speed = 0.0
            rand_factor = 0.0

        emitter = self._make_emitter(collection, slot, f"{stem}_emitter",
                                     kind, vel_bl)

        # Gravity: PTL gravity is authored per effect; Blender scene gravity
        # must not leak in. Rose/Blender gravity is ~always vertical, which
        # maps exactly onto a gravity weight.
        gx, gy, gz = self._gravity_blender(seq)
        scene_gz = context.scene.gravity[2]
        if abs(gz) < 1e-6 and abs(gx) + abs(gy) < 1e-6:
            grav_weight = 0.0
        elif abs(scene_gz) > 1e-6:
            grav_weight = max(-2.0, min(gz / scene_gz, 2.0))
        else:
            grav_weight = 0.0
        if (gx * gx + gy * gy) ** 0.5 > 0.05:
            self.report({"WARNING"},
                        f"Sequence {seq.name}: horizontal gravity "
                        f"({gx:.2f}, {gy:.2f}) has no Blender equivalent; "
                        "vertical part only.")
        _set_prop(emitter, "eft_preview_kind", kind)
        _set_prop(emitter, "eft_preview_speed", float(emit_speed))
        _set_prop(emitter, "eft_preview_gravity_weight", float(grav_weight))
        # Billboard behavior mirrors align_type (0 full camera billboard,
        # 1 fixed, 2 Y-axis billboard); the quad sync reads it per emitter.
        _set_prop(emitter, "eft_billboard",
                  "FULL" if seq.align_type == 0
                  else ("Y" if seq.align_type == 2 else "FIXED"))
        # The emitter mesh is only a simulation source: wire in the
        # viewport as an edit affordance, never in renders.
        try:
            emitter.hide_render = True
        except Exception:
            pass

        try:
            emitter.modifiers.new(f"PS_{label[:20] or 'seq'}", "PARTICLE_SYSTEM")
        except Exception as e:
            self.report({"WARNING"}, f"Could not add particle modifier: {e}")
            return
        pool_size = 0
        try:
            systems = list(emitter.particle_systems)
            psys = systems[-1] if systems else None
            if psys is None:
                return
            settings = psys.settings
            if settings is None:
                return
            settings.name = f"{stem}_settings"
            self._configure_particle_settings(
                settings, seq, entry, fps, life_sec,
                emit_speed, rand_factor, grav_weight)
            pool_size = max(1, min(int(settings.count or 1), 512))
        except Exception as e:
            self.report({"WARNING"},
                        f"Could not configure particles for {seq.name}: {e}")
        size_m, _ = self._size_stats(seq)
        pool = self._make_quad_pool(context, collection, slot, emitter,
                                    stem, seq, life_sec, ptl_path, pool_size)
        _set_prop(emitter, "eft_quad_pool", pool)
        _set_prop(emitter, "eft_quad_size", float(size_m))

    def _load_image(self, path):
        try:
            return bpy.data.images.load(str(path), check_existing=True)
        except Exception:
            return None

    def _make_quad_pool(self, context, collection, slot, emitter, stem,
                          seq, life_sec, ptl_path, pool_size):
        """Pool of real 1x1 textured quads, one per potential particle.

        The legacy particle system is invisible (render NONE); the sync
        handler copies evaluated particle state onto these quads, so each
        gets its own cloned AgeNorm-driven material. Quads start hidden
        until the first sync places them.
        """
        image = None
        tex_has_alpha = False
        if self.load_textures and seq.texture_path and seq.texture_path != "NULL" \
                and ptl_path is not None:
            tex_path = resolve_vfs_path(str(ptl_path), seq.texture_path)
            if tex_path is not None:
                image = self._load_image(tex_path)
                tex_has_alpha = _image_has_alpha(image)
            else:
                self.report({"WARNING"},
                            f"Sprite texture not found: {seq.texture_path}")
        additive = (seq.dst_blend_mode == 2)
        template = self._make_sprite_material(
            f"{stem}_mat", image, tex_has_alpha, seq, life_sec, additive)
        if template is None:
            return []
        names = []
        try:
            for i in range(max(1, int(pool_size))):
                mesh = bpy.data.meshes.new(f"{stem}_p{i:02d}_mesh")
                mesh.from_pydata(
                    [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0),
                     (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)],
                    [], [(0, 1, 2, 3)])
                try:
                    mesh.uv_layers.new(name="UVMap")
                    uvs = [(0.0, 0.0), (1.0, 0.0),
                           (1.0, 1.0), (0.0, 1.0)]
                    layer = mesh.uv_layers["UVMap"]
                    for li, loop in enumerate(mesh.loops):
                        layer.data[li].uv = uvs[li]
                except Exception:
                    pass
                mesh.update(calc_edges=True)
                try:
                    mesh.materials.append(template.copy())
                except Exception:
                    pass
                quad = bpy.data.objects.new(f"{stem}_p{i:02d}", mesh)
                collection.objects.link(quad)
                quad.parent = slot
                _set_prop(quad, "eft_slot_type", "EFT_QUAD")
                _set_prop(quad, "eft_quad_index", int(i))
                _set_prop(quad, "eft_emitter", emitter.name)
                try:
                    quad.hide_viewport = True
                    quad.hide_render = True
                except Exception:
                    pass
                names.append(quad.name)
        finally:
            try:
                bpy.data.materials.remove(template)
            except Exception:
                pass
        return names

    def _configure_particle_settings(self, settings, seq, entry, fps,
                                      life_sec, emit_speed, rand_factor,
                                      grav_weight):
        def _set(attr, value, warn=True):
            try:
                setattr(settings, attr, value)
            except Exception as e:
                if warn:
                    self.report({"WARNING"},
                                f"Particle setting {attr} failed: {e}")

        count = max(1, min(int(seq.num_particles), 10000))
        life_frames = max(1, int(life_sec * fps))
        start_frame = 1 + int(entry.start_delay / 1000.0 * fps)
        end_frame = self._sequence_frame_end(seq, entry, fps)
        _set("count", count)
        _set("frame_start", start_frame)
        _set("frame_end", end_frame)
        _set("lifetime", life_frames)
        _set("lifetime_random", 0.0, warn=False)
        _set("emit_from", "FACE")
        _set("distribution", "JIT")
        _set("physics_type", "NEWTON")
        _set("damping", 0.0)
        _set("drag_factor", 0.0, warn=False)
        _set("brownian_factor", 0.0)
        size_m, size_rnd = self._size_stats(seq)
        _set("particle_size", size_m)
        _set("size_random", size_rnd, warn=False)
        # Calibrated: normal_factor is initial m/s along the emitter normal,
        # so the oriented emitter mesh turns it into the PTL velocity.
        _set("normal_factor", max(0.0, emit_speed))
        _set("tangent_factor", 0.0)
        _set("factor_random", max(0.0, min(rand_factor, 1.0)), warn=False)
        _set("use_rotations", False)
        _set("use_rotation_instance", False, warn=False)
        _set("use_scale_instance", True)
        # The system is only a motion source: the visible preview is a
        # pool of real quads fed by the sync handler (legacy OBJECT
        # duplis ignore the source rotation, so they cannot billboard).
        _set("render_type", "NONE")
        _set("instance_object", None)
        try:
            settings.effector_weights.gravity = grav_weight
        except Exception as e:
            self.report({"WARNING"}, f"Gravity weight failed: {e}")

    @staticmethod
    def _node(tree, ntype, label, loc):
        node = tree.nodes.new(type=ntype)
        node.label = label
        node.location = loc
        return node

    def _make_sprite_material(self, name, image, tex_has_alpha, seq,
                               life_sec, additive):
        """Template sequence material driven by its AgeNorm value.

        Color/alpha follow the ColourRGBA(7)/Alpha(6) keyframes and the
        texture atlas follows Texture(12) keyframes, evaluated from the
        AgeNorm value node (0..1 over the particle life). Each preview
        quad clones this material and the sync handler pokes its own
        AgeNorm, so every particle animates with its own timing.

        Additive sequences (the game blends them as src+dst) are built as
        Transparent+Emission: an emission-only surface has no alpha, so it
        would render its black background opaque. The mix factor is the
        texture alpha when the texture carries one, otherwise the texture
        luminance (black adds no light, exactly like the game).
        """
        try:
            mat = bpy.data.materials.new(name)
        except Exception as e:
            self.report({"WARNING"}, f"Could not create material: {e}")
            return None
        try:
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            for node in list(nodes):
                nodes.remove(node)
            out = self._node(mat.node_tree, "ShaderNodeOutputMaterial",
                             "Out", (500, 0))
            norm = self._node(mat.node_tree, "ShaderNodeValue",
                              "AgeNorm", (-700, 0))
            norm.name = "AgeNorm"
            try:
                norm.outputs["Value"].default_value = 0.0
            except Exception:
                pass

            base = self._base_rgba(seq)
            stops = self._color_stops(seq, life_sec, base)
            # No texture: the quad would render as a solid color slab, so
            # fade it to a translucent placeholder instead.
            alpha_scale = 0.25 if image is None else 1.0
            ramp = self._node(mat.node_tree, "ShaderNodeValToRGB",
                              "LifeColor", (-300, 120))
            try:
                elements = ramp.color_ramp.elements
                while len(elements) < len(stops):
                    elements.new(0.5)
                for el, (pos, rgba) in zip(elements, stops):
                    el.position = max(0.0, min(pos, 1.0))
                    el.color = (rgba[0], rgba[1], rgba[2],
                                max(0.0, min(rgba[3] * alpha_scale, 1.0)))
                while len(elements) > len(stops):
                    elements.remove(elements[-1])
            except Exception as e:
                self.report({"WARNING"}, f"Could not fill color ramp: {e}")
            # The ramp maps particle age to color/alpha; without this link
            # it would sit at its default factor for the whole life.
            links.new(norm.outputs["Value"], ramp.inputs["Fac"])

            tex_color = None
            tex_alpha = None
            if image is not None:
                tex = self._node(mat.node_tree, "ShaderNodeTexImage",
                                 "Sprite", (-100, -160))
                tex.image = image
                try:
                    tex.image.colorspace_settings.name = "sRGB"
                except Exception:
                    pass
                self._atlas_uv(mat.node_tree, links, norm, seq, life_sec, tex)
                tex_color = tex.outputs["Color"]
                tex_alpha = tex.outputs["Alpha"]

            if additive:
                emission = self._node(mat.node_tree, "ShaderNodeEmission",
                                      "Glow", (300, 120))
                if tex_color is not None:
                    tint = self._node(mat.node_tree, "ShaderNodeVectorMath",
                                      "Tint", (100, 120))
                    tint.operation = "MULTIPLY"
                    links.new(tex_color, tint.inputs[0])
                    links.new(ramp.outputs["Color"], tint.inputs[1])
                    links.new(tint.outputs["Vector"], emission.inputs["Color"])
                else:
                    links.new(ramp.outputs["Color"], emission.inputs["Color"])
                try:
                    emission.inputs["Strength"].default_value = 1.0
                except Exception:
                    pass
                transparent = self._node(mat.node_tree,
                                         "ShaderNodeBsdfTransparent",
                                         "Hole", (300, -100))
                if tex_alpha is not None and tex_has_alpha:
                    shape = tex_alpha
                elif tex_color is not None:
                    lum = self._node(mat.node_tree, "ShaderNodeRGBToBW",
                                     "Luma", (100, -160))
                    links.new(tex_color, lum.inputs["Color"])
                    shape = lum.outputs["Val"]
                else:
                    shape = None
                mix = self._node(mat.node_tree, "ShaderNodeMixShader",
                                 "Add", (450, 40))
                if shape is not None:
                    fac = self._node(mat.node_tree, "ShaderNodeMath",
                                     "Fade", (300, -220))
                    fac.operation = "MULTIPLY"
                    links.new(shape, fac.inputs[0])
                    links.new(ramp.outputs["Alpha"], fac.inputs[1])
                    links.new(fac.outputs["Value"], mix.inputs["Fac"])
                else:
                    links.new(ramp.outputs["Alpha"], mix.inputs["Fac"])
                links.new(transparent.outputs["BSDF"], mix.inputs[1])
                links.new(emission.outputs["Emission"], mix.inputs[2])
                links.new(mix.outputs["Shader"], out.inputs["Surface"])
            else:
                bsdf = self._node(mat.node_tree, "ShaderNodeBsdfPrincipled",
                                  "PBR", (300, 120))
                if tex_color is not None:
                    tint = self._node(mat.node_tree, "ShaderNodeVectorMath",
                                      "Tint", (100, 120))
                    tint.operation = "MULTIPLY"
                    links.new(tex_color, tint.inputs[0])
                    links.new(ramp.outputs["Color"], tint.inputs[1])
                    links.new(tint.outputs["Vector"],
                              bsdf.inputs["Base Color"])
                else:
                    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
                if tex_alpha is not None:
                    fade = self._node(mat.node_tree, "ShaderNodeMath",
                                      "Fade", (100, -60))
                    fade.operation = "MULTIPLY"
                    links.new(tex_alpha, fade.inputs[0])
                    links.new(ramp.outputs["Alpha"], fade.inputs[1])
                    links.new(fade.outputs["Value"], bsdf.inputs["Alpha"])
                else:
                    links.new(ramp.outputs["Alpha"], bsdf.inputs["Alpha"])
                try:
                    bsdf.inputs["Roughness"].default_value = 1.0
                    bsdf.inputs["Specular IOR Level"].default_value = 0.0
                except Exception:
                    pass
                links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
            try:
                mat.blend_method = "BLEND"
                mat.shadow_method = "NONE"
                mat.diffuse_color = (base[0], base[1], base[2], 1.0)
            except Exception:
                pass
            return mat
        except Exception as e:
            self.report({"WARNING"}, f"Could not build sprite material: {e}")
            return None

    def _atlas_uv(self, tree, links, norm, seq, life_sec, tex):
        """Drive the sprite texture node over the PTL atlas grid.

        Atlas cells run left-to-right, top-to-bottom; the Texture(12)
        keyframes give the cell index over the particle life. With a 1x1
        grid (or no keyframes) the full texture is used.
        """
        coord = self._node(tree, "ShaderNodeTexCoord", "UV", (-700, -260))
        cols = max(1, int(seq.texture_atlas_cols or 1))
        rows = max(1, int(seq.texture_atlas_rows or 1))
        scale = self._node(tree, "ShaderNodeCombineXYZ", "AtlasScale",
                           (-300, -260))
        scale.inputs["X"].default_value = 1.0 / cols
        scale.inputs["Y"].default_value = 1.0 / rows
        scale.inputs["Z"].default_value = 1.0
        cells = self._texture_cells(seq, life_sec, cols * rows)
        if cells is None or cols * rows <= 1:
            zoom = self._node(tree, "ShaderNodeVectorMath", "AtlasZoom",
                              (-100, -260))
            zoom.operation = "MULTIPLY"
            links.new(coord.outputs["UV"], zoom.inputs[0])
            links.new(scale.outputs["Vector"], zoom.inputs[1])
            links.new(zoom.outputs["Vector"], tex.inputs["Vector"])
            return
        t0, v0, t1, v1 = cells
        if t1 <= t0:
            off_const = self._offset_for_cell(tree, v0, cols, rows, (-300, -160))
            zoom = self._node(tree, "ShaderNodeVectorMath", "AtlasZoom",
                              (-100, -260))
            zoom.operation = "MULTIPLY"
            links.new(coord.outputs["UV"], zoom.inputs[0])
            links.new(scale.outputs["Vector"], zoom.inputs[1])
            pan = self._node(tree, "ShaderNodeVectorMath", "AtlasPan",
                             (100, -260))
            pan.operation = "ADD"
            links.new(zoom.outputs["Vector"], pan.inputs[0])
            links.new(off_const.outputs["Vector"], pan.inputs[1])
            links.new(pan.outputs["Vector"], tex.inputs["Vector"])
            return
        life = self._node(tree, "ShaderNodeValue", "Life", (-500, -160))
        life.outputs["Value"].default_value = life_sec
        age_s = self._node(tree, "ShaderNodeMath", "AgeS", (-300, -160))
        age_s.operation = "MULTIPLY"
        links.new(norm.outputs["Value"], age_s.inputs[0])
        links.new(life.outputs["Value"], age_s.inputs[1])
        to_cell = self._node(tree, "ShaderNodeMapRange", "AgeToCell",
                             (-100, -160))
        to_cell.clamp = True
        to_cell.inputs["From Min"].default_value = t0
        to_cell.inputs["From Max"].default_value = t1
        to_cell.inputs["To Min"].default_value = v0
        to_cell.inputs["To Max"].default_value = v1
        links.new(age_s.outputs["Value"], to_cell.inputs["Value"])
        cell = self._node(tree, "ShaderNodeMath", "Cell", (100, -160))
        cell.operation = "ROUND"
        links.new(to_cell.outputs["Result"], cell.inputs[0])
        off = self._cell_offset(tree, links, cell, cols, rows)
        zoom = self._node(tree, "ShaderNodeVectorMath", "AtlasZoom",
                          (300, -260))
        zoom.operation = "MULTIPLY"
        links.new(coord.outputs["UV"], zoom.inputs[0])
        links.new(scale.outputs["Vector"], zoom.inputs[1])
        pan = self._node(tree, "ShaderNodeVectorMath", "AtlasPan",
                         (500, -260))
        pan.operation = "ADD"
        links.new(zoom.outputs["Vector"], pan.inputs[0])
        links.new(off.outputs["Vector"], pan.inputs[1])
        links.new(pan.outputs["Vector"], tex.inputs["Vector"])

    def _offset_for_cell(self, tree, cell_value, cols, rows, loc):
        off = self._node(tree, "ShaderNodeCombineXYZ", "AtlasOff", loc)
        col = int(cell_value) % cols
        row = int(cell_value) // cols
        off.inputs["X"].default_value = col / cols
        off.inputs["Y"].default_value = 1.0 - (row + 1.0) / rows
        off.inputs["Z"].default_value = 0.0
        return off

    def _cell_offset(self, tree, links, cell_node, cols, rows):
        col = self._node(tree, "ShaderNodeMath", "Col", (300, -160))
        col.operation = "MODULO"
        links.new(cell_node.outputs["Value"], col.inputs[0])
        col.inputs[1].default_value = float(cols)
        row = self._node(tree, "ShaderNodeMath", "Row", (300, -60))
        row.operation = "DIVIDE"
        links.new(cell_node.outputs["Value"], row.inputs[0])
        row.inputs[1].default_value = float(cols)
        row_floor = self._node(tree, "ShaderNodeMath", "RowFloor", (500, -60))
        row_floor.operation = "FLOOR"
        links.new(row.outputs["Value"], row_floor.inputs[0])
        off_x = self._node(tree, "ShaderNodeMath", "OffX", (500, -160))
        off_x.operation = "DIVIDE"
        links.new(col.outputs["Value"], off_x.inputs[0])
        off_x.inputs[1].default_value = float(cols)
        row1 = self._node(tree, "ShaderNodeMath", "RowPlus1", (700, -60))
        row1.operation = "ADD"
        links.new(row_floor.outputs["Value"], row1.inputs[0])
        row1.inputs[1].default_value = 1.0
        row_n = self._node(tree, "ShaderNodeMath", "RowNorm", (900, -60))
        row_n.operation = "DIVIDE"
        links.new(row1.outputs["Value"], row_n.inputs[0])
        row_n.inputs[1].default_value = float(rows)
        off_y = self._node(tree, "ShaderNodeMath", "OffY", (1100, -60))
        off_y.operation = "SUBTRACT"
        off_y.inputs[0].default_value = 1.0
        links.new(row_n.outputs["Value"], off_y.inputs[1])
        off = self._node(tree, "ShaderNodeCombineXYZ", "AtlasOff",
                         (1300, -160))
        links.new(off_x.outputs["Value"], off.inputs["X"])
        links.new(off_y.outputs["Value"], off.inputs["Y"])
        return off

    # -- mesh slots ------------------------------------------------------
    def _import_mesh_slot(self, context, collection, root, eft,
                          index, entry, eft_path, fps):
        base = Path(entry.mesh_file).stem or f"mesh_{index:02d}"
        slot = bpy.data.objects.new(f"EFTMESH_{index:02d}_{base}", None)
        collection.objects.link(slot)
        slot.parent = root
        set_slot_transform(slot, entry.position.x, entry.position.y,
                           entry.position.z, entry.pitch, entry.yaw, entry.roll)
        _set_prop(slot, "eft_slot_type", "MESH")
        _set_prop(slot, "eft_slot_index", index)
        _set_prop(slot, "eft_mesh_file", entry.mesh_file)
        _set_prop(slot, "eft_mesh_anim_file", entry.mesh_animation_path())
        _set_prop(slot, "eft_mesh_texture", entry.texture_path())
        _set_prop(slot, "eft_alpha_enabled", bool(entry.alpha_enabled))
        _set_prop(slot, "eft_two_sided", bool(entry.two_sided))
        _set_prop(slot, "eft_alpha_test", bool(entry.alpha_test_enabled))
        _set_prop(slot, "eft_depth_test", bool(entry.depth_test_enabled))
        _set_prop(slot, "eft_depth_write", bool(entry.depth_write_enabled))
        _set_prop(slot, "eft_src_blend", int(entry.src_blend_factor))
        _set_prop(slot, "eft_dst_blend", int(entry.dst_blend_factor))
        _set_prop(slot, "eft_blend_op", int(entry.blend_op))
        _set_prop(slot, "eft_anim_file", entry.animation_path())
        _set_prop(slot, "eft_anim_repeat", int(entry.animation_repeat_count))
        _set_prop(slot, "eft_use_anim_raw", int(entry.use_animation))
        _set_prop(slot, "eft_start_delay_ms", int(entry.start_delay))
        _set_prop(slot, "eft_repeat_count", int(entry.repeat_count))
        _set_prop(slot, "eft_is_linked", bool(entry.is_linked))
        _set_prop(slot, "eft_pitch", float(entry.pitch))
        _set_prop(slot, "eft_yaw", float(entry.yaw))
        _set_prop(slot, "eft_roll", float(entry.roll))
        _set_prop(slot, "eft_pos", [float(entry.position.x),
                                   float(entry.position.y),
                                   float(entry.position.z)])
        _set_prop(slot, "eft_skip_a", entry.skip_a_text)
        _set_prop(slot, "eft_skip_b", entry.skip_b_text)

        frame_end = 1
        mesh_obj = None
        if self.load_meshes and entry.mesh_file:
            zms_path = resolve_vfs_path(eft_path, entry.mesh_file)
            _set_prop(slot, "eft_zms_resolved", str(zms_path) if zms_path else "")
            if zms_path is None:
                self.report({"WARNING"}, f"Mesh not found: {entry.mesh_file}")
            else:
                mesh_obj = self._load_effect_mesh(
                    context, collection, slot, zms_path, entry)
        if mesh_obj is None:
            self.report({"INFO"}, f"Mesh slot {index} kept as empty marker")

        morph = entry.mesh_animation_path()
        if morph:
            morph_path = resolve_vfs_path(eft_path, morph)
            _set_prop(slot, "eft_mesh_anim_resolved",
                      str(morph_path) if morph_path else "")
            if morph_path is None:
                self.report({"WARNING"}, f"Mesh anim not found: {morph}")
            else:
                self.report({"INFO"},
                            f"Mesh morph anim stored (not baked): {morph_path.name}")

        anim_path = resolve_vfs_path(eft_path, entry.animation_path())
        _set_prop(slot, "eft_anim_resolved", str(anim_path) if anim_path else "")
        if entry.animation_path() and anim_path is None:
            self.report({"WARNING"},
                        f"Transform ZMO not found: {entry.animation_path()}")
        if self.apply_animations and entry.animation_path() and anim_path is not None:
            traj = self._make_traj_child(collection, slot)
            _move_content_under(slot, traj)
            end = self._bake_transform_anim(
                traj, anim_path, entry.animation_repeat_count,
                entry.start_delay, fps)
            frame_end = max(frame_end, end)
        return frame_end

    def _load_effect_mesh(self, context, collection, slot, zms_path, entry):
        try:
            zms = ZMS(str(zms_path))
        except Exception as e:
            self.report({"WARNING"}, f"Failed to load ZMS {zms_path.name}: {e}")
            return None
        mesh = bpy.data.meshes.new(zms_path.stem)
        verts = [(v.position.x, v.position.y, v.position.z) for v in zms.vertices]
        faces = [(int(f.x), int(f.y), int(f.z)) for f in zms.indices]
        try:
            mesh.from_pydata(verts, [], faces)
        except Exception as e:
            self.report({"WARNING"}, f"Bad mesh data in {zms_path.name}: {e}")
            bpy.data.meshes.remove(mesh)
            return None
        if zms.normals_enabled():
            try:
                normals = [(v.normal.x, v.normal.y, v.normal.z) for v in zms.vertices]
                loop_normals = [normals[loop.vertex_index] for loop in mesh.loops]
                mesh.normals_split_custom_set(loop_normals)
            except Exception:
                pass
        for uv_name, enabled in (("uv1", zms.uv1_enabled()),
                                 ("uv2", zms.uv2_enabled()),
                                 ("uv3", zms.uv3_enabled()),
                                 ("uv4", zms.uv4_enabled())):
            if enabled:
                try:
                    mesh.uv_layers.new(name=uv_name)
                except Exception:
                    pass
        try:
            for loop_idx, loop in enumerate(mesh.loops):
                vi = loop.vertex_index
                if zms.uv1_enabled() and "uv1" in mesh.uv_layers:
                    mesh.uv_layers["uv1"].data[loop_idx].uv = (
                        zms.vertices[vi].uv1.x, 1.0 - zms.vertices[vi].uv1.y)
        except Exception:
            pass
        if zms.colors_enabled():
            try:
                color_attr = mesh.color_attributes.new(
                    name="Color", type="FLOAT_COLOR", domain="POINT")
                for vi, v in enumerate(zms.vertices):
                    if vi < len(color_attr.data):
                        color_attr.data[vi].color = (
                            v.color.r, v.color.g, v.color.b, v.color.a)
            except Exception:
                pass
        mesh.materials.append(self._make_mesh_material(zms_path.stem, entry))
        mesh.update(calc_edges=True)
        obj = bpy.data.objects.new(zms_path.stem, mesh)
        collection.objects.link(obj)
        obj.parent = slot
        _set_prop(obj, "eft_slot_type", "MESH_GEOMETRY")
        obj["zms_version"] = zms.version
        obj["zms_identifier"] = zms.identifier
        return obj

    def _make_mesh_material(self, name, entry):
        mat = bpy.data.materials.new(f"{name}_eft_mat")
        try:
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            principled = nodes.get("Principled BSDF")
            image = None
            if self.load_textures and entry.texture_path():
                tex_path = resolve_vfs_path(self.filepath, entry.texture_path())
                if tex_path is not None:
                    image = self._load_image(tex_path)
                if image is None:
                    # Same-name texture next to the mesh file.
                    mesh_path = resolve_vfs_path(self.filepath, entry.mesh_file)
                    if mesh_path is not None:
                        for ext in self.texture_extensions:
                            cand = mesh_path.with_suffix(ext)
                            if cand.is_file():
                                image = self._load_image(cand)
                                break
            if image is not None and principled is not None:
                tex_node = nodes.new(type="ShaderNodeTexImage")
                tex_node.image = image
                try:
                    links.new(tex_node.outputs["Color"],
                              principled.inputs["Base Color"])
                    if entry.alpha_enabled:
                        links.new(tex_node.outputs["Alpha"],
                                  principled.inputs["Alpha"])
                except Exception:
                    pass
            try:
                if entry.alpha_enabled:
                    mat.blend_method = "HASHED" if entry.alpha_test_enabled else "BLEND"
            except Exception:
                pass
        except Exception as e:
            self.report({"WARNING"}, f"Could not build mesh material: {e}")
        return mat

    # -- transform animation baking --------------------------------------
    def _bake_transform_anim(self, slot, zmo_path, repeat_count, start_delay_ms, fps):
        try:
            zmo = ZMO(str(zmo_path))
        except Exception as e:
            self.report({"WARNING"}, f"Failed to load ZMO {zmo_path.name}: {e}")
            return 1
        channels = zmo.get_bone_channels().get(0, {})
        pos_ch = None
        rot_ch = None
        for _key, ch in channels.items():
            cname = type(ch).__name__
            if cname == "ZmoPositionChannel":
                pos_ch = ch
            elif cname == "ZmoRotationChannel":
                rot_ch = ch
        if pos_ch is None and rot_ch is None:
            self.report({"WARNING"}, f"ZMO {zmo_path.name} has no bone-0 transform")
            return 1
        zmo_fps = zmo.fps if zmo.fps > 0 else fps
        start_frame = 1 + int(start_delay_ms / 1000.0 * fps)
        repeats = 1 if repeat_count == 0 else max(1, repeat_count)
        # Bake one pass; infinite-repeat (0) previews a single pass.
        action = bpy.data.actions.new(name=f"{slot.name}_anim")
        action.use_fake_user = True
        slot.rotation_mode = "QUATERNION"
        data_path_loc = "location"
        data_path_rot = "rotation_quaternion"
        try:
            fcurves = action.fcurves
        except Exception:
            fcurves = None
        loc_curves = []
        rot_curves = []
        if fcurves is not None:
            try:
                for i in range(3):
                    loc_curves.append(fcurves.new(data_path_loc, index=i))
                for i in range(4):
                    rot_curves.append(fcurves.new(data_path_rot, index=i))
            except Exception as e:
                self.report({"WARNING"}, f"Could not create F-Curves: {e}")
                return 1
        else:
            return 1
        base_loc = Vector(slot.location)
        base_rot = Quaternion(slot.rotation_quaternion)
        for frame_idx in range(zmo.num_frames):
            frame = start_frame + int(frame_idx * fps / zmo_fps)
            if pos_ch is not None and frame_idx < len(pos_ch.values):
                v = pos_ch.values[frame_idx]
                loc = Vector((v.x / 100.0, -v.y / 100.0, v.z / 100.0))
            else:
                loc = base_loc
            if rot_ch is not None and frame_idx < len(rot_ch.values):
                q = rot_ch.values[frame_idx]
                rot = Quaternion((q.w, q.x, -q.y, q.z))
            else:
                rot = base_rot
            for axis, curve in enumerate(loc_curves):
                key = curve.keyframe_points.insert(frame, loc[axis])
                key.interpolation = "LINEAR"
            for axis, curve in enumerate(rot_curves):
                key = curve.keyframe_points.insert(frame, rot[axis])
                key.interpolation = "LINEAR"
        if not slot.animation_data:
            slot.animation_data_create()
        slot.animation_data.action = action
        end_frame = start_frame + int((zmo.num_frames - 1) * fps / zmo_fps)
        self.report({"INFO"},
                    f"Baked {zmo.num_frames} frames from {zmo_path.name} "
                    f"(repeat={repeat_count or 'infinite→1 preview'})")
        return max(1, end_frame)


def menu_func_import(self, context):
    self.layout.operator(ImportEFT.bl_idname, text="ROSE Effect (.eft)")


def register():
    bpy.utils.register_class(ImportEFT)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.utils.unregister_class(ImportEFT)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
    register()
