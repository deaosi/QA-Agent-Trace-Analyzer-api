"""Production WSGI entrypoint for the QA Agent Trace Analyzer."""

import os
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, "111")

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from app import app  # noqa: E402

