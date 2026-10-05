"""Normalize Anypoint Usage API responses into stable records."""

from __future__ import annotations

import datetime as dt


def _time_value(raw: dict, candidates: tuple[str, ...]) -> str | None:
    for key in candidates:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000, tz=dt.timezone.utc).isoformat()
        return str(value)
    return None


def _first(raw: dict, *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_rows(response: dict | list) -> list[dict]:
    if isinstance(response, list):
        return response
    data = response.get("data", [])
    if isinstance(data, list):
        return data
    return []


def normalize_result(snapshot_id: int, timeseries: str, meter: dict, response: dict | list) -> list[dict]:
    rows = _extract_rows(response)
    records: list[dict] = []
    measurement = meter["measurement"]
    for row in rows:
        if measurement not in row:
            continue
        value = row.get(measurement)
        if value is None:
            continue
        records.append(
            {
                "snapshot_id": snapshot_id,
                "meter_key": meter["key"],
                "meter_name": meter["meter"],
                "measurement": measurement,
                "meter_type": meter.get("type", "SUM"),
                "timeseries": timeseries,
                "period_start_utc": _time_value(row, ("startTime", "start_time", "timestamp")),
                "period_end_utc": _time_value(row, ("endTime", "end_time", "timestamp")),
                "org_id": _first(row, "org_id"),
                "org_name": _first(row, "org_name"),
                "env_id": _first(row, "env_id"),
                "env_name": _first(row, "env_name"),
                "env_type": _first(row, "env_type"),
                "asset_id": _first(row, "asset_id"),
                "asset_name": _first(row, "asset_name"),
                "app_name": _first(row, "app_name", "asset_name"),
                "deployment_model": _first(row, "deployment_model", "deployment_mode"),
                "value": float(value),
                "raw_dimensions": {k: v for k, v in row.items() if k != measurement},
                "raw_record": row,
            }
        )
    return records
