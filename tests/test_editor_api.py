"""Smoke tests for the editor HTTP API."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool.web.server import make_server


class EditorAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def _post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers.get("Content-Type"), r.read()

    def test_editor_page(self):
        status, ctype, body = self._get("/editor.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Emblem editor", body)

    def test_shapes_list_and_png(self):
        status, _, body = self._get("/api/shapes")
        self.assertEqual(status, 200)
        shapes = json.loads(body)
        self.assertGreater(len(shapes), 50)
        sid = shapes[0]["id"]
        status, ctype, png = self._get(f"/api/shapes/{sid}.png")
        self.assertEqual(status, 200)
        self.assertIn("png", ctype)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_shape_atlas(self):
        status, _, body = self._get("/api/shapes")
        shapes = json.loads(body)
        status, ctype, meta_raw = self._get("/api/shapes/atlas.json")
        self.assertEqual(status, 200)
        meta = json.loads(meta_raw)
        self.assertEqual(len(meta["tiles"]), len(shapes))
        self.assertIn(str(shapes[0]["id"]), meta["tiles"])
        status, ctype, png = self._get("/api/shapes/atlas.png")
        self.assertEqual(status, 200)
        self.assertIn("png", ctype)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 10_000)

    def test_preview_and_export(self):
        layers = [{
            "index": 0, "shape": 192,
            "r": 1, "g": 0.5, "b": 0, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 20,
            "outlined": False, "flipped": False,
        }]
        status, ctype, png = self._post("/api/editor/preview", {"layers": layers, "size": 128})
        self.assertEqual(status, 200)
        self.assertIn("png", ctype)
        self.assertTrue(png.startswith(b"\x89PNG"))
        status, _, blob = self._post("/api/editor/export", {"layers": layers})
        self.assertEqual(status, 200)
        self.assertTrue(blob.startswith(b"HTTP/1.1 200 OK"))


if __name__ == "__main__":
    unittest.main()
