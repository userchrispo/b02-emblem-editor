"""The web control panel: a small JSON API (below) plus static files served
from web/static/. This is the only interface end users touch - there is no
command-line mode for day-to-day use.
"""
import http.server
import json
import mimetypes
import os
import urllib.parse

from .. import config
from ..state import read_state, write_state
from ..activity import read_activity, clear_session_flag
from ..storage import (
    list_emblems, set_emblem_label, slot_path, save_authored_emblem,
    read_meta, get_emblem_label, delete_emblem, import_slot_bytes,
)
from ..broadcast import select_emblem, clear_selection, read_selection
from ..shapes import render as emblem_render
from ..shapes.atlas import get_shape_atlas, warm_atlas
from ..shapes.shape_id_map import list_shapes
from ..thumbs import THUMB_SIZE, cached_thumb_png, thumb_etag
from .netinfo import get_lan_ip

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_MODE_TO_INTERNAL = {"off": "PASSTHROUGH", "capture": "CAPTURE", "inject": "INJECT"}
_MODE_TO_PUBLIC = {v: k for k, v in _MODE_TO_INTERNAL.items()}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet - proxy.py already logs what matters

    # ---------- helpers ----------

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, content_type, code=200, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _serve_static(self, rel_path):
        if rel_path == "":
            rel_path = "index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            self._bytes(f.read(), ctype)

    # ---------- GET ----------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/status":
            activity = read_activity()
            self._json({
                "mode": _MODE_TO_PUBLIC.get(read_state(), "off"),
                "selected": read_selection(),
                "last_hit": activity.get("last_hit"),
                "last_capture": activity.get("last_capture"),
                "needs_session_reset": bool(activity.get("needs_session_reset")),
            })

        elif path == "/api/emblems":
            self._json(list_emblems())

        elif path == "/api/network-info":
            self._json({
                "lan_ip": get_lan_ip(),
                "proxy_port": config.PROXY_PORT,
                "web_port": config.WEB_PORT,
            })

        elif path == "/LICENSE":
            license_path = os.path.join(config.RESOURCE_DIR, "LICENSE")
            if os.path.isfile(license_path):
                with open(license_path, "rb") as f:
                    self._bytes(f.read(), "text/plain; charset=utf-8")
            else:
                self._json({"error": "not found"}, 404)

        elif path == "/api/shapes":
            self._json(list_shapes())

        elif path == "/api/shapes/atlas.json":
            _, meta = get_shape_atlas()
            self.send_response(200)
            body = json.dumps(meta).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/shapes/atlas.png":
            data, _ = get_shape_atlas()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        elif path.startswith("/api/shapes/") and path.endswith(".png"):
            try:
                shape_id = int(path.rsplit("/", 1)[1].removesuffix(".png"))
            except ValueError:
                self._json({"error": "not found"}, 404)
                return
            data = emblem_render.shape_png_bytes(shape_id)
            if not data:
                self._json({"error": "not found"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)

        elif path.startswith("/api/emblems/") and path.endswith("/layers"):
            bits = path.split("/")
            # /api/emblems/<group>/<slot>/layers
            if len(bits) == 6:
                group = urllib.parse.unquote(bits[3])
                try:
                    slot = int(bits[4])
                except ValueError:
                    self._json({"error": "not found"}, 404)
                    return
                bin_path = slot_path(group, slot)
                if os.path.exists(bin_path):
                    meta = read_meta(group) or {}
                    self._json({
                        "group": group,
                        "slot": slot,
                        "label": get_emblem_label(group, slot),
                        "source": meta.get("source", "capture"),
                        "layers": emblem_render.parse_slot_file(bin_path),
                    })
                    return
            self._json({"error": "not found"}, 404)

        elif path.startswith("/api/render/"):
            bits = path.split("/")
            if len(bits) == 5:
                group = urllib.parse.unquote(bits[3])
                slot_name = bits[4].split("?", 1)[0].removesuffix(".png")
                try:
                    slot = int(slot_name)
                except ValueError:
                    self._json({"error": "not found"}, 404)
                    return
                bin_path = slot_path(group, slot)
                if os.path.exists(bin_path):
                    try:
                        etag = thumb_etag(group, slot, THUMB_SIZE)
                        if self.headers.get("If-None-Match") == etag:
                            self.send_response(304)
                            self.send_header("ETag", etag)
                            self.send_header("Cache-Control", "private, max-age=3600")
                            self.end_headers()
                            return
                        body = cached_thumb_png(group, slot, THUMB_SIZE)
                        self._bytes(body, "image/png", extra_headers={
                            "ETag": etag,
                            "Cache-Control": "private, max-age=3600",
                        })
                    except Exception as e:
                        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220">'
                               f'<rect width="100%" height="100%" fill="#1a1a1a"/>'
                               f'<text x="10" y="30" fill="#e55" font-size="12">render error</text>'
                               f'<text x="10" y="50" fill="#999" font-size="10">{e}</text></svg>')
                        self._bytes(svg.encode(), "image/svg+xml")
                    return
            self._json({"error": "not found"}, 404)

        elif path.startswith("/api/"):
            self._json({"error": "not found"}, 404)

        else:
            self._serve_static(path.lstrip("/"))

    def _prepare_layers(self, body):
        layers = emblem_render.compact_layers(body.get("layers") or [])
        if len(layers) > emblem_render.NUM_LAYERS:
            return None, "too many layers"
        return layers, None

    # ---------- POST ----------

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 1024 * 1024:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self._json({"ok": False, "error": "Request is too large"}, 413)
            return
        body = self._read_json_body()

        if path == "/api/mode":
            public_mode = body.get("mode")
            internal = _MODE_TO_INTERNAL.get(public_mode)
            if not internal:
                self._json({"ok": False, "error": "invalid mode"}, 400)
                return
            write_state(internal)
            self._json({"ok": True})

        elif path == "/api/session-reset":
            clear_session_flag()
            self._json({"ok": True})

        elif path == "/api/select":
            group, slot = body.get("group"), body.get("slot")
            try:
                slot = int(slot)
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "not found"}, 404)
                return
            if not group or not os.path.exists(slot_path(group, slot)):
                self._json({"ok": False, "error": "not found"}, 404)
                return
            select_emblem(group, slot)
            self._json({"ok": True})

        elif path == "/api/editor/save":
            layers, err = self._prepare_layers(body)
            if err:
                self._json({"ok": False, "error": err}, 400)
                return
            blob = emblem_render.encode_slot_file_bytes(layers)
            group = body.get("group") or None
            try:
                slot = int(body.get("slot", 0))
            except (TypeError, ValueError):
                slot = 0
            if group:
                meta = read_meta(group) or {}
                if meta.get("source") != "editor":
                    group = None
            name, slot = save_authored_emblem(
                blob, body.get("label"), group=group, slot=slot,
            )
            if body.get("select") or body.get("show"):
                select_emblem(name, slot)
            if body.get("show"):
                write_state("INJECT")
            self._json({"ok": True, "group": name, "slot": slot, "id": f"{name}:{slot}"})

        elif path == "/api/editor/export":
            layers, err = self._prepare_layers(body)
            if err:
                self._json({"ok": False, "error": err}, 400)
                return
            blob = emblem_render.encode_slot_file_bytes(layers)
            self._bytes(blob, "application/octet-stream")

        elif path == "/api/editor/preview":
            layers, err = self._prepare_layers(body)
            if err:
                self._json({"ok": False, "error": err}, 400)
                return
            try:
                size = int(body.get("size") or 512)
            except (TypeError, ValueError):
                size = 512
            size = max(64, min(size, 768))
            png = emblem_render.render_layers_png_bytes(layers, size=size)
            self._bytes(png, "image/png")

        elif path == "/api/deselect":
            clear_selection()
            self._json({"ok": True})

        elif path == "/api/emblems/import":
            import base64
            raw = body.get("data_base64") or body.get("bin_base64") or ""
            try:
                data = base64.b64decode(raw)
            except (ValueError, TypeError):
                self._json({"ok": False, "error": "That is not an emblem file"}, 400)
                return
            try:
                group, slot = import_slot_bytes(data, body.get("label"))
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "group": group, "slot": slot, "id": f"{group}:{slot}"})

        elif path.startswith("/api/emblems/") and path.endswith("/delete"):
            bits = path.split("/")
            if len(bits) == 6:
                group = urllib.parse.unquote(bits[3])
                try:
                    slot = int(bits[4])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "not found"}, 404)
                    return
                sel = read_selection()
                if sel and sel.get("group") == group and int(sel.get("slot", -1)) == slot:
                    clear_selection()
                if delete_emblem(group, slot):
                    self._json({"ok": True})
                    return
            self._json({"ok": False, "error": "not found"}, 404)

        elif path.startswith("/api/emblems/") and path.endswith("/label"):
            bits = path.split("/")
            # /api/emblems/<group>/<slot>/label
            if len(bits) == 6:
                group = urllib.parse.unquote(bits[3])
                slot = bits[4]
                set_emblem_label(group, int(slot), (body.get("label") or "").strip())
                self._json({"ok": True})
                return
            self._json({"ok": False, "error": "bad request"}, 400)

        else:
            self._json({"error": "not found"}, 404)


def make_server(host=None, port=None):
    host = host or config.WEB_HOST
    port = port or config.WEB_PORT
    warm_atlas()
    return http.server.ThreadingHTTPServer((host, port), Handler)
