"""SQLite persistence for Anypoint Platform inventory data."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Iterable


INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  collected_at_utc TEXT NOT NULL,
  org_id TEXT NOT NULL,
  org_name TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS inventory_business_group (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  bg_id TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_bg_id TEXT NOT NULL DEFAULT '',
  bg_path TEXT NOT NULL DEFAULT '',
  is_master INTEGER NOT NULL DEFAULT 0,
  owner_id TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(snapshot_id) REFERENCES inventory_snapshot(id),
  UNIQUE(snapshot_id, bg_id)
);

CREATE TABLE IF NOT EXISTS inventory_environment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  env_id TEXT NOT NULL,
  name TEXT NOT NULL,
  org_id TEXT NOT NULL,
  env_type TEXT NOT NULL DEFAULT '',
  is_production INTEGER NOT NULL DEFAULT 0,
  client_id TEXT NOT NULL DEFAULT '',
  bg_id TEXT NOT NULL DEFAULT '',
  bg_name TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(snapshot_id) REFERENCES inventory_snapshot(id),
  UNIQUE(snapshot_id, env_id)
);

CREATE TABLE IF NOT EXISTS inventory_application (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  env_id TEXT NOT NULL,
  domain TEXT NOT NULL,
  full_domain TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  worker_count INTEGER NOT NULL DEFAULT 0,
  worker_size TEXT NOT NULL DEFAULT '',
  worker_cpu TEXT NOT NULL DEFAULT '',
  worker_memory TEXT NOT NULL DEFAULT '',
  mule_version TEXT NOT NULL DEFAULT '',
  region TEXT NOT NULL DEFAULT '',
  file_name TEXT NOT NULL DEFAULT '',
  last_update_time TEXT NOT NULL DEFAULT '',
  runtime_version TEXT NOT NULL DEFAULT '',
  bg_id TEXT NOT NULL DEFAULT '',
  bg_name TEXT NOT NULL DEFAULT '',
  extra_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(snapshot_id) REFERENCES inventory_snapshot(id),
  UNIQUE(snapshot_id, source, env_id, domain)
);

CREATE TABLE IF NOT EXISTS inventory_api_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  env_id TEXT NOT NULL,
  api_id TEXT NOT NULL,
  instance_label TEXT NOT NULL DEFAULT '',
  group_id TEXT NOT NULL DEFAULT '',
  asset_id TEXT NOT NULL DEFAULT '',
  asset_version TEXT NOT NULL DEFAULT '',
  technology TEXT NOT NULL DEFAULT '',
  endpoint_uri TEXT NOT NULL DEFAULT '',
  proxy_uri TEXT NOT NULL DEFAULT '',
  is_cloudhub INTEGER NOT NULL DEFAULT 0,
  autodiscovery_name TEXT NOT NULL DEFAULT '',
  bg_id TEXT NOT NULL DEFAULT '',
  bg_name TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(snapshot_id) REFERENCES inventory_snapshot(id),
  UNIQUE(snapshot_id, env_id, api_id)
);

CREATE TABLE IF NOT EXISTS inventory_change (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  change_type TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_key TEXT NOT NULL,
  env_id TEXT NOT NULL DEFAULT '',
  old_json TEXT,
  new_json TEXT,
  detected_at_utc TEXT NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES inventory_snapshot(id)
);

CREATE INDEX IF NOT EXISTS idx_inv_change_snapshot ON inventory_change(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_inv_change_type ON inventory_change(change_type, entity_type);
CREATE INDEX IF NOT EXISTS idx_inv_app_env ON inventory_application(snapshot_id, env_id);
CREATE INDEX IF NOT EXISTS idx_inv_api_env ON inventory_api_instance(snapshot_id, env_id);
CREATE INDEX IF NOT EXISTS idx_inv_bg ON inventory_business_group(snapshot_id);
"""

# Migration: add columns to existing tables that lack them
_MIGRATIONS = [
    "ALTER TABLE inventory_environment ADD COLUMN bg_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE inventory_environment ADD COLUMN bg_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE inventory_application ADD COLUMN bg_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE inventory_application ADD COLUMN bg_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE inventory_api_instance ADD COLUMN bg_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE inventory_api_instance ADD COLUMN bg_name TEXT NOT NULL DEFAULT ''",
]


