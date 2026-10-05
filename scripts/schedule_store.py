"""SQLite persistence for app lifecycle schedules."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

SCHEDULE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_environment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id TEXT NOT NULL,
  env_name TEXT NOT NULL DEFAULT '',
  org_id TEXT NOT NULL DEFAULT '',
  start_time TEXT NOT NULL,
  stop_time TEXT NOT NULL,
  days_of_week TEXT NOT NULL DEFAULT '0,1,2,3,4',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_app_override (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  env_id TEXT NOT NULL,
  domain TEXT NOT NULL,
  org_id TEXT NOT NULL DEFAULT '',
  deployment_id TEXT NOT NULL DEFAULT '',
  override_type TEXT NOT NULL,
  start_time TEXT,
  stop_time TEXT,
  days_of_week TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  UNIQUE(source, env_id, domain)
);

CREATE TABLE IF NOT EXISTS schedule_one_time (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_type TEXT NOT NULL,
  action TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  env_id TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  org_id TEXT NOT NULL DEFAULT '',
  deployment_id TEXT NOT NULL DEFAULT '',
  scheduled_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  result_detail TEXT,
  created_at_utc TEXT NOT NULL,
  executed_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS schedule_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp_utc TEXT NOT NULL,
  action TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  trigger_id INTEGER,
  target_type TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '',
  env_id TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  org_id TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  response_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS schedule_execution (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id INTEGER NOT NULL,
  schedule_type TEXT NOT NULL,
  action TEXT NOT NULL,
  env_id TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  executed_date TEXT NOT NULL,
  executed_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sched_log_ts ON schedule_log(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_sched_log_env ON schedule_log(env_id, timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_sched_onetime_status ON schedule_one_time(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sched_exec_lookup ON schedule_execution(schedule_id, domain, action, executed_date);
"""


def ensure_schedule_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEDULE_SCHEMA)
    # Migration: remove UNIQUE(env_id) constraint if present (allow multiple schedules per env)
    _migrate_env_unique(conn)


def _migrate_env_unique(conn: sqlite3.Connection) -> None:
    """Drop UNIQUE(env_id) from schedule_environment if it exists."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='schedule_environment'"
    ).fetchone()
    if row and "UNIQUE(env_id)" in (row[0] or ""):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS _se_tmp AS SELECT * FROM schedule_environment;
            DROP TABLE schedule_environment;
            CREATE TABLE schedule_environment (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              env_id TEXT NOT NULL,
              env_name TEXT NOT NULL DEFAULT '',
              org_id TEXT NOT NULL DEFAULT '',
              start_time TEXT NOT NULL,
              stop_time TEXT NOT NULL,
              days_of_week TEXT NOT NULL DEFAULT '0,1,2,3,4',
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at_utc TEXT NOT NULL,
              updated_at_utc TEXT NOT NULL
            );
            INSERT INTO schedule_environment SELECT * FROM _se_tmp;
            DROP TABLE _se_tmp;
        """)


# ---------------------------------------------------------------------------
# Environment schedules
# ---------------------------------------------------------------------------

