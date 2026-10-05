"""Usage meter catalog helpers."""

from __future__ import annotations

import json
from pathlib import Path


def load_meters(path: str | Path = "config/anypoint_usage_meters.json") -> list[dict]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)["meters"]


def build_query(meter: dict, start_ms: int, end_ms: int, timeseries: str, dimensions: bool) -> str:
    if dimensions:
        fields = meter["select"]
    else:
        fields = [meter["measurement"]]
        if meter.get("type") == "MAX_CONCURRENT":
            fields.append("max_concurrent_time")
    selected = ", ".join(fields)
    return (
        f"SELECT {selected} FROM {meter['meter']} "
        f"WHERE timestamp between {start_ms} and {end_ms} TIMESERIES {timeseries}"
    )
