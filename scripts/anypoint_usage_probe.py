#!/usr/bin/env python3
"""Probe Anypoint Usage API without persisting credentials."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from anypoint_auth import load_auth
from anypoint_usage_api import UsageApiClient


def utc_ms(value: dt.datetime) -> int:
    return int(value.timestamp() * 1000)


def default_window(days: int) -> tuple[dt.datetime, dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    end_date = (now - dt.timedelta(days=4)).date()
    end = dt.datetime.combine(end_date, dt.time(23, 59, 59), tzinfo=dt.timezone.utc)
    start = end - dt.timedelta(days=days - 1, hours=23, minutes=59, seconds=59)
    return start, end


def load_meters(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Query selected Anypoint Usage API meters.")
    parser.add_argument("--host", help="Anypoint control-plane host. Defaults to $ANYPOINT_HOST or US control plane.")
    parser.add_argument("--token-env", default="ANYPOINT_TOKEN")
    parser.add_argument("--meters-file", default="config/anypoint_usage_meters.json")
    parser.add_argument("--days", type=int, default=30, help="Daily window size. Max 30 for P1D.")
    parser.add_argument("--timeseries", choices=["P1D", "P1M"], default="P1D")
    parser.add_argument("--dimensions", action="store_true", help="Include enriched dimensions such as org/app/env names.")
    parser.add_argument("--describe", action="store_true", help="Also call meters:describe.")
    parser.add_argument("--output", help="Optional output JSON path.")
    args = parser.parse_args()

    try:
        auth = load_auth(args.host, args.token_env)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.timeseries == "P1D" and args.days > 30:
        print("Usage API restricts P1D queries to 30 days.", file=sys.stderr)
        return 2

    client = UsageApiClient(auth)
    start, end = default_window(args.days)
    start_ms = utc_ms(start)
    end_ms = utc_ms(end)
    meters = load_meters(Path(args.meters_file))

    output: dict = {
        "host": auth.normalized_host,
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "timeseries": args.timeseries,
        "dimensions": args.dimensions,
        "results": [],
    }

    if args.describe:
        output["describe"] = client.describe_meters()

    for meter in meters:
        query = build_query(meter, start_ms, end_ms, args.timeseries, args.dimensions)
        try:
            response = client.search(query)
            output["results"].append(
                {
                    "key": meter["key"],
                    "meter": meter["meter"],
                    "measurement": meter["measurement"],
                    "query": query,
                    "ok": True,
                    "response": response,
                }
            )
        except RuntimeError as exc:
            output["results"].append(
                {
                    "key": meter["key"],
                    "meter": meter["meter"],
                    "measurement": meter["measurement"],
                    "query": query,
                    "ok": False,
                    "error": str(exc),
                }
            )

    rendered = json.dumps(output, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
