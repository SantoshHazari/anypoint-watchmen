"""Tests for schedule_store — CRUD operations on schedule tables."""

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from schedule_store import (
    ensure_schedule_schema,
    upsert_env_schedule,
    delete_env_schedule,
    list_env_schedules,
    get_env_schedule,
    upsert_app_override,
    delete_app_override,
    list_app_overrides,
    get_app_override,
    create_one_time_action,
    cancel_one_time_action,
    list_one_time_actions,
    get_pending_one_time_actions,
    mark_one_time_executed,
    insert_schedule_log,
    list_schedule_logs,
    was_action_done_today,
    schedule_counts,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schedule_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Environment schedules
# ---------------------------------------------------------------------------

def test_create_env_schedule():
    conn = _make_conn()
    row_id = upsert_env_schedule(
        conn, env_id="env-1", env_name="Development",
        org_id="org-1", start_time="08:00", stop_time="18:00",
    )
    assert row_id >= 1
    row = get_env_schedule(conn, "env-1")
    assert row is not None
    assert row["env_name"] == "Development"
    assert row["start_time"] == "08:00"
    assert row["stop_time"] == "18:00"
    assert row["days_of_week"] == "0,1,2,3,4"
    assert row["enabled"] == 1


def test_multiple_schedules_per_env():
    """Multiple schedule entries for the same env are allowed."""
    conn = _make_conn()
    id1 = upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="")
    id2 = upsert_env_schedule(conn, env_id="env-1", start_time="", stop_time="18:00")
    assert id1 != id2
    schedules = list_env_schedules(conn)
    env1_scheds = [s for s in schedules if s["env_id"] == "env-1"]
    assert len(env1_scheds) == 2


def test_delete_env_schedule():
    conn = _make_conn()
    row_id = upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    assert delete_env_schedule(conn, row_id) is True
    assert get_env_schedule(conn, "env-1") is None
    assert delete_env_schedule(conn, row_id) is False  # already gone


def test_list_env_schedules():
    conn = _make_conn()
    upsert_env_schedule(conn, env_id="env-a", env_name="Alpha",
                        start_time="08:00", stop_time="18:00")
    upsert_env_schedule(conn, env_id="env-b", env_name="Beta",
                        start_time="09:00", stop_time="17:00", enabled=False)
    assert len(list_env_schedules(conn)) == 2
    assert len(list_env_schedules(conn, enabled_only=True)) == 1


def test_get_env_schedule_missing():
    conn = _make_conn()
    assert get_env_schedule(conn, "nonexistent") is None


# ---------------------------------------------------------------------------
# App overrides
# ---------------------------------------------------------------------------

def test_upsert_app_override_custom():
    conn = _make_conn()
    row_id = upsert_app_override(
        conn, source="cloudhub", env_id="env-1", domain="my-app",
        org_id="org-1", deployment_id="", override_type="custom",
        start_time="10:00", stop_time="16:00", days_of_week="0,1,2,3,4",
    )
    assert row_id >= 1
    row = get_app_override(conn, "cloudhub", "env-1", "my-app")
    assert row is not None
    assert row["override_type"] == "custom"
    assert row["start_time"] == "10:00"
    assert row["stop_time"] == "16:00"


def test_upsert_app_override_always_on():
    conn = _make_conn()
    upsert_app_override(
        conn, source="ch2", env_id="env-1", domain="critical-app",
        override_type="always_on",
    )
    row = get_app_override(conn, "ch2", "env-1", "critical-app")
    assert row["override_type"] == "always_on"
    assert row["start_time"] is None
    assert row["stop_time"] is None


def test_upsert_app_override_updates():
    conn = _make_conn()
    upsert_app_override(
        conn, source="cloudhub", env_id="env-1", domain="my-app",
        override_type="custom", start_time="10:00", stop_time="16:00",
    )
    upsert_app_override(
        conn, source="cloudhub", env_id="env-1", domain="my-app",
        override_type="always_on",
    )
    row = get_app_override(conn, "cloudhub", "env-1", "my-app")
    assert row["override_type"] == "always_on"


