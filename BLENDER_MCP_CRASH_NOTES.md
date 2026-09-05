# Blender MCP Crash Notes (not io_rose docs - different addon)

> This file documents the Blender MCP remote-control addon (`addon.py`, command-queue over socket), not `io_rose`. Kept here only for the Woopie-fur task history and the Incident 5 triage rule below. Do not read as `io_rose` architecture.

Lessons from crashes/freezes during the Woopie Chef fur task (Blender 4.5.13 LTS, commit daeeeca98fb0, 2026-08-24). Add-on: Blender MCP (addon.py, command-queue over socket).

## Incident 1 — Access violation crash (fatal)

Crash log summary:

- Session recorded interactive operators immediately before the crash: `editmode_toggle`, repeated `shading.type` switches (`SOLID -> RENDERED -> MATERIAL`), and an interactive `transform.translate` (user was editing/moving objects).
- Python backtrace at crash: our MCP `execute_code` script, `box_from` list-comp (`[p.x for p in pts]` over `poly.center` of an **evaluated** mesh), inside `execute_code`.
- Native fault: `EXCEPTION_ACCESS_VIOLATION`, null-ish address (`params 0x0 / 0x5C`) — classic use-after-free / freed-C-data access.

Root cause (most probable combination):

1. The script iterated `polygons` of a mesh obtained via `evaluated_get(...).to_mesh()` **while the user was simultaneously transforming objects**. Any user transform forces depsgraph re-evaluation; the evaluated mesh and its wrapped C data can be freed underneath the Python loop.
2. The scene contains hair-curve objects whose `.surface` references the skinned meshes; EEVEE re-evaluates hair attachment every shading switch. Rapid `SOLID -> RENDERED -> MATERIAL` switching while a script was mutating the same objects added pressure on the same evaluated data path.

Rules to prevent repeats:

- **Never iterate evaluated-geometry datablocks (`to_mesh()`) in long or interleaved passes.** Copy what is needed into plain Python lists (tuples of floats) in one short pass, then call `to_mesh_clear()` immediately, and do all remaining math on the copies.
- **Keep every `execute_blender_code` chunk under ~5–10 s of main-thread work.** Bounded loops, small attempt caps, no multi-part heavy loops in one call.
- **One lifetime per call**: get evaluated mesh -> use -> `to_mesh_clear()` in the same small chunk. Never hold references to evaluated mesh data (or `poly.center` Vectors from it) across long computations.
- **Tell the user to not interact (no transforms, no shading switches, no edit mode) while a heavy MCP job runs.** Interactive ops and scripted depsgraph work in the same window are the direct crash condition.
- When building/inspecting hair curves: prefer `SOLID` shading for validation passes; avoid rapid repeated shading-type toggles in scripts.
- Rebuilding fur objects: delete-then-create in a chunk that also **saves nothing but touches only MCP-owned objects**; keep armature-skinned originals as read-only inputs.

## Incident 2 — main-thread freeze before the crash

Symptom: after a timed-out heavy call, even `print("alive")` timed out for minutes.

Why:

- The MCP server queues commands and executes them on Blender's main thread. A heavy queued script keeps running after the MCP client times out; **each timeout-probe we sent was queued and re-executed later**, multiplying the workload behind the queue.
- A script whose rejection rate is high runs until `count * 40` attempts — that bound can be minutes of pure Python when combined with per-attempt KD-tree queries and 9-tap texture sampling.

Rules:

- On timeout: **wait 60–120 s, then send exactly one tiny probe** (`print("alive")`). Do NOT resend or probe repeatedly — queued duplicates compound.
- Design loops to exit early: cap attempts at `count * 10`, and bail with a partial result + diagnostic instead of grinding to the cap.
- Pre-filter cheaply: classify each triangle ONCE (fur/costume) before sampling, instead of calling corner-sampling + dilation + KD-tree on every rejected attempt.

## Incident 3 — KDTree NameError in exec'd builder (non-fatal, but caused a stray timeout)

- `exec(code, ns)`-defined builder functions only see names present in `ns`. `from mathutils.kdtree import KDTree` in the outer call does not propagate.
- Rule: after exec'ing a builder, explicitly inject every external name it uses: `ns["KDTree"] = KDTree`, or import inside the code string.

## Incident 4 — this build lacks most GN hair-curve nodes

