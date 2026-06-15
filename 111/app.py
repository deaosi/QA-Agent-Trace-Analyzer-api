"""QA Agent Trace Analyzer."""

import json
import os
import re
import secrets
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import wraps

import requests
from flask import Flask, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import jieba
except ImportError:
    jieba = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("QA_DATA_DIR", BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
COOKIE_FILE = os.path.join(DATA_DIR, ".cookies.json")
SHOPS_FILE = os.path.join(DATA_DIR, ".shops.json")
ISSUE_STATUS_FILE = os.path.join(DATA_DIR, ".issue_status.json")
USERS_FILE = os.path.join(DATA_DIR, ".users.json")
ACCESS_PASSWORD = os.environ.get("QA_ACCESS_PASSWORD", "")
ADMIN_USERNAME = os.environ.get("QA_ADMIN_USERNAME", "shuxing666")
ADMIN_PASSWORD = os.environ.get("QA_ADMIN_PASSWORD", ACCESS_PASSWORD or "asdfghjkl")

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


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA Agent Trace Analyzer</title>
<style>
:root{
  --bg:#f3f6fb;--panel:#fff;--line:#dfe5ee;--line-strong:#cbd5e1;
  --text:#172033;--muted:#64748b;--primary:#1677ff;--primary-dark:#0958d9;
  --danger:#d92d20;--success:#039855;--warn:#dc6803;--indigo:#4f46e5;
  --shadow:0 1px 2px rgba(16,24,40,.04);--shadow-lg:0 12px 30px rgba(15,23,42,.08)
}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;font-size:13px}
.topbar{height:60px;background:#101828;color:white;display:flex;align-items:center;justify-content:space-between;padding:0 24px;border-bottom:1px solid #1f2937;box-shadow:0 8px 24px rgba(16,24,40,.16)}
.brand{display:flex;align-items:center;gap:12px;font-size:17px;font-weight:760}.brand-mark{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#1677ff,#4f46e5);display:grid;place-items:center;font-weight:800;box-shadow:0 8px 18px rgba(22,119,255,.32)}
.top-meta{color:#cbd5e1;font-size:12px}.page{max-width:1320px;margin:0 auto;padding:18px 20px 28px}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:15px 16px;box-shadow:var(--shadow);position:relative;overflow:hidden}.metric::before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:#1677ff}.metric:nth-child(3)::before{background:#039855}.metric:nth-child(4)::before{background:#4f46e5}
.metric-value{font-size:27px;line-height:1;font-weight:780;color:#0f172a}.metric-label{font-size:12px;color:var(--muted);margin-top:8px}.metric:nth-child(2) .metric-value{color:#1677ff}.metric:nth-child(3) .metric-value{color:#039855}
.layout{display:grid;grid-template-columns:360px 1fr;gap:14px;align-items:start}.stack{display:flex;flex-direction:column;gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);overflow:hidden}.card-header{height:46px;padding:0 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:linear-gradient(180deg,#fff,#fbfcfe);font-weight:750}
.card-body{padding:14px 16px}.section-title{font-size:12px;color:#475467;font-weight:700;margin:2px 0 10px}.divider{height:1px;background:var(--line);margin:14px 0}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.filter-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px 12px}.action-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.field{display:flex;flex-direction:column;gap:5px;min-width:0}.field label{font-size:12px;color:#475467;font-weight:650}
input,select,textarea{width:100%;border:1px solid var(--line-strong);border-radius:6px;background:white;color:var(--text);font:inherit;font-size:13px;outline:none;transition:border-color .15s,box-shadow .15s}
input,select{height:32px;padding:0 9px}textarea{min-height:76px;resize:vertical;padding:8px 9px;font-family:Consolas,"SFMono-Regular",monospace;line-height:1.5}
input:focus,select:focus,textarea:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(22,119,255,.12)}
.btn{height:32px;border:1px solid var(--line-strong);border-radius:6px;padding:0 12px;background:#fff;color:#1f2937;font-weight:650;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap}
.btn:hover{border-color:var(--primary);color:var(--primary)}.btn-primary{background:var(--primary);border-color:var(--primary);color:white}.btn-primary:hover{background:var(--primary-dark);border-color:var(--primary-dark);color:white}
.btn-danger{background:#fff;border-color:#fda29b;color:var(--danger)}.btn-danger:hover{background:#fff5f5;border-color:var(--danger);color:var(--danger)}.btn-sm{height:28px;padding:0 10px;font-size:12px}
.status{font-size:12px;color:var(--muted)}.pill{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;background:#eef4ff;color:#175cd3;font-size:12px;font-weight:650}
.checkbar{display:flex;flex-wrap:wrap;gap:8px 14px;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:#f8fafc}.checkbar label{display:inline-flex;align-items:center;gap:6px;color:#344054}
input[type=checkbox]{width:14px;height:14px;accent-color:var(--primary)}.progress{display:none;margin-top:14px}.progress-track{height:7px;background:#e4e7ec;border-radius:999px;overflow:hidden}.progress-bar{height:100%;width:0;background:var(--primary);transition:width .2s}.progress-meta{display:flex;justify-content:space-between;margin-top:5px;font-size:12px;color:var(--muted)}
.log{display:none;white-space:pre-wrap;margin-top:12px;max-height:220px;overflow:auto;background:#0f172a;color:#d1d5db;padding:12px;border-radius:6px;font:12px/1.6 Consolas,monospace}
.result-card{margin-top:14px}.result-title{font-size:14px;font-weight:750;margin:0 0 8px}.empty{padding:28px;text-align:center;color:var(--muted);border:1px dashed var(--line-strong);border-radius:8px;background:#fbfcfe}
.diagnosis-grid{display:grid;grid-template-columns:1.1fr repeat(4,1fr);gap:10px;margin-bottom:14px}.diagnosis-main{background:linear-gradient(135deg,#0f172a,#1e3a8a);color:white;border-radius:10px;padding:16px;box-shadow:var(--shadow-lg)}.score{font-size:34px;font-weight:820;line-height:1}.score-label{color:#cbd5e1;margin-top:6px}.diagnosis-card{border:1px solid var(--line);border-radius:10px;padding:13px;background:#fff}.diagnosis-value{font-size:22px;font-weight:780}.diagnosis-label{color:var(--muted);font-size:12px;margin-top:4px}.diagnosis-summary{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}.summary-box{border:1px solid var(--line);border-radius:10px;background:#fbfcfe;padding:12px}.summary-box b{display:block;margin-bottom:6px}.summary-box ul{margin:0;padding-left:18px;color:#475467;line-height:1.7}
.task-list{display:grid;gap:8px}.task-item{display:flex;align-items:center;justify-content:space-between;gap:10px;border:1px solid var(--line);border-radius:8px;background:#fff;padding:9px 10px}.task-type{display:inline-flex;height:22px;align-items:center;padding:0 7px;border-radius:999px;background:#eef4ff;color:#175cd3;font-size:12px;font-weight:750}.value-score{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;background:#f5f3ff;color:#5b21b6;font-size:12px;font-weight:750}
.topic-dashboard{margin:14px 0 18px}.topic-kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px}.topic-kpi{border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px}.topic-kpi strong{font-size:22px;display:block}.topic-kpi span{font-size:12px;color:var(--muted)}.topic-layout{display:grid;grid-template-columns:1.2fr .8fr;gap:10px}.topic-panel{border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px}.topic-panel-title{font-weight:780;margin-bottom:10px}.rank-row{display:grid;grid-template-columns:110px 1fr 72px;gap:10px;align-items:center;margin:10px 0}.rank-name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.rank-track{height:9px;background:#eef2f7;border-radius:999px;overflow:hidden}.rank-fill{height:100%;background:linear-gradient(90deg,#1677ff,#4f46e5);border-radius:999px}.rank-meta{text-align:right;color:#475467;font-size:12px}.topic-heat{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.heat-card{border:1px solid var(--line);border-radius:8px;padding:10px;background:#fbfcfe}.heat-title{font-weight:750}.heat-sub{color:var(--muted);font-size:12px;margin-top:4px}.heat-risk{margin-top:8px;font-weight:800}.keyword-cloud{display:flex;gap:6px;flex-wrap:wrap}.keyword{display:inline-flex;align-items:center;height:24px;padding:0 9px;border-radius:999px;background:#f2f4f7;color:#344054;font-weight:650;font-size:12px}
.identity-dashboard{margin:0 0 18px}.identity-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}.identity-kpi{border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px}.identity-kpi strong{font-size:22px;display:block}.identity-kpi span{font-size:12px;color:var(--muted)}.identity-layout{display:grid;grid-template-columns:1fr 1fr;gap:10px}.identity-panel{border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px}.identity-list{display:grid;gap:8px}.identity-row{display:grid;grid-template-columns:minmax(0,1fr) 70px;gap:10px;align-items:center;border:1px solid var(--line);border-radius:8px;background:#fbfcfe;padding:9px}.identity-name{font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.identity-meta{font-size:12px;color:var(--muted);margin-top:3px;word-break:break-all}.identity-count{text-align:right;font-weight:800;color:#175cd3}.trace-meta{display:flex;gap:5px;flex-wrap:wrap;margin-top:6px}.trace-chip{display:inline-flex;max-width:220px;height:22px;align-items:center;padding:0 7px;border-radius:6px;background:#f2f4f7;color:#344054;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.knowledge-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0 18px}.knowledge-card{border:1px solid var(--line);border-radius:10px;background:#fff;padding:12px;box-shadow:var(--shadow)}.knowledge-title{font-weight:780;color:#101828;margin-bottom:8px}.knowledge-line{font-size:12px;color:#475467;line-height:1.55;margin-top:6px}.kw{display:inline-flex;align-items:center;height:22px;padding:0 7px;border-radius:999px;background:#eef4ff;color:#175cd3;margin:3px 4px 0 0;font-size:12px;font-weight:650}
table{width:100%;border-collapse:collapse;margin-top:8px}th,td{padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px;text-align:left;vertical-align:top}th{color:#475467;background:#f8fafc;font-weight:700}
.topic-bar{height:6px;background:#e4e7ec;border-radius:999px;overflow:hidden;margin-top:5px}.topic-fill{height:100%;background:var(--primary)}.qa-q{font-weight:650;color:#0958d9}.qa-a{color:#334155;white-space:pre-wrap}.qa-a,.issue-suggestion{max-height:92px;overflow:auto;padding-right:4px}
.issue-panel{margin-bottom:18px}.issue-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:10px}.issue-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.issue-tools select{width:120px}.badge{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;font-size:12px;font-weight:700}.badge-high{background:#fef3f2;color:#b42318}.badge-mid{background:#fffaeb;color:#b54708}.badge-low{background:#ecfdf3;color:#027a48}.issue-type{background:#eef4ff;color:#175cd3}.issue-status{background:#f2f4f7;color:#344054}.risk-high{background:#fef3f2;color:#b42318}.risk-mid{background:#fff7ed;color:#c2410c}.risk-low{background:#ecfdf3;color:#027a48}.issue-question{font-weight:750;color:#101828}.issue-meta{margin-top:4px;color:var(--muted);font-size:12px}.issue-reason{color:#7a271a;font-size:12px;margin-top:5px}.issue-suggestion{color:#344054;font-size:12px;line-height:1.5;margin-top:5px}.mini-actions{display:flex;gap:6px;flex-wrap:wrap}.mini-actions .btn{height:26px;padding:0 8px;font-size:12px}
.qa-panel{margin-top:18px}.qa-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px}.qa-count{font-size:12px;color:var(--muted);font-weight:500}.qa-topic{display:inline-flex;align-items:center;height:22px;padding:0 8px;border-radius:999px;background:#f2f4f7;color:#344054;font-size:12px;font-weight:650}.pager{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:12px;flex-wrap:wrap}.pager-total{color:#475467;font-size:12px;margin-right:auto}.pager-pages{display:grid;grid-template-columns:repeat(7,32px);gap:4px}.page-btn{height:30px;min-width:32px;border:1px solid var(--line-strong);border-radius:6px;background:#fff;color:#344054;font-weight:650;cursor:pointer}.page-btn:hover{border-color:var(--primary);color:var(--primary)}.page-btn.active{background:var(--primary);border-color:var(--primary);color:#fff}.page-btn:disabled{cursor:not-allowed;opacity:.45;border-color:var(--line);color:var(--muted)}.page-ellipsis{height:30px;display:grid;place-items:center;color:var(--muted);font-weight:700}.pager-jump{display:flex;align-items:center;gap:6px;color:#475467;font-size:12px}.pager-jump input{width:48px;height:30px;text-align:center}.pager-size{height:30px;width:auto;min-width:92px}
.cookie-output{min-height:92px}.muted-line{color:var(--muted);font-size:12px;margin-top:6px}.toolbar{display:flex;justify-content:space-between;gap:10px;align-items:center;margin-top:12px}
@media(max-width:1020px){.layout{grid-template-columns:1fr}.filter-grid{grid-template-columns:repeat(2,minmax(150px,1fr))}.diagnosis-grid,.diagnosis-summary,.knowledge-grid,.topic-layout,.identity-layout{grid-template-columns:1fr 1fr}.topic-kpis,.identity-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.metrics,.filter-grid,.grid-2,.diagnosis-grid,.diagnosis-summary,.knowledge-grid,.topic-layout,.topic-kpis,.topic-heat,.identity-grid,.identity-layout{grid-template-columns:1fr}.page{padding:12px}.topbar{padding:0 14px}.top-meta{display:none}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand"><div class="brand-mark">QA</div><span>店铺智能体训练工作台</span></div>
  <div class="top-meta"><span id="adminLink" style="display:none"><a href="/admin/users" style="color:#4ade80;text-decoration:none;margin-right:12px">账号后台</a></span><a href="/logout" style="color:#f87171;text-decoration:none;margin-left:12px">退出登录</a></div>
</header>
<main class="page">
  <section class="metrics">
    <div class="metric"><div class="metric-value" id="m1">0</div><div class="metric-label">已抓取 Trace</div></div>
    <div class="metric"><div class="metric-value" id="m2">0</div><div class="metric-label">识别话题</div></div>
    <div class="metric"><div class="metric-value" id="m3">0</div><div class="metric-label">待补知识卡片</div></div>
    <div class="metric"><div class="metric-value" id="m4">-</div><div class="metric-label">采纳风险/当前店铺</div></div>
  </section>

  <section class="layout">
    <aside class="stack">
      <section class="card">
        <div class="card-header"><span>Cookie 拼接助手</span><span id="shopStatus" class="pill">未检测</span></div>
        <div class="card-body">
          <div class="field">
            <label>Cookie 表格</label>
            <textarea id="cookieRaw" placeholder="Name&#9;Value&#9;Domain"></textarea>
          </div>
          <div class="toolbar">
            <div class="action-row">
              <button class="btn btn-primary btn-sm" onclick="buildCookie()">拼接并复制</button>
              <button class="btn btn-sm" onclick="clearCookieRaw()">清空</button>
            </div>
            <span id="cookieMsg" class="status"></span>
          </div>
          <div class="divider"></div>
          <div class="field">
            <label>Cookie 字符串</label>
            <textarea id="cookie" class="cookie-output" placeholder="name=value; name2=value2"></textarea>
          </div>
          <div class="toolbar">
            <div class="action-row">
              <button class="btn btn-primary btn-sm" onclick="saveCookie()">保存 Cookie</button>
              <button class="btn btn-sm" onclick="copyCookie()">复制</button>
            </div>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-header">店铺管理</div>
        <div class="card-body">
          <div class="field">
            <label>已保存店铺</label>
            <select id="shopSelect" onchange="switchShop()"><option value="">-- 选择店铺 --</option></select>
          </div>
          <div class="grid-2" style="margin-top:10px">
            <div class="field"><label>店铺 ID</label><input id="shopId" placeholder="thirdShopId"></div>
            <div class="field"><label>探测页大小</label><input id="probePageSize" value="1" type="number" min="1"></div>
          </div>
          <div class="action-row" style="margin-top:10px">
            <button class="btn btn-primary btn-sm" onclick="probeCurrentShop()">探测店铺</button>
            <button class="btn btn-danger btn-sm" onclick="delShop()">删除</button>
          </div>
          <div class="divider"></div>
          <div class="field">
            <label>批量店铺 ID</label>
            <textarea id="batchIds" placeholder="2605317072510000690"></textarea>
          </div>
          <div class="toolbar">
            <button class="btn btn-sm" onclick="batchProbe()">批量探测</button>
            <span id="batchStatus" class="status"></span>
          </div>
        </div>
      </section>
    </aside>

    <section class="stack">
      <section class="card">
        <div class="card-header">
          <span>后台筛选</span>
          <span class="status">请求参数与 Agent Trace 列表对齐</span>
        </div>
        <div class="card-body">
          <div class="filter-grid">
            <div class="field"><label>开始时间</label><input type="datetime-local" id="beginTime"></div>
            <div class="field"><label>结束时间</label><input type="datetime-local" id="endTime"></div>
            <div class="field"><label>处理状态</label><select id="fReviewStatus"><option value="" selected>全部</option><option value="0">待审核</option><option value="1">已审核</option><option value="2">已处理</option></select></div>
            <div class="field"><label>标记状态</label><select id="fIfLabel"><option value="" selected>全部</option><option value="0">未标记</option><option value="1">已标记</option><option value="2">使用淘宝应用接待</option></select></div>
            <div class="field"><label>对话类型</label><select id="fType"><option value="">全部</option><option value="CONSULT_PRODUCT">商品咨询</option><option value="CONSULT_REPLY">咨询回复</option><option value="AFTER_SALE">售后</option><option value="COMPLAINT">投诉</option></select></div>
            <div class="field"><label>业务线</label><select id="fBusi"><option value="" selected>全部</option><option value="RECEPTION">接待</option><option value="AFTER_SALE">售后</option></select></div>
            <div class="field"><label>每页条数</label><input id="pageSize" value="50" type="number" min="1"></div>
            <div class="field"><label>最大页数</label><input id="maxPages" value="40" type="number" min="1"></div>
          </div>
          <div class="field" style="margin-top:12px">
            <label>高级：粘贴电商后台 Request Payload（可选，用于完全对齐官方筛选）</label>
            <textarea id="rawPayload" placeholder='{"thirdShopId":"...","pageIndex":1,"pageSize":50,...}'></textarea>
          </div>
          <div class="section-title" style="margin-top:14px">发送状态</div>
          <div class="checkbar">
            <label><input type="checkbox" class="sendTypeCb" value="0" checked> 未发送</label>
            <label><input type="checkbox" class="sendTypeCb" value="1" checked> 自动发送</label>
            <label><input type="checkbox" class="sendTypeCb" value="2" checked> 侧边栏点击</label>
            <label><input type="checkbox" class="sendTypeCb" value="3"> 编辑后发送</label>
            <label><input type="checkbox" id="overwriteCb"> 覆盖已有数据</label>
          </div>
          <div class="toolbar">
            <div class="action-row">
              <button class="btn btn-primary" id="goBtn" onclick="go()">抓取并分析</button>
              <button class="btn btn-danger" id="stopBtn" style="display:none" onclick="stop()">停止</button>
              <button class="btn" onclick="reanalyzeCurrent()">重新分析已有数据</button>
              <button class="btn" onclick="refresh()">刷新概览</button>
            </div>
            <span id="filterSummary" class="status"></span>
          </div>
          <div class="progress" id="progressWrap">
            <div class="progress-track"><div class="progress-bar" id="progressBar"></div></div>
            <div class="progress-meta"><span id="progressLabel"></span><span id="progressPct"></span></div>
          </div>
          <div class="log" id="log"></div>
        </div>
      </section>

      <section class="card result-card">
        <div class="card-header">训练优化看板</div>
        <div class="card-body" id="resultArea"><div id="resultBody"><div class="empty">暂无结果</div></div></div>
      </section>
    </section>
  </section>
</main>
<script>
let currentShop = "";
let abortCtrl = null;
let lastAnalysis = null;
let qaPage = 1;
let issuePage = 1;
let issueStatusFilter = "全部";
let issuePriorityFilter = "全部";
const QA_PAGE_SIZE = 20;
const ISSUE_PAGE_SIZE = 20;
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
async function get(url){ const r = await fetch(url); return r.json(); }
async function post(url,data){ const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)}); return r.json(); }
function log(msg){ const e=$("log"); e.style.display="block"; e.textContent += msg + "\n"; e.scrollTop=e.scrollHeight; }
function setProgress(pct,label){ $("progressBar").style.width=pct+"%"; $("progressPct").textContent=pct+"%"; $("progressLabel").textContent=label; }
function dateValue(hour, minute){ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}T${String(hour).padStart(2,"0")}:${String(minute).padStart(2,"0")}`; }

async function init(){
  $("beginTime").value = dateValue(0,0);
  $("endTime").value = dateValue(23,59);
  const r = await get("/api/cookie-status");
  $("shopStatus").textContent = r.hasCookie ? "Cookie 已保存" : "未配置";
  if (r.hasCookie) $("cookie").placeholder = "已保存，不需要重复填写";
  await loadShops();
  updateFilterSummary();
  // 检查是否为管理员，显示管理员入口
  try {
    const me = await get("/api/me");
    if (me.user && me.user.role === "admin") {
      const adminLink = $("adminLink");
      if (adminLink) adminLink.style.display = "inline";
    }
  } catch(e) {}
}

function parseCookieTable(raw){
  const lines = raw.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const skip = new Set(["name","名称","value","值","domain","path","expires","size","httponly","secure","samesite","priority"]);
  const pairs = [];
  for (const line of lines) {
    if (line.includes(";") && line.includes("=") && !line.includes("\t")) {
      line.split(";").map(s => s.trim()).filter(Boolean).forEach(part => {
        if (part.includes("=")) pairs.push(part);
      });
      continue;
    }
    let cols = line.split(/\t+/).map(s => s.trim()).filter(Boolean);
    if (cols.length < 2) cols = line.split(/\s{2,}/).map(s => s.trim()).filter(Boolean);
    if (cols.length < 2) continue;
    const name = cols[0];
    const value = cols[1];
    if (!name || !value || skip.has(name.toLowerCase()) || name.includes("=")) continue;
    pairs.push(`${name}=${value}`);
  }
  return Array.from(new Map(pairs.map(pair => [pair.split("=")[0], pair])).values());
}

async function buildCookie(){
  const parts = parseCookieTable($("cookieRaw").value);
  if(!parts.length){ $("cookieMsg").textContent = "未识别到 Cookie"; return; }
  const cookie = parts.join("; ");
  $("cookie").value = cookie;
  $("cookieMsg").textContent = `已拼接 ${parts.length} 项`;
  try { await navigator.clipboard.writeText(cookie); $("cookieMsg").textContent += "，已复制"; } catch(e) {}
}

function clearCookieRaw(){ $("cookieRaw").value = ""; $("cookieMsg").textContent = ""; }
async function copyCookie(){
  const cookie = $("cookie").value.trim();
  if(!cookie){ $("cookieMsg").textContent = "Cookie 为空"; return; }
  try { await navigator.clipboard.writeText(cookie); $("cookieMsg").textContent = "已复制"; }
  catch(e){ $("cookieMsg").textContent = "复制失败"; }
}

async function saveCookie(){
  const cookie = $("cookie").value.trim();
  if(!cookie){ $("cookieMsg").textContent = "请先粘贴 Cookie"; return; }
  const r = await post("/api/save-cookie",{cookie});
  $("cookieMsg").textContent = r.success ? "已保存" : (r.error || "保存失败");
  await init();
}

async function loadShops(){
  const r = await get("/api/shops");
  const sel = $("shopSelect");
  sel.innerHTML = '<option value="">-- 选择店铺 --</option>';
  (r.shops || []).forEach(s => {
    sel.innerHTML += `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name || s.id)}</option>`;
  });
}

async function switchShop(){
  const sid = $("shopSelect").value;
  if(!sid) return;
  currentShop = sid;
  $("shopId").value = sid;
  const selected = $("shopSelect").selectedOptions[0];
  $("m4").textContent = selected ? selected.textContent : sid;
  await post("/api/set-shop",{shopId:sid});
  await refresh();
  await loadExistingAnalysis();
}

async function probeCurrentShop(){
  const sid = $("shopId").value.trim();
  if(!sid) return alert("请输入店铺 ID");
  $("batchStatus").textContent = "探测中...";
  const r = await post("/api/probe-shop",{shopId:sid, cookie:$("cookie").value.trim()});
  $("batchStatus").textContent = r.success ? `${r.shopName || sid}，共 ${r.total || 0} 条` : (r.error || "探测失败");
  if(r.success){ currentShop = sid; $("m4").textContent = r.shopName || sid; await loadShops(); await refresh(); }
}

async function delShop(){
  const sid = $("shopSelect").value || $("shopId").value.trim();
  if(!sid) return alert("请选择或输入店铺 ID");
  if(!confirm(`删除店铺 ${sid} 的本地数据？`)) return;
  await post("/api/delete-shop",{shopId:sid});
  if(currentShop === sid) currentShop = "";
  $("shopId").value = "";
  $("resultBody").innerHTML = '<div class="empty">数据已删除</div>';
  await loadShops();
  await refresh();
}

async function batchProbe(){
  const ids = $("batchIds").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if(!ids.length) return;
  $("batchStatus").textContent = "探测中...";
  const parts = [];
  for (const id of ids) {
    const r = await post("/api/probe-shop",{shopId:id, cookie:$("cookie").value.trim()});
    parts.push(`${id}: ${r.shopName || r.name || r.error || "失败"}`);
  }
  $("batchStatus").textContent = parts.join(" | ");
  await loadShops();
}

function resetBtn(){
  $("goBtn").style.display="inline-flex";
  $("stopBtn").style.display="none";
  $("progressWrap").style.display="none";
  abortCtrl = null;
}

async function loadExistingAnalysis(){
  const sid = $("shopId").value.trim() || currentShop;
  if(!sid) return;
  const r = await get("/api/analysis?shopId="+encodeURIComponent(sid));
  if(r.success && r.data && !r.data.error){
    renderAnalysis(r.data);
  }
}

async function reanalyzeCurrent(){
  const sid = $("shopId").value.trim() || currentShop;
  if(!sid) return alert("请先选择或输入店铺 ID");
  $("resultBody").innerHTML='<div class="empty">正在重新分析已有数据...</div>';
  const a = await post("/api/analyze",{shopId:sid});
  if(!a.success || a.data?.error){
    $("resultBody").innerHTML='<div class="empty">没有可分析的数据，请先抓取数据</div>';
    return;
  }
  renderAnalysis(a.data);
  await refresh();
}

function collectFilters(){
  const filters = {};
  if($("fReviewStatus").value !== "") filters.reviewStatus = Number($("fReviewStatus").value);
  if($("fIfLabel").value !== "") filters.ifLabel = Number($("fIfLabel").value);
  if($("fType").value !== "") filters.type = $("fType").value;
  if($("fBusi").value !== "") filters.busi = $("fBusi").value;
  const sendType = Array.from(document.querySelectorAll(".sendTypeCb:checked")).map(cb => Number(cb.value));
  if(sendType.length) filters.sendType = sendType;
  return filters;
}

function updateFilterSummary(){
  const labels = [];
  ["fReviewStatus","fIfLabel","fType","fBusi"].forEach(id => {
    const el = $(id);
    const text = el.selectedOptions[0]?.textContent || "";
    if(el.value !== "") labels.push(text);
  });
  const checked = Array.from(document.querySelectorAll(".sendTypeCb:checked")).map(cb => cb.parentElement.textContent.trim());
  $("filterSummary").textContent = labels.concat(checked).join(" / ");
}
document.addEventListener("change", e => {
  if(e.target.matches("select,.sendTypeCb")) updateFilterSummary();
});

async function go(){
  const sid = $("shopId").value.trim() || currentShop;
  if(!sid) return alert("请选择或输入店铺 ID");
  currentShop = sid;
  $("shopId").value = sid;
  $("goBtn").style.display="none";
  $("stopBtn").style.display="inline-flex";
  $("progressWrap").style.display="block";
  $("log").style.display="block";
  $("log").textContent="";
  $("resultBody").innerHTML='<div class="empty">正在处理...</div>';
  setProgress(10, "正在抓取数据...");
  log(`=== 店铺: ${sid} ===`);

  const cookie = $("cookie").value.trim();
  if(cookie) await post("/api/save-cookie",{cookie});

  abortCtrl = new AbortController();
  const f = await post("/api/fetch", {
    shopId:sid,
    beginTime:$("beginTime").value,
    endTime:$("endTime").value,
    pageSize:Number($("pageSize").value || 50),
    maxPages:Number($("maxPages").value || 40),
    filters:collectFilters(),
    overwrite:$("overwriteCb").checked,
    rawPayload:$("rawPayload").value.trim(),
  });

  if(f.requestBody) log(`[REQ] ${JSON.stringify(f.requestBody)}`);
  (f.log || []).forEach(l => {
    if(l.status === "ok") {
      log(`[OK] 第${l.page}页 ${l.count}条 (共${l.total}条)`);
      if(l.ids && l.ids.length) log(`[IDS] 第${l.page}页样本ID：${l.ids.join(", ")}`);
    }
    else if(l.status === "error") log(`[ERR] ${l.msg}`);
    else log(`[EMPTY] 第${l.page}页`);
  });
  if(!f.success){ resetBtn(); return; }
  log(`>> 新增 ${f.totalFetched || 0} 条，累计 ${f.totalStored || 0} 条`);
  if(f.shopName){ $("m4").textContent = f.shopName; await loadShops(); }
  if(!f.totalStored){ $("resultBody").innerHTML='<div class="empty">没有匹配数据</div>'; resetBtn(); return; }

  setProgress(55, "正在分析话题...");
  const a = await post("/api/analyze",{shopId:sid});
  if(!a.success || a.data?.error){ log(`[ERR] ${a.data?.error || a.error || "分析失败"}`); resetBtn(); return; }
  setProgress(90, "正在生成结果...");
  renderAnalysis(a.data);
  await refresh();
  setProgress(100, "完成");
  resetBtn();
}

function renderAnalysis(d){
  lastAnalysis = d;
  qaPage = 1;
  issuePage = 1;
  $("m1").textContent = d.totalRecords || 0;
  $("m2").textContent = (d.topicDistribution || []).length;
  $("m3").textContent = d.storeDiagnosis?.pendingCount || 0;
  $("m4").textContent = `${d.storeDiagnosis?.adoptionRiskAvg || 0}%`;

  let html = renderDiagnosis(d) + renderIdentityDashboard(d) + renderTopicDashboard(d) + renderKnowledgeCards(d);
  html += '<div id="issueWorkbenchArea"></div>';
  html += '<div id="qaPageArea"></div>';
  $("resultBody").innerHTML = html;
  renderIssueWorkbench();
  renderQaPage();
}

function renderDiagnosis(d){
  const s = d.storeDiagnosis || {};
  const topics = (s.topIssueTopics || []).map(item => `${escapeHtml(item.topic)} ${item.count}`).join(" / ") || "-";
  const actions = (s.actionDistribution || []).map(item => `${escapeHtml(item.action)} ${item.count}`).join(" / ") || "-";
  let html = '<div class="diagnosis-grid">';
  html += `<div class="diagnosis-main"><div class="score">${s.healthScore ?? "-"}</div><div class="score-label">店铺智能体健康分</div><div style="margin-top:12px;color:#dbeafe;line-height:1.6">优先处理高风险、未回复和重复追问问题，可直接转成知识卡片。</div></div>`;
  html += `<div class="diagnosis-card"><div class="diagnosis-value">${s.issueCount || 0}</div><div class="diagnosis-label">问题簇</div></div>`;
  html += `<div class="diagnosis-card"><div class="diagnosis-value">${s.highPriorityCount || 0}</div><div class="diagnosis-label">高优先级</div></div>`;
  html += `<div class="diagnosis-card"><div class="diagnosis-value">${s.pendingCount || 0}</div><div class="diagnosis-label">待处理</div></div>`;
  html += `<div class="diagnosis-card"><div class="diagnosis-value">${s.reviewCount || 0}</div><div class="diagnosis-label">待复查</div></div>`;
  html += '</div>';
  html += '<div class="diagnosis-summary">';
  html += `<div class="summary-box"><b>诊断结论</b><ul>${(s.summary || []).map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>`;
  html += `<div class="summary-box"><b>今日任务清单</b><div class="task-list">${(s.todayTasks || []).map(task => `<div class="task-item"><span>${escapeHtml(task.title)}</span><span class="task-type">${escapeHtml(task.type)}</span></div>`).join("") || '<div class="knowledge-line">暂无待办任务</div>'}</div><div class="knowledge-line" style="margin-top:8px">高频问题：${topics}</div><div class="knowledge-line">建议动作：${actions}</div></div>`;
  html += '</div>';
  return html;
}

function renderTopicDashboard(d){
  const insight = d.topicInsights || {};
  const topics = insight.topics || [];
  const maxCount = Math.max(...topics.map(t => t.count || 0), 1);
  let html = '<div class="topic-dashboard">';
  html += '<div class="issue-head"><div><div class="result-title" style="margin:0">话题分析看板</div><div class="qa-count">从话题量级、风险、知识缺口和关键词四个维度定位店铺智能体问题</div></div></div>';
  html += '<div class="topic-kpis">';
  html += `<div class="topic-kpi"><strong>${insight.topicCount || 0}</strong><span>识别话题数</span></div>`;
  html += `<div class="topic-kpi"><strong>${insight.coverageRate || 0}%</strong><span>样本覆盖率</span></div>`;
  html += `<div class="topic-kpi"><strong>${insight.issueTopicCount || 0}</strong><span>存在问题的话题</span></div>`;
  html += `<div class="topic-kpi"><strong>${insight.knowledgeGapCount || 0}</strong><span>知识缺口</span></div>`;
  html += `<div class="topic-kpi"><strong>${insight.avgAdoptionRisk || 0}%</strong><span>平均采纳风险</span></div>`;
  html += '</div>';
  html += '<div class="topic-layout">';
  html += '<div class="topic-panel"><div class="topic-panel-title">话题量级与知识缺口排行</div>';
  if(!topics.length){
    html += '<div class="empty">暂无话题分析数据</div>';
  } else {
    topics.forEach(t => {
      const width = Math.max(4, Math.round((t.count || 0) / maxCount * 100));
      html += '<div class="rank-row">';
      html += `<div class="rank-name" title="${escapeHtml(t.topic)}">${escapeHtml(t.topic)}</div>`;
      html += `<div><div class="rank-track"><div class="rank-fill" style="width:${width}%"></div></div><div class="heat-sub">问题簇 ${t.issueCount || 0} · 缺口 ${t.knowledgeGapCount || 0} · 高优 ${t.highPriorityCount || 0}</div></div>`;
      html += `<div class="rank-meta">${t.count || 0} 条<br>${t.percentage || 0}%</div>`;
      html += '</div>';
    });
  }
  html += '</div>';
  html += '<div class="topic-panel"><div class="topic-panel-title">风险热区</div><div class="topic-heat">';
  (insight.riskTopics || []).slice(0,6).forEach(t => {
    html += `<div class="heat-card"><div class="heat-title">${escapeHtml(t.topic)}</div><div class="heat-sub">高优 ${t.highPriorityCount || 0} · 未解决 ${t.unresolvedCount || 0}</div><div class="heat-risk"><span class="badge ${riskClass(t.adoptionRisk)}">风险 ${t.adoptionRisk || 0}%</span></div></div>`;
  });
  if(!(insight.riskTopics || []).length) html += '<div class="empty">暂无风险热区</div>';
  html += '</div><div class="topic-panel-title" style="margin-top:14px">高频关键词</div><div class="keyword-cloud">';
  (insight.keywords || []).forEach(k => {
    html += `<span class="keyword">${escapeHtml(k.word)} ${k.count}</span>`;
  });
  html += '</div></div></div>';
  html += '<div class="topic-panel" style="margin-top:10px"><div class="topic-panel-title">知识补齐机会</div><div class="topic-heat">';
  (insight.gapTopics || []).slice(0,6).forEach(t => {
    html += `<div class="heat-card"><div class="heat-title">${escapeHtml(t.topic)}</div><div class="heat-sub">待补 ${t.knowledgeGapCount || 0} 个卡片 · 动作：${escapeHtml(t.suggestedAction || "-")}</div><div class="knowledge-line">优先覆盖该话题下未回复、弱回复和重复追问样本。</div></div>`;
  });
  if(!(insight.gapTopics || []).length) html += '<div class="empty">暂无知识缺口</div>';
  html += '</div></div></div>';
  return html;
}

function traceIdentityHtml(identity){
  identity = identity || {};
  const chips = [];
  if(identity.buyerId) chips.push(`买家ID：${identity.buyerId}`);
  if(identity.productId) chips.push(`商品ID：${identity.productId}`);
  if(identity.spuId) chips.push(`SPU：${identity.spuId}`);
  if(identity.skuId) chips.push(`SKU：${identity.skuId}`);
  if(identity.traceId) chips.push(`Trace：${identity.traceId}`);
  if(identity.productTitle) chips.push(`商品：${identity.productTitle}`);
  if(!chips.length) return '<div class="trace-meta"><span class="trace-chip">未抓到买家/商品线索</span></div>';
  return `<div class="trace-meta">${chips.map(text => `<span class="trace-chip" title="${escapeHtml(text)}">${escapeHtml(text)}</span>`).join("")}</div>`;
}

function renderIdentityList(rows, kind){
  if(!rows || !rows.length) return '<div class="empty">暂无可用线索</div>';
  return `<div class="identity-list">${rows.map(row => {
    const name = kind === "product" ? (row.productTitle || row.productId || "-") : (row.buyerId || "-");
    const meta = kind === "product"
      ? `商品ID：${row.productId || "-"}${row.skuId ? " / SKU：" + row.skuId : ""}`
      : `涉及商品 ${row.productCount || 0} 个`;
    return `<div class="identity-row"><div><div class="identity-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div><div class="identity-meta">${escapeHtml(meta)}</div></div><div class="identity-count">${row.issueCount || row.traceCount || 0}<div class="qa-count">${row.issueCount ? "问题" : "样本"}</div></div></div>`;
  }).join("")}</div>`;
}

function renderIdentityDashboard(d){
  const s = d.identityInsights || {};
  let html = '<div class="identity-dashboard">';
  html += '<div class="issue-head"><div><div class="result-title" style="margin:0">商品 / 买家线索</div><div class="qa-count">从抓取结果中提取商品ID、买家ID、SPU/SKU、Trace ID，方便定位问题商品和回查会话</div></div></div>';
  html += '<div class="identity-grid">';
  html += `<div class="identity-kpi"><strong>${s.productCount || 0}</strong><span>涉及商品数</span></div>`;
  html += `<div class="identity-kpi"><strong>${s.buyerCount || 0}</strong><span>涉及买家数</span></div>`;
  html += `<div class="identity-kpi"><strong>${s.productCoverage || 0}%</strong><span>商品线索覆盖率</span></div>`;
  html += `<div class="identity-kpi"><strong>${s.buyerCoverage || 0}%</strong><span>买家线索覆盖率</span></div>`;
  html += '</div>';
  html += '<div class="identity-layout">';
  html += `<div class="identity-panel"><div class="topic-panel-title">问题最多的商品</div>${renderIdentityList((s.issueProducts || s.topProducts || []).slice(0,6), "product")}</div>`;
  html += `<div class="identity-panel"><div class="topic-panel-title">问题最多的买家</div>${renderIdentityList((s.issueBuyers || s.topBuyers || []).slice(0,6), "buyer")}</div>`;
  html += '</div>';
  html += `<div class="knowledge-line">未抓到商品线索 ${s.missingProduct || 0} 条，未抓到买家线索 ${s.missingBuyer || 0} 条；如果这里为空，说明接口当前批次没有返回对应字段。</div>`;
  html += '</div>';
  return html;
}

function topKnowledgeIssues(d){
  return (d.issueWorkbench || [])
    .filter(issue => issue.status !== "忽略")
    .slice()
    .sort((a,b) => (b.score || 0) - (a.score || 0))
    .slice(0, 6);
}

function renderKnowledgeCards(d){
  const cards = topKnowledgeIssues(d);
  let html = '<div class="issue-head"><div><div class="result-title" style="margin:0">待补知识卡片</div><div class="qa-count">按优先级自动生成卡片草稿，可导出后完善到知识库</div></div><button class="btn btn-sm" onclick="exportKnowledgeCardsCsv()">导出知识卡片 CSV</button></div>';
  if(!cards.length) return html + '<div class="empty" style="margin-bottom:14px">暂无待补知识卡片</div>';
  html += '<div class="knowledge-grid">';
  cards.forEach(issue => {
    const draft = issue.knowledgeCardDraft || {};
    html += '<div class="knowledge-card">';
    html += `<div class="knowledge-title">${escapeHtml(draft.title || issue.standardQuestion || "-")}</div>`;
    html += `<div><span class="badge ${priorityClass(issue.priority)}">${issue.priority}</span> <span class="badge issue-type">${escapeHtml(issue.issueType || "-")}</span> <span class="badge ${riskClass(issue.adoptionRisk)}">风险 ${issue.adoptionRisk || 0}%</span></div>`;
    html += `<div class="knowledge-line"><b>标准问：</b>${escapeHtml(draft.standardQuestion || "-")}</div>`;
    html += `<div class="knowledge-line"><b>标准答案：</b>${escapeHtml(draft.standardAnswer || draft.answerOutline || "-")}</div>`;
    html += `<div class="knowledge-line"><b>转人工边界：</b>${escapeHtml(draft.manualHandoffRule || "-")}</div>`;
    html += `<div class="knowledge-line">${(draft.triggerWords || []).map(word => `<span class="kw">${escapeHtml(word)}</span>`).join("")}</div>`;
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function filteredIssues(){
  let rows = (lastAnalysis?.issueWorkbench || []).slice();
  if(issueStatusFilter !== "全部") rows = rows.filter(item => item.status === issueStatusFilter);
  if(issuePriorityFilter !== "全部") rows = rows.filter(item => item.priority === issuePriorityFilter);
  return rows;
}

function priorityClass(priority){
  if(priority === "高") return "badge-high";
  if(priority === "中") return "badge-mid";
  return "badge-low";
}

function riskClass(risk){
  if((risk || 0) >= 70) return "risk-high";
  if((risk || 0) >= 45) return "risk-mid";
  return "risk-low";
}

function pagerSlots(current, totalPages){
  if(totalPages <= 7) return Array.from({length: totalPages}, (_, i) => i + 1);
  if(current <= 4) return [1,2,3,4,5,"...",totalPages];
  if(current >= totalPages - 3) return [1,"...",totalPages-4,totalPages-3,totalPages-2,totalPages-1,totalPages];
  return [1,"...",current-1,current,current+1,"...",totalPages];
}

function renderPager(kind, current, totalPages, total, pageSize){
  const setter = kind === "issue" ? "setIssuePage" : "setQaPage";
  const jumper = kind === "issue" ? "jumpIssuePage" : "jumpQaPage";
  let html = '<div class="pager">';
  html += `<div class="pager-total">共 ${total} 条 · ${pageSize} 条/页 · 第 ${current}/${totalPages} 页</div>`;
  html += `<button class="page-btn" onclick="${setter}(${current - 1})" ${current <= 1 ? "disabled" : ""}>‹</button>`;
  html += '<div class="pager-pages">';
  pagerSlots(current, totalPages).forEach(slot => {
    if(slot === "...") html += '<span class="page-ellipsis">…</span>';
    else html += `<button class="page-btn ${slot === current ? "active" : ""}" onclick="${setter}(${slot})">${slot}</button>`;
  });
  html += '</div>';
  html += `<button class="page-btn" onclick="${setter}(${current + 1})" ${current >= totalPages ? "disabled" : ""}>›</button>`;
  html += `<div class="pager-jump"><span>跳至</span><input id="${kind}JumpInput" type="number" min="1" max="${totalPages}" value="${current}" onkeydown="if(event.key==='Enter') ${jumper}()"><span>页</span><button class="btn btn-sm" onclick="${jumper}()">确定</button></div>`;
  html += `<select class="pager-size" disabled><option>${pageSize} 条/页</option></select>`;
  html += '</div>';
  return html;
}

function keepPagerPosition(areaId, renderFn){
  const area = $(areaId);
  const pager = area ? area.querySelector(".pager") : null;
  const beforeTop = pager ? pager.getBoundingClientRect().top : null;
  renderFn();
  if(beforeTop === null) return;
  requestAnimationFrame(() => {
    const nextArea = $(areaId);
    const nextPager = nextArea ? nextArea.querySelector(".pager") : null;
    if(!nextPager) return;
    const afterTop = nextPager.getBoundingClientRect().top;
    window.scrollBy({top: afterTop - beforeTop, left: 0, behavior: "auto"});
  });
}

function renderIssueWorkbench(){
  if(!lastAnalysis || !$("issueWorkbenchArea")) return;
  const rows = filteredIssues();
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / ISSUE_PAGE_SIZE));
  issuePage = Math.min(Math.max(issuePage, 1), totalPages);
  const start = (issuePage - 1) * ISSUE_PAGE_SIZE;
  const pageRows = rows.slice(start, start + ISSUE_PAGE_SIZE);
  let html = '<div class="issue-panel">';
  html += '<div class="issue-head">';
  html += `<div><div class="result-title" style="margin:0">问题优化工作台</div><div class="qa-count">自动筛出未回复、弱回复和高风险问题，共 ${total} 个问题簇</div></div>`;
  html += '<div class="issue-tools">';
  html += `<select onchange="setIssueStatusFilter(this.value)"><option ${issueStatusFilter==="全部"?"selected":""}>全部</option><option ${issueStatusFilter==="待处理"?"selected":""}>待处理</option><option ${issueStatusFilter==="需要补知识"?"selected":""}>需要补知识</option><option ${issueStatusFilter==="需要改话术"?"selected":""}>需要改话术</option><option ${issueStatusFilter==="加转人工规则"?"selected":""}>加转人工规则</option><option ${issueStatusFilter==="已补知识"?"selected":""}>已补知识</option><option ${issueStatusFilter==="已优化话术"?"selected":""}>已优化话术</option><option ${issueStatusFilter==="复查通过"?"selected":""}>复查通过</option><option ${issueStatusFilter==="忽略"?"selected":""}>忽略</option></select>`;
  html += `<select onchange="setIssuePriorityFilter(this.value)"><option ${issuePriorityFilter==="全部"?"selected":""}>全部</option><option ${issuePriorityFilter==="高"?"selected":""}>高</option><option ${issuePriorityFilter==="中"?"selected":""}>中</option><option ${issuePriorityFilter==="低"?"selected":""}>低</option></select>`;
  html += '<button class="btn btn-sm" onclick="exportIssuesCsv()">导出 CSV</button>';
  html += '</div></div>';
  if(!total){
    html += '<div class="empty">没有识别到待优化问题</div></div>';
    $("issueWorkbenchArea").innerHTML = html;
    return;
  }
  html += '<table><tr><th style="width:64px">优先级</th><th>问题簇</th><th style="width:120px">失败原因</th><th style="width:96px">出现</th><th>知识卡片建议</th><th style="width:90px">价值分</th><th style="width:210px">处理动作</th></tr>';
  pageRows.forEach(issue => {
    const first = (issue.examples || [])[0] || {};
    const draft = issue.knowledgeCardDraft || {};
    html += '<tr>';
    html += `<td><span class="badge ${priorityClass(issue.priority)}">${issue.priority}</span></td>`;
    html += `<td><div class="issue-question">${escapeHtml(issue.standardQuestion || "-")}</div><div class="issue-meta">${escapeHtml(issue.topic || "-")} · 示例：${escapeHtml(first.question || "-")}</div>${traceIdentityHtml(first.identity)}<div class="issue-reason">${escapeHtml((issue.reasons || []).join("；") || "-")}</div></td>`;
    html += `<td><span class="badge issue-type">${escapeHtml(issue.issueType || "-")}</span><div class="issue-meta">${escapeHtml(issue.failureReason || "-")}</div></td>`;
    html += `<td>${issue.count || 0} 次<br><span class="qa-count">未解决 ${issue.unresolvedCount || 0}</span></td>`;
    html += `<td><div><b>${escapeHtml(issue.suggestedAction || "-")}</b></div><div class="issue-suggestion">${escapeHtml(draft.standardAnswer || draft.answerOutline || issue.trainingSuggestion || "-")}</div><div class="issue-meta">触发词：${(draft.triggerWords || []).map(escapeHtml).join("、") || "-"}</div><div class="issue-meta">转人工：${escapeHtml(draft.manualHandoffRule || "-")}</div></td>`;
    html += `<td><span class="value-score">${issue.optimizationValue || 0}</span><div class="issue-meta">风险 ${issue.adoptionRisk || 0}%</div></td>`;
    html += `<td><div><span class="badge issue-status">${escapeHtml(issue.status || "待处理")}</span></div><div class="mini-actions" style="margin-top:8px">`;
    ["需要补知识","需要改话术","加转人工规则","已补知识","已优化话术","复查通过","忽略"].forEach(status => {
      html += `<button class="btn" onclick='updateIssueStatus(${JSON.stringify(issue.id)},${JSON.stringify(status)})'>${status}</button>`;
    });
    html += '</div></td></tr>';
  });
  html += '</table>';
  html += renderPager("issue", issuePage, totalPages, total, ISSUE_PAGE_SIZE) + '</div>';
  $("issueWorkbenchArea").innerHTML = html;
}

function setIssuePage(page){
  issuePage = page;
  keepPagerPosition("issueWorkbenchArea", renderIssueWorkbench);
}

function jumpIssuePage(){
  const value = Number($("issueJumpInput")?.value || issuePage);
  setIssuePage(value);
}

function setIssueStatusFilter(value){
  issueStatusFilter = value;
  issuePage = 1;
  renderIssueWorkbench();
}

function setIssuePriorityFilter(value){
  issuePriorityFilter = value;
  issuePage = 1;
  renderIssueWorkbench();
}

async function updateIssueStatus(issueId, status){
  const sid = $("shopId").value.trim() || currentShop;
  if(!sid) return alert("请先选择店铺");
  const r = await post("/api/issue-status", {shopId:sid, issueId, status});
  if(!r.success) return alert(r.error || "状态保存失败");
  (lastAnalysis.issueWorkbench || []).forEach(issue => {
    if(issue.id === issueId) issue.status = status;
  });
  renderIssueWorkbench();
}

function csvCell(value){
  return `"${String(value ?? "").replace(/"/g, '""')}"`;
}

function exportIssuesCsv(){
  const rows = filteredIssues();
  const headers = ["优先级","优化价值分","问题类型","失败原因","采纳风险","状态","问题簇","话题","出现次数","未解决数","原因","建议处理","触发词","标准问题","相似问法","标准答案","转人工边界","适用场景","不适用场景","示例问题","当前回复"];
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach(issue => {
    const first = (issue.examples || [])[0] || {};
    const draft = issue.knowledgeCardDraft || {};
    lines.push([
      issue.priority, issue.optimizationValue, issue.issueType, issue.failureReason, issue.adoptionRisk, issue.status, issue.standardQuestion, issue.topic,
      issue.count, issue.unresolvedCount, (issue.reasons || []).join("；"),
      issue.suggestedAction, (draft.triggerWords || []).join("；"),
      draft.standardQuestion || issue.standardQuestion, (draft.similarQuestions || []).join("；"),
      draft.standardAnswer || draft.answerOutline || "", draft.manualHandoffRule || "",
      draft.applicableScene || "", draft.notApplicableScene || "", first.question || "", first.answer || "",
    ].map(csvCell).join(","));
  });
  const blob = new Blob(["\ufeff" + lines.join("\n")], {type:"text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `issue-workbench-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function exportKnowledgeCardsCsv(){
  const rows = topKnowledgeIssues(lastAnalysis || {});
  const headers = ["卡片标题","优先级","优化价值分","问题类型","失败原因","采纳风险","标准问题","相似问法","触发词","标准答案","转人工边界","适用场景","不适用场景","采纳目标","出现次数","状态"];
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach(issue => {
    const draft = issue.knowledgeCardDraft || {};
    lines.push([
      draft.title || "", issue.priority, issue.optimizationValue, issue.issueType, issue.failureReason, issue.adoptionRisk,
      draft.standardQuestion || issue.standardQuestion || "",
      (draft.similarQuestions || []).join("；"),
      (draft.triggerWords || []).join("；"),
      draft.standardAnswer || draft.answerOutline || "",
      draft.manualHandoffRule || "",
      draft.applicableScene || "",
      draft.notApplicableScene || "",
      draft.acceptanceGoal || "",
      issue.count || 0,
      issue.status || "待处理",
    ].map(csvCell).join(","));
  });
  const blob = new Blob(["\ufeff" + lines.join("\n")], {type:"text/csv;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `knowledge-cards-${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function flattenQaExamples(d){
  const rows = [];
  (d.qaExamples || []).forEach(topic => {
    (topic.examples || []).forEach(qa => rows.push({...qa, topic: topic.topic}));
  });
  return rows;
}

function renderQaPage(){
  if(!lastAnalysis || !$("qaPageArea")) return;
  const rows = flattenQaExamples(lastAnalysis);
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / QA_PAGE_SIZE));
  qaPage = Math.min(Math.max(qaPage, 1), totalPages);
  const start = (qaPage - 1) * QA_PAGE_SIZE;
  const pageRows = rows.slice(start, start + QA_PAGE_SIZE);
  let html = '<div class="qa-panel">';
  html += `<div class="qa-head"><div class="result-title" style="margin:0">典型 Q&A 示例</div><div class="qa-count">共 ${total} 条，每页 ${QA_PAGE_SIZE} 条，第 ${qaPage}/${totalPages} 页</div></div>`;
  if(!total){
    html += '<div class="empty">暂无 Q&A 示例</div></div>';
    $("qaPageArea").innerHTML = html;
    return;
  }
  html += '<table><tr><th style="width:56px">#</th><th style="width:150px">话题</th><th>用户问题</th><th>智能体回复</th></tr>';
  pageRows.forEach((qa, idx) => {
    html += `<tr><td>${start + idx + 1}</td><td><span class="qa-topic">${escapeHtml(qa.topic || "-")}</span></td><td><div class="qa-q">${escapeHtml(qa.question || "-")}</div>${traceIdentityHtml(qa.identity)}</td><td class="qa-a">${escapeHtml(qa.answer || "-")}</td></tr>`;
  });
  html += '</table>';
  html += renderPager("qa", qaPage, totalPages, total, QA_PAGE_SIZE) + '</div>';
  $("qaPageArea").innerHTML = html;
}

function setQaPage(page){
  qaPage = page;
  keepPagerPosition("qaPageArea", renderQaPage);
}

function jumpQaPage(){
  const value = Number($("qaJumpInput")?.value || qaPage);
  setQaPage(value);
}

async function refresh(){
  const sid = $("shopId").value.trim() || currentShop;
  const r = await get("/api/overview" + (sid ? `?shopId=${encodeURIComponent(sid)}` : ""));
  $("m1").textContent = r.totalTraces || 0;
  $("m2").textContent = r.totalTopics || 0;
  $("m3").textContent = r.totalIssues || 0;
}

function stop(){ if(abortCtrl) abortCtrl.abort(); resetBtn(); }
init();
</script>
</body>
</html>"""


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 - 店铺智能体训练工作台</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f6fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;color:#172033}
.box{width:min(420px,calc(100vw - 32px));background:#fff;border:1px solid #dfe5ee;border-radius:12px;padding:28px;box-shadow:0 12px 30px rgba(15,23,42,.08)}
h1{font-size:20px;margin:0 0 8px}.tip{color:#64748b;font-size:13px;margin-bottom:18px}label{font-size:12px;font-weight:700;color:#475467}
input{width:100%;height:38px;margin-top:6px;border:1px solid #cbd5e1;border-radius:8px;padding:0 10px;font:inherit}
button{width:100%;height:38px;margin-top:16px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-weight:700;cursor:pointer}
.err{margin-top:12px;color:#d92d20;font-size:13px}
</style>
</head>
<body><form class="box" method="post"><h1>店铺智能体训练工作台</h1><div class="tip">请输入访问密码</div><label>访问密码</label><input name="password" type="password" autofocus><button type="submit">进入后台</button>{error}</form></body></html>"""

AUTH_STYLE = """
body{margin:0;min-height:100vh;background:#eef3f9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",Arial,sans-serif;color:#172033}
.shell{min-height:100vh;display:grid;place-items:center;padding:24px}.box{width:min(420px,calc(100vw - 32px));background:#fff;border:1px solid #dfe5ee;border-radius:12px;padding:28px;box-shadow:0 12px 30px rgba(15,23,42,.08)}
h1{font-size:20px;margin:0 0 8px}.tip{color:#64748b;font-size:13px;margin-bottom:18px;line-height:1.6}label{font-size:12px;font-weight:700;color:#475467}
input,select{box-sizing:border-box;width:100%;height:38px;margin:6px 0 12px;border:1px solid #cbd5e1;border-radius:8px;padding:0 10px;font:inherit}.row{display:flex;align-items:center;justify-content:space-between;gap:12px}.check{display:flex;align-items:center;gap:6px;color:#475467;font-size:13px}.check input{width:14px;height:14px;margin:0}
button,.btn{width:100%;height:38px;margin-top:8px;border:0;border-radius:8px;background:#1677ff;color:#fff;font-weight:700;cursor:pointer;text-decoration:none;display:grid;place-items:center}.link{color:#175cd3;text-decoration:none;font-size:13px}.err{margin-top:12px;color:#d92d20;font-size:13px}.ok{margin-top:12px;color:#027a48;font-size:13px}
table{width:100%;border-collapse:collapse;background:#fff}th,td{padding:10px;border-bottom:1px solid #e4e7ec;text-align:left;font-size:13px}th{background:#f8fafc;color:#475467}.admin{max-width:1100px;margin:0 auto;padding:24px}.panel{background:#fff;border:1px solid #dfe5ee;border-radius:12px;box-shadow:0 12px 30px rgba(15,23,42,.06);overflow:hidden}.panel-head{height:52px;padding:0 16px;border-bottom:1px solid #e4e7ec;display:flex;align-items:center;justify-content:space-between}.panel-body{padding:16px}.actions{display:flex;gap:6px;flex-wrap:wrap}.actions button{width:auto;height:30px;margin:0;padding:0 10px}.danger{background:#d92d20}.muted{color:#667085}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}@media(max-width:760px){.grid{grid-template-columns:1fr}.admin{padding:12px;overflow:auto}}
"""

ACCOUNT_LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>&#30331;&#24405; - &#24215;&#38138;&#26234;&#33021;&#20307;&#35757;&#32451;&#24037;&#20316;&#21488;</title><style>{style}</style></head><body><div class="shell"><form class="box" method="post"><h1>&#24215;&#38138;&#26234;&#33021;&#20307;&#35757;&#32451;&#24037;&#20316;&#21488;</h1><div class="tip">&#35831;&#36755;&#20837;&#36134;&#21495;&#23494;&#30721;&#30331;&#24405;&#12290;&#21246;&#36873;&#35760;&#20303;&#30331;&#24405;&#21518;&#65292;&#19979;&#27425;&#25171;&#24320;&#22806;&#37096;&#38142;&#25509;&#20250;&#33258;&#21160;&#36827;&#20837;&#21518;&#21488;&#12290;</div><label>&#36134;&#21495;</label><input name="username" value="{username}" autocomplete="username" autofocus><label>&#23494;&#30721;</label><input name="password" type="password" autocomplete="current-password"><div class="row"><label class="check"><input name="remember" type="checkbox" checked> &#35760;&#20303;&#30331;&#24405;</label><a class="link" href="/register">&#27880;&#20876;&#36134;&#21495;</a></div><button type="submit">&#36827;&#20837;&#21518;&#21488;</button><div class="row" style="margin-top:12px"><a class="link" href="/admin/users">&#31649;&#29702;&#21592;&#20837;&#21475;</a></div>{error}</form></div></body></html>"""

REGISTER_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>&#27880;&#20876; - &#24215;&#38138;&#26234;&#33021;&#20307;&#35757;&#32451;&#24037;&#20316;&#21488;</title><style>{style}</style></head><body><div class="shell"><form class="box" method="post"><h1>&#27880;&#20876;&#36134;&#21495;</h1><div class="tip">&#27880;&#20876;&#21518;&#40664;&#35748;&#21487;&#30452;&#25509;&#20351;&#29992;&#12290;&#31649;&#29702;&#21592;&#21487;&#22312;&#36134;&#21495;&#21518;&#21488;&#20462;&#25913;&#23494;&#30721;&#12289;&#31105;&#29992;&#36134;&#21495;&#12289;&#35774;&#32622;&#21040;&#26399;&#26102;&#38388;&#12290;</div><label>&#36134;&#21495;</label><input name="username" value="{username}" autocomplete="username" autofocus><label>&#23494;&#30721;</label><input name="password" type="password" autocomplete="new-password"><label>&#30830;&#35748;&#23494;&#30721;</label><input name="password2" type="password" autocomplete="new-password"><button type="submit">&#27880;&#20876;&#24182;&#30331;&#24405;</button><div class="row" style="margin-top:12px"><a class="link" href="/login">&#24050;&#26377;&#36134;&#21495;&#65292;&#21435;&#30331;&#24405;</a></div>{error}</form></div></body></html>"""

ADMIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>&#36134;&#21495;&#21518;&#21488;&#31649;&#29702;</title><style>{style}</style></head><body><div class="admin"><div class="panel"><div class="panel-head"><h1>&#36134;&#21495;&#21518;&#21488;&#31649;&#29702;</h1><div><a class="link" href="/">&#36820;&#22238;&#24037;&#20316;&#21488;</a> | <a class="link" href="/logout">&#36864;&#20986;</a></div></div><div class="panel-body"><div class="grid"><input id="newUsername" placeholder="&#26032;&#36134;&#21495;"><input id="newPassword" placeholder="&#21021;&#22987;&#23494;&#30721;"><select id="newRole"><option value="user">&#26222;&#36890;&#36134;&#21495;</option><option value="admin">&#31649;&#29702;&#21592;</option></select><button onclick="createUser()">&#26032;&#22686;&#36134;&#21495;</button></div><table><thead><tr><th>&#36134;&#21495;</th><th>&#35282;&#33394;</th><th>&#29366;&#24577;</th><th>&#21040;&#26399;&#26102;&#38388;</th><th>&#26368;&#36817;&#30331;&#24405;</th><th>&#25805;&#20316;</th></tr></thead><tbody id="userRows"></tbody></table><div id="msg" class="muted" style="margin-top:12px"></div></div></div></div><script>
const $=id=>document.getElementById(id);const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(url,data){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data||{})});return r.json()}
async function load(){const r=await fetch("/api/admin/users");const d=await r.json();$("userRows").innerHTML=(d.users||[]).map(u=>`<tr><td>${esc(u.username)}</td><td>${u.role==="admin"?"\u7ba1\u7406\u5458":"\u666e\u901a\u7528\u6237"}</td><td>${u.active?"\u542f\u7528":"\u7981\u7528"}</td><td>${esc(u.expiresAt||"\u6c38\u4e45")}</td><td>${esc(u.lastLoginAt||"-")}</td><td><div class="actions"><button onclick="resetPwd(this)" data-username="${esc(u.username)}">\u6539\u5bc6\u7801</button><button onclick="setExpire(this)" data-username="${esc(u.username)}">\u5230\u671f\u65f6\u95f4</button><button onclick="toggleUser(this)" data-username="${esc(u.username)}" data-active="${u.active}">${u.active?"\u7981\u7528":"\u542f\u7528"}</button><button class="danger" onclick="delUser(this)" data-username="${esc(u.username)}">\u5220\u9664</button></div></td></tr>`).join("")}
async function createUser(){const r=await api("/api/admin/users/create",{username:$("newUsername").value,password:$("newPassword").value,role:$("newRole").value});$("msg").textContent=r.success?"\u5df2\u65b0\u589e":r.error;load()}
async function resetPwd(btn){const username=btn.dataset.username,password=prompt("\u8f93\u5165\u65b0\u5bc6\u7801");if(!password)return;const r=await api("/api/admin/users/update",{username,password});$("msg").textContent=r.success?"\u5bc6\u7801\u5df2\u4fee\u6539":r.error;load()}
async function setExpire(btn){const username=btn.dataset.username,expiresAt=prompt("\u5230\u671f\u65f6\u95f4\uff0c\u683c\u5f0f 2026-12-31T23:59:59\uff1b\u7559\u7a7a\u8868\u793a\u6c38\u4e45");if(expiresAt===null)return;const r=await api("/api/admin/users/update",{username,expiresAt});$("msg").textContent=r.success?"\u5230\u671f\u65f6\u95f4\u5df2\u66f4\u65b0":r.error;load()}
async function toggleUser(btn){const username=btn.dataset.username,active=btn.dataset.active!=="true";const r=await api("/api/admin/users/update",{username,active});$("msg").textContent=r.success?"\u72b6\u6001\u5df2\u66f4\u65b0":r.error;load()}
async function delUser(btn){const username=btn.dataset.username;if(!confirm("\u786e\u5b9a\u5220\u9664\u8d26\u53f7 "+username+"?"))return;const r=await api("/api/admin/users/delete",{username});$("msg").textContent=r.success?"\u5df2\u5220\u9664":r.error;load()}
load();
</script></body></html>"""


def render_auth_page(template, **kwargs):
    values = {"style": AUTH_STYLE, "error": "", "username": ""}
    values.update(kwargs)
    html = template
    for key, value in values.items():
        html = html.replace("{" + key + "}", str(value))
    return html


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
            error = '<div class="err">账号或密码错误</div>'
        elif not user.get("active", True):
            error = '<div class="err">账号已被禁用</div>'
        elif is_user_expired(user):
            error = '<div class="err">账号已过期</div>'
        else:
            users = load_users()
            users[username]["lastLoginAt"] = now_iso()
            save_users(users)
            login_user(username, remember=bool(request.form.get("remember")))
            return redirect(url_for("index"))
    return render_auth_page(ACCOUNT_LOGIN_HTML, username=username, error=error)


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
            error = '<div class="err">账号 2-32 位，支持中英文数字下划线</div>'
        elif username in users:
            error = '<div class="err">账号已存在</div>'
        elif len(password) < 6:
            error = '<div class="err">密码至少 6 位</div>'
        elif password != password2:
            error = '<div class="err">两次密码不一致</div>'
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
    return render_auth_page(REGISTER_HTML, username=username, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users")
@require_admin
def admin_users_page():
    return render_auth_page(ADMIN_HTML)


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
    return HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


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

    return {
        "traceId": pick("id", "traceId"),
        "shopId": pick("thirdShopId"),
        "shopName": pick("shopName"),
        "buyerId": pick("buyerAccount", "buyerId", "buyerNick", "buyerName", "userId"),
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
        "hasBuyer": bool(pick("buyerAccount", "buyerId", "buyerNick", "buyerName", "userId")),
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


if __name__ == "__main__":
    host = os.environ.get("QA_HOST", "127.0.0.1")
    port = int(os.environ.get("QA_PORT", "5000"))
    print("=" * 50)
    print("  QA Agent Trace Analyzer")
    print(f"  http://{host}:{port}")
    print("=" * 50)
    app.run(host=host, port=port, debug=False)
