"""Tests for schedule_engine — priority resolution, helpers, and tick logic."""

import sys
import sqlite3
import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schedule_engine import ScheduleEngine, _is_running, _parse_days, _desired_action
from schedule_store import (
    ensure_schedule_schema,
    upsert_env_schedule,
    upsert_app_override,
    create_one_time_action,
    list_schedule_logs,
    list_one_time_actions,
)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schedule_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

def test_is_running():
    assert _is_running("STARTED") is True
    assert _is_running("RUNNING") is True
    assert _is_running("DEPLOYING") is True
    assert _is_running("STOPPED") is False
    assert _is_running("UNDEPLOYED") is False
    assert _is_running("") is False


def test_parse_days():
    assert _parse_days("0,1,2,3,4") == {0, 1, 2, 3, 4}
    assert _parse_days("0,1,2,3,4,5,6") == {0, 1, 2, 3, 4, 5, 6}
    assert _parse_days("") == set()
    assert _parse_days("bad") == set()


def test_desired_action_before_start():
    assert _desired_action("07:30", "08:00", "18:00") == "stop"


def test_desired_action_during_window():
    assert _desired_action("12:00", "08:00", "18:00") == "start"


def test_desired_action_at_start():
    assert _desired_action("08:00", "08:00", "18:00") == "start"


def test_desired_action_after_stop():
    assert _desired_action("18:00", "08:00", "18:00") == "stop"
    assert _desired_action("23:00", "08:00", "18:00") == "stop"


def test_desired_action_stop_only():
    """Stop-only mode: stop at/after stop_time, nothing before."""
    assert _desired_action("17:00", "", "18:00") is None
    assert _desired_action("18:00", "", "18:00") == "stop"
    assert _desired_action("23:00", "", "18:00") == "stop"


def test_desired_action_start_only():
    """Start-only mode: start at/after start_time, nothing before."""
    assert _desired_action("07:00", "08:00", "") is None
    assert _desired_action("08:00", "08:00", "") == "start"
    assert _desired_action("12:00", "08:00", "") == "start"


def test_desired_action_neither():
    assert _desired_action("12:00", "", "") is None


# ---------------------------------------------------------------------------
# Engine tick tests
# ---------------------------------------------------------------------------

def _make_engine(conn):
    """Create engine with a shared in-memory connection and mock client."""
    client = MagicMock()
    client.start_ch2_deployment.return_value = {"status": "APPLYING"}
    client.stop_ch2_deployment.return_value = {"status": "APPLYING"}
    client.start_cloudhub_app.return_value = {"status": "STARTED"}
    client.stop_cloudhub_app.return_value = {"status": "STOPPED"}

    engine = ScheduleEngine(
        db_opener=lambda: conn,
        client_factory=lambda: client,
        poll_interval=30,
    )
    engine._close_connections = False  # don't close shared in-memory conn
    return engine, client


def _insert_test_inventory(conn, apps):
    """Insert minimal inventory data for testing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, org_id TEXT, org_name TEXT,
            collected_at_utc TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory_business_group (
            id INTEGER PRIMARY KEY, snapshot_id INTEGER,
            bg_id TEXT, name TEXT, bg_path TEXT, is_master INTEGER
        );
        CREATE TABLE IF NOT EXISTS inventory_environment (
            id INTEGER PRIMARY KEY, snapshot_id INTEGER,
            env_id TEXT, name TEXT, env_type TEXT, is_production INTEGER,
            bg_id TEXT, bg_name TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory_application (
            id INTEGER PRIMARY KEY, snapshot_id INTEGER,
            source TEXT, env_id TEXT, domain TEXT, status TEXT,
            worker_count INTEGER DEFAULT 1, worker_size TEXT DEFAULT '',
            mule_version TEXT DEFAULT '', region TEXT DEFAULT '',
            bg_id TEXT DEFAULT '', bg_name TEXT DEFAULT '',
            extra_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS inventory_api_instance (
            id INTEGER PRIMARY KEY, snapshot_id INTEGER,
            asset_id TEXT, instance_label TEXT, asset_version TEXT,
            technology TEXT, endpoint_uri TEXT, is_cloudhub INTEGER,
            env_id TEXT, bg_id TEXT, bg_name TEXT
        );
    """)
    conn.execute(
        "INSERT INTO inventory_snapshot (run_id, org_id, org_name, collected_at_utc) VALUES (?,?,?,?)",
        ("run-1", "org-1", "TestOrg", "2026-05-23T10:00:00"),
    )
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for app in apps:
        conn.execute(
            """INSERT INTO inventory_application
            (snapshot_id, source, env_id, domain, status, bg_id, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sid, app["source"], app["env_id"], app["domain"],
             app["status"], app.get("bg_id", "org-1"),
             app.get("extra_json", '{"deployment_id": "dep-1"}')),
        )


def test_tick_stops_app_after_hours():
    """App running after stop_time -> should be stopped."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 19, 0, 0)  # Friday 19:00
    summary = engine.tick(_now=now)

    assert summary["stopped"] == 1
    client.stop_cloudhub_app.assert_called_once()
    logs = list_schedule_logs(conn)
    assert len(logs) == 1
    assert logs[0]["action"] == "stop"
    assert logs[0]["ok"] == 1


def test_tick_starts_app_during_hours():
    """App stopped during business hours -> should be started (catch-up)."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "ch2", "env_id": "env-1", "domain": "my-app", "status": "STOPPED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 10, 0, 0)  # Friday 10:00
    summary = engine.tick(_now=now)

    assert summary["started"] == 1
    client.start_ch2_deployment.assert_called_once()


def test_tick_skips_always_on():
    """always_on app should never be touched, even outside hours."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "critical-app", "status": "STARTED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="critical-app",
                        override_type="always_on")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 22, 0, 0)  # Friday 22:00
    summary = engine.tick(_now=now)

    assert summary["skipped"] == 1
    assert summary["stopped"] == 0
    client.stop_cloudhub_app.assert_not_called()


