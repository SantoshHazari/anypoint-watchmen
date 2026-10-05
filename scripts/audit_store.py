"""SQLite persistence for Anypoint Audit Log events."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Iterable


AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_poll_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  collected_at_utc TEXT NOT NULL,
  org_id TEXT NOT NULL,
  org_name TEXT NOT NULL DEFAULT '',
  window_start_utc TEXT NOT NULL,
  window_end_utc TEXT NOT NULL,
  events_seen INTEGER NOT NULL DEFAULT 0,
  events_inserted INTEGER NOT NULL DEFAULT 0,
  risky_events INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS audit_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL UNIQUE,
  poll_run_id INTEGER NOT NULL,
  event_time_utc TEXT NOT NULL DEFAULT '',
  product TEXT NOT NULL DEFAULT '',
  object_type TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '',
  object_id TEXT NOT NULL DEFAULT '',
  object_name TEXT NOT NULL DEFAULT '',
  user_id TEXT NOT NULL DEFAULT '',
  user_name TEXT NOT NULL DEFAULT '',
  env_id TEXT NOT NULL DEFAULT '',
  env_name TEXT NOT NULL DEFAULT '',
  connected_app TEXT NOT NULL DEFAULT '',
  risky INTEGER NOT NULL DEFAULT 0,
  raw_json TEXT NOT NULL,
  inserted_at_utc TEXT NOT NULL,
  FOREIGN KEY(poll_run_id) REFERENCES audit_poll_run(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_event_time ON audit_event(event_time_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event_risky ON audit_event(risky, event_time_utc DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event_action ON audit_event(object_type, action);
"""


_MIGRATIONS = [
    "ALTER TABLE audit_event ADD COLUMN risk_tier TEXT NOT NULL DEFAULT 'info'",
    "ALTER TABLE audit_event ADD COLUMN subaction TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE audit_event ADD COLUMN bg_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE audit_event ADD COLUMN bg_name TEXT NOT NULL DEFAULT ''",
]


def ensure_audit_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(AUDIT_SCHEMA)
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists


def backfill_objects_fields(conn: sqlite3.Connection) -> int:
    """One-time backfill: extract object_id, object_name, env_id, env_name
    from raw_json objects[] array for rows where those fields are empty.

    Safe to call repeatedly — only updates rows with empty fields.
    """
    rows = conn.execute(
        "SELECT id, raw_json FROM audit_event WHERE object_id = '' OR env_id = ''"
    ).fetchall()
    updated = 0
    for row in rows:
        raw = json.loads(row["raw_json"] or "{}")
        objects = raw.get("objects")
        if not isinstance(objects, list) or not objects:
            continue
        obj = objects[0] if isinstance(objects[0], dict) else {}
        obj_id = obj.get("objectId") or ""
        obj_name = obj.get("objectName") or ""
        env_id = obj.get("environmentId") or ""
        env_name = obj.get("environmentName") or ""
        if obj_id or env_id:
            conn.execute(
                """UPDATE audit_event
                SET object_id = CASE WHEN object_id = '' THEN ? ELSE object_id END,
                    object_name = CASE WHEN object_name = '' THEN ? ELSE object_name END,
                    env_id = CASE WHEN env_id = '' THEN ? ELSE env_id END,
                    env_name = CASE WHEN env_name = '' THEN ? ELSE env_name END
                WHERE id = ?""",
                (obj_id, obj_name, env_id, env_name, row["id"]),
            )
            updated += 1
    if updated:
        conn.commit()
    return updated


def insert_audit_poll_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    collected_at_utc: str,
    org_id: str,
    org_name: str,
    window_start_utc: str,
    window_end_utc: str,
) -> int:
    ensure_audit_schema(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO audit_poll_run
        (run_id, collected_at_utc, org_id, org_name, window_start_utc, window_end_utc)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, collected_at_utc, org_id, org_name, window_start_utc, window_end_utc),
    )
    row = conn.execute("SELECT id FROM audit_poll_run WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to insert audit poll run {run_id}")
    return int(row["id"])


