import os
import sys
import unittest
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "111"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import app as qa_app


class AiProviderCallTests(unittest.TestCase):
    def test_call_llm_uses_configured_read_timeout(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"choices": [{"message": {"content": "{\"ok\":true}"}}]}

        captured = {}

        def fake_post(url, json, headers, timeout):
            captured["timeout"] = timeout
            return FakeResponse()

        cfg = {
            "apiKey": "sk-test",
            "baseUrl": "https://example.test/v1",
            "model": "agnes-2.0-flash",
            "temperature": 0.3,
            "maxTokens": 12000,
            "timeoutSeconds": 420,
        }

        with patch.object(qa_app.requests, "post", side_effect=fake_post):
            text, err = qa_app._call_llm("system", "user", cfg)

        self.assertIsNone(err)
        self.assertEqual(text, "{\"ok\":true}")
        self.assertEqual(captured["timeout"], (20, 420))

    def test_format_llm_error_adds_batch_timeout_guidance(self):
        message = qa_app.format_llm_error(
            "HTTPSConnectionPool(host='apihub.agnes-ai.com', port=443): Read timed out. (read timeout=300)",
            attempt=3,
            issue_limit=50,
        )

        self.assertIn("AI 接口超过 300 秒仍未返回", message)
        self.assertIn("当前每批 50 条", message)
        self.assertIn("先改成 20 条一批", message)


