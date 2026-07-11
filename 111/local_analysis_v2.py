"""Pure, explainable local quality analysis rules."""

import re


ANALYSIS_VERSION = "local-v2.0"

ACKNOWLEDGEMENT_REPLIES = {
    "好的", "好", "可以", "收到", "收到了", "已收到", "明白", "了解", "知道了", "已处理", "处理好了",
    "谢谢", "感谢", "不客气", "稍等", "请稍等",
}

DETAIL_QUESTION_PATTERNS = (
    "怎么", "如何", "为什么", "多久", "什么时候", "哪里", "哪儿", "流程", "步骤", "条件",
    "退款", "退货", "换货", "物流", "发货", "售后", "保修", "补发", "赔付", "优惠",
)

ACTION_PATTERNS = (
    "点击", "进入", "提交", "申请", "上传", "提供", "查看", "查询", "选择", "联系", "转接",
    "订单", "售后", "入口", "页面", "客服", "人工", "小时", "工作日", "预计", "步骤", "处理",
)

WEAK_FALLBACK_PATTERNS = (
    "不知道", "不清楚", "无法确定", "不能确定", "暂不支持", "系统繁忙", "没有权限",
    "请稍等", "稍后再试", "建议咨询", "请咨询", "没听懂", "无法识别",
)

HANDOFF_PATTERNS = ("转人工", "人工客服", "联系人工", "咨询人工")
HANDOFF_DETAIL_PATTERNS = (
    "点击", "入口", "在线客服", "客服中心", "订单", "售后", "提供", "转接", "为您转", "凭证",
)

STRONG_NEGATIVE_PATTERNS = (
    "投诉", "差评", "欺骗", "骗子", "垃圾", "生气", "非常不满", "没人处理", "一直不回复",
)

EMPATHY_PATTERNS = (
    "抱歉", "对不起", "理解您的", "给您带来", "非常理解", "请您放心", "会为您处理",
)

RISKY_PROMISE_PATTERNS = (
    "百分百", "绝对", "肯定会", "一定会", "保证退款", "无条件退款", "马上到账", "立即到账",
    "保证赔付", "肯定赔", "随时都能退",
)

PLACEHOLDER_PREFIXES = ("参考话题：", "参考问题：", "暂无答案", "无答案")

QUESTION_NOISE_WORDS = (
    "请问", "你好", "您好", "麻烦", "帮我", "可以", "能不能", "怎么", "如何", "为什么",
)


