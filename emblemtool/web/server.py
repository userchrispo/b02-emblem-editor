"""The web control panel: a small JSON API + SSE event stream plus static files
served from web/static/.
"""
import base64
import html
import http.server
import json
import mimetypes
import os
import queue
import struct
import urllib.parse

from .. import config
from ..activity import (
    clear_session_flag,
    notify_subscribers,
    read_activity,
    subscribe,
    unsubscribe,
)
from ..broadcast import clear_selection, read_selection, select_emblem
from ..shapes import render as emblem_render
from ..shapes.atlas import get_shape_atlas, warm_atlas
from ..shapes.shape_id_map import known_ids, list_shapes
from ..shapes.vectorize import vectorize_image_bytes
from ..state import read_state, write_state
from ..storage import (
    delete_emblem,
    get_emblem_label,
    import_slot_bytes,
    list_emblems,
    read_meta,
    save_authored_emblem,
    set_emblem_label,
    slot_path,
    validate_group_name,
)
from ..thumbs import THUMB_SIZE, cached_thumb_png, thumb_etag
from .netinfo import get_lan_ip

STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))

_MODE_TO_INTERNAL = {"off": "PASSTHROUGH", "capture": "CAPTURE", "inject": "INJECT"}
_MODE_TO_PUBLIC = {v: k for k, v in _MODE_TO_INTERNAL.items()}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _status_payload():
    activity = read_activity()
    return {
        "mode": _MODE_TO_PUBLIC.get(read_state(), "off"),
        "selected": read_selection(),
        "last_hit": activity.get("last_hit"),
        "last_capture": activity.get("last_capture"),
        "needs_session_reset": bool(activity.get("needs_session_reset")),
        "events": activity.get("events", []),
    }


