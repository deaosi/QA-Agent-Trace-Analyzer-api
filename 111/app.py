"""QA Agent Trace Analyzer."""

import csv
import hashlib
import ipaddress
import io
import json
import os
import re
import secrets
import time
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse
from functools import wraps

import requests
from flask import Flask, has_request_context, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from storage import load_json as storage_load_json, save_json as storage_save_json
from shared_store import SharedStore
from conversation_sessions import build_conversation_sessions as group_conversation_sessions
from local_analysis_v2 import (
    ANALYSIS_VERSION as LOCAL_ANALYSIS_VERSION,
    calculate_health_score,
    derive_priority,
    evaluate_trace_quality,
    primary_rule_family,
    question_similarity,
    question_tokens,
)

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
DB_FILE = os.path.join(DATA_DIR, "qa_shared.sqlite3")
SHARED_STORE = SharedStore(DB_FILE)
SHARED_STORE.migrate_legacy(DATA_DIR, SHOPS_FILE, ISSUE_STATUS_FILE, storage_load_json)
SHARED_STORE.fail_incomplete_ai_tasks()

DEFAULT_AI_CONFIG = {
    "providerName": "",
    "providerNote": "",
    "providerWebsite": "",
    "baseUrl": "https://api.xxx.com/v1",
    "model": "",
    "apiKey": "",
    "fullUrl": False,
    "temperature": 0.3,
    "maxTokens": 4096,
    "timeoutSeconds": 300,
}

TRACE_API = "https://agent.tanyuai.com/api/im/agent-trace/paginateV2"
REFERER = "https://agent.tanyuai.com/v2/diagnostic-optimization/optimization-workshop"
DEFAULT_SECRET_KEY = "change-me-before-public-deploy"
SHOP_ID_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}")
AI_DEFAULT_ISSUE_BATCH_SIZE = 10
AI_MAX_ISSUE_BATCH_SIZE = 200
AI_CONNECT_TIMEOUT_SECONDS = 20
AI_DEFAULT_READ_TIMEOUT_SECONDS = 300
AI_MIN_READ_TIMEOUT_SECONDS = 60
AI_MAX_READ_TIMEOUT_SECONDS = 900
AI_PROMPT_VERSION = "deep-analysis-v2"

SHOP_SYNC_URL = "https://agent.tanyuai.com/v2/agent-builder/knowledge-base"
SHOP_SYNC_STATE = {
    "running": False,
    "startedAt": "",
    "finishedAt": "",
    "success": False,
    "message": "",
    "savedCookieCount": 0,
    "savedShopCount": 0,
    "shops": [],
}
SHOP_SYNC_LOCK = threading.Lock()
SHOP_SYNC_SID_KEYS = {"sid", "shopid", "shop_id", "thirdshopid", "third_shop_id", "thirdshopids"}
SHOP_SYNC_NAME_KEYS = {"name", "shopname", "shop_name", "title", "label"}
SHOP_SYNC_NOISE_KEYS = {"sessionid", "csrf", "token", "userid", "user_id", "traceid", "requestid"}



class InvalidShopId(ValueError):
    pass


def resolve_secret_key(environ=os.environ):
    return environ.get("QA_SECRET_KEY", DEFAULT_SECRET_KEY)


def validate_startup_security(environ=os.environ):
    """Fail closed for production entrypoints while keeping local imports usable."""
    mode = str(environ.get("QA_ENV", "")).strip().lower()
    required = mode in {"production", "prod"} or str(environ.get("QA_REQUIRE_SECURE_CONFIG", "")).strip() == "1"
    if not required:
        return
    secret = str(environ.get("QA_SECRET_KEY", "")).strip()
    password = str(environ.get("QA_ADMIN_PASSWORD", "")).strip()
    if not secret or secret == DEFAULT_SECRET_KEY:
        raise RuntimeError("QA_SECRET_KEY must be set to a unique value in production")
    if not password:
        raise RuntimeError("QA_ADMIN_PASSWORD must be set in production")


def warn_if_default_secret(secret):
    if secret == DEFAULT_SECRET_KEY:
        print("WARNING: QA_SECRET_KEY is using the development default. Set QA_SECRET_KEY before public deployment.")


app = Flask(__name__)
app.secret_key = resolve_secret_key()
app.permanent_session_lifetime = timedelta(days=30)


@app.errorhandler(InvalidShopId)
def handle_invalid_shop_id(error):
    return jsonify({"success": False, "error": str(error)}), 400


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
    return storage_load_json(path, default)


def save_json(path, data):
    storage_save_json(path, data)


def cookie_file_for_user(username=None):
    username = str(username or (session.get("username", "") if has_request_context() else "")).strip()
    if not username:
        username = "anonymous"
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:24]
    return os.path.join(DATA_DIR, f".cookies_user_{digest}.json")


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def load_users():
    users = load_json(USERS_FILE, {})
    changed = False
    for username, user in list(users.items()):
        if not isinstance(user, dict):
            continue
        if "remark" not in user:
            user["remark"] = ""
            changed = True
        if "systemLocked" not in user:
            user["systemLocked"] = username == ADMIN_USERNAME
            changed = True
        if username == ADMIN_USERNAME:
            if user.get("role") != "admin":
                user["role"] = "admin"
                changed = True
            if not user.get("active", True):
                user["active"] = True
                changed = True
            if not user.get("systemLocked"):
                user["systemLocked"] = True
                changed = True
            if not user.get("remark"):
                user["remark"] = "系统默认管理员"
                changed = True
    if not users and ADMIN_PASSWORD:
        users[ADMIN_USERNAME] = {
            "username": ADMIN_USERNAME,
            "passwordHash": generate_password_hash(ADMIN_PASSWORD),
            "role": "admin",
            "active": True,
            "expiresAt": "",
            "remark": "系统默认管理员",
            "systemLocked": True,
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "lastLoginAt": "",
        }
        changed = True
    if changed:
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
        "remark": user.get("remark", ""),
        "systemLocked": bool(user.get("systemLocked", False)),
        "hasPassword": bool(user.get("passwordHash")),
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


def shop_file_token(shop_id):
    token = str(shop_id or "").strip()
    if not SHOP_ID_PATTERN.fullmatch(token):
        raise InvalidShopId("Invalid shop ID. Use 1-128 letters, numbers, dot, dash, or underscore characters.")
    return token


def shop_data_path(prefix, shop_id):
    token = shop_file_token(shop_id)
    root = os.path.abspath(DATA_DIR)
    path = os.path.abspath(os.path.join(root, f"{prefix}_{token}.json"))
    if not path.startswith(root + os.sep):
        raise InvalidShopId("Invalid shop ID path.")
    return path


def data_file(shop_id):
    return shop_data_path("traces", shop_id)


def analysis_file(shop_id):
    return shop_data_path("analysis", shop_id)


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


