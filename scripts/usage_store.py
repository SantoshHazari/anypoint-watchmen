"""SQLite persistence for normalized Anypoint usage data."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS usage_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  collected_at_utc TEXT NOT NULL,
  host TEXT NOT NULL,
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  timeseries TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  raw_path TEXT
);

CREATE TABLE IF NOT EXISTS usage_record (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  meter_key TEXT NOT NULL,
  meter_name TEXT NOT NULL,
  measurement TEXT NOT NULL,
  meter_type TEXT NOT NULL,
  timeseries TEXT NOT NULL,
  period_start_utc TEXT,
  period_end_utc TEXT,
  org_id TEXT,
  org_name TEXT,
  env_id TEXT,
  env_name TEXT,
  env_type TEXT,
  asset_id TEXT,
  asset_name TEXT,
  app_name TEXT,
  deployment_model TEXT,
  value REAL NOT NULL,
  raw_dimensions_json TEXT NOT NULL,
  raw_record_json TEXT NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES usage_snapshot(id),
  UNIQUE(snapshot_id, meter_key, measurement, period_start_utc, org_id, env_id, asset_id, asset_name, app_name, raw_dimensions_json)
);

CREATE TABLE IF NOT EXISTS entitlement_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  entitlement_key TEXT NOT NULL,
  calculated_at_utc TEXT NOT NULL,
  usage_value REAL NOT NULL,
  limit_value REAL NOT NULL,
  percent_used REAL NOT NULL,
  severity TEXT NOT NULL,
  threshold_percent INTEGER,
  payload_json TEXT NOT NULL,
  UNIQUE(run_id, entitlement_key)
);

CREATE TABLE IF NOT EXISTS alert_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_key TEXT NOT NULL,
  rule TEXT NOT NULL,
  severity TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'open',
  entitlement_key TEXT,
  title TEXT NOT NULL,
  detail TEXT,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  resolved_at_utc TEXT,
  run_id TEXT,
  notified INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_key ON alert_event(alert_key);
CREATE INDEX IF NOT EXISTS idx_alert_state ON alert_event(state);
CREATE INDEX IF NOT EXISTS idx_alert_severity ON alert_event(severity, state);

CREATE TABLE IF NOT EXISTS job_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  job_name TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  duration_seconds REAL,
  summary_json TEXT NOT NULL,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_run_started ON job_run(started_at_utc DESC);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert_snapshot(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    collected_at_utc: str,
    host: str,
    window_start_utc: str,
    window_end_utc: str,
    timeseries: str,
    dimensions: bool,
    raw_path: str | None,
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO usage_snapshot
        (run_id, collected_at_utc, host, window_start_utc, window_end_utc, timeseries, dimensions, raw_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, collected_at_utc, host, window_start_utc, window_end_utc, timeseries, int(dimensions), raw_path),
    )
    row = conn.execute("SELECT id FROM usage_snapshot WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to insert usage snapshot {run_id}")
    return int(row["id"])


def insert_usage_records(conn: sqlite3.Connection, records: Iterable[dict]) -> int:
    count = 0
    for record in records:
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO usage_record
            (snapshot_id, meter_key, meter_name, measurement, meter_type, timeseries,
             period_start_utc, period_end_utc, org_id, org_name, env_id, env_name, env_type,
             asset_id, asset_name, app_name, deployment_model, value,
             raw_dimensions_json, raw_record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["snapshot_id"],
                record["meter_key"],
                record["meter_name"],
                record["measurement"],
                record["meter_type"],
                record["timeseries"],
                record.get("period_start_utc"),
                record.get("period_end_utc"),
                record.get("org_id"),
                record.get("org_name"),
                record.get("env_id"),
                record.get("env_name"),
                record.get("env_type"),
                record.get("asset_id"),
                record.get("asset_name"),
                record.get("app_name"),
                record.get("deployment_model"),
                record["value"],
                json.dumps(record.get("raw_dimensions", {}), sort_keys=True),
                json.dumps(record.get("raw_record", {}), sort_keys=True),
            ),
        )
        if conn.total_changes > before:
            count += 1
    return count


def _dedup_usage_query(
    conn: sqlite3.Connection,
    since_utc: str,
    until_utc: str,
    include_fixtures: bool = False,
) -> dict[str, float]:
    """Aggregate usage values per meter, deduplicating across snapshots.

    The same data point (meter + period + app + env + dimensions) can appear
    in multiple snapshots because each collection run re-fetches the full
    window.  We keep only the value from the most recent snapshot per unique
    data point before aggregating, preventing double-counting.
    """
    fixture_filter = "" if include_fixtures else "AND s.run_id NOT LIKE 'fixture-%'"
    rows = conn.execute(
        f"""
        WITH deduped AS (
            SELECT r.meter_key, r.meter_type, r.period_start_utc,
                   r.app_name, r.env_id, r.raw_dimensions_json,
                   r.value,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.meter_key, r.period_start_utc,
                                    r.app_name, r.env_id, r.raw_dimensions_json
                       ORDER BY r.snapshot_id DESC
                   ) AS rn
            FROM usage_record r
            JOIN usage_snapshot s ON s.id = r.snapshot_id
            WHERE COALESCE(r.period_start_utc, '') >= ?
              AND COALESCE(r.period_end_utc, r.period_start_utc, '') <= ?
              {fixture_filter}
        )
        SELECT meter_key, meter_type, SUM(value) AS sum_value, MAX(value) AS max_value
        FROM deduped
        WHERE rn = 1
        GROUP BY meter_key, meter_type
        """,
        (since_utc, until_utc),
    ).fetchall()
    values: dict[str, float] = {}
    for row in rows:
        if row["meter_type"] == "MAX_CONCURRENT":
            values[row["meter_key"]] = float(row["max_value"] or 0)
        else:
            values[row["meter_key"]] = float(row["sum_value"] or 0)
    return values


def latest_usage_by_meter(
    conn: sqlite3.Connection,
    since_utc: str,
    until_utc: str,
    *,
    include_fixtures: bool = False,
) -> dict[str, float]:
    return _dedup_usage_query(conn, since_utc, until_utc, include_fixtures)


def usage_by_meter_window(
    conn: sqlite3.Connection,
    since_utc: str,
    until_utc: str,
    *,
    include_fixtures: bool = False,
) -> dict[str, float]:
    return _dedup_usage_query(conn, since_utc, until_utc, include_fixtures)


def daily_usage_timeseries(
    conn: sqlite3.Connection,
    meter_keys: list[str],
    since_utc: str,
    until_utc: str,
    *,
    include_fixtures: bool = False,
    days_limit: int = 15,
) -> dict[str, list[dict]]:
    """Return daily timeseries per meter, deduplicated across snapshots.

    Returns {meter_key: [{"date": "2026-05-15", "value": 123.4}, ...]}
    sorted by date ascending.

    Args:
        days_limit: Maximum days to show in response (rolling window).
                   Default 15. Data beyond this is not fetched from DB.
                   All historical data is preserved in database.
    """
    import datetime as dt
    fixture_filter = "" if include_fixtures else "AND s.run_id NOT LIKE 'fixture-%'"
    placeholders = ",".join("?" for _ in meter_keys)

    # Calculate rolling window: last N days from until_utc
    until_date = dt.datetime.fromisoformat(until_utc.replace("Z", "+00:00")).date()
    since_date = until_date - dt.timedelta(days=days_limit - 1)
    rolling_since = since_date.isoformat()
    rolling_until = until_date.isoformat()

    rows = conn.execute(
        f"""
        WITH deduped AS (
            SELECT r.meter_key, r.meter_type, r.period_start_utc,
                   r.app_name, r.env_id, r.raw_dimensions_json,
                   r.value,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.meter_key, r.period_start_utc,
                                    r.app_name, r.env_id, r.raw_dimensions_json
                       ORDER BY r.snapshot_id DESC
                   ) AS rn
            FROM usage_record r
            JOIN usage_snapshot s ON s.id = r.snapshot_id
            WHERE r.meter_key IN ({placeholders})
              AND DATE(r.period_start_utc) >= ?
              AND DATE(r.period_start_utc) <= ?
              {fixture_filter}
        )
        SELECT meter_key, meter_type, period_start_utc,
               SUM(value) AS sum_value, MAX(value) AS max_value
        FROM deduped
        WHERE rn = 1
        GROUP BY meter_key, meter_type, period_start_utc
        ORDER BY meter_key, period_start_utc
        """,
        (*meter_keys, rolling_since, rolling_until),
    ).fetchall()
    result: dict[str, list[dict]] = {k: [] for k in meter_keys}
    for row in rows:
        key = row["meter_key"]
        date_str = (row["period_start_utc"] or "")[:10]
        val = float(row["max_value"] or 0) if row["meter_type"] == "MAX_CONCURRENT" else float(row["sum_value"] or 0)
        result[key].append({"date": date_str, "value": val})
    return result


def latest_day_usage(
    conn: sqlite3.Connection,
    *,
    include_fixtures: bool = False,
) -> dict[str, float]:
    """Return usage values from the most recent daily bucket per meter.

    Finds the latest period_start_utc per meter and deduplicates across
    snapshots so repeated collections don't inflate values.
    """
    fixture_filter = "" if include_fixtures else "AND s.run_id NOT LIKE 'fixture-%'"
    rows = conn.execute(
        f"""
        WITH latest_period AS (
            SELECT meter_key, MAX(period_start_utc) AS max_period
            FROM usage_record
            GROUP BY meter_key
        ),
        deduped AS (
            SELECT r.meter_key, r.meter_type, r.value,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.meter_key, r.app_name, r.env_id, r.raw_dimensions_json
                       ORDER BY r.snapshot_id DESC
                   ) AS rn
            FROM usage_record r
            JOIN usage_snapshot s ON s.id = r.snapshot_id
            JOIN latest_period lp ON r.meter_key = lp.meter_key AND r.period_start_utc = lp.max_period
            WHERE 1=1 {fixture_filter}
        )
        SELECT meter_key, meter_type, SUM(value) AS sum_value, MAX(value) AS max_value
        FROM deduped
        WHERE rn = 1
        GROUP BY meter_key, meter_type
        """,
    ).fetchall()
    values: dict[str, float] = {}
    for row in rows:
        if row["meter_type"] == "MAX_CONCURRENT":
            values[row["meter_key"]] = float(row["max_value"] or 0)
        else:
            values[row["meter_key"]] = float(row["sum_value"] or 0)
    return values


def top_consumers(
    conn: sqlite3.Connection,
    since_utc: str,
    until_utc: str,
    *,
    limit: int = 20,
    include_fixtures: bool = False,
) -> list[dict]:
    fixture_filter = "" if include_fixtures else "AND s.run_id NOT LIKE 'fixture-%'"
    rows = conn.execute(
        f"""
        WITH deduped AS (
            SELECT r.meter_key, r.meter_type,
                   COALESCE(r.env_name, 'unknown') AS env_name,
                   COALESCE(r.app_name, r.asset_name, 'unknown') AS consumer,
                   COALESCE(r.deployment_model, 'unknown') AS deployment_model,
                   r.value,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.meter_key, r.period_start_utc,
                                    r.app_name, r.env_id, r.raw_dimensions_json
                       ORDER BY r.snapshot_id DESC
                   ) AS rn
            FROM usage_record r
            JOIN usage_snapshot s ON s.id = r.snapshot_id
            WHERE COALESCE(r.period_start_utc, '') >= ?
              AND COALESCE(r.period_end_utc, r.period_start_utc, '') <= ?
              AND r.value > 0
              {fixture_filter}
        )
        SELECT meter_key, meter_type, env_name, consumer, deployment_model,
               SUM(value) AS sum_value, MAX(value) AS max_value
        FROM deduped
        WHERE rn = 1
        GROUP BY meter_key, meter_type, env_name, consumer, deployment_model
        ORDER BY sum_value DESC
        LIMIT ?
        """,
        (since_utc, until_utc, limit),
    ).fetchall()
    output = []
    for row in rows:
        val = float(row["max_value"] or 0) if row["meter_type"] == "MAX_CONCURRENT" else float(row["sum_value"] or 0)
        output.append({
            "meter_key": row["meter_key"],
            "env_name": row["env_name"],
            "consumer": row["consumer"],
            "deployment_model": row["deployment_model"],
            "value": val,
        })
    return output


def insert_entitlement_status(conn: sqlite3.Connection, run_id: str, statuses: Iterable[dict]) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for status in statuses:
        conn.execute(
            """
            INSERT OR REPLACE INTO entitlement_status
            (run_id, entitlement_key, calculated_at_utc, usage_value, limit_value,
             percent_used, severity, threshold_percent, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                status["key"],
                now,
                status["usage_value"],
                status["limit_value"],
                status["percent_used"],
                status["severity"],
                status.get("threshold_percent"),
                json.dumps(status, sort_keys=True),
            ),
        )


