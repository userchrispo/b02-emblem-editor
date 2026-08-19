"""Smoke tests for the editor HTTP API."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool import config
from emblemtool.web.server import make_server


class EditorAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._act_tmp = tempfile.mkdtemp()
        cls._saved_tmp = tempfile.mkdtemp()
        cls._old_activity = config.ACTIVITY_FILE
        cls._old_saved = config.SAVED_DIR
        config.ACTIVITY_FILE = os.path.join(cls._act_tmp, "activity.json")
        config.SAVED_DIR = cls._saved_tmp
        cls.server = make_server("127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        config.ACTIVITY_FILE = cls._old_activity
        config.SAVED_DIR = cls._old_saved
        shutil.rmtree(cls._act_tmp, ignore_errors=True)
        shutil.rmtree(cls._saved_tmp, ignore_errors=True)

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

    def test_status_includes_activity_and_session_reset(self):
        status, _, body = self._get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("last_hit", data)
        self.assertIn("last_capture", data)
        self.assertIn("needs_session_reset", data)
        from emblemtool import activity
        activity.record_event("show", slot=1)
        status, _, body = self._get("/api/status")
        self.assertTrue(json.loads(body)["needs_session_reset"])
        _, _, posted = self._post("/api/session-reset", {})
        self.assertTrue(json.loads(posted)["ok"])
        status, _, body = self._get("/api/status")
        self.assertFalse(json.loads(body)["needs_session_reset"])

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

    def test_import_and_delete_bin(self):
        import base64
        layers = [{
            "index": 0, "shape": 192,
            "r": 1, "g": 1, "b": 1, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0,
            "outlined": False, "flipped": False,
        }]
        status, _, blob = self._post("/api/editor/export", {"layers": layers})
        self.assertEqual(status, 200)
        status, _, body = self._post("/api/emblems/import", {
            "data_base64": base64.b64encode(blob).decode("ascii"),
            "label": "Imported circle",
        })
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        status, _, listed = self._get("/api/emblems")
        emblems = json.loads(listed)
        self.assertEqual(len(emblems), 1)
        self.assertEqual(emblems[0]["label"], "Imported circle")
        self.assertEqual(emblems[0]["risks"], [])
        group, slot = data["group"], data["slot"]
        status, _, deleted = self._post(
            f"/api/emblems/{group}/{slot}/delete", {},
        )
        self.assertEqual(status, 200)
        status, _, listed = self._get("/api/emblems")
        self.assertEqual(json.loads(listed), [])

    def test_import_rejects_garbage(self):
        import base64
        try:
            self._post("/api/emblems/import", {
                "data_base64": base64.b64encode(b"nope").decode("ascii"),
            })
            self.fail("expected HTTP error")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)


if __name__ == "__main__":
    unittest.main()
