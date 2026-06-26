"""QA Agent Trace Analyzer."""

import csv
import io
import json
import os
import re
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import jieba
except ImportError:
    jieba = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.environ.get("QA_DATA_DIR", os.path.join(PROJECT_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)
COOKIE_FILE = os.path.join(DATA_DIR, ".cookies.json")
SHOPS_FILE = os.path.join(DATA_DIR, ".shops.json")
ISSUE_STATUS_FILE = os.path.join(DATA_DIR, ".issue_status.json")
USERS_FILE = os.path.join(DATA_DIR, ".users.json")
ACCESS_PASSWORD = os.environ.get("QA_ACCESS_PASSWORD", "")
ADMIN_USERNAME = os.environ.get("QA_ADMIN_USERNAME", "shuxing666")
ADMIN_PASSWORD = os.environ.get("QA_ADMIN_PASSWORD", ACCESS_PASSWORD or "asdfghjkl")
AI_CONFIG_FILE = os.path.join(DATA_DIR, ".ai_config.json")

DEFAULT_AI_CONFIG = {
    "providers": {
        "agnes": {"enabled": False, "apiKey": "", "baseUrl": "https://agent.tanyuai.com/api/v1", "model": "agnes-4"},
        "openai": {"enabled": False, "apiKey": "", "baseUrl": "https://api.openai.com/v1", "model": "gpt-4o"},
        "anthropic": {"enabled": False, "apiKey": "", "baseUrl": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-20250514"},
        "custom": {"enabled": False, "apiKey": "", "baseUrl": "", "model": ""},
    },
    "activeProvider": "agnes",
    "temperature": 0.3,
    "maxTokens": 4096,
}

TRACE_API = "https://agent.tanyuai.com/api/im/agent-trace/paginateV2"
REFERER = "https://agent.tanyuai.com/v2/diagnostic-optimization/optimization-workshop"

app = Flask(__name__)
app.secret_key = os.environ.get("QA_SECRET_KEY", "change-me-before-public-deploy")
app.permanent_session_lifetime = timedelta(days=30)


def auth_enabled():
    return bool(load_users() or ADMIN_PASSWORD)


def is_authenticated():
    if not auth_enabled():
        return True
    user = current_user()
    return bool(user and user.get("active", True) and not is_user_expired(user))


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if is_authenticated():
            return fn(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "未登录"}), 401
        return redirect(url_for("login"))

    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user and user.get("role") == "admin" and not is_user_expired(user):
            return fn(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": "需要管理员权限"}), 403
        return redirect(url_for("login"))

    return wrapper


def load_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def load_users():
    users = load_json(USERS_FILE, {})
    if users or not ADMIN_PASSWORD:
        return users
    admin = {
        "username": ADMIN_USERNAME,
        "passwordHash": generate_password_hash(ADMIN_PASSWORD),
        "role": "admin",
        "active": True,
        "expiresAt": "",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "lastLoginAt": "",
    }
    users[ADMIN_USERNAME] = admin
    save_json(USERS_FILE, users)
    return users


def save_users(users):
    save_json(USERS_FILE, users)


def public_user(user):
    return {
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "active": user.get("active", True),
        "expiresAt": user.get("expiresAt", ""),
        "createdAt": user.get("createdAt", ""),
        "updatedAt": user.get("updatedAt", ""),
        "lastLoginAt": user.get("lastLoginAt", ""),
    }


def current_user():
    username = session.get("username", "")
    if not username:
        return None
    return load_users().get(username)


def is_user_expired(user):
    expires_at = str(user.get("expiresAt", "") or "").strip()
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None) < datetime.now()
    except ValueError:
        return False


def valid_username(username):
    return bool(re.fullmatch(r"[A-Za-z0-9_\-.\u4e00-\u9fff]{2,32}", username or ""))


def login_user(username, remember=True):
    session.clear()
    session.permanent = bool(remember)
    session["username"] = username
    session["loginNonce"] = secrets.token_hex(8)


def data_file(shop_id):
    return os.path.join(DATA_DIR, f"traces_{shop_id}.json")


def analysis_file(shop_id):
    return os.path.join(DATA_DIR, f"analysis_{shop_id}.json")


def issue_status_key(shop_id, issue_id):
    return f"{shop_id}:{issue_id}"


def parse_cookie_string(raw):
    pairs = {}
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            pairs[key] = value.strip()
    return pairs


def build_session():
    saved = load_json(COOKIE_FILE)
    if not saved:
        return None

    session = requests.Session()
    for name, value in saved.items():
        session.cookies.set(name, value, domain=".tanyuai.com")

    try:
        session.get("https://agent.tanyuai.com", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    except requests.RequestException:
        pass
    return session


def request_headers():
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Referer": REFERER,
        "User-Agent": "Mozilla/5.0",
    }


def normalize_datetime(value, fallback):
    value = (value or fallback).strip()
    if not value:
        value = fallback
    value = value.replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", value):
        value += ":00"
    return value





@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("index"))
    if is_authenticated():
        return redirect(url_for("index"))
    error = ""
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = load_users().get(username)
        if not user or not check_password_hash(user.get("passwordHash", ""), password):
            error = "账号或密码错误"
        elif not user.get("active", True):
            error = "账号已被禁用"
        elif is_user_expired(user):
            error = "账号已过期"
        else:
            users = load_users()
            users[username]["lastLoginAt"] = now_iso()
            save_users(users)
            login_user(username, remember=bool(request.form.get("remember")))
            return redirect(url_for("index"))
    return render_template("login.html", username=username, error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if not auth_enabled():
        return redirect(url_for("index"))
    error = ""
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        users = load_users()
        if not valid_username(username):
            error = "账号 2-32 位，支持中英文数字下划线"
        elif username in users:
            error = "账号已存在"
        elif len(password) < 6:
            error = "密码至少 6 位"
        elif password != password2:
            error = "两次密码不一致"
        else:
            users[username] = {
                "username": username,
                "passwordHash": generate_password_hash(password),
                "role": "user",
                "active": True,
                "expiresAt": "",
                "createdAt": now_iso(),
                "updatedAt": now_iso(),
                "lastLoginAt": now_iso(),
            }
            save_users(users)
            login_user(username, remember=True)
            return redirect(url_for("index"))
    return render_template("register.html", username=username, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users")
@require_admin
def admin_users_page():
    return render_template("admin.html")


@app.route("/api/me")
@require_auth
def api_me():
    user = current_user()
    return jsonify({"success": True, "user": public_user(user) if user else None})


@app.route("/api/admin/users")
@require_admin
def api_admin_users():
    users = load_users()
    rows = [public_user(user) for user in users.values()]
    rows.sort(key=lambda item: (item.get("role") != "admin", item.get("username", "")))
    return jsonify({"success": True, "users": rows})


@app.route("/api/admin/users/create", methods=["POST"])
@require_admin
def api_admin_create_user():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    role = str(data.get("role", "user")).strip()
    if not valid_username(username):
        return jsonify({"success": False, "error": "账号格式不正确"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "error": "密码至少 6 位"}), 400
    if role not in ("user", "admin"):
        return jsonify({"success": False, "error": "角色不正确"}), 400
    users = load_users()
    if username in users:
        return jsonify({"success": False, "error": "账号已存在"}), 400
    users[username] = {
        "username": username,
        "passwordHash": generate_password_hash(password),
        "role": role,
        "active": True,
        "expiresAt": "",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "lastLoginAt": "",
    }
    save_users(users)
    return jsonify({"success": True, "user": public_user(users[username])})


@app.route("/api/admin/users/update", methods=["POST"])
@require_admin
def api_admin_update_user():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    users = load_users()
    user = users.get(username)
    if not user:
        return jsonify({"success": False, "error": "账号不存在"}), 404
    if "password" in data:
        password = str(data.get("password", ""))
        if len(password) < 6:
            return jsonify({"success": False, "error": "密码至少 6 位"}), 400
        user["passwordHash"] = generate_password_hash(password)
    if "active" in data:
        if username == session.get("username") and data.get("active") is False:
            return jsonify({"success": False, "error": "不能禁用当前登录管理员"}), 400
        user["active"] = bool(data.get("active"))
    if "role" in data:
        role = str(data.get("role", "")).strip()
        if role not in ("user", "admin"):
            return jsonify({"success": False, "error": "角色不正确"}), 400
        user["role"] = role
    if "expiresAt" in data:
        expires_at = str(data.get("expiresAt", "") or "").strip()
        if expires_at:
            try:
                datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"success": False, "error": "到期时间格式不正确"}), 400
        user["expiresAt"] = expires_at
    user["updatedAt"] = now_iso()
    users[username] = user
    save_users(users)
    return jsonify({"success": True, "user": public_user(user)})