def ensure_inventory_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(INVENTORY_SCHEMA)
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists


def insert_inventory_snapshot(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    collected_at_utc: str,
    org_id: str,
    org_name: str = "",
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO inventory_snapshot (run_id, collected_at_utc, org_id, org_name) VALUES (?, ?, ?, ?)",
        (run_id, collected_at_utc, org_id, org_name),
    )
    row = conn.execute("SELECT id FROM inventory_snapshot WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to insert inventory snapshot {run_id}")
    return int(row["id"])


def insert_business_groups(conn: sqlite3.Connection, snapshot_id: int, groups: Iterable[dict]) -> int:
    count = 0
    for bg in groups:
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO inventory_business_group
            (snapshot_id, bg_id, name, parent_bg_id, bg_path, is_master, owner_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                bg["bg_id"],
                bg["name"],
                bg.get("parent_bg_id", ""),
                bg.get("bg_path", ""),
                bg.get("is_master", 0),
                bg.get("owner_id", ""),
            ),
        )
        if conn.total_changes > before:
            count += 1
    return count


def insert_environments(conn: sqlite3.Connection, snapshot_id: int, envs: Iterable[dict]) -> int:
    count = 0
    for env in envs:
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO inventory_environment
            (snapshot_id, env_id, name, org_id, env_type, is_production, client_id, bg_id, bg_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, env["env_id"], env["name"], env["org_id"], env["env_type"],
             env["is_production"], env.get("client_id", ""),
             env.get("bg_id", ""), env.get("bg_name", "")),
        )
        if conn.total_changes > before:
            count += 1
    return count


def insert_applications(conn: sqlite3.Connection, snapshot_id: int, apps: Iterable[dict]) -> int:
    count = 0
    for app in apps:
        extra = {k: v for k, v in app.items() if k not in (
            "source", "env_id", "domain", "full_domain", "status",
            "worker_count", "worker_size", "worker_cpu", "worker_memory",
            "mule_version", "region", "file_name", "last_update_time", "runtime_version",
            "bg_id", "bg_name",
        )}
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO inventory_application
            (snapshot_id, source, env_id, domain, full_domain, status,
             worker_count, worker_size, worker_cpu, worker_memory,
             mule_version, region, file_name, last_update_time, runtime_version,
             bg_id, bg_name, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                app["source"],
                app["env_id"],
                app["domain"],
                app.get("full_domain") or "",
                app.get("status") or "",
                app.get("worker_count") or 0,
                app.get("worker_size") or "",
                app.get("worker_cpu") or "",
                app.get("worker_memory") or "",
                app.get("mule_version") or "",
                app.get("region") or "",
                app.get("file_name") or "",
                app.get("last_update_time") or "",
                app.get("runtime_version") or "",
                app.get("bg_id") or "",
                app.get("bg_name") or "",
                json.dumps(extra, sort_keys=True),
            ),
        )
        if conn.total_changes > before:
            count += 1
    return count


def insert_api_instances(conn: sqlite3.Connection, snapshot_id: int, instances: Iterable[dict]) -> int:
    count = 0
    for inst in instances:
        before = conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO inventory_api_instance
            (snapshot_id, env_id, api_id, instance_label, group_id, asset_id,
             asset_version, technology, endpoint_uri, proxy_uri, is_cloudhub,
             autodiscovery_name, bg_id, bg_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                inst["env_id"],
                str(inst["api_id"]),
                inst.get("instance_label") or "",
                inst.get("group_id") or "",
                inst.get("asset_id") or "",
                inst.get("asset_version") or "",
                inst.get("technology") or "",
                inst.get("endpoint_uri") or "",
                inst.get("proxy_uri") or "",
                inst.get("is_cloudhub") or 0,
                inst.get("autodiscovery_name") or "",
                inst.get("bg_id") or "",
                inst.get("bg_name") or "",
            ),
        )
        if conn.total_changes > before:
            count += 1
    return count


