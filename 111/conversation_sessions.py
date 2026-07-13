"""Pure helpers for grouping normalized Trace rows into buyer conversations."""

import hashlib


DEFAULT_SESSION_GAP_MINUTES = 60


def _int_timestamp(value):
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(timestamp, 0)


def _session_id(shop_id, buyer_id, start_time, first_trace_id):
    source = "|".join((str(shop_id or ""), str(buyer_id or ""), str(start_time or 0), str(first_trace_id or "")))
    return "session-" + hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def build_conversation_sessions(rows, shop_id="", gap_minutes=DEFAULT_SESSION_GAP_MINUTES):
    """Group normalized Trace rows by buyer and a bounded inactivity gap.

    Rows must provide traceId, buyerIdRaw and timestamp. Missing identity or time is
    intentionally treated as an isolated single-turn session to avoid false joins.
    """
    gap_ms = max(1, int(gap_minutes or DEFAULT_SESSION_GAP_MINUTES)) * 60 * 1000
    normalized = []
    for raw_row in rows or []:
        if not isinstance(raw_row, dict):
            continue
        trace_id = str(raw_row.get("traceId", "") or "").strip()
        if not trace_id:
            continue
        row = dict(raw_row)
        row["traceId"] = trace_id
        row["buyerIdRaw"] = str(row.get("buyerIdRaw", "") or "").strip()
        row["timestamp"] = _int_timestamp(row.get("timestamp"))
        normalized.append(row)

    normalized.sort(key=lambda row: (row["buyerIdRaw"], row["timestamp"], row["traceId"]))
    sessions = []
    active = {}

    for row in normalized:
        buyer_id = row["buyerIdRaw"]
        timestamp = row["timestamp"]
        previous = active.get(buyer_id) if buyer_id and timestamp else None
        if previous and timestamp - previous["endTime"] <= gap_ms:
            previous["records"].append(row)
            previous["endTime"] = timestamp
            continue

        session = {
            "id": _session_id(shop_id, buyer_id or row["traceId"], timestamp, row["traceId"]),
            "buyerIdRaw": buyer_id,
            "buyerId": row.get("buyerId", ""),
            "startTime": timestamp,
            "endTime": timestamp,
            "records": [row],
        }
        sessions.append(session)
        if buyer_id and timestamp:
            active[buyer_id] = session

    for session in sessions:
        session["records"].sort(key=lambda row: (row["timestamp"], row["traceId"]))
        session["traceIds"] = [row["traceId"] for row in session["records"]]
        session["turnCount"] = len(session["records"])
        session["isMultiTurn"] = session["turnCount"] >= 2

    return sorted(sessions, key=lambda session: (session["startTime"], session["id"]), reverse=True)
