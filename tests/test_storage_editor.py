"""Save path for emblems authored in the PC editor."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool import config
from emblemtool.shapes.render import encode_slot_file_bytes, parse_slot_file
from emblemtool import storage


class SaveAuthored(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = config.SAVED_DIR
        config.SAVED_DIR = self.tmp

    def tearDown(self):
        config.SAVED_DIR = self._old
        shutil.rmtree(self.tmp)

    def test_creates_editor_group_and_roundtrips(self):
        layers = [{
            "index": 0, "shape": 192,
            "r": 0.2, "g": 0.4, "b": 0.8, "a": 1,
            "x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0,
            "outlined": False, "flipped": False,
        }]
        blob = encode_slot_file_bytes(layers)
        group, slot = storage.save_authored_emblem(blob, "Blue circle")
        self.assertEqual(slot, 0)
        meta = storage.read_meta(group)
        self.assertEqual(meta["source"], "editor")
        self.assertEqual(meta["labels"]["0"], "Blue circle")
        parsed = parse_slot_file(storage.slot_path(group, slot))
        self.assertEqual(parsed[0]["shape"], 192)
        emblems = storage.list_emblems()
        self.assertEqual(emblems[0]["source"], "editor")
        self.assertEqual(emblems[0]["label"], "Blue circle")

    def test_overwrite_keeps_group(self):
        blob = encode_slot_file_bytes([])
        group, _ = storage.save_authored_emblem(blob, "First")
        group2, _ = storage.save_authored_emblem(blob, "Second", group=group, slot=0)
        self.assertEqual(group, group2)
        self.assertEqual(storage.get_emblem_label(group, 0), "Second")
        self.assertEqual(len(storage.list_groups()), 1)


if __name__ == "__main__":
    unittest.main()