def _compact(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _contains_any(text, patterns):
    return any(pattern in text for pattern in patterns)


def _excerpt(value, limit=120):
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _rule_hit(rule_id, name, severity, confidence, weight, evidence, deductions):
    return {
        "ruleId": rule_id,
        "ruleVersion": ANALYSIS_VERSION,
        "name": name,
        "severity": severity,
        "confidence": int(confidence),
        "weight": int(weight),
        "evidence": _excerpt(evidence),
        "deductions": dict(deductions),
    }


def _confidence_level(confidence):
    if confidence >= 85:
        return "高"
    if confidence >= 70:
        return "中"
    return "低"


def evaluate_trace_quality(question, answer, context=None):
    """Evaluate one normalized question/answer pair without external state."""
    context = context if isinstance(context, dict) else {}
    question_text = str(question or "").strip()
    answer_text = str(answer or "").strip()
    compact_answer = _compact(answer_text)
    search_content = str(context.get("searchContent", "") or "")
    dimensions = {
        "relevance": 100,
        "completeness": 100,
        "actionability": 100,
        "safety": 100,
        "resolution": 100,
    }
    hits = []

    no_answer = not compact_answer or any(answer_text.startswith(prefix) for prefix in PLACEHOLDER_PREFIXES)
    if no_answer:
        hits.append(_rule_hit(
            "QA-NO-ANSWER-001", "无有效回复", "高", 98, 100,
            answer_text or "机器人回答为空",
            {"relevance": 100, "completeness": 100, "actionability": 100, "resolution": 100},
        ))
    else:
        requires_detail = _contains_any(question_text, DETAIL_QUESTION_PATTERNS)
        has_action = _contains_any(answer_text, ACTION_PATTERNS)
        is_acknowledgement = compact_answer in ACKNOWLEDGEMENT_REPLIES

        if requires_detail and len(compact_answer) <= 12 and not has_action:
            hits.append(_rule_hit(
                "QA-INCOMPLETE-001", "回答缺少必要步骤或条件", "中", 74, 45,
                answer_text,
                {"completeness": 45, "actionability": 45, "resolution": 30},
            ))
        elif len(compact_answer) <= 8 and not is_acknowledgement and not has_action:
            hits.append(_rule_hit(
                "QA-INCOMPLETE-001", "回答过短且缺少可执行信息", "低", 58, 25,
                answer_text,
                {"completeness": 25, "actionability": 25},
            ))

        fallback_hits = [pattern for pattern in WEAK_FALLBACK_PATTERNS if pattern in answer_text]
        if fallback_hits and not has_action:
            hits.append(_rule_hit(
                "QA-FALLBACK-001", "兜底话术没有下一步动作", "中", 82, 55,
                "、".join(fallback_hits[:3]),
                {"completeness": 35, "actionability": 55, "resolution": 40},
            ))

        handoff_hits = [pattern for pattern in HANDOFF_PATTERNS if pattern in answer_text]
        if handoff_hits and not _contains_any(answer_text, HANDOFF_DETAIL_PATTERNS):
            hits.append(_rule_hit(
                "QA-HANDOFF-001", "转人工缺少入口或交接说明", "中", 78, 45,
                "、".join(handoff_hits[:2]),
                {"completeness": 30, "actionability": 45, "resolution": 25},
            ))

        promise_hits = [pattern for pattern in RISKY_PROMISE_PATTERNS if pattern in answer_text]
        if promise_hits:
            hits.append(_rule_hit(
                "QA-PROMISE-001", "存在未经校验的绝对承诺", "高", 94, 85,
                "、".join(promise_hits[:3]),
                {"safety": 85, "resolution": 25},
            ))

        combined_buyer_text = f"{question_text} {search_content}"
        negative_hits = [pattern for pattern in STRONG_NEGATIVE_PATTERNS if pattern in combined_buyer_text]
        handled_sentiment = _contains_any(answer_text, EMPATHY_PATTERNS) and (
            has_action or _contains_any(answer_text, HANDOFF_PATTERNS)
        )
        if negative_hits and not handled_sentiment:
            hits.append(_rule_hit(
                "QA-SENTIMENT-001", "强烈不满场景缺少安抚和处理动作", "高", 86, 70,
                "、".join(negative_hits[:3]),
                {"relevance": 20, "actionability": 35, "safety": 45, "resolution": 35},
            ))

        buyer_turns = len(re.findall(r"(?:买家|buyer)\s*[:：]", search_content, re.I))
        if buyer_turns >= 3 and not has_action and not is_acknowledgement:
            hits.append(_rule_hit(
                "QA-MULTITURN-001", "多轮追问后仍缺少解决动作", "中", 80, 60,
                f"检测到 {buyer_turns} 个买家轮次",
                {"relevance": 20, "actionability": 45, "resolution": 60},
            ))

    for hit in hits:
        for dimension, deduction in hit.get("deductions", {}).items():
            dimensions[dimension] = max(0, dimensions.get(dimension, 100) - int(deduction or 0))

    rule_ids = {hit["ruleId"] for hit in hits}
    if "QA-NO-ANSWER-001" in rule_ids:
        issue_type = "未回复"
    elif rule_ids.intersection({"QA-PROMISE-001", "QA-SENTIMENT-001"}):
        issue_type = "高风险"
    elif hits:
        issue_type = "弱回复"
    else:
        issue_type = "正常"

    confidence = max((hit["confidence"] for hit in hits), default=80)
    if len(hits) >= 2:
        confidence = min(100, confidence + 4)
    primary_hit = max(hits, key=lambda item: (item["weight"], item["confidence"]), default=None)
    reasons = [
        f"{hit['name']}：{hit['evidence']}" if hit.get("evidence") else hit["name"]
        for hit in hits
    ]
    return {
        "analysisVersion": ANALYSIS_VERSION,
        "issueType": issue_type,
        "confidence": confidence,
        "confidenceLevel": _confidence_level(confidence),
        "qualityDimensions": dimensions,
        "ruleHits": hits,
        "primaryRuleId": primary_hit.get("ruleId", "") if primary_hit else "",
        "reasons": reasons,
    }


def primary_rule_family(evaluation):
    rule_id = str((evaluation or {}).get("primaryRuleId", "") or "")
    parts = rule_id.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else rule_id or "QA-NORMAL"


def question_tokens(question):
    text = str(question or "").lower()
    text = re.sub(r"https?://\S+|\d{5,}", " ", text)
    for word in QUESTION_NOISE_WORDS:
        text = text.replace(word, " ")
    chunks = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]{2,}", text)
    tokens = set()
    for chunk in chunks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) <= 2:
                tokens.add(chunk)
            else:
                tokens.update(chunk[index:index + 2] for index in range(len(chunk) - 1))
        else:
            tokens.add(chunk)
    return tokens


