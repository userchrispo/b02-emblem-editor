"""Activity log for PS5 emblem-endpoint hits."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool import activity, config


class ActivityLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = config.ACTIVITY_FILE
        config.ACTIVITY_FILE = os.path.join(self.tmp, "activity.json")

    def tearDown(self):
        config.ACTIVITY_FILE = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_file_is_empty(self):
        data = activity.read_activity()
        self.assertEqual(data["seq"], 0)
        self.assertIsNone(data["last_hit"])
        self.assertFalse(data["needs_session_reset"])

    def test_passthrough_is_a_hit_but_not_a_session_lock(self):
        activity.record_event("passthrough", slot=504)
        data = activity.read_activity()
        self.assertEqual(data["seq"], 1)
        self.assertEqual(data["last_hit"]["kind"], "passthrough")
        self.assertEqual(data["last_hit"]["slot"], 504)
        self.assertFalse(data["needs_session_reset"])
        self.assertIsNone(data["last_capture"])

    def test_capture_records_group(self):
        activity.record_event("capture", slot=2, group="003")
        data = activity.read_activity()
        self.assertEqual(data["last_capture"]["group"], "003")
        self.assertEqual(data["last_capture"]["slot"], 2)
        self.assertEqual(data["last_capture"]["seq"], data["seq"])
        self.assertFalse(data["needs_session_reset"])

    def test_show_sets_session_flag_cache_check_does_not(self):
        activity.record_event("show-cache-check", slot=1)
        self.assertFalse(activity.read_activity()["needs_session_reset"])
        activity.record_event("show", slot=1)
        self.assertTrue(activity.read_activity()["needs_session_reset"])
        activity.clear_session_flag()
        self.assertFalse(activity.read_activity()["needs_session_reset"])
        self.assertIsNotNone(activity.read_activity()["last_hit"])


if __name__ == "__main__":
    unittest.main()
