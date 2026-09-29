# IngeTrazo → Blender

A Blender 5 extension that imports IngeTrazo documents (`.igz`) and reloads
them when they change. No network: the bridge is the file.

**Install:** in Blender, Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸
*Install from Disk…* and pick `ingetrazo_io-<version>.zip`.

**Use:** File ▸ Import ▸ IngeTrazo (.igz). The *IngeTrazo* tab of the 3D
view's sidebar (N) reloads a document after you save it again in IngeTrazo
— with the Reload button, or automatically with *Reload when the file
changes*. A reload updates what came from the file (geometry, placements,
new and deleted groups) and keeps what you added in Blender: lights, your
own cameras, modifiers and the materials you edited (the material button
next to Reload overwrites those too).

What comes across:

| IngeTrazo | Blender |
|---|---|
| document | a collection named after the file |
| tags (layers) | child collections, hidden if the tag was hidden |
| groups, nested groups | objects, parented as they nest |
| component copies | linked duplicates sharing one mesh |
| faces | polygons as drawn; faces with holes are triangulated |
| soft edges | smooth shading, every other edge sharp |
| paint, textures | materials (registry names), packed images, IngeTrazo's UVs |
| author's camera, saved views | cameras, plus a *Vista general* of the whole model |

Not carried: dimensions, texts, section planes and sheets. Face-me figures
come in as they stand (they do not turn toward the Blender camera).

**Build the zip** (from this folder):

    blender --command extension build --source-dir ingetrazo_io --output-dir .

**Check it** in a real Blender:

    blender -b --factory-startup --python tests/check_import.py

`tests/test_blender_igz_reader.py` in IngeTrazo's own suite keeps the
add-on's UV and camera maths identical to the app's.
