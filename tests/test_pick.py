"""Pixel picking must ignore transparent parts of a layer's bounding box."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool.shapes.atlas import atlas_alpha, get_shape_atlas
from emblemtool.shapes.pick import ALPHA_MIN, hit_layer_index, layer_uv


class LayerUV(unittest.TestCase):
    def test_centre_is_half(self):
        layer = {"x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0, "flipped": False}
        self.assertEqual(layer_uv(layer, 0, 0), (0.5, 0.5))

    def test_outside_box_is_none(self):
        layer = {"x": 0, "y": 0, "sx": -1, "sy": -1, "rot": 0, "flipped": False}
        self.assertIsNone(layer_uv(layer, 0.4, 0))

    def test_clockwise_90_maps_right_to_up(self):
        layer = {"x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 90, "flipped": False}
        u, v = layer_uv(layer, 0.1, 0)
        self.assertAlmostEqual(u, 0.5)
        self.assertAlmostEqual(v, 0.4)


class PixelPick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        get_shape_atlas()

    def test_circle_centre_is_solid_corner_is_empty(self):
        self.assertGreater(atlas_alpha(192, 0.5, 0.5), 200)
        self.assertLess(atlas_alpha(192, 0.95, 0.95), ALPHA_MIN)

    def test_click_through_empty_pixels_of_front_layer(self):
        diamond = {
            "index": 0, "shape": 194,
            "x": 0.4, "y": 0.4, "sx": -2, "sy": -2,
            "rot": 0, "flipped": False,
        }
        circle = {
            "index": 1, "shape": 192,
            "x": 0, "y": 0, "sx": 0, "sy": 0,
            "rot": 0, "flipped": False,
        }
        layers = [diamond, circle]
        self.assertEqual(hit_layer_index(layers, 0.4, 0.4, atlas_alpha), 0)
        self.assertEqual(hit_layer_index(layers, 0, 0, atlas_alpha), 1)
        self.assertEqual(hit_layer_index(layers, -0.48, -0.48, atlas_alpha), -1)


if __name__ == "__main__":
    unittest.main()
