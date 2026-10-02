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


# ---- Finishes and lights (IngeTrazo 0.5.6+) ---------------------------------

_fspec = importlib.util.spec_from_file_location(
    "ingetrazo_blender_finish",
    Path(__file__).resolve().parents[1] / "ingetrazo_io" / "finish.py")
fin = importlib.util.module_from_spec(_fspec)
_fspec.loader.exec_module(fin)


def test_finish_guesses_match_ingetrazo():
    from core import finish as ref
    assert fin.FINISHES == ref.FINISHES
    names = ["water_calm", "[Water Pool Light]", "Metal_10_1K", "Marble_07",
             "Mar", "Marble", "vidrio pulido", "Madera roble", "acero inox",
             "Laca blanca", "aço", "Aço escovado", "pileta", "Ladrillo",
             None, "", "Cristal templado", "Leather-brown", "porcelanato"]
    for n in names:
        for pic in (None, "Wood_04.jpg", "glass-pane.png", "concrete.png"):
            for op in (None, 0.5, 1.0):
                assert fin.guess(n, pic, op) == ref.guess(n, pic, op), (n, pic, op)
                for chosen in (None, "auto", "metal", "nonsense"):
                    assert fin.resolve(chosen, n, pic, op) == \
                        ref.resolve(chosen, n, pic, op)


def test_lights_are_cleaned_like_ingetrazo():
    from core import render_blender as ref
    raw = [
        {"kind": "point", "pos": [1, 2, 3]},
        {"kind": "spot", "pos": [0, 0, 4], "dir": [0, 0, 0], "kelvin": 99999,
         "power": -5, "angle": 400, "on": False, "name": "Poste"},
        {"kind": "spot", "pos": [0, 0, 4], "color": "cool"},
        {"kind": "point", "pos": [0, 0, 1], "color": [2, 0.5, -1]},
        {"kind": "area", "pos": [0, 0, 0]},
        {"kind": "point", "pos": ["x", 0, 0]},
        {"kind": "point"},
        "nonsense",
        {"kind": "point", "pos": [float("nan"), 0, 0]},
    ]
    ours = fin.lights({"plugin_data": {"render_blender": {"lights": raw}}})
    theirs = ref.clean_lights(raw)
    assert len(ours) == len(theirs) == 4
    for a, b in zip(ours, theirs):
        for k in ("kind", "pos", "dir", "power", "angle", "on", "name"):
            assert a[k] == b[k], k
        assert all(abs(x - y) < 1e-4 for x, y in zip(a["color"], b["color"]))
    for k in (1800, 2700, 4000, 6500, 10000):
        assert fin.kelvin_to_rgb(k) == ref.kelvin_to_rgb(k)


def test_no_lights_when_the_document_has_none():
    assert fin.lights({}) == []
    assert fin.lights({"plugin_data": {"render_blender": "x"}}) == []


# ---- UVs back to IngeTrazo ---------------------------------------------------

def _rand_face(rnd):
    """A random planar polygon in 3D: a convex 2D loop placed on a random
    plane."""
    from math import cos, sin, tau
    n = rnd.randint(3, 8)
    angs = sorted(rnd.uniform(0, tau) for _ in range(n))
    loop = [(rnd.uniform(0.5, 3) * cos(a), rnd.uniform(0.5, 3) * sin(a)) for a in angs]
    nrm = igz.normalize((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)))
    e1 = igz.normalize(igz.cross(nrm, (0.3, 0.5, 0.8)))
    e2 = igz.cross(nrm, e1)
    o = (rnd.uniform(-9, 9), rnd.uniform(-9, 9), rnd.uniform(-9, 9))
    return [tuple(o[k] + x * e1[k] + y * e2[k] for k in range(3)) for x, y in loop]


def test_a_fitted_map_reproduces_affine_uvs_and_matches_ingetrazo():
    from core.texture import affine_uv, fit_uv_affine
    rnd = random.Random(11)
    for _ in range(200):
        pts = _rand_face(rnd)
        # Any affine UV edit: a random 2D affine map of the face's own UVs.
        base = igz.face_uvs({"sw": 1.3, "sh": 0.7}, igz.polygon_normal(pts), pts)
        a, b, c, d = (rnd.uniform(-2, 2) for _ in range(4))
        tu, tv = rnd.uniform(-5, 5), rnd.uniform(-5, 5)
        uvs = [(a * u + b * v + tu, c * u + d * v + tv) for u, v in base]
        if abs(a * d - b * c) < 1e-3:
            continue
        uvw, err = igz.fit_uvw(pts, uvs)
        assert err < 1e-7
        # IngeTrazo draws it exactly there…
        for (u, v), (ru, rv) in zip(uvs, affine_uv(uvw, pts)):
            assert abs(u - ru) < 1e-7 and abs(v - rv) < 1e-7
        # …and its own fit of the same UVs is the same map on the face
        # (IngeTrazo's runs through QVector3D, single precision: compare
        # relative to the size of the values).
        ref = fit_uv_affine([QVector3D(*p) for p in pts], uvs)
        for (u, v), (ru, rv) in zip(affine_uv(ref, pts), affine_uv(uvw, pts)):
            assert abs(u - ru) <= 1e-5 * max(1.0, abs(u))
            assert abs(v - rv) <= 1e-5 * max(1.0, abs(v))


def test_a_non_affine_edit_is_fitted_and_its_error_reported():
    rnd = random.Random(5)
    pts = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
    uvs = [(0, 0), (1, 0), (1.4, 1.2), (0, 1)]       # one corner dragged
    uvw, err = igz.fit_uvw(pts, uvs)
    assert uvw is not None and 0.05 < err < 1.0
    assert abs(uvw[2]) < 1e-12 and abs(uvw[6]) < 1e-12   # no normal component


def test_a_degenerate_face_has_no_map():
    assert igz.fit_uvw([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 0)] * 3) == (None, None)
