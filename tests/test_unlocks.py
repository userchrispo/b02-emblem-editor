"""Category-based unlock warnings."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool.shapes.unlocks import layer_risks


class Unlocks(unittest.TestCase):
    def test_tools_circle_is_clean(self):
        self.assertEqual(layer_risks([{"shape": 192}]), [])

    def test_rank_and_weapon(self):
        self.assertEqual(layer_risks([{"shape": 198}]), ["ranks"])
        self.assertEqual(layer_risks([{"shape": 0}]), ["weapons"])
        self.assertEqual(
            set(layer_risks([{"shape": 198}, {"shape": 0}])),
            {"ranks", "weapons"},
        )

    def test_unknown_shape_is_ignored(self):
        self.assertEqual(layer_risks([{"shape": 99999}]), [])


if __name__ == "__main__":
    unittest.main()