def create_env_schedule(
    conn: sqlite3.Connection,
    *,
    env_id: str,
    env_name: str = "",
    org_id: str = "",
    start_time: str = "",
    stop_time: str = "",
    days_of_week: str = "0,1,2,3,4",
    enabled: bool = True,
) -> int:
    """Insert a new environment schedule entry (multiple per env allowed)."""
    now = _now_utc()
    cur = conn.execute(
        """INSERT INTO schedule_environment
        (env_id, env_name, org_id, start_time, stop_time, days_of_week, enabled, created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (env_id, env_name, org_id, start_time, stop_time, days_of_week, int(enabled), now, now),
    )
    return cur.lastrowid


# Keep old name as alias for backwards compatibility with tests
upsert_env_schedule = create_env_schedule


def delete_env_schedule(conn: sqlite3.Connection, schedule_id: int) -> bool:
    """Delete an environment schedule by its row id."""
    cur = conn.execute("DELETE FROM schedule_environment WHERE id = ?", (schedule_id,))
    return cur.rowcount > 0


def list_env_schedules(conn: sqlite3.Connection, *, enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM schedule_environment"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY env_name, env_id"
    return [dict(r) for r in conn.execute(sql).fetchall()]


def get_env_schedule(conn: sqlite3.Connection, env_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM schedule_environment WHERE env_id = ?", (env_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# App overrides (custom schedule + always_on exclusion)
# ---------------------------------------------------------------------------

def upsert_app_override(
    conn: sqlite3.Connection,
    *,
    source: str,
    env_id: str,
    domain: str,
    org_id: str = "",
    deployment_id: str = "",
    override_type: str,  # 'custom' or 'always_on'
    start_time: str | None = None,
    stop_time: str | None = None,
    days_of_week: str | None = None,
    enabled: bool = True,
) -> int:
    now = _now_utc()
    conn.execute(
        """INSERT INTO schedule_app_override
        (source, env_id, domain, org_id, deployment_id, override_type,
         start_time, stop_time, days_of_week, enabled, created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, env_id, domain) DO UPDATE SET
            org_id=excluded.org_id, deployment_id=excluded.deployment_id,
            override_type=excluded.override_type,
            start_time=excluded.start_time, stop_time=excluded.stop_time,
            days_of_week=excluded.days_of_week, enabled=excluded.enabled,
            updated_at_utc=excluded.updated_at_utc""",
        (source, env_id, domain, org_id, deployment_id, override_type,
         start_time, stop_time, days_of_week, int(enabled), now, now),
    )
    row = conn.execute(
        "SELECT id FROM schedule_app_override WHERE source = ? AND env_id = ? AND domain = ?",
        (source, env_id, domain),
    ).fetchone()
    return int(row["id"])


def delete_app_override(conn: sqlite3.Connection, override_id: int) -> bool:
    cur = conn.execute("DELETE FROM schedule_app_override WHERE id = ?", (override_id,))
    return cur.rowcount > 0


def list_app_overrides(
    conn: sqlite3.Connection,
    *,
    override_type: str | None = None,
    enabled_only: bool = False,
) -> list[dict]:
    clauses = []
    params: list = []
    if override_type:
        clauses.append("override_type = ?")
        params.append(override_type)
    if enabled_only:
        clauses.append("enabled = 1")
    sql = "SELECT * FROM schedule_app_override"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY domain"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_app_override(conn: sqlite3.Connection, source: str, env_id: str, domain: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM schedule_app_override WHERE source = ? AND env_id = ? AND domain = ?",
        (source, env_id, domain),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# One-time actions
# ---------------------------------------------------------------------------

def create_one_time_action(
    conn: sqlite3.Connection,
    *,
    target_type: str,  # 'app' or 'env'
    action: str,       # 'start', 'stop', 'stop_all'
    env_id: str,
    scheduled_at: str,
    source: str = "",
    domain: str = "",
    org_id: str = "",
    deployment_id: str = "",
) -> int:
    now = _now_utc()
    cur = conn.execute(
        """INSERT INTO schedule_one_time
        (target_type, action, source, env_id, domain, org_id, deployment_id,
         scheduled_at, status, created_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (target_type, action, source, env_id, domain, org_id, deployment_id, scheduled_at, now),
    )
    return cur.lastrowid


def cancel_one_time_action(conn: sqlite3.Connection, action_id: int) -> bool:
    cur = conn.execute(
        "UPDATE schedule_one_time SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
        (action_id,),
    )
    return cur.rowcount > 0


def list_one_time_actions(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM schedule_one_time WHERE status = ? ORDER BY scheduled_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schedule_one_time ORDER BY scheduled_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_one_time_actions(conn: sqlite3.Connection, before: str) -> list[dict]:
    """Get pending one-time actions scheduled at or before the given timestamp."""
    rows = conn.execute(
        "SELECT * FROM schedule_one_time WHERE status = 'pending' AND scheduled_at <= ? ORDER BY scheduled_at",
        (before,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_one_time_executed(
    conn: sqlite3.Connection,
    action_id: int,
    *,
    status: str = "executed",
    detail: str = "",
) -> None:
    now = _now_utc()
    conn.execute(
        "UPDATE schedule_one_time SET status = ?, result_detail = ?, executed_at_utc = ? WHERE id = ?",
        (status, detail, now, action_id),
    )


# ---------------------------------------------------------------------------
# Schedule log
# ---------------------------------------------------------------------------

def insert_schedule_log(
    conn: sqlite3.Connection,
    *,
    action: str,
    trigger_type: str,
    trigger_id: int | None = None,
    target_type: str,
    source: str = "",
    env_id: str = "",
    domain: str = "",
    org_id: str = "",
    ok: bool = True,
    detail: str = "",
    response: dict | None = None,
) -> int:
    now = _now_utc()
    cur = conn.execute(
        """INSERT INTO schedule_log
        (timestamp_utc, action, trigger_type, trigger_id, target_type,
         source, env_id, domain, org_id, ok, detail, response_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, action, trigger_type, trigger_id, target_type,
         source, env_id, domain, org_id, int(ok), detail,
         json.dumps(response or {}, default=str)),
    )
    return cur.lastrowid


def list_schedule_logs(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM schedule_log ORDER BY timestamp_utc DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def was_action_done_today(
    conn: sqlite3.Connection,
    *,
    action: str,
    env_id: str,
    domain: str = "",
    today_str: str,
) -> bool:
    """Check if an action was actually executed today for this target.

    Excludes "no action needed" skip entries — those should not block
    real actions triggered by later schedules.
    """
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM schedule_log
        WHERE action = ? AND env_id = ? AND domain = ? AND ok = 1
        AND timestamp_utc >= ?
        AND detail NOT LIKE '%no action needed%'""",
        (action, env_id, domain, today_str),
    ).fetchone()
    return row["c"] > 0


def schedule_counts(conn: sqlite3.Connection) -> dict:
    """Summary counts for the schedule dashboard."""
    env_count = conn.execute("SELECT COUNT(*) AS c FROM schedule_environment WHERE enabled = 1").fetchone()["c"]
    custom_count = conn.execute("SELECT COUNT(*) AS c FROM schedule_app_override WHERE override_type = 'custom' AND enabled = 1").fetchone()["c"]
    always_on_count = conn.execute("SELECT COUNT(*) AS c FROM schedule_app_override WHERE override_type = 'always_on' AND enabled = 1").fetchone()["c"]
    pending_count = conn.execute("SELECT COUNT(*) AS c FROM schedule_one_time WHERE status = 'pending'").fetchone()["c"]

    today_start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
    actions_today = conn.execute(
        "SELECT COUNT(*) AS c FROM schedule_log WHERE timestamp_utc >= ?", (today_start,)
    ).fetchone()["c"]

    return {
        "env_schedules": env_count,
        "custom_overrides": custom_count,
        "always_on": always_on_count,
        "pending_one_time": pending_count,
        "actions_today": actions_today,
    }


# ---------------------------------------------------------------------------
# Execution tracking — prevents re-firing a schedule entry that already ran
# ---------------------------------------------------------------------------

def mark_schedule_executed(
    conn: sqlite3.Connection,
    *,
    schedule_id: int,
    schedule_type: str,   # 'env_schedule' or 'app_override'
    action: str,
    env_id: str,
    domain: str = "",
    today_date: str,      # 'YYYY-MM-DD'
) -> None:
    """Record that a schedule entry was executed for a specific app today."""
    now = _now_utc()
    conn.execute(
        """INSERT INTO schedule_execution
        (schedule_id, schedule_type, action, env_id, domain, executed_date, executed_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (schedule_id, schedule_type, action, env_id, domain, today_date, now),
    )


def was_schedule_executed_today(
    conn: sqlite3.Connection,
    *,
    schedule_id: int,
    action: str,
    domain: str = "",
    today_date: str,
) -> bool:
    """Check if a specific schedule entry already fired for this app today."""
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM schedule_execution
        WHERE schedule_id = ? AND action = ? AND domain = ? AND executed_date = ?""",
        (schedule_id, action, domain, today_date),
    ).fetchone()
    return row["c"] > 0


def clear_executions_before(conn: sqlite3.Connection, before_date: str) -> int:
    """Purge old execution records. Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM schedule_execution WHERE executed_date < ?", (before_date,)
    )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()
