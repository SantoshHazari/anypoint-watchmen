"""SQLite persistence for demo ownership registry."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3


DEMO_SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  owner TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT '',
  customer_context TEXT NOT NULL DEFAULT '',
  environment_id TEXT NOT NULL DEFAULT '',
  environment_name TEXT NOT NULL DEFAULT '',
  resource_type TEXT NOT NULL DEFAULT '',
  resource_name TEXT NOT NULL DEFAULT '',
  expected_usage_notes TEXT NOT NULL DEFAULT '',
  start_date TEXT NOT NULL DEFAULT '',
  expiry_date TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT NOT NULL DEFAULT '',
  created_at_utc TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_demo_status ON demo_registry(status);
CREATE INDEX IF NOT EXISTS idx_demo_resource ON demo_registry(resource_type, resource_name, environment_id);
CREATE INDEX IF NOT EXISTS idx_demo_expiry ON demo_registry(expiry_date);
"""


VALID_STATUSES = {"planned", "active", "expired", "closed"}
VALID_RESOURCE_TYPES = {"application", "api_instance", "gateway", "mq", "object_store", "other"}


def ensure_demo_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DEMO_SCHEMA)


def create_demo(conn: sqlite3.Connection, data: dict) -> int:
    ensure_demo_schema(conn)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    status = data.get("status") or "active"
    resource_type = data.get("resource_type") or "other"
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid demo status: {status}")
    if resource_type not in VALID_RESOURCE_TYPES:
        raise ValueError(f"Invalid resource type: {resource_type}")
    cur = conn.execute(
        """
        INSERT INTO demo_registry
        (name, owner, purpose, customer_context, environment_id, environment_name,
         resource_type, resource_name, expected_usage_notes, start_date, expiry_date,
         status, notes, created_at_utc, updated_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"].strip(),
            data["owner"].strip(),
            data.get("purpose", "").strip(),
            data.get("customer_context", "").strip(),
            data.get("environment_id", "").strip(),
            data.get("environment_name", "").strip(),
            resource_type,
            data.get("resource_name", "").strip(),
            data.get("expected_usage_notes", "").strip(),
            data.get("start_date", "").strip(),
            data.get("expiry_date", "").strip(),
            status,
            data.get("notes", "").strip(),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_demo(conn: sqlite3.Connection, demo_id: int, data: dict) -> bool:
    ensure_demo_schema(conn)
    status = data.get("status") or "active"
    resource_type = data.get("resource_type") or "other"
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid demo status: {status}")
    if resource_type not in VALID_RESOURCE_TYPES:
        raise ValueError(f"Invalid resource type: {resource_type}")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    cur = conn.execute(
        """
        UPDATE demo_registry
        SET name = ?, owner = ?, purpose = ?, customer_context = ?,
            environment_id = ?, environment_name = ?, resource_type = ?, resource_name = ?,
            expected_usage_notes = ?, start_date = ?, expiry_date = ?,
            status = ?, notes = ?, updated_at_utc = ?
        WHERE id = ?
        """,
        (
            data["name"].strip(),
            data["owner"].strip(),
            data.get("purpose", "").strip(),
            data.get("customer_context", "").strip(),
            data.get("environment_id", "").strip(),
            data.get("environment_name", "").strip(),
            resource_type,
            data.get("resource_name", "").strip(),
            data.get("expected_usage_notes", "").strip(),
            data.get("start_date", "").strip(),
            data.get("expiry_date", "").strip(),
            status,
            data.get("notes", "").strip(),
            now,
            demo_id,
        ),
    )
    return cur.rowcount > 0


def set_demo_status(conn: sqlite3.Connection, demo_id: int, status: str) -> bool:
    ensure_demo_schema(conn)
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid demo status: {status}")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE demo_registry SET status = ?, updated_at_utc = ? WHERE id = ?",
        (status, now, demo_id),
    )
    return cur.rowcount > 0


def get_demo(conn: sqlite3.Connection, demo_id: int) -> dict | None:
    ensure_demo_schema(conn)
    row = conn.execute("SELECT * FROM demo_registry WHERE id = ?", (demo_id,)).fetchone()
    return dict(row) if row else None


def list_demos(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict]:
    ensure_demo_schema(conn)
    if status:
        rows = conn.execute(
            "SELECT * FROM demo_registry WHERE status = ? ORDER BY expiry_date, name",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM demo_registry ORDER BY status, expiry_date, name").fetchall()
    return [dict(row) for row in rows]


def demo_counts(conn: sqlite3.Connection) -> dict:
    ensure_demo_schema(conn)
    rows = conn.execute("SELECT status, COUNT(*) AS c FROM demo_registry GROUP BY status").fetchall()
    counts = {row["status"]: int(row["c"]) for row in rows}
    counts["total"] = sum(counts.values())
    today = dt.date.today().isoformat()
    counts["expired_open"] = conn.execute(
        """
        SELECT COUNT(*) AS c FROM demo_registry
        WHERE expiry_date <> '' AND expiry_date < ? AND status NOT IN ('closed')
        """,
        (today,),
    ).fetchone()["c"]
    return counts


def active_demo_resource_keys(conn: sqlite3.Connection) -> set[tuple[str, str, str]]:
    ensure_demo_schema(conn)
    rows = conn.execute(
        """
        SELECT resource_type, resource_name, environment_id
        FROM demo_registry
        WHERE status IN ('planned', 'active')
          AND resource_name <> ''
        """
    ).fetchall()
    return {(row["resource_type"], row["resource_name"], row["environment_id"]) for row in rows}


def serialize_demo(demo: dict) -> str:
    return json.dumps(demo, sort_keys=True)