def test_tick_custom_override_wins():
    """Custom app schedule overrides env schedule."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "late-app", "status": "STARTED"},
    ])
    # Env says stop at 17:00
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="17:00")
    # But this app has custom schedule until 20:00
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="late-app",
                        override_type="custom", start_time="08:00", stop_time="20:00",
                        days_of_week="0,1,2,3,4")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 17, 30, 0)  # Friday 17:30
    summary = engine.tick(_now=now)

    # App should NOT be stopped — custom schedule says it's still in window
    assert summary["stopped"] == 0
    client.stop_cloudhub_app.assert_not_called()


def test_tick_weekend_shutdown():
    """Running app on Saturday with weekday-only schedule -> stop."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "weekday-app", "status": "STARTED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00",
                        days_of_week="0,1,2,3,4")  # Mon-Fri only
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 24, 12, 0, 0)  # Saturday 12:00
    summary = engine.tick(_now=now)

    assert summary["stopped"] == 1


def test_tick_no_redundant_action():
    """Engine should not call stop API if app is already stopped."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 19, 0, 0)  # Friday 19:00

    # First tick — stops
    summary1 = engine.tick(_now=now)
    assert summary1["stopped"] == 1

    # Simulate: app is now stopped in inventory
    conn.execute("UPDATE inventory_application SET status = 'STOPPED' WHERE domain = 'my-app'")
    conn.commit()

    # Second tick — Gate 1 catches it (already executed today), no API call
    summary2 = engine.tick(_now=now)
    assert summary2["stopped"] == 0
    assert client.stop_cloudhub_app.call_count == 1  # only the first call


def test_tick_can_re_stop_after_restart():
    """If an app is stopped then restarted, a later schedule can stop it again."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    # Two schedules: stop at 10:00 and stop at 14:00
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="10:00")
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="14:00")
    conn.commit()

    engine, client = _make_engine(conn)

    # 10:30 — first schedule stops the app
    summary1 = engine.tick(_now=dt.datetime(2026, 5, 22, 10, 30, 0))
    assert summary1["stopped"] == 1

    # App gets restarted (externally or by another schedule)
    conn.execute("UPDATE inventory_application SET status = 'STARTED' WHERE domain = 'my-app'")
    conn.commit()

    # 14:30 — both schedules match (>=10:00 and >=14:00), but the app
    # is STARTED so it will be stopped. The second schedule also matches
    # but the app is now stopped (from first), so it logs "no action needed".
    summary2 = engine.tick(_now=dt.datetime(2026, 5, 22, 14, 30, 0))
    assert summary2["stopped"] >= 1
    assert client.stop_cloudhub_app.call_count >= 2  # first tick + this tick


def test_tick_one_time_action():
    """One-time stop action should be executed and marked."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    create_one_time_action(
        conn, target_type="app", action="stop", env_id="env-1",
        scheduled_at="2026-05-22T18:00:00", source="cloudhub", domain="my-app",
    )
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 18, 5, 0)
    summary = engine.tick(_now=now)

    assert summary["one_time"] == 1
    actions = list_one_time_actions(conn, status="executed")
    assert len(actions) == 1


def test_tick_one_time_env_stop():
    """One-time env-level stop should call stop_all_apps_in_env."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "app-a", "status": "STARTED"},
    ])
    create_one_time_action(
        conn, target_type="env", action="stop_all", env_id="env-1",
        scheduled_at="2026-05-22T18:00:00", org_id="org-1",
    )
    conn.commit()

    engine, client = _make_engine(conn)
    # Mock the list methods so stop_all_apps_in_env can iterate
    client.list_ch2_deployments.return_value = []
    client.list_cloudhub_apps.return_value = [
        {"domain": "app-a", "status": "STARTED"},
    ]

    now = dt.datetime(2026, 5, 22, 18, 5, 0)
    summary = engine.tick(_now=now)

    assert summary["one_time"] == 1
    actions = list_one_time_actions(conn, status="executed")
    assert len(actions) == 1
    client.stop_cloudhub_app.assert_called_once()


