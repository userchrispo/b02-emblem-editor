"""Security regression, binary inspector, CLI, and static demo build tests."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import run as cli_run
from emblemtool import config, storage
from emblemtool.shapes.render import encode_slot_file_bytes
from emblemtool.web.server import make_server
from scripts.build_demo import build_demo


class SecurityAndInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls._old_saved = config.SAVED_DIR
        cls._old_state = config.STATE_FILE
        cls._old_act = config.ACTIVITY_FILE
        config.configure_data_dir(cls.tmp)
        cls.server = make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        storage.wait_warm_thumbs()
        cls.server.shutdown()
        cls.server.server_close()
        config.SAVED_DIR = cls._old_saved
        config.STATE_FILE = cls._old_state
        config.ACTIVITY_FILE = cls._old_act
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, path, *, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = dict(headers or {})
        if body is not None:
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_security_headers_present(self):
        status, headers, _ = self._req("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_path_traversal_blocked_across_endpoints(self):
        bad_groups = ["..", "%2e%2e", "..%2fetc", "_active", "foo/bar"]
        for bad in bad_groups:
            s1, _, _ = self._req(f"/api/emblems/{bad}/0/layers")
            self.assertIn(s1, (400, 404), f"layers failed for {bad}")
            s2, _, _ = self._req(f"/api/emblems/{bad}/0/inspect")
            self.assertIn(s2, (400, 404), f"inspect failed for {bad}")
            s3, _, _ = self._req(f"/api/render/{bad}/0.png")
            self.assertIn(s3, (400, 404), f"render failed for {bad}")
            s4, _, _ = self._req(f"/api/emblems/{bad}/0/delete", method="POST", body={})
            self.assertIn(s4, (400, 404), f"delete failed for {bad}")
            s5, _, _ = self._req(f"/api/emblems/{bad}/0/label", method="POST", body={"label": "x"})
            self.assertIn(s5, (400, 404), f"label failed for {bad}")

        s6, _, _ = self._req("/api/select", method="POST", body={"group": "../outside", "slot": 0})
        self.assertEqual(s6, 404)
        s7, _, _ = self._req("/api/editor/save", method="POST", body={"group": "../outside", "layers": []})
        self.assertEqual(s7, 400)

    def test_cross_origin_post_rejected(self):
        status, _, _ = self._req(
            "/api/mode",
            method="POST",
            body={"mode": "capture"},
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(status, 403)

    def test_svg_render_error_is_escaped(self):
        blob = encode_slot_file_bytes([{
            "index": 0, "shape": 192,
            "r": 1, "g": 1, "b": 1, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0,
            "outlined": False, "flipped": False,
        }])
        group, slot = storage.save_authored_emblem(blob, "XSS Check")
        with mock.patch(
            "emblemtool.web.server.cached_thumb_png",
            side_effect=RuntimeError("<script>alert(1)</script>"),
        ):
            status, headers, body = self._req(f"/api/render/{group}/{slot}.png")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", headers.get("Content-Type", ""))
        self.assertNotIn(b"<script>", body)
        self.assertIn(b"&lt;script&gt;", body)

    def test_binary_inspector_endpoint(self):
        blob = encode_slot_file_bytes([{
            "index": 0, "shape": 178,
            "r": 1, "g": 0.5, "b": 0, "a": 1,
            "x": 0.1, "y": -0.1, "sx": -1, "sy": -1, "rot": 30,
            "outlined": True, "flipped": True,
        }])
        group, slot = storage.save_authored_emblem(blob, "Shield Inspect")
        status, _, body = self._req(f"/api/emblems/{group}/{slot}/inspect")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["body_bytes"], 1408)
        self.assertEqual(data["used_layers"], 1)
        rec = data["records"][0]
        self.assertEqual(rec["offset_hex"], "0x000")
        self.assertEqual(rec["shape"], 178)
        self.assertTrue(rec["outlined"])
        self.assertTrue(rec["flipped"])

        # Selecting the emblem creates _active; /api/emblems must still succeed
        s_sel, _, _ = self._req("/api/select", method="POST", body={"group": group, "slot": slot})
        self.assertEqual(s_sel, 200)
        s_list, _, list_body = self._req("/api/emblems")
        self.assertEqual(s_list, 200)
        self.assertTrue(any(e["group"] == group for e in json.loads(list_body)))

    def test_cli_parse_args(self):
        args = cli_run.parse_args([
            "--proxy-port", "9080",
            "--web-port", "9090",
            "--web-host", "127.0.0.1",
            "--allow-ip", "192.168.1.42",
            "--data-dir", "/tmp/bo2-data",
            "--no-browser",
        ])
        self.assertEqual(args.proxy_port, 9080)
        self.assertEqual(args.web_port, 9090)
        self.assertEqual(args.web_host, "127.0.0.1")
        self.assertEqual(args.allow_ip, ["192.168.1.42"])
        self.assertEqual(args.data_dir, "/tmp/bo2-data")
        self.assertTrue(args.no_browser)

    def test_static_demo_build(self):
        out = tempfile.mkdtemp()
        try:
            build_demo(out)
            for rel in (
                "index.html",
                "editor.js",
                "editor/binary.js",
                "editor/canvas.js",
                "demo-data/atlas.png",
                "demo-data/atlas.json",
                "demo-data/shapes.json",
                "demo-data/presets.json",
            ):
                self.assertTrue(os.path.isfile(os.path.join(out, rel)), f"missing {rel}")
        finally:
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
