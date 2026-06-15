# -*- coding: utf-8 -*-
"""QA Agent Trace Analyzer - with Q&A examples"""
import json, os, time, re
from collections import Counter, defaultdict
from datetime import datetime

import jieba
import requests
from flask import Flask, jsonify, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(BASE_DIR, ".cookies.json")
SHOPS_FILE = os.path.join(BASE_DIR, ".shops.json")

app = Flask(__name__)

def load_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except: pass
    return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def data_file(shop_id):
    return os.path.join(BASE_DIR, f"traces_{shop_id}.json")

def analysis_file(shop_id):
    return os.path.join(BASE_DIR, f"analysis_{shop_id}.json")

def build_session():
    saved = load_json(COOKIE_FILE)
    if not saved: return None
    s = requests.Session()
    for name, value in saved.items():
        s.cookies.set(name, value, domain=".tanyuai.com")
    try:
        s.get("https://agent.tanyuai.com", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    except: pass
    return s

HTML = r"""<