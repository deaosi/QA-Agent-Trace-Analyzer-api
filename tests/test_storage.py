import json
import os
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from storage import load_json, save_json


class StorageTests(unittest.TestCase):
    def test_load_json_returns_default_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.json")
            self.assertEqual(load_json(path, []), [])

    def test_load_json_returns_default_when_json_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            self.assertEqual(load_json(path, {"fallback": True}), {"fallback": True})

    def test_save_json_writes_readable_utf8_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            save_json(path, {"name": "测试", "items": [1, 2]})
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), {"name": "测试", "items": [1, 2]})

    def test_save_json_replaces_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            save_json(path, {"version": 1})
            save_json(path, {"version": 2})
            self.assertEqual(load_json(path), {"version": 2})
            leftovers = [name for name in os.listdir(tmp) if name.startswith(".data.json.")]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