@app.route("/api/admin/users/delete", methods=["POST"])
@require_admin
def api_admin_delete_user():
    username = str((request.get_json() or {}).get("username", "")).strip()
    if username == session.get("username"):
        return jsonify({"success": False, "error": "不能删除当前登录管理员"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"success": False, "error": "账号不存在"}), 404
    admin_count = sum(1 for user in users.values() if user.get("role") == "admin" and user.get("active", True))
    if users[username].get("role") == "admin" and admin_count <= 1:
        return jsonify({"success": False, "error": "至少保留一个启用管理员"}), 400
    del users[username]
    save_users(users)
    return jsonify({"success": True})


@app.route("/")
@require_auth
def index():
    return render_template("index.html"), 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/cookie-status")
@require_auth
def cookie_status():
    return jsonify({"hasCookie": bool(load_json(COOKIE_FILE))})


@app.route("/api/save-cookie", methods=["POST"])
@require_auth
def save_cookie():
    pairs = parse_cookie_string((request.get_json() or {}).get("cookie", ""))
    if not pairs:
        return jsonify({"success": False, "error": "Cookie 为空或格式无效"}), 400
    save_json(COOKIE_FILE, pairs)
    return jsonify({"success": True})


@app.route("/api/shops")
@require_auth
def list_shops():
    shops = load_json(SHOPS_FILE, {})
    return jsonify({"shops": [{"id": k, "name": v.get("name", k)} for k, v in shops.items()], "current": None})


@app.route("/api/set-shop", methods=["POST"])
@require_auth
def set_shop():
    return jsonify({"success": True})


@app.route("/api/probe-shop", methods=["POST"])
@require_auth
def probe_shop():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400

    raw_cookie = data.get("cookie", "")
    if raw_cookie:
        pairs = parse_cookie_string(raw_cookie)
        if pairs:
            save_json(COOKIE_FILE, pairs)

    session = build_session()
    if not session:
        return jsonify({"success": False, "error": "请先配置 Cookie"})

    body = {
        "thirdShopId": shop_id,
        "pageIndex": 1,
        "pageSize": 1,
        "beginTime": "2024-01-01 00:00:00",
        "endTime": datetime.now().strftime("%Y-%m-%d 23:59:59"),
        "sendType": [0, 3],
    }
    try:
        resp = session.post(TRACE_API, json=body, headers=request_headers(), timeout=30)
        result = resp.json()
    except (requests.RequestException, ValueError) as e:
        return jsonify({"success": False, "error": str(e)})

    if result.get("code") != 1:
        return jsonify({"success": False, "error": result.get("msg", "认证失败")})

    data_obj = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
    total = data_obj.get("total", 0)
    results = data_obj.get("results", [])
    shop_name = results[0].get("shopName", "") if results else ""

    shops = load_json(SHOPS_FILE, {})
    shops[shop_id] = {"name": shop_name or shop_id, "total": total, "updated": datetime.now().isoformat()}
    save_json(SHOPS_FILE, shops)
    return jsonify({"success": True, "shopName": shop_name or shop_id, "total": total})


@app.route("/api/delete-shop", methods=["POST"])
@require_auth
def delete_shop():
    shop_id = str((request.get_json() or {}).get("shopId", "")).strip()
    shops = load_json(SHOPS_FILE, {})
    if shop_id in shops:
        del shops[shop_id]
        save_json(SHOPS_FILE, shops)

    for path in (data_file(shop_id), analysis_file(shop_id)):
        if shop_id and os.path.exists(path):
            os.remove(path)
    return jsonify({"success": True})


@app.route("/api/fetch", methods=["POST"])
@require_auth
def fetch_data():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请指定店铺 ID"}]})

    session = build_session()
    if not session:
        return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请先配置 Cookie"}]})

    begin_time = normalize_datetime(data.get("beginTime"), "2024-01-01 00:00:00")
    end_time = normalize_datetime(data.get("endTime"), datetime.now().strftime("%Y-%m-%d 23:59:59"))
    page_size = max(int(data.get("pageSize") or 100), 1)
    max_pages = max(int(data.get("maxPages") or 10), 1)
    filters = data.get("filters", {}) or {}
    raw_payload = data.get("rawPayload", "")
    payload_template = {}
    if isinstance(raw_payload, str) and raw_payload.strip():
        try:
            parsed_payload = json.loads(raw_payload)
            if isinstance(parsed_payload, dict):
                payload_template = parsed_payload
        except json.JSONDecodeError:
            return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "Request Payload 不是合法 JSON"}]})

    all_records = []
    fetch_log = []
    shop_name = None
    first_request_body = None
    for page_idx in range(1, max_pages + 1):
        body = dict(payload_template)
        body.update({
            "thirdShopId": shop_id,
            "pageIndex": page_idx,
            "pageSize": page_size,
            "beginTime": begin_time,
            "endTime": end_time,
        })
        for key in ("reviewStatus", "ifLabel", "type", "busi"):
            if filters.get(key) is not None and filters.get(key) != "":
                body[key] = filters[key]
        send_type = filters.get("sendType")
        if send_type and sorted(send_type) != [0, 1, 2, 3]:
            body["sendType"] = send_type
        if first_request_body is None:
            first_request_body = dict(body)

        try:
            resp = session.post(TRACE_API, json=body, headers=request_headers(), timeout=30)
            result = resp.json()
        except (requests.RequestException, ValueError) as e:
            fetch_log.append({"page": page_idx, "status": "error", "msg": str(e)})
            break

        if result.get("code") != 1:
            fetch_log.append({"page": page_idx, "status": "error", "msg": result.get("msg", "认证失败")})
            break

        data_obj = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        records = data_obj.get("results", []) or []
        total = data_obj.get("total", 0) or 0
        if not records:
            fetch_log.append({"page": page_idx, "status": "empty"})
            break

        if not shop_name:
            shop_name = records[0].get("shopName", "")
        all_records.extend(records)
        fetch_log.append({
            "page": page_idx,
            "status": "ok",
            "count": len(records),
            "total": total,
            "ids": [str(record.get("id", "")) for record in records[:5] if isinstance(record, dict)],
            "requestBody": body if page_idx == 1 else None,
        })
        if total and len(all_records) >= total:
            break
        time.sleep(0.3)

    df = data_file(shop_id)
    total_fetched = 0
    if all_records:
        if data.get("overwrite", False):
            save_json(df, all_records)
            total_fetched = len(all_records)
        else:
            existing = load_json(df, [])
            seen = {r.get("id") for r in existing if isinstance(r, dict)}
            new_records = [r for r in all_records if isinstance(r, dict) and r.get("id") not in seen]
            save_json(df, existing + new_records)
            total_fetched = len(new_records)

    if shop_name:
        shops = load_json(SHOPS_FILE, {})
        shops[shop_id] = {"name": shop_name, "updated": datetime.now().isoformat()}
        save_json(SHOPS_FILE, shops)

    return jsonify({
        "success": True,
        "totalFetched": total_fetched,
        "totalStored": len(load_json(df, [])),
        "shopName": shop_name,
        "requestBody": first_request_body,
        "log": fetch_log,
    })


STOP_WORDS = set("""
的 了 和 是 在 我 有 不 就 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没 看 好 自己 这 那
可以 需要 现在 已经 如果 因为 所以 但是 然后 之后 之前 比较 非常 真的 知道 觉得 应该 可能
全部 您好 请问 感谢 麻烦 辛苦 稍等 稍后 买家 卖家 客服
""".split())

