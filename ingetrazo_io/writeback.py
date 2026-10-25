# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""Blender → IngeTrazo, for two things only: the texture mapping and the
cameras. Geometry never travels this way.

Modelling is easier in IngeTrazo; placing a texture precisely is easier in
Blender's UV editor (issue #2). IngeTrazo keeps, per face, an affine
world→UV map (``texture["uvw"]``, the one its own COLLADA/OBJ importers
fit), so the UVs edited here can be written back into the ``.igz`` face by
face.

A camera framed in Blender can become a scene of the document (issue #3).
IngeTrazo keeps a scene's camera as an orbit: a target, a distance, two
angles, the field of view and the kind of view (perspective, parallel or
two point), so a Blender camera is turned into those. IngeTrazo has no
roll, so a camera tilted sideways arrives level.

Safety first, since this writes the user's own document:

- only when the ``.igz`` has not changed since it was imported or reloaded
  (otherwise IngeTrazo's newer work would be overwritten): reload first;
- only faces whose corners are still where IngeTrazo has them, and that
  carry their own texture (a face painted through its group is skipped);
- a scene that already exists keeps everything but its camera (its layers,
  style, section, shadows and hidden objects);
- a dated copy of the document is kept next to it before writing,
  ``<name>.<YYYY-MM-DD-HHMMSS>.igz.bak`` (the newest :data:`KEEP_BACKUPS`).
"""
from __future__ import annotations

import math
import os
import re
import shutil
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from . import build, igz

#: How far (m) a corner may sit from IngeTrazo's and still be the same.
_SAME_POINT = 1e-4
#: Below this the UVs did not change.
_SAME_UV = 1e-6
#: Dated backups kept per document; older ones are removed.
KEEP_BACKUPS = 10


def backup(doc_path: str) -> str:
    """Copy the document to ``<name>.<date-time>.igz.bak`` beside it, keep
    the newest :data:`KEEP_BACKUPS` of them, and return the new one's path."""
    import time
    p = Path(doc_path)
    stem = p.name[:-4] if p.name.lower().endswith(".igz") else p.name
    out = p.with_name(f"{stem}.{time.strftime('%Y-%m-%d-%H%M%S')}.igz.bak")
    n = 1
    while out.exists():               # two sends within the same second
        out = p.with_name(f"{stem}.{time.strftime('%Y-%m-%d-%H%M%S')}-{n}.igz.bak")
        n += 1
    shutil.copy2(p, out)
    pattern = re.compile(re.escape(stem) + r"\.\d{4}-\d\d-\d\d-\d{6}(-\d+)?\.igz\.bak$")
    olds = sorted((q for q in p.parent.iterdir() if pattern.match(q.name)),
                  key=lambda q: q.stat().st_mtime)
    for q in olds[:-KEEP_BACKUPS]:
        try:
            q.unlink()
        except OSError:
            pass
    return str(out)


class Changed(Exception):
    """The .igz changed in IngeTrazo after it was imported here."""


def _root(doc_path: str):
    for coll in bpy.data.collections:
        if coll.get(build.DOC_PROP) == doc_path and \
                coll.get(build.MTIME_PROP) is not None:
            return coll
    return None


def _load(doc_path: str):
    """The document as loaded here: ``(path, root collection, scene)``.
    Refuses one that IngeTrazo saved again since (:class:`Changed`)."""
    doc_path = str(Path(doc_path).resolve())
    root = _root(doc_path)
    if root is None:
        raise ValueError("this document was not imported here")
    mtime = os.path.getmtime(doc_path)
    if abs(mtime - float(root[build.MTIME_PROP])) > 1e-6:
        raise Changed("the .igz changed in IngeTrazo since it was last "
                      "loaded here: reload it first")
    doc = igz.Document(doc_path)
    try:
        return doc_path, root, doc.scene
    finally:
        doc.close()


def send_uvs(doc_path: str, own_paint: bool = False,
             dry_run: bool = False) -> dict:
    """Write the UVs edited in Blender into the ``.igz`` at ``doc_path``.

    A face whose UVs changed but that wears its group's paint (it has no
    texture of its own) gets its own copy of that paint, mapped as edited,
    when ``own_paint`` (the face's own paint wins over the group's, as in
    IngeTrazo), and is skipped otherwise. Never inside a component: its
    faces are shared by every copy, which would all change.

    ``dry_run`` counts without writing (what the dialog shows). Returns:
    ``written`` (faces sent), ``own_paint`` (of those, given their own
    paint), ``group_paint`` (changed, wear the group's paint, not sent),
    ``component_paint`` (the same inside a component: never sent),
    ``moved`` (reshaped in Blender: not sent), ``unchanged``, ``max_error``
    (UV units, 0 = exact)."""
    import json
    doc_path, root, scene = _load(doc_path)
    stats = {"written": 0, "own_paint": 0, "group_paint": 0,
             "component_paint": 0, "moved": 0, "unchanged": 0,
             "max_error": 0.0}
    done: set = set()                 # (src, face) already handled
    for me in bpy.data.meshes:
        if me.get(build.DOC_PROP) != doc_path or not me.get(build.SRC_PROP):
            continue
        attr = me.attributes.get(build.FACE_ATTR)
        uvl = me.uv_layers.active
        if attr is None or uvl is None or not me.polygons \
                or len(attr.data) != len(me.polygons):
            continue
        src = str(me[build.SRC_PROP])
        faces = igz.faces_at(scene, src)
        if not isinstance(faces, list):
            continue
        try:
            wear = json.loads(me.get(build.WEAR_PROP) or "{}")
        except ValueError:
            wear = {}
        origin = me.get(build.ORIGIN_PROP)
        to_it = Matrix([origin[r * 4:(r + 1) * 4] for r in range(4)]) \
            if origin is not None and len(origin) == 16 else Matrix.Identity(4)
        index = [0] * len(me.polygons)
        attr.data.foreach_get("value", index)
        loops: dict = {}
        for poly, fi in zip(me.polygons, index):
            pts, uvs = loops.setdefault(fi, ([], []))
            for li in poly.loop_indices:
                co = to_it @ me.vertices[me.loops[li].vertex_index].co
                pts.append((co.x, co.y, co.z))
                uv = uvl.data[li].uv
                uvs.append((uv.x, uv.y))
        for fi, (pts, uvs) in loops.items():
            if not 0 <= fi < len(faces) or (src, fi) in done:
                continue
            face = faces[fi]
            tex = face.get("texture")
            own = isinstance(tex, dict)
            if not own:
                if face.get("color") is not None:
                    continue          # its own plain colour: no texture
                tex = wear.get("texture")
                if not isinstance(tex, dict):
                    continue          # no texture at all: no UVs to keep
            outer = [tuple(p) for p in face.get("vertices", [])]
            corners = outer + [tuple(p) for h in face.get("holes", []) or [] for p in h]
            if len(outer) < 3 or any(
                    min((Vector(p) - Vector(c)).length for c in corners) > _SAME_POINT
                    for p in pts):
                stats["moved"] += 1
                continue
            now = igz.face_uvs(tex, igz.polygon_normal(outer), pts)
            if max(abs(a - b) for uv, cur in zip(uvs, now)
                   for a, b in zip(uv, cur)) < _SAME_UV:
                stats["unchanged"] += 1
                continue
            if not own:
                if src.startswith("proto/"):
                    stats["component_paint"] += 1
                    continue
                if not own_paint:
                    stats["group_paint"] += 1
                    continue
            uvw, err = igz.fit_uvw(pts, uvs)
            if uvw is None:
                stats["moved"] += 1
                continue
            done.add((src, fi))
            stats["written"] += 1
            stats["max_error"] = max(stats["max_error"], err)
            if dry_run:
                if not own:
                    stats["own_paint"] += 1
                continue
            if not own:
                # The group's paint, as the face's own (its own paint wins).
                tex = dict(tex)
                face["texture"] = tex
                for k in ("mat", "opacity"):
                    if wear.get(k) is not None and face.get(k) is None:
                        face[k] = wear[k]
                stats["own_paint"] += 1
            tex["uvw"] = [round(x, 12) for x in uvw]
    if stats["written"] and not dry_run:
        stats["backup"] = backup(doc_path)
        igz.write_document(doc_path, scene)
        # Our own save: the auto-reload must not take it for IngeTrazo's.
        root[build.MTIME_PROP] = os.path.getmtime(doc_path)
    return stats


# ---- Cameras ------------------------------------------------------------------

#: IngeTrazo's orbit stops 1° short of straight down or up (core/camera.py).
_MAX_PITCH = math.radians(89.0)
#: Below this a lens shift is none.
_NO_SHIFT = 1e-6


def _model_centre(root) -> Vector:
    """The middle of the document's box, as built here."""
    pts = [obj.matrix_world @ Vector(c) for obj in root.all_objects
           if obj.type == "MESH" and not obj.hide_render for c in obj.bound_box]
    if not pts:
        return Vector((0.0, 0.0, 0.0))
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2.0


def camera_view(obj, scene, centre) -> tuple:
    """A Blender camera as IngeTrazo's orbit camera: ``(camera, notes)``.

    ``camera`` is what :func:`igz.set_view_camera` takes. ``notes`` says
    what could not go: ``"roll"`` (tilted sideways, it arrives level) and
    ``"shift"`` (a lens shift that only a level camera keeps, as a two point
    perspective; any other camera aims at the middle of its frame instead).

    The orbit needs a target, which a Blender camera does not have: it is
    the focus point when depth of field is on, else the point of the sight
    line nearest the middle of the model. It only sets what IngeTrazo
    orbits around, never what the scene shows."""
    data = obj.data
    rot = obj.matrix_world.to_quaternion()
    eye = obj.matrix_world.translation.copy()
    right, up, back = (rot @ Vector(a) for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    # The frame as rendered (sensor fit, resolution and shift included), in
    # the camera's own space: z is the depth, x and y the frame's corners.
    frame = data.view_frame(scene=scene)
    xs, ys = [v.x for v in frame], [v.y for v in frame]
    depth = abs(frame[0].z) or 1.0
    half_h = (max(ys) - min(ys)) / 2.0
    cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    notes = set()
    parallel = data.type == "ORTHO"
    two_point = False
    if parallel:
        fov = 45.0
        eye = eye + right * cx + up * cy      # a shift only slides the frame
        look = -back
    else:
        fov = math.degrees(2.0 * math.atan(half_h / depth))
        if abs(cx) > _NO_SHIFT or abs(cy) > _NO_SHIFT:
            level = abs(back.z) < 1e-4 and abs(right.z) < 1e-4
            if level and abs(cx) <= _NO_SHIFT:
                two_point = True              # how the import builds one
            else:
                notes.add("shift")
        look = (rot @ Vector((cx, cy, -depth))).normalized()
    if abs(look.z) < math.sin(_MAX_PITCH) and abs(right.z) > 1e-3:
        notes.add("roll")
    dof = data.dof
    if not parallel and dof.use_dof:
        d = (dof.focus_object.matrix_world.translation - eye).dot(look) \
            if dof.focus_object is not None else dof.focus_distance
    else:
        d = (centre - eye).dot(look)
    if d < 0.5:                       # the model behind, or at, the camera
        d = max((centre - eye).length, 1.0)
    target = eye + look * d
    distance = half_h / math.tan(math.radians(fov) / 2.0) if parallel else d
    away = -look                              # from the target to the eye
    pitch = math.asin(max(-1.0, min(1.0, away.z)))
    if abs(pitch) < _MAX_PITCH:
        yaw = math.atan2(away.y, away.x)
    else:
        # Straight down (a plan) or up: what is up on the screen sets the
        # yaw, as in IngeTrazo's own importers (saved_views.from_lookat).
        yaw = math.atan2(-up.y, -up.x) if pitch > 0 else math.atan2(up.y, up.x)
        pitch = math.copysign(_MAX_PITCH, pitch)
    return ({"target": tuple(target), "distance": distance, "yaw": yaw,
             "pitch": pitch, "fov": fov, "parallel": parallel,
             "two_point": two_point}, notes)


def send_cameras(doc_path: str, names, dry_run: bool = False) -> dict:
    """Write the Blender cameras called ``names`` into the ``.igz`` at
    ``doc_path``, as scenes.

    Each camera goes to the scene it stands for: the one it was imported
    from, the one it was sent to before, or else one with its name. A
    camera with none becomes a new scene, named after it. Only the scene's
    camera changes. ``dry_run`` works it all out without writing (what the
    dialog shows).

    Returns ``scenes``: per camera sent, ``(camera, scene, new, notes)``,
    with the notes of :func:`camera_view`; and ``backup`` once written."""
    doc_path, root, scene = _load(doc_path)
    views = scene.get("saved_views") or []
    if not isinstance(views, list) or not all(isinstance(v, dict) for v in views):
        raise ValueError("this document's scenes could not be read")
    bpy.context.view_layer.update()           # cameras placed by a script
    centre = _model_centre(root)
    sent = []
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "CAMERA":
            continue
        cam, notes = camera_view(obj, bpy.context.scene, centre)
        key = str(obj.get(build.KEY_PROP, "")) \
            if obj.get(build.DOC_PROP) == doc_path else ""
        at = None
        if key.startswith("view/") and key[5:].isdigit() \
                and int(key[5:]) < len(views):
            at = int(key[5:])
        if at is None:
            before = obj.get(build.SCENE_PROP) \
                if obj.get(build.SCENE_DOC_PROP) == doc_path else None
            for want in (before, obj.name):
                at = next((i for i, v in enumerate(views)
                           if want and v.get("name") == want), None)
                if at is not None:
                    break
        new = at is None
        if new:
            views.append({"name": obj.name})
            at = len(views) - 1
        igz.set_view_camera(views[at], cam)
        sent.append((obj, at, new, sorted(notes)))
    out = {"scenes": [(obj.name, views[at]["name"], new, notes)
                      for obj, at, new, notes in sent]}
    if sent and not dry_run:
        scene["saved_views"] = views
        out["backup"] = backup(doc_path)
        igz.write_document(doc_path, scene)
        # Our own save: the auto-reload must not take it for IngeTrazo's.
        root[build.MTIME_PROP] = os.path.getmtime(doc_path)
        for obj, at, _new, _notes in sent:
            if str(obj.get(build.KEY_PROP, "")).startswith("view/") \
                    and obj.get(build.DOC_PROP) == doc_path:
                continue                      # imported from that scene
            obj[build.SCENE_DOC_PROP] = doc_path
            obj[build.SCENE_PROP] = views[at]["name"]
            obj[build.SCENE_SIG_PROP] = igz.view_sig(views[at])
    return out
