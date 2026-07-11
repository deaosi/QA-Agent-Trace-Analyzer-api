import os
import json
import sys
import tempfile
import unittest

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from shared_store import SharedStore


class SharedStoreTests(unittest.TestCase):
    def test_merge_traces_deduplicates_by_trace_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            first = store.merge_traces("shop-1", [{"id": "trace-1", "question": "first"}], "alice")
            second = store.merge_traces("shop-1", [{"id": "trace-1", "question": "updated"}, {"id": "trace-2"}], "bob")

            self.assertEqual(first["inserted"], 1)
            self.assertEqual(second["inserted"], 1)
            self.assertEqual(second["updated"], 1)
            self.assertEqual(store.count_traces("shop-1"), 2)
            traces = {item["id"]: item for item in store.load_traces("shop-1")}
            self.assertEqual(traces["trace-1"]["question"], "updated")

    def test_merge_never_deletes_other_shared_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            store.merge_traces("shop-1", [{"id": "trace-1"}, {"id": "trace-2"}], "alice")
            store.merge_traces("shop-1", [{"id": "trace-1", "updated": True}], "bob", overwrite=True)

            self.assertEqual(store.count_traces("shop-1"), 2)
            traces = {item["id"]: item for item in store.load_traces("shop-1")}
            self.assertTrue(traces["trace-1"]["updated"])
            self.assertIn("trace-2", traces)

    def test_shared_analysis_and_issue_status_are_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            store.save_analysis("shop-1", {"issueWorkbench": [{"id": "issue-1"}]})
            store.set_issue_status("shop-1", "issue-1", "已确认问题")

            self.assertEqual(store.load_analysis("shop-1")["issueWorkbench"][0]["id"], "issue-1")
            self.assertEqual(store.load_issue_status()["shop-1:issue-1"], "已确认问题")

    def test_delete_shop_removes_all_shared_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            store.upsert_shop("shop-1", "Demo")
            store.merge_traces("shop-1", [{"id": "trace-1"}], "alice")
            store.save_analysis("shop-1", {"ok": True})
            store.set_issue_status("shop-1", "issue-1", "待处理")
            store.set_issue_feedback("shop-1", "issue-1", "correct", updated_by="alice")

            store.delete_shop("shop-1")

            self.assertNotIn("shop-1", store.list_shops())
            self.assertEqual(store.load_traces("shop-1"), [])
            self.assertEqual(store.load_analysis("shop-1"), {})
            self.assertNotIn("shop-1:issue-1", store.load_issue_status())
            self.assertEqual(store.load_issue_feedback("shop-1"), {})

    def test_issue_feedback_is_persistent_and_updateable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            first = store.set_issue_feedback(
                "shop-1", "issue-1", "needs_review", note="check evidence", updated_by="alice"
            )
            second = store.set_issue_feedback(
                "shop-1", "issue-1", "false_positive", note="valid reply", updated_by="bob"
            )

            loaded = store.load_issue_feedback("shop-1")["issue-1"]
            self.assertEqual(first["verdict"], "needs_review")
            self.assertEqual(second["verdict"], "false_positive")
            self.assertEqual(loaded["note"], "valid reply")
            self.assertEqual(loaded["updatedBy"], "bob")

    def test_migrate_legacy_json_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            shops_file = os.path.join(tmp, ".shops.json")
            status_file = os.path.join(tmp, ".issue_status.json")
            with open(shops_file, "w", encoding="utf-8") as handle:
                json.dump({"shop-1": {"name": "Legacy shop", "total": 1}}, handle)
            with open(status_file, "w", encoding="utf-8") as handle:
                json.dump({"shop-1:issue-1": "待处理"}, handle, ensure_ascii=False)
            with open(os.path.join(tmp, "traces_shop-1.json"), "w", encoding="utf-8") as handle:
                json.dump([{"id": "trace-1", "question": "hello"}], handle)

            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            def load_json(path, default=None):
                if not os.path.exists(path):
                    return default
                with open(path, encoding="utf-8") as handle:
                    return json.load(handle)
            store.migrate_legacy(tmp, shops_file, status_file, load_json)
            store.migrate_legacy(tmp, shops_file, status_file, load_json)

            self.assertEqual(store.list_shops()["shop-1"]["name"], "Legacy shop")
            self.assertEqual(store.count_traces("shop-1"), 1)
            self.assertEqual(store.load_issue_status()["shop-1:issue-1"], "待处理")


if __name__ == "__main__":
    unittest.main()
