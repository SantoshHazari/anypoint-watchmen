#!/usr/bin/env python3
"""Collect Anypoint usage data, save raw snapshots, and persist normalized records."""

from __future__ import annotations

import argparse
import json
import sys

from watchmen_services import collect_usage


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and persist Anypoint usage data.")
    parser.add_argument("--host", help="Anypoint control-plane host. Defaults to $ANYPOINT_HOST or US control plane.")
    parser.add_argument("--token-env", default="ANYPOINT_TOKEN")
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("--meters-file", default="config/anypoint_usage_meters.json")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start-date", help="UTC start date YYYY-MM-DD.")
    parser.add_argument("--end-date", help="UTC end date YYYY-MM-DD.")
    parser.add_argument("--timeseries", choices=["P1D", "P1M"], default="P1D")
    parser.add_argument("--dimensions", action="store_true", default=True)
    args = parser.parse_args()

    if args.timeseries == "P1D" and args.days > 30 and not (args.start_date and args.end_date):
        print("Usage API restricts P1D queries to 30 days.", file=sys.stderr)
        return 2

    try:
        result = collect_usage(
            host=args.host,
            token_env=args.token_env,
            settings_path=args.settings,
            meters_file=args.meters_file,
            days=args.days,
            start_date=args.start_date,
            end_date=args.end_date,
            timeseries=args.timeseries,
            dimensions=args.dimensions,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
