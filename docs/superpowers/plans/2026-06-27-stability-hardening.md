# Stability Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden JSON persistence, shop ID path handling, and startup secret display without changing user-facing workflows.

**Architecture:** Add a focused `111/storage.py` module and make `111/app.py` delegate JSON reads/writes to it. Add narrow shop ID validation around per-shop file paths. Keep the Flask routes and templates otherwise intact.

**Tech Stack:** Python 3, Flask, stdlib `unittest`, Windows batch scripts.

---

### Task 1: Storage Helper

**Files:**
- Create: `111/storage.py`
- Create: `tests/test_storage.py`
- Modify: `111/app.py`

- [ ] Write tests for missing JSON fallback, malformed JSON fallback, successful JSON load, and atomic replacement.
- [ ] Run `python -m unittest tests.test_storage -v` and confirm it fails because `storage` does not exist.
- [ ] Create `111/storage.py` with `load_json` and atomic `save_json`.
- [ ] Update `111/app.py` so its existing `load_json` and `save_json` wrappers delegate to `storage.py`.
- [ ] Run `python -m unittest tests.test_storage -v` and confirm it passes.

### Task 2: Shop ID Path Guard

**Files:**
- Create: `tests/test_app_helpers.py`
- Modify: `111/app.py`

- [ ] Write tests that valid shop IDs produce paths under `DATA_DIR` and invalid IDs such as `../secret` or `a/b` raise `InvalidShopId`.
- [ ] Run `python -m unittest tests.test_app_helpers -v` and confirm it fails because `InvalidShopId` does not exist.
- [ ] Add `InvalidShopId`, `shop_file_token`, and guarded `data_file` / `analysis_file` helpers.
- [ ] Add a Flask error handler that returns HTTP 400 for `InvalidShopId`.
- [ ] Run `python -m unittest tests.test_app_helpers -v` and confirm it passes.

### Task 3: Startup Secret Hygiene

**Files:**
- Modify: `111/app.py`
- Modify: `start_server.bat`

- [ ] Add a startup warning helper for the default Flask secret key.
- [ ] Remove direct admin password echo from `start_server.bat` and replace it with a note to check `.env`.
- [ ] Run `python -m py_compile 111/app.py 111/storage.py wsgi.py`.

### Task 4: Full Verification

**Files:**
- All changed files

- [ ] Run `python -m unittest discover -s tests -v`.
- [ ] Run `python -m py_compile 111/app.py 111/storage.py wsgi.py`.
- [ ] Run `git diff --check`.
- [ ] Review `git diff --stat` for scope creep.