QA_TOPICS = {
    "退款纠纷": ["退款", "退货", "退钱", "仅退款", "退货退款"],
    "物流问题": ["物流", "快递", "发货", "配送", "签收", "未收到", "查件", "延迟", "超时"],
    "商品质量": ["质量", "瑕疵", "破损", "损坏", "坏了", "有问题", "缺陷", "色差"],
    "客服态度": ["态度", "投诉", "不理人", "敷衍", "辱骂", "服务差", "不回复"],
    "优惠促销": ["优惠券", "折扣", "满减", "活动", "赠品", "积分", "会员", "促销"],
    "订单问题": ["订单", "取消", "修改", "改地址", "备注", "改价"],
    "售后处理": ["售后", "保修", "维修", "换货", "补偿", "赔偿", "维权"],
    "库存问题": ["库存", "缺货", "断货", "补货", "到货", "预售", "下架"],
}


WEAK_REPLY_PATTERNS = [
    "不知道", "不清楚", "无法", "不能确定", "不确定", "抱歉", "不好意思",
    "请咨询", "联系人工", "转人工", "人工客服", "稍后", "请稍等",
    "没有权限", "系统繁忙", "暂不支持", "无法识别", "没听懂",
]

ADOPTION_POSITIVE_PATTERNS = [
    "好的", "谢谢", "明白", "知道了", "可以", "已解决", "收到", "行",
]

ADOPTION_NEGATIVE_PATTERNS = [
    "不是", "不对", "没用", "没解决", "答非所问", "人工", "转人工",
    "还是", "继续", "又", "不明白", "看不懂",
]

NEGATIVE_PATTERNS = [
    "投诉", "差评", "生气", "不满意", "骗人", "垃圾", "退钱", "退款",
    "怎么还", "为什么不", "没人理", "不回复", "太慢", "催",
]

QUESTION_NOISE_WORDS = [
    "请问", "你好", "您好", "麻烦", "帮我", "一下", "可以", "能不能",
    "怎么", "如何", "为什么", "吗", "呢", "呀", "啊", "哦",
]


def normalize_question_text(question):
    text = str(question or "").lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\d{5,}", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    for word in QUESTION_NOISE_WORDS:
        text = text.replace(word, "")
    return text[:32] or str(question or "")[:32]


def classify_reply_quality(question, answer, record):
    reasons = []
    answer_text = str(answer or "").strip()
    question_text = str(question or "").strip()
    search_text = str(record.get("searchContent", "") or "")
    combined = f"{question_text} {answer_text} {search_text}"

    if not answer_text or answer_text.startswith("参考话题："):
        reasons.append("智能体无有效回复")
    if answer_text and len(answer_text) <= 8:
        reasons.append("回复过短")
    weak_hits = [pattern for pattern in WEAK_REPLY_PATTERNS if pattern in answer_text]
    if weak_hits:
        reasons.append("弱回复/兜底话术：" + "、".join(weak_hits[:3]))
    if any(pattern in combined for pattern in NEGATIVE_PATTERNS):
        reasons.append("包含负向/高风险表达")
    if search_text.count("买家") >= 2 and len(answer_text) < 30:
        reasons.append("用户多轮追问但回复不足")

    if not reasons:
        return "正常", []
    if any("无有效回复" in reason for reason in reasons):
        return "未回复", reasons
    if any("负向" in reason for reason in reasons):
        return "高风险", reasons
    return "弱回复", reasons


def infer_issue_topic(question, record):
    text = f"{question} {record.get('topicName', '') or ''} {record.get('searchContent', '') or ''}"
    for topic, keywords in QA_TOPICS.items():
        if any(keyword in text for keyword in keywords):
            return topic
    topic_name = str(record.get("topicName", "") or "").strip()
    return topic_name[:20] if topic_name else "未分类问题"


def priority_score(issue_type, count, unresolved_count, negative_count):
    score = count * 2 + unresolved_count * 6 + negative_count * 4
    if issue_type == "未回复":
        score += 12
    elif issue_type == "高风险":
        score += 10
    elif issue_type == "弱回复":
        score += 6
    if issue_type in ("未回复", "高风险") and count >= 1:
        return "高", score
    if score >= 24:
        return "高", score
    if score >= 10:
        return "中", score
    return "低", score


def build_training_suggestion(issue_type, topic, question, answer):
    if issue_type == "未回复":
        action = "补充知识库标准答案"
        suggestion = f"为“{topic}”补充可直接命中的标准问法和回复，覆盖该问题的同义问法。"
    elif issue_type == "高风险":
        action = "优先优化安抚和处理流程"
        suggestion = f"补充“{topic}”的风险处理话术，明确承诺、处理路径和转人工边界。"
    elif issue_type == "弱回复":
        action = "优化回复完整度"
        suggestion = f"将当前回复改成可执行答案，包含条件、步骤、时效或售后入口。"
    else:
        action = "观察"
        suggestion = "当前样本暂不需要优先处理。"
    return action, suggestion


def build_answer_outline(topic, issue_type, question):
    base = {
        "退款纠纷": "先确认订单/售后状态，再说明可申请的退款类型、操作入口、审核时效和特殊限制。",
        "物流问题": "先确认订单发货状态，再说明发货/揽收/派送时效，提供查件方式和异常处理入口。",
        "商品质量": "先安抚用户并确认问题图片/视频，再说明退换/补偿/售后流程。",
        "客服态度": "先道歉安抚，再给出明确处理路径，必要时承诺升级人工跟进。",
        "优惠促销": "说明活动规则、使用门槛、有效期、叠加限制和未生效排查步骤。",
        "订单问题": "说明订单修改条件、可修改字段、处理时效和无法修改时的替代方案。",
        "售后处理": "说明售后入口、所需凭证、审核时效、退换/维修/补偿规则。",
        "库存问题": "说明当前库存/补货/预售/下架状态，并给出可替代商品或到货提醒方式。",
    }
    outline = base.get(topic, "先正面回答用户问题，再补充条件、步骤、时效、异常处理和转人工边界。")
    if issue_type == "未回复":
        return "新增知识卡片：" + outline
    if issue_type == "弱回复":
        return "扩写现有回复：" + outline
    if issue_type == "高风险":
        return "高风险话术：" + outline + " 语气需先安抚，再给承诺和兜底处理方式。"
    return outline


def infer_failure_reason(issue_type, reasons, answer, record):
    answer_text = str(answer or "")
    search_text = str(record.get("searchContent", "") or "")
    if issue_type == "未回复":
        return "知识库没有答案"
    if "人工" in answer_text or "转人工" in answer_text:
        return "应该加转人工规则"
    if any(word in answer_text for word in ("请稍等", "不清楚", "无法", "不知道")):
        return "答案太泛"
    if search_text.count("买家") >= 2:
        return "已有回复但没有解决追问"
    if issue_type == "高风险":
        return "高风险场景缺少处理流程"
    if issue_type == "弱回复":
        return "有答案但触发/表达不完整"
    return "需要人工判断"


def build_standard_answer(topic, issue_type):
    templates = {
        "退款纠纷": "您可以在订单售后入口提交退款/退货退款申请。若订单已发货，需要按平台流程先申请售后并上传凭证；审核通过后会按原支付路径退款。若页面无法操作，请提供订单号和问题凭证，我们会协助核实处理。",
        "物流问题": "我先帮您确认订单发货和物流状态。正常情况下发货后会有揽收和运输更新；如物流长时间未更新、显示异常或超时未收到，可提供订单号，我们会协助查件并给出补发、催派或售后处理方案。",
        "商品质量": "如果商品存在破损、瑕疵、缺件或质量问题，请您保留包装并上传图片/视频凭证。我们会根据问题情况为您处理退换、补发、维修或补偿，具体以售后审核结果为准。",
        "客服态度": "非常抱歉给您带来不好的体验。请您说明遇到的问题和订单信息，我们会优先核实并给出明确处理方案；如涉及投诉或紧急售后，会升级人工跟进。",
        "优惠促销": "优惠是否生效通常受活动时间、商品范围、使用门槛、叠加规则和账号状态影响。请确认优惠券是否在有效期内、订单金额是否满足门槛；如仍异常，请提供活动页或订单截图核实。",
        "订单问题": "订单信息能否修改取决于当前订单状态。未发货前通常可尝试修改地址、备注或取消订单；已发货后一般无法直接修改，可联系快递或申请售后处理。",
        "售后处理": "售后处理需要根据订单状态和问题凭证判断。请在订单售后入口提交申请并上传相关凭证，我们会按平台规则审核；如情况紧急或页面无法提交，可提供订单号协助处理。",
        "库存问题": "商品库存会随销售实时变化。若当前缺货、断货或下架，建议关注补货通知或选择同类替代商品；如是已下单缺货，我们会根据订单情况协商发货、换货或退款方案。",
    }
    answer = templates.get(topic, "请先确认用户问题所属场景，再给出明确结论、操作入口、处理条件、预计时效和异常兜底方案。")
    if issue_type == "高风险":
        answer = "先安抚用户情绪并明确会处理：" + answer
    return answer


