import os
import sys
import unittest


APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from local_analysis_v2 import calculate_health_score, derive_priority, evaluate_trace_quality
import app as qa_app
from shared_store import SharedStore
from unittest.mock import patch
import tempfile


class LocalAnalysisV2GoldenTests(unittest.TestCase):
    def test_empty_answer_is_high_confidence_no_answer(self):
        result = evaluate_trace_quality("退款怎么操作", "")
        self.assertEqual(result["issueType"], "未回复")
        self.assertGreaterEqual(result["confidence"], 90)
        self.assertEqual(result["primaryRuleId"], "QA-NO-ANSWER-001")

    def test_acknowledgement_is_not_weak_only_because_it_is_short(self):
        result = evaluate_trace_quality("已经收到商品了吗", "收到了")
        self.assertEqual(result["issueType"], "正常")
        self.assertEqual(result["ruleHits"], [])

    def test_detailed_question_with_short_answer_is_incomplete(self):
        result = evaluate_trace_quality("退款怎么操作", "可以")
        self.assertEqual(result["issueType"], "弱回复")
        self.assertIn("QA-INCOMPLETE-001", {item["ruleId"] for item in result["ruleHits"]})

    def test_buyer_complaint_is_not_high_risk_when_answer_handles_it(self):
        result = evaluate_trace_quality(
            "一直没人处理，我要投诉",
            "非常抱歉给您带来不便，请提供订单号，我马上为您转接人工客服处理。",
        )
        self.assertNotEqual(result["issueType"], "高风险")

    def test_unhandled_buyer_complaint_is_high_risk(self):
        result = evaluate_trace_quality("一直没人处理，我要投诉", "请稍等")
        self.assertEqual(result["issueType"], "高风险")
        self.assertIn("QA-SENTIMENT-001", {item["ruleId"] for item in result["ruleHits"]})

    def test_fallback_without_action_is_weak(self):
        result = evaluate_trace_quality("物流多久能到", "不清楚，请稍等")
        self.assertEqual(result["issueType"], "弱回复")
        self.assertIn("QA-FALLBACK-001", {item["ruleId"] for item in result["ruleHits"]})

    def test_absolute_promise_is_high_risk(self):
        result = evaluate_trace_quality("退款什么时候到", "保证退款马上到账")
        self.assertEqual(result["issueType"], "高风险")
        self.assertIn("QA-PROMISE-001", {item["ruleId"] for item in result["ruleHits"]})

    def test_handoff_with_entry_and_context_is_not_weak(self):
        result = evaluate_trace_quality(
            "订单状态异常怎么办",
            "请点击订单售后入口并提供订单号，我会为您转接人工客服继续处理。",
        )
        self.assertEqual(result["issueType"], "正常")

    def test_single_no_answer_is_not_automatically_high_priority(self):
        priority, _ = derive_priority("未回复", 1, 1, 0, 98, [])
        self.assertEqual(priority, "中")

    def test_health_score_uses_rates_instead_of_absolute_counts(self):
        small = [{"issueType": "未回复", "count": 10, "typeCounts": {"未回复": 10}}]
        large = [{"issueType": "未回复", "count": 100, "typeCounts": {"未回复": 100}}]
        self.assertEqual(
            calculate_health_score(100, small)["healthScore"],
            calculate_health_score(1000, large)["healthScore"],
        )

    def test_run_analysis_keeps_v2_explanation_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            traces = [
                {"id": "1", "question": "退款怎么操作", "answer": ""},
                {
                    "id": "2",
                    "question": "一直没人处理，我要投诉",
                    "answer": "非常抱歉，请提供订单号，我为您转接人工客服处理。",
                },
            ]
            with patch.object(qa_app, "SHARED_STORE", store):
                result = qa_app.run_analysis(traces, "shop-1")

            self.assertEqual(result["analysisVersion"], "local-v2.0")
            self.assertTrue(result["issueWorkbench"])
            issue = result["issueWorkbench"][0]
            for key in ("confidence", "confidenceLevel", "qualityDimensions", "ruleHits", "analysisVersion"):
                self.assertIn(key, issue)
            self.assertIn("conversationSummary", result)
            self.assertIn("manualQueue", result)
            self.assertGreater(result["storeDiagnosis"]["healthScore"], 0)

    def test_manual_queue_uses_mutually_exclusive_buckets(self):
        queue = qa_app.build_manual_queue([
            {
                "id": "promise", "standardQuestion": "退款什么时候到账", "issueType": "高风险",
                "priority": "高", "confidence": 94, "count": 1, "status": "待处理",
                "ruleHits": [{"ruleId": "QA-PROMISE-001"}], "suggestedAction": "改话术",
            },
            {
                "id": "review", "standardQuestion": "物流多久到", "issueType": "弱回复",
                "priority": "中", "confidence": 58, "count": 8, "status": "待处理",
                "ruleHits": [], "suggestedAction": "补知识",
            },
            {
                "id": "batch", "standardQuestion": "如何退货", "issueType": "弱回复",
                "priority": "中", "confidence": 82, "count": 3, "status": "待处理",
                "ruleHits": [], "suggestedAction": "补知识",
            },
            {
                "id": "done", "standardQuestion": "已完成问题", "issueType": "高风险",
                "priority": "高", "confidence": 94, "count": 9, "status": "复查通过",
                "ruleHits": [{"ruleId": "QA-PROMISE-001"}], "suggestedAction": "改话术",
            },
        ])
        buckets = {item["issueId"]: item["bucket"] for item in queue}
        self.assertEqual(buckets, {"promise": "立即处理", "review": "需要复核", "batch": "批量整改"})

    def test_chat_records_returns_paginated_multi_turn_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            store.merge_traces("shop-1", [
                {"id": "t1", "buyerAccount": "buyer-1", "time": 60_000, "question": "怎么退款", "content": "请从订单售后入口申请"},
                {"id": "t2", "buyerAccount": "buyer-1", "time": 60_000 * 30, "question": "多久到账", "content": "预计三个工作日"},
                {"id": "t3", "buyerAccount": "buyer-2", "time": 60_000 * 31, "question": "怎么发货", "content": "今日安排"},
            ], fetched_by="test")
            original_password = qa_app.ADMIN_PASSWORD
            try:
                qa_app.ADMIN_PASSWORD = ""
                with patch.object(qa_app, "SHARED_STORE", store), patch.object(qa_app, "load_users", return_value={}):
                    client = qa_app.app.test_client()
                    multi = client.get("/api/chat-records?shopId=shop-1&conversationMode=true").get_json()
                    self.assertTrue(multi["success"])
                    self.assertEqual(multi["total"], 1)
                    self.assertEqual(multi["sessions"][0]["turnCount"], 2)
                    self.assertEqual([row["id"] for row in multi["sessions"][0]["records"]], ["t1", "t2"])

                    all_sessions = client.get("/api/chat-records?shopId=shop-1&conversationMode=true&includeSingleTurns=true").get_json()
                    self.assertEqual(all_sessions["total"], 2)

                    detail = client.post("/api/issue-detail", json={"shopId": "shop-1", "traceIds": "t2,t1"}).get_json()
                    self.assertEqual([row["id"] for row in detail["records"]], ["t1", "t2"])
            finally:
                qa_app.ADMIN_PASSWORD = original_password

    def test_issue_feedback_route_persists_current_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SharedStore(os.path.join(tmp, "shared.sqlite3"))
            original_password = qa_app.ADMIN_PASSWORD
            try:
                qa_app.ADMIN_PASSWORD = ""
                with patch.object(qa_app, "SHARED_STORE", store), patch.object(qa_app, "load_users", return_value={}):
                    client = qa_app.app.test_client()
                    response = client.post(
                        "/api/issue-feedback",
                        json={
                            "shopId": "shop-1",
                            "issueId": "issue-1",
                            "verdict": "correct",
                            "note": "confirmed",
                        },
                    )
                    self.assertTrue(response.get_json()["success"])
                    loaded = client.get("/api/issue-feedback?shopId=shop-1").get_json()
                    self.assertEqual(loaded["feedback"]["issue-1"]["verdict"], "correct")
            finally:
                qa_app.ADMIN_PASSWORD = original_password

    def test_template_renders_v2_explanation_and_feedback_controls(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "QUALITY_DIMENSION_LABELS",
            "renderIssueExplanation",
            "quality-dimension-grid",
            "rule-evidence-list",
            "updateIssueFeedback",
            "/api/issue-feedback",
            "判断正确",
            "标记误判",
            "需要复核",
            "renderManualQueue",
            "loadQaSessions",
            "conversationMode",
            "包含单轮风险记录",
            "多轮质检会话",
            "qualityDrawer",
            "openQualityDrawer",
            "renderTypicalQaPage",
            "典型 Q&A",
        ):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