def build_session(username=None):
    username = str(username or (session.get("username", "") if has_request_context() else "")).strip()
    saved = load_json(cookie_file_for_user(username))
    if not saved and username == ADMIN_USERNAME:
        saved = load_json(COOKIE_FILE)
    if not saved:
        return None

    http_session = requests.Session()
    for name, value in saved.items():
        http_session.cookies.set(name, value, domain=".tanyuai.com")

    try:
        http_session.get("https://agent.tanyuai.com", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    except requests.RequestException:
        pass
    return http_session


def normalize_shop_sid(value):
    sid = str(value or "").strip()
    if not sid or not SHOP_ID_PATTERN.fullmatch(sid):
        return ""
    lowered = sid.lower()
    if lowered in {"sid", "shopid", "null", "none", "undefined", "true", "false"}:
        return ""
    return sid


def find_nearby_shop_name(obj):
    if not isinstance(obj, dict):
        return ""
    for key, value in obj.items():
        compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
        if compact in SHOP_SYNC_NAME_KEYS and isinstance(value, (str, int, float)):
            name = str(value).strip()
            if name:
                return name[:120]
    return ""


def collect_shop_candidates(obj, found=None, depth=0):
    if found is None:
        found = {}
    if depth > 8:
        return found
    if isinstance(obj, dict):
        nearby_name = find_nearby_shop_name(obj)
        for key, value in obj.items():
            compact = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if compact in SHOP_SYNC_NOISE_KEYS:
                continue
            values = value if isinstance(value, list) else [value]
            if compact in SHOP_SYNC_SID_KEYS or compact.endswith("sid") or compact.endswith("shopid"):
                for item in values:
                    sid = normalize_shop_sid(item)
                    if sid:
                        current = found.get(sid, {}) if isinstance(found.get(sid), dict) else {}
                        current.setdefault("name", nearby_name or sid)
                        found[sid] = current
            collect_shop_candidates(value, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:5000]:
            collect_shop_candidates(item, found, depth + 1)
    elif isinstance(obj, str) and depth <= 4:
        raw = obj.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                collect_shop_candidates(json.loads(raw), found, depth + 1)
            except ValueError:
                pass
    return found


def collect_shop_candidates_from_text(raw, found=None):
    if found is None:
        found = {}
    text_value = str(raw or "")
    patterns = [
        r'''(?i)["\'](?:thirdShopId|third_shop_id|shopId|shop_id|sid)["\']\s*[:=]\s*["\']([A-Za-z0-9_.-]{1,128})["\']''',
        r"(?i)(?:thirdShopId|third_shop_id|shopId|shop_id|sid)=([A-Za-z0-9_.-]{1,128})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text_value):
            sid = normalize_shop_sid(match.group(1))
            if sid:
                found.setdefault(sid, {"name": sid})
    return found


def normalize_shop_name(value):
    """Use the visible shop name as the auto-sync duplicate grouping key."""
    return re.sub(r"[\s\u3000]+", "", str(value or "")).casefold()


def save_synced_shops(candidates):
    existing = SHARED_STORE.list_shops()
    by_sid = {}

    # Include existing records in the grouping. This prevents a new sync from
    # adding another SID when the shared store already has the same shop name.
    for raw_sid, info in existing.items():
        sid = normalize_shop_sid(raw_sid)
        if not sid:
            continue
        current = info if isinstance(info, dict) else {}
        display_name = str(current.get("name") or sid).strip() or sid
        by_sid[sid] = {
            "name": display_name,
            "total": current.get("total", 0),
            "traceCount": current.get("traceCount", 0),
            "source": current.get("source", ""),
            "existing": True,
        }

    for raw_sid, info in sorted(candidates.items(), key=lambda item: str(item[0])):
        sid = normalize_shop_sid(raw_sid)
        if not sid:
            continue
        name = info.get("name") if isinstance(info, dict) else info
        candidate_name = str(name or "").strip()
        current = by_sid.get(sid, {})
        current_name = str(current.get("name") or sid).strip() or sid
        # A candidate's human-readable name is more useful than a SID placeholder.
        if candidate_name and (not current_name or current_name == sid):
            current_name = candidate_name
        by_sid[sid] = {
            "name": current_name,
            "total": current.get("total", 0),
            "traceCount": current.get("traceCount", 0),
            "source": current.get("source", ""),
            "existing": bool(current.get("existing")),
        }

    grouped = defaultdict(list)
    for sid, info in by_sid.items():
        grouped[normalize_shop_name(info["name"]) or sid].append((sid, info))

    saved = []
    for group in grouped.values():
        def rank(item):
            sid, info = item
            trace_count = int(info.get("traceCount", 0) or 0)
            total = int(info.get("total", 0) or 0)
            # Preserve an existing SID on ties, then use a stable SID order.
            return (-trace_count, -total, 0 if info.get("existing") else 1, sid)

        sid, info = sorted(group, key=rank)[0]
        source = info.get("source") or "auto-sync"
        SHARED_STORE.upsert_shop(sid, info["name"], info.get("total", 0), source)
        saved.append({"id": sid, "name": info["name"]})
    return saved


def cookie_header_from_playwright(cookies):
    pairs = {}
    for cookie in cookies:
        domain = str(cookie.get("domain", ""))
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", ""))
        if name and "tanyuai.com" in domain:
            pairs[name] = value
    return pairs


def set_shop_sync_state(**kwargs):
    with SHOP_SYNC_LOCK:
        SHOP_SYNC_STATE.update(kwargs)
        return dict(SHOP_SYNC_STATE)


def run_shop_sync(username="", timeout_seconds=180):
    set_shop_sync_state(
        running=True,
        startedAt=datetime.now().isoformat(),
        finishedAt="",
        success=False,
        message="正在打开浏览器，请在弹出的页面完成登录。",
        savedCookieCount=0,
        savedShopCount=0,
        shops=[],
    )
    candidates = {}
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError:
        set_shop_sync_state(
            running=False,
            finishedAt=datetime.now().isoformat(),
            success=False,
            message="缺少 Playwright 依赖，请先安装 requirements.txt 并执行 playwright install chromium。",
        )
        return

    try:
        with sync_playwright() as pw:
            profile_digest = hashlib.sha256(str(username or "anonymous").encode("utf-8")).hexdigest()[:24]
            user_data_dir = os.path.join(DATA_DIR, f"playwright-agent-profile-{profile_digest}")
            launch_options = {
                "headless": False,
                "viewport": {"width": 1280, "height": 860},
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            try:
                context = pw.chromium.launch_persistent_context(user_data_dir, **launch_options)
            except Exception as first_exc:
                try:
                    context = pw.chromium.launch_persistent_context(user_data_dir, channel="msedge", **launch_options)
                except Exception as edge_exc:
                    raise RuntimeError(f"无法启动 Playwright Chromium 或 Edge：{edge_exc}") from first_exc
            page = context.pages[0] if context.pages else context.new_page()

            def handle_response(resp):
                parsed = urlparse(resp.url)
                if not parsed.netloc.endswith("agent.tanyuai.com"):
                    return
                try:
                    ctype = (resp.headers or {}).get("content-type", "")
                    if "json" in ctype:
                        collect_shop_candidates(resp.json(), candidates)
                    else:
                        body = resp.text()
                        if body and ("sid" in body.lower() or "shop" in body.lower()):
                            try:
                                collect_shop_candidates(json.loads(body), candidates)
                            except ValueError:
                                collect_shop_candidates_from_text(body, candidates)
                except Exception:
                    return

            page.on("response", handle_response)
            page.goto(SHOP_SYNC_URL, wait_until="domcontentloaded", timeout=60000)
            deadline = time.time() + max(int(timeout_seconds), 30)
            last_count = -1
            stable_since = time.time()
            while time.time() < deadline:
                try:
                    if "agent.tanyuai.com" not in page.url:
                        page.goto(SHOP_SYNC_URL, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(2000)
                    collect_shop_candidates_from_text(page.content(), candidates)
                    storage_items = page.evaluate("""
                    () => {
                      const items = [];
                      for (const store of [localStorage, sessionStorage]) {
                        for (let i = 0; i < store.length; i++) {
                          const key = store.key(i);
                          items.push({key, value: store.getItem(key)});
                        }
                      }
                      return items;
                    }
                    """)
                    collect_shop_candidates(storage_items, candidates)
                    storage_text = json.dumps(storage_items, ensure_ascii=False)
                    collect_shop_candidates_from_text(storage_text, candidates)
                except PlaywrightTimeoutError:
                    pass
                except Exception:
                    pass

                count = len(candidates)
                if count != last_count:
                    set_shop_sync_state(message=f"已发现 {count} 个候选店铺，继续等待页面接口完成。", savedShopCount=count)
                    last_count = count
                    stable_since = time.time()
                elif count > 0 and time.time() - stable_since >= 8:
                    break

            cookies = cookie_header_from_playwright(context.cookies("https://agent.tanyuai.com"))
            if cookies:
                save_json(cookie_file_for_user(username), cookies)
            saved_shops = save_synced_shops(candidates)
            context.close()
            if not cookies:
                message = "未读取到 agent.tanyuai.com Cookie，请确认弹出的浏览器已登录。"
                success = False
            elif not saved_shops:
                message = "Cookie 已保存，但没有发现店铺 SID。请在弹出的页面确认已进入知识库店铺列表。"
                success = False
            else:
                message = f"同步完成：保存 Cookie {len(cookies)} 项，店铺 {len(saved_shops)} 个。"
                success = True
            set_shop_sync_state(
                running=False,
                finishedAt=datetime.now().isoformat(),
                success=success,
                message=message,
                savedCookieCount=len(cookies),
                savedShopCount=len(saved_shops),
                shops=saved_shops[:200],
            )
    except Exception as exc:
        set_shop_sync_state(
            running=False,
            finishedAt=datetime.now().isoformat(),
            success=False,
            message=f"自动同步失败：{exc}",
        )


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


def parse_filter_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("T", " "))
    return parsed.timestamp() * 1000





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
                "remark": "",
                "systemLocked": False,
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
        "role": "admin" if username == ADMIN_USERNAME else role,
        "active": True,
        "expiresAt": "",
        "remark": str(data.get("remark", "")).strip(),
        "systemLocked": username == ADMIN_USERNAME,
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
    locked = bool(user.get("systemLocked")) or username == ADMIN_USERNAME
    is_self = username == session.get("username")
    if "password" in data:
        if locked and not is_self:
            return jsonify({"success": False, "error": "系统锁定账号不可修改密码"}), 400
        password = str(data.get("password", ""))
        if len(password) < 6:
            return jsonify({"success": False, "error": "密码至少 6 位"}), 400
        user["passwordHash"] = generate_password_hash(password)
    if "remark" in data:
        if locked and not is_self:
            return jsonify({"success": False, "error": "系统锁定账号不可修改备注"}), 400
        user["remark"] = str(data.get("remark", "")).strip()
    if "active" in data:
        if locked:
            return jsonify({"success": False, "error": "系统锁定账号不可禁用"}), 400
        if is_self and data.get("active") is False:
            return jsonify({"success": False, "error": "不能禁用当前登录账号"}), 400
        user["active"] = bool(data.get("active"))
    if "role" in data:
        if locked:
            return jsonify({"success": False, "error": "系统锁定账号不可修改角色"}), 400
        role = str(data.get("role", "")).strip()
        if role not in ("user", "admin"):
            return jsonify({"success": False, "error": "角色不正确"}), 400
        user["role"] = role
    if "expiresAt" in data:
        if locked:
            return jsonify({"success": False, "error": "系统锁定账号不可修改到期时间"}), 400
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
        return jsonify({"success": False, "error": "不能删除当前登录账号"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"success": False, "error": "账号不存在"}), 404
    if username == ADMIN_USERNAME or users[username].get("systemLocked"):
        return jsonify({"success": False, "error": "系统锁定账号不可删除"}), 400
    admin_count = sum(1 for user in users.values() if user.get("role") == "admin" and user.get("active", True))
    if users[username].get("role") == "admin" and admin_count <= 1:
        return jsonify({"success": False, "error": "至少保留一个可用管理员"}), 400
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
    return jsonify({"hasCookie": bool(load_json(cookie_file_for_user()))})


@app.route("/api/save-cookie", methods=["POST"])
@require_auth
def save_cookie():
    pairs = parse_cookie_string((request.get_json() or {}).get("cookie", ""))
    if not pairs:
        return jsonify({"success": False, "error": "Cookie 为空或格式无效"}), 400
    save_json(cookie_file_for_user(), pairs)
    return jsonify({"success": True})



@app.route("/api/auto-sync-shops", methods=["POST"])
@require_auth
def auto_sync_shops():
    with SHOP_SYNC_LOCK:
        if SHOP_SYNC_STATE.get("running"):
            return jsonify({"success": False, "error": "自动同步正在运行", "state": dict(SHOP_SYNC_STATE)}), 409
    try:
        timeout_seconds = max(30, min(int((request.get_json() or {}).get("timeoutSeconds") or 180), 900))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "同步超时时间无效"}), 400
    username = session.get("username", "")
    thread = threading.Thread(target=run_shop_sync, args=(username, timeout_seconds), daemon=True)
    thread.start()
    return jsonify({"success": True, "state": dict(SHOP_SYNC_STATE)})


@app.route("/api/auto-sync-shops/status")
@require_auth
def auto_sync_shops_status():
    with SHOP_SYNC_LOCK:
        return jsonify({"success": True, "state": dict(SHOP_SYNC_STATE)})


@app.route("/api/shops")
@require_auth
def list_shops():
    shops = SHARED_STORE.list_shops()
    return jsonify({"shops": [{"id": k, "name": v.get("name", k), "total": v.get("total", 0), "traceCount": v.get("traceCount", 0), "source": v.get("source", "")} for k, v in shops.items()], "current": None})


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
            save_json(cookie_file_for_user(), pairs)

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

    SHARED_STORE.upsert_shop(shop_id, shop_name or shop_id, total, "probe")
    return jsonify({"success": True, "shopName": shop_name or shop_id, "total": total})


@app.route("/api/delete-shop", methods=["POST"])
@require_auth
def delete_shop():
    shop_id = str((request.get_json() or {}).get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
    shop_file_token(shop_id)
    SHARED_STORE.delete_shop(shop_id)
    return jsonify({"success": True})


@app.route("/api/delete-shops", methods=["POST"])
@require_auth
def delete_shops():
    data = request.get_json() or {}
    raw_ids = data.get("shopIds", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "error": "店铺 ID 列表无效"}), 400

    shop_ids = []
    seen = set()
    for raw_id in raw_ids:
        shop_id = str(raw_id or "").strip()
        if not shop_id:
            continue
        shop_file_token(shop_id)
        key = shop_id.lower()
        if key in seen:
            continue
        seen.add(key)
        shop_ids.append(shop_id)
    if not shop_ids:
        return jsonify({"success": False, "error": "请先勾选要删除的店铺"}), 400

    stored_shops = SHARED_STORE.list_shops()
    deleted_ids = []
    for shop_id in shop_ids:
        matching_ids = [stored_id for stored_id in stored_shops if stored_id.lower() == shop_id.lower()]
        for stored_id in matching_ids or [shop_id]:
            SHARED_STORE.delete_shop(stored_id)
            deleted_ids.append(stored_id)
    return jsonify({"success": True, "deleted": deleted_ids, "count": len(deleted_ids)})


@app.route("/api/fetch", methods=["POST"])
@require_auth
def fetch_data():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请指定店铺 ID"}]})
    shop_file_token(shop_id)

    username = session.get("username", "")
    http_session = build_session()
    if not http_session:
        return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "请先配置 Cookie"}]})

    begin_time = normalize_datetime(data.get("beginTime"), "2024-01-01 00:00:00")
    end_time = normalize_datetime(data.get("endTime"), datetime.now().strftime("%Y-%m-%d 23:59:59"))
    try:
        page_size = max(1, min(int(data.get("pageSize") or 100), 500))
        max_pages = max(1, min(int(data.get("maxPages") or 10), 200))
    except (TypeError, ValueError):
        return jsonify({"success": False, "log": [{"page": 1, "status": "error", "msg": "分页参数无效"}]}), 400
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
            resp = http_session.post(TRACE_API, json=body, headers=request_headers(), timeout=30)
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

    total_fetched = 0
    if all_records:
        merge_result = SHARED_STORE.merge_traces(
            shop_id,
            all_records,
            fetched_by=username,
            overwrite=bool(data.get("overwrite", False)),
        )
        total_fetched = merge_result["inserted"]

    if shop_name:
        SHARED_STORE.upsert_shop(shop_id, shop_name, SHARED_STORE.count_traces(shop_id), "fetch")

    return jsonify({
        "success": True,
        "totalFetched": total_fetched,
        "totalStored": SHARED_STORE.count_traces(shop_id),
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
    evaluation = evaluate_trace_quality(
        question,
        answer,
        {"searchContent": record.get("searchContent", ""), "record": record},
    )
    return evaluation["issueType"], evaluation["reasons"]


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


def build_issue_workbench(traces, shop_id="", conversation_index=None):
    groups = []
    conversation_index = conversation_index if isinstance(conversation_index, dict) else {}
    sorted_traces = sorted(
        (record for record in traces if isinstance(record, dict)),
        key=lambda record: str(record.get("id") or record.get("traceId") or ""),
    )
    for record in sorted_traces:
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        if not question:
            continue
        evaluation = evaluate_trace_quality(
            question,
            answer,
            {"searchContent": record.get("searchContent", ""), "record": record},
        )
        issue_type = evaluation["issueType"]
        reasons = evaluation["reasons"]
        if issue_type == "正常":
            continue
        topic = infer_issue_topic(question, record)
        tokens = question_tokens(question)
        family = primary_rule_family(evaluation)
        group = next(
            (
                candidate for candidate in groups
                if candidate["topic"] == topic
                and candidate["ruleFamily"] == family
                and question_similarity(tokens, candidate["questionTokens"]) >= 0.58
            ),
            None,
        )
        if group is None:
            key = f"{topic}:{normalize_question_text(question)}"
            group = {
                "id": re.sub(r"\W+", "_", key, flags=re.UNICODE)[:80],
                "topic": topic,
                "standardQuestion": question[:160],
                "issueType": issue_type,
                "analysisVersion": LOCAL_ANALYSIS_VERSION,
                "ruleFamily": family,
                "questionTokens": set(tokens),
                "count": 0,
                "unresolvedCount": 0,
                "negativeCount": 0,
                "adoptionRiskTotal": 0,
                "confidenceTotal": 0,
                "confidenceMax": 0,
                "dimensionTotals": Counter(),
                "ruleHitCounts": Counter(),
                "ruleHitsById": {},
                "typeCounts": Counter(),
                "reasons": Counter(),
                "questionVariants": Counter(),
                "examples": [],
            }
            groups.append(group)
        else:
            group["questionTokens"].update(tokens)
        group["count"] += 1
        group["typeCounts"][issue_type] += 1
        group["confidenceTotal"] += int(evaluation.get("confidence", 0) or 0)
        group["confidenceMax"] = max(group["confidenceMax"], int(evaluation.get("confidence", 0) or 0))
        for dimension, value in evaluation.get("qualityDimensions", {}).items():
            group["dimensionTotals"][dimension] += int(value or 0)
        for hit in evaluation.get("ruleHits", []):
            rule_id = str(hit.get("ruleId", "") or "")
            if not rule_id:
                continue
            group["ruleHitCounts"][rule_id] += 1
            stored_hit = group["ruleHitsById"].setdefault(rule_id, {
                key: value for key, value in hit.items() if key != "deductions"
            })
            stored_hit["count"] = group["ruleHitCounts"][rule_id]
            evidence_samples = stored_hit.setdefault("evidenceSamples", [])
            evidence = str(hit.get("evidence", "") or "").strip()
            if evidence and evidence not in evidence_samples and len(evidence_samples) < 3:
                evidence_samples.append(evidence)
        group["questionVariants"][question[:80]] += 1
        if issue_type in ("未回复", "弱回复"):
            group["unresolvedCount"] += 1
        if issue_type == "高风险":
            group["negativeCount"] += 1
        group["adoptionRiskTotal"] += estimate_adoption_risk(issue_type, reasons, answer, record)
        for reason in reasons:
            group["reasons"][reason] += 1
        if len(group["examples"]) < 20:
            identity = extract_trace_identity(record)
            trace_id = str(record.get("id") or record.get("traceId") or "")
            group["examples"].append({
                "id": trace_id,
                "question": question[:240],
                "answer": answer[:600],
                "seller": record.get("sellerAccount", ""),
                "type": record.get("type", ""),
                "topicName": record.get("topicName", ""),
                "identity": compact_identity(identity),
                "conversation": conversation_index.get(trace_id, {}),
            })
        severity_order = {"未回复": 3, "高风险": 2, "弱回复": 1, "正常": 0}
        if severity_order.get(issue_type, 0) > severity_order.get(group["issueType"], 0):
            group["issueType"] = issue_type

    status_map = SHARED_STORE.load_issue_status()
    feedback_map = SHARED_STORE.load_issue_feedback(shop_id) if shop_id else {}
    issues = []
    for issue in groups:
        confidence = round(issue["confidenceTotal"] / max(issue["count"], 1))
        quality_dimensions = {
            dimension: round(total / max(issue["count"], 1))
            for dimension, total in issue["dimensionTotals"].items()
        }
        rule_hits = sorted(
            issue["ruleHitsById"].values(),
            key=lambda hit: (hit.get("severity") == "高", hit.get("weight", 0), hit.get("count", 0)),
            reverse=True,
        )
        priority, score = derive_priority(
            issue["issueType"], issue["count"], issue["unresolvedCount"], issue["negativeCount"],
            confidence, rule_hits,
        )
        examples = sorted(
            issue["examples"],
            key=lambda example: (
                bool((example.get("conversation") or {}).get("isMultiTurn")),
                int((example.get("conversation") or {}).get("endTime", 0) or 0),
                str(example.get("id", "")),
            ),
            reverse=True,
        )[:5]
        sample = examples[0] if examples else {}
        action, suggestion = build_training_suggestion(
            issue["issueType"], issue["topic"], sample.get("question", ""), sample.get("answer", "")
        )
        trigger_words = extract_trigger_words(issue["standardQuestion"], issue["topic"])
        answer_outline = build_answer_outline(issue["topic"], issue["issueType"], issue["standardQuestion"])
        failure_reason = infer_failure_reason(
            issue["issueType"], [reason for reason, _ in issue["reasons"].most_common(3)], sample.get("answer", ""), sample
        )
        dimension_risk = round(
            (100 - quality_dimensions.get("safety", 100)) * 0.45
            + (100 - quality_dimensions.get("resolution", 100)) * 0.35
            + (100 - quality_dimensions.get("actionability", 100)) * 0.20
        )
        adoption_risk = max(70 if issue["issueType"] == "高风险" else 0, dimension_risk)
        optimization_value = optimization_value_score(priority, issue["count"], issue["unresolvedCount"], adoption_risk)
        issue_id = issue["id"]
        state = status_map.get(issue_status_key(shop_id, issue_id), "待处理")
        issues.append({
            "id": issue_id,
            "topic": issue["topic"],
            "standardQuestion": issue["standardQuestion"],
            "issueType": issue["issueType"],
            "analysisVersion": issue["analysisVersion"],
            "confidence": confidence,
            "confidenceLevel": "高" if confidence >= 85 else ("中" if confidence >= 70 else "低"),
            "qualityDimensions": quality_dimensions,
            "ruleHits": rule_hits,
            "primaryRuleId": rule_hits[0].get("ruleId", "") if rule_hits else "",
            "ruleHitCounts": dict(issue["ruleHitCounts"]),
            "typeCounts": dict(issue["typeCounts"]),
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
            "feedback": feedback_map.get(issue_id),
            "examples": examples,
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
    health_metrics = calculate_health_score(total, issue_workbench)
    health = health_metrics["healthScore"]

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
        "analysisVersion": LOCAL_ANALYSIS_VERSION,
        "sampleAdequate": health_metrics["sampleAdequate"],
        "sampleLabel": health_metrics["sampleLabel"],
        "qualityRates": health_metrics["qualityRates"],
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

    conversation_sessions, conversation_index = build_conversation_workspace(traces, shop_id)
    issue_workbench = build_issue_workbench(traces, shop_id, conversation_index)
    manual_queue = build_manual_queue(issue_workbench)
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
        "analysisVersion": LOCAL_ANALYSIS_VERSION,
        "totalRecords": total,
        "withContent": with_content,
        "analyzedAt": datetime.now().isoformat(),
        "topicDistribution": topic_dist,
        "topicInsights": topic_insights,
        "qaExamples": qa_examples,
        "issueWorkbench": issue_workbench,
        "conversationSummary": conversation_summary(conversation_sessions),
        "manualQueue": manual_queue,
        "storeDiagnosis": store_diagnosis,
        "identityInsights": identity_insights,
        "topKeywords": top_keywords,
    }


@app.route("/api/analyze", methods=["POST"])
@require_auth
def analyze():
    shop_id = str((request.get_json() or {}).get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
    shop_file_token(shop_id)
    traces = SHARED_STORE.load_traces(shop_id)
    result = run_analysis(traces, shop_id)
    if shop_id:
        SHARED_STORE.save_analysis(shop_id, result)
    return jsonify({"success": True, "data": result})


@app.route("/api/analysis")
@require_auth
def get_analysis():
    shop_id = request.args.get("shopId", "").strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
    shop_file_token(shop_id)
    analysis = SHARED_STORE.load_analysis(shop_id)
    if analysis and ("storeDiagnosis" not in analysis or "topicInsights" not in analysis):
        traces = SHARED_STORE.load_traces(shop_id)
        if traces:
            analysis = run_analysis(traces, shop_id)
            SHARED_STORE.save_analysis(shop_id, analysis)
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
    shop_file_token(shop_id)
    traces = SHARED_STORE.load_traces(shop_id)
    if not traces:
        return jsonify({"success": False, "error": "没有可导出的数据"})

    # Optional time filters
    try:
        begin_ts = parse_filter_timestamp(begin_time)
        end_ts = parse_filter_timestamp(end_time)
    except (TypeError, ValueError, OverflowError):
        return jsonify({"success": False, "error": "时间筛选格式无效"}), 400
    if begin_ts is not None:
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) >= begin_ts]
    if end_ts is not None:
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) <= end_ts]

    rows = []
    for record in traces:
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        identity = extract_trace_identity(record)
        buyer = identity.get("buyerId", "") or record.get("buyerAccount", "")
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
        page_raw = request.args.get("page", 1)
        page_size_raw = request.args.get("pageSize", 50)
        conversation_mode = request.args.get("conversationMode", "")
        include_single_turns = request.args.get("includeSingleTurns", "")
        search_term = request.args.get("search", "")
    else:
        data = request.get_json() or {}
        shop_id = str(data.get("shopId", "")).strip()
        begin_time = data.get("beginTime", "")
        end_time = data.get("endTime", "")
        page_raw = data.get("page", 1)
        page_size_raw = data.get("pageSize", 50)
        conversation_mode = data.get("conversationMode", "")
        include_single_turns = data.get("includeSingleTurns", "")
        search_term = data.get("search", "")

    try:
        page = max(1, min(int(page_raw), 1000000))
        page_size = max(1, min(int(page_size_raw), 200))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "分页参数无效"}), 400

    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})
    shop_file_token(shop_id)
    traces = SHARED_STORE.load_traces(shop_id)
    if not traces:
        return jsonify({"success": False, "error": "没有可展示的聊天记录"})

    # Apply time filters
    try:
        begin_ts = parse_filter_timestamp(begin_time)
        end_ts = parse_filter_timestamp(end_time)
    except (TypeError, ValueError, OverflowError):
        return jsonify({"success": False, "error": "时间筛选格式无效"}), 400
    if begin_ts is not None:
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) >= begin_ts]
    if end_ts is not None:
        traces = [t for t in traces if isinstance(t, dict) and t.get("time", 0) <= end_ts]

    conversation_mode = str(conversation_mode).strip().lower() in {"1", "true", "yes", "on"}
    include_single_turns = str(include_single_turns).strip().lower() in {"1", "true", "yes", "on"}
    search_term = str(search_term or "").strip().lower()
    if conversation_mode:
        sessions, _ = build_conversation_workspace(traces, shop_id)
        if not include_single_turns:
            sessions = [session for session in sessions if session.get("isMultiTurn")]
        if search_term:
            def session_matches(session):
                values = [session.get("buyerId", "")]
                for row in session.get("records", []):
                    values.extend((row.get("question", ""), row.get("answer", ""), row.get("topic", "")))
                return any(search_term in str(value or "").lower() for value in values)

            sessions = [session for session in sessions if session_matches(session)]
        total = len(sessions)
        start = (page - 1) * page_size
        page_sessions = sessions[start:start + page_size]
        return jsonify({
            "success": True,
            "mode": "conversation",
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": max(1, (total + page_size - 1) // page_size),
            "summary": conversation_summary(sessions),
            "sessions": [public_conversation_session(session) for session in page_sessions],
        })

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
    shop_file_token(shop_id)
    trace_ids = [tid.strip() for tid in trace_ids_str.split(",") if tid.strip()]
    if not trace_ids:
        return jsonify({"success": False, "error": "没有trace ID"})

    traces = SHARED_STORE.load_traces(shop_id)
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
        identity = extract_trace_identity(record)
        buyer = identity.get("buyerId", "") or record.get("buyerAccount", "")
        seller = record.get("sellerAccount", "")
        topic = record.get("topicName", "")
        product_info = record.get("productInfo", {}) if isinstance(record.get("productInfo"), dict) else {}
        product_title = product_info.get("spuTitle", "") or product_info.get("title", "") or ""
        sku_id = product_info.get("skuId", "") or ""
        spu_id = product_info.get("spuId", "") or ""
        ts = record.get("time", 0)
        dt_str = format_trace_time(ts)

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

    # A conversation needs chronological order for a useful review.
    records.sort(key=lambda r: r.get("rawTime", 0))
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
    shop_file_token(shop_id)
    SHARED_STORE.set_issue_status(shop_id, issue_id, status)

    analysis = SHARED_STORE.load_analysis(shop_id)
    for issue in analysis.get("issueWorkbench", []):
        if issue.get("id") == issue_id:
            issue["status"] = status
    if analysis:
        SHARED_STORE.save_analysis(shop_id, analysis)
    return jsonify({"success": True, "status": status})


