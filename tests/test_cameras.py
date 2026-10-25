"""Cameras sent from Blender become scenes (saved views) of the document.
IngeTrazo must read them as written, and keep them as they are when it
saves the document again: a camera sent from Blender only follows its
scene on reload when IngeTrazo changed it, so a plain save there must
leave the scene's camera exactly as sent. Needs an IngeTrazo checkout
and its Python environment:

    INGETRAZO_SRC=~/src/ingetrazo  <ingetrazo venv>/bin/python -m pytest tests
"""
from __future__ import annotations

import importlib.util
import math
import os
import shutil
import sys
from pathlib import Path

import pytest

_SRC = os.environ.get("INGETRAZO_SRC")
if not _SRC or not (Path(_SRC) / "core" / "saved_views.py").is_file():
    pytest.skip("set INGETRAZO_SRC to an IngeTrazo checkout",
                allow_module_level=True)
sys.path.insert(0, _SRC)

from core.scene import Scene  # noqa: E402
from formats import igz as it_igz  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ingetrazo_blender_igz",
    Path(__file__).resolve().parents[1] / "ingetrazo_io" / "igz.py")
igz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(igz)

EXAMPLES = sorted((Path(_SRC) / "examples").glob("*.igz"))

STREET = {"target": (3.5, -2.25, 1.6), "distance": 12.75, "yaw": -2.1,
          "pitch": 0.08, "fov": 39.6, "parallel": False, "two_point": True}
PLAN = {"target": (0.0, 0.0, 0.0), "distance": 30.0, "yaw": math.pi / 2,
        "pitch": math.radians(89.0), "fov": 45.0, "parallel": True,
        "two_point": False}


@pytest.fixture(params=EXAMPLES, ids=[p.name for p in EXAMPLES])
def doc(request, tmp_path):
    path = tmp_path / request.param.name
    shutil.copy(request.param, path)
    return path


def _send(path, cams):
    """What writeback.send_cameras does to the document, without Blender:
    each camera to the scene of that name, else a new scene."""
    d = igz.Document(path)
    try:
        scene = d.scene
    finally:
        d.close()
    views = scene.setdefault("saved_views", [])
    for name, cam in cams:
        view = next((v for v in views if v.get("name") == name), None)
        if view is None:
            view = {"name": name}
            views.append(view)
        igz.set_view_camera(view, cam)
    igz.write_document(path, scene)
    return views


def test_new_scenes_read_by_ingetrazo(doc):
    _send(doc, [("Street view", STREET), ("Plan", PLAN)])
    scene = Scene()
    it_igz.load_into(scene, doc)
    got = {v.name: v for v in scene.saved_views}
    for name, cam in (("Street view", STREET), ("Plan", PLAN)):
        v = got[name]
        assert v.target == pytest.approx(cam["target"])
        assert v.distance == pytest.approx(cam["distance"])
        assert v.yaw == pytest.approx(cam["yaw"])
        assert v.pitch == pytest.approx(cam["pitch"])
        assert v.fov_deg == pytest.approx(cam["fov"])
        assert v.perspective is not cam["parallel"]
        assert v.two_point is cam["two_point"]
        # A new scene leaves the rest alone when recalled.
        assert v.section is None and v.hidden_objects is None


def test_updated_scene_keeps_the_rest(doc):
    views = _send(doc, [("Kept", STREET)])
    views[-1].update({"hidden_layers": ["Trees"], "section": {"active": None}})
    d = igz.Document(doc)
    scene = d.scene
    d.close()
    scene["saved_views"] = views
    igz.write_document(doc, scene)
    _send(doc, [("Kept", PLAN)])
    it = Scene()
    it_igz.load_into(it, doc)
    v = next(v for v in it.saved_views if v.name == "Kept")
    assert v.hidden_layers == ["Trees"] and v.section == {"active": None}
    assert not v.perspective and v.pitch == pytest.approx(PLAN["pitch"])
    assert [x.name for x in it.saved_views].count("Kept") == 1


def test_a_save_in_ingetrazo_keeps_the_camera_as_sent(doc):
    views = _send(doc, [("Street view", STREET), ("Plan", PLAN)])
    sent = {v["name"]: igz.view_sig(v) for v in views}
    scene = Scene()
    it_igz.load_into(scene, doc)
    it_igz.save_scene(scene, doc)
    d = igz.Document(doc)
    try:
        again = {v["name"]: igz.view_sig(v) for v in d.scene["saved_views"]}
    finally:
        d.close()
    assert again["Street view"] == sent["Street view"]
    assert again["Plan"] == sent["Plan"]
