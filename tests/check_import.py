"""Headless check of the add-on inside a real Blender:

    blender -b --factory-startup --python tests/check_import.py -- <folder>

``<folder>`` holds .igz documents (default: ``$INGETRAZO_SRC/examples``, the
examples of an IngeTrazo checkout). Imports every document, checks the scene it builds (objects,
shared component meshes, materials, cameras) and that a reload updates in
place without duplicating anything. Exits non-zero on failure.
"""
import os
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ingetrazo_io  # noqa: E402
from ingetrazo_io import build, igz  # noqa: E402,F401

ingetrazo_io.register()
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
folder = Path(args[0]) if args else Path(os.environ.get("INGETRAZO_SRC", ".")) / "examples"
docs = sorted(folder.glob("*.igz"))
if not docs:
    print(f"no .igz documents in {folder}")
    sys.exit(2)
for path in docs:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    stats = build.import_igz(bpy.context, str(path))
    ours = [o for o in bpy.data.objects if o.get(build.DOC_PROP)]
    meshes = [o for o in ours if o.type == "MESH"]
    check(meshes, f"{path.name}: no mesh objects")
    check(stats["faces"] > 0, f"{path.name}: no faces")
    check(bpy.context.scene.camera is not None, f"{path.name}: no active camera")
    check(any(o.name == "Vista general" for o in ours), f"{path.name}: no overview")
    doc = ingetrazo_io.igz.Document(path)
    uses: dict = {}

    def count(groups):
        for g in groups or []:
            if "proto" in g:
                uses[g["proto"]] = uses.get(g["proto"], 0) + 1
            count(g.get("children"))
    count(doc.scene.get("groups"))
    doc.close()
    if any(n > 1 for n in uses.values()):
        check(any(o.data.users > 1 for o in meshes),
              f"{path.name}: component copies do not share a mesh")
    keys = sorted(o[build.KEY_PROP] for o in ours)
    bpy.ops.ingetrazo.reload(filepath=str(path))
    again = [o for o in bpy.data.objects if o.get(build.DOC_PROP)]
    check(sorted(o[build.KEY_PROP] for o in again) == keys,
          f"{path.name}: reload changed the objects")
    check(not [m for m in bpy.data.meshes if m.users == 0],
          f"{path.name}: reload left orphan meshes")
    print(f"{path.name}: {stats}")

# A saved view renamed in IngeTrazo renames its camera on reload.
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402

from mathutils import Vector  # noqa: E402

for path in docs:
    probe = ingetrazo_io.igz.Document(path)
    has_views = bool(probe.scene.get("saved_views"))
    probe.close()
    if not has_views:
        continue
    tmp = Path(tempfile.mkdtemp()) / path.name
    shutil.copy(path, tmp)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build.import_igz(bpy.context, str(tmp))

    def rename_first_view(doc: dict) -> None:
        doc["scene"]["saved_views"][0]["name"] = "Renamed view"
    raw = tmp.read_bytes()
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(tmp) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        doc = json.loads(members["document.json"])
        rename_first_view(doc)
        members["document.json"] = json.dumps(doc).encode()
        with zipfile.ZipFile(tmp, "w") as zf:
            for n, data in members.items():
                zf.writestr(n, data)
    else:
        doc = json.loads(raw)
        rename_first_view(doc)
        tmp.write_text(json.dumps(doc))
    bpy.ops.ingetrazo.reload(filepath=str(tmp))
    cams = [o.name for o in bpy.data.objects if o.type == "CAMERA"]
    check("Renamed view" in cams, f"{path.name}: renamed view not renamed ({cams})")
    print(f"{path.name}: view rename -> {cams}")
    break

# A group moved into another one (IngeTrazo's Outliner re-nesting) keeps
# its Blender object on reload: same object, its modifier, same place.


def rewrite(path: Path, edit) -> None:
    raw = path.read_bytes()
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        doc = json.loads(members["document.json"])
        edit(doc)
        members["document.json"] = json.dumps(doc).encode()
        with zipfile.ZipFile(path, "w") as zf:
            for n, data in members.items():
                zf.writestr(n, data)
    else:
        doc = json.loads(raw)
        edit(doc)
        path.write_text(json.dumps(doc))