class AiDeepAnalysisPromptTests(unittest.TestCase):
    def sample_traces(self):
        return [
            {
                "id": "trace-1",
                "thirdShopId": "shop_1",
                "buyerAccount": "buyer-a",
                "sellerAccount": "bot-a",
                "topicName": "售后退款",
                "question": "退货规则是什么",
                "content": ["可以退货，但这个场景需要补充更明确的规则。"],
                "type": "agent",
                "productInfo": {"spuTitle": "便携水杯", "skuId": "sku-1", "spuId": "spu-1"},
            },
            {
                "id": "trace-2",
                "thirdShopId": "shop_1",
                "buyerAccount": "buyer-b",
                "sellerAccount": "bot-a",
                "topicName": "物流时效",
                "searchContent": "买家: 今天能发货吗[[一般会尽快安排]]",
                "type": "agent",
            },
        ]

    def local_analysis(self, issue_count=2, qa_count=2, topic_count=2, keyword_count=3):
        issues = []
        for idx in range(issue_count):
            issues.append({
                "id": f"issue-{idx}",
                "topic": "售后退款",
                "issueType": "弱回复",
                "priority": "高" if idx == 0 else "中",
                "score": 92 - idx,
                "count": 4 + idx,
                "adoptionRisk": 81,
                "failureReason": "回答没有说明退货条件和人工兜底规则",
                "suggestedAction": "补知识卡片",
                "trainingSuggestion": "补充退货条件、运费承担和转人工边界",
                "knowledgeCardDraft": {
                    "title": "售后退款｜退货规则是什么",
                    "standardQuestion": "退货规则是什么",
                    "similarQuestions": ["能退吗", "怎么退货"],
                    "triggerWords": ["退货", "退款"],
                    "standardAnswer": "说明退货条件、流程和时效。",
                    "manualHandoffRule": "涉及赔付争议时转人工。",
                },
                "examples": [
                    {
                        "id": "trace-1",
                        "question": "退货规则是什么",
                        "answer": "可以退货，但这个场景需要补充更明确的规则。",
                        "identity": {"productTitle": "便携水杯", "buyerId": "buyer-a"},
                    }
                ],
            })
        qa_examples = [
            {
                "topic": f"主题-{idx}",
                "count": 3,
                "examples": [
                    {"id": f"qa-{idx}", "question": "今天能发货吗", "answer": "一般会尽快安排"}
                ],
            }
            for idx in range(qa_count)
        ]
        topics = [
            {
                "topic": f"主题-{idx}",
                "count": 5,
                "issueCount": 2,
                "knowledgeGapCount": 1,
                "adoptionRisk": 72,
                "suggestedAction": "优先补卡",
            }
            for idx in range(topic_count)
        ]
        keywords = [{"word": f"关键词{idx}", "count": 10 - idx} for idx in range(keyword_count)]
        return {
            "totalRecords": 50,
            "withContent": 45,
            "topicDistribution": [{"topic": "售后退款", "count": 12, "percentage": 24.0}],
            "storeDiagnosis": {
                "healthScore": 62,
                "issueCount": issue_count,
                "highPriorityCount": 1,
                "pendingCount": issue_count,
                "adoptionRiskAvg": 76,
                "summary": ["售后退款问题集中，建议优先补卡。"],
                "todayTasks": [{"title": "优先处理高优先级售后问题", "type": "补卡", "count": 1}],
            },
            "issueWorkbench": issues,
            "qaExamples": qa_examples,
            "topicInsights": {
                "topicCount": topic_count,
                "knowledgeGapCount": topic_count,
                "avgAdoptionRisk": 72,
                "topics": topics,
                "riskTopics": topics,
                "gapTopics": topics,
                "keywords": keywords,
            },
            "identityInsights": {
                "productCoverage": 90.0,
                "buyerCoverage": 88.0,
                "topProducts": [{"productTitle": "便携水杯", "traceCount": 6, "issueCount": 2}],
                "issueProducts": [{"productTitle": "便携水杯", "traceCount": 6, "issueCount": 2}],
                "topBuyers": [{"buyerId": "buyer-a", "traceCount": 3, "issueCount": 1}],
            },
            "topKeywords": keywords,
        }

    def test_prompt_bundle_includes_local_analysis_and_deep_schema(self):
        bundle = qa_app.build_ai_analysis_prompt_bundle(
            self.sample_traces(),
            "shop_1",
            self.local_analysis(),
        )

        self.assertIn("systemPrompt", bundle)
        self.assertIn("userText", bundle)
        self.assertIn("analysisContext", bundle)
        for field in (
            "executiveSummary",
            "pendingKnowledgeCards",
            "issueOptimizationWorkbench",
            "typicalQAExamples",
            "topicDeepDives",
            "actionPlan",
            "riskAlerts",
        ):
            self.assertIn(field, bundle["systemPrompt"])

        context = bundle["analysisContext"]
        self.assertEqual(context["shopId"], "shop_1")
        self.assertEqual(context["localAnalysis"]["storeDiagnosis"]["healthScore"], 62)
        self.assertEqual(
            context["localAnalysis"]["issueWorkbench"][0]["knowledgeCardDraft"]["standardQuestion"],
            "退货规则是什么",
        )
        self.assertLessEqual(len(context["traceSamples"]), 20)
        self.assertIn("待补知识卡片", bundle["userText"])
        self.assertIn("问题优化工作台", bundle["userText"])
        self.assertIn("退货规则是什么", bundle["userText"])

    def test_prompt_bundle_scales_trace_samples_with_batch_size(self):
        bundle = qa_app.build_ai_analysis_prompt_bundle(
            self.sample_traces() * 50,
            "shop_1",
            self.local_analysis(issue_count=60),
            issue_offset=0,
            issue_limit=10,
        )

        self.assertLessEqual(len(bundle["analysisContext"]["traceSamples"]), 10)

    def test_compact_ai_context_limits_large_local_analysis_sections(self):
        compact = qa_app.compact_ai_context(
            self.local_analysis(issue_count=260, qa_count=12, topic_count=16, keyword_count=30)
        )

        self.assertEqual(len(compact["issueWorkbench"]), qa_app.AI_DEFAULT_ISSUE_BATCH_SIZE)
        self.assertEqual(compact["issueBatch"]["offset"], 0)
        self.assertEqual(compact["issueBatch"]["limit"], qa_app.AI_DEFAULT_ISSUE_BATCH_SIZE)
        self.assertEqual(compact["issueBatch"]["total"], 260)
        self.assertTrue(compact["issueBatch"]["hasMore"])
        self.assertLessEqual(len(compact["qaExamples"]), 8)
        self.assertLessEqual(len(compact["topicInsights"]["topics"]), 8)
        self.assertLessEqual(len(compact["topicInsights"]["riskTopics"]), 6)
        self.assertLessEqual(len(compact["topicInsights"]["gapTopics"]), 6)
        self.assertLessEqual(len(compact["topKeywords"]), 12)

    def test_compact_ai_context_supports_variable_issue_batches(self):
        compact = qa_app.compact_ai_context(
            self.local_analysis(issue_count=260),
            issue_offset=200,
            issue_limit=50,
        )

        self.assertEqual(len(compact["issueWorkbench"]), 50)
        self.assertEqual(compact["issueWorkbench"][0]["id"], "issue-200")
        self.assertEqual(compact["issueBatch"]["offset"], 200)
        self.assertEqual(compact["issueBatch"]["limit"], 50)
        self.assertEqual(compact["issueBatch"]["nextOffset"], 250)
        self.assertTrue(compact["issueBatch"]["hasMore"])

    def test_compact_ai_context_clamps_batch_size_to_safe_max(self):
        compact = qa_app.compact_ai_context(
            self.local_analysis(issue_count=300),
            issue_offset=250,
            issue_limit=999,
        )

        self.assertEqual(compact["issueBatch"]["limit"], qa_app.AI_MAX_ISSUE_BATCH_SIZE)
        self.assertEqual(compact["issueBatch"]["count"], 50)
        self.assertFalse(compact["issueBatch"]["hasMore"])

    def test_prompt_bundle_requests_batch_quality_scripts(self):
        bundle = qa_app.build_ai_analysis_prompt_bundle(
            self.sample_traces(),
            "shop_1",
            self.local_analysis(issue_count=260),
            issue_offset=200,
            issue_limit=50,
        )

        context = bundle["analysisContext"]
        self.assertEqual(context["localAnalysis"]["issueBatch"]["offset"], 200)
        self.assertEqual(context["localAnalysis"]["issueBatch"]["limit"], 50)
        self.assertIn("qualityInspectionWorkbench", bundle["systemPrompt"])
        self.assertIn("customerServiceReply", bundle["systemPrompt"])
        self.assertIn("knowledgeBaseAnswer", bundle["systemPrompt"])
        self.assertIn("manualHandoffScript", bundle["systemPrompt"])
        self.assertIn("issueBatch.limit", bundle["systemPrompt"])
        self.assertIn("继续生成后续批次", bundle["userText"])


