# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
# Texture projection and orbit-camera maths ported from IngeTrazo,
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Reading an IngeTrazo ``.igz`` document without IngeTrazo.

Pure Python (no ``bpy``, no Qt), so it can be tested on its own. A document
is either plain JSON or a ZIP holding ``document.json`` plus the texture
images under ``textures/`` (see ``formats/igz.py`` in IngeTrazo).

Also here: IngeTrazo's texture projection, so every face gets exactly the
UVs IngeTrazo draws it with (SketchUp's planar basis, tile size, rotation,
or the fitted world→UV map of an imported face).
"""
from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

_ZIP_MAGIC = b"PK\x03\x04"
_DOC_ENTRY = "document.json"
#: IngeTrazo's colour for faces nobody painted (the viewport default).
DEFAULT_COLOR = (0.96, 0.95, 0.925)
#: Below this ``|Z × n|`` a face counts as horizontal (SketchUp's measured
#: tolerance, ``core/texture.py``).
_VERTICAL_TOLERANCE = 1e-3


class Document:
    """A parsed ``.igz``: the ``scene`` payload and the embedded images."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        raw = self.path.read_bytes()
        self._archive = None
        if raw.startswith(_ZIP_MAGIC):
            self._archive = zipfile.ZipFile(self.path)
            try:
                data = json.loads(self._archive.read(_DOC_ENTRY).decode("utf-8"))
            except KeyError:
                raise ValueError(f"{self.path.name} is not an IngeTrazo document")
        else:
            data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or "scene" not in data:
            raise ValueError(f"{self.path.name} is not an IngeTrazo document")
        self.format = data.get("igz_format", 1)
        self.app_version = data.get("app_version", "")
        self.scene = data["scene"]

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
            self._archive = None

    def image_bytes(self, tex: dict) -> tuple[str, bytes] | None:
        """``(file name, bytes)`` of a face texture: the embedded member of
        a container, or the file a plain document points at. ``None`` when
        neither can be read."""
        member = tex.get("embed")
        if member and self._archive is not None:
            try:
                return member.rsplit("/", 1)[-1], self._archive.read(member)
            except KeyError:
                pass
        path = tex.get("path")
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = self.path.parent / p
            try:
                return p.name, p.read_bytes()
            except OSError:
                pass
        return None


# ---- Geometry helpers ---------------------------------------------------------

def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def normalize(v):
    ln = math.sqrt(dot(v, v))
    return (v[0] / ln, v[1] / ln, v[2] / ln) if ln > 1e-30 else (0.0, 0.0, 1.0)


def polygon_normal(pts):
    """Newell's normal of a polygon (robust for concave loops)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return normalize((nx, ny, nz))


def matrix_from_column_major(vals):
    """A 4×4 row list from IngeTrazo's column-major ``QMatrix4x4.data()``."""
    return [[float(vals[col * 4 + row]) for col in range(4)] for row in range(4)]


# ---- Texture projection (IngeTrazo core/texture.py) ------------------------

def projection_basis(normal):
    nx, ny, nz = normalize(normal)
    xx, xy = -ny, nx                      # Z × n
    lx = math.sqrt(xx * xx + xy * xy)
    if lx < _VERTICAL_TOLERANCE:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)) if nz > 0 \
            else ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    xx, xy = xx / lx, xy / lx
    return (xx, xy, 0.0), (-nz * xy, nz * xx, nx * xy - ny * xx)


def face_uvs(tex: dict, normal, pts):
    """The UV of each point, exactly as IngeTrazo maps ``tex`` on a face."""
    uvw = tex.get("uvw")
    if uvw and len(uvw) == 8:
        return [(uvw[0] * p[0] + uvw[1] * p[1] + uvw[2] * p[2] + uvw[3],
                 uvw[4] * p[0] + uvw[5] * p[1] + uvw[6] * p[2] + uvw[7])
                for p in pts]
    u_axis, v_axis = projection_basis(normal)
    rot = float(tex.get("rot", 0.0) or 0.0)
    if rot:
        a = math.radians(rot)
        c, s = math.cos(a), math.sin(a)
        u_axis, v_axis = (tuple(u_axis[i] * c + v_axis[i] * s for i in range(3)),
                          tuple(v_axis[i] * c - u_axis[i] * s for i in range(3)))
    sw = float(tex.get("sw", 1.0) or 1.0)
    sh = float(tex.get("sh", 1.0) or 1.0)
    sw = sw if abs(sw) > 1e-9 else 1.0
    sh = sh if abs(sh) > 1e-9 else 1.0
    return [(dot(p, u_axis) / sw, dot(p, v_axis) / sh) for p in pts]


def effective_attrs(face: dict, container: dict | None) -> dict:
    """What a face is drawn with inside a painted group (SketchUp's rule:
    the face's own paint wins; unpainted faces wear the container's)."""
    if not container or face.get("texture") is not None \
            or face.get("color") is not None:
        return face
    merged = dict(face)
    for key in ("color", "texture", "opacity", "mat"):
        if container.get(key) is not None:
            merged[key] = container[key]
    return merged


# ---- Cameras ----------------------------------------------------------------

def camera_eye(view: dict):
    """Eye point of an IngeTrazo orbit camera (``core/camera.py``)."""
    t = view.get("target") or (0.0, 0.0, 0.0)
    d = float(view.get("distance", 20.0))
    yaw = float(view.get("yaw", -math.pi / 4))
    pitch = float(view.get("pitch", math.pi / 6))
    cp, sp = math.cos(pitch), math.sin(pitch)
    return (t[0] + d * cp * math.cos(yaw), t[1] + d * cp * math.sin(yaw),
            t[2] + d * sp)