def test_tick_one_time_env_stop_action():
    """One-time env target with action='stop' should also route to stop_all."""
    conn = _make_conn()
    _insert_test_inventory(conn, [])
    create_one_time_action(
        conn, target_type="env", action="stop", env_id="env-1",
        scheduled_at="2026-05-22T18:00:00", org_id="org-1",
    )
    conn.commit()

    engine, client = _make_engine(conn)
    client.list_ch2_deployments.return_value = []
    client.list_cloudhub_apps.return_value = []

    now = dt.datetime(2026, 5, 22, 18, 5, 0)
    summary = engine.tick(_now=now)

    assert summary["one_time"] == 1
    actions = list_one_time_actions(conn, status="executed")
    assert len(actions) == 1


def test_tick_expired_one_time():
    """One-time action past grace period should be marked expired."""
    conn = _make_conn()
    _insert_test_inventory(conn, [])
    create_one_time_action(
        conn, target_type="app", action="stop", env_id="env-1",
        scheduled_at="2026-05-22T18:00:00", source="cloudhub", domain="my-app",
    )
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 22, 0, 0)  # 4 hours after scheduled
    engine.tick(_now=now)

    actions = list_one_time_actions(conn, status="expired")
    assert len(actions) == 1


def test_tick_no_schedules():
    """No schedules -> no actions."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 12, 0, 0)
    summary = engine.tick(_now=now)

    assert summary["started"] == 0
    assert summary["stopped"] == 0
    client.stop_cloudhub_app.assert_not_called()
    client.start_cloudhub_app.assert_not_called()


def test_tick_already_stopped_no_action():
    """Already stopped app outside hours -> logged as skipped, no API call."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STOPPED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 20, 0, 0)
    summary = engine.tick(_now=now)

    assert summary["stopped"] == 0
    assert summary["skipped"] == 1
    client.stop_cloudhub_app.assert_not_called()
    # Verify a log entry was created for the skip
    logs = list_schedule_logs(conn)
    assert len(logs) == 1
    assert "already" in logs[0]["detail"]


def test_tick_already_running_no_action():
    """Already running app during hours -> logged as skipped, no API call."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    conn.commit()

    engine, client = _make_engine(conn)
    now = dt.datetime(2026, 5, 22, 12, 0, 0)
    summary = engine.tick(_now=now)

    assert summary["started"] == 0
    assert summary["skipped"] == 1
    client.start_cloudhub_app.assert_not_called()
    logs = list_schedule_logs(conn)
    assert len(logs) == 1
    assert "already" in logs[0]["detail"]


def test_tick_execution_tracking_per_schedule_entry():
    """Different schedule entries for same env fire independently."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    # Two stop schedules at different times (stop-only mode)
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="10:00")
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="14:00")
    conn.commit()

    engine, client = _make_engine(conn)

    # At 10:30 — first schedule fires (10:30 >= 10:00), second doesn't yet (10:30 < 14:00)
    summary = engine.tick(_now=dt.datetime(2026, 5, 22, 10, 30, 0))
    assert summary["stopped"] == 1
    assert client.stop_cloudhub_app.call_count == 1

    # At 14:30 — first schedule already executed (Gate 1), second now matches
    # but app is already stopped from first tick → Gate 2 skips
    conn.execute("UPDATE inventory_application SET status = 'STOPPED' WHERE domain = 'my-app'")
    conn.commit()
    summary2 = engine.tick(_now=dt.datetime(2026, 5, 22, 14, 30, 0))
    assert summary2["stopped"] == 0
    assert summary2["skipped"] == 1  # second schedule sees app stopped
    assert client.stop_cloudhub_app.call_count == 1  # no new API call


def test_tick_status_override_within_tick():
    """After stopping an app, later schedule entries see it as stopped."""
    conn = _make_conn()
    _insert_test_inventory(conn, [
        {"source": "cloudhub", "env_id": "env-1", "domain": "my-app", "status": "STARTED"},
    ])
    # Two schedules — both want to stop
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="10:00")
    upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="11:00")
    conn.commit()

    engine, client = _make_engine(conn)
    summary = engine.tick(_now=dt.datetime(2026, 5, 22, 11, 30, 0))

    # Only one actual API call — second entry sees the status_override
    assert client.stop_cloudhub_app.call_count == 1
    assert summary["stopped"] == 1
    assert summary["skipped"] == 1


def test_engine_start_stop():
    """Engine thread starts and stops cleanly."""
    conn = _make_conn()
    engine, _ = _make_engine(conn)
    engine.poll_interval = 0.1  # fast for test

    assert engine.running is False
    engine.start()
    assert engine.running is True
    engine.start()  # double start is safe
    engine.stop()
    assert engine.running is False
    engine.stop()  # double stop is safe
