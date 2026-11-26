import base64
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from emblemtool import config
from emblemtool.shapes.render import NUM_LAYERS, encode_slot_file_bytes, parse_slot_bytes
from emblemtool.shapes.unlocks import layer_risks
from emblemtool.shapes.vectorize import vectorize_image
from emblemtool.web import server as web_server


def _make_target_image():
    """Simple high-contrast geometric emblem target (red circle on dark blue with gold bar)."""
    img = Image.new("RGB", (128, 128), (18, 28, 64))
    draw = ImageDraw.Draw(img)
    draw.ellipse((24, 24, 104, 104), fill=(220, 48, 42))
    draw.rectangle((40, 56, 88, 72), fill=(250, 204, 21))
    return img


class VectorizeTests(unittest.TestCase):
    def test_vectorize_improves_similarity_and_uses_unlocked_shapes(self):
        img = _make_target_image()
        res = vectorize_image(img, max_layers=12, quality="fast", shape_set="geometric", seed=42)
        layers = res["layers"]
        self.assertGreaterEqual(len(layers), 2)
        self.assertLessEqual(len(layers), 12)
        self.assertGreater(res["similarity"], 80.0)
        # Must never introduce locked ranks/weapon shapes
        self.assertEqual(layer_risks(layers), [])
        # Must round-trip cleanly through BO2's 1408-byte binary encoder
        blob = encode_slot_file_bytes(layers)
        parsed = parse_slot_bytes(blob)
        self.assertEqual(len(parsed), len(layers))

    def test_vectorize_caps_at_32_layers(self):
        img = _make_target_image()
        res = vectorize_image(img, max_layers=99, quality="fast", shape_set="basic", seed=7)
        self.assertLessEqual(len(res["layers"]), NUM_LAYERS)

    def test_api_editor_vectorize_endpoint(self):
        tmp = tempfile.mkdtemp(prefix="bo2-vec-api-")
        orig_saved = config.SAVED_DIR
        orig_state = config.STATE_FILE
        orig_activity = config.ACTIVITY_FILE
        config.configure_data_dir(tmp)
        srv = web_server.make_server("127.0.0.1", 0)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            buf = io.BytesIO()
            _make_target_image().save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            payload = json.dumps({
                "image_base64": f"data:image/png;base64,{b64}",
                "max_layers": 8,
                "quality": "fast",
                "shape_set": "basic",
            }).encode("utf-8")
            conn.request(
                "POST",
                "/api/editor/vectorize",
                body=payload,
                headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
            )
            resp = conn.getresponse()
            data = json.loads(resp.read().decode("utf-8"))
            conn.close()

            self.assertEqual(resp.status, 200)
            self.assertTrue(data["ok"])
            self.assertGreaterEqual(len(data["layers"]), 2)
            self.assertLessEqual(len(data["layers"]), 8)
            self.assertGreater(data["similarity"], 75.0)

            # Rejects garbage base64
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request(
                "POST",
                "/api/editor/vectorize",
                body=json.dumps({"image_base64": "not-an-image"}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"},
            )
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 400)
        finally:
            srv.shutdown()
            srv.server_close()
            config.SAVED_DIR = orig_saved
            config.STATE_FILE = orig_state
            config.ACTIVITY_FILE = orig_activity
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