class AiResponseParsingTests(unittest.TestCase):
    def test_parse_ai_json_accepts_fenced_and_prefixed_content(self):
        text = """模型输出如下:
```json
{"summary":"ok","recommendations":["补知识"],"classifications":[]}
```
请查收。"""

        result = qa_app.parse_ai_response(text)

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["recommendations"], ["补知识"])

    def test_parse_ai_json_accepts_object_body_without_outer_braces(self):
        text = '"summary":"ok","recommendations":["补卡"],"classifications":[]'

        result = qa_app.parse_ai_response(text)

        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["classifications"], [])

    def test_truncated_ai_json_error_is_detected(self):
        error = 'Unterminated string starting at: line 351 column 24 (char 10116)'

        self.assertTrue(qa_app.is_probably_truncated_ai_json_error(error))

    def test_normalize_ai_result_backfills_deep_dashboard_fields_from_legacy_shape(self):
        local = AiDeepAnalysisPromptTests().local_analysis(issue_count=1, qa_count=1, topic_count=1)
        raw = {
            "summary": "售后退款需要补知识",
            "recommendations": ["补充退货规则"],
            "classifications": [
                {
                    "issueType": "弱回复",
                    "priority": "高",
                    "topic": "售后退款",
                    "standardQuestion": "退货规则是什么",
                    "rootCause": "规则缺失",
                    "suggestedAction": "补知识卡片",
                    "count": 3,
                }
            ],
            "knowledgeCards": [
                {
                    "title": "退货规则",
                    "standardQuestion": "退货规则是什么",
                    "similarQuestions": ["能退吗"],
                    "triggerWords": ["退货"],
                    "standardAnswer": "说明条件、流程和时效。",
                    "manualHandoffRule": "争议转人工。",
                }
            ],
        }

        result = qa_app.normalize_ai_result(raw, local)

        self.assertIn("executiveSummary", result)
        self.assertIn("pendingKnowledgeCards", result)
        self.assertIn("issueOptimizationWorkbench", result)
        self.assertIn("typicalQAExamples", result)
        self.assertIn("topicDeepDives", result)
        self.assertIn("actionPlan", result)
        self.assertEqual(result["pendingKnowledgeCards"][0]["standardQuestion"], "退货规则是什么")
        self.assertEqual(result["issueOptimizationWorkbench"][0]["rootCause"], "规则缺失")

    def test_normalize_ai_result_adds_evidence_examples_and_qa_template(self):
        local = AiDeepAnalysisPromptTests().local_analysis(issue_count=1, qa_count=1, topic_count=1)
        raw = {
            "summary": "售后退款需要补知识",
            "issueOptimizationWorkbench": [
                {
                    "issueId": "issue-0",
                    "priority": "高",
                    "issueType": "弱回复",
                    "topic": "售后退款",
                    "rootCause": "回答没有说明退货条件",
                    "suggestedAction": "补知识卡片",
                }
            ],
            "pendingKnowledgeCards": [
                {
                    "title": "售后退款｜退货规则是什么",
                    "topic": "售后退款",
                    "standardQuestion": "退货规则是什么",
                    "standardAnswer": "说明退货条件、流程和时效。",
                }
            ],
        }

        result = qa_app.normalize_ai_result(raw, local)

        issue = result["issueOptimizationWorkbench"][0]
        card = result["pendingKnowledgeCards"][0]
        self.assertIn("evidenceExamples", issue)
        self.assertIn("qaTemplate", issue)
        self.assertEqual(issue["evidenceExamples"][0]["buyerId"], "buyer-a")
        self.assertEqual(issue["evidenceExamples"][0]["productTitle"], "便携水杯")
        self.assertEqual(issue["qaTemplate"]["standardQuestion"], "退货规则是什么")
        self.assertIn("转人工", issue["qaTemplate"]["manualHandoffRule"])
        self.assertIn("evidenceExamples", card)
        self.assertIn("qaTemplate", card)


