"""Headless Blender test for EFT import + export.

Run with the Blender executable (requires bpy):
  blender --background --factory-startup --python tests/test_blender_eft.py

- Imports TEST.EFT (1 particle slot + 1 mesh slot).
- Checks the EFT_ root, slot empties, mesh geometry, particle emitter
  systems, PTL JSON text block and baked actions.
- Exports untouched -> must be byte-identical to the source file.
- Moves a slot, exports again -> the moved position must round-trip
  through the parser.

Exit code 0 on success, 1 on failure.
"""
import bpy
import math
import mathutils
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(ADDON_ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths  # noqa: E402
import io_rose  # noqa: E402

io_rose.register()

EFFECT_DIR = os.path.join(_paths.client_3ddata_root(), "EFFECT")
SRC_EFT = os.path.join(EFFECT_DIR, "TEST.EFT")
# 1 particle + 2 meshes, all referenced assets exist on disk.
MESH_EFT = os.path.join(EFFECT_DIR, "CHOUTAN_01.EFT")
# Particle slots with MOTION transform animations (TRAJ baking).
ANIM_EFT = os.path.join(EFFECT_DIR, "FLAT_POINT_01.EFT")
# 20 debris particles with -700 cm/s^2 gravity (gravity mapping check).
GRAV_EFT = os.path.join(EFFECT_DIR, "_HIT_HAWK_01.EFT")
# Radial debris spray (velocity spread wider than aim, ~0.73 m/s).
JET_EFT = os.path.join(EFFECT_DIR, "BANDY_BOW_01.EFT")
TMP_DIR = os.path.join(os.environ.get("TEMP", "/tmp"), "io_rose_eft_test")


def check(condition, message):
    if not condition:
        print(f"FAIL: {message}")
        return False
    print(f"ok: {message}")
    return True


def live_particles(obj):
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    eo = deps.objects.get(obj.name)
    out = []
    if eo is not None:
        for ps in eo.particle_systems:
            out.extend(p for p in ps.particles if p.alive_state == "ALIVE")
    return out


def main():
    ok = True
    if not os.path.isfile(SRC_EFT):
        print(f"effect file not found: {SRC_EFT}")
        return 1
    os.makedirs(TMP_DIR, exist_ok=True)

    res = bpy.ops.rose.import_eft(
        filepath=SRC_EFT,
        load_meshes=True,
        load_textures=True,
        load_particles=True,
        create_particle_systems=True,
        apply_animations=False,
    )
    ok &= check(res == {"FINISHED"}, f"import finished ({res})")

    root = bpy.data.objects.get("EFT_TEST")
    ok &= check(root is not None, "EFT_TEST root exists")
    if root is None:
        return 1
    ok &= check(root.get("eft_is_effect_root"), "root tagged as effect root")

    particles = [c for c in root.children if c.get("eft_slot_type") == "PARTICLE"]
    meshes = [c for c in root.children if c.get("eft_slot_type") == "MESH"]
    ok &= check(len(particles) == 1, f"1 particle slot ({len(particles)})")
    ok &= check(len(meshes) == 1, f"1 mesh slot ({len(meshes)})")

    pslot = particles[0]
    ok &= check("firebullet" in (pslot.get("eft_particle_file") or "").lower(),
                f"particle file prop ({pslot.get('eft_particle_file')})")
    ok &= check(pslot.get("eft_ptl_json") in bpy.data.texts,
                "PTL JSON text block stored")

    emitters = [c for c in pslot.children
                if c.get("eft_slot_type") == "PARTICLE_EMITTER"]
    ok &= check(len(emitters) == 2, f"2 sequence emitters ({len(emitters)})")
    quads = [c for c in pslot.children
             if c.get("eft_slot_type") == "EFT_QUAD"]
    pooled = []
    for em in emitters:
        pooled.extend(list(em.get("eft_quad_pool") or []))
    ok &= check(len(quads) == len(pooled) and len(quads) >= 2
                and {q.name for q in quads} == set(pooled),
                f"quad pool covers emitters ({len(quads)} quads)")
    sizes = []
    for em in emitters:
        ok &= check(em.display_type == "WIRE", f"{em.name} wire display")
        ok &= check(em.hide_render, f"{em.name} hidden in renders")
        systems = list(em.particle_systems)
        ok &= check(len(systems) == 1, f"{em.name} has 1 system")
        if not systems:
            continue
        st = systems[0].settings
        ok &= check(st.render_type == "NONE",
                    f"{em.name} invisible motion source")
        ok &= check(st.instance_object is None,
                    f"{em.name} instances nothing (quads are real)")
        ok &= check(st.use_rotations is False,
                    f"{em.name} no particle rotation")
        ok &= check(abs(st.effector_weights.gravity) < 1e-6,
                    f"{em.name} zero gravity weight (fire has no gravity)")
        ok &= check(abs(st.normal_factor - em["eft_preview_speed"]) < 1e-6,
                    f"{em.name} velocity matches preview prop")
        pool = list(em.get("eft_quad_pool") or [])
        ok &= check(len(pool) == st.count and len(pool) >= 1,
                    f"{em.name} quad pool matches count ({len(pool)})")
        ok &= check(all(q in bpy.data.objects for q in pool),
                    f"{em.name} pool quads exist")
        sizes.append(st.particle_size)
    ok &= check(any(abs(v - 0.47) < 0.03 for v in sizes)
                and any(abs(v - 1.7) < 0.03 for v in sizes),
                f"quad sizes 0.47/1.7 m "
                f"({sorted(round(v, 2) for v in sizes)})")
    for q in quads:
        mats = [m for m in q.data.materials if m and m.use_nodes]
        ok &= check(len(mats) == 1, f"{q.name} has quad material")
        if mats:
            nodes = mats[0].node_tree.nodes
            types = {n.bl_idname for n in nodes}
            ok &= check("ShaderNodeTexImage" in types,
                        f"{q.name} textured material")
            ok &= check("AgeNorm" in nodes,
                        f"{q.name} AgeNorm-driven material")
            ramp = next((n for n in nodes
                         if n.bl_idname == "ShaderNodeValToRGB"), None)
            if ramp is not None:
                fac_linked = any(
                    l.to_node == ramp and l.to_socket.name == "Fac"
                    for l in mats[0].node_tree.links)
                ok &= check(fac_linked,
                            f"{q.name} life color ramp age-driven")
        ok &= check(q.hide_viewport and q.hide_render,
                    f"{q.name} starts hidden until first sync")
        ok &= check(len(q.constraints) == 0,
                    f"{q.name} constraint-free (aim is baked per sync)")

    # Static particles (v=0, g=0) must hang still, not fall.
    statics = [em for em in emitters
               if abs(em.get("eft_preview_speed", 1.0)) < 1e-9]
    ok &= check(len(statics) == 1, "one static emitter in TEST.EFT")
    if statics:
        bpy.context.scene.frame_set(1)
        pos1 = sorted(tuple(p.location) for p in live_particles(statics[0]))
        bpy.context.scene.frame_set(9)
        pos9 = sorted(tuple(p.location) for p in live_particles(statics[0]))
        still = (len(pos1) > 0 and len(pos1) == len(pos9) and max(
            abs(a - b) for pa, pb in zip(pos1, pos9)
            for a, b in zip(pa, pb)) < 1e-4)
        ok &= check(still, "zero-gravity particles hang still (no Earth fall)")

    # Debris with authored -700 cm/s^2 gravity accelerates at ~-7 m/s^2.
    res = bpy.ops.rose.import_eft(
        filepath=GRAV_EFT,
        load_meshes=False,
        load_textures=False,
        load_particles=True,
        create_particle_systems=True,
        apply_animations=False,
    )
    ok &= check(res == {"FINISHED"}, "gravity effect import finished")
    hem = None
    hroot = bpy.data.objects.get("EFT__HIT_HAWK_01")
    if hroot is not None:
        for child in hroot.children:
            for em in child.children:
                if em.get("eft_slot_type") == "PARTICLE_EMITTER" \
                        and abs(em.get("eft_preview_gravity_weight", 0.0)) > 0.5:
                    hem = em
    ok &= check(hem is not None, "found gravity-weighted emitter")
    if hem is not None:
        w = hem["eft_preview_gravity_weight"]
        ok &= check(abs(w - 700.0 / 981.0) < 0.03,
                    f"gravity weight ~= +0.71 ({w:.3f})")
        bpy.context.scene.frame_set(5)
        fell5 = [p.velocity[2] for p in live_particles(hem)]
        bpy.context.scene.frame_set(13)
        fell13 = [p.velocity[2] for p in live_particles(hem)]
        fps = bpy.context.scene.render.fps or 24
        if fell5 and fell13:
            acc = (sum(fell13) / len(fell13) - sum(fell5) / len(fell5)) \
                / ((13 - 5) / fps)
            ok &= check(abs(acc - (-7.0)) < 1.5,
                        f"measured accel ~= -7 m/s^2 ({acc:.2f})")
        else:
            ok &= check(False, "gravity particles alive for measurement")

    # Radial spray (avg vel ~0, spread 73 cm/s): particles leave the
    # emitter at ~0.73 m/s in all directions.
    res = bpy.ops.rose.import_eft(
        filepath=JET_EFT,
        load_meshes=False,
        load_textures=False,
        load_particles=True,
        create_particle_systems=True,
        apply_animations=False,
    )
    ok &= check(res == {"FINISHED"}, "spray effect import finished")
    jem = None
    jroot = bpy.data.objects.get("EFT_BANDY_BOW_01")
    if jroot is not None:
        for child in jroot.children:
            for em in child.children:
                if em.get("eft_slot_type") == "PARTICLE_EMITTER" \
                        and em.get("eft_preview_kind") == "radial":
                    jem = em
    ok &= check(jem is not None, "found radial spray emitter")
    if jem is not None:
        ok &= check(abs(jem["eft_preview_speed"] - 0.73) < 0.05,
                    f"spray speed ~= 0.73 m/s ({jem['eft_preview_speed']:.2f})")
        bpy.context.scene.frame_set(2)
        jets = live_particles(jem)
        ok &= check(len(jets) > 0, "spray particles alive")
        if jets:
            import math as _math
            speeds = [_math.sqrt(v.velocity[0] ** 2 + v.velocity[1] ** 2
                                 + v.velocity[2] ** 2) for v in jets]
            mean_speed = sum(speeds) / len(speeds)
            ok &= check(abs(mean_speed - 0.73) < 0.2,
                        f"spray speed measured ~= 0.73 ({mean_speed:.2f})")

    mslot = meshes[0]
    ok &= check("HOKE_01.ZMS" in (mslot.get("eft_mesh_file") or "").upper(),
                f"mesh file prop ({mslot.get('eft_mesh_file')})")
    # TEST.EFT's mesh is a dangling reference (no _HOKE_01.ZMS on disk);
    # the slot must survive as an empty marker, like the game client.
    geo = [c for c in mslot.children if c.get("eft_slot_type") == "MESH_GEOMETRY"]
    ok &= check(len(geo) == 0, "dangling mesh kept as empty marker")

    # A fully-resolvable effect loads real mesh geometry.
    res = bpy.ops.rose.import_eft(
        filepath=MESH_EFT,
        load_meshes=True,
        load_textures=False,
        load_particles=True,
        create_particle_systems=False,
        apply_animations=False,
    )
    ok &= check(res == {"FINISHED"}, "mesh effect import finished")
    mroot = bpy.data.objects.get("EFT_CHOUTAN_01")
    ok &= check(mroot is not None, "EFT_CHOUTAN_01 root exists")
    geos = []
    if mroot is not None:
        for child in mroot.children:
            if child.get("eft_slot_type") == "MESH":
                geos.extend(c for c in child.children
                            if c.get("eft_slot_type") == "MESH_GEOMETRY")
    ok &= check(len(geos) == 2, f"2 mesh geometries loaded ({len(geos)})")
    ok &= check(all(len(g.data.polygons) > 0 for g in geos),
                "loaded meshes have faces")

    # Transform animations bake onto TRAJ children, never onto the slot.
    res = bpy.ops.rose.import_eft(
        filepath=ANIM_EFT,
        load_meshes=False,
        load_textures=False,
        load_particles=False,
        create_particle_systems=False,
        apply_animations=True,
    )
    ok &= check(res == {"FINISHED"}, "anim effect import finished")
    aroot = bpy.data.objects.get("EFT_FLAT_POINT_01")
    traj_ok = False
    action_ok = False
    if aroot is not None:
        for child in aroot.children:
            for grand in child.children:
                if grand.get("eft_slot_type") == "TRAJ":
                    traj_ok = True
                    if grand.animation_data and grand.animation_data.action:
                        action_ok = True
            if child.animation_data and child.animation_data.action:
                ok = False
                print("FAIL: baked action landed on the slot itself")
    ok &= check(traj_ok, "TRAJ child created for transform anim")
    ok &= check(action_ok, "TRAJ child carries the baked action")

    # Untouched export must be byte-identical.
    out_plain = os.path.join(TMP_DIR, "TEST_plain.EFT")
    res = bpy.ops.rose.export_eft(filepath=out_plain, effect_root="EFT_TEST")
    ok &= check(res == {"FINISHED"}, f"export finished ({res})")
    with open(SRC_EFT, "rb") as f:
        original = f.read()
    with open(out_plain, "rb") as f:
        saved = f.read()
    ok &= check(saved == original,
                f"untouched export byte-identical ({len(saved)} bytes)")

    # Move the mesh slot +2 m on X (= +200 cm Rose) and re-export.
    mslot.location.x += 2.0
    bpy.context.view_layer.update()
    out_moved = os.path.join(TMP_DIR, "TEST_moved.EFT")
    res = bpy.ops.rose.export_eft(filepath=out_moved, effect_root="EFT_TEST")
    ok &= check(res == {"FINISHED"}, "moved export finished")

    sys.path.insert(0, ADDON_ROOT)
    from rose.eft import Eft
    moved = Eft(out_moved)
    ok &= check(len(moved.meshes) == 1 and len(moved.particles) == 1,
                "moved file keeps slot counts")
    if moved.meshes:
        dx = moved.meshes[0].position.x
        ok &= check(abs(dx - 200.0) < 0.5,
                    f"moved mesh x = {dx:.2f} cm (expected ~200)")

    # Additive fire quads: Transparent+Emission mix driven by texture
    # luminance (their DDS textures carry no alpha).
    res = bpy.ops.rose.import_eft(
        filepath=os.path.join(EFFECT_DIR, "_FIRE_01.EFT"),
        load_meshes=False,
        load_textures=True,
        load_particles=True,
        create_particle_systems=True,
        apply_animations=False,
    )
    ok &= check(res == {"FINISHED"}, "additive fire import finished")
    froot = bpy.data.objects.get("EFT__FIRE_01")
    fquads = []
    femitters = []
    if froot is not None:
        for child in froot.children:
            fquads.extend(c for c in child.children
                          if c.get("eft_slot_type") == "EFT_QUAD")
            femitters.extend(c for c in child.children
                             if c.get("eft_slot_type") == "PARTICLE_EMITTER")
    ok &= check(len(fquads) == 2, f"2 fire quads ({len(fquads)})")
    for q in fquads:
        mats = [m for m in q.data.materials if m and m.use_nodes]
        if mats:
            types = {n.bl_idname for n in mats[0].node_tree.nodes}
            ok &= check("ShaderNodeMixShader" in types
                        and "ShaderNodeBsdfTransparent" in types
                        and "ShaderNodeRGBToBW" in types,
                        f"{q.name} additive luminance-alpha material")

    # Quad sync machinery: frame_change_post + render_pre hooks
    # registered, and a direct sync places quads on live particles.
    import io_rose.import_eft as _eft_mod
    ok &= check(any(getattr(h, "__name__", "") == "_eft_quad_sync_frame"
                    for h in bpy.app.handlers.frame_change_post),
                "quad frame sync registered")
    ok &= check(any(getattr(h, "__name__", "") == "_eft_quad_sync_render"
                    for h in bpy.app.handlers.render_pre),
                "quad render sync registered")
    cam = bpy.context.scene.camera
    if cam is None:
        cdata = bpy.data.cameras.new("EFT_TestCam")
        cam = bpy.data.objects.new("EFT_TestCam", cdata)
        bpy.context.scene.collection.objects.link(cam)
        bpy.context.scene.camera = cam
    cam.location = (0.0, -20.0, 4.0)
    cam.rotation_euler = (mathutils.Vector((0.0, 0.0, 1.0)) - cam.location) \
        .to_track_quat("-Z", "Y").to_euler()
    # Direct sync (what the frame/render hooks call). Note: this must
    # also work with a frame change between the camera move and the sync
    # (legacy evaluated-object writes do not stick, so the sync composes
    # fresh world matrices from live properties instead).
    bpy.context.scene.frame_set(30)
    bpy.context.view_layer.update()
    placed = _eft_mod._sync_particle_quads(bpy.context.scene)
    bpy.context.view_layer.update()
    ok &= check(placed >= 2, f"sync placed {placed} quads")
    if fquads:
        q = fquads[0]
        qz = q.matrix_world.to_3x3().col[2].normalized()
        cz = cam.matrix_world.to_3x3().col[2].normalized()
        angle = qz.angle(cz)
        ok &= check(angle < 0.05,
                    f"quad faces camera ({math.degrees(angle):.2f} deg)")
        ok &= check(not q.hide_render and not q.hide_viewport,
                    f"{q.name} visible after sync")
        # AgeNorm must carry the real particle age (frame 30 of an
        # 84-frame life ~= 0.35); legacy Particle has no .age in 4.x so
        # the sync derives it from birth_time (regression guard).
        age_node = None
        if q.active_material is not None:
            age_node = q.active_material.node_tree.nodes.get("AgeNorm")
        age_val = age_node.outputs[0].default_value if age_node else -1.0
        ok &= check(0.2 < age_val < 0.5,
                    f"quad age tracks particle ({age_val:.2f} at frame 30)")

    # Render check: isolate the fire root (other imported effects sit at
    # the origin too and would photobomb the frame), then the additive
    # glow must read as a camera-facing puff, not streaks or black quads.
    # (Drop the factory-startup cube first so it cannot occlude anything.)
    cube = bpy.data.objects.get("Cube")
    if cube is not None and cube.type == "MESH" and not cube.particle_systems:
        bpy.data.objects.remove(cube, do_unlink=True)
    hidden = []
    frozen = []
    for o in list(bpy.data.objects):
        if o.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT"):
            continue
        root = o
        seen = 0
        while root.parent is not None and seen < 16:
            root = root.parent
            seen += 1
        if root is not froot and o is not cam:
            # Freeze other roots first: the render_pre quad sync would
            # otherwise unhide their live quads for the render.
            if root.get("eft_is_effect_root"):
                try:
                    if root.get("eft_live_billboard", True):
                        root["eft_live_billboard"] = False
                        frozen.append(root)
                except Exception:
                    pass
            if not o.hide_render:
                o.hide_render = True
                hidden.append(o)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 256
    scene.render.resolution_y = 144
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.frame_set(30)
    render_png = os.path.join(TMP_DIR, "TEST_fire_render.png")
    scene.render.filepath = render_png
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    for o in hidden:
        try:
            o.hide_render = False
        except Exception:
            pass
    for root in frozen:
        try:
            root["eft_live_billboard"] = True
        except Exception:
            pass
    probe = bpy.data.images.load(render_png)
    px = list(probe.pixels[:])
    bpy.data.images.remove(probe)
    n = len(px) // 4
    glow = sum(1 for i in range(n) if px[4 * i] > 0.25) / n
    black = sum(1 for i in range(n)
                if max(px[4 * i], px[4 * i + 1], px[4 * i + 2]) < 0.02) / n
    ok &= check(glow > 0.005, f"fire glow visible ({glow * 100:.1f}% warm px)")
    ok &= check(black < 0.35, f"no opaque black quads ({black * 100:.1f}% black)")

    # Yaw the particle slot 30 degrees about up and re-export.
    pslot.rotation_mode = "QUATERNION"
    pslot.rotation_quaternion = mathutils.Quaternion((0.0, 0.0, 1.0),
                                                     math.radians(30.0))
    bpy.context.view_layer.update()
    out_rot = os.path.join(TMP_DIR, "TEST_rot.EFT")
    res = bpy.ops.rose.export_eft(filepath=out_rot, effect_root="EFT_TEST")
    ok &= check(res == {"FINISHED"}, "rotated export finished")
    rotated = Eft(out_rot)
    if rotated.particles:
        rp = rotated.particles[0]
        ok &= check(abs(rp.yaw - 30.0) < 0.5,
                    f"rotated particle yaw = {rp.yaw:.2f} (expected ~30)")
        ok &= check(abs(rp.pitch) < 0.5 and abs(rp.roll) < 0.5,
                    f"pitch/roll stay ~0 ({rp.pitch:.2f}/{rp.roll:.2f})")

    print("BLENDER EFT TEST " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