@app.route("/api/issue-feedback", methods=["GET", "POST"])
@require_auth
def issue_feedback():
    if request.method == "GET":
        shop_id = str(request.args.get("shopId", "") or "").strip()
        if not shop_id:
            return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
        shop_file_token(shop_id)
        return jsonify({"success": True, "feedback": SHARED_STORE.load_issue_feedback(shop_id)})

    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "") or "").strip()
    issue_id = str(data.get("issueId", "") or "").strip()
    verdict = str(data.get("verdict", "") or "").strip()
    note = str(data.get("note", "") or "").strip()[:500]
    if not shop_id or not issue_id:
        return jsonify({"success": False, "error": "缺少店铺或问题 ID"}), 400
    if verdict not in {"correct", "false_positive", "needs_review"}:
        return jsonify({"success": False, "error": "反馈类型无效"}), 400
    shop_file_token(shop_id)
    feedback = SHARED_STORE.set_issue_feedback(
        shop_id,
        issue_id,
        verdict,
        note=note,
        updated_by=session.get("username", ""),
    )

    analysis = SHARED_STORE.load_analysis(shop_id)
    for issue in analysis.get("issueWorkbench", []):
        if issue.get("id") == issue_id:
            issue["feedback"] = feedback
    if analysis:
        SHARED_STORE.save_analysis(shop_id, analysis)
    return jsonify({"success": True, "feedback": feedback})