def create_job_run(conn: sqlite3.Connection, *, run_id: str, job_name: str, requested_by: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO job_run
        (run_id, job_name, status, requested_by, started_at_utc, summary_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, job_name, "running", requested_by, now, "{}"),
    )


def finish_job_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    summary: dict,
    error: str | None = None,
) -> None:
    finished = dt.datetime.now(dt.timezone.utc)
    row = conn.execute("SELECT started_at_utc FROM job_run WHERE run_id = ?", (run_id,)).fetchone()
    started = dt.datetime.fromisoformat(row["started_at_utc"]) if row else finished
    conn.execute(
        """
        UPDATE job_run
        SET status = ?, finished_at_utc = ?, duration_seconds = ?, summary_json = ?, error = ?
        WHERE run_id = ?
        """,
        (
            status,
            finished.isoformat(),
            max((finished - started).total_seconds(), 0),
            json.dumps(summary, sort_keys=True),
            error,
            run_id,
        ),
    )


def get_job_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT run_id, job_name, status, requested_by, started_at_utc, finished_at_utc,
               duration_seconds, summary_json, error
        FROM job_run WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return {**dict(row), "summary": json.loads(row["summary_json"] or "{}")}


def list_job_runs(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    rows = conn.execute(
        """
        SELECT run_id, job_name, status, requested_by, started_at_utc, finished_at_utc,
               duration_seconds, summary_json, error
        FROM job_run
        ORDER BY started_at_utc DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            **dict(row),
            "summary": json.loads(row["summary_json"] or "{}"),
        }
        for row in rows
    ]


def latest_entitlement_status(conn: sqlite3.Connection) -> list[dict]:
    row = conn.execute(
        "SELECT run_id FROM entitlement_status ORDER BY calculated_at_utc DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    rows = conn.execute(
        """
        SELECT payload_json
        FROM entitlement_status
        WHERE run_id = ?
        ORDER BY entitlement_key
        """,
        (row["run_id"],),
    ).fetchall()
    return [json.loads(item["payload_json"]) for item in rows]


def upsert_alert(
    conn: sqlite3.Connection,
    *,
    alert_key: str,
    rule: str,
    severity: str,
    entitlement_key: str | None,
    title: str,
    detail: str | None,
    run_id: str,
    payload: dict,
) -> dict:
    """Insert or update an alert. Returns {"action": "created"|"updated"|"unchanged", "id": int}."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id, severity, state, detail FROM alert_event WHERE alert_key = ?",
        (alert_key,),
    ).fetchone()

    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO alert_event
            (alert_key, rule, severity, state, entitlement_key, title, detail,
             created_at_utc, updated_at_utc, run_id, notified, payload_json)
            VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (alert_key, rule, severity, entitlement_key, title, detail, now, now, run_id, json.dumps(payload, sort_keys=True)),
        )
        return {"action": "created", "id": cur.lastrowid}

    eid = existing["id"]
    old_severity = existing["severity"]
    old_state = existing["state"]

    if old_state in ("resolved", "suppressed"):
        # Re-open if condition recurs
        conn.execute(
            """
            UPDATE alert_event
            SET severity = ?, state = 'open', detail = ?, updated_at_utc = ?,
                run_id = ?, resolved_at_utc = NULL, notified = 0, payload_json = ?
            WHERE id = ?
            """,
            (severity, detail, now, run_id, json.dumps(payload, sort_keys=True), eid),
        )
        return {"action": "reopened", "id": eid}

    if severity != old_severity or detail != existing["detail"]:
        needs_renotify = 1 if severity != old_severity else None
        conn.execute(
            f"""
            UPDATE alert_event
            SET severity = ?, detail = ?, updated_at_utc = ?, run_id = ?, payload_json = ?
                {', notified = 0' if needs_renotify else ''}
            WHERE id = ?
            """,
            (severity, detail, now, run_id, json.dumps(payload, sort_keys=True), eid),
        )
        return {"action": "updated", "id": eid}

    return {"action": "unchanged", "id": eid}


