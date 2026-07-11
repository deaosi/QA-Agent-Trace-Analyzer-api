import os
import sys
import tempfile
import unittest
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from shared_store import SharedStore
import app as qa_app


class SharedAiTaskStoreTests(unittest.TestCase):
    def test_task_lifecycle_and_result_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            task_id = store.create_ai_task("shop-1", username="alice", issue_limit=10)

            task = store.get_ai_task(task_id)
            self.assertEqual(task["status"], "queued")
            self.assertEqual(task["issueLimit"], 10)
            self.assertFalse(task["cancelRequested"])

            store.update_ai_task(task_id, status="succeeded", progress=100, stage="分析完成", result={"summary": "ok"})
            task = store.get_ai_task(task_id)
            self.assertEqual(task["result"], {"summary": "ok"})
            self.assertEqual(task["progress"], 100)

            cached = store.find_cached_ai_result(task["cacheKey"])
            self.assertIsNone(cached)

    def test_cancel_and_cache_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            task_id = store.create_ai_task(
                "shop-1",
                username="alice",
                cache_key="cache-1",
                result={"summary": "cached"},
                status="succeeded",
                progress=100,
            )
            cached = store.find_cached_ai_result("cache-1")
            self.assertEqual(cached["taskId"], task_id)
            self.assertEqual(cached["result"]["summary"], "cached")

            running_id = store.create_ai_task("shop-1", username="alice", status="running")
            self.assertTrue(store.request_ai_task_cancel(running_id))
            self.assertTrue(store.get_ai_task(running_id)["cancelRequested"])
            self.assertFalse(store.request_ai_task_cancel(task_id))

    def test_shop_delete_removes_ai_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            task_id = store.create_ai_task("shop-1", username="alice")
            store.delete_shop("shop-1")
            self.assertIsNone(store.get_ai_task(task_id))


class AiRetryPolicyTests(unittest.TestCase):
    def test_only_transient_errors_are_retryable(self):
        self.assertTrue(qa_app.is_retryable_ai_error("429 Client Error"))
        self.assertTrue(qa_app.is_retryable_ai_error("503 Service Unavailable"))
        self.assertTrue(qa_app.is_retryable_ai_error("Read timed out"))
        self.assertFalse(qa_app.is_retryable_ai_error("401 Client Error"))
        self.assertFalse(qa_app.is_retryable_ai_error("invalid API key"))

    def test_full_url_mode_does_not_append_chat_completions_path(self):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        def fake_post(url, json, headers, timeout):
            captured["url"] = url
            return FakeResponse()

        cfg = {
            "apiKey": "sk-test",
            "baseUrl": "https://example.test/v1/chat/completions",
            "model": "test-model",
            "fullUrl": True,
            "temperature": 0.3,
            "maxTokens": 4096,
            "timeoutSeconds": 60,
        }
        with patch.object(qa_app.requests, "post", side_effect=fake_post):
            text, error = qa_app._call_llm("system", "user", cfg)
        self.assertIsNone(error)
        self.assertEqual(text, "ok")
        self.assertEqual(captured["url"], cfg["baseUrl"])

    def test_transient_provider_failure_retries_but_bad_request_does_not(self):
        cfg = {
            "apiKey": "sk-test",
            "baseUrl": "https://example.test/v1",
            "model": "test-model",
            "temperature": 0.3,
            "maxTokens": 4096,
            "timeoutSeconds": 60,
        }
        valid_json = '{"summary":"ok"}'
        with patch.object(qa_app.time, "sleep"), patch.object(
            qa_app, "_call_llm", side_effect=[(None, "429 Client Error"), (None, "503 Service Unavailable"), (valid_json, None)]
        ) as call:
            result, error = qa_app.run_ai_llm_analysis("system", "user", cfg, {}, {"total": 0}, 10)
        self.assertIsNone(error)
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(call.call_count, 3)

        with patch.object(qa_app, "_call_llm", return_value=(None, "400 Client Error")) as call:
            result, error = qa_app.run_ai_llm_analysis("system", "user", cfg, {}, {"total": 0}, 10)
        self.assertIsNone(result)
        self.assertIn("400 Client Error", error)
        self.assertEqual(call.call_count, 1)

    def test_invalid_json_gets_one_repair_attempt(self):
        cfg = {
            "apiKey": "sk-test",
            "baseUrl": "https://example.test/v1",
            "model": "test-model",
            "temperature": 0.3,
            "maxTokens": 4096,
            "timeoutSeconds": 60,
        }
        with patch.object(qa_app.time, "sleep"), patch.object(
            qa_app, "_call_llm", side_effect=[("not json", None), ('{"summary":"repaired"}', None)]
        ) as call:
            result, error = qa_app.run_ai_llm_analysis("system", "user", cfg, {}, {"total": 0}, 10)
        self.assertIsNone(error)
        self.assertEqual(result["summary"], "repaired")
        self.assertEqual(call.call_count, 2)