@app.route("/api/overview")
@require_auth
def overview():
    shop_id = request.args.get("shopId", "").strip()
    if shop_id:
        shop_file_token(shop_id)
    traces = SHARED_STORE.load_traces(shop_id) if shop_id else []
    analysis = SHARED_STORE.load_analysis(shop_id) if shop_id else {}
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
    app.logger.exception("Unhandled request error")
    return jsonify({"success": False, "error": "服务暂时不可用，请稍后重试"}), 500


# ============================================================
# AI Analysis module
# ============================================================

AI_CONFIG_KEYS = tuple(DEFAULT_AI_CONFIG.keys())


def current_ai_config_username():
    if has_request_context():
        return session.get("username", "")
    return ""


def normalize_ai_config(config):
    config = config if isinstance(config, dict) else {}
    normalized = {}
    for key, default in DEFAULT_AI_CONFIG.items():
        normalized[key] = config.get(key, default)
    normalized["baseUrl"] = str(normalized.get("baseUrl", "") or "").strip()
    normalized["model"] = str(normalized.get("model", "") or "").strip()
    normalized["apiKey"] = str(normalized.get("apiKey", "") or "").strip()
    normalized["fullUrl"] = bool(normalized.get("fullUrl", False))
    try:
        normalized["temperature"] = float(normalized.get("temperature", DEFAULT_AI_CONFIG["temperature"]) or DEFAULT_AI_CONFIG["temperature"])
    except (TypeError, ValueError):
        normalized["temperature"] = DEFAULT_AI_CONFIG["temperature"]
    try:
        normalized["maxTokens"] = int(normalized.get("maxTokens", DEFAULT_AI_CONFIG["maxTokens"]) or DEFAULT_AI_CONFIG["maxTokens"])
    except (TypeError, ValueError):
        normalized["maxTokens"] = DEFAULT_AI_CONFIG["maxTokens"]
    normalized["timeoutSeconds"] = clamp_ai_timeout_seconds(normalized.get("timeoutSeconds", AI_DEFAULT_READ_TIMEOUT_SECONDS))
    return normalized


def legacy_ai_config_from_store(store):
    if not isinstance(store, dict):
        return normalize_ai_config({})
    if "providers" in store and "activeProvider" in store:
        providers = store.get("providers", {})
        active = store.get("activeProvider", "agnes")
        provider = providers.get(active, providers.get("agnes", {}))
        return normalize_ai_config({
            "baseUrl": provider.get("baseUrl", ""),
            "model": provider.get("model", ""),
            "apiKey": provider.get("apiKey", ""),
            "fullUrl": provider.get("fullUrl", False),
            "temperature": store.get("temperature", 0.3),
            "maxTokens": store.get("maxTokens", 4096),
            "timeoutSeconds": store.get("timeoutSeconds", AI_DEFAULT_READ_TIMEOUT_SECONDS),
        })
    return normalize_ai_config({key: store.get(key) for key in AI_CONFIG_KEYS if key in store})


def load_ai_config_store():
    store = load_json(AI_CONFIG_FILE, {})
    changed = False
    if not isinstance(store, dict):
        store = {}
        changed = True

    is_scoped_store = isinstance(store.get("users"), dict) or isinstance(store.get("default"), dict)
    if not is_scoped_store:
        base_config = legacy_ai_config_from_store(store)
        users = {}
        for username in load_users().keys():
            users[username] = dict(base_config)
        store = {"default": base_config, "users": users}
        changed = True
    else:
        default_config = normalize_ai_config(store.get("default", {}))
        if store.get("default") != default_config:
            store["default"] = default_config
            changed = True
        users = store.get("users")
        if not isinstance(users, dict):
            users = {}
            store["users"] = users
            changed = True
        for username, config in list(users.items()):
            normalized = normalize_ai_config(config)
            if config != normalized:
                users[username] = normalized
                changed = True

    if changed:
        save_json(AI_CONFIG_FILE, store)
    return store


def load_ai_config(username=None):
    username = username if username is not None else current_ai_config_username()
    username = str(username or "").strip()
    store = load_ai_config_store()
    if username:
        users = store.setdefault("users", {})
        if username not in users:
            users[username] = dict(store.get("default", DEFAULT_AI_CONFIG))
            save_json(AI_CONFIG_FILE, store)
        return normalize_ai_config(users.get(username, {}))
    return normalize_ai_config(store.get("default", {}))


def save_ai_config(config, username=None):
    username = username if username is not None else current_ai_config_username()
    username = str(username or "").strip()
    store = load_ai_config_store()
    if username:
        store.setdefault("users", {})[username] = normalize_ai_config(config)
    else:
        store["default"] = normalize_ai_config(config)
    save_json(AI_CONFIG_FILE, store)