def resolve_stale_alerts(conn: sqlite3.Connection, active_keys: set[str]) -> int:
    """Resolve any open/acknowledged alerts whose keys are no longer active."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT id, alert_key FROM alert_event WHERE state IN ('open', 'acknowledged')"
    ).fetchall()
    resolved = 0
    for row in rows:
        if row["alert_key"] not in active_keys:
            conn.execute(
                "UPDATE alert_event SET state = 'resolved', resolved_at_utc = ?, updated_at_utc = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            resolved += 1
    return resolved


def resolve_old_audit_alerts(conn: sqlite3.Connection, max_age_days: int = 7) -> int:
    """Resolve open audit alerts older than max_age_days.

    Audit alerts (rule='audit_risky_change') are event-based and never auto-resolve
    through the normal active-key mechanism.  Once we stop polling an event (because
    it has aged out of the collection window) its alert should close automatically.
    """
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=max_age_days)).isoformat()
    cur = conn.execute(
        """
        UPDATE alert_event
           SET state = 'resolved',
               resolved_at_utc = ?,
               updated_at_utc  = ?
         WHERE rule  = 'audit_risky_change'
           AND state IN ('open', 'acknowledged')
           AND created_at_utc < ?
        """,
        (now.isoformat(), now.isoformat(), cutoff),
    )
    return cur.rowcount


def update_alert_state(conn: sqlite3.Connection, alert_id: int, new_state: str) -> bool:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    resolved_at = now if new_state in ("resolved", "suppressed") else None
    cur = conn.execute(
        """
        UPDATE alert_event
        SET state = ?, updated_at_utc = ?, resolved_at_utc = COALESCE(?, resolved_at_utc)
        WHERE id = ?
        """,
        (new_state, now, resolved_at, alert_id),
    )
    return cur.rowcount > 0


def get_alert(conn: sqlite3.Connection, alert_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM alert_event WHERE id = ?", (alert_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json", "{}"))
    return d


def list_alerts(
    conn: sqlite3.Connection,
    *,
    state: str | None = None,
    limit: int = 50,
) -> list[dict]:
    where = "WHERE state = ?" if state else ""
    params: tuple = (state, limit) if state else (limit,)
    rows = conn.execute(
        f"""
        SELECT * FROM alert_event
        {where}
        ORDER BY
          CASE state WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,
          CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          updated_at_utc DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload_json"] or "{}")} for r in rows]