def test_delete_app_override():
    conn = _make_conn()
    row_id = upsert_app_override(
        conn, source="cloudhub", env_id="env-1", domain="my-app",
        override_type="always_on",
    )
    assert delete_app_override(conn, row_id) is True
    assert delete_app_override(conn, row_id) is False


def test_list_app_overrides_filters():
    conn = _make_conn()
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="app-a",
                        override_type="custom", start_time="10:00", stop_time="16:00")
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="app-b",
                        override_type="always_on")
    upsert_app_override(conn, source="ch2", env_id="env-1", domain="app-c",
                        override_type="custom", start_time="08:00", stop_time="20:00",
                        enabled=False)

    assert len(list_app_overrides(conn)) == 3
    assert len(list_app_overrides(conn, override_type="custom")) == 2
    assert len(list_app_overrides(conn, override_type="always_on")) == 1
    assert len(list_app_overrides(conn, enabled_only=True)) == 2
    assert len(list_app_overrides(conn, override_type="custom", enabled_only=True)) == 1


def test_get_app_override_missing():
    conn = _make_conn()
    assert get_app_override(conn, "cloudhub", "env-1", "nope") is None


# ---------------------------------------------------------------------------
# One-time actions
# ---------------------------------------------------------------------------

def test_create_and_list_one_time():
    conn = _make_conn()
    aid = create_one_time_action(
        conn, target_type="app", action="stop", env_id="env-1",
        scheduled_at="2026-05-23T18:00:00", source="cloudhub", domain="my-app",
    )
    assert aid >= 1
    rows = list_one_time_actions(conn)
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    assert rows[0]["action"] == "stop"


def test_cancel_one_time_action():
    conn = _make_conn()
    aid = create_one_time_action(
        conn, target_type="app", action="stop", env_id="env-1",
        scheduled_at="2026-05-23T18:00:00",
    )
    assert cancel_one_time_action(conn, aid) is True
    rows = list_one_time_actions(conn, status="cancelled")
    assert len(rows) == 1
    # can't cancel twice
    assert cancel_one_time_action(conn, aid) is False


def test_get_pending_one_time_actions():
    conn = _make_conn()
    create_one_time_action(
        conn, target_type="app", action="stop", env_id="env-1",
        scheduled_at="2026-05-23T10:00:00",
    )
    create_one_time_action(
        conn, target_type="app", action="start", env_id="env-1",
        scheduled_at="2026-05-23T20:00:00",
    )
    # Only the 10:00 one should be pending before 15:00
    pending = get_pending_one_time_actions(conn, "2026-05-23T15:00:00")
    assert len(pending) == 1
    assert pending[0]["action"] == "stop"


def test_mark_one_time_executed():
    conn = _make_conn()
    aid = create_one_time_action(
        conn, target_type="env", action="stop_all", env_id="env-1",
        scheduled_at="2026-05-23T18:00:00",
    )
    mark_one_time_executed(conn, aid, status="executed", detail="stopped 5 apps")
    rows = list_one_time_actions(conn, status="executed")
    assert len(rows) == 1
    assert rows[0]["result_detail"] == "stopped 5 apps"
    assert rows[0]["executed_at_utc"] is not None


def test_list_one_time_status_filter():
    conn = _make_conn()
    a1 = create_one_time_action(conn, target_type="app", action="stop",
                                env_id="e1", scheduled_at="2026-05-23T10:00:00")
    a2 = create_one_time_action(conn, target_type="app", action="start",
                                env_id="e1", scheduled_at="2026-05-23T11:00:00")
    mark_one_time_executed(conn, a1)
    assert len(list_one_time_actions(conn, status="pending")) == 1
    assert len(list_one_time_actions(conn, status="executed")) == 1
    assert len(list_one_time_actions(conn)) == 2


# ---------------------------------------------------------------------------
# Schedule log
# ---------------------------------------------------------------------------