def _mask_api_key(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


def validate_ai_base_url(value):
    value = str(value or "").strip()
    if not value:
        return False, "Base URL 不能为空"
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not hostname or parsed.username or parsed.password:
        return False, "Base URL 必须是无凭据的 HTTPS 地址"
    if hostname in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        return False, "Base URL 不允许访问本机或云元数据地址"
    try:
        address = ipaddress.ip_address(hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False, "Base URL 不允许访问内网地址"
    except ValueError:
        if "." not in hostname:
            return False, "Base URL 域名无效"
    return True, ""


def clamp_ai_timeout_seconds(value):
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = AI_DEFAULT_READ_TIMEOUT_SECONDS
    return max(AI_MIN_READ_TIMEOUT_SECONDS, min(AI_MAX_READ_TIMEOUT_SECONDS, timeout))


def _resolve_config(cfg):
    """Return a flat dict with the current AI config values."""
    return {
        "apiKey": cfg.get("apiKey", "").strip(),
        "baseUrl": cfg.get("baseUrl", "").strip(),
        "model": cfg.get("model", "").strip(),
        "fullUrl": bool(cfg.get("fullUrl", False)),
        "temperature": float(cfg.get("temperature", 0.3)),
        "maxTokens": int(cfg.get("maxTokens", 4096)),
        "timeoutSeconds": clamp_ai_timeout_seconds(cfg.get("timeoutSeconds", AI_DEFAULT_READ_TIMEOUT_SECONDS)),
    }


def _call_llm(system_prompt, user_text, cfg):
    """Call the configured LLM provider via OpenAI-compatible API."""
    p = _resolve_config(cfg)
    api_key = p["apiKey"]
    base_url = p["baseUrl"]
    model = p["model"]
    temperature = p["temperature"]
    max_tokens = p["maxTokens"]
    timeout_seconds = p["timeoutSeconds"]

    if not api_key or not base_url or not model:
        return None, "未配置 API Key、Base URL 或 Model"
    valid_url, url_error = validate_ai_base_url(base_url)
    if not valid_url:
        return None, url_error

    # Detect Anthropic vs OpenAI-compatible API format
    is_anthropic = "anthropic" in base_url.lower() or "anthropic" in api_key.lower()

    if is_anthropic:
        url = base_url.rstrip("/") if p["fullUrl"] else base_url.rstrip("/") + "/messages"
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
        url = base_url.rstrip("/") if p["fullUrl"] else base_url.rstrip("/") + "/chat/completions"
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
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=(AI_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
        )
        resp.raise_for_status()
        data = resp.json()

        if is_anthropic:
            content = data.get("content", [])
            if isinstance(content, list) and content:
                text = content[0].get("text", "")
            else:
                text = str(data)
        else:
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


def format_llm_error(err, attempt=None, issue_limit=None):
    raw = str(err or "")
    prefix = f"LLM 调用失败 (尝试{attempt}次): " if attempt else "LLM 调用失败: "
    if "read timed out" in raw.lower():
        match = re.search(r"read timeout=([0-9.]+)", raw, re.IGNORECASE)
        timeout = match.group(1) if match else str(AI_DEFAULT_READ_TIMEOUT_SECONDS)
        batch_hint = f"当前每批 {issue_limit} 条，" if issue_limit else ""
        return (
            f"{prefix}AI 接口超过 {timeout} 秒仍未返回。"
            f"{batch_hint}建议先改成 20 条一批，或在 API 配置中把 Read Timeout(s) 调到 600/900 后重试。"
            f"原始错误: {raw}"
        )
    return f"{prefix}{raw}"


@app.route("/api/ai/config")
@require_auth
def ai_config_get():
    cfg = load_ai_config()
    masked_key = _mask_api_key(cfg.get("apiKey", ""))
    return jsonify({
        "success": True,
        "config": {
            "baseUrl": cfg.get("baseUrl", ""),
            "model": cfg.get("model", ""),
            "fullUrl": bool(cfg.get("fullUrl", False)),
            "providerName": cfg.get("providerName", ""),
            "providerNote": cfg.get("providerNote", ""),
            "providerWebsite": cfg.get("providerWebsite", ""),
            "apiKey": masked_key,
            "apiKeyMasked": bool(masked_key),
            "temperature": cfg.get("temperature", 0.3),
            "maxTokens": cfg.get("maxTokens", 4096),
            "timeoutSeconds": clamp_ai_timeout_seconds(cfg.get("timeoutSeconds", AI_DEFAULT_READ_TIMEOUT_SECONDS)),
        },
    })


@app.route("/api/ai/config", methods=["POST"])
@require_auth
def ai_config_save():
    data = request.get_json() or {}
    cfg = load_ai_config()

    # Update temperature / max tokens
    if "fullUrl" in data:
        cfg["fullUrl"] = bool(data["fullUrl"])
    for key in ("providerName", "providerNote", "providerWebsite"):
        if key in data:
            cfg[key] = str(data[key] or "").strip()
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
    if "timeoutSeconds" in data:
        cfg["timeoutSeconds"] = clamp_ai_timeout_seconds(data["timeoutSeconds"])

    # Update baseUrl / model
    for key in ("baseUrl", "model"):
        if key in data:
            val = str(data[key]).strip()
            if key == "baseUrl" and val:
                valid_url, url_error = validate_ai_base_url(val)
                if not valid_url:
                    return jsonify({"success": False, "error": url_error}), 400
            cfg[key] = val

    # Only update apiKey if user actually typed something
    if "apiKey" in data and data["apiKey"]:
        cfg["apiKey"] = str(data["apiKey"]).strip()

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



def _slice_list(value, limit):
    if not isinstance(value, list):
        return []
    return value[:limit]


def clamp_ai_issue_batch_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = AI_DEFAULT_ISSUE_BATCH_SIZE
    return max(1, min(AI_MAX_ISSUE_BATCH_SIZE, limit))


def clamp_ai_issue_offset(value):
    try:
        offset = int(value)
    except (TypeError, ValueError):
        offset = 0
    return max(0, offset)


def compact_ai_context(local_analysis, issue_offset=0, issue_limit=None):
    """Keep the local analysis rich enough for the LLM while bounding prompt size."""
    if not isinstance(local_analysis, dict):
        local_analysis = {}

    def compact_issue(issue):
        draft = issue.get("knowledgeCardDraft", {}) if isinstance(issue.get("knowledgeCardDraft"), dict) else {}
        examples = issue.get("examples", []) if isinstance(issue.get("examples"), list) else []
        return {
            "id": issue.get("id", ""),
            "topic": issue.get("topic", ""),
            "issueType": issue.get("issueType", ""),
            "priority": issue.get("priority", ""),
            "score": issue.get("score", 0),
            "count": issue.get("count", 0),
            "unresolvedCount": issue.get("unresolvedCount", 0),
            "negativeCount": issue.get("negativeCount", 0),
            "adoptionRisk": issue.get("adoptionRisk", 0),
            "failureReason": issue.get("failureReason", ""),
            "reasons": _slice_list(issue.get("reasons", []), 3),
            "suggestedAction": issue.get("suggestedAction", ""),
            "trainingSuggestion": issue.get("trainingSuggestion", ""),
            "status": issue.get("status", ""),
            "knowledgeCardDraft": {
                "title": draft.get("title", ""),
                "standardQuestion": draft.get("standardQuestion", ""),
                "similarQuestions": _slice_list(draft.get("similarQuestions", []), 5),
                "triggerWords": _slice_list(draft.get("triggerWords", []), 8),
                "answerOutline": draft.get("answerOutline", ""),
                "standardAnswer": draft.get("standardAnswer", ""),
                "manualHandoffRule": draft.get("manualHandoffRule", ""),
                "applicableScene": draft.get("applicableScene", ""),
                "notApplicableScene": draft.get("notApplicableScene", ""),
                "acceptanceGoal": draft.get("acceptanceGoal", ""),
            },
            "examples": [
                {
                    "id": example.get("id", ""),
                    "question": str(example.get("question", ""))[:220],
                    "answer": str(example.get("answer", ""))[:420],
                    "identity": example.get("identity", {}) if isinstance(example.get("identity"), dict) else {},
                }
                for example in examples[:3]
                if isinstance(example, dict)
            ],
        }

    def compact_qa(topic):
        examples = topic.get("examples", []) if isinstance(topic.get("examples"), list) else []
        return {
            "topic": topic.get("topic", ""),
            "count": topic.get("count", 0),
            "examples": [
                {
                    "id": item.get("id", ""),
                    "question": str(item.get("question", ""))[:220],
                    "answer": str(item.get("answer", ""))[:420],
                    "identity": item.get("identity", {}) if isinstance(item.get("identity"), dict) else {},
                }
                for item in examples[:3]
                if isinstance(item, dict)
            ],
        }

    topic_insights = local_analysis.get("topicInsights", {}) if isinstance(local_analysis.get("topicInsights"), dict) else {}
    identity_insights = local_analysis.get("identityInsights", {}) if isinstance(local_analysis.get("identityInsights"), dict) else {}
    all_issues = [issue for issue in _as_list(local_analysis.get("issueWorkbench")) if isinstance(issue, dict)]
    issue_offset = clamp_ai_issue_offset(issue_offset)
    issue_limit = clamp_ai_issue_batch_limit(issue_limit)
    issue_batch = all_issues[issue_offset:issue_offset + issue_limit]
    issue_next_offset = issue_offset + len(issue_batch)
    issue_batch_info = {
        "offset": issue_offset,
        "limit": issue_limit,
        "count": len(issue_batch),
        "total": len(all_issues),
        "nextOffset": issue_next_offset,
        "hasMore": issue_next_offset < len(all_issues),
    }
    return {
        "totalRecords": local_analysis.get("totalRecords", 0),
        "withContent": local_analysis.get("withContent", 0),
        "topicDistribution": _slice_list(local_analysis.get("topicDistribution", []), 12),
        "storeDiagnosis": local_analysis.get("storeDiagnosis", {}) if isinstance(local_analysis.get("storeDiagnosis"), dict) else {},
        "issueBatch": issue_batch_info,
        "issueWorkbench": [compact_issue(issue) for issue in issue_batch],
        "qaExamples": [
            compact_qa(topic)
            for topic in _slice_list(local_analysis.get("qaExamples", []), 8)
            if isinstance(topic, dict)
        ],
        "topicInsights": {
            "topicCount": topic_insights.get("topicCount", 0),
            "knowledgeGapCount": topic_insights.get("knowledgeGapCount", 0),
            "avgAdoptionRisk": topic_insights.get("avgAdoptionRisk", 0),
            "totalIssueCount": topic_insights.get("totalIssueCount", 0),
            "topics": _slice_list(topic_insights.get("topics", []), 8),
            "riskTopics": _slice_list(topic_insights.get("riskTopics", []), 6),
            "gapTopics": _slice_list(topic_insights.get("gapTopics", []), 6),
            "keywords": _slice_list(topic_insights.get("keywords", []), 12),
        },
        "identityInsights": {
            "productCoverage": identity_insights.get("productCoverage", 0),
            "buyerCoverage": identity_insights.get("buyerCoverage", 0),
            "productCount": identity_insights.get("productCount", 0),
            "buyerCount": identity_insights.get("buyerCount", 0),
            "topProducts": _slice_list(identity_insights.get("topProducts", []), 6),
            "issueProducts": _slice_list(identity_insights.get("issueProducts", []), 6),
            "topBuyers": _slice_list(identity_insights.get("topBuyers", []), 6),
            "issueBuyers": _slice_list(identity_insights.get("issueBuyers", []), 6),
        },
        "topKeywords": _slice_list(local_analysis.get("topKeywords", []), 12),
    }


def build_ai_trace_samples(traces, max_samples=40):
    samples = []
    for record in traces:
        if len(samples) >= max_samples:
            break
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        identity = compact_identity(extract_trace_identity(record))
        samples.append({
            "id": record.get("id", ""),
            "topic": str(record.get("topicName", "") or "")[:80],
            "type": str(record.get("type", "") or "")[:40],
            "seller": str(record.get("sellerAccount", "") or "")[:80],
            "question": str(question or "")[:260],
            "answer": str(answer or "")[:520],
            "identity": identity,
        })
    return samples


def build_ai_analysis_prompt_bundle(traces, shop_id, local_analysis=None, issue_offset=0, issue_limit=None):
    local_context = compact_ai_context(local_analysis or {}, issue_offset=issue_offset, issue_limit=issue_limit)
    trace_samples = build_ai_trace_samples(
        traces,
        max_samples=max(10, min(20, int(local_context.get("issueBatch", {}).get("limit", 10)))),
    )
    analysis_context = {
        "shopId": shop_id,
        "issueBatch": local_context.get("issueBatch", {}),
        "localAnalysis": local_context,
        "traceSamples": trace_samples,
    }
    system_prompt = """You are an ecommerce AI-agent QA expert and knowledge-base operations consultant. Use localAnalysis plus traceSamples to produce a deep, actionable diagnosis for an optimization workbench.

Return JSON only. Do not return Markdown. The JSON must include these fields:
{
  "summary": "one-sentence summary for legacy UI",
  "recommendations": ["legacy recommendation"],
  "classifications": [
    {
      "issueType": "未回复|弱回复|高风险|正常",
      "priority": "高|中|低",
      "topic": "issue topic",
      "standardQuestion": "standard question",
      "rootCause": "root cause",
      "suggestedAction": "suggested action",
      "count": 0
    }
  ],
  "knowledgeCards": [
    {
      "title": "card title",
      "standardQuestion": "standard question",
      "similarQuestions": ["similar question"],
      "triggerWords": ["trigger word"],
      "standardAnswer": "standard answer",
      "manualHandoffRule": "handoff rule"
    }
  ],
  "executiveSummary": {
    "healthScore": 0,
    "mainConclusion": "main conclusion",
    "topRisks": ["top risk"],
    "quickWins": ["quick win"]
  },
  "pendingKnowledgeCards": [
    {
      "title": "pending card title",
      "priority": "高|中|低",
      "topic": "topic",
      "standardQuestion": "standard question",
      "similarQuestions": ["similar question"],
      "triggerWords": ["trigger word"],
      "standardAnswer": "answer ready for the knowledge base",
      "manualHandoffRule": "handoff boundary",
      "sourceIssueIds": ["issue id"],
      "acceptanceGoal": "acceptance goal"
    }
  ],
  "issueOptimizationWorkbench": [
    {
      "issueId": "issue id",
      "priority": "高|中|低",
      "issueType": "未回复|弱回复|高风险",
      "topic": "topic",
      "rootCause": "root cause",
      "impact": "impact on adoption or conversion",
      "suggestedAction": "action",
      "ownerHint": "knowledge|script|handoff|product-info",
      "verificationMethod": "how to verify",
      "evidenceExamples": [
        {
          "traceId": "trace id",
          "buyerId": "buyer id or nickname",
          "productTitle": "product title",
          "productId": "product id",
          "spuId": "spu id",
          "skuId": "sku id",
          "question": "original user question",
          "currentAnswer": "current agent answer"
        }
      ],
      "customerServiceReply": "copy-ready customer-service reply for this issue",
      "knowledgeBaseAnswer": "copy-ready knowledge-base answer with conditions, steps, timing, and boundaries",
      "manualHandoffScript": "copy-ready manual handoff rule and wording",
      "avoidWords": ["words or vague expressions to avoid"],
      "qualityChecklist": ["quality check item"],
      "copyReadyTemplate": "full copy-ready QA template",
      "qaTemplate": {
        "standardQuestion": "standard question for knowledge base",
        "similarQuestions": ["similar question"],
        "triggerWords": ["trigger word"],
        "standardAnswer": "copy-ready answer",
        "manualHandoffRule": "manual handoff boundary",
        "applicableScene": "when to use",
        "notApplicableScene": "when not to use",
        "acceptanceGoal": "how to verify",
        "customerServiceReply": "copy-ready customer-service reply",
        "knowledgeBaseAnswer": "copy-ready knowledge-base answer",
        "manualHandoffScript": "copy-ready manual handoff script",
        "avoidWords": ["words to avoid"],
        "qualityChecklist": ["check item"],
        "copyReadyTemplate": "full template that can be copied directly"
      }
    }
  ],
  "qualityInspectionWorkbench": "same array as issueOptimizationWorkbench; each item must be usable as a quality-inspection script card",
  "typicalQAExamples": [
    {
      "topic": "topic",
      "question": "typical user question",
      "currentAnswer": "current answer",
      "optimizedAnswer": "optimized answer",
      "whyBetter": "why it is better",
      "evidenceExamples": [
        {
          "traceId": "trace id",
          "buyerId": "buyer id or nickname",
          "productTitle": "product title",
          "question": "original user question",
          "currentAnswer": "current answer"
        }
      ]
    }
  ],
  "topicDeepDives": [
    {
      "topic": "topic",
      "volume": 0,
      "riskLevel": "高|中|低",
      "knowledgeGaps": ["knowledge gap"],
      "conversationPattern": "common follow-up pattern",
      "recommendedPlaybook": "recommended playbook"
    }
  ],
  "actionPlan": [
    {
      "phase": "今日|本周|复查",
      "task": "task",
      "expectedImpact": "expected impact",
      "doneDefinition": "definition of done"
    }
  ],
  "riskAlerts": [
    {
      "level": "高|中|低",
      "title": "risk title",
      "evidence": "evidence",
      "mitigation": "mitigation"
    }
  ]
}

Rules:
1. Prioritize localAnalysis.issueWorkbench, storeDiagnosis, qaExamples, topicInsights, and identityInsights. Do not only summarize traceSamples.
2. pendingKnowledgeCards must be ready to enter the knowledge base, with conditions, steps, timing, and handoff boundaries.
3. issueOptimizationWorkbench must be sorted by priority and focus on high-risk, no-reply, weak-reply, and frequent topics.
4. typicalQAExamples must compare currentAnswer with optimizedAnswer.
5. Keep field names in English. Use Simplified Chinese for all user-facing field values.
6. The current batch is localAnalysis.issueBatch. Generate qualityInspectionWorkbench / issueOptimizationWorkbench for as many items in the current issueWorkbench batch as possible, up to issueBatch.limit. Do not silently stop at 10 items.
7. If the batch is large, keep each item concise but preserve copy-ready scripts. pendingKnowledgeCards <= issueBatch.limit, typicalQAExamples <= min(issueBatch.limit, 50), topicDeepDives <= 12, actionPlan <= 8, riskAlerts <= 8. Never truncate JSON.
8. For issueOptimizationWorkbench and typicalQAExamples, include evidenceExamples with buyer/product/trace/question/currentAnswer when available, and include customerServiceReply, knowledgeBaseAnswer, manualHandoffScript, avoidWords, qualityChecklist, copyReadyTemplate, and a copy-ready qaTemplate for every issue that needs a knowledge card."""
    user_text = f"""请基于下面的本地分析结果和 trace 样本，生成 AI 深度分析。

重点输出模块：
- 待补知识卡片
- 问题优化工作台
- AI 质检话术工作台
- 典型 QA 实例
- 主题深挖
- 行动计划
- 风险提醒

当前批次说明：只质检 localAnalysis.issueWorkbench 里的当前批次；如果 issueBatch.hasMore 为 true，前端会继续生成后续批次，所以本次不要试图概括未提供的后续问题。
请为当前批次生成可直接复制使用的话术；继续生成后续批次时会复用同一结构追加到工作台。

分析上下文：
{json.dumps(analysis_context, ensure_ascii=False, indent=2)}"""
    return {
        "systemPrompt": system_prompt,
        "userText": user_text,
        "analysisContext": analysis_context,
    }




def _strip_json_fences(text):
    cleaned = str(text or "").strip().lstrip("\ufeff")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    return cleaned


def _find_balanced_json_object(text):
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def parse_ai_response(text):
    """Parse JSON-ish model output into a dict."""
    cleaned = _strip_json_fences(text)
    candidates = [cleaned]
    balanced = _find_balanced_json_object(cleaned)
    if balanced and balanced not in candidates:
        candidates.append(balanced)
    body = cleaned.strip().rstrip(",")
    if body.startswith('"') and ":" in body[:120]:
        candidates.append("{" + body + "}")

    errors = []
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = json.loads(candidate, strict=False)
            if not isinstance(result, dict):
                raise ValueError("AI JSON root must be an object")
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(str(exc))
    raise ValueError("; ".join(errors) or "empty AI response")


def is_probably_truncated_ai_json_error(error):
    raw = str(error or "").lower()
    return any(fragment in raw for fragment in (
        "unterminated string",
        "expecting ',' delimiter",
        "expecting property name",
        "expecting value",
        "empty ai response",
    ))


def _as_list(value):
    return value if isinstance(value, list) else []



def build_ai_evidence_examples(source, limit=3):
    examples = source.get("examples", []) if isinstance(source, dict) else []
    rows = []
    for example in _as_list(examples)[:limit]:
        if not isinstance(example, dict):
            continue
        identity = example.get("identity", {}) if isinstance(example.get("identity"), dict) else {}
        rows.append({
            "traceId": identity.get("traceId") or example.get("id", ""),
            "buyerId": identity.get("buyerId", ""),
            "buyerIdRaw": identity.get("buyerIdRaw", identity.get("buyerId", "")),
            "productTitle": identity.get("productTitle", ""),
            "productId": identity.get("productId", ""),
            "spuId": identity.get("spuId", ""),
            "skuId": identity.get("skuId", ""),
            "topic": example.get("topicName") or example.get("topic") or source.get("topic", ""),
            "question": example.get("question", ""),
            "currentAnswer": example.get("answer", ""),
            "seller": example.get("seller", ""),
            "type": example.get("type", ""),
        })
    return rows


def build_ai_qa_template(source, fallback=None):
    fallback = fallback if isinstance(fallback, dict) else {}
    draft = source.get("knowledgeCardDraft", {}) if isinstance(source, dict) and isinstance(source.get("knowledgeCardDraft"), dict) else {}
    first_example = {}
    examples = source.get("examples", []) if isinstance(source, dict) else []
    if examples and isinstance(examples[0], dict):
        first_example = examples[0]
    standard_question = (
        draft.get("standardQuestion")
        or fallback.get("standardQuestion")
        or source.get("standardQuestion", "")
        or first_example.get("question", "")
    )
    standard_answer = (
        draft.get("standardAnswer")
        or draft.get("answerOutline")
        or fallback.get("standardAnswer")
        or fallback.get("optimizedAnswer")
        or fallback.get("suggestedAction")
        or source.get("trainingSuggestion", "")
    )
    handoff = draft.get("manualHandoffRule") or fallback.get("manualHandoffRule") or ""
    customer_reply = fallback.get("customerServiceReply") or standard_answer
    knowledge_answer = fallback.get("knowledgeBaseAnswer") or standard_answer
    handoff_script = fallback.get("manualHandoffScript") or handoff
    copy_ready = fallback.get("copyReadyTemplate") or "\n".join(
        line for line in [
            f"Q:{standard_question}" if standard_question else "",
            f"????:{customer_reply}" if customer_reply else "",
            f"?????:{knowledge_answer}" if knowledge_answer else "",
            f"?????:{handoff_script}" if handoff_script else "",
        ] if line
    )
    return {
        "standardQuestion": standard_question,
        "similarQuestions": _as_list(draft.get("similarQuestions")) or _as_list(fallback.get("similarQuestions")),
        "triggerWords": _as_list(draft.get("triggerWords")) or _as_list(fallback.get("triggerWords")),
        "standardAnswer": standard_answer,
        "manualHandoffRule": handoff,
        "applicableScene": draft.get("applicableScene") or fallback.get("applicableScene") or "",
        "notApplicableScene": draft.get("notApplicableScene") or fallback.get("notApplicableScene") or "",
        "acceptanceGoal": draft.get("acceptanceGoal") or fallback.get("acceptanceGoal") or "",
        "customerServiceReply": customer_reply,
        "knowledgeBaseAnswer": knowledge_answer,
        "manualHandoffScript": handoff_script,
        "avoidWords": _as_list(fallback.get("avoidWords")),
        "qualityChecklist": _as_list(fallback.get("qualityChecklist")),
        "copyReadyTemplate": copy_ready,
    }


def find_local_issue_for_ai_item(item, local_issues):
    if not isinstance(item, dict):
        return {}
    issue_id = item.get("issueId") or item.get("id")
    topic = str(item.get("topic", "") or "")
    standard_question = str(item.get("standardQuestion", "") or "")
    for issue in local_issues:
        if not isinstance(issue, dict):
            continue
        if issue_id and issue_id == issue.get("id"):
            return issue
    for issue in local_issues:
        if not isinstance(issue, dict):
            continue
        if topic and topic == str(issue.get("topic", "") or ""):
            return issue
    for issue in local_issues:
        if not isinstance(issue, dict):
            continue
        draft = issue.get("knowledgeCardDraft", {}) if isinstance(issue.get("knowledgeCardDraft"), dict) else {}
        if standard_question and standard_question in (issue.get("standardQuestion"), draft.get("standardQuestion")):
            return issue
    return local_issues[0] if local_issues else {}

def normalize_ai_result(result, local_analysis=None, batch_info=None):
    """Backfill the deep dashboard shape when the model returns legacy fields."""
    if not isinstance(result, dict):
        raise ValueError("AI result must be a JSON object")
    local_analysis = local_analysis if isinstance(local_analysis, dict) else {}
    store = local_analysis.get("storeDiagnosis", {}) if isinstance(local_analysis.get("storeDiagnosis"), dict) else {}
    topic_insights = local_analysis.get("topicInsights", {}) if isinstance(local_analysis.get("topicInsights"), dict) else {}
    local_issues = [issue for issue in _as_list(local_analysis.get("issueWorkbench")) if isinstance(issue, dict)]

    executive = result.get("executiveSummary") if isinstance(result.get("executiveSummary"), dict) else {}
    result.setdefault("summary", executive.get("mainConclusion", ""))
    result.setdefault("recommendations", [])
    result.setdefault("classifications", [])
    result.setdefault("knowledgeCards", [])

    if not isinstance(result.get("executiveSummary"), dict):
        result["executiveSummary"] = {
            "healthScore": store.get("healthScore", ""),
            "mainConclusion": result.get("summary") or "AI \u5df2\u751f\u6210\u5206\u6790\u7ed3\u8bba",
            "topRisks": _as_list(store.get("summary"))[:3],
            "quickWins": _as_list(result.get("recommendations"))[:3],
            "knowledgeGapCount": topic_insights.get("knowledgeGapCount", 0),
        }

    if not _as_list(result.get("pendingKnowledgeCards")):
        result["pendingKnowledgeCards"] = [
            {
                "title": card.get("title") or card.get("standardQuestion") or "\u77e5\u8bc6\u5361\u7247",
                "priority": card.get("priority") or "\u4e2d",
                "topic": card.get("topic") or "",
                "standardQuestion": card.get("standardQuestion") or "",
                "similarQuestions": _as_list(card.get("similarQuestions")),
                "triggerWords": _as_list(card.get("triggerWords")),
                "standardAnswer": card.get("standardAnswer") or card.get("answerOutline") or "",
                "manualHandoffRule": card.get("manualHandoffRule") or "",
                "sourceIssueIds": _as_list(card.get("sourceIssueIds")),
                "acceptanceGoal": card.get("acceptanceGoal") or "\u77e5\u8bc6\u547d\u4e2d\u540e\u80fd\u8986\u76d6\u5178\u578b\u95ee\u6cd5",
            }
            for card in _as_list(result.get("knowledgeCards"))[:10]
            if isinstance(card, dict)
        ]

    if not _as_list(result.get("issueOptimizationWorkbench")):
        result["issueOptimizationWorkbench"] = [
            {
                "issueId": item.get("issueId") or item.get("id") or "",
                "priority": item.get("priority") or "\u4e2d",
                "issueType": item.get("issueType") or "\u5f85\u5224\u65ad",
                "topic": item.get("topic") or "",
                "rootCause": item.get("rootCause") or item.get("failureReason") or "",
                "impact": item.get("impact") or f"\u5f71\u54cd {item.get('count', 0)} \u6761 trace",
                "suggestedAction": item.get("suggestedAction") or item.get("trainingSuggestion") or "",
                "ownerHint": item.get("ownerHint") or "knowledge",
                "verificationMethod": item.get("verificationMethod") or "\u590d\u67e5\u540c\u4e3b\u9898 trace \u7684\u56de\u7b54\u547d\u4e2d\u7387",
            }
            for item in _as_list(result.get("classifications"))[:12]
            if isinstance(item, dict)
        ]

    for issue in _as_list(result.get("issueOptimizationWorkbench")):
        if not isinstance(issue, dict):
            continue
        local_issue = find_local_issue_for_ai_item(issue, local_issues)
        if not _as_list(issue.get("evidenceExamples")):
            issue["evidenceExamples"] = build_ai_evidence_examples(local_issue)
        if not isinstance(issue.get("qaTemplate"), dict):
            issue["qaTemplate"] = build_ai_qa_template(local_issue, issue)
        else:
            issue["qaTemplate"] = build_ai_qa_template(local_issue, {**issue, **issue.get("qaTemplate", {})})
        template = issue.get("qaTemplate", {}) if isinstance(issue.get("qaTemplate"), dict) else {}
        issue.setdefault("customerServiceReply", template.get("customerServiceReply") or template.get("standardAnswer", ""))
        issue.setdefault("knowledgeBaseAnswer", template.get("knowledgeBaseAnswer") or template.get("standardAnswer", ""))
        issue.setdefault("manualHandoffScript", template.get("manualHandoffScript") or template.get("manualHandoffRule", ""))
        issue.setdefault("avoidWords", _as_list(template.get("avoidWords")))
        issue.setdefault("qualityChecklist", _as_list(template.get("qualityChecklist")))
        issue.setdefault("copyReadyTemplate", template.get("copyReadyTemplate") or "")

    for card in _as_list(result.get("pendingKnowledgeCards")):
        if not isinstance(card, dict):
            continue
        local_issue = find_local_issue_for_ai_item(card, local_issues)
        if not _as_list(card.get("evidenceExamples")):
            card["evidenceExamples"] = build_ai_evidence_examples(local_issue)
        if not isinstance(card.get("qaTemplate"), dict):
            card["qaTemplate"] = build_ai_qa_template(local_issue, card)

    if not _as_list(result.get("typicalQAExamples")):
        examples = []
        for topic in _as_list(local_analysis.get("qaExamples"))[:6]:
            if not isinstance(topic, dict):
                continue
            for item in _as_list(topic.get("examples"))[:1]:
                if isinstance(item, dict):
                    identity = item.get("identity", {}) if isinstance(item.get("identity"), dict) else {}
                    examples.append({
                        "topic": topic.get("topic", ""),
                        "question": item.get("question", ""),
                        "currentAnswer": item.get("answer", ""),
                        "optimizedAnswer": "\u53ef\u57fa\u4e8e\u5f85\u8865\u77e5\u8bc6\u5361\u7247\u8865\u5145\u66f4\u5b8c\u6574\u7684\u6807\u51c6\u56de\u590d",
                        "whyBetter": "\u8865\u5145\u6761\u4ef6\u3001\u6d41\u7a0b\u548c\u8f6c\u4eba\u5de5\u8fb9\u754c",
                        "evidenceExamples": [{
                            "traceId": identity.get("traceId") or item.get("id", ""),
                            "buyerId": identity.get("buyerId", ""),
                            "buyerIdRaw": identity.get("buyerIdRaw", identity.get("buyerId", "")),
                            "productTitle": identity.get("productTitle", ""),
                            "productId": identity.get("productId", ""),
                            "spuId": identity.get("spuId", ""),
                            "skuId": identity.get("skuId", ""),
                            "topic": topic.get("topic", ""),
                            "question": item.get("question", ""),
                            "currentAnswer": item.get("answer", ""),
                        }],
                    })
        result["typicalQAExamples"] = examples

    if not _as_list(result.get("topicDeepDives")):
        result["topicDeepDives"] = [
            {
                "topic": item.get("topic", ""),
                "volume": item.get("count", 0),
                "riskLevel": "\u9ad8" if item.get("adoptionRisk", 0) >= 70 else "\u4e2d",
                "knowledgeGaps": [item.get("suggestedAction", "\u8865\u9f50\u77e5\u8bc6\u70b9")],
                "conversationPattern": "\u9ad8\u9891\u54a8\u8be2\u548c\u8ffd\u95ee\u96c6\u4e2d\u5728\u8be5\u4e3b\u9898",
                "recommendedPlaybook": item.get("suggestedAction", "\u4f18\u5148\u8865\u5361\u5e76\u590d\u67e5"),
            }
            for item in _as_list(topic_insights.get("topics"))[:8]
            if isinstance(item, dict)
        ]

    if not _as_list(result.get("actionPlan")):
        result["actionPlan"] = [
            {
                "phase": "\u5efa\u8bae",
                "task": item,
                "expectedImpact": "\u964d\u4f4e\u672a\u56de\u590d\u6216\u5f31\u56de\u590d\u98ce\u9669",
                "doneDefinition": "\u8865\u5145\u540e\u590d\u67e5\u540c\u7c7b trace",
            }
            for item in _as_list(result.get("recommendations"))[:6]
        ]

    result.setdefault("riskAlerts", [])
    if isinstance(batch_info, dict):
        result["aiBatchInfo"] = batch_info
    else:
        result.setdefault("aiBatchInfo", {})
    result["qualityInspectionWorkbench"] = _as_list(result.get("issueOptimizationWorkbench"))
    return result


# Legacy synchronous implementation kept as an internal reference while the public endpoint uses tasks.
def ai_analyze_legacy():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})
    shop_file_token(shop_id)

    traces = SHARED_STORE.load_traces(shop_id)
    if not traces:
        return jsonify({"success": False, "error": "没有可分析的数据，请先抓取数据"})

    cfg = load_ai_config()
    p = _resolve_config(cfg)
    if not p["apiKey"] or not p["baseUrl"] or not p["model"]:
        return jsonify({"success": False, "error": "请先在 API 配置中设置模型参数"})

    local_analysis = SHARED_STORE.load_analysis(shop_id)
    if not local_analysis or "storeDiagnosis" not in local_analysis or "topicInsights" not in local_analysis:
        local_analysis = run_analysis(traces, shop_id)
        SHARED_STORE.save_analysis(shop_id, local_analysis)

    issue_offset = clamp_ai_issue_offset(data.get("issueOffset", data.get("offset", 0)))
    issue_limit = clamp_ai_issue_batch_limit(data.get("issueLimit", data.get("limit", AI_DEFAULT_ISSUE_BATCH_SIZE)))
    prompt_bundle = build_ai_analysis_prompt_bundle(
        traces,
        shop_id,
        local_analysis,
        issue_offset=issue_offset,
        issue_limit=issue_limit,
    )
    system_prompt = prompt_bundle["systemPrompt"]
    user_text = prompt_bundle["userText"]
    batch_info = prompt_bundle["analysisContext"].get("issueBatch", {})

    parse_error = None
    result = None
    err = None
    llm_text = ""
    request_cfg = dict(cfg)
    current_prompt = user_text
    token_retry_plan = [8192, 12000]
    for attempt in range(1, 4):
        llm_text, err = _call_llm(system_prompt, current_prompt, request_cfg)
        if err:
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            break
        try:
            result = normalize_ai_result(parse_ai_response(llm_text), local_analysis, batch_info)
            break
        except ValueError as exc:
            parse_error = exc
            if attempt >= 3:
                break
            if is_probably_truncated_ai_json_error(exc):
                next_max_tokens = token_retry_plan[min(attempt - 1, len(token_retry_plan) - 1)]
                request_cfg["maxTokens"] = max(int(request_cfg.get("maxTokens", 0) or 0), next_max_tokens)
            repair_prompt = (
                user_text
                + "\n\n上一次输出不是可解析 JSON。请重新输出更紧凑且完整闭合的 JSON 对象。"
                + "不要 Markdown，不要解释文字；数组最多 6 项；所有字符串、数组和对象必须闭合；不要在 JSON 中间截断。"
            )
            current_prompt = repair_prompt
            time.sleep(1)

    if err:
        return jsonify({"success": False, "error": format_llm_error(err, attempt=attempt, issue_limit=issue_limit)})

    if result is None:
        raw_preview = str(llm_text or "")[:800]
        token_hint = "后端已自动用更高 Max Tokens 重试；仍失败时建议把“每批”调到 5，或把 Max Tokens 调到 12000 后重试。"
        error_text = (
            f"AI 返回格式解析失败: {parse_error}"
            f"\n建议：{token_hint}"
            f"\n原始内容: {raw_preview}"
        )
        return jsonify({"success": False, "error": error_text})

    return jsonify({"success": True, "data": result})