def build_manual_handoff_rule(topic, issue_type):
    if issue_type == "高风险":
        return "用户出现投诉、差评、强烈不满、退款纠纷升级或多轮追问未解决时转人工。"
    if topic in ("退款纠纷", "售后处理", "商品质量"):
        return "用户已提交售后但状态异常、要求特殊赔付、凭证争议或机器人无法判断责任时转人工。"
    if topic == "物流问题":
        return "物流超时、丢件、拒收、地址异常或用户连续追问仍无法解决时转人工。"
    return "用户连续追问两轮仍未解决、涉及订单个性化判断或系统无法查询实时状态时转人工。"


def optimization_value_score(priority, count, unresolved_count, adoption_risk):
    priority_score_map = {"高": 40, "中": 24, "低": 10}
    return min(100, priority_score_map.get(priority, 10) + count * 3 + unresolved_count * 5 + adoption_risk // 3)


def extract_trigger_words(question, topic):
    text = str(question or "")
    words = []
    for keyword in QA_TOPICS.get(topic, []):
        if keyword in text:
            words.append(keyword)
    if jieba:
        candidates = jieba.cut(text)
    else:
        candidates = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", text)
    for word in candidates:
        if len(word) >= 2 and word not in STOP_WORDS and word not in words:
            words.append(word)
        if len(words) >= 8:
            break
    return words[:8]


def estimate_adoption_risk(issue_type, reasons, answer, record):
    text = f"{answer or ''} {record.get('searchContent', '') or ''}"
    risk = 35
    if issue_type == "未回复":
        risk += 35
    elif issue_type == "高风险":
        risk += 30
    elif issue_type == "弱回复":
        risk += 20
    if any(pattern in text for pattern in ADOPTION_NEGATIVE_PATTERNS):
        risk += 15
    if any(pattern in text for pattern in ADOPTION_POSITIVE_PATTERNS):
        risk -= 10
    if any("多轮追问" in reason for reason in reasons):
        risk += 10
    return max(0, min(100, risk))


def build_issue_workbench(traces, shop_id=""):
    groups = {}
    for record in traces:
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        if not question:
            continue
        issue_type, reasons = classify_reply_quality(question, answer, record)
        if issue_type == "正常":
            continue
        topic = infer_issue_topic(question, record)
        key = f"{topic}:{normalize_question_text(question)}"
        group = groups.setdefault(key, {
            "id": re.sub(r"\W+", "_", key, flags=re.UNICODE)[:80],
            "topic": topic,
            "standardQuestion": question[:160],
            "issueType": issue_type,
            "count": 0,
            "unresolvedCount": 0,
            "negativeCount": 0,
            "adoptionRiskTotal": 0,
            "reasons": Counter(),
            "questionVariants": Counter(),
            "examples": [],
        })
        group["count"] += 1
        group["questionVariants"][question[:80]] += 1
        if issue_type in ("未回复", "弱回复"):
            group["unresolvedCount"] += 1
        if issue_type == "高风险":
            group["negativeCount"] += 1
        group["adoptionRiskTotal"] += estimate_adoption_risk(issue_type, reasons, answer, record)
        for reason in reasons:
            group["reasons"][reason] += 1
        if len(group["examples"]) < 5:
            identity = extract_trace_identity(record)
            group["examples"].append({
                "id": record.get("id", ""),
                "question": question[:240],
                "answer": answer[:600],
                "seller": record.get("sellerAccount", ""),
                "type": record.get("type", ""),
                "topicName": record.get("topicName", ""),
                "identity": compact_identity(identity),
            })
        severity_order = {"未回复": 3, "高风险": 2, "弱回复": 1, "正常": 0}
        if severity_order.get(issue_type, 0) > severity_order.get(group["issueType"], 0):
            group["issueType"] = issue_type

    status_map = load_json(ISSUE_STATUS_FILE, {})
    issues = []
    for issue in groups.values():
        priority, score = priority_score(
            issue["issueType"], issue["count"], issue["unresolvedCount"], issue["negativeCount"]
        )
        sample = issue["examples"][0] if issue["examples"] else {}
        action, suggestion = build_training_suggestion(
            issue["issueType"], issue["topic"], sample.get("question", ""), sample.get("answer", "")
        )
        trigger_words = extract_trigger_words(issue["standardQuestion"], issue["topic"])
        answer_outline = build_answer_outline(issue["topic"], issue["issueType"], issue["standardQuestion"])
        failure_reason = infer_failure_reason(
            issue["issueType"], [reason for reason, _ in issue["reasons"].most_common(3)], sample.get("answer", ""), sample
        )
        adoption_risk = round(issue["adoptionRiskTotal"] / max(issue["count"], 1))
        optimization_value = optimization_value_score(priority, issue["count"], issue["unresolvedCount"], adoption_risk)
        issue_id = issue["id"]
        state = status_map.get(issue_status_key(shop_id, issue_id), "待处理")
        issues.append({
            "id": issue_id,
            "topic": issue["topic"],
            "standardQuestion": issue["standardQuestion"],
            "issueType": issue["issueType"],
            "priority": priority,
            "score": score,
            "count": issue["count"],
            "unresolvedCount": issue["unresolvedCount"],
            "negativeCount": issue["negativeCount"],
            "adoptionRisk": adoption_risk,
            "optimizationValue": optimization_value,
            "failureReason": failure_reason,
            "reasons": [reason for reason, _ in issue["reasons"].most_common(3)],
            "suggestedAction": action,
            "trainingSuggestion": suggestion,
            "knowledgeCardDraft": {
                "title": f"{issue['topic']}｜{issue['standardQuestion'][:28]}",
                "standardQuestion": issue["standardQuestion"],
                "similarQuestions": [text for text, _ in issue["questionVariants"].most_common(5)],
                "triggerWords": trigger_words,
                "answerOutline": answer_outline,
                "standardAnswer": build_standard_answer(issue["topic"], issue["issueType"]),
                "manualHandoffRule": build_manual_handoff_rule(issue["topic"], issue["issueType"]),
                "applicableScene": f"适用于用户咨询“{issue['topic']}”相关问题，且机器人可按规则给出明确步骤或处理路径的场景。",
                "notApplicableScene": "涉及订单责任争议、特殊赔付、实时系统无法查询或用户情绪明显升级时不适用，应转人工。",
                "acceptanceGoal": "让智能体正面回答问题，减少转人工和重复追问，提升人工采纳率。",
            },
            "status": state,
            "examples": issue["examples"],
        })
    return sorted(issues, key=lambda item: (-item["score"], -item["count"], item["topic"]))


def build_store_diagnosis(traces, issue_workbench):
    total = len(traces)
    issue_count = len(issue_workbench)
    high_count = sum(1 for issue in issue_workbench if issue.get("priority") == "高")
    unresolved = sum(issue.get("unresolvedCount", 0) for issue in issue_workbench)
    risk_avg = round(sum(issue.get("adoptionRisk", 0) for issue in issue_workbench) / max(issue_count, 1))
    topic_counter = Counter(issue.get("topic", "未分类问题") for issue in issue_workbench)
    action_counter = Counter(issue.get("suggestedAction", "待判断") for issue in issue_workbench)
    processed = sum(1 for issue in issue_workbench if issue.get("status") in ("已补知识", "已优化话术", "复查通过", "忽略"))
    review_count = sum(1 for issue in issue_workbench if issue.get("status") in ("已补知识", "已优化话术"))
    pending = max(issue_count - processed, 0)
    health = max(0, 100 - high_count * 8 - unresolved * 3 - risk_avg // 2)

    summary = []
    if high_count:
        summary.append(f"发现 {high_count} 个高优先级问题，建议优先处理未回复和高风险售后/退款/物流问题。")
    if unresolved:
        summary.append(f"存在 {unresolved} 条未解决或弱回复样本，适合直接沉淀为知识卡片。")
    if topic_counter:
        topic, count = topic_counter.most_common(1)[0]
        summary.append(f"当前问题最集中在“{topic}”，共 {count} 个问题簇。")
    if not summary:
        summary.append("未发现明显高风险问题，可继续观察新增样本。")

    top_tasks = []
    high_pending = [issue for issue in issue_workbench if issue.get("priority") == "高" and issue.get("status") == "待处理"]
    if high_pending:
        top_tasks.append({"title": f"优先处理 {min(len(high_pending), 5)} 个高优先级问题", "type": "补卡", "count": len(high_pending)})
    weak_pending = [issue for issue in issue_workbench if issue.get("issueType") == "弱回复" and issue.get("status") == "待处理"]
    if weak_pending:
        top_tasks.append({"title": f"扩写 {min(len(weak_pending), 5)} 个弱回复答案", "type": "话术", "count": len(weak_pending)})
    review_candidates = [issue for issue in issue_workbench if issue.get("status") in ("已补知识", "已优化话术") and issue.get("count", 0) > 1]
    if review_candidates:
        top_tasks.append({"title": f"复查 {min(len(review_candidates), 5)} 个已优化但仍出现的问题", "type": "复查", "count": len(review_candidates)})
    ignored = [issue for issue in issue_workbench if issue.get("priority") == "低" and issue.get("status") == "待处理"]
    if ignored:
        top_tasks.append({"title": f"快速判断 {min(len(ignored), 8)} 个低优先级样本是否忽略", "type": "清理", "count": len(ignored)})

    return {
        "healthScore": health,
        "totalRecords": total,
        "issueCount": issue_count,
        "highPriorityCount": high_count,
        "pendingCount": pending,
        "reviewCount": review_count,
        "unresolvedCount": unresolved,
        "adoptionRiskAvg": risk_avg,
        "topIssueTopics": [{"topic": topic, "count": count} for topic, count in topic_counter.most_common(6)],
        "actionDistribution": [{"action": action, "count": count} for action, count in action_counter.most_common(6)],
        "summary": summary,
        "todayTasks": top_tasks[:5],
    }


def build_topic_insights(traces, topic_dist, issue_workbench, top_keywords):
    total = max(len(traces), 1)
    issue_by_topic = defaultdict(list)
    for issue in issue_workbench:
        issue_by_topic[issue.get("topic", "未分类问题")].append(issue)

    topic_rows = []
    seen_topics = set()
    for item in topic_dist:
        topic = item.get("topic", "未分类问题")
        seen_topics.add(topic)
        issues = issue_by_topic.get(topic, [])
        unresolved = sum(issue.get("unresolvedCount", 0) for issue in issues)
        high = sum(1 for issue in issues if issue.get("priority") == "高")
        risk = round(sum(issue.get("adoptionRisk", 0) for issue in issues) / max(len(issues), 1))
        gap = len([issue for issue in issues if issue.get("status") not in ("已补知识", "已优化话术", "忽略")])
        topic_rows.append({
            "topic": topic,
            "count": item.get("count", 0),
            "percentage": item.get("percentage", 0),
            "issueCount": len(issues),
            "highPriorityCount": high,
            "unresolvedCount": unresolved,
            "knowledgeGapCount": gap,
            "adoptionRisk": risk,
            "suggestedAction": "优先补卡" if gap else "持续观察",
        })

    for topic, issues in issue_by_topic.items():
        if topic in seen_topics:
            continue
        unresolved = sum(issue.get("unresolvedCount", 0) for issue in issues)
        high = sum(1 for issue in issues if issue.get("priority") == "高")
        risk = round(sum(issue.get("adoptionRisk", 0) for issue in issues) / max(len(issues), 1))
        gap = len([issue for issue in issues if issue.get("status") not in ("已补知识", "已优化话术", "忽略")])
        topic_rows.append({
            "topic": topic,
            "count": 0,
            "percentage": 0,
            "issueCount": len(issues),
            "highPriorityCount": high,
            "unresolvedCount": unresolved,
            "knowledgeGapCount": gap,
            "adoptionRisk": risk,
            "suggestedAction": "优先补卡" if gap else "持续观察",
        })

    topic_rows = sorted(
        topic_rows,
        key=lambda row: (row["highPriorityCount"], row["knowledgeGapCount"], row["adoptionRisk"], row["count"]),
        reverse=True,
    )
    total_issue = sum(row["issueCount"] for row in topic_rows)
    total_gap = sum(row["knowledgeGapCount"] for row in topic_rows)
    avg_risk = round(sum(row["adoptionRisk"] for row in topic_rows) / max(len(topic_rows), 1))
    return {
        "topicCount": len(topic_rows),
        "coverageRate": round(sum(row["count"] for row in topic_rows) / total * 100, 1),
        "issueTopicCount": len([row for row in topic_rows if row["issueCount"]]),
        "knowledgeGapCount": total_gap,
        "avgAdoptionRisk": avg_risk,
        "totalIssueCount": total_issue,
        "topics": topic_rows[:12],
        "riskTopics": sorted(topic_rows, key=lambda row: (row["adoptionRisk"], row["highPriorityCount"]), reverse=True)[:6],
        "gapTopics": sorted(topic_rows, key=lambda row: (row["knowledgeGapCount"], row["unresolvedCount"]), reverse=True)[:6],
        "keywords": top_keywords[:12],
    }


def parse_conversation(record):
    question = ""
    answer = ""

    q = record.get("question", "")
    if isinstance(q, str) and q.strip():
        question = re.sub(r"^(买家|buyer)\s*[:：]\s*", "", q.strip(), flags=re.I)

    content = record.get("content")
    if isinstance(content, list):
        answer = "\n".join(str(c) for c in content if isinstance(c, str) and c.strip())
    elif isinstance(content, str):
        answer = content.strip()

    search_content = record.get("searchContent", "")
    if isinstance(search_content, str) and search_content:
        if not question:
            buyer_match = re.search(r"(?:买家|buyer)\s*[:：]\s*(.+?)(?:\[\[|$)", search_content, re.I | re.S)
            if buyer_match:
                question = buyer_match.group(1).strip()
        if not answer:
            seller_match = re.search(r"\[\[(.+?)\]\]", search_content, re.S)
            if seller_match:
                answer = seller_match.group(1).replace(" , ", "\n").replace(", ", "\n").strip()

    if not question:
        question = str(record.get("topicName", "") or "")
    if not answer and record.get("topicName"):
        answer = f"参考话题：{record.get('topicName')}"
    return question, answer


def extract_trace_identity(record):
    product = record.get("productInfo") if isinstance(record.get("productInfo"), dict) else {}

    def pick(*keys):
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def pick_product(*keys):
        for key in keys:
            value = product.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    goods_code = pick_product("goodsCode", "goodsId", "itemId", "productId")
    spu_id = pick_product("spuId")
    sku_id = pick_product("skuId")
    product_title = pick_product("spuTitle", "title", "goodsTitle", "productTitle")
    product_id = goods_code or spu_id or sku_id

    # Extract buyer info with fallback chain
    raw_buyer = pick("buyerAccount", "buyerId", "buyerNick", "buyerName", "userId")

    # Try to get a shorter alias from buyerAlias/buyerNick/buyerShowName
    buyer_alias = pick("buyerAlias", "buyerNick", "buyerShowName", "buyerNickname")
    if buyer_alias:
        buyer_display = buyer_alias
    elif raw_buyer and len(raw_buyer) > 32:
        # Truncate long opaque buyer IDs
        buyer_display = raw_buyer[:16] + "..." + raw_buyer[-12:]
    else:
        buyer_display = raw_buyer

    return {
        "traceId": pick("id", "traceId"),
        "shopId": pick("thirdShopId"),
        "shopName": pick("shopName"),
        "buyerId": buyer_display,
        "buyerIdRaw": raw_buyer,
        "sellerId": pick("sellerAccount", "sellerId"),
        "productId": product_id,
        "goodsCode": goods_code,
        "spuId": spu_id,
        "skuId": sku_id,
        "productTitle": product_title,
        "productUrl": pick_product("detailUrl", "url"),
        "productImage": pick_product("img", "image", "picUrl"),
        "platform": pick("platform"),
        "type": pick("type"),
        "time": pick("time"),
        "topicName": pick("topicName"),
        "hasBuyer": bool(raw_buyer),
        "hasProduct": bool(product_id or product_title),
    }


def compact_identity(identity):
    return {
        key: value
        for key, value in identity.items()
        if value not in ("", None, False)
    }


def build_identity_insights(traces, issue_workbench):
    product_counter = Counter()
    buyer_counter = Counter()
    product_titles = {}
    product_sku = {}
    product_urls = {}
    buyer_products = defaultdict(set)
    issue_products = Counter()
    issue_buyers = Counter()
    missing_product = 0
    missing_buyer = 0

    for record in traces:
        if not isinstance(record, dict):
            continue
        identity = extract_trace_identity(record)
        product_key = identity.get("productId") or identity.get("productTitle")
        buyer_key = identity.get("buyerId")
        if product_key:
            product_counter[product_key] += 1
            product_titles[product_key] = identity.get("productTitle") or product_key
            product_sku[product_key] = identity.get("skuId") or ""
            product_urls[product_key] = identity.get("productUrl") or ""
        else:
            missing_product += 1
        if buyer_key:
            buyer_counter[buyer_key] += 1
            if product_key:
                buyer_products[buyer_key].add(product_key)
        else:
            missing_buyer += 1

    for issue in issue_workbench:
        for example in issue.get("examples", []):
            identity = example.get("identity", {}) if isinstance(example.get("identity"), dict) else {}
            product_key = identity.get("productId") or identity.get("productTitle")
            buyer_key = identity.get("buyerId")
            if product_key:
                issue_products[product_key] += 1
            if buyer_key:
                issue_buyers[buyer_key] += 1

    def product_row(item):
        product_key, count = item
        return {
            "productId": product_key,
            "productTitle": product_titles.get(product_key, product_key),
            "skuId": product_sku.get(product_key, ""),
            "productUrl": product_urls.get(product_key, ""),
            "traceCount": count,
            "issueCount": issue_products.get(product_key, 0),
        }

    def buyer_row(item):
        buyer_key, count = item
        return {
            "buyerId": buyer_key,
            "traceCount": count,
            "issueCount": issue_buyers.get(buyer_key, 0),
            "productCount": len(buyer_products.get(buyer_key, set())),
        }

    total = max(len(traces), 1)
    return {
        "productCoverage": round((len(traces) - missing_product) / total * 100, 1),
        "buyerCoverage": round((len(traces) - missing_buyer) / total * 100, 1),
        "productCount": len(product_counter),
        "buyerCount": len(buyer_counter),
        "missingProduct": missing_product,
        "missingBuyer": missing_buyer,
        "topProducts": [product_row(item) for item in product_counter.most_common(8)],
        "issueProducts": [product_row(item) for item in issue_products.most_common(8)],
        "topBuyers": [buyer_row(item) for item in buyer_counter.most_common(8)],
        "issueBuyers": [buyer_row(item) for item in issue_buyers.most_common(8)],
    }


def run_analysis(traces, shop_id=""):
    if not traces:
        return {"error": "没有可分析的数据"}

    topic_groups = defaultdict(list)
    kw_groups = defaultdict(list)
    for record in traces:
        if not isinstance(record, dict):
            continue
        topic_name = str(record.get("topicName", "") or "").strip()
        if topic_name:
            topic_groups[topic_name[:20]].append(record)

        text = " ".join(str(record.get(k, "") or "") for k in ("searchContent", "topicName", "sellerAccount", "type"))
        for topic, keywords in QA_TOPICS.items():
            if any(keyword in text for keyword in keywords):
                kw_groups[topic].append(record)

    total = len(traces)
    with_content = sum(
        1
        for record in traces
        if isinstance(record, dict)
        and ((record.get("searchContent") or "").strip() or bool(record.get("content")))
    )

    topic_dist = [
        {"topic": topic, "count": len(records), "percentage": round(len(records) / total * 100, 1)}
        for topic, records in sorted(kw_groups.items(), key=lambda item: -len(item[1]))
    ]

    if not topic_dist and topic_groups:
        topic_dist = [
            {"topic": topic, "count": len(records), "percentage": round(len(records) / total * 100, 1)}
            for topic, records in sorted(topic_groups.items(), key=lambda item: -len(item[1]))
        ]

    qa_examples = []
    for topic_name, records in sorted(topic_groups.items(), key=lambda item: -len(item[1])):
        examples = []
        for record in records:
            if len(examples) >= 10:
                break
            question, answer = parse_conversation(record)
            if question and answer:
                identity = extract_trace_identity(record)
                examples.append({
                    "id": record.get("id", ""),
                    "question": question[:200],
                    "answer": answer[:500],
                    "seller": record.get("sellerAccount", ""),
                    "type": record.get("type", ""),
                    "topicName": topic_name,
                    "identity": compact_identity(identity),
                })
        if examples:
            qa_examples.append({"topic": topic_name, "count": len(records), "examples": examples})

    issue_workbench = build_issue_workbench(traces, shop_id)
    store_diagnosis = build_store_diagnosis(traces, issue_workbench)
    identity_insights = build_identity_insights(traces, issue_workbench)

    combined = " ".join(
        f"{record.get('searchContent', '') or ''} {record.get('topicName', '') or ''}"
        for record in traces
        if isinstance(record, dict)
    )
    if jieba:
        words = jieba.cut(combined)
    else:
        words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", combined)
    filtered = [word for word in words if len(word) >= 2 and word not in STOP_WORDS and not word.isdigit()]

    top_keywords = [{"word": word, "count": count} for word, count in Counter(filtered).most_common(50)]
    topic_insights = build_topic_insights(traces, topic_dist, issue_workbench, top_keywords)

    return {
        "totalRecords": total,
        "withContent": with_content,
        "analyzedAt": datetime.now().isoformat(),
        "topicDistribution": topic_dist,
        "topicInsights": topic_insights,
        "qaExamples": qa_examples,
        "issueWorkbench": issue_workbench,
        "storeDiagnosis": store_diagnosis,
        "identityInsights": identity_insights,
        "topKeywords": top_keywords,
    }


@app.route("/api/analyze", methods=["POST"])
@require_auth
def analyze():
    shop_id = str((request.get_json() or {}).get("shopId", "")).strip()
    traces = load_json(data_file(shop_id), [])
    result = run_analysis(traces, shop_id)
    if shop_id:
        save_json(analysis_file(shop_id), result)
    return jsonify({"success": True, "data": result})


@app.route("/api/analysis")
@require_auth
def get_analysis():
    shop_id = request.args.get("shopId", "").strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
    analysis = load_json(analysis_file(shop_id), {})
    if analysis and ("storeDiagnosis" not in analysis or "topicInsights" not in analysis):
        traces = load_json(data_file(shop_id), [])
        if traces:
            analysis = run_analysis(traces, shop_id)
            save_json(analysis_file(shop_id), analysis)
    if not analysis:
        return jsonify({"success": False, "error": "暂无分析结果"})
    return jsonify({"success": True, "data": analysis})


@app.route("/api/export-chat", methods=["GET", "POST"])
@require_auth
def export_chat():
    """Export chat records as CSV from fetched traces."""
    if request.method == "GET":
        shop_id = request.args.get("shopId", "").strip()
        begin_time = request.args.get("beginTime", "")
        end_time = request.args.get("endTime", "")
    else:
        data = request.get_json() or {}
        shop_id = str(data.get("shopId", "")).strip()
        begin_time = data.get("beginTime", "")
        end_time = data.get("endTime", "")

    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})
    traces = load_json(data_file(shop_id), [])
    if not traces:
        return jsonify({"success": False, "error": "没有可导出的数据"})

    # Optional time filters
    if begin_time:
        begin_ts = datetime.fromisoformat(begin_time.replace("T", " ").rstrip(":00")).timestamp() * 1000
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) >= begin_ts]
    if end_time:
        end_ts = datetime.fromisoformat(end_time.replace("T", " ").rstrip(":00")).timestamp() * 1000
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) <= end_ts]

    rows = []
    for record in traces:
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        buyer = record.get("buyerAccount", "")
        seller = record.get("sellerAccount", "")
        topic = record.get("topicName", "")
        trace_id = record.get("id", "")
        product_info = record.get("productInfo", {}) if isinstance(record.get("productInfo"), dict) else {}
        product_title = product_info.get("spuTitle", "") or product_info.get("title", "") or ""
        sku_id = product_info.get("skuId", "") or ""
        spu_id = product_info.get("spuId", "") or ""
        ts = record.get("time", 0)
        try:
            dt_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dt_str = str(ts)

        rows.append({
            "traceId": trace_id,
            "时间": dt_str,
            "买家": buyer[:50],
            "卖家": seller[:50],
            "话题": topic[:50],
            "商品": product_title[:100],
            "SPU": spu_id,
            "SKU": sku_id,
            "用户问题": question[:500],
            "智能体回复": answer[:1000],
            "状态": record.get("status", ""),
            "类型": record.get("type", ""),
        })

    if not rows:
        return jsonify({"success": False, "error": "没有匹配的聊天记录"})

    # Build CSV
    output = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    csv_content = output.getvalue()
    output.close()

    from flask import Response
    return Response(
        csv_content.encode("utf-8-sig"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=chat-{shop_id}-{int(time.time())}.csv"}
    )


@app.route("/api/chat-records", methods=["GET", "POST"])
@require_auth
def chat_records():
    """Return chat records as JSON for inline viewing."""
    if request.method == "GET":
        shop_id = request.args.get("shopId", "").strip()
        begin_time = request.args.get("beginTime", "")
        end_time = request.args.get("endTime", "")
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("pageSize", 50))
    else:
        data = request.get_json() or {}
        shop_id = str(data.get("shopId", "")).strip()
        begin_time = data.get("beginTime", "")
        end_time = data.get("endTime", "")
        page = int(data.get("page", 1))
        page_size = int(data.get("pageSize", 50))

    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})
    traces = load_json(data_file(shop_id), [])
    if not traces:
        return jsonify({"success": False, "error": "没有可展示的聊天记录"})

    # Apply time filters
    if begin_time:
        begin_ts = datetime.fromisoformat(begin_time.replace("T", " ").rstrip(":00")).timestamp() * 1000
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) >= begin_ts]
    if end_time:
        end_ts = datetime.fromisoformat(end_time.replace("T", " ").rstrip(":00")).timestamp() * 1000
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) <= end_ts]

    total = len(traces)
    traces = sorted(traces, key=lambda t: t.get("time", 0), reverse=True)
    start = (page - 1) * page_size
    page_traces = traces[start:start + page_size]

    records = []
    for record in page_traces:
        question, answer = parse_conversation(record)
        buyer = record.get("buyerAccount", "")
        seller = record.get("sellerAccount", "")
        topic = record.get("topicName", "")
        product_info = record.get("productInfo", {}) if isinstance(record.get("productInfo"), dict) else {}
        product_title = product_info.get("spuTitle", "") or product_info.get("title", "") or ""
        sku_id = product_info.get("skuId", "") or ""
        spu_id = product_info.get("spuId", "") or ""
        ts = record.get("time", 0)
        try:
            dt_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dt_str = str(ts)

        records.append({
            "id": record.get("id", ""),
            "time": dt_str,
            "buyer": buyer[:60],
            "seller": seller[:60],
            "topic": topic[:50],
            "product": product_title[:80],
            "spu": spu_id,
            "sku": sku_id,
            "question": question[:500],
            "answer": answer[:1000],
            "status": record.get("status", ""),
            "type": record.get("type", ""),
        })

    return jsonify({
        "success": True,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": max(1, (total + page_size - 1) // page_size),
        "records": records,
    })