def test_insert_and_list_schedule_log():
    conn = _make_conn()
    lid = insert_schedule_log(
        conn, action="stop", trigger_type="env_schedule", trigger_id=1,
        target_type="app", source="cloudhub", env_id="env-1", domain="my-app",
        ok=True, detail="stopped", response={"status": "STOPPED"},
    )
    assert lid >= 1
    logs = list_schedule_logs(conn)
    assert len(logs) == 1
    assert logs[0]["action"] == "stop"
    assert logs[0]["ok"] == 1
    assert '"status"' in logs[0]["response_json"]


def test_was_action_done_today():
    conn = _make_conn()
    # No log yet
    assert was_action_done_today(
        conn, action="stop", env_id="env-1", domain="my-app",
        today_str="2026-05-23T00:00:00",
    ) is False

    # Insert a successful log
    insert_schedule_log(
        conn, action="stop", trigger_type="env_schedule",
        target_type="app", env_id="env-1", domain="my-app", ok=True,
    )

    # Now it should be True (timestamps are UTC ISO, always > today_str start)
    assert was_action_done_today(
        conn, action="stop", env_id="env-1", domain="my-app",
        today_str="2026-05-23T00:00:00",
    ) is True


def test_was_action_done_today_ignores_failures():
    conn = _make_conn()
    insert_schedule_log(
        conn, action="stop", trigger_type="env_schedule",
        target_type="app", env_id="env-1", domain="my-app", ok=False,
    )
    assert was_action_done_today(
        conn, action="stop", env_id="env-1", domain="my-app",
        today_str="2026-05-23T00:00:00",
    ) is False


def test_was_action_done_today_ignores_skips():
    """Skip entries ('no action needed') should NOT block real actions."""
    conn = _make_conn()
    insert_schedule_log(
        conn, action="stop", trigger_type="env_schedule",
        target_type="app", env_id="env-1", domain="my-app", ok=True,
        detail="already NOT_RUNNING — no action needed",
    )
    # Skip entry should NOT count as "done today"
    assert was_action_done_today(
        conn, action="stop", env_id="env-1", domain="my-app",
        today_str="2026-05-23T00:00:00",
    ) is False


def test_was_action_done_today_different_domain():
    conn = _make_conn()
    insert_schedule_log(
        conn, action="stop", trigger_type="env_schedule",
        target_type="app", env_id="env-1", domain="other-app", ok=True,
    )
    assert was_action_done_today(
        conn, action="stop", env_id="env-1", domain="my-app",
        today_str="2026-05-23T00:00:00",
    ) is False


# ---------------------------------------------------------------------------
# Schedule counts
# ---------------------------------------------------------------------------

def test_schedule_counts():
    conn = _make_conn()
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    upsert_env_schedule(conn, env_id="env-2", start_time="09:00", stop_time="17:00",
                        enabled=False)
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="app-a",
                        override_type="custom", start_time="10:00", stop_time="16:00")
    upsert_app_override(conn, source="cloudhub", env_id="env-1", domain="app-b",
                        override_type="always_on")
    create_one_time_action(conn, target_type="app", action="stop", env_id="env-1",
                           scheduled_at="2026-05-23T18:00:00")
    insert_schedule_log(conn, action="stop", trigger_type="env_schedule",
                        target_type="app", env_id="env-1", ok=True)

    c = schedule_counts(conn)
    assert c["env_schedules"] == 1       # only enabled
    assert c["custom_overrides"] == 1
    assert c["always_on"] == 1
    assert c["pending_one_time"] == 1
    assert c["actions_today"] >= 1


# ---------------------------------------------------------------------------
# Schema idempotency
# ---------------------------------------------------------------------------

def test_ensure_schema_idempotent():
    conn = _make_conn()
    upsert_env_schedule(conn, env_id="env-1", start_time="08:00", stop_time="18:00")
    ensure_schedule_schema(conn)  # run again — should not crash or lose data
    assert get_env_schedule(conn, "env-1") is not None