def is_retryable_ai_error(error):
    """Retry only transient provider failures, never invalid configuration or other 4xx errors."""
    raw = str(error or "").lower()
    if "timed out" in raw or "timeout" in raw:
        return True
    if re.search(r"(?:^|\D)429(?:\D|$)", raw):
        return True
    return bool(re.search(r"(?:^|\D)5\d\d(?:\D|$)", raw))


def build_ai_cache_key(shop_id, traces, local_analysis, cfg, issue_offset, issue_limit):
    local = dict(local_analysis) if isinstance(local_analysis, dict) else {}
    local.pop("analyzedAt", None)
    cache_payload = {
        "version": AI_PROMPT_VERSION,
        "shopId": shop_id,
        "issueOffset": issue_offset,
        "issueLimit": issue_limit,
        "model": cfg.get("model", ""),
        "baseUrl": cfg.get("baseUrl", ""),
        "temperature": cfg.get("temperature", 0.3),
        "maxTokens": cfg.get("maxTokens", 4096),
        "traces": traces,
        "localAnalysis": local,
    }


def format_trace_time(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp or 0) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(timestamp or "")


def build_conversation_workspace(traces, shop_id=""):
    """Build reusable conversation sessions and a trace-to-session lookup."""
    rows = []
    for record in traces:
        if not isinstance(record, dict):
            continue
        question, answer = parse_conversation(record)
        trace_id = str(record.get("id") or record.get("traceId") or "").strip()
        if not trace_id or not question:
            continue
        identity = compact_identity(extract_trace_identity(record))
        rows.append({
            "traceId": trace_id,
            "buyerIdRaw": identity.get("buyerIdRaw", ""),
            "buyerId": identity.get("buyerId", ""),
            "timestamp": record.get("time", 0),
            "time": format_trace_time(record.get("time", 0)),
            "question": question[:500],
            "answer": answer[:1000],
            "topic": str(record.get("topicName", "") or "")[:50],
            "identity": identity,
        })

    sessions = group_conversation_sessions(rows, shop_id)
    index = {}
    for session in sessions:
        summary = {
            "id": session["id"],
            "turnCount": session["turnCount"],
            "isMultiTurn": session["isMultiTurn"],
            "traceIds": session["traceIds"],
            "startTime": session["startTime"],
            "endTime": session["endTime"],
            "buyerId": session.get("buyerId", ""),
        }
        for trace_id in session["traceIds"]:
            index[trace_id] = summary
    return sessions, index