def insert_audit_events(conn: sqlite3.Connection, poll_run_id: int, events: Iterable[dict]) -> tuple[int, int]:
    inserted = 0
    risky = 0
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for event in events:
        if event.get("risky"):
            risky += 1
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO audit_event
            (event_key, poll_run_id, event_time_utc, product, object_type, action,
             object_id, object_name, user_id, user_name, env_id, env_name, connected_app,
             risky, risk_tier, subaction, bg_id, bg_name, raw_json, inserted_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_key"],
                poll_run_id,
                event.get("event_time_utc", ""),
                event.get("product", ""),
                event.get("object_type", ""),
                event.get("action", ""),
                event.get("object_id", ""),
                event.get("object_name", ""),
                event.get("user_id", ""),
                event.get("user_name", ""),
                event.get("env_id", ""),
                event.get("env_name", ""),
                event.get("connected_app", ""),
                1 if event.get("risky") else 0,
                event.get("risk_tier", "info"),
                event.get("subaction", ""),
                event.get("bg_id", ""),
                event.get("bg_name", ""),
                json.dumps(event.get("raw", {}), sort_keys=True, default=str),
                now,
            ),
        )
        if conn.total_changes > before:
            inserted += 1
    return inserted, risky


def finish_audit_poll_run(conn: sqlite3.Connection, poll_run_id: int, *, seen: int, inserted: int, risky: int, error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE audit_poll_run
        SET events_seen = ?, events_inserted = ?, risky_events = ?, error = ?
        WHERE id = ?
        """,
        (seen, inserted, risky, error, poll_run_id),
    )


def get_last_audit_poll_end(conn: sqlite3.Connection) -> str | None:
    """Return window_end_utc of the most recent successful audit poll run.

    "Successful" means the poll completed without an error (error IS NULL).
    Returns None if no successful run exists yet (first run).
    """
    ensure_audit_schema(conn)
    row = conn.execute(
        """
        SELECT window_end_utc
          FROM audit_poll_run
         WHERE error IS NULL
         ORDER BY id DESC
         LIMIT 1
        """
    ).fetchone()
    return row["window_end_utc"] if row else None


def list_audit_users(conn: sqlite3.Connection) -> list[str]:
    """Return distinct non-empty user_name values, sorted alphabetically."""
    ensure_audit_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT user_name FROM audit_event WHERE user_name != '' ORDER BY user_name"
    ).fetchall()
    return [r["user_name"] for r in rows]


def list_audit_bgs(conn: sqlite3.Connection) -> list[str]:
    """Return distinct non-empty bg_name values, sorted alphabetically."""
    ensure_audit_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT bg_name FROM audit_event WHERE bg_name != '' ORDER BY bg_name"
    ).fetchall()
    return [r["bg_name"] for r in rows]


def list_audit_events(conn: sqlite3.Connection, *, risk_tier: str = "", risky_only: bool = False, user_name: str = "", bg_name: str = "", limit: int = 100) -> list[dict]:
    ensure_audit_schema(conn)
    conditions = []
    params: list = []
    if risk_tier:
        conditions.append("risk_tier = ?")
        params.append(risk_tier)
    elif risky_only:
        conditions.append("risky = 1")
    if user_name:
        conditions.append("user_name = ?")
        params.append(user_name)
    if bg_name:
        conditions.append("bg_name = ?")
        params.append(bg_name)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT *
        FROM audit_event
        {where}
        ORDER BY COALESCE(event_time_utc, inserted_at_utc) DESC, id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["raw"] = json.loads(item.pop("raw_json") or "{}")
        output.append(item)
    return output


def audit_counts(conn: sqlite3.Connection) -> dict:
    ensure_audit_schema(conn)
    total = conn.execute("SELECT COUNT(*) AS c FROM audit_event").fetchone()["c"]
    risky = conn.execute("SELECT COUNT(*) AS c FROM audit_event WHERE risky = 1").fetchone()["c"]
    critical = conn.execute("SELECT COUNT(*) AS c FROM audit_event WHERE risk_tier = 'critical'").fetchone()["c"]
    warning = conn.execute("SELECT COUNT(*) AS c FROM audit_event WHERE risk_tier = 'warning'").fetchone()["c"]
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    recent = conn.execute("SELECT COUNT(*) AS c FROM audit_event WHERE inserted_at_utc >= ?", (cutoff,)).fetchone()["c"]
    return {"total": total, "risky": risky, "critical": critical, "warning": warning, "recent_24h": recent}
