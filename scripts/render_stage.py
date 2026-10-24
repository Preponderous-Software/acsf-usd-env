"""Render an animated 3D view of an exported stage in its synthetic schematic layout.

The bundled golden runs are exported with the project's own CLI, and the stage's time samples
are then read inside headless Blender (with the ``pxr`` module Blender bundles) to build and
animate the scene. Nothing is invented: every node, ring and pulse is one time sample.

What is drawn, per instance (the instance's origin and the ``Stimulus`` sphere come from the
stage; every other placement is a drawing choice made here, like the exporter's own layout):

* ``Stream.acsf:thought`` — one node per cycle with a thought, laid along an Archimedean
  spiral around the instance origin in cycle order; node size follows the thought's length;
* ``Stimulus.visibility`` / ``acsf:perception`` — the stage's sphere lights up on cycles with a
  perception, and a pulse travels from it to that cycle's node;
* ``Stream.acsf:reflection`` — an amber ring around the node of each cycle with a reflection;
* ``Mood.acsf:mood:<dim>`` — final value only in these runs (``acsf:moodSampling = final``),
  so it is shown only after the last cycle, as a faint tint under the spiral.

The spiral, sizes, colours and camera encode nothing ACSF knows; the stage records
``acsf:layout:synthetic = true``. The result is a picture of journaled events, not of a mind,
a body or a place.

Usage (Pillow and imageio-ffmpeg are not project dependencies)::

    uv run --python 3.12 --no-project --with . --with pillow --with imageio-ffmpeg \\
        python scripts/render_stage.py --blender /path/to/blender --out out/ Echo Wren

Writes ``stage.mp4``, ``stage.gif``, ``stage-poster.png`` and ``meta.json`` to ``--out``.
Inside Blender the same file runs as ``blender -b --python scripts/render_stage.py -- ...``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "fixtures" / "golden"
# Blender's bundled Python does not have acsf-usd-env installed: import the shared encoding
# (the spiral, node sizes, colours and caption that `acsf-usd view` also uses) from the checkout.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from acsf_usd.schematic import (  # noqa: E402
    CAPTION,
    MOOD_RGB,
    PERCEPTION_RGB,
    REFLECTION_RGB,
    SPIRAL_R_MAX,
    THOUGHT_RGB,
    THREAD_RGB,
    node_radius,
    spiral_positions,
)


def frame_of(cycle: float, c0: float, c1: float, pad: int, play: int) -> float:
    """Frame at which ``cycle`` is shown: cycles c0..c1 span ``play`` frames after ``pad``."""
    if c1 <= c0:
        return float(pad)
    return pad + (cycle - c0) * play / (c1 - c0)


# --------------------------------------------------------------------------------------------
# Inside Blender
# --------------------------------------------------------------------------------------------


def read_stage(path: str) -> dict[str, Any]:
    """Read every channel the render draws from the stage's time samples."""
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(path)
    layer_data = dict(stage.GetRootLayer().customLayerData)
    xcache = UsdGeom.XformCache(Usd.TimeCode.Default())
    instances = []
    parent = stage.GetPrimAtPath("/World/Instances")
    children = parent.GetChildren() if parent else []
    children = sorted(children, key=lambda p: xcache.GetLocalToWorldTransform(p)[3][0])
    for inst in children:
        origin = xcache.GetLocalToWorldTransform(inst).ExtractTranslation()

        def samples(prim_rel: str, attr: str, _inst: Any = inst) -> dict[float, Any]:
            prim = _inst.GetPrimAtPath(prim_rel)
            if not prim or not prim.IsValid() or not prim.HasAttribute(attr):
                return {}
            a = prim.GetAttribute(attr)
            return {float(t): a.Get(t) for t in a.GetTimeSamples()}

        thoughts = {t: v for t, v in samples("Stream", "acsf:thought").items() if v}
        reflections = sorted(t for t, v in samples("Stream", "acsf:reflection").items() if v)
        perceptions = sorted(t for t, v in samples("Stimulus", "acsf:perception").items() if v)
        visible = sorted(
            t for t, v in samples("Stimulus", "visibility").items() if v != UsdGeom.Tokens.invisible
        )
        sphere = inst.GetPrimAtPath("Stimulus/Sphere")
        if not sphere:
            sphere = next(
                (p for p in Usd.PrimRange(inst.GetPrimAtPath("Stimulus")) if p.IsA(UsdGeom.Sphere)),
                None,
            )
        if sphere:
            spos = xcache.GetLocalToWorldTransform(sphere).ExtractTranslation()
            srad = float(UsdGeom.Sphere(sphere).GetRadiusAttr().Get())
        else:
            spos, srad = origin, 0.0
        mood: dict[str, float] = {}
        mood_prim = inst.GetPrimAtPath("Mood")
        if mood_prim:
            for a in mood_prim.GetAttributes():
                n = a.GetName()
                if n.startswith("acsf:mood:") and a.Get() is not None:
                    mood[n.removeprefix("acsf:mood:")] = float(a.Get())
        name_attr = inst.GetAttribute("acsf:name")
        instances.append(
            {
                "name": name_attr.Get() if name_attr else inst.GetName(),
                "origin": tuple(origin),
                "stimulus": tuple(spos),
                "stimulus_radius": srad,
                "thought_len": {t: len(v) for t, v in sorted(thoughts.items())},
                "reflections": reflections,
                "perceptions": perceptions,
                "stimulus_visible": visible,
                "mood_final": mood,
                "cycle_indexing": inst.GetAttribute("acsf:cycleIndexing").Get()
                if inst.HasAttribute("acsf:cycleIndexing")
                else None,
            }
        )
    return {
        "start": stage.GetStartTimeCode(),
        "end": stage.GetEndTimeCode(),
        "layer_data": {k: v for k, v in layer_data.items()},
        "instances": instances,
    }