class AiTaskRouteTests(unittest.TestCase):
    def test_analyze_returns_task_id_without_waiting_for_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            store.merge_traces("shop-1", [{"id": "trace-1", "question": "hello"}], fetched_by="alice")

            class FakeThread:
                def __init__(self, *args, **kwargs):
                    self.args = args

                def start(self):
                    return None

            original_password = qa_app.ADMIN_PASSWORD
            try:
                qa_app.ADMIN_PASSWORD = ""
                with patch.object(qa_app, "SHARED_STORE", store), patch.object(qa_app, "load_users", return_value={}), patch.object(
                    qa_app, "load_ai_config", return_value={
                        "apiKey": "sk-test",
                        "baseUrl": "https://example.test/v1",
                        "model": "test-model",
                        "temperature": 0.3,
                        "maxTokens": 4096,
                        "timeoutSeconds": 60,
                    }
                ), patch.object(qa_app.threading, "Thread", FakeThread):
                    client = qa_app.app.test_client()
                    response = client.post("/api/ai/analyze", json={"shopId": "shop-1", "issueLimit": 10})
                    body = response.get_json()
                    self.assertTrue(body["success"])
                    self.assertTrue(body["taskId"])
                    self.assertFalse(body["cached"])

                    task_response = client.get("/api/ai/tasks/" + body["taskId"])
                    self.assertEqual(task_response.status_code, 200)
                    self.assertEqual(task_response.get_json()["task"]["status"], "queued")

                    cancel_response = client.post("/api/ai/tasks/" + body["taskId"] + "/cancel", json={})
                    self.assertTrue(cancel_response.get_json()["success"])
                    self.assertTrue(store.get_ai_task(body["taskId"])["cancelRequested"])
            finally:
                qa_app.ADMIN_PASSWORD = original_password