@app.route("/api/issue-detail", methods=["GET", "POST"])
@require_auth
def issue_detail():
    """Fetch detailed chat records for a specific issue (by trace IDs)."""
    if request.method == "GET":
        shop_id = request.args.get("shopId", "").strip()
        trace_ids_str = request.args.get("traceIds", "")
    else:
        data = request.get_json() or {}
        shop_id = str(data.get("shopId", "")).strip()
        trace_ids_str = data.get("traceIds", "")

    if not shop_id or not trace_ids_str:
        return jsonify({"success": False, "error": "缺少参数"})
    trace_ids = [tid.strip() for tid in trace_ids_str.split(",") if tid.strip()]
    if not trace_ids:
        return jsonify({"success": False, "error": "没有trace ID"})

    traces = load_json(data_file(shop_id), [])
    if not traces:
        return jsonify({"success": False, "error": "没有数据"})

    # Filter to only matching trace IDs, preserve order
    id_set = set(trace_ids)
    records = []
    seen = set()
    for record in traces:
        if not isinstance(record, dict):
            continue
        tid = str(record.get("id", ""))
        if tid not in id_set or tid in seen:
            continue
        seen.add(tid)
        question, answer = parse_conversation(record)
        buyer = record.get("buyerAccount", "")
        seller = record.get("sellerAccount", "")
        topic = record.get("topicName", "")
        product_info = record.get("productInfo", {}) if isinstance(record.get("productInfo"), dict) else {}
        product_title = product_info.get("spuTitle", "") or product_info.get("title", "") or ""
        sku_id = product_info.get("skuId", "") or ""
        spu_id = product_info.get("spuId", "") or ""
        ts = record.get("time", 0)
        try:
            dt_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            dt_str = str(ts)
        identity = extract_trace_identity(record)

        records.append({
            "id": tid,
            "time": dt_str,
            "buyer": buyer[:60],
            "buyerAlias": buyer[:30],
            "seller": seller[:60],
            "topic": topic[:50],
            "product": product_title[:80],
            "spu": spu_id,
            "sku": sku_id,
            "question": question[:500],
            "answer": answer[:1000],
            "status": record.get("status", ""),
            "type": record.get("type", ""),
            "identity": identity,
            "rawTime": ts,
        })

    # Sort by time descending
    records.sort(key=lambda r: r.get("rawTime", 0), reverse=True)
    for r in records:
        r.pop("rawTime", None)

    return jsonify({"success": True, "records": records})


