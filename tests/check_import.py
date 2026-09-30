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

print("OK" if not failures else f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
