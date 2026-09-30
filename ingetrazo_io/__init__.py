# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""IngeTrazo → Blender.

File ▸ Import ▸ IngeTrazo (.igz) brings a document in with its groups,
components, tags, materials, textures, soft edges and cameras. The
IngeTrazo tab of the 3D view's sidebar (N) reloads it after you save again
in IngeTrazo — by hand, or automatically whenever the file changes — keeping
what you added in Blender.

No network: the "bridge" is the .igz file itself.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import build


def _documents():
    """Collections that came from a .igz: ``[(collection, path)]``."""
    return [(c, c[build.DOC_PROP]) for c in bpy.data.collections
            if c.get(build.DOC_PROP) and c.get(build.MTIME_PROP) is not None]


class INGETRAZO_OT_import(bpy.types.Operator, ImportHelper):
    """Import an IngeTrazo document"""
    bl_idname = "import_scene.ingetrazo"
    bl_label = "Import IngeTrazo"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".igz"
    filter_glob: StringProperty(default="*.igz", options={"HIDDEN"})

    def execute(self, context):
        try:
            stats = build.import_igz(context, self.filepath)
        except (OSError, ValueError, KeyError) as exc:
            self.report({"ERROR"}, f"IngeTrazo: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "IngeTrazo: {objects} objects, {faces} faces, "
                    "{components} component copies".format(**stats))
        return {"FINISHED"}


class INGETRAZO_OT_reload(bpy.types.Operator):
    """Read the .igz again and update the scene in place"""
    bl_idname = "ingetrazo.reload"
    bl_label = "Reload"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty()
    update_materials: BoolProperty(
        name="Update materials",
        description="Also overwrite materials already in the .blend (loses "
                    "the changes you made to them in Blender)",
        default=False)

    def execute(self, context):
        targets = [self.filepath] if self.filepath else [p for _c, p in _documents()]
        done = 0
        for path in targets:
            if not os.path.isfile(path):
                self.report({"WARNING"}, f"IngeTrazo: missing {path}")
                continue
            try:
                build.import_igz(context, path, self.update_materials)
                done += 1
            except (OSError, ValueError, KeyError) as exc:
                self.report({"ERROR"}, f"IngeTrazo: {exc}")
        self.report({"INFO"}, f"IngeTrazo: {done} document(s) reloaded")
        return {"FINISHED"}


def _auto_reload():
    """Timer: reload every imported document whose file changed. IngeTrazo
    saves through a temporary file and a rename, so the new mtime appears
    once the document is complete."""
    scene = bpy.context.scene
    if scene is None or not scene.ingetrazo_auto_reload:
        return 2.0
    for coll, path in _documents():
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > float(coll.get(build.MTIME_PROP, 0.0)) + 1e-6:
            try:
                build.import_igz(bpy.context, path)
            except Exception as exc:  # noqa: BLE001 — a timer must not die
                print(f"IngeTrazo: auto-reload of {path} failed: {exc}")
                coll[build.MTIME_PROP] = mtime   # do not retry a broken save
    return 2.0


class INGETRAZO_PT_panel(bpy.types.Panel):
    bl_label = "IngeTrazo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IngeTrazo"

    def draw(self, context):
        layout = self.layout
        layout.operator(INGETRAZO_OT_import.bl_idname, icon="IMPORT",
                        text="Import .igz…")
        docs = _documents()
        if not docs:
            layout.label(text="No IngeTrazo document yet")
            return
        layout.prop(context.scene, "ingetrazo_auto_reload")
        for coll, path in docs:
            box = layout.box()
            box.label(text=coll.name, icon="OUTLINER_COLLECTION")
            row = box.row(align=True)
            op = row.operator(INGETRAZO_OT_reload.bl_idname, icon="FILE_REFRESH")
            op.filepath = path
            op = row.operator(INGETRAZO_OT_reload.bl_idname, text="",
                              icon="MATERIAL")
            op.filepath = path
            op.update_materials = True
            if not os.path.isfile(path):
                box.label(text="File not found", icon="ERROR")


def _menu_import(self, _context):
    self.layout.operator(INGETRAZO_OT_import.bl_idname, text="IngeTrazo (.igz)")


_classes = (INGETRAZO_OT_import, INGETRAZO_OT_reload, INGETRAZO_PT_panel)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.Scene.ingetrazo_auto_reload = BoolProperty(
        name="Reload when the file changes",
        description="Watch the imported .igz files and update the scene "
                    "each time IngeTrazo saves them",
        default=False)
    if not bpy.app.timers.is_registered(_auto_reload):
        bpy.app.timers.register(_auto_reload, first_interval=2.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_auto_reload):
        bpy.app.timers.unregister(_auto_reload)
    del bpy.types.Scene.ingetrazo_auto_reload
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
