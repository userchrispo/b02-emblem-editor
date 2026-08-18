# Updates

This project is based on [alexkotr1/bo2-emblem-toolkit](https://github.com/alexkotr1/bo2-emblem-toolkit). The proxy, capture/show flow, emblem renderer, shape calibration, and original control panel are his work and are still here.

## What this copy adds

The original toolkit copies another player's emblem onto your account. This copy keeps that, and adds a PC editor on top:

- **Emblem editor** (`/editor.html`): pick shapes, drag layers, set color, size, rotation, outline, and flip, with a live preview
- **`.bin` writer** so authored emblems use the same 1408-byte format as captures
- **Save**, **Use in Show**, and **Download .bin** from the editor
- **Edit a captured emblem** without overwriting the original (saving a capture creates a new copy)

## What we removed

**From image (removed 17 Aug 2026).** The editor used to take a dropped or pasted picture and try to rebuild it with 32 Black Ops II shapes. That copy was not accurate enough to keep, so the button, drop zone, and `/api/editor/from-image` path are gone. Capture another player's emblem, or build one by hand in the PC editor. Then **Use in Show** and save it in the in-game editor so it stays on your account.

## 17 Aug 2026 — Editor load and clicking

Two problems in the PC emblem editor:

**Clicks picked the wrong layer.** The editor treated each shape as a full rectangle, so empty corners of a big circle in front stole clicks from the shape you could actually see. Clicks now go through transparent pixels and hit the visible shape.

**The editor loaded slowly.** Opening it requested every shape PNG on its own — hundreds of them through the local server. It now loads one packed sheet, and dragging or moving sliders no longer rebuilds the whole side panel on every move.

Restart the toolkit (`python run.py` or `start.bat`) and refresh the editor so this version is what you’re running. Then click a shape that’s tucked under another — it should select the one you’re pointing at.