- `GeometryNodeGenerateHairCurves`, `Interpolate`, `Clump`, `Trim`, `Duplicate`, `SetHairCurveProfile`, `FunctionNodeHairCurvesNoise` are **not registered**; only `GeometryNodeDeformCurvesOnSurface` exists and it is a **no-op** for our curves (verified: 0 movement of evaluated positions).
- Rules:
  - Never assume hair nodes exist — probe by attempting `nodes.new()` on a throwaway group first.
  - Don't rely on `Deform Curves on Surface` for following armature deformation in this build; bake roots onto the evaluated posed surface and rebuild after re-posing.
  - Vertex groups on Hair Curves objects: `obj.vertex_groups.new()` raises "not supported for 'Hair Curves' objects"; Armature modifier on curves silently fails to add. Don't pursue curve skinning.

## General operating rules (distilled)

1. Copy evaluated data to plain Python immediately; free the evaluated mesh at once.
2. Small chunks, early exits, per-triangle pre-classification instead of per-sample heavy tests.
3. One probe after timeouts; never spam; never resend the same heavy script.
4. User: hands off viewport (no transforms/edit/shading toggles) while a job runs.
5. exec'd functions must receive their dependencies explicitly in the namespace.
6. Query node/feature availability instead of assuming (this build has trimmed GN hair nodes).
7. Autosave exists (`39620_autosave.blend`) — but do not rely on it; ask the user to save before heavy passes.

## Incident 5 — NVIDIA driver crash in viewport present (2026-09-03 ~19:03)

Crash log (`%TEMP%/blender.crash.txt`, Blender 4.5.13, commit daeeeca98fb0):

- Last operators before the crash: `bpy.ops.object.delete()` ("Deleted 1 object(s)"),
  then file-browser navigation (`space_data.recent_folders_active = 1 -> 2`).
- 19:02 autosave (`19820_autosave.blend`, 93 KB) is a near-empty scene — the crash
  happened on a ~empty viewport, not mid-import.
- Native fault: `EXCEPTION_ACCESS_VIOLATION`, params `0x0 / 0x0` (null deref),
  faulting module `d3d11.dll`, called from `nvoglv64.dll DrvPresentBuffers`
  via `GHOST_ContextWGL::swapBuffers <- wm_draw_update <- WM_main`.
- No Python traceback, no io_rose/addon operator anywhere in the recent history.
- Environment: NVIDIA GeForce RTX 5080, driver 32.0.16.1047; a virtual-display
  driver + virtual desktop monitor are also installed (remote-desktop session).

Root cause assessment:

- This is a GPU-driver crash in the viewport present path, not an addon bug.
  Python (including io_rose) cannot produce an access violation inside
  `nvoglv64 DrvPresentBuffers` — script errors raise tracebacks instead.
- Proximate trigger was a viewport redraw right after an object delete, with the
  file browser open. Possible aggravators: driver state, GPU resource churn from
  earlier heavy validation (dense enhanced meshes, full-zone imports), and/or the
  virtual display in the present chain. None of these implicate scene content —
  the scene was nearly empty.

Rules to prevent repeats:

1. **Save before delete-heavy / import-heavy validation passes.** (Autosave covered
   this one, but do not rely on it — see rule 7 of the general list.)
2. **Run batch imports/deletes headless** (`blender --background --python ...`):
   no viewport means no swap chain, making this entire crash class impossible.
3. **Keep the viewport in Solid shading while churning scenes** (mass import,
   mass delete, armature validation); avoid EEVEE-rendered viewports during those.
4. **Record the driver version with every GPU crash** (was 32.0.16.1047 here) and
   keep the NVIDIA driver current; if this recurs, DDU-clean-reinstall or try the
   Studio branch before blaming scene content.
5. **If it reproduces, capture the exact repro** (file + shading mode + op
   sequence) — driver bugs can only be worked around with a repro. Note whether
   the session was over remote desktop, given the virtual display in the chain.
6. Triage shortcut for future crashes: fault inside `nvoglv64/nvwgf2umx/d3d11`
   from `swapBuffers` with no Python frames = driver/present issue, not addon code.
   Fault with io_rose frames or `to_mesh` on the stack = addon-side, handle per
   Incidents 1-2.

## Post-crash recovery checklist

1. Reopen the saved/autosaved `.blend`.
2. Probe: MCP alive? (`print("alive")`), scene object list, `MCP_WoopieFur` collection contents.
3. `driver_namespace` builders are lost after restart — redefine helpers before rebuilding fur.
4. Rebuild fur parts one part per call, at test count first, then scale up.