def question_similarity(left, right):
    left_tokens = left if isinstance(left, set) else question_tokens(left)
    right_tokens = right if isinstance(right, set) else question_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def derive_priority(issue_type, count, unresolved_count, negative_count, confidence, rule_hits=None):
    rule_ids = {str(hit.get("ruleId", "")) for hit in (rule_hits or []) if isinstance(hit, dict)}
    if rule_ids.intersection({"QA-PROMISE-001", "QA-SENTIMENT-001"}) and confidence >= 85:
        return "高", 40

    base = {"未回复": 8, "高风险": 10, "弱回复": 4}.get(issue_type, 0)
    score = (
        base
        + min(15, int(count or 0) * 3)
        + min(18, int(unresolved_count or 0) * 3)
        + min(15, int(negative_count or 0) * 4)
        + int(confidence or 0) // 10
    )
    if score >= 34:
        return "高", score
    if score >= 18:
        return "中", score
    return "低", score


def calculate_health_score(total_records, issues):
    total = max(int(total_records or 0), 1)
    counts = {"未回复": 0, "高风险": 0, "弱回复": 0}
    multi_turn = 0
    low_actionability = 0
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        issue_count = int(issue.get("count", 0) or 0)
        type_counts = issue.get("typeCounts", {}) if isinstance(issue.get("typeCounts"), dict) else {}
        if type_counts:
            for issue_type in counts:
                counts[issue_type] += int(type_counts.get(issue_type, 0) or 0)
        elif issue.get("issueType") in counts:
            counts[issue["issueType"]] += issue_count
        rule_counts = issue.get("ruleHitCounts", {}) if isinstance(issue.get("ruleHitCounts"), dict) else {}
        multi_turn += min(issue_count, int(rule_counts.get("QA-MULTITURN-001", 0) or 0))
        dimensions = issue.get("qualityDimensions", {}) if isinstance(issue.get("qualityDimensions"), dict) else {}
        if int(dimensions.get("actionability", 100) or 0) < 60:
            low_actionability += issue_count

    rates = {
        "noReplyRate": min(1.0, counts["未回复"] / total),
        "highRiskRate": min(1.0, counts["高风险"] / total),
        "weakReplyRate": min(1.0, counts["弱回复"] / total),
        "multiTurnUnresolvedRate": min(1.0, multi_turn / total),
        "lowActionabilityRate": min(1.0, low_actionability / total),
    }
    deduction = (
        rates["noReplyRate"] * 35
        + rates["highRiskRate"] * 30
        + rates["weakReplyRate"] * 15
        + rates["multiTurnUnresolvedRate"] * 10
        + rates["lowActionabilityRate"] * 10
    )
    return {
        "healthScore": max(0, round(100 - deduction)),
        "sampleAdequate": int(total_records or 0) >= 20,
        "sampleLabel": "样本充足" if int(total_records or 0) >= 20 else "样本不足",
        "qualityRates": {key: round(value * 100, 1) for key, value in rates.items()},
    }
