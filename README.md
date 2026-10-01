# IngeTrazo → Blender

A Blender 5 extension that imports [IngeTrazo](https://github.com/ingelibre/ingetrazo)
documents (`.igz`) **with their structure** and reloads them when they
change, so a model drawn in IngeTrazo can be lit, textured and rendered in
Blender while you keep modelling in IngeTrazo.

It complements IngeTrazo's own *Render with Blender* tab (which renders
from inside IngeTrazo): this one works from inside Blender, keeps groups,
components and tags, and updates the scene in place on every save.

No network, no server: the bridge is the `.igz` file itself.

![A spiral stair in IngeTrazo (left) and the same model in Blender, reloaded
live with its components, cameras and face-me figure](docs/ingetrazo-to-blender.jpg)

> Independent project, not affiliated with IngeTrazo's maintainers.

## Install

1. Download `ingetrazo_io-<version>.zip` from the
   [Releases](../../releases) page (or build it, below).
2. In Blender: **Edit ▸ Preferences ▸ Get Extensions ▸ ⌄ ▸ Install from Disk…**
   and pick the zip.

Requires Blender **5.0** or newer (tested with 5.2.2 LTS).

## Use

- **File ▸ Import ▸ IngeTrazo (.igz)**.
- The **IngeTrazo** tab of the 3D view's sidebar (N) reloads a document after
  you save it again in IngeTrazo — with **Reload**, or automatically with
  **Reload when the file changes**.
- A reload updates what came from the file (geometry, placements, new and
  deleted groups, IngeTrazo's lights) and keeps what you added in Blender:
  your own lights, your own
  cameras, modifiers and the materials you edited. The material button next
  to Reload overwrites those materials too.

## What comes across

| IngeTrazo | Blender |
|---|---|
| document | a collection named after the file |
| tags (layers) | child collections, hidden if the tag was hidden |
| groups, nested groups | objects, parented as they nest, origin at the base of their box |
| component copies | linked duplicates sharing one mesh |
| faces | polygons as drawn; faces with holes are triangulated |
| soft edges | smooth shading, every other edge sharp |
| paint, textures | materials (registry names), packed images, IngeTrazo's exact UVs |
| face-me figures (2D people) | a card that turns to the scene camera (Locked Track) |
| material finishes (0.5.6+): matte, satin, gloss, metal, glass, water | the Principled BSDF set as IngeTrazo's *Render with Blender* sets it (glass is a thin pane you see through — the add-on turns on EEVEE's ray tracing for it — and water ripples); a material with no finish chosen gets the one IngeTrazo guesses from its name |
| lights of the Render panel (0.5.6+) | point and spot lights in a *Luces* collection, same watts, colour temperature and aim; one switched off in IngeTrazo is hidden |
| author's camera, saved views | cameras, plus a *Vista general* of the whole model |

Not carried: dimensions, texts, section planes, sheets, and the Render
panel's ambience (day sun, night, overcast) — light the world in Blender.

Materials imported with an earlier version of the add-on keep their old
settings: use the material button next to Reload once to give them their
finishes.

## Build the zip

```bash
blender --command extension build --source-dir ingetrazo_io --output-dir .
```

## Tests

Inside a real Blender, on a folder of `.igz` documents (for instance the
examples of an IngeTrazo checkout):

```bash
blender -b --factory-startup --python tests/check_import.py -- /path/to/ingetrazo/examples
```

The add-on re-implements IngeTrazo's texture projection, camera maths, finish
guessing and light rules;
`tests/test_parity_with_ingetrazo.py` checks them against IngeTrazo's own
code (needs an IngeTrazo checkout and its Python environment):

```bash
INGETRAZO_SRC=/path/to/ingetrazo /path/to/ingetrazo/venv/bin/python -m pytest tests
```

## Credits

**Concept, UX/UI & Design** — [Victor Crespo](https://3dvic.com)

Developed with AI assistance (Claude). The texture projection, camera maths,
material finishes and light rules are ported from [IngeTrazo](https://github.com/ingelibre/ingetrazo) by Marco
Sumari Tellez and contributors.

Released under the GPL-3.0-or-later License (IngeTrazo's licence, which the
ported code carries).
