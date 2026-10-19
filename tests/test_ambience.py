"""The ambience (day sun, night, overcast) is ported from IngeTrazo's
core/sun.py and core/render_blender.py: these tests pin the port to
IngeTrazo's own code, through documents IngeTrazo saves. Needs an
IngeTrazo checkout and its Python environment (see README)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import math
import os
import random
import sys
from pathlib import Path

import pytest

_SRC = os.environ.get("INGETRAZO_SRC")
if not _SRC or not (Path(_SRC) / "core" / "sun.py").is_file():
    pytest.skip("set INGETRAZO_SRC to an IngeTrazo checkout", allow_module_level=True)
sys.path.insert(0, _SRC)

from core import render_blender as rb  # noqa: E402
from core import sun as ref_sun  # noqa: E402
from core.scene import Scene  # noqa: E402
from formats import igz as it_igz  # noqa: E402
from georef.datum import SceneDatum  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"ingetrazo_blender_{name}",
        Path(__file__).resolve().parents[1] / "ingetrazo_io" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


amb = _load("ambience")
igz = _load("igz")


def _saved(tmp_path, scene, name="doc.igz") -> dict:
    """The scene as the add-on reads it from a document IngeTrazo saved."""
    path = tmp_path / name
    it_igz.save_scene(scene, path)
    doc = igz.Document(path)
    try:
        return doc.scene
    finally:
        doc.close()


def test_constants_match():
    assert amb.AMBIENCES == rb.AMBIENCES
    assert amb.EXPOSURE == rb.EXPOSURE
    assert (amb.DEFAULT_LAT, amb.DEFAULT_LON) == (ref_sun.DEFAULT_LAT, ref_sun.DEFAULT_LON)


def test_solar_position_matches():
    rnd = random.Random(3)
    for _ in range(400):
        lat, lon = rnd.uniform(-89, 89), rnd.uniform(-180, 180)
        when = dt.datetime(rnd.randint(1950, 2090), rnd.randint(1, 12), rnd.randint(1, 28),
                           rnd.randint(0, 23), rnd.randint(0, 59), rnd.randint(0, 59))
        assert amb.solar_position(lat, lon, when) == ref_sun.solar_position(lat, lon, when)


def test_the_sun_falls_as_in_ingetrazo(tmp_path):
    """Site, north, date, time and zone from saved documents: the same sun
    direction IngeTrazo sends to Blender, or none at night in both."""
    rnd = random.Random(11)
    year = dt.date.today().year
    nights = days = 0
    for i in range(60):
        scene = Scene()
        if i % 4:
            scene.georef = SceneDatum(rnd.uniform(-60, 60), rnd.uniform(-180, 180),
                                      north=rnd.choice([0.0, rnd.uniform(-180, 180)]))
        scene.shadows = ref_sun.ShadowSettings(
            month=rnd.randint(1, 12), day=rnd.randint(1, 30), hour=rnd.randint(0, 23),
            minute=rnd.randint(0, 59),
            utc_offset=rnd.choice([None, rnd.randint(-12, 12)]))
        ours = amb.sun_toward(_saved(tmp_path, scene), year)
        theirs = rb.sun_toward(scene)
        if theirs is None:
            assert ours is None
            nights += 1
            continue
        days += 1
        assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(ours, theirs)), i
    assert nights and days


def test_defaults_without_render_or_shadow_data(tmp_path):
    got = amb.ambience(_saved(tmp_path, Scene()))
    assert got["ambience"] == "day" and got["exposure"] == rb.EXPOSURE["day"]
    assert got["sun"] is not None and math.isclose(got["sun_strength"], 3.0)
    assert all(math.isclose(a, b, abs_tol=1e-9)
               for a, b in zip(got["sun"], rb.sun_toward(Scene())))


@pytest.mark.parametrize("raw, kind, strength", [
    ({"ambience": "night"}, "night", 3.0),
    ({"ambience": "overcast", "sun_scale": 1.5}, "overcast", 4.5),
    ({"ambience": "day", "sun_scale": 9}, "day", 6.0),
    ({"ambience": "sunset", "sun_scale": "x"}, "day", 3.0),
    ({"ambience": "day", "sun_scale": 0.0}, "day", 0.3),
])
def test_render_panel_settings(raw, kind, strength):
    got = amb.ambience({"plugin_data": {"render_blender": raw}})
    assert got["ambience"] == kind
    assert got["exposure"] == rb.EXPOSURE[kind]
    assert math.isclose(got["sun_strength"], strength)
    assert (got["sun"] is None) == (kind != "day")


def test_a_broken_document_still_reads():
    for scene in ({"shadows": "x", "georef": {"datum": {"lat": "a"}}},
                  {"shadows": {"month": 4, "day": 31}},
                  {"shadows": {"utc_offset": 99}},
                  {"plugin_data": []}):
        amb.ambience(scene)