def mark_alerts_notified(conn: sqlite3.Connection, alert_ids: list[int]) -> None:
    for aid in alert_ids:
        conn.execute("UPDATE alert_event SET notified = 1 WHERE id = ?", (aid,))


def alert_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT state, COUNT(*) AS cnt FROM alert_event GROUP BY state"
    ).fetchall()
    counts = {r["state"]: r["cnt"] for r in rows}
    counts["total"] = sum(counts.values())
    return counts


def usage_totals_by_meter(conn: sqlite3.Connection, include_fixtures: bool = False) -> list[dict]:
    fixture_filter = "" if include_fixtures else "AND s.run_id NOT LIKE 'fixture-%'"
    rows = conn.execute(
        f"""
        WITH deduped AS (
            SELECT r.meter_key, r.meter_type, r.value,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.meter_key, r.period_start_utc,
                                    r.app_name, r.env_id, r.raw_dimensions_json
                       ORDER BY r.snapshot_id DESC
                   ) AS rn
            FROM usage_record r
            JOIN usage_snapshot s ON s.id = r.snapshot_id
            WHERE 1=1 {fixture_filter}
        )
        SELECT meter_key, meter_type, SUM(value) AS sum_value, MAX(value) AS max_value, COUNT(*) AS records
        FROM deduped
        WHERE rn = 1
        GROUP BY meter_key, meter_type
        ORDER BY meter_key
        """
    ).fetchall()
    output = []
    for row in rows:
        output.append(
            {
                "meter_key": row["meter_key"],
                "meter_type": row["meter_type"],
                "value": float(row["max_value"] or 0) if row["meter_type"] == "MAX_CONCURRENT" else float(row["sum_value"] or 0),
                "records": int(row["records"] or 0),
            }
        )
    return output
