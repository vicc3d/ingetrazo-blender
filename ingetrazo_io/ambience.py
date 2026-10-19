# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
# Solar position and ambience rules ported from IngeTrazo (core/sun.py,
# core/render_blender.py), Copyright (C) 2026 Marco Sumari Tellez and
# IngeTrazo contributors.
"""The ambience of IngeTrazo's Render panel: day, night or overcast.

Pure Python (no ``bpy``), so it can be checked against IngeTrazo's own code.
By day the sun stands where IngeTrazo's Shadows panel puts it: the model's
site (its georeference, or IngeTrazo's default site), the panel's date and
local time, and the site's north, so the shadows in Blender fall as they do
in IngeTrazo. Night is a dark sky, a faint moon and the Render panel's
lights; overcast is a bright even sky with no sun.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

AMBIENCES = ("day", "night", "overcast")
#: Exposure per ambience (IngeTrazo's ``EXPOSURE``): a daylit scene washes
#: out under AgX at 0, a night lit by lamps does not.
EXPOSURE = {"day": -0.6, "night": 0.0, "overcast": -0.3}
#: IngeTrazo's site when the model has no georeference: Arequipa.
DEFAULT_LAT = -16.409
DEFAULT_LON = -71.537
#: The Shadows panel's defaults (``ShadowSettings``).
_DEFAULT_WHEN = {"month": 3, "day": 21, "hour": 12, "minute": 0}


def solar_position(lat: float, lon: float, when_utc: datetime) -> tuple:
    """``(elevation_deg, azimuth_deg)`` of the sun, NOAA equations, as
    IngeTrazo computes it (``core.sun.solar_position``). Azimuth clockwise
    from north."""
    y, m = when_utc.year, when_utc.month
    d = (when_utc.day + when_utc.hour / 24.0 + when_utc.minute / 1440.0
         + when_utc.second / 86400.0)
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    jd = (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
          + d + b - 1524.5)
    t = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360.0
    m_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m_anom)
    center = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
              + math.sin(3 * mr) * 0.000289)
    true_long = l0 + center
    omega = 125.04 - 1934.136 * t
    app_long = (true_long - 0.00569
                - 0.00478 * math.sin(math.radians(omega)))

    obliq0 = (23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059
              - t * 0.001813))) / 60.0) / 60.0)
    obliq = obliq0 + 0.00256 * math.cos(math.radians(omega))

    decl = math.degrees(math.asin(
        math.sin(math.radians(obliq)) * math.sin(math.radians(app_long))))
    var_y = math.tan(math.radians(obliq / 2.0)) ** 2
    l0r = math.radians(l0)
    eot = 4.0 * math.degrees(
        var_y * math.sin(2 * l0r)
        - 2.0 * ecc * math.sin(mr)
        + 4.0 * ecc * var_y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * var_y * var_y * math.sin(4 * l0r)
        - 1.25 * ecc * ecc * math.sin(2 * mr))

    minutes = (when_utc.hour * 60.0 + when_utc.minute
               + when_utc.second / 60.0)
    tst = (minutes + eot + 4.0 * lon) % 1440.0
    ha = tst / 4.0 - 180.0
    if ha < -180.0:
        ha += 360.0

    latr = math.radians(lat)
    declr = math.radians(decl)
    har = math.radians(ha)
    cos_zen = (math.sin(latr) * math.sin(declr)
               + math.cos(latr) * math.cos(declr) * math.cos(har))
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zenith = math.degrees(math.acos(cos_zen))
    elevation = 90.0 - zenith

    denom = math.cos(latr) * math.sin(math.radians(zenith))
    if abs(denom) < 1e-12:
        azimuth = 180.0
    else:
        cos_az = ((math.sin(latr) * cos_zen - math.sin(declr)) / denom)
        cos_az = max(-1.0, min(1.0, cos_az))
        az = math.degrees(math.acos(cos_az))
        azimuth = (az + 180.0) % 360.0 if ha > 0 else (540.0 - az) % 360.0
    return elevation, azimuth


def _int(raw: dict, key: str, lo: int, hi: int, default: int) -> int:
    try:
        v = int(raw.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def _site(scene_json: dict) -> tuple:
    """``(lat, lon, north_deg)`` of the model: its georeference, or
    IngeTrazo's default site with north up."""
    geo = scene_json.get("georef")
    datum = geo.get("datum") if isinstance(geo, dict) else None
    if isinstance(datum, dict):
        try:
            lat, lon = float(datum["lat"]), float(datum["lon"])
            north = float(datum.get("north", 0.0) or 0.0)
            if all(math.isfinite(v) for v in (lat, lon, north)):
                return lat, lon, north
        except (KeyError, TypeError, ValueError):
            pass
    return DEFAULT_LAT, DEFAULT_LON, 0.0


def sun_toward(scene_json: dict, year: int | None = None):
    """Unit vector from the model toward the sun, in the model's axes, for
    the Shadows panel's date and local time (``core.render_blender
    .sun_toward``); ``None`` with the sun below the horizon."""
    raw = scene_json.get("shadows")
    raw = raw if isinstance(raw, dict) else {}
    lat, lon, north = _site(scene_json)
    month = _int(raw, "month", 1, 12, _DEFAULT_WHEN["month"])
    day = _int(raw, "day", 1, 31, _DEFAULT_WHEN["day"])
    hour = _int(raw, "hour", 0, 23, _DEFAULT_WHEN["hour"])
    minute = _int(raw, "minute", 0, 59, _DEFAULT_WHEN["minute"])
    off = raw.get("utc_offset")
    try:
        off = int(off) if off is not None else int(round(lon / 15.0))
        local = datetime(year or date.today().year, month, day, hour, minute,
                         tzinfo=timezone(timedelta(hours=off)))
    except (TypeError, ValueError, OverflowError):
        return None                     # 31 April, a zone out of range
    elevation, azimuth = solar_position(lat, lon, local.astimezone(timezone.utc))
    if elevation <= 0.0:
        return None
    er, ar = math.radians(elevation), math.radians(azimuth)
    east, north_c, up = (math.cos(er) * math.sin(ar), math.cos(er) * math.cos(ar),
                         math.sin(er))
    if north:
        b = math.radians(north)
        c, s = math.cos(b), math.sin(b)
        east, north_c = east * c + north_c * s, north_c * c - east * s
    return (east, north_c, up)


def ambience(scene_json: dict, year: int | None = None) -> dict:
    """What the Render panel asks for: ``ambience``, ``exposure``, ``sun``
    (the direction toward it, by day; ``None`` otherwise or below the
    horizon) and ``sun_strength`` (watts per m², as IngeTrazo sends it)."""
    pdata = scene_json.get("plugin_data")
    raw = pdata.get("render_blender") if isinstance(pdata, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    amb = raw.get("ambience", "day")
    amb = amb if amb in AMBIENCES else "day"
    try:
        scale = max(0.1, min(2.0, float(raw.get("sun_scale", 1.0))))
    except (TypeError, ValueError):
        scale = 1.0
    return {"ambience": amb,
            "exposure": EXPOSURE[amb],
            "sun": sun_toward(scene_json, year) if amb == "day" else None,
            "sun_strength": 3.0 * scale}
