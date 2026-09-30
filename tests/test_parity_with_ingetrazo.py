"""The add-on's .igz reader (ingetrazo_io/igz.py) is pure Python and
re-implements a few of IngeTrazo's rules (texture projection, the orbit
camera). These tests pin them to IngeTrazo's own code so the two cannot
drift apart. They need an IngeTrazo checkout and its Python environment:

    INGETRAZO_SRC=~/src/ingetrazo  <ingetrazo venv>/bin/python -m pytest tests

and are skipped otherwise."""
from __future__ import annotations

import importlib.util
import math
import os
import random
import sys
from pathlib import Path

import pytest

_SRC = os.environ.get("INGETRAZO_SRC")
if not _SRC or not (Path(_SRC) / "core" / "texture.py").is_file():
    pytest.skip("set INGETRAZO_SRC to an IngeTrazo checkout", allow_module_level=True)
sys.path.insert(0, _SRC)

from PySide6.QtGui import QVector3D  # noqa: E402

from core.camera import OrbitCamera  # noqa: E402
from core.texture import affine_uv, planar_uv  # noqa: E402

_EXAMPLES = Path(_SRC) / "examples"
_spec = importlib.util.spec_from_file_location(
    "ingetrazo_blender_igz", Path(__file__).resolve().parents[1] / "ingetrazo_io" / "igz.py")
igz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(igz)


def _normals():
    rnd = random.Random(7)
    out = [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
           (1e-4, 0, 1), (0, 5e-4, -1)]
    for _ in range(40):
        out.append((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)))
    return out


def test_planar_uvs_match_ingetrazo():
    pts = [(1.0, 2.0, 3.0), (-4.0, 0.5, 2.0), (0.3, -1.0, 7.0)]
    for n in _normals():
        for tex in ({"sw": 1.0, "sh": 1.0}, {"sw": 0.6, "sh": 2.5, "rot": 33.0}):
            ours = igz.face_uvs(tex, n, pts)
            ref = planar_uv(QVector3D(*n), [QVector3D(*p) for p in pts],
                            tex["sw"], tex["sh"], tex.get("rot", 0.0))
            for (u, v), (ru, rv) in zip(ours, ref):
                assert abs(u - ru) < 1e-5 and abs(v - rv) < 1e-5, (n, tex)


def test_affine_uvs_match_ingetrazo():
    uvw = [0.5, -0.2, 0.1, 3.0, 0.0, 0.7, -0.3, -1.0]
    pts = [(1.0, 2.0, 3.0), (-4.0, 0.5, 2.0)]
    ours = igz.face_uvs({"uvw": uvw}, (0, 0, 1), pts)
    ref = affine_uv(uvw, pts)
    assert all(abs(a - b) < 1e-9 for p, q in zip(ours, ref) for a, b in zip(p, q))


def test_camera_eye_matches_the_orbit_camera():
    cam = OrbitCamera()
    cam.target = QVector3D(1.0, -2.0, 0.5)
    cam.distance, cam.yaw, cam.pitch = 12.0, 0.7, 0.4
    eye = igz.camera_eye({"target": [1.0, -2.0, 0.5], "distance": 12.0,
                          "yaw": 0.7, "pitch": 0.4})
    ref = cam.eye()
    assert math.dist(eye, (ref.x(), ref.y(), ref.z())) < 1e-5


def test_reads_every_example_document():
    for path in sorted(_EXAMPLES.glob("*.igz")):
        doc = igz.Document(path)
        try:
            assert doc.scene.get("groups")
            # Every embedded texture a face names can be read back.
            def walk(node):
                if isinstance(node, dict):
                    tex = node.get("texture")
                    if isinstance(tex, dict) and tex.get("embed"):
                        assert doc.image_bytes(tex) is not None, tex
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for v in node:
                        walk(v)
            walk(doc.scene)
        finally:
            doc.close()


def test_container_paint_goes_to_unpainted_faces_only():
    container = {"color": [0.1, 0.2, 0.3]}
    assert igz.effective_attrs({}, container)["color"] == [0.1, 0.2, 0.3]
    own = {"color": [1, 0, 0]}
    assert igz.effective_attrs(own, container) is own
