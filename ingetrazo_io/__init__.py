# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Victor Crespo (3dvic.com · github.com/vicc3d)
"""IngeTrazo → Blender.

File ▸ Import ▸ IngeTrazo (.igz) brings a document in with its groups,
components, tags, materials, textures, soft edges and cameras. The
IngeTrazo tab of the 3D view's sidebar (N) reloads it after you save again
in IngeTrazo — by hand, or automatically whenever the file changes — keeping
what you added in Blender. One thing travels back: **Send UVs** writes the
texture mapping edited in Blender's UV editor into the .igz (writeback.py).

No network: the "bridge" is the .igz file itself.
"""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import build, writeback


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


class INGETRAZO_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    remind_reopen: BoolProperty(
        name="Remind me to reopen the file in IngeTrazo after sending UVs",
        description="IngeTrazo does not notice the change by itself: the "
                    "document has to be closed and opened again there",
        default=True)

    def draw(self, _context):
        self.layout.prop(self, "remind_reopen")


def _prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon is not None else None


class INGETRAZO_OT_send_uvs(bpy.types.Operator):
    """Write the texture mapping edited in Blender's UV editor into the
    .igz, so IngeTrazo shows it (a backup of the file is kept as .igz.bak)"""
    bl_idname = "ingetrazo.send_uvs"
    bl_label = "Send UVs to IngeTrazo"
    bl_options = {"REGISTER"}

    filepath: StringProperty()
    own_paint: BoolProperty(
        name="Give them their own copy of the group's paint",
        description="The face keeps the same texture, now as its own paint, "
                    "mapped as you edited it. Repainting the group in "
                    "IngeTrazo will no longer change these faces",
        default=True)
    dont_remind: BoolProperty(name="Don't show this again", default=False)
    # What the dialog shows (from a dry run).
    n_own: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    n_send: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    n_component: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    n_moved: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    remind: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def _sync(self, context):
        """UVs edited in Edit Mode live in the edit mesh: step out to
        Object Mode so the mesh holds them. Returns whether to step back."""
        if context.mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")
            return True
        return False

    def invoke(self, context, _event):
        editing = self._sync(context)
        try:
            st = writeback.send_uvs(self.filepath, own_paint=True, dry_run=True)
        except writeback.Changed:
            self.report({"ERROR"}, "IngeTrazo: the .igz was saved in IngeTrazo "
                        "after it was loaded here. Reload first, then send.")
            return {"CANCELLED"}
        except (OSError, ValueError, KeyError) as exc:
            self.report({"ERROR"}, f"IngeTrazo: {exc}")
            return {"CANCELLED"}
        finally:
            if editing:
                bpy.ops.object.mode_set(mode="EDIT")
        self.n_own = st["own_paint"]
        self.n_send = st["written"] - st["own_paint"]
        self.n_component = st["component_paint"]
        self.n_moved = st["moved"]
        prefs = _prefs(context)
        self.remind = prefs is None or prefs.remind_reopen
        if not st["written"] and not self.n_component and not self.n_moved:
            self.report({"INFO"}, "IngeTrazo: no UV changes to send")
            return {"CANCELLED"}
        if self.n_own or self.n_component or self.n_moved or self.remind \
                or not st["written"]:
            return context.window_manager.invoke_props_dialog(
                self, width=460, title="Send UVs to IngeTrazo",
                confirm_text="Send" if st["written"] else "OK")
        return self.execute(context)

    def draw(self, _context):
        col = self.layout.column(align=False)
        name = os.path.basename(self.filepath)

        def lines(box, icon, *texts):
            for i, t in enumerate(texts):
                box.label(text=t, icon=icon if i == 0 else "BLANK1")
        if self.n_send:
            lines(col, "CHECKMARK", f"{self.n_send} face(s) will be sent to {name}.")
        if self.n_own:
            box = col.box()
            lines(box, "INFO", f"{self.n_own} edited face(s) take their texture",
                  "from their group's paint.")
            box.prop(self, "own_paint")
        if self.n_component:
            lines(col.box(), "ERROR",
                  f"{self.n_component} edited face(s) take their component's paint.",
                  "They are not sent: every copy would change.",
                  "Paint them inside the component in IngeTrazo.")
        if self.n_moved:
            lines(col.box(), "ERROR",
                  f"{self.n_moved} face(s) were moved or reshaped in Blender.",
                  "Only UVs travel back, so they are not sent.")
        if self.remind and (self.n_send or self.n_own):
            box = col.box()
            lines(box, "FILE_REFRESH", f"Then close {name} in IngeTrazo",
                  "and open it again to see the new mapping.",
                  f"A backup is kept as {name}.bak.")
            box.prop(self, "dont_remind")

    def execute(self, context):
        if self.dont_remind:
            prefs = _prefs(context)
            if prefs is not None:
                prefs.remind_reopen = False
        editing = self._sync(context)
        try:
            st = writeback.send_uvs(self.filepath, own_paint=self.own_paint)
        except writeback.Changed:
            self.report({"ERROR"}, "IngeTrazo: the .igz was saved in IngeTrazo "
                        "after it was loaded here. Reload first, then send.")
            return {"CANCELLED"}
        except (OSError, ValueError, KeyError) as exc:
            self.report({"ERROR"}, f"IngeTrazo: {exc}")
            return {"CANCELLED"}
        finally:
            if editing:
                bpy.ops.object.mode_set(mode="EDIT")
        if not st["written"]:
            self.report({"INFO"}, "IngeTrazo: nothing sent")
            return {"FINISHED"}
        msg = (f"IngeTrazo: UVs of {st['written']} face(s) sent to "
               f"{os.path.basename(self.filepath)} — reopen it in IngeTrazo")
        if st["max_error"] > 1e-4:
            msg += " (some edits were approximated)"
        self.report({"INFO"}, msg)
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
            op = box.operator(INGETRAZO_OT_send_uvs.bl_idname, icon="UV",
                              text="Send UVs to IngeTrazo")
            op.filepath = path
            if not os.path.isfile(path):
                box.label(text="File not found", icon="ERROR")


def _menu_import(self, _context):
    self.layout.operator(INGETRAZO_OT_import.bl_idname, text="IngeTrazo (.igz)")


_classes = (INGETRAZO_Preferences, INGETRAZO_OT_import, INGETRAZO_OT_reload,
            INGETRAZO_OT_send_uvs, INGETRAZO_PT_panel)


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