class FetchRouteRegressionTests(unittest.TestCase):
    def test_build_session_reads_flask_user_without_shadowing_session(self):
        class FakeCookies:
            def __init__(self):
                self.values = []

            def set(self, name, value, domain=None):
                self.values.append((name, value, domain))

        class FakeHttpSession:
            def __init__(self):
                self.cookies = FakeCookies()

            def get(self, *args, **kwargs):
                return None

        fake_http_session = FakeHttpSession()
        with qa_app.app.test_request_context("/"):
            qa_app.session["username"] = "alice"
            with patch.object(qa_app, "load_json", return_value={"token": "saved"}), patch.object(
                qa_app.requests, "Session", return_value=fake_http_session
            ):
                result = qa_app.build_session()

        self.assertIs(result, fake_http_session)
        self.assertEqual(fake_http_session.cookies.values, [("token", "saved", ".tanyuai.com")])

    def test_auto_sync_groups_same_name_candidates_and_keeps_data_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            store.upsert_shop("shop-2", "同名店铺", 1, "fetch")
            store.merge_traces("shop-2", [{"id": "trace-2"}], fetched_by="test")
            with patch.object(qa_app, "SHARED_STORE", store):
                saved = qa_app.save_synced_shops({
                    "shop-1": {"name": "同名店铺"},
                    "shop-2": {"name": "同名店铺"},
                })
            self.assertEqual([item["id"] for item in saved], ["shop-2"])

    def test_auto_sync_does_not_add_new_sid_for_existing_same_name_shop(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            store.upsert_shop("existing-shop", "同名店铺", 0, "auto-sync")
            with patch.object(qa_app, "SHARED_STORE", store):
                saved = qa_app.save_synced_shops({"new-shop": {"name": " 同名 店铺 "}})
            self.assertEqual([item["id"] for item in saved], ["existing-shop"])
            self.assertEqual(list(store.list_shops()), ["existing-shop"])

    def test_shop_list_includes_trace_count_for_cleanup_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            store.upsert_shop("shop-1", "店铺", 2, "test")
            store.merge_traces("shop-1", [{"id": "trace-1"}], fetched_by="test")
            self.assertEqual(store.list_shops()["shop-1"]["traceCount"], 1)

    def test_template_has_searchable_shop_selection_and_batch_delete_controls(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()
        for token in ("panel-cleanup", "cleanupShopSearch", "selectDuplicateCleanupShops", "cleanupSelectedCount", "savedShopList", "shopSelectAll", "toggleAllVisibleShops", "deleteSelectedShops", "selectedShopCount", "/api/delete-shops", "progressError", "showProgressError", "抓取失败"):
            self.assertIn(token, html)

    def test_shop_candidates_text_populates_an_empty_accumulator(self):
        found = {}
        result = qa_app.collect_shop_candidates_from_text('{"shopId":"shop-1"}', found)
        self.assertIs(result, found)
        self.assertIn("shop-1", found)

    def test_fetch_route_uses_http_session_without_shadowing_flask_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))

            class FakeResponse:
                def json(self):
                    return {"code": 1, "data": {"total": 1, "results": [{"id": "trace-1", "shopName": "测试店铺"}]}}

            class FakeHttpSession:
                def post(self, *args, **kwargs):
                    return FakeResponse()

            original_password = qa_app.ADMIN_PASSWORD
            try:
                qa_app.ADMIN_PASSWORD = ""
                with patch.object(qa_app, "SHARED_STORE", store), patch.object(qa_app, "load_users", return_value={}), patch.object(
                    qa_app, "build_session", return_value=FakeHttpSession()
                ):
                    client = qa_app.app.test_client()
                    response = client.post("/api/fetch", json={"shopId": "shop-1", "maxPages": 1})
                    body = response.get_json()
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(body["success"])
                    self.assertEqual(body["totalFetched"], 1)
                    self.assertEqual(store.count_traces("shop-1"), 1)
            finally:
                qa_app.ADMIN_PASSWORD = original_password

    def test_batch_delete_route_removes_selected_shared_shops(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "qa.sqlite3"))
            store.upsert_shop("shop-1", "店铺一", 1, "test")
            store.upsert_shop("shop-2", "店铺二", 1, "test")
            store.merge_traces("shop-1", [{"id": "trace-1"}], fetched_by="test")
            store.merge_traces("shop-2", [{"id": "trace-2"}], fetched_by="test")

            original_password = qa_app.ADMIN_PASSWORD
            try:
                qa_app.ADMIN_PASSWORD = ""
                with patch.object(qa_app, "SHARED_STORE", store), patch.object(qa_app, "load_users", return_value={}):
                    client = qa_app.app.test_client()
                    response = client.post("/api/delete-shops", json={"shopIds": ["shop-1", "SHOP-1"]})
                    body = response.get_json()
                    self.assertTrue(body["success"])
                    self.assertEqual(body["count"], 1)
                    self.assertIsNone(store.get_ai_task("missing"))
                    self.assertNotIn("shop-1", store.list_shops())
                    self.assertIn("shop-2", store.list_shops())
                    self.assertEqual(store.count_traces("shop-2"), 1)
            finally:
                qa_app.ADMIN_PASSWORD = original_password


if __name__ == "__main__":
    unittest.main()
