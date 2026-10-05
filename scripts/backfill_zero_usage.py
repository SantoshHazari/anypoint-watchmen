#!/usr/bin/env python
"""Backfill missing usage data with zeros for dates where there was no activity."""

from __future__ import annotations

import datetime as dt
import sqlite3
import json
from pathlib import Path


def backfill_zero_usage(
    conn: sqlite3.Connection,
    since_date: str,  # ISO format: 2026-05-26
    until_date: str,  # ISO format: 2026-05-28
) -> dict:
    """
    Insert zero-value usage records for missing dates.

    For each meter that has ANY data, fill in zeros for dates in the range
    where no data exists.

    Returns summary of what was inserted.
    """
    since = dt.datetime.fromisoformat(since_date).date()
    until = dt.datetime.fromisoformat(until_date).date()

    # Find a recent usage_snapshot to link to (use the most recent one)
    snapshot = conn.execute("""
        SELECT id FROM usage_snapshot
        ORDER BY collected_at_utc DESC LIMIT 1
    """).fetchone()

    if not snapshot:
        return {"error": "No usage_snapshot found in database"}

    snapshot_id = snapshot[0]

    # Get all distinct meters
    meters = conn.execute("""
        SELECT DISTINCT meter_key, meter_name, meter_type
        FROM usage_record
        ORDER BY meter_key
    """).fetchall()

    summary = {
        "backfilled_dates": f"{since_date} to {until_date}",
        "snapshot_id": snapshot_id,
        "meters_processed": 0,
        "records_inserted": 0,
        "details": {},
    }

    # For each meter, find missing dates and insert zeros
    for meter_key, meter_name, meter_type in meters:
        # Get date range that exists for this meter
        existing_dates = conn.execute("""
            SELECT DISTINCT DATE(period_start_utc)
            FROM usage_record
            WHERE meter_key = ?
            ORDER BY period_start_utc
        """, (meter_key,)).fetchall()

        existing_dates_set = {dt.datetime.fromisoformat(d[0]).date() for d in existing_dates}

        # Generate all dates in target range
        all_dates = []
        current = since
        while current <= until:
            all_dates.append(current)
            current += dt.timedelta(days=1)

        # Find missing dates
        missing_dates = [d for d in all_dates if d not in existing_dates_set]

        if not missing_dates:
            continue

        summary["meters_processed"] += 1
        inserted = 0

        # Insert zero records for missing dates
        for missing_date in missing_dates:
            period_start = f"{missing_date.isoformat()}T00:00:00+00:00"
            period_end = f"{(missing_date + dt.timedelta(days=1)).isoformat()}T00:00:00+00:00"

            try:
                conn.execute("""
                    INSERT INTO usage_record (
                        snapshot_id, meter_key, meter_name, measurement,
                        meter_type, timeseries, period_start_utc, period_end_utc,
                        org_id, org_name, env_id, env_name, env_type,
                        asset_id, asset_name, app_name, deployment_model,
                        value, raw_dimensions_json, raw_record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot_id,
                    meter_key,
                    meter_name,
                    "zero_backfill",
                    meter_type,
                    "P1D",
                    period_start,
                    period_end,
                    None,  # org_id
                    None,  # org_name
                    None,  # env_id
                    None,  # env_name
                    None,  # env_type
                    None,  # asset_id
                    None,  # asset_name
                    None,  # app_name
                    None,  # deployment_model
                    0.0,   # value (ZERO!)
                    "{}",  # raw_dimensions_json
                    json.dumps({"backfilled": True, "reason": "no_api_data"})
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                # Record may already exist due to deduplication
                pass

        if inserted > 0:
            summary["records_inserted"] += inserted
            summary["details"][meter_key] = {
                "missing_dates": [d.isoformat() for d in missing_dates],
                "records_inserted": inserted,
            }

    conn.commit()
    return summary


if __name__ == "__main__":
    # Backfill May 26-28, 2026
    conn = sqlite3.connect("data/watchmen.sqlite")
    result = backfill_zero_usage(
        conn,
        since_date="2026-05-26",
        until_date="2026-05-28",
    )
    conn.close()

    print("Backfill Summary:")
    print(f"  Dates: {result['backfilled_dates']}")
    print(f"  Meters processed: {result['meters_processed']}")
    print(f"  Records inserted: {result['records_inserted']}")
    print()

    if result['details']:
        print("Details:")
        for meter_key, details in result['details'].items():
            print(f"  {meter_key}:")
            print(f"    Missing dates: {details['missing_dates']}")
            print(f"    Records inserted: {details['records_inserted']}")
    else:
        print("No backfill was needed")
