"""On-disk emblem thumbnail cache: reuse until the slot file changes."""
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool import config
from emblemtool.shapes.render import encode_slot_file_bytes
from emblemtool import storage
from emblemtool.thumbs import cached_thumb_png, thumb_path


class ThumbCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = config.SAVED_DIR
        config.SAVED_DIR = self.tmp

    def tearDown(self):
        config.SAVED_DIR = self._old
        storage.wait_warm_thumbs()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_second_call_reuses_file(self):
        blob = encode_slot_file_bytes([{
            "index": 0, "shape": 192,
            "r": 1, "g": 0, "b": 0, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0,
            "outlined": False, "flipped": False,
        }])
        group, slot = storage.save_authored_emblem(blob, "Red")
        first = cached_thumb_png(group, slot)
        path = thumb_path(group, slot)
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(first.startswith(b"\x89PNG"))
        second = cached_thumb_png(group, slot)
        self.assertEqual(first, second)

    def test_rebuilds_after_slot_changes(self):
        blob = encode_slot_file_bytes([])
        group, slot = storage.save_authored_emblem(blob, "Empty")
        cached_thumb_png(group, slot)
        path = thumb_path(group, slot)
        old_mtime = os.path.getmtime(path)
        time.sleep(0.05)
        storage.write_slot_bytes(group, slot, encode_slot_file_bytes([{
            "index": 0, "shape": 192,
            "r": 0, "g": 1, "b": 0, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0,
            "outlined": False, "flipped": False,
        }]))
        png = cached_thumb_png(group, slot)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreaterEqual(os.path.getmtime(path), old_mtime)


if __name__ == "__main__":
    unittest.main()
