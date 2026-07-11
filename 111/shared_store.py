"""SQLite-backed shared shop and quality data store."""

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def stable_trace_id(record):
    """Create a deterministic id for legacy records that have no trace id."""
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "generated-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class SharedStore:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shops (
                    shop_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    total INTEGER NOT NULL DEFAULT 0,
                    updated TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS traces (
                    shop_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    fetched_at TEXT NOT NULL DEFAULT '',
                    fetched_by TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (shop_id, trace_id)
                );
                CREATE INDEX IF NOT EXISTS idx_traces_shop_time
                    ON traces(shop_id, fetched_at);
                CREATE TABLE IF NOT EXISTS analyses (
                    shop_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS issue_status (
                    shop_id TEXT NOT NULL,
                    issue_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (shop_id, issue_id)
                );
                CREATE TABLE IF NOT EXISTS ai_tasks (
                    task_id TEXT PRIMARY KEY,
                    shop_id TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '',
                    cache_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    issue_offset INTEGER NOT NULL DEFAULT 0,
                    issue_limit INTEGER NOT NULL DEFAULT 10,
                    total INTEGER NOT NULL DEFAULT 0,
                    generated INTEGER NOT NULL DEFAULT 0,
                    result_payload TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_ai_tasks_cache
                    ON ai_tasks(cache_key, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_ai_tasks_owner
                    ON ai_tasks(shop_id, username, updated_at);
                """
            )

    def list_shops(self):
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.shop_id, s.name, s.total, s.updated, s.source,
                       COUNT(t.trace_id) AS trace_count
                FROM shops AS s
                LEFT JOIN traces AS t ON t.shop_id = s.shop_id
                GROUP BY s.shop_id, s.name, s.total, s.updated, s.source
                ORDER BY s.shop_id
                """
            ).fetchall()
        return {
            row["shop_id"]: {
                "name": row["name"],
                "total": row["total"],
                "traceCount": int(row["trace_count"] or 0),
                "updated": row["updated"],
                "source": row["source"],
            }
            for row in rows
        }

    def upsert_shop(self, shop_id, name="", total=0, source=""):
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO shops(shop_id, name, total, updated, source)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(shop_id) DO UPDATE SET
                    name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE shops.name END,
                    total = excluded.total,
                    updated = excluded.updated,
                    source = CASE WHEN excluded.source <> '' THEN excluded.source ELSE shops.source END
                """,
                (shop_id, str(name or shop_id), int(total or 0), timestamp, source),
            )

    def delete_shop(self, shop_id):
        with self.connect() as connection:
            connection.execute("DELETE FROM shops WHERE shop_id = ?", (shop_id,))
            connection.execute("DELETE FROM traces WHERE shop_id = ?", (shop_id,))
            connection.execute("DELETE FROM analyses WHERE shop_id = ?", (shop_id,))
            connection.execute("DELETE FROM issue_status WHERE shop_id = ?", (shop_id,))
            connection.execute("DELETE FROM ai_tasks WHERE shop_id = ?", (shop_id,))

    @staticmethod
    def _decode_json(value, fallback=None):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _ai_task_from_row(self, row):
        if not row:
            return None
        return {
            "taskId": row["task_id"],
            "shopId": row["shop_id"],
            "username": row["username"],
            "cacheKey": row["cache_key"],
            "status": row["status"],
            "progress": int(row["progress"] or 0),
            "stage": row["stage"],
            "issueOffset": int(row["issue_offset"] or 0),
            "issueLimit": int(row["issue_limit"] or 0),
            "total": int(row["total"] or 0),
            "generated": int(row["generated"] or 0),
            "result": self._decode_json(row["result_payload"], None),
            "error": row["error"] or "",
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "cancelRequested": bool(row["cancel_requested"]),
        }

    def create_ai_task(
        self,
        shop_id,
        username="",
        cache_key="",
        issue_offset=0,
        issue_limit=10,
        status="queued",
        progress=0,
        stage="等待任务",
        total=0,
        generated=0,
        result=None,
        error="",
    ):
        task_id = uuid.uuid4().hex
        timestamp = now_iso()
        result_payload = json.dumps(result, ensure_ascii=False) if result is not None else ""
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_tasks(
                    task_id, shop_id, username, cache_key, status, progress, stage,
                    issue_offset, issue_limit, total, generated, result_payload,
                    error, created_at, updated_at, cancel_requested
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    str(shop_id),
                    str(username or ""),
                    str(cache_key or ""),
                    str(status),
                    int(progress or 0),
                    str(stage or ""),
                    int(issue_offset or 0),
                    int(issue_limit or 0),
                    int(total or 0),
                    int(generated or 0),
                    result_payload,
                    str(error or ""),
                    timestamp,
                    timestamp,
                ),
            )
        return task_id

    def get_ai_task(self, task_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_tasks WHERE task_id = ?", (str(task_id),)
            ).fetchone()
        return self._ai_task_from_row(row)

    def update_ai_task(self, task_id, **fields):
        column_map = {
            "cacheKey": "cache_key",
            "status": "status",
            "progress": "progress",
            "stage": "stage",
            "issueOffset": "issue_offset",
            "issueLimit": "issue_limit",
            "total": "total",
            "generated": "generated",
            "error": "error",
            "cancelRequested": "cancel_requested",
        }
        assignments = []
        values = []
        for key, value in fields.items():
            column = column_map.get(key)
            if not column:
                continue
            if key == "cancelRequested":
                value = 1 if value else 0
            elif key in {"progress", "issueOffset", "issueLimit", "total", "generated"}:
                value = int(value or 0)
            assignments.append(f"{column} = ?")
            values.append(value)
        if "result" in fields:
            assignments.append("result_payload = ?")
            values.append(json.dumps(fields["result"], ensure_ascii=False) if fields["result"] is not None else "")
        if not assignments:
            return self.get_ai_task(task_id)
        assignments.append("updated_at = ?")
        values.extend([now_iso(), str(task_id)])
        with self.connect() as connection:
            connection.execute(
                f"UPDATE ai_tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
        return self.get_ai_task(task_id)

    def request_ai_task_cancel(self, task_id):
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ai_tasks
                SET cancel_requested = 1, updated_at = ?
                WHERE task_id = ? AND status IN ('queued', 'running')
                """,
                (now_iso(), str(task_id)),
            )
        return cursor.rowcount > 0

    def find_cached_ai_result(self, cache_key):
        if not cache_key:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM ai_tasks
                WHERE cache_key = ? AND status = 'succeeded' AND result_payload <> ''
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (str(cache_key),),
            ).fetchone()
        return self._ai_task_from_row(row)

    def latest_ai_task(self, shop_id, username=""):
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM ai_tasks
                WHERE shop_id = ? AND username = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (str(shop_id), str(username or "")),
            ).fetchone()
        return self._ai_task_from_row(row)

    def fail_incomplete_ai_tasks(self, error="服务已重启，任务未继续执行"):
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE ai_tasks
                SET status = 'failed', error = ?, updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (str(error), now_iso()),
            )

    def load_traces(self, shop_id):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT trace_id, payload FROM traces WHERE shop_id = ?",
                (shop_id,),
            ).fetchall()
        traces = []
        for row in rows:
            try:
                record = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                traces.append(record)
        return traces

    def count_traces(self, shop_id):
        with self.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM traces WHERE shop_id = ?", (shop_id,)
            ).fetchone()[0]

    def merge_traces(self, shop_id, records, fetched_by="", overwrite=False):
        timestamp = now_iso()
        inserted = 0
        updated = 0
        with self.connect() as connection:
            for record in records:
                if not isinstance(record, dict):
                    continue
                trace_id = str(record.get("id") or record.get("traceId") or stable_trace_id(record))
                payload = dict(record)
                payload.setdefault("id", trace_id)
                cursor = connection.execute(
                    "SELECT 1 FROM traces WHERE shop_id = ? AND trace_id = ?",
                    (shop_id, trace_id),
                )
                existed = cursor.fetchone() is not None
                connection.execute(
                    """
                    INSERT INTO traces(shop_id, trace_id, payload, fetched_at, fetched_by)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(shop_id, trace_id) DO UPDATE SET
                        payload = excluded.payload,
                        fetched_at = excluded.fetched_at,
                        fetched_by = excluded.fetched_by
                    """,
                    (shop_id, trace_id, json.dumps(payload, ensure_ascii=False), timestamp, str(fetched_by or "")),
                )
                if existed:
                    updated += 1
                else:
                    inserted += 1
        return {"inserted": inserted, "updated": updated, "total": self.count_traces(shop_id)}

    def load_analysis(self, shop_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM analyses WHERE shop_id = ?", (shop_id,)
            ).fetchone()
        if not row:
            return {}
        try:
            value = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_analysis(self, shop_id, value):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses(shop_id, payload, updated)
                VALUES (?, ?, ?)
                ON CONFLICT(shop_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated = excluded.updated
                """,
                (shop_id, json.dumps(value, ensure_ascii=False), now_iso()),
            )

    def load_issue_status(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT shop_id, issue_id, status FROM issue_status"
            ).fetchall()
        return {f"{row['shop_id']}:{row['issue_id']}": row["status"] for row in rows}

    def set_issue_status(self, shop_id, issue_id, status):
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO issue_status(shop_id, issue_id, status, updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(shop_id, issue_id) DO UPDATE SET
                    status = excluded.status,
                    updated = excluded.updated
                """,
                (shop_id, issue_id, status, now_iso()),
            )

    def migrate_legacy(self, data_dir, shops_file, issue_status_file, load_json):
        """Import legacy JSON once without deleting the source files."""
        with self.connect() as connection:
            has_data = connection.execute("SELECT 1 FROM shops LIMIT 1").fetchone()
        if has_data:
            return

        shops = load_json(shops_file, {})
        if isinstance(shops, dict):
            for shop_id, info in shops.items():
                if isinstance(info, dict):
                    self.upsert_shop(shop_id, info.get("name", shop_id), info.get("total", 0), info.get("source", "legacy"))

        for filename in os.listdir(data_dir):
            if not filename.startswith("traces_") or not filename.endswith(".json"):
                continue
            shop_id = filename[7:-5]
            records = load_json(os.path.join(data_dir, filename), [])
            if isinstance(records, list):
                self.merge_traces(shop_id, records, "legacy")

        for filename in os.listdir(data_dir):
            if not filename.startswith("analysis_") or not filename.endswith(".json"):
                continue
            shop_id = filename[8:-5]
            analysis = load_json(os.path.join(data_dir, filename), {})
            if isinstance(analysis, dict) and analysis:
                self.save_analysis(shop_id, analysis)

        statuses = load_json(issue_status_file, {})
        if isinstance(statuses, dict):
            for key, status in statuses.items():
                if ":" in str(key):
                    shop_id, issue_id = str(key).split(":", 1)
                    self.set_issue_status(shop_id, issue_id, status)
