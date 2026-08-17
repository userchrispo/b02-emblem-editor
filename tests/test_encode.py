"""Round-trip tests for the emblem .bin writer used by the PC editor."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool.shapes.render import (
    EMPTY_SHAPE,
    NUM_LAYERS,
    compact_layers,
    encode_layers,
    parse_slot_bytes,
    strip_http,
    wrap_slot_http,
)


class EncodeRoundTrip(unittest.TestCase):
    def test_empty_is_1408_empty_slots(self):
        body = encode_layers([])
        self.assertEqual(len(body), 32 * 44)
        self.assertEqual(parse_slot_bytes(body), [])

    def test_layer_survives_parse(self):
        layers = [{
            "index": 3,
            "shape": 196,
            "r": 1.0, "g": 0.5, "b": 0.0, "a": 0.8,
            "x": 0.1, "y": -0.2,
            "sx": -1.0, "sy": -0.5,
            "rot": 45.0,
            "outlined": True,
            "flipped": True,
        }]
        parsed = parse_slot_bytes(encode_layers(layers))
        self.assertEqual(len(parsed), 1)
        got = parsed[0]
        self.assertEqual(got["index"], 3)
        self.assertEqual(got["shape"], 196)
        self.assertTrue(got["outlined"])
        self.assertTrue(got["flipped"])
        for key in ("r", "g", "b", "a", "x", "y", "sx", "sy", "rot"):
            self.assertAlmostEqual(got[key], layers[0][key], places=5)

    def test_http_wrap_strips_back_to_body(self):
        body = encode_layers([{"index": 0, "shape": 192, "r": 1, "g": 1, "b": 1, "a": 1,
                               "x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0,
                               "outlined": False, "flipped": False}])
        wrapped = wrap_slot_http(body)
        self.assertTrue(wrapped.startswith(b"HTTP/1.1 200 OK"))
        self.assertEqual(strip_http(wrapped), body)
        self.assertEqual(parse_slot_bytes(wrapped)[0]["shape"], 192)

    def test_compact_reindexes_back_to_front(self):
        compact = compact_layers([
            {"index": 9, "shape": 10},
            {"index": 2, "shape": 11},
            {"index": 2, "shape": EMPTY_SHAPE},
        ])
        self.assertEqual([L["index"] for L in compact], [0, 1])
        self.assertEqual([L["shape"] for L in compact], [11, 10])

    def test_later_duplicate_index_wins(self):
        body = encode_layers([
            {"index": 1, "shape": 10, "r": 1, "g": 0, "b": 0, "a": 1,
             "x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0},
            {"index": 1, "shape": 20, "r": 0, "g": 1, "b": 0, "a": 1,
             "x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0},
        ])
        parsed = parse_slot_bytes(body)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["shape"], 20)

    def test_clamps_color_and_rejects_bad_index(self):
        parsed = parse_slot_bytes(encode_layers([
            {"index": 99, "shape": 1, "r": 9, "g": -1, "b": 0.5, "a": 2,
             "x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0},
            {"index": 0, "shape": 1, "r": 9, "g": -1, "b": 0.5, "a": 2,
             "x": 0, "y": 0, "sx": 0, "sy": 0, "rot": 0},
        ]))
        self.assertEqual(len(parsed), 1)
        self.assertAlmostEqual(parsed[0]["r"], 1.0)
        self.assertAlmostEqual(parsed[0]["g"], 0.0)
        self.assertAlmostEqual(parsed[0]["a"], 1.0)

    def test_slot_count_constant(self):
        self.assertEqual(NUM_LAYERS, 32)


if __name__ == "__main__":
    unittest.main()