def u2b(p: tuple[float, float, float]) -> tuple[float, float, float]:
    """USD Y-up → Blender Z-up."""
    return (p[0], -p[2], p[1])


def blender_main(argv: list[str]) -> None:
    import bpy

    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--scene-json", required=True)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--play-seconds", type=float, default=13.0)
    ap.add_argument("--hold-seconds", type=float, default=2.5)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--only-frames", default="", help="comma list of frames (preview)")
    args = ap.parse_args(argv)

    data = read_stage(args.stage)
    c0, c1 = data["start"], data["end"]
    fps = args.fps
    pad = int(0.5 * fps)
    play = int(args.play_seconds * fps)
    hold = int(args.hold_seconds * fps)
    last = pad + play + hold

    def fr(c: float) -> float:
        return frame_of(c, c0, c1, pad, play)

    # ---- scene reset
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, last
    scene.render.fps = fps
    scene.render.resolution_x, scene.render.resolution_y = args.width, args.height
    scene.render.resolution_percentage = 100
    scene.render.engine = "CYCLES"
    cy = scene.cycles
    cy.device = "CPU"
    cy.samples = args.samples
    cy.use_adaptive_sampling = True
    cy.use_denoising = True
    cy.max_bounces = 2
    cy.diffuse_bounces = cy.glossy_bounces = 1
    cy.transmission_bounces = cy.transparent_max_bounces = 8
    scene.render.threads_mode = "FIXED"
    scene.render.threads = args.threads
    scene.render.use_motion_blur = True
    scene.render.motion_blur_shutter = 0.6
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy" if hasattr(scene.view_settings, "look") else ""
    scene.render.image_settings.file_format = "PNG"
    if hasattr(scene.render, "compositor_device"):
        scene.render.compositor_device = "CPU"  # the GPU compositor crashes headless under WSL

    world = bpy.data.worlds.new("bg")
    scene.world = world
    world.use_nodes = True
    bgn = world.node_tree.nodes["Background"]
    bgn.inputs[0].default_value = (0.010, 0.013, 0.026, 1)
    bgn.inputs[1].default_value = 1.0

    # ---- materials: emission strength = K * object colour R, opacity = object colour G
    def emissive(name: str, rgb: tuple[float, float, float], k: float) -> Any:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        info = nt.nodes.new("ShaderNodeObjectInfo")
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = k
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*rgb, 1)
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(info.outputs["Color"], sep.inputs[0])
        nt.links.new(sep.outputs[0], mul.inputs[0])
        nt.links.new(mul.outputs[0], em.inputs["Strength"])
        nt.links.new(sep.outputs[1], mix.inputs[0])
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])
        return m

    def key_color(obj: Any, frame: float, bright: float, opacity: float = 1.0) -> None:
        obj.color = (bright, opacity, 0.0, 1.0)
        obj.keyframe_insert("color", frame=frame)

    def key_scale(obj: Any, frame: float, s: float) -> None:
        obj.scale = (s, s, s)
        obj.keyframe_insert("scale", frame=frame)

    def sphere_mesh(name: str, segs: int = 16, rings: int = 10) -> Any:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=segs, ring_count=rings, radius=1.0)
        o = bpy.context.active_object
        bpy.ops.object.shade_smooth()
        me = o.data
        me.name = name
        bpy.data.objects.remove(o)
        return me

    m_thought = emissive("thought", THOUGHT_RGB, 3.0)
    m_percep = emissive("perception", PERCEPTION_RGB, 8.0)
    m_stim = emissive("stimulus", PERCEPTION_RGB, 3.0)
    m_refl = emissive("reflection", REFLECTION_RGB, 3.0)
    m_thread = emissive("thread", THREAD_RGB, 1.5)
    m_label = emissive("label", (0.75, 0.8, 0.9), 1.2)
    me_node = sphere_mesh("node")
    me_pulse = sphere_mesh("pulse", 10, 6)
    me_stim = sphere_mesh("stim", 24, 14)
    bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.045, major_segments=40)
    ring_obj = bpy.context.active_object
    me_ring = ring_obj.data
    bpy.data.objects.remove(ring_obj)

    def new_obj(name: str, mesh: Any, mat: Any, loc: tuple[float, float, float]) -> Any:
        o = bpy.data.objects.new(name, mesh)
        if not o.data.materials:
            o.data.materials.append(mat)
        else:
            o.material_slots[0].link = "OBJECT"
            o.material_slots[0].material = mat
        o.location = loc
        scene.collection.objects.link(o)
        return o

    counts: list[dict[str, Any]] = []
    centers = []
    # Archimedean spiral in the USD XZ plane around the instance origin (drawing choice),
    # shared with `acsf-usd view` through acsf_usd.schematic.
    r_max = SPIRAL_R_MAX
    for inst in data["instances"]:
        ox, oy, oz = inst["origin"]
        centers.append(u2b((ox, oy, oz)))
        cycles = sorted(inst["thought_len"])
        n = len(cycles)
        maxlen = max(inst["thought_len"].values(), default=1)
        node_pos: dict[float, tuple[float, float, float]] = {}
        pts = []
        spiral = spiral_positions(cycles, (ox, oy, oz))
        for c in cycles:
            p = u2b(spiral[c])
            node_pos[c] = p
            pts.append(p)
            size = node_radius(inst["thought_len"][c], maxlen)
            o = new_obj(f"{inst['name']}_thought_{int(c)}", me_node, m_thought, p)
            f = fr(c)
            key_scale(o, f - 1, 0.0)
            key_scale(o, f, size * 1.8)
            key_scale(o, f + 6, size)
            key_color(o, f - 1, 0.0)
            key_color(o, f, 2.5)
            key_color(o, f + 8, 0.7)
            key_color(o, f + 60, 0.4)

        # thread through the nodes, revealed in cycle order
        if pts:
            cu = bpy.data.curves.new(f"{inst['name']}_thread", "CURVE")
            cu.dimensions = "3D"
            cu.bevel_depth = 0.012
            cu.bevel_resolution = 2
            sp = cu.splines.new("POLY")
            sp.points.add(len(pts) - 1)
            for pt, p in zip(sp.points, pts, strict=True):
                pt.co = (*p, 1.0)
            cu.bevel_factor_mapping_end = "SPLINE"
            th = bpy.data.objects.new(f"{inst['name']}_thread", cu)
            th.data.materials.append(m_thread)
            scene.collection.objects.link(th)
            th.color = (1.0, 1.0, 0, 1)
            cu.bevel_factor_end = 0.0
            cu.keyframe_insert("bevel_factor_end", frame=fr(cycles[0]))
            cu.bevel_factor_end = 1.0
            cu.keyframe_insert("bevel_factor_end", frame=fr(cycles[-1]))

        # the stage's own Stimulus sphere, lit on cycles where it is visible
        stim_pos = u2b(inst["stimulus"])
        stim = new_obj(f"{inst['name']}_stimulus", me_stim, m_stim, stim_pos)
        stim.scale = (inst["stimulus_radius"],) * 3
        # idle = the stage's "invisible" state, drawn as a faint ghost so the anchor stays readable
        key_color(stim, 1, 0.15, 0.08)
        for c in inst["stimulus_visible"]:
            f = fr(c)
            key_color(stim, f - 0.5, 0.15, 0.08)
            key_color(stim, f, 1.6, 0.9)
            key_color(stim, f + 5, 0.15, 0.08)

        # perception pulses: stimulus sphere → that cycle's node
        for c in inst["perceptions"]:
            target = node_pos.get(c)
            if target is None:  # a perception on a cycle with no thought: stays at the sphere
                target = stim_pos
            o = new_obj(f"{inst['name']}_pulse_{int(c)}", me_pulse, m_percep, stim_pos)
            f = fr(c)
            key_scale(o, f - 1, 0.0)
            key_scale(o, f, 0.075)
            o.location = stim_pos
            o.keyframe_insert("location", frame=f)
            o.location = target
            o.keyframe_insert("location", frame=f + 7)
            key_scale(o, f + 7, 0.05)
            key_scale(o, f + 8, 0.0)
            key_color(o, f, 3.0)
            key_color(o, f + 7, 1.5)

        # reflection rings around the node of the cycle
        for c in inst["reflections"]:
            p = node_pos.get(c, u2b(inst["origin"]))
            o = new_obj(f"{inst['name']}_reflection_{int(c)}", me_ring, m_refl, p)
            f = fr(c)
            key_scale(o, f - 1, 0.0)
            key_scale(o, f, 0.1)
            key_scale(o, f + 8, 0.5)
            key_scale(o, f + 16, 0.2)
            key_color(o, f - 1, 0.0)
            key_color(o, f, 3.0)
            key_color(o, f + 16, 0.6)

        # final mood: a faint tint under the spiral, only after the last cycle
        mood = inst["mood_final"]
        if mood:
            # Tinted by the strongest final dimension (blending all of them washes to grey);
            # the overlay prints every value.
            top = max(sorted(mood), key=lambda d: mood[d])
            col = MOOD_RGB.get(top)
            if col is None:
                h = int(hashlib.sha256(top.encode()).hexdigest()[:6], 16) / 0xFFFFFF
                col = tuple(0.5 + 0.5 * math.cos(2 * math.pi * (h + k / 3)) for k in range(3))
            rgb = list(col)
            inst["mood_top"] = top
            m_mood = bpy.data.materials.new(f"{inst['name']}_mood")
            m_mood.use_nodes = True
            nt = m_mood.node_tree
            nt.nodes.clear()
            out = nt.nodes.new("ShaderNodeOutputMaterial")
            tc = nt.nodes.new("ShaderNodeTexCoord")
            grad = nt.nodes.new("ShaderNodeTexGradient")
            grad.gradient_type = "SPHERICAL"
            vm = nt.nodes.new("ShaderNodeVectorMath")
            vm.operation = "SUBTRACT"
            vm.inputs[1].default_value = (0.5, 0.5, 0.5)
            sc = nt.nodes.new("ShaderNodeVectorMath")
            sc.operation = "SCALE"
            sc.inputs["Scale"].default_value = 2.0
            info = nt.nodes.new("ShaderNodeObjectInfo")
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            mul = nt.nodes.new("ShaderNodeMath")
            mul.operation = "MULTIPLY"
            pw = nt.nodes.new("ShaderNodeMath")
            pw.operation = "POWER"
            pw.inputs[1].default_value = 1.6
            em = nt.nodes.new("ShaderNodeEmission")
            em.inputs["Color"].default_value = (*rgb, 1)
            em.inputs["Strength"].default_value = 0.5
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            mix = nt.nodes.new("ShaderNodeMixShader")
            nt.links.new(tc.outputs["Generated"], vm.inputs[0])
            nt.links.new(vm.outputs[0], sc.inputs[0])
            nt.links.new(sc.outputs[0], grad.inputs["Vector"])
            nt.links.new(grad.outputs["Fac"], pw.inputs[0])
            nt.links.new(info.outputs["Color"], sep.inputs[0])
            nt.links.new(pw.outputs[0], mul.inputs[0])
            nt.links.new(sep.outputs[1], mul.inputs[1])
            nt.links.new(mul.outputs[0], mix.inputs[0])
            nt.links.new(tr.outputs[0], mix.inputs[1])
            nt.links.new(em.outputs[0], mix.inputs[2])
            nt.links.new(mix.outputs[0], out.inputs["Surface"])
            bpy.ops.mesh.primitive_circle_add(vertices=64, radius=r_max + 1.2, fill_type="NGON")
            disc = bpy.context.active_object
            disc.name = f"{inst['name']}_mood"
            c = u2b((ox, oy, oz))
            disc.location = (c[0], c[1], c[2] - 0.6)
            disc.data.materials.append(m_mood)
            fe = fr(c1)
            key_color(disc, 1, 0.0, 0.0)
            key_color(disc, fe + 2, 0.0, 0.0)
            key_color(disc, fe + 2 + fps * 1.2, 1.0, 0.85)

        # name label behind the spiral
        cu = bpy.data.curves.new(f"{inst['name']}_label", "FONT")
        cu.body = inst["name"]
        cu.size = 0.55
        cu.align_x = "CENTER"
        lab = bpy.data.objects.new(f"{inst['name']}_label", cu)
        lab.data.materials.append(m_label)
        lab.color = (1.0, 1.0, 0, 1)
        c = u2b((ox, oy, oz))
        lab.location = (c[0], c[1] + r_max + 0.9, c[2] - 0.3)
        lab.rotation_euler = (math.radians(62), 0, 0)
        scene.collection.objects.link(lab)

        counts.append(
            {
                "instance": inst["name"],
                "cycles": [c0, c1],
                "thought_nodes": n,
                "perception_pulses": len(inst["perceptions"]),
                "stimulus_visible_cycles": len(inst["stimulus_visible"]),
                "reflection_rings": len(inst["reflections"]),
                "mood_final": mood,
                "mood_tint_dimension": inst.get("mood_top"),
            }
        )

    # ---- interpolation: linear for the thread, bezier elsewhere
    for cu in bpy.data.curves:
        ad = cu.animation_data
        if ad and ad.action:
            for fc in _fcurves(ad.action):
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
    for o in scene.objects:
        ad = o.animation_data
        if ad and ad.action:
            for fc in _fcurves(ad.action):
                if fc.data_path == "location":
                    for kp in fc.keyframe_points:
                        kp.interpolation = "SINE"

    # ---- camera: slow orbit around the middle of all instances
    cx = sum(c[0] for c in centers) / len(centers)
    cyy = sum(c[1] for c in centers) / len(centers)
    span = max(c[0] for c in centers) - min(c[0] for c in centers)
    pivot = bpy.data.objects.new("pivot", None)
    pivot.location = (cx, cyy + 0.4, -0.3)
    scene.collection.objects.link(pivot)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 35
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    dist = 12.5 + 0.8 * span
    cam.location = (0, -dist * math.cos(math.radians(38)), dist * math.sin(math.radians(38)))
    cam.parent = pivot
    tt = cam.constraints.new("TRACK_TO")
    tt.target = pivot
    tt.track_axis = "TRACK_NEGATIVE_Z"
    tt.up_axis = "UP_Y"
    scene.camera = cam
    pivot.rotation_euler = (0, 0, math.radians(-6))
    pivot.keyframe_insert("rotation_euler", frame=1)
    pivot.rotation_euler = (0, 0, math.radians(6))
    pivot.keyframe_insert("rotation_euler", frame=last)
    for fc in _fcurves(pivot.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"

    _bloom(scene)

    frames_dir = Path(args.frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "start": c0,
        "end": c1,
        "layer_data": {k: str(v) for k, v in data["layer_data"].items()},
        "fps": fps,
        "pad": pad,
        "play": play,
        "hold": hold,
        "frames": last,
        "instances": counts,
        "objects": len(scene.objects),
    }
    Path(args.scene_json).write_text(json.dumps(meta, indent=2))
    only = [int(x) for x in args.only_frames.split(",") if x]
    t0 = time.time()
    for f in only or range(1, last + 1):
        scene.frame_set(f)
        scene.render.filepath = str(frames_dir / f"{f:04d}.png")
        bpy.ops.render.render(write_still=True)
    meta["render_seconds"] = round(time.time() - t0, 1)
    Path(args.scene_json).write_text(json.dumps(meta, indent=2))


def _fcurves(action: Any) -> list[Any]:
    """F-curves of an action (Blender 4.4+ layered actions, or the legacy list)."""
    out: list[Any] = []
    if hasattr(action, "layers") and action.layers:
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out.extend(bag.fcurves)
    elif hasattr(action, "fcurves"):
        out.extend(action.fcurves)
    return out


def _bloom(scene: Any) -> None:
    """Soft glow via the compositor's Glare node; skipped if this Blender's API differs."""
    import bpy

    try:
        if hasattr(scene, "compositing_node_group"):
            tree = bpy.data.node_groups.new("comp", "CompositorNodeTree")
            scene.compositing_node_group = tree
            rl = tree.nodes.new("CompositorNodeRLayers")
            gl = tree.nodes.new("CompositorNodeGlare")
            tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            out = tree.nodes.new("NodeGroupOutput")
        else:
            scene.use_nodes = True
            tree = scene.node_tree
            tree.nodes.clear()
            rl = tree.nodes.new("CompositorNodeRLayers")
            gl = tree.nodes.new("CompositorNodeGlare")
            out = tree.nodes.new("CompositorNodeComposite")
        _set_glare(gl)
        tree.links.new(rl.outputs["Image"], gl.inputs["Image"])
        tree.links.new(gl.outputs["Image"], out.inputs[0])
    except Exception as exc:  # glow is cosmetic
        print(f"bloom skipped: {exc}")


def _set_glare(gl: Any) -> None:
    for key, val in (("glare_type", "BLOOM"), ("quality", "MEDIUM"), ("size", 7)):
        if hasattr(gl, key):
            try:
                setattr(gl, key, val)
            except (TypeError, ValueError):
                pass
    for name, val in (
        ("Type", "Bloom"),
        ("Quality", "Medium"),
        ("Size", 0.6),
        ("Strength", 0.6),
        ("Threshold", 0.6),
    ):
        sock = gl.inputs.get(name)
        if sock is not None:
            try:
                sock.default_value = val
            except (TypeError, ValueError):
                pass
    if hasattr(gl, "mix"):
        gl.mix = -0.3
    if hasattr(gl, "threshold"):
        gl.threshold = 0.6


# --------------------------------------------------------------------------------------------
# Driver (outside Blender)
# --------------------------------------------------------------------------------------------


def overlay(src: Path, dst: Path, scene: dict[str, Any], title: str) -> None:
    """Burn in the title, cycle counter, legend and the synthetic-layout caption."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int, bold: bool = False) -> Any:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        for d in ("/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "C:/Windows/Fonts"):
            p = Path(d) / name
            if p.exists():
                return ImageFont.truetype(str(p), size)
        return ImageFont.load_default(size=size)

    f_title, f_small, f_cyc = font(17, True), font(13), font(15, True)
    dst.mkdir(parents=True, exist_ok=True)
    c0, c1 = scene["start"], scene["end"]
    pad, play = scene["pad"], scene["play"]
    for png in sorted(src.glob("*.png")):
        frame = int(png.stem)
        im = Image.open(png).convert("RGB")
        w, h = im.size
        d = ImageDraw.Draw(im, "RGBA")
        cycle = c0 + (frame - pad) * (c1 - c0) / play if play else c1
        cycle = int(round(min(max(cycle, c0), c1)))
        d.text((18, 14), title, font=f_title, fill=(225, 232, 245))
        d.text(
            (18, 38),
            f"cycle {cycle} / {int(c1)}  (USD time code)",
            font=f_cyc,
            fill=(150, 200, 255),
        )
        x = 18
        for label, rgb in (
            ("thought", THOUGHT_RGB),
            ("perception", PERCEPTION_RGB),
            ("reflection", REFLECTION_RGB),
        ):
            col = tuple(int(255 * min(1.0, v) ** (1 / 2.2)) for v in rgb)
            d.ellipse((x, h - 47, x + 10, h - 37), fill=col)
            d.text((x + 15, h - 50), label, font=f_small, fill=(205, 212, 225))
            x += 30 + int(d.textlength(label, font=f_small))
        note = "\u00b7 tint: strongest final mood value, shown after the last cycle"
        d.text((x + 6, h - 50), note, font=f_small, fill=(160, 168, 185))
        if frame >= pad + play + 2:  # after the last cycle: the final mood values, as text
            y = 62
            for inst in scene["instances"]:
                vals = " \u00b7 ".join(
                    f"{k} {v:.2f}"
                    for k, v in sorted(inst["mood_final"].items(), key=lambda kv: -kv[1])
                )
                d.text(
                    (18, y),
                    f"final mood ({inst['instance']}, state.json): {vals}",
                    font=f_small,
                    fill=(215, 200, 160),
                )
                y += 18
        d.text((18, h - 28), CAPTION, font=f_small, fill=(175, 182, 198))
        im.save(dst / png.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="golden-run names (tests/fixtures/golden/<name>)")
    ap.add_argument("--blender", required=True, help="path to the blender executable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, help="scratch dir (default: <out>/work)")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--only-frames", default="", help="render only these frames (preview)")
    args = ap.parse_args()

    import imageio_ffmpeg

    out: Path = args.out.resolve()
    work: Path = (args.work or out / "work").resolve()
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    t = time.time()
    stage = work / "stage.usda"
    inputs = [str(GOLDEN / r) for r in args.runs]
    cmd = [sys.executable, "-m", "acsf_usd", "export", *inputs, "-o", str(stage)]
    subprocess.run(cmd, check=True, cwd=REPO)
    timings["export_s"] = round(time.time() - t, 1)

    raw, burned = work / "frames", work / "frames_caption"
    for d in (raw, burned):
        shutil.rmtree(d, ignore_errors=True)
    scene_json = work / "scene.json"
    t = time.time()
    subprocess.run(
        [
            args.blender,
            "-b",
            "--factory-startup",
            "-t",
            str(args.threads),
            "--python",
            str(Path(__file__).resolve()),
            "--",
            "--stage",
            str(stage),
            "--frames-dir",
            str(raw),
            "--scene-json",
            str(scene_json),
            "--threads",
            str(args.threads),
            "--samples",
            str(args.samples),
            "--fps",
            str(args.fps),
            "--only-frames",
            args.only_frames,
        ],
        check=True,
    )
    timings["blender_s"] = round(time.time() - t, 1)
    scene = json.loads(scene_json.read_text())
    names = " and ".join(i["instance"] for i in scene["instances"])
    title = f"A USD stage of the {names} golden runs' journaled events"
    if len(scene["instances"]) == 1:
        title = f"A USD stage of the {names} golden run's journaled events"
    overlay(raw, burned, scene, title)
    frames = sorted(burned.glob("*.png"))
    shutil.copy(frames[-1], out / "stage-poster.png")
    if args.only_frames:
        print(f"preview frames in {burned}")
        return 0

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    t = time.time()
    meta_tag = ["-metadata", f"comment={title} - {CAPTION}", "-metadata", f"title={title}"]
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(args.fps),
            "-start_number",
            "1",
            "-i",
            str(burned / "%04d.png"),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            *meta_tag,
            str(out / "stage.mp4"),
        ],
        check=True,
    )
    gif_fps, gif_w = 8, 448  # keeps the 16 s GIF under 3 MB
    subprocess.run(
        [
            ff,
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            str(args.fps),
            "-start_number",
            "1",
            "-i",
            str(burned / "%04d.png"),
            "-vf",
            f"fps={gif_fps},scale={gif_w}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen=max_colors=64:stats_mode=diff[p];"
            "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            str(out / "stage.gif"),
        ],
        check=True,
    )
    timings["encode_s"] = round(time.time() - t, 1)
    meta = {
        "what": f"{title}, in acsf-usd-env's synthetic schematic layout",
        "caption": CAPTION,
        "runs": args.runs,
        "stage": {
            "start_time_code": scene["start"],
            "end_time_code": scene["end"],
            "custom_layer_data": scene["layer_data"],
        },
        "events_drawn": scene["instances"],
        "render": {
            "engine": "Cycles (CPU)",
            "threads": args.threads,
            "samples": args.samples,
            "denoise": True,
            "motion_blur": True,
            "resolution": [960, 540],
            "fps": args.fps,
            "frames": scene["frames"],
            "seconds": round(scene["frames"] / args.fps, 2),
            "gif": {"fps": gif_fps, "width": gif_w},
        },
        "timings": {**timings, "render_s": scene.get("render_seconds")},
        "sizes_bytes": {
            p: (out / p).stat().st_size for p in ("stage.mp4", "stage.gif", "stage-poster.png")
        },
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(json.dumps(meta["sizes_bytes"]))
    return 0


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401
    except ImportError:
        sys.exit(main())
    else:
        blender_main(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