class AiTemplateDeepResultTests(unittest.TestCase):
    def test_ai_template_renders_and_exports_deep_result_sections(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "pendingKnowledgeCards",
            "issueOptimizationWorkbench",
            "typicalQAExamples",
            "topicDeepDives",
            "actionPlan",
            "riskAlerts",
        ):
            self.assertIn(token, html)

        for label in (
            "AI 深度仪表板",
            "健康分",
            "知识缺口",
            "待补知识卡片",
            "问题优化工作台",
            "典型 QA 实例",
            "主题深挖",
            "行动计划",
            "风险提醒",
        ):
            self.assertIn(label, html)


        for token in ("renderAiDashboard", "aiDashboardKpis", "aiDashboardPanel"):
            self.assertIn(token, html)

    def test_ai_template_has_evidence_examples_and_qa_template_view(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "renderAiEvidenceExamples",
            "renderAiQaTemplate",
            "ai-evidence-card",
            "ai-template-box",
            "evidenceExamples",
            "qaTemplate",
            "data-copy-template",
            "买家",
            "商品",
            "聊天记录",
            "可复制 QA 模板",
        ):
            self.assertIn(token, html)

    def test_ai_template_has_live_visual_progress_dashboard(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "aiProgressVisual",
            "aiProgressStageList",
            "aiElapsedText",
            "ai-progress-flow",
            "aiProgressTicker",
            "startAiProgressTicker",
            "stopAiProgressTicker",
            "updateAiProgressVisual",
            "is-running",
        ):
            self.assertIn(token, html)

    def test_ai_template_has_detailed_progress_explainer(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "aiProgressDetailPanel",
            "aiProgressDetailList",
            "aiProgressCurrentStep",
            "aiProgressNextStep",
            "aiProgressWaitHint",
            "updateAiProgressDetails",
            "AI 正在生成深度结论",
            "等待模型返回",
            "下一步",
            "请不要刷新页面",
        ):
            self.assertIn(token, html)


    def test_ai_template_supports_batch_quality_script_workbench(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            "AI_BATCH_SIZE_OPTIONS",
            "aiBatchSize",
            "loadMoreAiAnalysis",
            "mergeAiAnalysisResult",
            "renderAiQualityWorkbench",
            "aiBatchInfo",
            "继续生成下一批",
            "AI 质检话术工作台",
            "复制客服回复",
            "复制知识库答案",
            "复制转人工规则",
            "data-copy-ai-script",
            "aiTimeoutSeconds",
            '<option value="10" selected>10</option>',
            "接口慢或超时先用 20",
        ):
            self.assertIn(token, html)

class LocalAnalysisTemplateTests(unittest.TestCase):
    def test_local_analysis_does_not_render_chat_viewer_panel(self):
        template_path = os.path.join(APP_DIR, "templates", "index.html")
        with open(template_path, encoding="utf-8") as handle:
            html = handle.read()

        for token in (
            'onclick="toggleChatViewer()"',
            'id="chatViewerPanel"',
            'id="chatList"',
            'id="chatJumpInput"',
            'function showChatViewer',
            'function loadChatPage',
            'function jumpChatPage',
            '"/api/chat-records"',
        ):
            self.assertNotIn(token, html)


if __name__ == "__main__":
    unittest.main()
