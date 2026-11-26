#!/usr/bin/env python3
"""Build a standalone static web demo of the BO2 Emblem Editor for GitHub Pages.

Outputs a self-contained directory (default: dist/demo) with pre-baked shape
atlas, shape catalog, preset showcase emblems, and the zero-build ES module
editor. Recruiters and users can test the editor and export real 1408-byte
.bin files directly in the browser with no Python install required.
"""
import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool.shapes.atlas import get_shape_atlas
from emblemtool.shapes.shape_id_map import list_shapes

STATIC_SRC = os.path.join(ROOT, "emblemtool", "web", "static")

DEMO_PRESETS = [
    {
        "group": "preset_shield",
        "slot": 0,
        "label": "Cyber Shield (Demo)",
        "source": "editor",
        "layers": [
            {"index": 0, "shape": 196, "r": 0.08, "g": 0.1, "b": 0.14, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": 0.05, "sy": 0.05, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 1, "shape": 192, "r": 0.02, "g": 0.71, "b": 0.83, "a": 0.22,
             "x": 0.0, "y": 0.0, "sx": -0.15, "sy": -0.15, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 2, "shape": 178, "r": 0.98, "g": 0.45, "b": 0.09, "a": 1.0,
             "x": 0.0, "y": 0.02, "sx": -0.32, "sy": -0.32, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 3, "shape": 178, "r": 0.07, "g": 0.09, "b": 0.13, "a": 1.0,
             "x": 0.0, "y": 0.02, "sx": -0.48, "sy": -0.48, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 4, "shape": 186, "r": 0.98, "g": 0.8, "b": 0.08, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": -1.15, "sy": -1.15, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 5, "shape": 192, "r": 0.22, "g": 0.74, "b": 0.97, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": -0.1, "sy": -0.1, "rot": 0.0, "outlined": True, "flipped": False},
        ],
    },
    {
        "group": "preset_treyarch",
        "slot": 0,
        "label": "Tactical Star (Demo)",
        "source": "editor",
        "layers": [
            {"index": 0, "shape": 196, "r": 0.1, "g": 0.06, "b": 0.06, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": 0.05, "sy": 0.05, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 1, "shape": 145, "r": 0.86, "g": 0.15, "b": 0.15, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": -0.25, "sy": -0.25, "rot": 15.0, "outlined": False, "flipped": False},
            {"index": 2, "shape": 192, "r": 0.1, "g": 0.1, "b": 0.12, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": -0.95, "sy": -0.95, "rot": 0.0, "outlined": False, "flipped": False},
            {"index": 3, "shape": 197, "r": 1.0, "g": 0.5, "b": 0.0, "a": 1.0,
             "x": 0.0, "y": 0.0, "sx": -1.25, "sy": -1.25, "rot": 0.0, "outlined": False, "flipped": False},
        ],
    },
]


def build_demo(out_dir):
    out_dir = os.path.abspath(out_dir)
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "demo-data"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "editor"), exist_ok=True)

    # Copy static frontend assets
    for fname in ("style.css", "editor.css", "editor.js"):
        shutil.copy2(os.path.join(STATIC_SRC, fname), os.path.join(out_dir, fname))
    editor_mod_dir = os.path.join(STATIC_SRC, "editor")
    for fname in os.listdir(editor_mod_dir):
        if fname.endswith(".js"):
            shutil.copy2(
                os.path.join(editor_mod_dir, fname),
                os.path.join(out_dir, "editor", fname),
            )

    # Rewrite editor.html -> index.html with relative paths + demo mode flag
    with open(os.path.join(STATIC_SRC, "editor.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace('href="/style.css"', 'href="./style.css"')
    html = re.sub(r'href="/editor\.css[^"]*"', 'href="./editor.css"', html)
    html = re.sub(
        r'<script type="module" src="/editor\.js[^"]*"></script>',
        '<script>window.EMBLEM_DEMO_MODE = true; window.EMBLEM_DEMO_BASE = "./demo-data";</script>\n'
        '<script type="module" src="./editor.js"></script>',
        html,
    )
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # Export pre-baked shape catalog, atlas PNG/JSON, and showcase presets
    png_bytes, meta = get_shape_atlas()
    meta_out = dict(meta)
    meta_out["url"] = "./demo-data/atlas.png"
    with open(os.path.join(out_dir, "demo-data", "atlas.png"), "wb") as f:
        f.write(png_bytes)
    with open(os.path.join(out_dir, "demo-data", "atlas.json"), "w", encoding="utf-8") as f:
        json.dump(meta_out, f)
    with open(os.path.join(out_dir, "demo-data", "shapes.json"), "w", encoding="utf-8") as f:
        json.dump(list_shapes(), f)
    with open(os.path.join(out_dir, "demo-data", "presets.json"), "w", encoding="utf-8") as f:
        json.dump(DEMO_PRESETS, f, indent=2)

    return out_dir


def main():
    parser = argparse.ArgumentParser(description="Build static GitHub Pages demo.")
    parser.add_argument("--out", default=os.path.join(ROOT, "dist", "demo"))
    args = parser.parse_args()
    out = build_demo(args.out)
    print(f"Built static demo in {out}")


if __name__ == "__main__":
    main()