def mat_mul(a, b):
    """Column-major 4x4 product (QMatrix4x4.data() order)."""
    A = [[a[c * 4 + r] for c in range(4)] for r in range(4)]
    B = [[b[c * 4 + r] for c in range(4)] for r in range(4)]
    C = [[sum(A[r][k] * B[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
    return [C[r][c] for c in range(4) for r in range(4)]


def mat_inv(a):
    from mathutils import Matrix
    m = Matrix([[a[c * 4 + r] for c in range(4)] for r in range(4)]).inverted()
    return [m[r][c] for c in range(4) for r in range(4)]


for path in docs:
    probe = ingetrazo_io.igz.Document(path)
    placed = [g for g in probe.scene.get("groups", []) if g.get("xform")
              and not g.get("billboard")]
    probe.close()
    if len(placed) < 2:
        continue
    tmp = Path(tempfile.mkdtemp()) / path.name
    shutil.copy(path, tmp)

    def give_uids(doc):
        def walk(gs, prefix):
            for i, g in enumerate(gs or []):
                g.setdefault("uid", f"{prefix}{i}")
                walk(g.get("children"), f"{prefix}{i}c")
        walk(doc["scene"]["groups"], "t")
    rewrite(tmp, give_uids)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    build.import_igz(bpy.context, str(tmp))
    doc0 = ingetrazo_io.igz.Document(tmp)
    groups0 = [g for g in doc0.scene["groups"] if g.get("xform") and not g.get("billboard")]
    moved_uid, host_uid = groups0[0]["uid"], groups0[1]["uid"]
    doc0.close()
    def by_uid(uid):
        return [o for o in bpy.data.objects
                if str(o.get(build.KEY_PROP, "")).endswith(uid)]
    moved = by_uid(moved_uid)[0]
    moved.modifiers.new("Bevel", "BEVEL")
    world_before = moved.matrix_world.copy()

    def renest(doc):
        gs = doc["scene"]["groups"]
        a = next(g for g in gs if g.get("uid") == moved_uid)
        b = next(g for g in gs if g.get("uid") == host_uid)
        gs.remove(a)
        a["xform"] = mat_mul(mat_inv(b["xform"]), a["xform"])
        b.setdefault("children", []).append(a)
    rewrite(tmp, renest)
    bpy.ops.ingetrazo.reload(filepath=str(tmp))
    again = by_uid(moved_uid)
    check(len(again) == 1, f"{path.name}: re-nested group duplicated or lost ({len(again)})")
    if again:
        obj = again[0]
        check(obj.modifiers.get("Bevel") is not None,
              f"{path.name}: re-nested group lost its modifier")
        check(obj.parent is not None
              and str(obj.parent.get(build.KEY_PROP, "")).endswith(host_uid),
              f"{path.name}: re-nested group not parented to its new host")
        drift = max(abs(obj.matrix_world[r][c] - world_before[r][c])
                    for r in range(4) for c in range(4))
        check(drift < 1e-4, f"{path.name}: re-nested group moved ({drift})")
        print(f"{path.name}: re-nest -> parent {obj.parent.name if obj.parent else None}, "
              f"modifier kept {obj.modifiers.get('Bevel') is not None}, drift {drift:.2e}")
    break

# IngeTrazo 0.5.6+: every material gets a finish (chosen or guessed), and
# the Render panel's lights come in as Blender lights; a reload updates
# them in place, and one switched off in IngeTrazo is hidden, not lost.
path = docs[0]
tmp = Path(tempfile.mkdtemp()) / path.name
shutil.copy(path, tmp)
LIGHTS = [
    {"kind": "point", "pos": [1, 2, 2.5], "kelvin": 2700, "power": 400,
     "name": "Lámpara"},
    {"kind": "spot", "pos": [0, 0, 6], "dir": [0, 0.5, -1], "kelvin": 6500,
     "power": 1500, "angle": 40},
]


def add_lights(doc):
    pd = doc["scene"].setdefault("plugin_data", {})
    pd["render_blender"] = {"ambience": "night", "sun_scale": 1.0,
                            "lights": LIGHTS}
    reg = doc["scene"].get("materials") or []
    if reg:
        reg[0]["finish"] = "metal"


rewrite(tmp, add_lights)
bpy.ops.wm.read_factory_settings(use_empty=True)
build.import_igz(bpy.context, str(tmp))
mats = [m for m in bpy.data.materials if m.get(build.DOC_PROP)]
check(mats and all(m.get("ingetrazo_finish") in
                   ("matte", "satin", "gloss", "metal", "glass", "water")
                   for m in mats), f"{path.name}: a material without a finish")
probe = ingetrazo_io.igz.Document(tmp)
reg = probe.scene.get("materials") or []
probe.close()
if reg and bpy.data.materials.get(reg[0]["name"]) is not None:
    m = bpy.data.materials[reg[0]["name"]]
    bsdf = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    check(m["ingetrazo_finish"] == "metal" and bsdf.inputs["Metallic"].default_value == 1.0,
          f"{path.name}: the chosen finish was not applied")
lamps = sorted((o for o in bpy.data.objects if o.type == "LIGHT"), key=lambda o: o.name)
check(sorted(o.data.type for o in lamps) == ["POINT", "SPOT"],
      f"{path.name}: lights {[o.data.type for o in lamps]}")
if len(lamps) == 2:
    spot = next(o for o in lamps if o.data.type == "SPOT")
    aim = (spot.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    want = Vector((0, 0.5, -1)).normalized()
    check((aim - want).length < 1e-4, f"{path.name}: spot aims {tuple(aim)}")
    check(abs(spot.data.energy - 1500) < 1e-6, f"{path.name}: spot power")
    check(any(o.name == "Lámpara" for o in lamps), f"{path.name}: light name")
LIGHTS[0]["on"] = False
LIGHTS[0]["pos"] = [3, 3, 3]
del LIGHTS[1]
rewrite(tmp, add_lights)
bpy.ops.ingetrazo.reload(filepath=str(tmp))
lamps = [o for o in bpy.data.objects if o.type == "LIGHT"]
check(len(lamps) == 1 and lamps[0].hide_render
      and tuple(lamps[0].location) == (3, 3, 3),
      f"{path.name}: reloaded lights {[(o.name, o.hide_render) for o in lamps]}")
print(f"{path.name}: finishes {sorted({m['ingetrazo_finish'] for m in mats})}, "
      f"lights after reload {[(o.name, o.hide_render) for o in lamps]}")

print("OK" if not failures else f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