def conversation_summary(sessions):
    total = len(sessions)
    multi_turn = [session for session in sessions if session.get("isMultiTurn")]
    return {
        "totalSessions": total,
        "multiTurnSessions": len(multi_turn),
        "multiTurnTraceCount": sum(session.get("turnCount", 0) for session in multi_turn),
        "singleTurnSessions": total - len(multi_turn),
    }


def public_conversation_session(session):
    records = []
    topics = []
    for row in session.get("records", []):
        topic = str(row.get("topic", "") or "")
        if topic and topic not in topics:
            topics.append(topic)
        records.append({
            "id": row.get("traceId", ""),
            "time": row.get("time") or format_trace_time(row.get("timestamp", 0)),
            "question": row.get("question", ""),
            "answer": row.get("answer", ""),
            "topic": topic,
            "identity": row.get("identity", {}),
        })
    return {
        "id": session.get("id", ""),
        "buyer": session.get("buyerId", ""),
        "turnCount": session.get("turnCount", 0),
        "isMultiTurn": bool(session.get("isMultiTurn")),
        "startTime": format_trace_time(session.get("startTime", 0)),
        "endTime": format_trace_time(session.get("endTime", 0)),
        "topics": topics,
        "records": records,
    }


def build_manual_queue(issue_workbench):
    """Create mutually exclusive, action-oriented work queues from issue clusters."""
    resolved_statuses = {"已补知识", "已优化话术", "复查通过", "忽略"}
    bucket_order = {"立即处理": 0, "需要复核": 1, "批量整改": 2}
    queue = []
    for issue in issue_workbench or []:
        if not isinstance(issue, dict) or issue.get("status") in resolved_statuses:
            continue
        rule_ids = {str(hit.get("ruleId", "")) for hit in issue.get("ruleHits", []) if isinstance(hit, dict)}
        confidence = int(issue.get("confidence", 0) or 0)
        feedback = issue.get("feedback") if isinstance(issue.get("feedback"), dict) else {}
        if (
            (issue.get("issueType") == "高风险" and confidence >= 85)
            or (issue.get("priority") == "高" and "QA-PROMISE-001" in rule_ids)
        ):
            bucket, reason = "立即处理", "高风险且证据充分"
        elif feedback.get("verdict") == "needs_review" or confidence < 70:
            bucket, reason = "需要复核", "证据不足或已标记人工复核"
        elif issue.get("priority") in {"高", "中"} and int(issue.get("count", 0) or 0) >= 3:
            bucket, reason = "批量整改", "同类问题重复出现，适合批量处理"
        else:
            continue
        queue.append({
            "issueId": issue.get("id", ""),
            "bucket": bucket,
            "reason": reason,
            "title": issue.get("standardQuestion") or issue.get("topic") or "待处理问题",
            "issueType": issue.get("issueType", ""),
            "priority": issue.get("priority", ""),
            "confidence": confidence,
            "count": int(issue.get("count", 0) or 0),
            "suggestedAction": issue.get("suggestedAction", ""),
            "status": issue.get("status", "待处理"),
        })
    return sorted(
        queue,
        key=lambda item: (bucket_order[item["bucket"]], -item["confidence"], -item["count"], item["issueId"]),
    )
    raw = json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def serialize_ai_task(task, include_result=True):
    if not task:
        return None
    result = {
        key: task.get(key)
        for key in (
            "taskId", "shopId", "status", "progress", "stage", "issueOffset",
            "issueLimit", "total", "generated", "error", "createdAt", "updatedAt",
            "cancelRequested",
        )
    }
    if include_result:
        result["result"] = task.get("result")
    return result