def inspect_slot_bytes(raw):
    """Return HTTP header info + decoded 44-byte layer records with byte offsets."""
    sep = raw.find(b"\r\n\r\n")
    http_headers = raw[:sep].decode("latin1", "replace") if sep >= 0 else ""
    body = raw[sep + 4:] if sep >= 0 else raw
    records = []
    for i in range(min(emblem_render.NUM_LAYERS, len(body) // emblem_render.LAYER_SIZE)):
        offset = i * emblem_render.LAYER_SIZE
        rec = body[offset:offset + emblem_render.LAYER_SIZE]
        shape = struct.unpack("<H", rec[0:2])[0]
        if shape == emblem_render.EMPTY_SHAPE:
            continue
        f = struct.unpack("<9f", rec[4:40])
        outlined, flipped = bool(rec[40]), bool(rec[41])
        records.append({
            "index": i,
            "offset": offset,
            "offset_hex": f"0x{offset:03X}",
            "shape": shape,
            "shape_label": known_ids.get(shape, f"unknown/{shape}"),
            "rgba": [round(f[0], 3), round(f[1], 3), round(f[2], 3), round(f[3], 3)],
            "pos": [round(f[4], 4), round(f[5], 4)],
            "scale": [round(f[6], 4), round(f[7], 4)],
            "rot": round(f[8], 2),
            "outlined": outlined,
            "flipped": flipped,
            "raw_hex": rec.hex(),
        })
    return {
        "total_bytes": len(raw),
        "body_bytes": len(body),
        "layer_size": emblem_render.LAYER_SIZE,
        "max_layers": emblem_render.NUM_LAYERS,
        "used_layers": len(records),
        "has_http_headers": sep >= 0,
        "http_headers": http_headers,
        "records": records,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet - proxy.py already logs what matters

    # ---------- security helpers ----------

    def _send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _allowed_hosts(self):
        allowed = set(_LOOPBACK_HOSTS)
        bound = self.server.server_address[0]
        if bound and bound != "0.0.0.0":
            allowed.add(bound)
        else:
            lan = get_lan_ip()
            if lan:
                allowed.add(lan)
        return allowed

    def _extract_host(self, raw_host):
        if not raw_host:
            return ""
        # Handle IPv6 bracketed host or host:port
        parsed = urllib.parse.urlparse(f"http://{raw_host}")
        return (parsed.hostname or "").lower()

    def _check_post_origin(self):
        """Block cross-origin CSRF and DNS-rebinding POST requests."""
        allowed = self._allowed_hosts()
        host_hdr = self.headers.get("Host")
        if host_hdr:
            req_host = self._extract_host(host_hdr)
            if req_host and req_host not in allowed and self.server.server_address[0] != "0.0.0.0":
                return False
        origin = self.headers.get("Origin")
        if origin:
            origin_host = (urllib.parse.urlparse(origin).hostname or "").lower()
            if origin_host not in allowed:
                if host_hdr and origin_host == self._extract_host(host_hdr):
                    return True
                return False
        return True

    def _safe_group_slot(self, raw_group, raw_slot):
        group = validate_group_name(urllib.parse.unquote(raw_group))
        slot = int(raw_slot)
        if slot < 0:
            raise ValueError("negative slot")
        return group, slot

    # ---------- response helpers ----------

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, content_type, code=200, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._send_security_headers()
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
        full = os.path.abspath(os.path.join(STATIC_DIR, rel_path))
        try:
            in_static = os.path.commonpath([STATIC_DIR, full]) == STATIC_DIR
        except ValueError:
            in_static = False
        if not in_static or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js") or full.endswith(".mjs"):
            ctype = "application/javascript; charset=utf-8"
        with open(full, "rb") as f:
            self._bytes(f.read(), ctype)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._send_security_headers()
        self.end_headers()

        q = subscribe()
        try:
            init_payload = json.dumps(_status_payload())
            self.wfile.write(f"event: status\ndata: {init_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event_type, _ = q.get(timeout=15.0)
                    payload = json.dumps(_status_payload())
                    self.wfile.write(f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            unsubscribe(q)
            self.close_connection = True

    # ---------- GET ----------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/api/status":
            self._json(_status_payload())

        elif path == "/api/events":
            self._serve_sse()

        elif path == "/api/emblems":
            self._json(list_emblems())

        elif path == "/api/network-info":
            self._json({
                "lan_ip": get_lan_ip(),
                "proxy_port": config.PROXY_PORT,
                "web_port": config.WEB_PORT,
                "web_host": self.server.server_address[0],
                "allow_ips": sorted(config.PROXY_ALLOW_IPS) if config.PROXY_ALLOW_IPS else [],
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
            body = json.dumps(meta).encode()
            self._bytes(body, "application/json", extra_headers={
                "Cache-Control": "public, max-age=86400",
            })

        elif path == "/api/shapes/atlas.png":
            data, _ = get_shape_atlas()
            self._bytes(data, "image/png", extra_headers={
                "Cache-Control": "public, max-age=86400",
            })

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
            self._bytes(data, "image/png", extra_headers={
                "Cache-Control": "public, max-age=86400",
            })

        elif path.startswith("/api/emblems/") and path.endswith("/layers"):
            bits = path.split("/")
            if len(bits) == 6:
                try:
                    group, slot = self._safe_group_slot(bits[3], bits[4])
                except ValueError:
                    self._json({"error": "invalid emblem id"}, 400)
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

        elif path.startswith("/api/emblems/") and path.endswith("/inspect"):
            bits = path.split("/")
            if len(bits) == 6:
                try:
                    group, slot = self._safe_group_slot(bits[3], bits[4])
                except ValueError:
                    self._json({"error": "invalid emblem id"}, 400)
                    return
                bin_path = slot_path(group, slot)
                if os.path.isfile(bin_path):
                    with open(bin_path, "rb") as f:
                        raw = f.read()
                    info = inspect_slot_bytes(raw)
                    info["group"] = group
                    info["slot"] = slot
                    info["label"] = get_emblem_label(group, slot)
                    self._json(info)
                    return
            self._json({"error": "not found"}, 404)

        elif path.startswith("/api/render/"):
            bits = path.split("/")
            if len(bits) == 5:
                slot_name = bits[4].split("?", 1)[0].removesuffix(".png")
                try:
                    group, slot = self._safe_group_slot(bits[3], slot_name)
                except ValueError:
                    self._json({"error": "invalid emblem id"}, 400)
                    return
                bin_path = slot_path(group, slot)
                if os.path.exists(bin_path):
                    try:
                        etag = thumb_etag(group, slot, THUMB_SIZE)
                        if self.headers.get("If-None-Match") == etag:
                            self.send_response(304)
                            self.send_header("ETag", etag)
                            self.send_header("Cache-Control", "private, max-age=3600")
                            self._send_security_headers()
                            self.end_headers()
                            return
                        body = cached_thumb_png(group, slot, THUMB_SIZE)
                        self._bytes(body, "image/png", extra_headers={
                            "ETag": etag,
                            "Cache-Control": "private, max-age=3600",
                        })
                    except Exception as e:
                        safe_err = html.escape(str(e), quote=True)
                        svg = (
                            '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220">'
                            '<rect width="100%" height="100%" fill="#1a1a1a"/>'
                            '<text x="10" y="30" fill="#e55" font-size="12">render error</text>'
                            f'<text x="10" y="50" fill="#999" font-size="10">{safe_err}</text></svg>'
                        )
                        self._bytes(svg.encode("utf-8"), "image/svg+xml")
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
        if not self._check_post_origin():
            self._json({"ok": False, "error": "forbidden origin"}, 403)
            return

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
            notify_subscribers("mode", {"mode": public_mode})
            self._json({"ok": True})

        elif path == "/api/session-reset":
            clear_session_flag()
            self._json({"ok": True})

        elif path == "/api/select":
            raw_group, raw_slot = body.get("group"), body.get("slot")
            try:
                group = validate_group_name(raw_group)
                slot = int(raw_slot)
                target = slot_path(group, slot)
            except (TypeError, ValueError):
                self._json({"ok": False, "error": "not found"}, 404)
                return
            if not os.path.exists(target):
                self._json({"ok": False, "error": "not found"}, 404)
                return
            select_emblem(group, slot)
            notify_subscribers("select", {"group": group, "slot": slot})
            self._json({"ok": True})

        elif path == "/api/editor/save":
            layers, err = self._prepare_layers(body)
            if err:
                self._json({"ok": False, "error": err}, 400)
                return
            blob = emblem_render.encode_slot_file_bytes(layers)
            group = body.get("group") or None
            if group is not None:
                try:
                    group = validate_group_name(group)
                except ValueError:
                    self._json({"ok": False, "error": "invalid group name"}, 400)
                    return
            try:
                slot = int(body.get("slot", 0))
                if slot < 0:
                    raise ValueError("negative slot")
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
            notify_subscribers("library", {"group": name, "slot": slot})
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

        elif path == "/api/editor/vectorize":
            raw_b64 = body.get("image_base64") or ""
            if isinstance(raw_b64, str) and "," in raw_b64 and raw_b64.startswith("data:"):
                raw_b64 = raw_b64.split(",", 1)[1]
            try:
                raw_img = base64.b64decode(raw_b64)
            except (ValueError, TypeError):
                self._json({"ok": False, "error": "Invalid base64 image"}, 400)
                return
            if not raw_img or len(raw_img) > 8 * 1024 * 1024:
                self._json({"ok": False, "error": "Image is empty or exceeds 8 MiB limit"}, 400)
                return
            try:
                max_layers = int(body.get("max_layers") or 32)
            except (TypeError, ValueError):
                max_layers = 32
            quality = str(body.get("quality") or "balanced")
            shape_set = str(body.get("shape_set") or "geometric")
            fit = str(body.get("fit") or "cover")
            allow_alpha = bool(body.get("allow_alpha", True))
            try:
                res = vectorize_image_bytes(
                    raw_img,
                    max_layers=max_layers,
                    quality=quality,
                    shape_set=shape_set,
                    fit=fit,
                    allow_alpha=allow_alpha,
                )
            except Exception as e:
                self._json({"ok": False, "error": f"Could not convert image: {e}"}, 400)
                return
            self._json({"ok": True, **res})

        elif path == "/api/deselect":
            clear_selection()
            notify_subscribers("select", None)
            self._json({"ok": True})

        elif path == "/api/emblems/import":
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
            notify_subscribers("library", {"group": group, "slot": slot})
            self._json({"ok": True, "group": group, "slot": slot, "id": f"{group}:{slot}"})

        elif path.startswith("/api/emblems/") and path.endswith("/delete"):
            bits = path.split("/")
            if len(bits) == 6:
                try:
                    group, slot = self._safe_group_slot(bits[3], bits[4])
                except ValueError:
                    self._json({"ok": False, "error": "invalid emblem id"}, 400)
                    return
                sel = read_selection()
                if sel and sel.get("group") == group and int(sel.get("slot", -1)) == slot:
                    clear_selection()
                if delete_emblem(group, slot):
                    notify_subscribers("library", {"deleted": f"{group}:{slot}"})
                    self._json({"ok": True})
                    return
            self._json({"ok": False, "error": "not found"}, 404)

        elif path.startswith("/api/emblems/") and path.endswith("/label"):
            bits = path.split("/")
            if len(bits) == 6:
                try:
                    group, slot = self._safe_group_slot(bits[3], bits[4])
                except ValueError:
                    self._json({"ok": False, "error": "invalid emblem id"}, 400)
                    return
                set_emblem_label(group, slot, (body.get("label") or "").strip())
                notify_subscribers("library", {"group": group, "slot": slot})
                self._json({"ok": True})
                return
            self._json({"ok": False, "error": "bad request"}, 400)

        else:
            self._json({"error": "not found"}, 404)


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


def make_server(host=None, port=None):
    host = host if host is not None else config.WEB_HOST
    port = port if port is not None else config.WEB_PORT
    warm_atlas()
    return _HTTPServer((host, port), Handler)