@app.route("/api/issue-status", methods=["POST"])
@require_auth
def update_issue_status():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    issue_id = str(data.get("issueId", "")).strip()
    status = str(data.get("status", "")).strip()
    allowed = {"待处理", "已确认问题", "需要补知识", "需要改话术", "加转人工规则", "已补知识", "已优化话术", "复查通过", "忽略"}
    if not shop_id or not issue_id or status not in allowed:
        return jsonify({"success": False, "error": "参数无效"}), 400
    status_map = load_json(ISSUE_STATUS_FILE, {})
    status_map[issue_status_key(shop_id, issue_id)] = status
    save_json(ISSUE_STATUS_FILE, status_map)

    analysis = load_json(analysis_file(shop_id), {})
    for issue in analysis.get("issueWorkbench", []):
        if issue.get("id") == issue_id:
            issue["status"] = status
    if analysis:
        save_json(analysis_file(shop_id), analysis)
    return jsonify({"success": True, "status": status})


@app.route("/api/overview")
@require_auth
def overview():
    shop_id = request.args.get("shopId", "").strip()
    traces = load_json(data_file(shop_id), []) if shop_id else []
    analysis = load_json(analysis_file(shop_id), {}) if shop_id else {}
    total_qa = sum(len(topic.get("examples", [])) for topic in analysis.get("qaExamples", []))
    return jsonify({
        "totalTraces": len(traces),
        "totalTopics": len(analysis.get("topicDistribution", [])),
        "totalQaExamples": total_qa,
        "totalIssues": len(analysis.get("issueWorkbench", [])),
        "totalRules": total_qa,
    })


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# AI Analysis module
# ============================================================