def insert_changes(conn: sqlite3.Connection, snapshot_id: int, changes: Iterable[dict]) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    count = 0
    for ch in changes:
        conn.execute(
            """INSERT INTO inventory_change
            (snapshot_id, change_type, entity_type, entity_key, env_id, old_json, new_json, detected_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                ch["change_type"],
                ch["entity_type"],
                ch["entity_key"],
                ch.get("env_id", ""),
                json.dumps(ch["old"], sort_keys=True) if ch.get("old") else None,
                json.dumps(ch["new"], sort_keys=True) if ch.get("new") else None,
                now,
            ),
        )
        count += 1
    return count


# -- Query functions --


def latest_snapshot_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM inventory_snapshot ORDER BY collected_at_utc DESC LIMIT 1"
    ).fetchone()
    return int(row["id"]) if row else None


def latest_inventory(conn: sqlite3.Connection) -> dict:
    sid = latest_snapshot_id(conn)
    if sid is None:
        return {"snapshot": None, "business_groups": [], "environments": [], "applications": [], "api_instances": []}

    snapshot = dict(conn.execute("SELECT * FROM inventory_snapshot WHERE id = ?", (sid,)).fetchone())
    bgs = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_business_group WHERE snapshot_id = ? ORDER BY bg_path", (sid,)
    ).fetchall()]
    envs = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_environment WHERE snapshot_id = ? ORDER BY bg_name, name", (sid,)
    ).fetchall()]
    apps = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_application WHERE snapshot_id = ? ORDER BY bg_name, env_id, domain", (sid,)
    ).fetchall()]
    apis = [dict(r) for r in conn.execute(
        "SELECT * FROM inventory_api_instance WHERE snapshot_id = ? ORDER BY bg_name, env_id, asset_id", (sid,)
    ).fetchall()]

    return {"snapshot": snapshot, "business_groups": bgs, "environments": envs, "applications": apps, "api_instances": apis}


def previous_snapshot_data(conn: sqlite3.Connection, before_snapshot_id: int) -> dict:
    row = conn.execute(
        "SELECT id FROM inventory_snapshot WHERE id < ? ORDER BY id DESC LIMIT 1",
        (before_snapshot_id,),
    ).fetchone()
    if row is None:
        return {"applications": {}, "api_instances": {}}

    prev_id = int(row["id"])
    apps = {}
    for r in conn.execute("SELECT * FROM inventory_application WHERE snapshot_id = ?", (prev_id,)).fetchall():
        d = dict(r)
        apps[f"{d['source']}:{d['env_id']}:{d['domain']}"] = d
    apis = {}
    for r in conn.execute("SELECT * FROM inventory_api_instance WHERE snapshot_id = ?", (prev_id,)).fetchall():
        d = dict(r)
        apis[f"{d['env_id']}:{d['api_id']}"] = d
    return {"applications": apps, "api_instances": apis}


def list_changes(conn: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, s.run_id, s.collected_at_utc
        FROM inventory_change c
        JOIN inventory_snapshot s ON s.id = c.snapshot_id
        ORDER BY c.detected_at_utc DESC
        LIMIT ?""",
        (limit,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["old"] = json.loads(d.pop("old_json")) if d.get("old_json") else None
        d["new"] = json.loads(d.pop("new_json")) if d.get("new_json") else None
        result.append(d)
    return result


def inventory_counts(conn: sqlite3.Connection) -> dict:
    sid = latest_snapshot_id(conn)
    if sid is None:
        return {"business_groups": 0, "environments": 0, "applications": 0, "api_instances": 0, "changes_24h": 0}

    bgs = conn.execute("SELECT COUNT(*) AS c FROM inventory_business_group WHERE snapshot_id = ?", (sid,)).fetchone()["c"]
    envs = conn.execute("SELECT COUNT(*) AS c FROM inventory_environment WHERE snapshot_id = ?", (sid,)).fetchone()["c"]
    apps = conn.execute("SELECT COUNT(*) AS c FROM inventory_application WHERE snapshot_id = ?", (sid,)).fetchone()["c"]
    apis = conn.execute("SELECT COUNT(*) AS c FROM inventory_api_instance WHERE snapshot_id = ?", (sid,)).fetchone()["c"]

    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    changes = conn.execute(
        "SELECT COUNT(*) AS c FROM inventory_change WHERE detected_at_utc >= ?", (cutoff,)
    ).fetchone()["c"]

    return {"business_groups": bgs, "environments": envs, "applications": apps, "api_instances": apis, "changes_24h": changes}
