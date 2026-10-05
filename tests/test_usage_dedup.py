"""Tests for usage_store deduplication across snapshots."""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from usage_store import (
    connect,
    insert_snapshot,
    insert_usage_records,
    latest_usage_by_meter,
    usage_by_meter_window,
    latest_day_usage,
    top_consumers,
    usage_totals_by_meter,
)


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        open(os.devnull, "w").name  # trigger schema via connect()
    ) if False else None
    # Use the connect function's schema but on an in-memory DB
    from usage_store import SCHEMA
    conn.executescript(SCHEMA)
    return conn


def _insert_snapshot(conn, run_id, window_start, window_end):
    return insert_snapshot(
        conn,
        run_id=run_id,
        collected_at_utc="2026-05-19T00:00:00Z",
        host="https://anypoint.mulesoft.com",
        window_start_utc=window_start,
        window_end_utc=window_end,
        timeseries="P1D",
        dimensions=True,
        raw_path=None,
    )


def _record(snapshot_id, meter_key, period, value, meter_type="SUM", app="app1", env_id="env1"):
    return {
        "snapshot_id": snapshot_id,
        "meter_key": meter_key,
        "meter_name": meter_key,
        "measurement": "count",
        "meter_type": meter_type,
        "timeseries": "P1D",
        "period_start_utc": period,
        "period_end_utc": period,
        "org_id": "org1",
        "org_name": "Test",
        "env_id": env_id,
        "env_name": "dev",
        "env_type": "sandbox",
        "asset_id": None,
        "asset_name": None,
        "app_name": app,
        "deployment_model": "CH2",
        "value": value,
        "raw_dimensions": {},
        "raw_record": {},
    }


def test_dedup_sum_meter():
    """Same DRAWDOWN data point in 3 snapshots should count once, not 3x."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")
    s3 = _insert_snapshot(conn, "run-3", "2026-05-01", "2026-05-19")

    # Same data point across 3 snapshots
    for sid in (s1, s2, s3):
        insert_usage_records(conn, [_record(sid, "messages", "2026-05-18T00:00:00Z", 100.0)])
    conn.commit()

    result = latest_usage_by_meter(conn, "2026-05-01", "2026-05-19")
    # Should be 100, NOT 300
    assert result["messages"] == 100.0, f"Expected 100.0, got {result['messages']}"


def test_dedup_max_concurrent():
    """MAX_CONCURRENT meter should take MAX, unaffected by duplicates."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [_record(sid, "flows", "2026-05-18T00:00:00Z", 14.0, meter_type="MAX_CONCURRENT")])
    conn.commit()

    result = latest_usage_by_meter(conn, "2026-05-01", "2026-05-19")
    assert result["flows"] == 14.0, f"Expected 14.0, got {result['flows']}"


def test_dedup_multiple_days():
    """SUM meter with multiple days and duplicated snapshots."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [
            _record(sid, "messages", "2026-05-17T00:00:00Z", 50.0),
            _record(sid, "messages", "2026-05-18T00:00:00Z", 100.0),
        ])
    conn.commit()

    result = latest_usage_by_meter(conn, "2026-05-01", "2026-05-19")
    # 50 + 100 = 150, not (50+50) + (100+100) = 300
    assert result["messages"] == 150.0, f"Expected 150.0, got {result['messages']}"


def test_dedup_different_apps():
    """Different apps on same day should both count (not deduplicated)."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [
            _record(sid, "messages", "2026-05-18T00:00:00Z", 100.0, app="app1"),
            _record(sid, "messages", "2026-05-18T00:00:00Z", 200.0, app="app2"),
        ])
    conn.commit()

    result = latest_usage_by_meter(conn, "2026-05-01", "2026-05-19")
    # 100 (app1) + 200 (app2) = 300
    assert result["messages"] == 300.0, f"Expected 300.0, got {result['messages']}"


def test_dedup_latest_day_usage():
    """latest_day_usage should also deduplicate."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [
            _record(sid, "messages", "2026-05-17T00:00:00Z", 50.0),
            _record(sid, "messages", "2026-05-18T00:00:00Z", 100.0),
        ])
    conn.commit()

    result = latest_day_usage(conn)
    # Latest day is May 18, value should be 100 not 200
    assert result["messages"] == 100.0, f"Expected 100.0, got {result['messages']}"


def test_dedup_top_consumers():
    """top_consumers should deduplicate across snapshots."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [_record(sid, "messages", "2026-05-18T00:00:00Z", 100.0)])
    conn.commit()

    result = top_consumers(conn, "2026-05-01", "2026-05-19")
    assert len(result) == 1
    assert result[0]["value"] == 100.0, f"Expected 100.0, got {result[0]['value']}"


def test_dedup_usage_totals():
    """usage_totals_by_meter should deduplicate across snapshots."""
    conn = _make_db()
    s1 = _insert_snapshot(conn, "run-1", "2026-05-01", "2026-05-19")
    s2 = _insert_snapshot(conn, "run-2", "2026-05-01", "2026-05-19")

    for sid in (s1, s2):
        insert_usage_records(conn, [_record(sid, "messages", "2026-05-18T00:00:00Z", 100.0)])
    conn.commit()

    result = usage_totals_by_meter(conn)
    assert len(result) == 1
    assert result[0]["value"] == 100.0, f"Expected 100.0, got {result[0]['value']}"
