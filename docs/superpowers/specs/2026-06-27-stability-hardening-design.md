# Stability Hardening Design

## Goal
Keep the Flask workbench behavior unchanged while making local JSON storage safer, preventing unsafe shop IDs from becoming file paths, and reducing accidental secret exposure during Windows startup.

## Scope
This pass intentionally avoids a large refactor. The core routes and UI stay in place. The only structural extraction is a small storage helper module for JSON loading and atomic JSON saving.

## Architecture
- `111/storage.py` owns `load_json` and `save_json`.
- `111/app.py` keeps existing routes, but delegates JSON file operations to `storage.py`.
- `app.py` validates shop IDs before building per-shop data filenames.
- Windows launch scripts keep existing behavior but avoid printing admin passwords after startup.

## Data Flow
Trace data, analysis data, cookie data, user data, issue status, and AI config remain JSON files under `QA_DATA_DIR`. Saves write to a temporary file in the target directory and then atomically replace the destination file.

## Error Handling
Malformed JSON still falls back to the caller-provided default. Invalid shop IDs raise a route-level 400 response instead of allowing path traversal or producing a server error.

## Verification
Use `python -m unittest discover -s tests` and `python -m py_compile 111/app.py 111/storage.py wsgi.py`.
