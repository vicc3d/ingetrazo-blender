# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""Blender → IngeTrazo, for one thing only: the texture mapping.

Modelling is easier in IngeTrazo; placing a texture precisely is easier in
Blender's UV editor (issue #2). IngeTrazo keeps, per face, an affine
world→UV map (``texture["uvw"]``, the one its own COLLADA/OBJ importers
fit), so the UVs edited here can be written back into the ``.igz`` face by
face. Geometry never travels this way.

Safety first, since this writes the user's own document:

- only when the ``.igz`` has not changed since it was imported or reloaded
  (otherwise IngeTrazo's newer work would be overwritten): reload first;
- only faces whose corners are still where IngeTrazo has them, and that
  carry their own texture (a face painted through its group is skipped);
- a dated copy of the document is kept next to it before writing,
  ``<name>.<YYYY-MM-DD-HHMMSS>.igz.bak`` (the newest :data:`KEEP_BACKUPS`).
"""
from __future__ import annotations

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


def send_uvs(doc_path: str, own_paint: bool = False,
             dry_run: bool = False) -> dict:
    """Write the UVs edited in Blender into the ``.igz`` at ``doc_path``.

    A face whose UVs changed but that wears its group's paint (it has no
    texture of its own) gets its own copy of that paint, mapped as edited,
    when ``own_paint`` — the face's own paint wins over the group's, as in
    IngeTrazo — and is skipped otherwise. Never inside a component: its
    faces are shared by every copy, which would all change.

    ``dry_run`` counts without writing (what the dialog shows). Returns:
    ``written`` (faces sent), ``own_paint`` (of those, given their own
    paint), ``group_paint`` (changed, wear the group's paint, not sent),
    ``component_paint`` (the same inside a component: never sent),
    ``moved`` (reshaped in Blender: not sent), ``unchanged``, ``max_error``
    (UV units, 0 = exact)."""
    import json
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
        scene = doc.scene
    finally:
        doc.close()
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
