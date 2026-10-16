"""IngeTrazo 0.5.8 writes large documents as format 3: big meshes become
binary blocks under ``geom/``. The add-on must read them as the same faces
and edges, in the same order, and write edited face attributes back so that
IngeTrazo reopens them. Needs an IngeTrazo checkout (0.5.8 or newer) and
its Python environment:

    INGETRAZO_SRC=~/src/ingetrazo  <ingetrazo venv>/bin/python -m pytest tests
"""
from __future__ import annotations

import importlib.util
import os
import sys
import zipfile
from pathlib import Path

import pytest

_SRC = os.environ.get("INGETRAZO_SRC")
if not _SRC or not (Path(_SRC) / "tests" / "test_igz_format3.py").is_file():
    pytest.skip("set INGETRAZO_SRC to an IngeTrazo 0.5.8+ checkout",
                allow_module_level=True)
sys.path.insert(0, _SRC)

from core.scene import Scene  # noqa: E402
from formats import igz as it_igz  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ingetrazo_blender_igz",
    Path(__file__).resolve().parents[1] / "ingetrazo_io" / "igz.py")
igz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(igz)

_it_spec = importlib.util.spec_from_file_location(
    "ingetrazo_format3_tests", Path(_SRC) / "tests" / "test_igz_format3.py")
_it_tests = importlib.util.module_from_spec(_it_spec)
_it_spec.loader.exec_module(_it_tests)


@pytest.fixture
def binary(monkeypatch):
    """Every document counts as large and every mesh goes binary."""
    monkeypatch.setattr(it_igz, "_LARGE_DOCUMENT", 0)
    monkeypatch.setattr(it_igz, "_BULK_MIN", 1)


def _strip(node):
    """Drop what legitimately differs (texture file names)."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items()
                if k not in ("path", "embed")}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    if isinstance(node, float):
        return round(node, 5)
    return node


def _meshes(scene: dict) -> list:
    out = [scene]
    for g in scene.get("groups") or []:
        stack = [g]
        while stack:
            n = stack.pop(0)
            out.append(n)
            stack.extend(n.get("children") or [])
    out.extend(scene.get("protos") or [])
    return [{"faces": m.get("faces", []), "edges": m.get("edges", [])}
            for m in out]


def _saved(tmp_path, name, scene_obj):
    path = tmp_path / name
    it_igz.save_scene(scene_obj, path)
    return path


def test_blocks_read_as_ingetrazo_reads_them(tmp_path, monkeypatch):
    monkeypatch.setattr(it_igz, "_LARGE_DOCUMENT", 0)
    monkeypatch.setattr(it_igz, "_BULK_MIN", 1)
    path = _saved(tmp_path, "big.igz", _it_tests._scene(tmp_path))
    with zipfile.ZipFile(path) as zf:
        assert any(n.startswith("geom/") for n in zf.namelist())
    doc = igz.Document(path)
    try:
        ours = _meshes(doc.scene)
    finally:
        doc.close()
    # IngeTrazo opens it, and writes the same scene as plain JSON meshes.
    back = Scene()
    it_igz.load_into(back, path)
    monkeypatch.undo()
    ref_path = _saved(tmp_path, "ref.igz", back)
    ref = igz.Document(ref_path)
    try:
        assert "geom" not in str(list(ref.scene))
        theirs = _meshes(ref.scene)
    finally:
        ref.close()
    assert _strip(ours) == _strip(theirs)
    assert sum(len(m["faces"]) for m in ours) > 0
    assert any(f.get("holes") for m in ours for f in m["faces"])
    assert any(e.get("soft") and e.get("curve") == 7 and e.get("layer") == "Guias"
               for m in ours for e in m["edges"])


def test_unchanged_write_reopens_the_same(tmp_path, binary):
    path = _saved(tmp_path, "big.igz", _it_tests._scene(tmp_path))
    before = Scene()
    it_igz.load_into(before, path)
    doc = igz.Document(path)
    scene = doc.scene
    doc.close()
    igz.write_document(path, scene)
    assert "faces" in scene                     # the caller's lists stay
    with zipfile.ZipFile(path) as zf:
        text = zf.read("document.json").decode()
    assert '"geom"' in text and '"faces"' not in text
    after = Scene()
    it_igz.load_into(after, path)
    assert _it_tests._canonical(after) == _it_tests._canonical(before)


def test_edited_uvw_reaches_ingetrazo(tmp_path, binary):
    path = _saved(tmp_path, "big.igz", _it_tests._scene(tmp_path))
    doc = igz.Document(path)
    scene = doc.scene
    doc.close()
    face = next(f for f in scene["faces"] if isinstance(f.get("texture"), dict))
    uvw = [0.5, 0, 0, 0.1, 0, 0.5, 0, 0.2]
    face["texture"]["uvw"] = uvw
    other = next(f for f in scene["faces"] if f is not face and f.get("opacity"))
    igz.write_document(path, scene)
    back = Scene()
    it_igz.load_into(back, path)
    textured = [f for f in back.mesh.faces if f.attrs.get("texture")]
    assert [list(f.attrs["texture"].get("uvw")) for f in textured] == [uvw]
    assert any(f.attrs.get("opacity") == other["opacity"] for f in back.mesh.faces)


def test_a_crafted_block_is_refused(tmp_path, binary):
    path = _saved(tmp_path, "big.igz", _it_tests._scene(tmp_path))
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    import json
    data = json.loads(members["document.json"])
    data["scene"]["geom"]["arrays"]["pos"][0] = "O"     # an object dtype
    members["document.json"] = json.dumps(data).encode()
    bad = tmp_path / "bad.igz"
    with zipfile.ZipFile(bad, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    with pytest.raises(ValueError, match="damaged geometry block"):
        igz.Document(bad)
