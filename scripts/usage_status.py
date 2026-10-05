#!/usr/bin/env python3
"""Calculate current Anypoint usage status against externalized entitlements."""

from __future__ import annotations

import argparse
import datetime as dt
import json

from alerts import dispatch_alerts, send_test_email
from thresholds import evaluate_entitlements
from usage_kpis import add_burn_rate_kpis
from usage_store import connect, insert_entitlement_status, latest_usage_by_meter
from usage_time import parse_date_window
from watchmen_config import load_entitlements, load_settings, project_path


def _contract_window(settings: dict) -> tuple[str, str, str]:
    contract = settings["contract"]
    start, end = parse_date_window(contract["start_date"], contract["end_date"])
    now = dt.datetime.now(dt.timezone.utc)
    effective_end = min(end, now)
    return start.isoformat(), effective_end.isoformat(), end.isoformat()


def _render_markdown(statuses: list[dict]) -> str:
    lines = [
        "| Severity | Entitlement | Usage | Limit | Used | Daily Burn | Allowed Remaining / Day | Expected To Date | Vs Expected | Projected | Burn Status | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for status in statuses:
        kpis = status.get("kpis", {})
        lines.append(
            f"| {status['severity']} | {status['name']} | "
            f"{status['usage_value']:.4f} {status['unit']} | "
            f"{status['limit_value']:.4f} {status['unit']} | "
            f"{status['percent_used']:.2f}% | "
            f"{kpis.get('average_daily_burn', 0):.6f} {status['unit']}/day | "
            f"{kpis.get('allowed_remaining_daily_burn', 0):.6f} {status['unit']}/day | "
            f"{kpis.get('allowed_usage_to_date', 0):.4f} {status['unit']} | "
            f"{kpis.get('usage_vs_expected_to_date', 0):.4f} {status['unit']} | "
            f"{kpis.get('projected_contract_percent', 0):.2f}% | "
            f"{kpis.get('burn_rate_status', 'ok')} | {status['status']} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report usage status against entitlement limits.")
    parser.add_argument("--settings", default="config/settings.json")
    parser.add_argument("--entitlements", default="config/entitlements.json")
    parser.add_argument("--start-date", help="UTC start date YYYY-MM-DD. Defaults to contract start.")
    parser.add_argument("--end-date", help="UTC end date YYYY-MM-DD. Defaults to now/contract end.")
    parser.add_argument("--json", action="store_true", help="Render JSON instead of Markdown.")
    parser.add_argument("--markdown-output", help="Optional Markdown output path.")
    parser.add_argument("--write-alerts", action="store_true", help="Dispatch configured alert channels.")
    parser.add_argument("--test-email", action="store_true", help="Send one synthetic SMTP validation email and exit.")
    parser.add_argument("--include-fixtures", action="store_true", help="Include synthetic fixture data in calculations.")
    args = parser.parse_args()

    settings = load_settings(args.settings)
    if args.test_email:
        sent = send_test_email(settings)
        print(json.dumps({"test_email_sent": sent}, indent=2))
        return 0

    entitlements = load_entitlements(args.entitlements)
    _, _, configured_contract_end_utc = _contract_window(settings)
    if args.start_date and args.end_date:
        start, end = parse_date_window(args.start_date, args.end_date)
        since_utc, until_utc, contract_end_utc = start.isoformat(), end.isoformat(), configured_contract_end_utc
    else:
        since_utc, until_utc, contract_end_utc = _contract_window(settings)

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        usage = latest_usage_by_meter(conn, since_utc, until_utc, include_fixtures=args.include_fixtures)
        statuses = evaluate_entitlements(entitlements, usage)
        statuses = add_burn_rate_kpis(
            statuses,
            window_start_utc=since_utc,
            window_end_utc=until_utc,
            contract_end_utc=contract_end_utc,
        )
        run_id = f"status-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        insert_entitlement_status(conn, run_id, statuses)
        conn.commit()
    finally:
        conn.close()

    alert_result = None
    if args.write_alerts:
        alert_result = dispatch_alerts(settings, statuses, run_id)

    payload = {
        "run_id": run_id,
        "window_utc": {"start": since_utc, "end": until_utc},
        "contract_end_utc": contract_end_utc,
        "statuses": statuses,
        "alerts": alert_result,
    }
    markdown = _render_markdown(statuses)
    if args.markdown_output:
        out_path = project_path(args.markdown_output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(markdown)
        if alert_result is not None:
            print()
            print(json.dumps({"alerts": alert_result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
