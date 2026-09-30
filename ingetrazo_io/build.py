# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""Build (or rebuild) a Blender scene from an IngeTrazo document.

The mapping:

- the document → one collection named after the file; each IngeTrazo tag
  (layer) → a child collection, hidden if the tag was hidden;
- loose geometry → one object; each group → one object at its placement;
  nested groups → child objects (parented), so moving the parent in Blender
  moves the whole assembly as in IngeTrazo;
- component copies share ONE mesh datablock (linked duplicates): edit it
  once in Blender, every copy follows;
- faces → polygons as drawn (n-gons kept; faces with holes, which Blender
  cannot hold, are triangulated); soft edges → smooth shading with every
  other edge marked sharp, so curved surfaces read smooth and corners stay
  crisp;
- paint → materials (the registry name when the face has one), textures
  packed into the .blend with IngeTrazo's exact UVs; a group's paint goes
  to its unpainted faces;
- the author's camera and every saved view → cameras.

Everything created carries ``ingetrazo_doc`` / ``ingetrazo_key`` custom
properties, which is how a reload finds and updates it in place while
leaving whatever you added in Blender (lights, your own cameras, modifiers,
materials you edited) alone.
"""
from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import bpy
from mathutils import Matrix, Vector
from mathutils.geometry import tessellate_polygon

from . import igz

DOC_PROP = "ingetrazo_doc"
KEY_PROP = "ingetrazo_key"
MTIME_PROP = "ingetrazo_mtime"
DEFAULT_MATERIAL = "IngeTrazo por defecto"


def _srgb_to_linear(c: float) -> float:
    c = max(0.0, min(1.0, float(c)))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _short(name: str, limit: int = 63) -> str:
    return name if len(name) <= limit else name[:limit]


class Builder:
    def __init__(self, context, doc: igz.Document, update_materials: bool = False):
        self.context = context
        self.doc = doc
        self.scene_json = doc.scene
        self.update_materials = update_materials
        self.doc_path = str(doc.path.resolve())
        self.images: dict = {}
        self.materials: dict = {}
        self.meshes: dict = {}
        self.tex_dir = Path(tempfile.mkdtemp(prefix="ingetrazo-tex-"))
        self.protos = self.scene_json.get("protos", []) or []
        registry = self.scene_json.get("materials", []) or []
        self.registry = {m.get("name"): m for m in registry if isinstance(m, dict)}
        self.stats = {"objects": 0, "faces": 0, "components": 0}
        self.facing: list = []
        self.uid_count: dict = {}
        self.count_uids(self.scene_json.get("groups"))

    # ---- Collections -------------------------------------------------------
    def root_collection(self):
        for coll in bpy.data.collections:
            if coll.get(DOC_PROP) == self.doc_path:
                return coll
        coll = bpy.data.collections.new(_short(self.doc.path.stem))
        coll[DOC_PROP] = self.doc_path
        self.context.scene.collection.children.link(coll)
        return coll

    def tag_collection(self, root, tag: str | None):
        if not tag:
            return root
        name = _short(f"{root.name} · {tag}")
        for coll in root.children:
            if coll.get("ingetrazo_tag") == tag:
                return coll
        coll = bpy.data.collections.new(name)
        coll["ingetrazo_tag"] = tag
        coll[DOC_PROP] = self.doc_path
        root.children.link(coll)
        return coll

    # ---- Materials -----------------------------------------------------------
    def image(self, tex: dict):
        key = tex.get("embed") or tex.get("path")
        if key in self.images:
            return self.images[key]
        got = self.doc.image_bytes(tex)
        img = None
        if got is not None:
            fname, data = got
            out = self.tex_dir / fname
            out.write_bytes(data)
            existing = bpy.data.images.get(_short(fname))
            if existing is not None and existing.get(DOC_PROP) == self.doc_path \
                    and not self.update_materials:
                img = existing
            else:
                img = bpy.data.images.load(str(out), check_existing=False)
                img.name = _short(fname)
                img[DOC_PROP] = self.doc_path
                img.pack()
        self.images[key] = img
        return img

    def material(self, attrs: dict):
        tex = attrs.get("texture")
        tex = tex if isinstance(tex, dict) and (tex.get("embed") or tex.get("path")) else None
        color = attrs.get("color")
        opacity = attrs.get("opacity")
        reg = attrs.get("mat")
        img = self.image(tex) if tex is not None else None
        key = (reg, tuple(round(c, 4) for c in color) if color else None,
               img.name if img is not None else None,
               round(float(opacity), 3) if opacity is not None else None)
        if key in self.materials:
            return self.materials[key]
        if reg:
            name = reg
        elif img is not None:
            name = Path(img.name).stem
        elif color:
            name = "IT #%02X%02X%02X" % tuple(int(round(c * 255)) for c in color[:3])
        else:
            name = DEFAULT_MATERIAL
        if opacity is not None and float(opacity) < 1.0:
            name += f" {int(round(float(opacity) * 100))}%"
        name = _short(name)
        mat = bpy.data.materials.get(name)
        if mat is None or self.update_materials:
            mat = mat or bpy.data.materials.new(name)
            mat[DOC_PROP] = self.doc_path
            self._paint(mat, color or (None if img is not None else igz.DEFAULT_COLOR),
                        img, opacity)
        self.materials[key] = mat
        return mat

    @staticmethod
    def _paint(mat, color, img, opacity) -> None:
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            try:
                mat.use_nodes = True
            except AttributeError:
                pass
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None) \
                or nodes.new("ShaderNodeOutputMaterial")
            links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        for n in [n for n in nodes if n.type == "TEX_IMAGE"]:
            nodes.remove(n)
        bsdf.inputs["Roughness"].default_value = 0.8
        if color is not None:
            lin = [_srgb_to_linear(c) for c in color[:3]]
            bsdf.inputs["Base Color"].default_value = (*lin, 1.0)
            mat.diffuse_color = (*lin, 1.0)
        if img is not None:
            node = nodes.new("ShaderNodeTexImage")
            node.image = img
            node.location = (bsdf.location.x - 320, bsdf.location.y)
            links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
            if img.depth == 32 or img.channels == 4:
                links.new(node.outputs["Alpha"], bsdf.inputs["Alpha"])
        if opacity is not None and float(opacity) < 1.0:
            bsdf.inputs["Alpha"].default_value = float(opacity)
            mat.diffuse_color[3] = float(opacity)
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "BLENDED"

    # ---- Meshes ----------------------------------------------------------------
    def mesh(self, mjson: dict, container: dict | None, name: str):
        """A mesh datablock for an IngeTrazo mesh, shared by every user of
        the same prototype and container paint (component copies)."""
        ckey = None
        if container:
            ckey = repr(sorted((k, repr(v)) for k, v in container.items()))
        cache_key = (id(mjson), ckey)
        if cache_key in self.meshes:
            return self.meshes[cache_key]
        verts: list = []
        vidx: dict = {}

        def vi(p) -> int:
            k = (round(p[0], 6), round(p[1], 6), round(p[2], 6))
            i = vidx.get(k)
            if i is None:
                i = vidx[k] = len(verts)
                verts.append((float(p[0]), float(p[1]), float(p[2])))
            return i

        faces: list = []
        face_mats: list = []
        face_uvs: list = []
        mat_slots: list = []
        slot_of: dict = {}
        diagonals: set = set()

        def slot(mat) -> int:
            s = slot_of.get(mat.name)
            if s is None:
                s = slot_of[mat.name] = len(mat_slots)
                mat_slots.append(mat)
            return s

        for f in mjson.get("faces", []) or []:
            if f.get("hidden"):
                continue
            outer = [tuple(p) for p in f.get("vertices", [])]
            if len(outer) < 3:
                continue
            attrs = igz.effective_attrs(f, container)
            mat = self.material(attrs)
            s = slot(mat)
            normal = igz.polygon_normal(outer)
            tex = attrs.get("texture") if isinstance(attrs.get("texture"), dict) else None
            holes = [[tuple(p) for p in h] for h in f.get("holes", []) or [] if len(h) >= 3]
            if holes:
                rings = [outer] + holes
                flat = [p for r in rings for p in r]
                tris = tessellate_polygon([[Vector(p) for p in r] for r in rings])
                polys = []
                for a, b, c in tris:
                    tri = [flat[a], flat[b], flat[c]]
                    tn = igz.cross(igz.sub(tri[1], tri[0]), igz.sub(tri[2], tri[0]))
                    if igz.dot(tn, normal) < 0:
                        tri = [tri[0], tri[2], tri[1]]
                    polys.append(tri)
            else:
                polys = [outer]
            for poly in polys:
                idx = []
                for p in poly:
                    i = vi(p)
                    if not idx or idx[-1] != i:
                        idx.append(i)
                if len(idx) > 1 and idx[0] == idx[-1]:
                    idx.pop()
                if len(idx) < 3 or len(set(idx)) != len(idx):
                    continue
                faces.append(idx)
                face_mats.append(s)
                face_uvs.append(igz.face_uvs(tex, normal, poly) if tex else None)
                if holes:
                    for k in range(len(idx)):
                        diagonals.add(frozenset((idx[k], idx[(k + 1) % len(idx)])))
        soft: set = set()
        loose: list = []
        face_edges = {frozenset((fi[k], fi[(k + 1) % len(fi)]))
                      for fi in faces for k in range(len(fi))}
        for e in mjson.get("edges", []) or []:
            a, b = vi(tuple(e["a"])), vi(tuple(e["b"]))
            if a == b:
                continue
            key = frozenset((a, b))
            if e.get("soft"):
                soft.add(key)
            if key not in face_edges and not e.get("hidden"):
                loose.append((a, b))
        # A holed face's triangulation adds diagonals: they are not IngeTrazo
        # edges, so they must never shade sharp.
        real = {frozenset((vi(tuple(e["a"])), vi(tuple(e["b"]))))
                for e in mjson.get("edges", []) or []}
        soft |= {d for d in diagonals if d not in real}

        me = bpy.data.meshes.new(_short(name))
        me.from_pydata(verts, loose, faces)
        me.validate(clean_customdata=False)
        for m in mat_slots:
            me.materials.append(m)
        if me.polygons:
            me.polygons.foreach_set("material_index", face_mats[:len(me.polygons)])
            if any(uv is not None for uv in face_uvs):
                layer = me.uv_layers.new(name="UVMap")
                flat_uv = []
                for fi, poly in enumerate(me.polygons):
                    uv = face_uvs[fi] if fi < len(face_uvs) else None
                    for k in range(poly.loop_total):
                        u, v = uv[k] if uv and k < len(uv) else (0.0, 0.0)
                        flat_uv += (u, v)
                layer.data.foreach_set("uv", flat_uv)
            # Smooth shading everywhere, sharp on every edge that is not
            # soft in IngeTrazo: exactly its smoothing groups.
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
            sharp = [frozenset(e.vertices) not in soft for e in me.edges]
            attr = me.attributes.get("sharp_edge") or \
                me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
            attr.data.foreach_set("value", sharp)
        me.update()
        me[DOC_PROP] = self.doc_path
        self.stats["faces"] += len(me.polygons)
        self.meshes[cache_key] = me
        return me

    # ---- Objects -----------------------------------------------------------------
    def existing_objects(self) -> dict:
        objs = [o for o in bpy.data.objects
                if o.get(DOC_PROP) == self.doc_path and o.get(KEY_PROP)]
        # Second key: the path of names, for groups saved without a uid
        # (documents older than the uid) — a first save that adds uids must
        # not recreate every object and drop the user's modifiers.
        self.old_by_name = {o["ingetrazo_name_key"]: o for o in objs
                            if o.get("ingetrazo_name_key")}
        return {o.get(KEY_PROP): o for o in objs}

    def take(self, key, name_key):
        obj = self.old.pop(key, None)
        if obj is None and name_key:
            obj = self.old_by_name.pop(name_key, None)
            if obj is not None:
                self.old.pop(obj.get(KEY_PROP), None)
        elif obj is not None:
            self.old_by_name.pop(obj.get("ingetrazo_name_key"), None)
        return obj

    def place(self, key, name, data, coll, parent=None, matrix=None,
              name_key=None):
        obj = self.take(key, name_key)
        if obj is not None and (obj.data is None) != (data is None):
            bpy.data.objects.remove(obj)
            obj = None
        if obj is None:
            obj = bpy.data.objects.new(_short(name), data)
            obj[DOC_PROP] = self.doc_path
            obj[KEY_PROP] = key
        else:
            obj[KEY_PROP] = key
            obj.name = _short(name)
            if data is not None and obj.data is not data:
                obj.data = data
        if name_key:
            obj["ingetrazo_name_key"] = name_key
        for c in list(obj.users_collection):
            if c is not coll:
                c.objects.unlink(obj)
        if coll not in obj.users_collection:
            coll.objects.link(obj)
        obj.parent = parent
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = Matrix(matrix) if matrix is not None else Matrix.Identity(4)
        self.stats["objects"] += 1
        return obj

    def group(self, g: dict, key: str, root, parent=None,
              container: dict | None = None, name_key: str | None = None):
        name = g.get("name") or "Grupo"
        paint = g.get("material") if isinstance(g.get("material"), dict) else None
        wear = paint or container
        if "proto" in g:
            try:
                mjson = self.protos[int(g["proto"])]
            except (IndexError, ValueError, TypeError):
                mjson = {}
        else:
            mjson = g
        billboard = g.get("billboard")
        matrix = igz.matrix_from_column_major(g["xform"]) if g.get("xform") else None
        if billboard is True:
            me, local = self.faceme_card(mjson, wear, name)
        else:
            me = self.mesh(mjson, wear, name) if mjson.get("faces") or mjson.get("edges") else None
            if me is not None and not me.polygons and not me.edges:
                me = None
            local = None
            if me is not None and (matrix is None or billboard):
                # A classic group's mesh sits in world coordinates: give the
                # object its own origin (the bottom centre of the box, where
                # SketchUp puts a group's axes) so it rotates and scales in
                # place; a face-me also turns its sheet to face −Y.
                local = self.recentre(me, turn=bool(billboard))
        if local is not None:
            matrix = (Matrix(matrix) if matrix is not None else Matrix.Identity(4)) @ local
        coll = self.tag_collection(root, g.get("layer"))
        obj = self.place(key, name, me, coll, parent, matrix, name_key)
        if g.get("component", "proto" in g) and "proto" in g:
            self.stats["components"] += 1
            obj["ingetrazo_component"] = True
        hidden = bool(g.get("hidden"))
        obj.hide_viewport = hidden
        obj.hide_render = hidden
        if billboard:
            obj["ingetrazo_billboard"] = str(billboard)
            self.facing.append(obj)
        for i, child, nk in self._named(g.get("children", []) or [], name_key or key):
            self.group(child, self.key_for(child, f"{key}/{i}"), root, obj, wear, nk)
        return obj

    def recentre(self, me, turn: bool = False):
        """Move ``me``'s origin to the bottom centre of its bounding box
        (turning a face-me sheet so its front looks down −Y) and return the
        matrix that puts it back where it was. ``None`` for a shared mesh
        (component copies keep the component's own origin)."""
        if me.users or not me.vertices:
            return None
        xs = [v.co.x for v in me.vertices]
        ys = [v.co.y for v in me.vertices]
        zs = [v.co.z for v in me.vertices]
        anchor = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
        m = Matrix.Translation(anchor)
        if turn:
            n = Vector((0.0, 0.0, 0.0))
            for poly in me.polygons:
                n += poly.normal * poly.area
            n.z = 0.0
            if n.length > 1e-9:
                n.normalize()
                # R_z(phi) takes the sheet's −Y front onto the face normal.
                m = m @ Matrix.Rotation(math.atan2(n.x, -n.y), 4, "Z")
        me.transform(m.inverted())
        me.update()
        return m

    def faceme_card(self, mjson: dict, container, name: str):
        """A textured face-me figure (SketchUp's 2D people): IngeTrazo draws
        the image ONCE over the card's box, turned toward the camera. Built
        the same way here — a card on the local XZ plane facing −Y, UVs 0..1,
        origin at its foot — and the Locked Track added in :meth:`build`
        keeps it turned to the scene camera."""
        pts = [tuple(p) for f in mjson.get("faces", []) or [] for p in f.get("vertices", [])]
        face = next((f for f in mjson.get("faces", []) or []
                     if isinstance(f.get("texture"), dict)), None)
        if not pts or face is None:
            me = self.mesh(mjson, container, name)
            return me, self.recentre(me, turn=True)
        xs, ys, zs = zip(*pts)
        w = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        h = max(zs) - min(zs)
        anchor = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
        me = bpy.data.meshes.new(_short(name))
        me.from_pydata([(-w / 2, 0, 0), (w / 2, 0, 0), (w / 2, 0, h), (-w / 2, 0, h)],
                       [], [(0, 1, 2, 3)])
        me.materials.append(self.material(igz.effective_attrs(face, container)))
        uv = me.uv_layers.new(name="UVMap")
        uv.data.foreach_set("uv", [0, 0, 1, 0, 1, 1, 0, 1])
        me.update()
        me[DOC_PROP] = self.doc_path
        self.stats["faces"] += 1
        return me, Matrix.Translation(anchor)

    def key_for(self, g: dict, path_key: str) -> str:
        """The identity a reload finds a group's object by: its uid when the
        document gives it one no other group shares — so a group moved into
        or out of another keeps its object, modifiers and all — else its
        position in the tree."""
        uid = g.get("uid")
        if uid and self.uid_count.get(uid) == 1:
            return f"uid/{uid}"
        return path_key

    def count_uids(self, groups) -> None:
        for g in groups or []:
            uid = g.get("uid")
            if uid:
                self.uid_count[uid] = self.uid_count.get(uid, 0) + 1
            self.count_uids(g.get("children"))

    @staticmethod
    def _named(groups, prefix):
        """``(index, group, name key)``: the name key counts repeats, so two
        chairs called «Silla» are «Silla#0» and «Silla#1»."""
        seen: dict = {}
        for i, g in enumerate(groups):
            nm = g.get("name") or "Grupo"
            n = seen.get(nm, 0)
            seen[nm] = n + 1
            yield i, g, f"{prefix}/{nm}#{n}"

    def overview(self, root) -> dict | None:
        """An IngeTrazo-style view of the whole model: the default orbit
        angles (yaw −45°, pitch 30°) at the distance that frames the
        bounding sphere of everything imported."""
        pts = []
        for obj in root.all_objects:
            if obj.type == "MESH" and obj.get(DOC_PROP) == self.doc_path \
                    and not obj.hide_render:
                mw = obj.matrix_world
                pts.extend(mw @ Vector(c) for c in obj.bound_box)
        if not pts:
            return None
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        centre = (lo + hi) / 2.0
        radius = max((hi - lo).length / 2.0, 0.1)
        fov = 35.0
        dist = radius / math.sin(math.radians(fov) / 2.0) * 0.8
        return {"target": list(centre), "distance": dist, "yaw": -math.pi / 4,
                "pitch": math.pi / 6, "fov": fov}

    def cameras(self, root) -> None:
        views = []
        home = self.scene_json.get("camera")
        if isinstance(home, dict):
            views.append(("camera", "Cámara IngeTrazo", home))
        # Every file gets a view of the whole model — the first saved view
        # may frame a detail, or a spot the model has since moved from.
        self.context.view_layer.update()
        whole = self.overview(root)
        if whole is not None:
            views.append(("overview", "Vista general", whole))
        for i, v in enumerate(self.scene_json.get("saved_views", []) or []):
            if isinstance(v, dict):
                views.append((f"view/{i}", v.get("name") or f"Vista {i + 1}", v))
        coll = self.tag_collection(root, "Cámaras") if views else None
        first = None
        for key, name, v in views:
            obj = self.take(key, None)
            if obj is None:
                cam = bpy.data.cameras.new(_short(name))
                obj = bpy.data.objects.new(_short(name), cam)
                obj[DOC_PROP] = self.doc_path
                obj[KEY_PROP] = key
                coll.objects.link(obj)
            else:
                # A view renamed (or deleted and made again) in IngeTrazo
                # keeps its slot: the camera must take the new name too.
                obj.name = _short(name)
                obj.data.name = _short(name)
            self._aim(obj, v)
            first = first or obj
        # The author's camera, else the first saved view, frames the render
        # — unless the scene already has a camera of its own.
        if first is not None and self.context.scene.camera is None:
            self.context.scene.camera = first

    def _aim(self, obj, v: dict) -> None:
        cam = obj.data
        fov = float(v.get("fov_deg", v.get("fov", 45.0)))
        parallel = bool(v.get("parallel")) or v.get("perspective") is False
        target = Vector(v.get("target") or (0.0, 0.0, 0.0))
        eye = Vector(igz.camera_eye(v))
        f = (target - eye)
        dist = f.length or 1.0
        f.normalize()
        yaw = float(v.get("yaw", -math.pi / 4))
        cam.sensor_fit = "VERTICAL"
        cam.angle_y = math.radians(fov)
        cam.clip_end = max(1000.0, dist * 50.0)
        cam.shift_y = 0.0
        if parallel:
            cam.type = "ORTHO"
            cam.ortho_scale = 2.0 * dist * math.tan(math.radians(fov) / 2.0)
        else:
            cam.type = "PERSP"
        two_point = bool(v.get("two_point")) and not parallel
        level = Vector((f.x, f.y, 0.0))
        if two_point and level.length > 0.05:
            # Two-point perspective: a level camera, the target brought back
            # to the centre with lens shift (verticals stay vertical).
            level.normalize()
            slope = math.atan2(f.z, math.hypot(f.x, f.y))
            r = self.context.scene.render
            aspect = (r.resolution_x * r.pixel_aspect_x) / max(
                1, r.resolution_y * r.pixel_aspect_y)
            cam.shift_y = math.tan(slope) / (2.0 * math.tan(math.radians(fov) / 2.0)) \
                / max(1.0, aspect)
            f = level
        up = Vector((0.0, 0.0, 1.0))
        if abs(f.dot(up)) > 0.9999:
            # Top/bottom: north up the screen, like IngeTrazo's exact views.
            sgn = -1.0 if f.z < 0 else 1.0
            up = Vector((-math.cos(yaw) * sgn, -math.sin(yaw) * sgn, 0.0))
        right = f.cross(up)
        right.normalize()
        true_up = right.cross(f)
        rot = Matrix((right, true_up, -f)).transposed().to_4x4()
        obj.matrix_world = Matrix.Translation(eye) @ rot

    # ---- The whole document ------------------------------------------------------
    def build(self):
        s = self.scene_json
        root = self.root_collection()
        self.old = self.existing_objects()
        us = self.context.scene.unit_settings
        us.system = "METRIC"
        us.scale_length = 1.0
        loose = {"faces": s.get("faces", []), "edges": s.get("edges", [])}
        if loose["faces"] or loose["edges"]:
            me = self.mesh(loose, None, self.doc.path.stem)
            self.place("loose", self.doc.path.stem, me, root)
        for i, g, nk in self._named(s.get("groups", []) or [], "g"):
            self.group(g, self.key_for(g, f"g/{i}"), root, name_key=nk)
        self.cameras(root)
        # Face-me figures turn to the scene camera about their vertical axis.
        cam = self.context.scene.camera
        for obj in self.facing:
            con = obj.constraints.get("IngeTrazo face-me") or \
                obj.constraints.new("LOCKED_TRACK")
            con.name = "IngeTrazo face-me"
            con.track_axis = "TRACK_NEGATIVE_Y"
            con.lock_axis = "LOCK_Z"
            if con.target is None:
                con.target = cam
        # Tags: hidden in IngeTrazo → hidden here too.
        for ly in s.get("layers", []) or []:
            if isinstance(ly, dict) and ly.get("visible") is False:
                for coll in root.children:
                    if coll.get("ingetrazo_tag") == ly.get("name"):
                        coll.hide_viewport = True
                        coll.hide_render = True
        # What the document no longer has goes away (only what we created).
        for obj in self.old.values():
            data = obj.data
            bpy.data.objects.remove(obj)
            if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
        for me in [m for m in bpy.data.meshes
                   if m.get(DOC_PROP) == self.doc_path and m.users == 0]:
            bpy.data.meshes.remove(me)
        for coll in [c for c in root.children if not c.objects and not c.children]:
            bpy.data.collections.remove(coll)
        geo = (s.get("georef") or {}).get("datum")
        if isinstance(geo, dict):
            for k in ("lat", "lon", "alt"):
                if k in geo:
                    root[f"ingetrazo_{k}"] = geo[k]
        root[MTIME_PROP] = os.path.getmtime(self.doc.path)
        return root


def import_igz(context, path, update_materials: bool = False) -> dict:
    doc = igz.Document(path)
    try:
        b = Builder(context, doc, update_materials)
        b.build()
        return b.stats
    finally:
        doc.close()
