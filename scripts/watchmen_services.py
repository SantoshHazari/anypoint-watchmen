"""Reusable Watchmen services for CLI and web entrypoints."""

from __future__ import annotations

import datetime as dt
import json
import uuid

from alerts import dispatch_alerts
from anypoint_auth import load_auth
from anypoint_usage_api import UsageApiClient
from thresholds import evaluate_entitlements
from usage_kpis import add_burn_rate_kpis
from usage_meters import build_query, load_meters
from usage_normalizer import normalize_result
from usage_store import (
    connect,
    insert_entitlement_status,
    insert_snapshot,
    insert_usage_records,
    latest_day_usage,
    latest_usage_by_meter,
    top_consumers,
    usage_by_meter_window,
)
from usage_time import default_closed_window, parse_date_window, utc_ms
from watchmen_config import load_entitlements, load_settings, project_path
from watchmen_log import get_logger

log = get_logger("services")


def fill_missing_dates_with_zeros(
    records: list[dict],
    window_start: str,
    window_end: str,
    meter_key: str,
    meter_name: str,
    meter_type: str,
    timeseries: str,
) -> list[dict]:
    """
    Fill in missing dates with zero-value records.

    If the API returns data for only some dates in the window, insert zeros
    for the missing dates to show that there was no activity on those days.

    Args:
        records: List of normalized usage records from API response
        window_start: ISO format start date (e.g., "2026-05-14T00:00:00+00:00")
        window_end: ISO format end date (e.g., "2026-05-28T23:59:59+00:00")
        meter_key: The meter identifier (e.g., "mule_messages")
        meter_name: The meter name (e.g., "runtime_mule_message_count")
        meter_type: The meter type (e.g., "SUM", "MAX_CONCURRENT")
        timeseries: The timeseries interval (e.g., "P1D")

    Returns:
        List of records with zeros filled in for missing dates
    """
    if not records or timeseries != "P1D":
        return records

    # Extract dates from window
    try:
        start_dt = dt.datetime.fromisoformat(window_start.replace("Z", "+00:00"))
        end_dt = dt.datetime.fromisoformat(window_end.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return records

    start_date = start_dt.date()
    end_date = end_dt.date()

    # Get all dates that have data in the records
    dates_with_data = set()
    for record in records:
        period_start = record.get("period_start_utc")
        if period_start:
            try:
                record_date = dt.datetime.fromisoformat(period_start.replace("Z", "+00:00")).date()
                dates_with_data.add(record_date)
            except ValueError:
                pass

    # Generate all dates in the window
    all_dates = []
    current = start_date
    while current <= end_date:
        all_dates.append(current)
        current += dt.timedelta(days=1)

    # Find missing dates
    missing_dates = [d for d in all_dates if d not in dates_with_data]

    if not missing_dates:
        return records

    log.info(
        "Filling %d missing dates with zeros for %s (have %d dates with data)",
        len(missing_dates),
        meter_key,
        len(dates_with_data),
    )

    # Create zero records for missing dates
    zero_records = []
    for missing_date in missing_dates:
        period_start = f"{missing_date.isoformat()}T00:00:00+00:00"
        period_end = f"{(missing_date + dt.timedelta(days=1)).isoformat()}T00:00:00+00:00"

        zero_records.append(
            {
                "snapshot_id": records[0]["snapshot_id"] if records else None,
                "meter_key": meter_key,
                "meter_name": meter_name,
                "measurement": records[0]["measurement"] if records else "zero_fill",
                "meter_type": meter_type,
                "timeseries": timeseries,
                "period_start_utc": period_start,
                "period_end_utc": period_end,
                "org_id": None,
                "org_name": None,
                "env_id": None,
                "env_name": None,
                "env_type": None,
                "asset_id": None,
                "asset_name": None,
                "app_name": None,
                "deployment_model": None,
                "value": 0.0,
                "raw_dimensions": {},
                "raw_record": {"zero_fill": True, "reason": "no_api_data"},
            }
        )

    return records + zero_records


def contract_window(settings: dict) -> tuple[str, str, str]:
    contract = settings["contract"]
    start, end = parse_date_window(contract["start_date"], contract["end_date"])
    now = dt.datetime.now(dt.timezone.utc)
    effective_end = min(end, now)
    return start.isoformat(), effective_end.isoformat(), end.isoformat()


def collect_usage(
    *,
    host: str | None = None,
    token_env: str = "ANYPOINT_TOKEN",
    settings_path: str = "config/settings.json",
    meters_file: str = "config/anypoint_usage_meters.json",
    days: int = 30,
    timeseries: str = "P1D",
    dimensions: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    if timeseries == "P1D" and days > 30 and not (start_date and end_date):
        raise RuntimeError("Usage API restricts P1D queries to 30 days.")

    auth = load_auth(host, token_env)
    settings = load_settings(settings_path)
    if start_date and end_date:
        start, end = parse_date_window(start_date, end_date)
    else:
        start, end = default_closed_window(days)

    collected_at = dt.datetime.now(dt.timezone.utc)
    run_id = f"usage-{collected_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    raw_dir = project_path(settings["storage"]["raw_usage_dir"]) / collected_at.strftime("%Y-%m-%d")
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{run_id}.json"

    client = UsageApiClient(auth)
    meters = load_meters(project_path(meters_file))
    payload = {
        "run_id": run_id,
        "host": auth.normalized_host,
        "collected_at_utc": collected_at.isoformat(),
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "timeseries": timeseries,
        "dimensions": dimensions,
        "results": [],
    }

    start_ms = utc_ms(start)
    end_ms = utc_ms(end)
    for meter in meters:
        query = build_query(meter, start_ms, end_ms, timeseries, dimensions)
        result = {"key": meter["key"], "meter": meter["meter"], "measurement": meter["measurement"], "query": query}
        try:
            result["ok"] = True
            result["response"] = client.search(query)
        except RuntimeError as exc:
            result["ok"] = False
            result["error"] = str(exc)
        payload["results"].append(result)

    raw_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.info("Collected %d meters, saved to %s", len(payload["results"]), raw_path)
    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        snapshot_id = insert_snapshot(
            conn,
            run_id=run_id,
            collected_at_utc=collected_at.isoformat(),
            host=auth.normalized_host,
            window_start_utc=start.isoformat(),
            window_end_utc=end.isoformat(),
            timeseries=timeseries,
            dimensions=dimensions,
            raw_path=str(raw_path),
        )
        inserted = 0
        meter_by_key = {m["key"]: m for m in meters}
        for result in payload["results"]:
            if result.get("ok"):
                meter = meter_by_key[result["key"]]
                records = normalize_result(snapshot_id, timeseries, meter, result["response"])
                # Fill missing dates with zeros
                records = fill_missing_dates_with_zeros(
                    records,
                    window_start=start.isoformat(),
                    window_end=end.isoformat(),
                    meter_key=meter["key"],
                    meter_name=meter["meter"],
                    meter_type=meter.get("type", "SUM"),
                    timeseries=timeseries,
                )
                inserted += insert_usage_records(conn, records)
        conn.commit()
    finally:
        conn.close()

    return {
        "run_id": run_id,
        "raw_path": str(raw_path),
        "sqlite_path": settings["storage"]["sqlite_path"],
        "window_utc": payload["window_utc"],
        "meters": len(payload["results"]),
        "records_inserted": inserted,
        "errors": [r for r in payload["results"] if not r.get("ok")],
    }


def calculate_status(
    *,
    settings_path: str = "config/settings.json",
    entitlements_path: str = "config/entitlements.json",
    include_fixtures: bool = False,
    write_alerts: bool = False,
    markdown_output: str | None = None,
) -> dict:
    settings = load_settings(settings_path)
    entitlements = load_entitlements(entitlements_path)
    since_utc, until_utc, contract_end_utc = contract_window(settings)
    now = dt.datetime.now(dt.timezone.utc)
    since_7d = max(now - dt.timedelta(days=7), dt.datetime.fromisoformat(since_utc.replace("Z", "+00:00")))
    since_30d = max(now - dt.timedelta(days=30), dt.datetime.fromisoformat(since_utc.replace("Z", "+00:00")))

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    import calendar
    month_total_days = float(calendar.monthrange(now.year, now.month)[1])
    month_elapsed_days = max((now - month_start).total_seconds() / 86400, 0.01)

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        usage = latest_usage_by_meter(conn, since_utc, until_utc, include_fixtures=include_fixtures)
        usage_7d = usage_by_meter_window(conn, since_7d.isoformat(), until_utc, include_fixtures=include_fixtures)
        usage_30d = usage_by_meter_window(conn, since_30d.isoformat(), until_utc, include_fixtures=include_fixtures)
        usage_month = usage_by_meter_window(conn, month_start.isoformat(), until_utc, include_fixtures=include_fixtures)
        usage_today = latest_day_usage(conn, include_fixtures=include_fixtures)
        consumers = top_consumers(conn, since_utc, until_utc, include_fixtures=include_fixtures)
        statuses = evaluate_entitlements(entitlements, usage)
        statuses = add_burn_rate_kpis(
            statuses,
            window_start_utc=since_utc,
            window_end_utc=until_utc,
            contract_end_utc=contract_end_utc,
            usage_7d=usage_7d,
            usage_30d=usage_30d,
            usage_month=usage_month,
            usage_today=usage_today,
            month_elapsed_days=month_elapsed_days,
            month_total_days=month_total_days,
        )
        run_id = f"status-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        insert_entitlement_status(conn, run_id, statuses)
        alerts = dispatch_alerts(settings, statuses, run_id, conn) if write_alerts else None
        conn.commit()
    finally:
        conn.close()

    log.info("Status %s: %d statuses, alerts=%s", run_id, len(statuses), alerts)
    payload = {
        "run_id": run_id,
        "window_utc": {"start": since_utc, "end": until_utc},
        "contract_end_utc": contract_end_utc,
        "statuses": statuses,
        "consumers": consumers,
        "alerts": alerts,
    }
    if markdown_output:
        from usage_status import _render_markdown

        out_path = project_path(markdown_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_render_markdown(statuses) + "\n", encoding="utf-8")
    return payload


def collect_then_status(write_alerts: bool = True) -> dict:
    collection = collect_usage()
    status = calculate_status(write_alerts=write_alerts, markdown_output="docs/latest-usage-status.md")
    return {"collection": collection, "status": {"run_id": status["run_id"], "alerts": status["alerts"]}}