def run_ai_llm_analysis(system_prompt, user_text, cfg, local_analysis, batch_info, issue_limit):
    """Perform one analysis with bounded transient retries and one JSON repair retry."""
    parse_error = None
    llm_text = ""
    request_cfg = dict(cfg)
    current_prompt = user_text
    repair_used = False
    token_retry_plan = [8192, 12000]

    for attempt in range(1, 4):
        llm_text, err = _call_llm(system_prompt, current_prompt, request_cfg)
        if err:
            if attempt < 3 and is_retryable_ai_error(err):
                time.sleep(2 ** (attempt - 1))
                continue
            return None, format_llm_error(err, attempt=attempt, issue_limit=issue_limit)
        try:
            return normalize_ai_result(parse_ai_response(llm_text), local_analysis, batch_info), None
        except ValueError as exc:
            parse_error = exc
            if repair_used:
                break
            repair_used = True
            if is_probably_truncated_ai_json_error(exc):
                request_cfg["maxTokens"] = max(int(request_cfg.get("maxTokens", 0) or 0), token_retry_plan[0])
            current_prompt = (
                user_text
                + "\n\nThe previous response was not valid complete JSON. Return only one compact, closed JSON object."
                + " Do not use Markdown or explanations, and keep arrays concise."
            )
            time.sleep(0.3)

    raw_preview = str(llm_text or "")[:800]
    token_hint = (
        "后端已自动进行一次 JSON 修复重试；仍失败时建议把每批调到 5-10 条，"
        "或把 Max Tokens 调到 12000 后重试。"
    )
    return None, f"AI 返回格式解析失败: {parse_error}\n建议：{token_hint}\n原始内容: {raw_preview}"


def _cancel_ai_task_if_requested(task_id):
    task = SHARED_STORE.get_ai_task(task_id)
    if task and (task.get("cancelRequested") or task.get("status") == "cancelled"):
        SHARED_STORE.update_ai_task(task_id, status="cancelled", stage="已停止", progress=task.get("progress", 0))
        return True
    return False


def run_ai_analysis_task(task_id, shop_id, username, cfg, issue_offset, issue_limit):
    """Run the slow part outside the request thread while persisting visible state transitions."""
    try:
        with app.app_context():
            SHARED_STORE.update_ai_task(task_id, status="running", progress=5, stage="准备分析")
            if _cancel_ai_task_if_requested(task_id):
                return

            traces = SHARED_STORE.load_traces(shop_id)
            if not traces:
                SHARED_STORE.update_ai_task(task_id, status="failed", progress=100, stage="分析失败", error="没有可分析的数据，请先抓取数据")
                return

            local_analysis = SHARED_STORE.load_analysis(shop_id)
            if not local_analysis or "storeDiagnosis" not in local_analysis or "topicInsights" not in local_analysis:
                SHARED_STORE.update_ai_task(task_id, progress=25, stage="读取本地诊断")
                local_analysis = run_analysis(traces, shop_id)
                SHARED_STORE.save_analysis(shop_id, local_analysis)
            else:
                SHARED_STORE.update_ai_task(task_id, progress=35, stage="读取本地诊断")

            if _cancel_ai_task_if_requested(task_id):
                return

            cache_key = build_ai_cache_key(shop_id, traces, local_analysis, cfg, issue_offset, issue_limit)
            SHARED_STORE.update_ai_task(task_id, cacheKey=cache_key, total=len(local_analysis.get("issueWorkbench", [])))
            cached = SHARED_STORE.find_cached_ai_result(cache_key)
            if cached and cached.get("taskId") != task_id and cached.get("result"):
                SHARED_STORE.update_ai_task(
                    task_id,
                    status="succeeded",
                    progress=100,
                    stage="已使用缓存结果",
                    generated=len(cached.get("result", {}).get("issueOptimizationWorkbench", [])),
                    result=cached["result"],
                )
                return

            prompt_bundle = build_ai_analysis_prompt_bundle(
                traces,
                shop_id,
                local_analysis,
                issue_offset=issue_offset,
                issue_limit=issue_limit,
            )
            system_prompt = prompt_bundle["systemPrompt"]
            user_text = prompt_bundle["userText"]
            batch_info = prompt_bundle["analysisContext"].get("issueBatch", {})
            SHARED_STORE.update_ai_task(
                task_id,
                progress=62,
                stage="调用模型",
                total=batch_info.get("total", 0),
            )
            if _cancel_ai_task_if_requested(task_id):
                return

            result, error = run_ai_llm_analysis(
                system_prompt,
                user_text,
                cfg,
                local_analysis,
                batch_info,
                issue_limit,
            )
            if _cancel_ai_task_if_requested(task_id):
                return
            if error:
                SHARED_STORE.update_ai_task(task_id, status="failed", progress=100, stage="分析失败", error=error)
                return

            generated = len(result.get("issueOptimizationWorkbench", [])) if isinstance(result, dict) else 0
            SHARED_STORE.update_ai_task(
                task_id,
                status="succeeded",
                progress=100,
                stage="分析完成",
                generated=generated,
                total=batch_info.get("total", 0),
                result=result,
            )
    except Exception:
        app.logger.exception("AI task failed: %s", task_id)
        SHARED_STORE.update_ai_task(
            task_id,
            status="failed",
            progress=100,
            stage="分析失败",
            error="AI 分析任务执行异常，请稍后重试",
        )


@app.route("/api/ai/analyze", methods=["POST"])
@require_auth
def ai_analyze():
    data = request.get_json() or {}
    shop_id = str(data.get("shopId", "")).strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"})
    shop_file_token(shop_id)

    traces = SHARED_STORE.load_traces(shop_id)
    if not traces:
        return jsonify({"success": False, "error": "没有可分析的数据，请先抓取数据"})

    username = current_ai_config_username()
    cfg = load_ai_config(username)
    p = _resolve_config(cfg)
    if not p["apiKey"] or not p["baseUrl"] or not p["model"]:
        return jsonify({"success": False, "error": "请先在 API 配置中设置模型参数"})

    issue_offset = clamp_ai_issue_offset(data.get("issueOffset", data.get("offset", 0)))
    issue_limit = clamp_ai_issue_batch_limit(data.get("issueLimit", data.get("limit", AI_DEFAULT_ISSUE_BATCH_SIZE)))
    local_analysis = SHARED_STORE.load_analysis(shop_id)
    cache_key = build_ai_cache_key(shop_id, traces, local_analysis, p, issue_offset, issue_limit)
    cached = SHARED_STORE.find_cached_ai_result(cache_key)
    if cached and cached.get("result"):
        task_id = SHARED_STORE.create_ai_task(
            shop_id,
            username=username,
            cache_key=cache_key,
            issue_offset=issue_offset,
            issue_limit=issue_limit,
            status="succeeded",
            progress=100,
            stage="已使用缓存结果",
            total=len(local_analysis.get("issueWorkbench", [])) if isinstance(local_analysis, dict) else 0,
            generated=len(cached["result"].get("issueOptimizationWorkbench", [])),
            result=cached["result"],
        )
        return jsonify({"success": True, "taskId": task_id, "cached": True})

    task_id = SHARED_STORE.create_ai_task(
        shop_id,
        username=username,
        cache_key=cache_key,
        issue_offset=issue_offset,
        issue_limit=issue_limit,
        stage="等待任务",
        total=len(local_analysis.get("issueWorkbench", [])) if isinstance(local_analysis, dict) else 0,
    )
    thread = threading.Thread(
        target=run_ai_analysis_task,
        args=(task_id, shop_id, username, dict(p), issue_offset, issue_limit),
        name=f"ai-analysis-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return jsonify({"success": True, "taskId": task_id, "cached": False})


@app.route("/api/ai/tasks/<task_id>")
@require_auth
def ai_task_status(task_id):
    task = SHARED_STORE.get_ai_task(task_id)
    if not task or task.get("username") != current_ai_config_username():
        return jsonify({"success": False, "error": "任务不存在"}), 404
    return jsonify({"success": True, "task": serialize_ai_task(task)})


@app.route("/api/ai/tasks/<task_id>/cancel", methods=["POST"])
@require_auth
def ai_task_cancel(task_id):
    task = SHARED_STORE.get_ai_task(task_id)
    if not task or task.get("username") != current_ai_config_username():
        return jsonify({"success": False, "error": "任务不存在"}), 404
    if task.get("status") in {"succeeded", "failed", "cancelled"}:
        return jsonify({"success": True, "task": serialize_ai_task(task)})
    SHARED_STORE.request_ai_task_cancel(task_id)
    task = SHARED_STORE.get_ai_task(task_id)
    return jsonify({"success": True, "task": serialize_ai_task(task)})


@app.route("/api/ai/tasks/latest")
@require_auth
def ai_task_latest():
    shop_id = request.args.get("shopId", "").strip()
    if not shop_id:
        return jsonify({"success": False, "error": "缺少店铺 ID"}), 400
    task = SHARED_STORE.latest_ai_task(shop_id, current_ai_config_username())
    return jsonify({"success": True, "task": serialize_ai_task(task)})


if __name__ == "__main__":
    warn_if_default_secret(app.secret_key)
    host = os.environ.get("QA_HOST", "127.0.0.1")
    port = int(os.environ.get("QA_PORT", "5000"))
    print("=" * 50)
    print("  QA Agent Trace Analyzer")
    print(f"  http://{host}:{port}")
    print("=" * 50)
    app.run(host=host, port=port, debug=False)
