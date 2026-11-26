# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Security & Networking
- Bound web control panel to `127.0.0.1` by default (`--web-host` to override) and added `Origin`/`Host` validation on `POST` endpoints plus `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy` headers.
- Enforced strict group identifier validation (`^[A-Za-z0-9_-]{1,64}$`) and `os.path.commonpath` checks across storage and static file serving to prevent path traversal and arbitrary deletion.
- Escaped render errors before embedding in fallback SVG responses.
- Tightened Demonware proxy interception to exact host matching, added optional `--allow-ip` client IP allowlisting, socket timeouts, bounded header/body reads, and `Transfer-Encoding: chunked` decoding.
- Added `SECURITY.md` documenting the threat model and hardening controls.

### Architecture, Observability & CLI
- Added CLI flags in `run.py` (`--proxy-host`, `--proxy-port`, `--web-host`, `--web-port`, `--allow-ip`, `--data-dir`, `--no-browser`) with graceful shutdown.
- Added Server-Sent Events (`/api/events`) and a collapsible **Live Proxy Activity** table showing intercepted Demonware slot requests, byte counts, and cache headers.
- Added `/api/emblems/<group>/<slot>/inspect` and a **Binary Inspector** modal decoding all 44-byte layer structs with byte offsets and HTTP wrapper headers.

### Editor UX, Image-to-Emblem Converter & Demo
- Added a shape-fitting **Image → Emblem** converter (`editor/vectorizer.js`, `emblemtool/shapes/vectorize.py`, `POST /api/editor/vectorize`) using closed-form least-squares color solving, error-weighted importance sampling, and multi-scale hill-climbing refinement over unlocked BO2 shapes with live side-by-side preview in both the desktop app and static GitHub Pages demo.
- Modularized the editor frontend into ES modules (`editor/binary.js`, `editor/canvas.js`, `editor/history.js`, `editor/api.js`, `editor/vectorizer.js`) with a client-side `DataView` `.bin` encoder/decoder and LRU glyph tint cache.
- Added on-canvas corner resize and top rotation handles, toggleable grid and center/quarter snapping guides, BO2 preset & recent color swatches, layer visibility/lock toggles, and **Export PNG**.
- Added `scripts/build_demo.py` and a GitHub Pages workflow (`.github/workflows/deploy-demo.yml`) to publish a zero-install interactive browser demo.

### Testing & CI
- Added integration tests for `emblemtool/proxy.py` (passthrough, capture, inject, cache-check, chunked transfer, CONNECT tunnel, allowlist, oversized headers) and security regression tests in `tests/test_security.py`.
- Added `pyproject.toml`, `ruff` linting, and `.github/workflows/ci.yml` across Python 3.9–3.12; gated `.github/workflows/build-exe.yml` on tests passing.

## [2026-08-19] — Control panel and editor craft
- Added PS5 last-seen telemetry, capture toast notifications, and Show-mode session reset reminder banner.
- Added library search, `.bin` import/delete, and locked rank/weapon risk warnings.
- Added editor undo/redo (`Ctrl+Z`/`Ctrl+Y`), duplicate (`Ctrl+D`), copy/paste layers, live outline rendering, and Windows PyInstaller GitHub Action.

## [2026-08-18] — Editor performance and pixel-accurate picking
- Packed all 261 calibrated shape glyphs into a single sprite atlas (`/api/shapes/atlas.png`) for fast editor startup.
- Switched canvas layer hit-testing from bounding boxes to glyph alpha pixels so clicks pass through transparent regions.
- Added mtime-based on-disk PNG thumbnail caching with `ETag` / `304 Not Modified`.

## [2026-08-17] — Initial PC emblem editor
- Added desktop emblem editor (`/editor.html`) and 1408-byte `.bin` serializer on top of `alexkotr1/bo2-emblem-toolkit`.
- Removed the low-accuracy image-to-32-shapes converter in favor of manual authoring and capture editing.
