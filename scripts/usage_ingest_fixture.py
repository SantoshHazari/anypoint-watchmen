#!/usr/bin/env python3
"""Ingest a synthetic raw Usage API fixture into SQLite for KPI testing."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from usage_meters import load_meters
from usage_normalizer import normalize_result
from usage_store import connect, insert_snapshot, insert_usage_records
from watchmen_config import load_settings, project_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a synthetic usage fixture.")
    parser.add_argument("fixture", help="Path to fixture JSON.")
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("--meters-file", default="config/anypoint_usage_meters.json")
    args = parser.parse_args()

    settings = load_settings(args.settings)
    fixture_path = project_path(args.fixture)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    run_id = f"{payload['run_id']}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        snapshot_id = insert_snapshot(
            conn,
            run_id=run_id,
            collected_at_utc=payload["collected_at_utc"],
            host=payload["host"],
            window_start_utc=payload["window_utc"]["start"],
            window_end_utc=payload["window_utc"]["end"],
            timeseries=payload["timeseries"],
            dimensions=payload["dimensions"],
            raw_path=str(fixture_path),
        )
        meter_by_key = {m["key"]: m for m in load_meters(project_path(args.meters_file))}
        inserted = 0
        for result in payload["results"]:
            if not result.get("ok"):
                continue
            records = normalize_result(snapshot_id, payload["timeseries"], meter_by_key[result["key"]], result["response"])
            inserted += insert_usage_records(conn, records)
        conn.commit()
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "run_id": run_id,
                "fixture": str(fixture_path),
                "records_inserted": inserted,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