def load_ai_config():
    cfg = load_json(AI_CONFIG_FILE, {})
    # Merge with defaults to ensure all provider keys exist
    for key, val in DEFAULT_AI_CONFIG.items():
        if key not in cfg:
            cfg[key] = val
    return cfg


def save_ai_config(config):
    save_json(AI_CONFIG_FILE, config)


def _mask_api_key(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


def _resolve_provider(cfg):
    """Resolve the active provider config from the full config dict."""
    active = cfg.get("activeProvider", "agnes")
    providers = cfg.get("providers", {})
    provider = providers.get(active, providers.get("agnes", {}))
    return provider


def _call_llm(system_prompt, user_text, cfg):
    """Call the configured LLM provider via OpenAI-compatible API."""
    provider = _resolve_provider(cfg)
    api_key = provider.get("apiKey", "").strip()
    base_url = provider.get("baseUrl", "").strip()
    model = provider.get("model", "").strip()
    temperature = float(cfg.get("temperature", 0.3))
    max_tokens = int(cfg.get("maxTokens", 4096))

    if not api_key or not base_url or not model:
        return None, "未配置 API Key 或 Base URL"

    # Determine the endpoint
    if "anthropic" in base_url or "anthropic" in api_key:
        # Anthropic format
        url = base_url.rstrip("/") + "/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_text}],
            "temperature": temperature,
        }
    else:
        # OpenAI-compatible (default)
        url = base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        if "anthropic" in base_url or "anthropic" in api_key:
            # Anthropic response format
            content = data.get("content", [])
            if isinstance(content, list) and content:
                text = content[0].get("text", "")
            else:
                text = str(data)
        else:
            # OpenAI response format
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
            else:
                text = str(data)

        return text, None
    except requests.exceptions.RequestException as e:
        return None, str(e)
    except (ValueError, KeyError) as e:
        return None, f"解析响应失败: {e}"


