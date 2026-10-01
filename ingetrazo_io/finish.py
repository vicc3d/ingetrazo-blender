# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""What IngeTrazo 0.5.6+ says about light: material finishes and the lights
of its Render panel.

Pure Python (no ``bpy``), so it can be checked against IngeTrazo itself:
the rules, keywords and numbers are IngeTrazo's own (``core/finish.py``,
``core/render_blender.py`` and ``resources/blender/render_scene.py``, by
Marco Sumari Tellez and IngeTrazo contributors, GPL-3), so a model looks
the same here as in IngeTrazo's «Render with Blender».

- A finish is per named material (``"finish"`` in the document's material
  registry); where nobody chose one it is guessed from the material's name
  and its picture's file name, and a translucent paint reads as glass.
- Lights live in the document's extension data, under ``render_blender``.
"""
from __future__ import annotations

import math
import re

FINISHES = ("matte", "satin", "gloss", "metal", "glass", "water")

# Keywords, Spanish / English / Portuguese, matched as whole words or word
# starts. Order matters: water and glass before gloss, metal before gloss.
_WORDS = (
    ("water", r"agua|water|pool|piscina|pileta|espejo[_ ]de[_ ]agua|"
              r"(?:lago|lake|mar|sea)(?![a-záéíóúñç])|ocean|oc[eé]ano|[aá]gua"),
    ("glass", r"vidri|glass|cristal|crystal|vitral|window[_ ]?pane|"
              r"mampara|vidro"),
    ("metal", r"metal|acero|steel|a[cç]o(?![a-záéíóúñç])|alumin|hierro|"
              r"iron|ferro|chrom|"
              r"cromo|inox|bronce|bronze|cobre|copper|lat[oó]n|brass|"
              r"galvaniz|zinc|zinco|titani"),
    ("gloss", r"m[aá]rmol|marble|m[aá]rmore|granit|porcel|cer[aá]mic|"
              r"ceramic|azulej|tile|lacad|lacquer|laca|pulid|polish|"
              r"brillant|glossy|pl[aá]stic|acr[ií]lic|esmalt|enamel|"
              r"lacquered|gleam"),
    ("satin", r"madera|wood|madeira|parquet|laminad|laminate|"
              r"cuero|leather|couro|satin|satinad|semi"),
)
_RX = [(f, re.compile(r"(?:^|[^a-záéíóúñç])(?:" + pat + r")", re.IGNORECASE))
       for f, pat in _WORDS]


def guess(name, picture=None, opacity=None) -> str:
    """The finish a material most likely has, from what it is called."""
    for text in (name, picture):
        if not text:
            continue
        low = str(text).replace("-", "_")
        for finish, rx in _RX:
            if rx.search(low):
                return finish
    if opacity is not None and float(opacity) < 0.999:
        return "glass"
    return "matte"


def resolve(chosen, name, picture=None, opacity=None) -> str:
    """The user's choice, or the guess when it is ``auto``/absent/unknown."""
    if chosen in FINISHES:
        return chosen
    return guess(name, picture, opacity)


# ---- Lights ---------------------------------------------------------------------

LIGHT_KINDS = ("point", "spot")
MIN_KELVIN, MAX_KELVIN = 1800, 10000
NAMED_KELVIN = {"warm": 2700, "neutral": 4000, "cool": 6500}
DEFAULT_KELVIN = 2700
DEFAULT_POWER = {"point": 400.0, "spot": 1500.0}


def kelvin_to_rgb(kelvin) -> tuple:
    """A light's colour at ``kelvin`` (black-body), brightest channel = 1."""
    t = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    rgb = [max(0.0, min(255.0, c)) / 255.0 for c in (r, g, b)]
    top = max(rgb) or 1.0
    return tuple(round(c / top, 4) for c in rgb)


def lights(scene_json: dict) -> list:
    """The document's lights, validated as IngeTrazo does: whatever a
    hand-edited or older file holds, only well-formed entries come out.
    Each: ``kind, pos, dir, color, power, angle, on, name``."""
    pdata = scene_json.get("plugin_data")
    raw = pdata.get("render_blender") if isinstance(pdata, dict) else None
    raw = raw.get("lights") if isinstance(raw, dict) else None
    out = []
    for lt in raw if isinstance(raw, list) else []:
        try:
            kind = lt.get("kind", "point")
            if kind not in LIGHT_KINDS:
                continue
            pos = [float(v) for v in lt["pos"]][:3]
            d = [float(v) for v in lt.get("dir", (0.0, 0.0, -1.0))][:3]
            power = float(lt.get("power", DEFAULT_POWER[kind]))
            angle = float(lt.get("angle", 60.0))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if len(pos) != 3 or len(d) != 3 \
                or not all(math.isfinite(v) for v in pos + d):
            continue
        if math.hypot(*d) < 1e-9:
            d = [0.0, 0.0, -1.0]
        kelvin, color, rgb = lt.get("kelvin"), lt.get("color"), None
        try:
            if kelvin is not None:
                kelvin = max(MIN_KELVIN, min(MAX_KELVIN, float(kelvin)))
            elif isinstance(color, str) or color is None:
                kelvin = NAMED_KELVIN.get(color or "warm", DEFAULT_KELVIN)
            else:
                rgb = tuple(max(0.0, min(1.0, float(c))) for c in color[:3])
                if len(rgb) != 3:
                    rgb, kelvin = None, DEFAULT_KELVIN
        except (TypeError, ValueError):
            rgb, kelvin = None, DEFAULT_KELVIN
        out.append({
            "kind": kind, "pos": pos, "dir": d,
            "color": list(rgb if rgb is not None else kelvin_to_rgb(kelvin)),
            "power": max(0.0, min(power, 1e6)),
            "angle": max(1.0, min(angle, 179.0)),
            "on": bool(lt.get("on", True)),
            "name": str(lt.get("name", "")),
        })
    return out
