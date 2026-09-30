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

print("OK" if not failures else f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