@app.route("/api/ai/config")
@require_auth
def ai_config_get():
    cfg = load_ai_config()
    # Mask API keys
    providers_out = {}
    for name, prov in cfg.get("providers", {}).items():
        providers_out[name] = {
            "enabled": prov.get("enabled", False),
            "apiKey": _mask_api_key(prov.get("apiKey", "")),
            "baseUrl": prov.get("baseUrl", ""),
            "model": prov.get("model", ""),
        }
    return jsonify({
        "success": True,
        "config": {
            "providers": providers_out,
            "activeProvider": cfg.get("activeProvider", "agnes"),
            "temperature": cfg.get("temperature", 0.3),
            "maxTokens": cfg.get("maxTokens", 4096),
        },
    })


@app.route("/api/ai/config", methods=["POST"])
@require_auth
def ai_config_save():
    data = request.get_json() or {}
    cfg = load_ai_config()

    # Update active provider
    if "activeProvider" in data:
        cfg["activeProvider"] = str(data["activeProvider"]).strip()

    # Update temperature / max tokens
    if "temperature" in data:
        try:
            cfg["temperature"] = float(data["temperature"])
        except (ValueError, TypeError):
            pass
    if "maxTokens" in data:
        try:
            cfg["maxTokens"] = int(data["maxTokens"])
        except (ValueError, TypeError):
            pass

    # Update provider configs
    prov = _resolve_provider(cfg)
    for key in ("apiKey", "baseUrl", "model"):
        if key in data:
            val = str(data[key]).strip()
            if val:
                prov[key] = val
            elif key == "apiKey":
                # Empty API key means user didn't change it — keep existing
                pass
            else:
                # Empty baseUrl or model — still update (user wants to clear)
                prov[key] = val

    # Update provider enable/disable
    if "providerEnabled" in data:
        enabled = data["providerEnabled"]
        if isinstance(enabled, dict):
            for pname, state in enabled.items():
                if pname in cfg.get("providers", {}):
                    cfg["providers"][pname]["enabled"] = bool(state)

    save_ai_config(cfg)
    return jsonify({"success": True})


@app.route("/api/ai/test", methods=["POST"])
@require_auth
def ai_test():
    cfg = load_ai_config()
    text, err = _call_llm(
        "Reply in one word: OK",
        "Say hello.",
        cfg,
    )
    if err:
        return jsonify({"success": False, "error": err})
    return jsonify({"success": True, "response": text[:200]})


@app.route("/api/ai/analyze", methods=["POST"])
@require_auth
def ai_analyze():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})

    traces = load_json(data_file(shop_id), [])
    if not traces:
        return jsonify({"success": False, "error": "没有可分析的数据，请先抓取数据"})

    cfg = load_ai_config()
    provider = _resolve_provider(cfg)
    if not provider.get("apiKey") or not provider.get("baseUrl") or not provider.get("model"):
        return jsonify({"success": False, "error": "请先在 API 配置中设置模型参数"})

    # Sample traces (max 500 to avoid token limits)
    sampled = traces[:500]

    # Build system prompt
    system_prompt = """你是一个电商智能体质检专家。你的任务是分析智能体与买家的对话记录，找出问题并给出优化建议。

请按以下 JSON 格式返回结果（不要返回其他内容）：
{
  "summary": "整体分析总结（100字以内）",
  "recommendations": ["建议1", "建议2", "建议3"],
  "classifications": [
    {
      "issueType": "未回复|弱回复|高风险|正常",
      "priority": "高|中|低",
      "topic": "问题分类",
      "standardQuestion": "标准问法",
      "rootCause": "根本原因",
      "suggestedAction": "建议动作",
      "count": 出现次数
    }
  ],
  "knowledgeCards": [
    {
      "title": "卡片标题",
      "standardQuestion": "标准问法",
      "similarQuestions": ["相似问法1", "相似问法2"],
      "triggerWords": ["触发词1", "触发词2"],
      "standardAnswer": "标准答案",
      "manualHandoffRule": "转人工规则"
    }
  ]
}

注意事项：
1. classifications 最多返回 10 条，按优先级排序
2. knowledgeCards 最多返回 6 张，只针对有问题（非正常）的对话生成
3. 所有字段都是中文
4. standardAnswer 要具体可执行，包含操作步骤和时效承诺
5. manualHandoffRule 要明确什么情况下必须转人工"""

    # Build user prompt with trace samples
    trace_samples = []
    for i, record in enumerate(sampled[:50]):  # Use first 50 for context
        if not isinstance(record, dict):
            continue
        question = record.get("question", "") or record.get("searchContent", "") or ""
        answer = record.get("content", "") or ""
        if isinstance(answer, list):
            answer = "\n".join(str(c) for c in answer if isinstance(c, str))
        topic = record.get("topicName", "") or ""
        buyer = record.get("buyerAccount", "") or ""
        product = ""
        pi = record.get("productInfo", {})
        if isinstance(pi, dict):
            product = pi.get("spuTitle", "") or pi.get("title", "") or ""
        trace_samples.append({
            "index": i + 1,
            "question": str(question)[:200],
            "answer": str(answer)[:300],
            "topic": str(topic)[:50],
            "buyer": str(buyer)[:30],
            "product": str(product)[:50],
        })

    user_text = f"""请分析以下 {len(trace_samples)} 条智能体对话记录：

{json.dumps(trace_samples, ensure_ascii=False, indent=2)}

请按照要求返回结构化分析结果。"""

    llm_text, err = _call_llm(system_prompt, user_text, cfg)
    if err:
        return jsonify({"success": False, "error": f"LLM 调用失败: {err}"})

    # Parse JSON from LLM response
    try:
        # Try to extract JSON from the response (handle markdown code blocks)
        cleaned = llm_text.strip()
        if cleaned.startswith("```"):
            # Remove markdown code fences
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:])
            if cleaned.startswith("```"):
                cleaned = cleaned[1:]
            lines = cleaned.split("\n")
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return jsonify({"success": False, "error": f"AI 返回格式解析失败: {e}\n原始内容: {llm_text[:500]}"})

    return jsonify({"success": True, "data": result})


if __name__ == "__main__":
    host = os.environ.get("QA_HOST", "127.0.0.1")
    port = int(os.environ.get("QA_PORT", "5000"))
    print("=" * 50)
    print("  QA Agent Trace Analyzer")
    print(f"  http://{host}:{port}")
    print("=" * 50)
    app.run(host=host, port=port, debug=False)
