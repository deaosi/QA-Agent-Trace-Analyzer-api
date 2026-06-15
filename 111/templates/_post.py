"""

@app.route("/")
def index():
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}

@app.route("/api/cookie-status")
def cookie_status():
    return jsonify({"hasCookie": bool(load_json(COOKIE_FILE))})

@app.route("/api/save-cookie", methods=["POST"])
def save_cookie():
    data = request.get_json() or {}
    raw = data.get("cookie", "")
    pairs = {}
    for part in raw.replace(" ", "").split(";"):
        if "=" in part: k, v = part.split("=", 1); pairs[k.strip()] = v.strip()
    if pairs: save_json(COOKIE_FILE, pairs)
    return jsonify({"success": True})

@app.route("/api/shops")
def list_shops():
    shops = load_json(SHOPS_FILE, {})
    return jsonify({"shops": [{"id": k, "name": v.get("name", k)} for k, v in shops.items()], "current": None})

@app.route("/api/set-shop", methods=["POST"])
def set_shop():
    return jsonify({"success": True})

@app.route("/api/probe-shop", methods=["POST"])
def probe_shop():
    data = request.get_json() or {}
    shop_id = data.get("shopId", "")
    raw_cookie = data.get("cookie", "")
    if raw_cookie and raw_cookie != "(已保�?":
        pairs = {}
        for part in raw_cookie.replace(" ", "").split(";"):
            if "=" in part: k, v = part.split("=", 1); pairs[k.strip()] = v.strip()
        if pairs: save_json(COOKIE_FILE, pairs)
    session = build_session()
    if not session: return jsonify({"error": "未配�?Cookie"})
    body = {"thirdShopId": shop_id, "pageIndex": 1, "pageSize": 1, "beginTime": "2024-01-01 00:00:00", "endTime": datetime.now().strftime("%Y-%m-%d 23:59:59"), "sendType": [0, 3]}
    try:
        resp = session.post("https://agent.tanyuai.com/api/im/agent-trace/paginateV2", json=body, headers={"Content-Type":"application/json","Referer":"https://agent.tanyuai.com/v2/diagnostic-optimization/optimization-workshop","Accept":"application/json","User-Agent":"Mozilla/5.0"}, timeout=30)
        result = resp.json()
    except Exception as e: return jsonify({"error": str(e)})
    if result.get("code") != 1: return jsonify({"error": result.get("msg", "认证失败")})
    data_obj = result.get("data", {})
    total = data_obj.get("total", 0) if isinstance(data_obj, dict) else 0
    results = data_obj.get("results", []) if isinstance(data_obj, dict) else []
    shop_name = results[0].get("shopName", "") if results else None
    shops = load_json(SHOPS_FILE, {})
    shops[shop_id] = {"name": shop_name or shop_id, "total": total, "updated": datetime.now().isoformat()}
    save_json(SHOPS_FILE, shops)
    return jsonify({"shopName": shop_name, "total": total, "success": True})

@app.route("/api/delete-shop", methods=["POST"])
def delete_shop():
    shop_id = (request.get_json() or {}).get("shopId", "")
    shops = load_json(SHOPS_FILE, {})
    if shop_id in shops: del shops[shop_id]; save_json(SHOPS_FILE, shops)
    for f in [data_file(shop_id), analysis_file(shop_id)]:
        if os.path.exists(f): os.remove(f)
    return jsonify({"success": True})

@app.route("/api/fetch", methods=["POST"])
def fetch_data():
    data = request.get_json() or {}
    shop_id = data.get("shopId", "")
    if not shop_id: return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请指定店�?ID"}]})
    session = build_session()
    if not session: return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请先配置 Cookie"}]})
    
    begin_time = data.get("beginTime", "2024-01-01 00:00:00").replace("T", " ") + (":00" if "T" in data.get("beginTime","") else "")
    end_time = data.get("endTime", datetime.now().strftime("%Y-%m-%d 23:59:59")).replace("T", " ") + (":00" if "T" in data.get("endTime","") else "")
    page_size = data.get("pageSize", 100)
    max_pages = data.get("maxPages", 10)
    
    headers = {"Content-Type":"application/json","Referer":"https://agent.tanyuai.com/v2/diagnostic-optimization/optimization-workshop","Accept":"application/json","User-Agent":"Mozilla/5.0"}
    
    all_records, fetch_log, shop_name = [], [], None
    for page_idx in range(1, max_pages + 1):
        body = {"thirdShopId": shop_id, "pageIndex": page_idx, "pageSize": page_size, "beginTime": begin_time, "endTime": end_time}
        filters = data.get("filters", {})
        if filters.get("reviewStatus") is not None: body["reviewStatus"] = filters["reviewStatus"]
        if filters.get("ifLabel") is not None: body["ifLabel"] = filters["ifLabel"]
        if filters.get("type"): body["type"] = filters["type"]
        if filters.get("busi"): body["busi"] = filters["busi"]
        if filters.get("sendType"): body["sendType"] = filters["sendType"]
        else: body["sendType"] = [0, 3]
        try:
            resp = session.post("https://agent.tanyuai.com/api/im/agent-trace/paginateV2", json=body, headers=headers, timeout=30)
            result = resp.json()
        except Exception as e:
            fetch_log.append({"page": page_idx, "status": "error", "msg": str(e)}); break
        if result.get("code") != 1:
            fetch_log.append({"page": page_idx, "status": "error", "msg": result.get("msg", "认证失败")}); break
        data_obj = result.get("data", {})
        records = data_obj.get("results", []) if isinstance(data_obj, dict) else []
        total = data_obj.get("total", 0) if isinstance(data_obj, dict) else 0
        if not records: fetch_log.append({"page": page_idx, "status": "empty"}); break
        if not shop_name and records: shop_name = records[0].get("shopName", "")
        all_records.extend(records)
        fetch_log.append({"page": page_idx, "status": "ok", "count": len(records), "total": total})
        if len(all_records) >= total: break
        time.sleep(0.3)
    
    overwrite = data.get("overwrite", True)
    total_fetched = 0
    if all_records:
        df = data_file(shop_id)
        if overwrite:
            save_json(df, all_records)
            total_fetched = len(all_records)
        else:
            existing = load_json(df, [])
            seen = {r.get("id") for r in existing}
            new_records = [r for r in all_records if r.get("id") not in seen]
            save_json(df, existing + new_records)
            total_fetched = len(new_records)
    
    if shop_name:
        shops = load_json(SHOPS_FILE, {})
        shops[shop_id] = {"name": shop_name, "updated": datetime.now().isoformat()}
        save_json(SHOPS_FILE, shops)
    
    return jsonify({"success": True, "totalFetched": total_fetched, "totalStored": len(load_json(data_file(shop_id), [])), "shopName": shop_name, "log": fetch_log})

# ── Analyze ──
STOP_WORDS = set("�?�?�?�?�?�?�?�?�?�?�?一 一�?�?�?�?�?�?�?�?�?�?着 没有 �?�?自己 �?�?�?�?�?�?�?什�?�?�?�?�?�?�?可以 这个 那个 怎么 为什�?因为 所�?但是 如果 虽然 而且 或�?不过 已经 还是 只是 然后 之后 之前 现在 以后 时�?比较 非常 真的 �?�?�?着 �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?知道 觉得 应该 可能 需�?必须 一�?一�?一�?一�?很多 更多 全部 �?您好 请问 感谢 麻烦 辛苦 稍等 稍后 买家".split())

QA_TOPICS = {
    "退款纠�?: ["退�?, "退�?, "退�?, "仅退�?, "退货退�?, "退�?],
    "物流问题": ["物流", "快�?, "发货", "配�?, "签收", "未收�?, "查件", "延迟", "超时"],
    "商品质量": ["质量", "瑕疵", "破损", "损坏", "坏了", "有问�?, "缺陷", "色差"],
    "客服态度": ["态度", "投诉", "不理�?, "敷衍", "辱骂", "服务�?, "不回�?],
    "优惠促销": ["优惠�?, "折扣", "满减", "活动", "赠品", "积分", "会员", "促销"],
    "订单问题": ["订单", "取消", "修改", "改地址", "备注", "改价"],
    "售后处理": ["售后", "保修", "维修", "换货", "补偿", "赔偿", "维权"],
    "库存问题": ["库存", "缺货", "断货", "补货", "到货", "预售", "下架"],
}

def parse_conversation(record):
    question = ""
    answer = ""
    q = record.get("question", "")
    if q and isinstance(q, str):
        question = q.replace("买家:", "").strip()
    content = record.get("content")
    if isinstance(content, list) and content:
        answer = "�?.join([c for c in content if isinstance(c, str)])
    elif isinstance(content, str) and content:
        answer = content
    if (not question or not answer) and record.get("searchContent"):
        sc = record.get("searchContent", "")
        buyer_match = re.search(r'买家[:：](.+?)(?:\[\[|$)', sc)
        if buyer_match and not question:
            question = buyer_match.group(1).strip()
        seller_match = re.search(r'\[\[(.+?)\]\]', sc)
        if seller_match and not answer:
            raw = seller_match.group(1)
            answer = raw.replace(" , ", "�?).replace(", ", "�?)
    if not question:
        question = record.get("topicName", "") or ""
    # If no seller reply, use topicName as reference answer
    if not answer:
        tn = record.get("topicName", "")
        if tn:
            answer = "??????" + tn
    return question, answer

def run_analysis(traces):
    if not traces: return {"error": "????"}
    
    # Group by API's topicName for fine-grained topics
    topic_groups = defaultdict(list)
    uncategorized = []
    
    for r in traces:
        tn = (r.get("topicName") or "").strip()
        if tn:
            # Simplify long topicNames - take first 15 chars as key
            key = tn[:20] if len(tn) > 20 else tn
            topic_groups[key].append(r)
        else:
            uncategorized.append(r)
    
    # Also do broad keyword matching for overview
    kw_groups = defaultdict(list)
    for r in traces:
        text = json.dumps(r, ensure_ascii=False)
        matched = False
        for topic, kws in QA_TOPICS.items():
            if any(kw in text for kw in kws):
                kw_groups[topic].append(r)
                matched = True
    
    total = len(traces)
    with_content = sum(1 for r in traces if (r.get("searchContent") or "").strip() or (isinstance(r.get("content"), list) and len(r.get("content") or []) > 0))
    
    # Broad topic distribution
    topic_dist = []
    for topic, recs in sorted(kw_groups.items(), key=lambda x: -len(x[1])):
        topic_dist.append({"topic": topic, "count": len(recs), "percentage": round(len(recs)/total*100, 1)})
    
    # Fine-grained Q&A examples grouped by topicName
    qa_examples = []
    for tn, recs in sorted(topic_groups.items(), key=lambda x: -len(x[1])):
        examples = []
        for r in recs:
            if len(examples) >= 10:
                break
            question, answer = parse_conversation(r)
            if question and answer:
                examples.append({
                    "question": question[:200],
                    "answer": answer[:500],
                    "seller": r.get("sellerAccount", ""),
                    "type": r.get("type", ""),
                    "topicName": tn,
                })
        if examples:
            qa_examples.append({"topic": tn, "count": len(recs), "examples": examples})
    
    # Keywords
    all_texts = []
    for r in traces:
        sc = r.get("searchContent", "") or ""
        tn = r.get("topicName", "") or ""
        all_texts.append(sc + " " + tn)
    combined = " ".join(all_texts)
    words = jieba.cut(combined)
    filtered = [w for w in words if len(w) >= 2 and w not in STOP_WORDS and not w.isdigit()]
    word_freq = Counter(filtered).most_common(50)
    
    result = {
        "totalRecords": total, "withContent": with_content,
        "analyzedAt": datetime.now().isoformat(),
        "topicDistribution": topic_dist,
        "qaExamples": qa_examples,
        "topKeywords": [{"word": w, "count": c} for w, c in word_freq],
    }
    return result
@app.route("/api/analyze", methods=["POST"])
def analyze():
    shop_id = (request.get_json() or {}).get("shopId", "")
    traces = load_json(data_file(shop_id), [])
    result = run_analysis(traces)
    save_json(analysis_file(shop_id), result)
    return jsonify({"success": True, "data": result})

@app.route("/api/overview")
def overview():
    shop_id = request.args.get("shopId", "")
    traces = load_json(data_file(shop_id), []) if shop_id else []
    analysis = load_json(analysis_file(shop_id), {}) if shop_id else {}
    return jsonify({"totalTraces": len(traces), "totalTopics": len(analysis.get("topicDistribution", [])), "totalRules": 0})

@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    print("=" * 50)
    print("  QA Agent Trace Analyzer")
    print("  http://127.0.0.1:5000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5000, debug=False)